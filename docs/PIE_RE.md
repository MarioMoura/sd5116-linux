# SD5116 PIE Ethernet - Reverse Engineering Findings

> Note: files named `*_decomp.c` and `uboot.bin` referenced below are decompilations and
> binaries of the vendor firmware; they are not part of this repository.

Source of truth: `rootfs_extracted/lib/hsan/ko/driver/hi_pie.ko`, analyzed with
`arm-none-eabi-objdump` and **Ghidra headless** (`archive/diag/DecompileAll.java` →
`archive/diag/hi_pie_decomp.c`, 118 functions decompiled).

PIE = **Packet Interface Engine**: HiSilicon's proprietary ethernet MAC + DMA
engine (the vendor's replacement for a standard DesignWare/stmmac GMAC).

## Register block

The decompiler recovered named symbols. The SD5116 uses **`g_dp_PIE_reg_5116_s`**
as its register base (there is a separate `g_dp_PIE_reg_5115_s` for the SD5115;
bring-up picks one via a chip-variant test on `[global+0x100]`,
`(variant - 4) <=u 3`). Per-channel registers have **stride 0x10**; channel 0 is
the CPU ethernet path.

| Offset (ch N = base + N*0x10) | Meaning | Evidence |
|---|---|---|
| `+0x000` CTRL | init clears bits 0,1 | `hi_kernel_pie_hw_init` |
| `+0x004` IRQ_MASK1 | `0x0007FFFF` | `hw_init` |
| `+0x008` IRQ_MASK2 | `0x0007FFFF` | `hw_init` |
| `+0x180` MODE | set bit12, clear bit8, channel mask bits0-7 | `hw_init` |
| `+(ch+16)*0x10` = `0x100` (ch0) | **RX ring base** (DMA addr of descriptor ring) | ring-setup helper / decomp line ~1379 |
| `+0x104` | RX ring config = `log2(ring_size)` | ring-setup helper |
| `+0x108` | RX **producer** (doorbell) | per-channel pointer table |
| `+0x10C` | RX **consumer** (HW writes; read `& 0xfff`) | `hi_kernel_pie_get_logic_rptr` |
| per-channel enables `0x1C..0x38`, `0x3C..0x58` | write `1` | `hw_init` loop |

Short-frame register (`0x00790011`) is written to `*[global+0x334]` (a distinct
MMIO block), **not** to `0x11080010` as the current driver assumes.

Producer doorbell - **RESOLVED** from `hi_kernel_pie_poll`'s refill/producer
update (the doorbell carries two distinct 12-bit indices, RMW-preserving the
reserved nibbles):
```c
reg = (reg & 0xf000f000)              /* preserve bits[31:28] and [15:12] */
    | (producer_idx & 0xfff)          /* bits[11:0]  = producer (write) index */
    | ((consumer_idx & 0xfff) << 16); /* bits[27:16] = consumer (read) index  */
```
`hi_kernel_pie_hw_kick_rx(prod, cons, channel)` is the thin wrapper for the same
write (called via function pointer; `hi_bridge.ko` does **not** reference it).
The current driver's `IDX_COMPOUND(idx)` wrote the *same* value into both fields
- wrong; producer and consumer differ.

## HW descriptor format - **16 bytes, little-endian**

Confirmed: HW descriptor stride is `idx * 0x10` everywhere (RX fill, TX, poll).
The 24-byte (`idx*0x18`) and 32-byte (`idx<<5`) strides seen elsewhere are
*software* shadow/management arrays, not the HW ring.

| Field | Bits / type | On POST (CPU→HW) | On COMPLETION (HW→CPU) |
|---|---|---|---|
| `word0` @ +0 | bit15 | **OWN/VALID** - CPU sets to hand buffer to HW | HW **clears** when done |
| `word0` @ +0 | bit14 | - | status/error flag (`(byte[1]^0x40)&0x7f)>>6`) |
| `word0` @ +0 | bits[13:0] | - | **received length** (`& 0x3fff`) |
| `+4` (u32) | buffer DMA/physical address | yes | - |
| `+12` (u16) | buffer size (input) | yes | (HW writes metadata in +8..+15) |
| +8..+15 | - | - | VLAN/port/cos metadata (see `hi_kernel_pie_poll`) |

OWN-bit handling (read-modify-write of **just** byte+1 bit7, never clobbering
the rest of word0):
- **RX post** (`hi_kernel_pie_up`/fill loop, asm `0x1934`): write buf addr @+4,
  then `byte[desc+1] |= 0x80`.
- **TX reclaim** (`eth_transmit`): stop when `byte[desc+1] & 0x80` is still set
  (HW hasn't sent it yet). i.e. CPU sets OWN on post; HW clears it on send.
- **RX completion** (`hi_kernel_pie_poll`): length = `word0 & 0x3fff`, valid if
  status bit14 path passes and length >= 5.

## ROOT CAUSE of the stuck rings

`drivers/net/ethernet/hisilicon/sd5116-eth.c` posts descriptors with
`desc->word0 = cpu_to_le32(idx)` on **both** RX (`sd5116_alloc_rx_buf`) and TX
(`sd5116_start_xmit`). Bit 15 is therefore **0** → the engine never owns any
descriptor → it DMAs nothing.

This exactly explains the boot log:
- RX: `rx_c` frozen at `0x0000`, buffer shows stale `0x55…` preamble (untouched).
- TX: `tx_c` frozen at U-Boot's leftover `0x0006` while `tx_p` climbs.

## Minimal fix (hypothesis test)

When posting a descriptor: write `+4 = buf_dma`, `+12 = buf_len`, then set
**word0 bit 15** (OWN) via read-modify-write *last* (after a `wmb()`), without
overwriting the other word0 bits. On RX completion, take length from
`word0 & 0x3FFF` only (drop the `word2` fallback - length is not there).

Expected result if correct: `rx_c` / `tx_c` begin advancing.

## Hardware test results

**Run 1 (OWN-bit fix):** Confirmed the fix - RX descriptors post with
`word0=0x00008000` (OWN set), link comes up 1000/Full, and the engine now
**consumes TX** (the TX consumer register `0x24c` advances `0x000N0000` as
descriptors are posted, trailing the producer by one). Before the fix it was
frozen. Root cause confirmed.

**Run 1 new finding:** the **TX consumer index is reported in the high bits**
of `0x24c` (`>> 16`), **not** bits[11:0] like the RX consumer at `0x10c`. The
driver's `IDX_EXTRACT` read bits[11:0] (always 0), so TX reclaim never ran and
the queue stalled after 8 packets. Fix: reclaim TX by scanning the descriptor
**OWN bit** (vendor `eth_transmit` does exactly this), independent of the
consumer register's bitfield. Applied.

**Run 2 (TX reclaim via OWN scan):** RX still receives **nothing** - across all
polls (incl. after link-up and during ping) `0x10c` low bits stay 0, `RX[0]`
keeps `w0=0x00008000` (OWN never cleared by HW), buffer untouched. ping → 0
replies. (TX prints are capped at 8 by `tx_debug_cnt`, so TX end-to-end is still
unconfirmed, but the `0x24c` consumer high-bits advance.)

### RX root cause - STILL OPEN (corrected)

Earlier note retracted: the `*_to_cpu_*` / `*_from_cpu_*` inits called from
`init_module` write only into the module's own **`.bss`** (total `.bss` = 1004
bytes), filling 256-byte software classification/"reason" lookup tables. They are
**not** hardware switch config and are **not** the RX blocker. Our simpler driver
(plain `eth_type_trans` + `napi_gro_receive`) doesn't need them.

What we actually know: the PIE never DMAs an ingress frame into channel-0's RX
ring - `0x10c` consumer low bits stay 0, the descriptor OWN bit is never cleared.
Candidate causes, not yet discriminated:
1. A PIE/MAC RX-enable or port-routing bit we don't set (compare live registers).
2. Ingress frames delivered to a different channel/port than channel 0.
3. The L2 switch (configured by **`hi_bridge.ko`**, real MMIO) not forwarding
   ingress to the CPU port.
4. Our PIE reset sequence fails (`soft reset timeout`, hw-reset reg reads back
   unchanged `0x462000c7`) - may leave RX wedged.

### Vendor live-register diff (devmem on running stock kernel, RX working)

| Reg | Vendor (works) | Ours (RX dead) | Note |
|---|---|---|---|
| `0x180` MODE | `0x00041200` | `0x000412ff` | **bits[7:0]: 0x00 vs 0xff** |
| `0x104` RX cfg | `5` (32 entries) | `3` (8) | ring size |
| `0x10c` RX consumer | `0x..001a`, **increments low16 on traffic** | `0x00040000` (never moves) | HW not RX'ing |
| `0x108` RX producer | `0x0019_0018` (both 16b fields used) | `0x00000008` | |
| `0x1c/0x20/0x3c` chan-en | `1`; rest `0` | `0x1c..0x58` all `1` | we over-enable |
| `0x04/0x08` IRQ mask | `0 / 0x100` | `0x7ffff / 0xfff00100` | poll mode; not a DMA gate |

**Confirmed:** RX consumer index is the **low 16 bits of `0x10c`** and it does
increment with live traffic on the vendor - so our poll reads the correct field;
our consumer is frozen only because the engine receives nothing.

**Fix applied (hypothesis test):** the MODE `bits[7:0]` delta maps exactly to the
`hw_init` branch - vendor ran the `param_1==1` path (`mode &= 0xffffff00`), our
driver hardcoded the `|= 0xff` branch. Changed `sd5116_pie_hw_init` to **clear**
the channel-mask byte. Expected MODE after = `0x00041200`, matching vendor.

Secondary levers if RX still dead after this: match RX/TX ring cfg to 32
(`0x104/0x244 = 5`), and trim channel-enable writes to only `0x1c/0x20/0x3c`.

### Result: MODE fix confirmed, RX still receives ZERO frames → it's the switch

After the fix, `MODE=0x00041200` (matches vendor exactly), but RX consumer
`0x10c` never moves even once and OWN is never cleared - *zero* frames, not
"receives then stalls". Every visible PIE register now matches the vendor's
working state, so the PIE is correctly configured; frames simply never arrive.

**Conclusion - RX is blocked upstream in the L2 switch fabric, not the PIE.**
`hi_bridge.ko` is the datapath/switch driver and configures the hardware switch
via `hi_kernel_dp_5116_reg_init` / `_table_init` / `hi_kernel_dp_glb_init` /
`hi_kernel_dp_l2_init` (+ ACL/MAC/BC tables). One of these installs the rule that
forwards external-port ingress to the CPU port. Until that switch config is
ported, the PIE RX ring stays empty regardless of PIE setup.

This is the "hardest peripheral" the SPEC called out (the 400KB+ `hi_bridge`/
`hi_khal` switch stack). Realistic unblock paths:
1. **GPL source** (foss@huawei request, sent) - would give the exact dp init.
2. **Port the `hi_kernel_dp_5116_*` switch init** from `hi_bridge.ko` - large RE;
   start by finding the minimal CPU-port-forward / flood-to-CPU enable.

### Switch wall → "inherit U-Boot datapath" approach

Decompiled `hi_bridge.ko` (412 funcs, `archive/diag/hi_bridge_decomp.c`). It's the full L2
switch driver (`hi_kernel_dp_init` → reg/mix/glb/uni/security/acl/pdu/l2/qos/prbs
init across dozens of register blocks). Porting it blind is impractical.

Key empirical finding: **U-Boot already configured the switch→CPU datapath** (its
TFTP works), and our PIE `hw reset` at `0x14880030` is **datapath-wide** (shared
block; WDT lives there too) - it wipes that switch config, which we can't restore.

Test 1 (skip hw reset): confirmed - RX consumer came back as U-Boot's live value
(`rx_c=0x...0005`, `tx_c=0x...0006`) instead of the reset-default `0x00040000`.
So the reset *was* wiping state. But two self-inflicted issues appeared: the
*soft* reset wrote `CTRL|0x10` into MODE (→ `MODE=0x000a1000`, lost port bits),
and our fresh ring + producer=8 mismatched the inherited HW consumer (=5).

Test 2 (this build) - full inherit: **no reset at all** (soft+hw), so MODE stays
clean (`hw_init` now yields `0x00041200`), switch config preserved, and
`sd5116_hw_start` **aligns rx/tx software indices to the live HW consumer**
(`rx_cons=hw`, `rx_prod=hw+RING`, `tx_cons=tx_prod=hw`).

**Test 2 result (decisive negative):** `MODE=0x00041200` (correct), reset
skipped, indices aligned - and RX is **still zero** (`rx_c=0x00040000` frozen,
`RX[0]` OWN never cleared, 0 ping replies).  Conclusion: the blocker is
definitively **not** the PIE, and **U-Boot's switch→CPU forwarding does NOT
survive into our kernel** even though we touch no switch MMIO and don't reset.
TX engine does advance its consumer (`0x24c`), so the PIE TX side is live.

### The wall (confirmed): switch fabric must be configured by us

`hi_bridge` accesses the datapath/switch registers via the `0xf8xxxxxx` ioremap
window - phys blocks at `0x10106000`, `0x10107000`, `0x10108000`, `0x10109000`
(+ PIE-adjacent `0x10a702xx`, sysctrl `0x10100000`/clock gate `0x10100080`).
Getting RX requires porting the `hi_kernel_dp_*` switch init (reg/glb/l2/...) to
program CPU-port forwarding. This is the large, remaining effort.

Pragmatic unblock: the **GPL source** (foss@huawei, sent) gives this directly.
Otherwise: targeted RE to find the minimal "forward/flood ingress → CPU port"
register writes among the `dp` init, then replicate just those.

### Targeted switch RE: L2 forwarding block replay (necessary, not sufficient)

Resolved the switch register map from `hi_bridge.ko`: the `reg_init` array IS the
`g_dp_*_reg_5116_s[]` table; index order EPS,EQM,FAM,IFC,IPS,L2,... ; virt→phys is
linear `−0xe8000000` (cross-checked: IPS=`0xf9080000`→`0x11080000`, matching our
known short-frame reg `0x11080010`).  So `g_dp_L2_reg_5116_s` = `0xfa180000` →
phys **`0x12180000`**.

devmem diff of the L2 block (vendor RX-works vs ours RX-dead): our per-port
forwarding regs (`0x12180108..0x13c`, `0x148/0x150`) sat at power-on default
(`0x00100100` / `0`); the vendor had them configured.  Added
`sd5116_switch_l2_config()` to replay the exact vendor values at open.

**Result:** registers confirmed set, but **RX still receives zero**.  → L2 alone
is not enough; CPU forwarding needs the broader `dp_init` block set
(glb/uni/PFE_GLB/DP/port config/...).  Reconstructing all of it via devmem diffs
is impractical (dozens of interdependent blocks).  The **GPL source** is now the
efficient unblock; the PIE driver itself is complete and correct.

### Status summary
- ✅ boot, WDT, MDIO, PHY, link 1000/Full
- ✅ descriptor OWN handling (RX+TX post, TX reclaim by OWN scan)
- ✅ PIE block configured to match vendor (CTRL/MODE/rings/IRQ-irrelevant)
- ✅ TX: engine consumes descriptors
- ❌ RX: zero frames - needs L2 switch (`hi_bridge`) CPU-port forwarding config

## Open items (lower priority, for full correctness)
1. `hw_kick_rx` `param_1`/`param_2` semantics - needs `hi_bridge.ko` RE.
2. Where `g_dp_PIE_reg_5116_s` is ioremap'd (set in another `hi_*` module) - to
   confirm the current driver's `0x10A70000` base is the 5116 block.
3. Confirm SD5116 takes the `_5116_`/generic path vs the `_5115_` path at runtime
   (`hi_pie_5116_to_cpu_reason_init` exists).
4. TX exact post sequence (24-byte SW shadow vs 16-byte HW descriptor field map).

## U-Boot ethernet driver RE (2026-07-27) - the working reference implementation

Decompiled the stock bootloader (flash offset 0, 256k `boot` partition, link
base `0x81600000`) with Ghidra headless → `archive/diag/uboot_decomp.c` (354 functions).
U-Boot's TFTP RX **works**, so its driver is ground truth for a minimal working
datapath.

### CORRECTION: the "0x13a00000 MAC" hypothesis is RETRACTED
The earlier "U-Boot references 0x13a00000 165×" claim came from scanning raw
words: `0x13a0xxxx` and `0x11a0xxxx` are ARM **instruction encodings**
(`MOVNE rX, #imm` / `MOVNE rX, rY`), not literal-pool addresses.  There is no
evidence U-Boot touches a MAC block at 0x13a00000 at all.

### U-Boot eth functions (all PIE-only - no MAC, no L2/switch writes)
| Function | Role |
|---|---|
| `FUN_81609700` | eth init |
| `FUN_816097f8` | TX send (polls OWN for completion) |
| `FUN_81609958` | RX poll (OWN cleared = frame ready) |
| `FUN_8160968c` | RX refill + producer doorbell |
| `FUN_816099a4` | link event: toggles `0x14880030` bits 0-2,25,26 (clear → 1ms → set `0x6000007`) |

### U-Boot init sequence (complete!)
```
PIE+0x000 &= ~1               ; clear CTRL bit0
PIE+0x180 = PIE+0x180         ; MODE read + write-back UNCHANGED
PIE+0x008 |= 0x7FF            ; low 11 bits = channel/queue enables (NOT an IRQ mask)
PIE+0x100 = rx_ring_pa        ; 16-byte aligned
PIE+0x104 = 3                 ; log2(8 entries)
PIE+0x108 = 0                 ; RX producer - RAW index write, no RMW/compound
; post 1 RX desc: memset(desc,0,16); desc+4=buf; desc.word0|=0x8000; PIE+0x108 = 1
PIE+0x240 = tx_ring_pa
PIE+0x244 = 3
PIE+0x248 = 0                 ; TX producer - RAW index write
```

### U-Boot descriptor/doorbell semantics
- Indices are **3-bit wrapped** (`(i+1) % 8`) and written **raw** to +0x108/+0x248.
  The compound prod|cons<<16 RMW format seen in the vendor kernel is NOT what
  U-Boot uses - and U-Boot works.
- TX word0 = `len & 0x3fff`, then byte1 |= **0xc0** → bits 15 (OWN) **and 14** set.
- TX frames padded to 64 bytes (filler 0x6b) before posting.
- RX desc: zeroed, buf ptr at +4, word0 = 0x8000 only.  **No buffer-size field**
  (word3 stays 0).  Completion length = word0 & 0x3fff after OWN clears.
- Completion detection is purely the OWN bit; no consumer register is ever read.
- U-Boot keeps only 1 RX buffer outstanding (prod==cons ambiguity with wrapped
  indices); driver posts 7/8 for the same reason.

### Driver realigned to U-Boot semantics (sd5116-eth.c, build 2026-07-27 13:38)
1. `hw_init` = the three U-Boot writes only.  **Removed**: MODE bit-fiddling,
   IRQ mask writes (`0x7FFFF`), short-frame reg write, per-channel `1` writes.
2. **Removed the `0xFFF00000` write to +0x08 in `hw_start`** - it cleared the
   0x7FF channel enables ⇒ prime RX-kill suspect.
3. Raw wrapped-index doorbells everywhere; `pie_write_prod()` RMW helper deleted.
4. Rings reprogrammed from 0 (like U-Boot does over ROM state); index
   inheritance removed.
5. TX: word0 gets OWN|BIT(14)|len; skb padded to 64B (`skb_put_padto`).
6. RX: word3 = 0 (was RX_BUF_SIZE); RX completion gated on OWN only.
7. L2 vendor-replay removed: U-Boot RX works on power-on switch defaults, so
   the vendor L2 values were the wrong target.

**UNTESTED on hardware** - board unresponsive since 13:20 (no serial output, no
192.168.1.1 ARP, in either smart-plug state): physical power/cabling issue.

## Hardware test 2026-07-27 16:2x - U-Boot-replica driver

Board booted cleanly with the U-Boot-replica PIE init.  Link negotiates
1000/Full, TX descriptors are consumed by the engine (tx_c tracks tx_p), RX
descriptors are never touched (`w0=0x00008000` forever, OWN never cleared).

### DECISIVE NEW EVIDENCE: TX doesn't reach the wire either
Previous sessions concluded "TX works, RX is broken" from descriptor
consumption alone.  That was wrong.  With the ONU pinging the host:
- host `ip neigh` for the ONU stays **FAILED** - the host never receives the
  ONU's ARP requests, so the host kernel never learns its MAC;
- `ifconfig eth0` on the ONU: TX packets:9, RX packets:0.

So the PIE consumes descriptors but **no frame ever leaves the chip**.  The
fault is therefore NOT in RX-specific config (switch CPU-port forwarding, RX
doorbells) - it is a single **datapath egress/ingress gate** that blocks both
directions, downstream of the PIE and upstream of the PHY.  This kills the
"replay the vendor L2 forwarding tables" line of attack: a forwarding-table
problem cannot explain dead TX.

### Datapath enable pulse tested - NOT the blocker
Implemented U-Boot's link-event handler (`FUN_816099a4`) in `adjust_link`:
clear bits 0-2,25-26 of `0x14880030`, 1ms, then `|= 0x6000007`.
Readback: `0x14880030 = 0x462000c7` - bits 0,1,2,25,26 were **already set**
before the pulse (value identical at probe time), so this register was never
the gate.  Kept in the driver (harmless, matches U-Boot).

### Register state at failure (for future diffs)
```
CTRL[0x000]=0x000a0008  [0x004]=0x00000101  [0x008]=0x000fffff
STATUS1[0x010]=0x00000002  STATUS2[0x014]=0x00000001 (ready)
MODE[0x180]=0x000412ff        <- inherited from U-Boot, untouched
RX  [0x100]=ring_pa [0x104]=3 [0x108]=7(prod) [0x10c]=0x00010005
TX  [0x240]=ring_pa [0x244]=3 [0x248]=0(prod) [0x24c]=0x00000002
     [0x250..0x25c] all 0x00000002
0x14880030 = 0x462000c7
```
Note the consumer registers are compound: `tx_c` observed stepping
`0x00010001, 0x00020002, 0x00050004, ...` = `(prod_echo << 16) | consumed`.

### Next lead
U-Boot loads its eth driver and TFTP works - but our kernel runs *after* U-Boot
has already stopped using the datapath.  The gate is something U-Boot sets up
in a path we haven't found yet (likely in its board init, not its eth init), or
something our kernel *clears* during early boot.  Best next step: dump the full
`0x14880000` and `0x10a70000` blocks from U-Boot's prompt (datapath known good),
then again from our kernel, and diff.  U-Boot has `md`/`mw`, so this is now
fully scriptable via `archive/diag/netboot.py` + `archive/diag/serialctl.py`.

## Debug automation (2026-07-27)
- `archive/diag/serialctl.py` - serial console driver (auto-detects `/dev/ttyUSB*`,
  `cmd`/`watch`/`raw` modes, logs to `~/onu/serial.log`).
- `archive/diag/netboot.py` - one command: power-cycle via `tuya-switch`, break into
  U-Boot, TFTP all three files, `go`, print the boot log.
  U-Boot's console drops fast input, so commands are typed at ~30 chars/sec.
- `/srv/tftp` is owned by `mario`; copy builds straight in (tftpd is chrooted,
  so symlinks into `$HOME` do NOT work).
- Host has `192.168.1.2/24` as a secondary address on `enp6s0` (NetworkManager
  `eth` connection), alongside DHCP `192.168.15.10`.

## 2026-07-27 (session 2): PIE fully proven correct; gate isolated to L2 switch

Rebuilt the driver to be byte-for-byte faithful to U-Boot's working eth driver
(RE'd from the flash dump) and captured live register dumps from BOTH U-Boot
(datapath proven live by a real TFTP) and our kernel, across an expanded block
set. Tooling: archive/diag/dumpregs.py (kernel+uboot), archive/diag/dump_vendor.py, archive/diag/diffregs.py.

### The wide U-Boot-vs-kernel diff is CLEAN
Comparing every block (PIE, PIEAUX, RSTCTL, MDIO, IPS, L2, DP1, MAC1-3, GE0-3):
the ONLY differences are our own ring base/indices, the dynamic MDIO latch, and
the WDT config. **Every MAC / GE / L2-switch / DP1 / IPS datapath register
matches U-Boot's working state exactly.** So the egress gate is NOT a static
register value. This kills the multi-session "replay the vendor L2 forwarding
tables" theory outright.

### PIE engine is ALIVE and correct (proven on HW via devmem)
- TX: send frames -> TX consumer reg advances (0x2->0x3), descriptor OWN bit
  CLEARS (word0 0x8000->0x4046 = bit14 marker + len). The engine reads our
  descriptors, DMAs the buffer, and reports completion. TX is done correctly.
- RX: under a host ping flood, RX consumer reg stays frozen and RX descriptor
  OWN never clears -> the engine receives NOTHING from the switch.

### Conclusion: both directions are blocked AT THE L2 SWITCH
PIE(CPU port) <-> [L2 switch fabric] <-> GE MAC <-> LAN PHY.
The PIE side works. Frames handed to the switch never egress to the LAN port,
and LAN ingress never reaches the CPU port - even though the switch registers
match U-Boot. U-Boot's frames traverse the same switch fine.

### Things TESTED and RULED OUT as the fix
- Aligned DMA-coherent bounce buffers (removes buffer-alignment + cache-coherency
  variables): no change. **Kept** - it is the correct design anyway.
- Datapath enable pulse on 0x14880030 (U-Boot's link handler FUN_816099a4):
  bits already set (0x462000c7), pulse is a no-op. Kept, harmless.
- PIE ring index desync: writing producer/base does NOT reset the engine's
  internal CONSUMER (it holds U-Boot's leftover pos). Now inherited in hw_start
  (real bug, fixed) - but not the gate.
- Setting eth0 MAC to U-Boot's ethaddr BC:98:89:09:5D:98 (the board MAC, also
  what the vendor kernel uses): no change.

### Why register-snapshot replay won't finish the job
hi_bridge.ko `hi_kernel_dp_init` (0x30034) brings the switch up via a DYNAMIC
sequence: serdes programming over an internal SPI (hi_kernel_dp_glb_spi_*),
clause-45 MDIO, and per-port tag-attr setup (set_port_tag_attr(4,..) for CPU
port 4), L2/glb/uni sub-inits. U-Boot's minimal bridge config is enough for
U-Boot's own single-port TFTP but does NOT stand up the full CPU<->LAN datapath
our kernel needs, and the missing setup is procedural, not a static reg state.

### REMAINING WORK for a working NIC
Port hi_bridge.ko's dp_init (the proprietary switch/serdes bring-up) into the
driver, OR obtain the GPL source. This is the real blocker; the PIE driver
itself is complete. Vendor telnet (for a live switch-reg capture) is gated by a
hashed FiberHome CLI password (um_users DB in libcli_cli.so); common defaults
(root/admin, admin/admin, GEPON/GEPON, gpon, fnc@fh...) all rejected.

## 2026-07-27 (session 2 cont.): vendor shell obtained; register-replay proven insufficient

### Vendor root shell access (from README, was the missing key)
- `telnet 192.168.1.1 26` -> login `root`, NO password -> full busybox shell,
  vendor kernel `2.6.34.10_sd5116`.  (Port 23 is the FiberHome CLI with a hashed
  password; port 26 is the backdoor root shell.)
- `archive/diag/dump_vendor26.py` dumps the live datapath registers over port 26.

### Vendor (working) vs our kernel - the REAL switch config diff
Captured `archive/diag/regs_vendor.txt` from the running vendor and diffed vs ours.
Differences are entirely in: L2 (23 regs), DP1 (17), RSTCTL (5), IPS (1) - the
switch forwarding tables + datapath control.  MAC1-3 and GE0-3 blocks read
identical (mostly 0), so they are NOT the gate.  Notable vendor-only values:
- L2 +0x108..0x13c: real per-port forwarding (0x00100d00, 0x00101900,
  0x00111d03, ...) vs our power-on default 0x00100100.
- L2 +0x148/0x150 = 0x00011000; +0x180 = 0x80000050; +0x190..0x1a0 populated.
- DP1 +0x40 = 0x80000000; +0x48 = 0x4260007f; +0x4c..0x68 port config; +0x7c = 0xfff.
- RSTCTL +0x60 = 0x08ce048c; +0x64 = 0x12345116 (magic w/ chip id); +0x2c bit26;
  +0x34 = 0x001ffc04.

### DECISIVE: replaying ALL those vendor values via devmem does NOT restore traffic
On our running kernel I wrote every differing L2 + DP1 + IPS + RSTCTL register to
the exact vendor value, set the board MAC, brought eth0 up.  Still 100% loss,
both directions.  The PIE engine keeps consuming TX descriptors (OWN clears) but
nothing egresses, and RX stays dead.

=> The switch bring-up is **procedural, not a register state**.  The vendor's
`hi_khal` (429KB) + `hi_bridge` (211KB) modules stand up the fabric via a
sequence - serdes programming over an internal SPI (hi_kernel_dp_glb_spi_*),
clause-45 MDIO, and table/command-register commits - whose *residue* is the
register values above but whose *effect* (internal forwarding-engine state) is
not reproduced by writing the final values at runtime.  Our kernel also inherits
U-Boot's partial datapath rather than doing the full reset+reinit the vendor does.

### Bottom line
The PIE ethernet driver is complete and correct (proven: engine processes TX,
clears OWN; all PIE regs match). A working mainline NIC requires porting the
proprietary switch datapath init from hi_khal/hi_bridge (large), or the GPL
source.  Register-snapshot replay is a dead end - confirmed on hardware.

### Modules that build the datapath (vendor /proc/modules)
hi_khal (P, 429K) <- hi_bridge (P, 211K) <- {hi_l3, hi_oam, hi_epon, hi_gpon},
hi_pie (31K, NOT tainted), hi_mdio, hi_sysctl.  The CPU<->LAN path needs
hi_khal + hi_bridge + hi_pie working together.

## 2026-07-27 (session 2, RE deep-dive): switch init is procedural + indirect-table - full map

### Vendor register-base table RESOLVED (from hi_kernel_dp_5116_reg_init @0x1013c)
virt→phys = −0xe8000000. The driver ioremaps ~26 blocks; I had only been dumping 8.
| idx | phys | idx | phys | idx | phys |
|---|---|---|---|---|---|
| 0 | 0x11280000 | 9 | 0x12400000 | 0x12 | 0x14280000 |
| 1 | 0x11200000 (DP/uni) | 0xa | 0x12080000 | 0x13 | 0x10a70000 (PIE) |
| 2 | 0x11100000 (FAM) | 0xb | 0x11180000 | 0x14 | 0x12200000 |
| 3 | 0x12100000 | 0xc | 0x12000000 | 0x15 | 0x12280000 |
| 4 | 0x11080000 (IPS) | 0xd | 0x12500000 | 0x16 | 0x14900000 |
| 5 | 0x12180000 (L2) | 0xe | 0x12480000 | 0x17-1a | 0x10106000-0x10109000 |
| 6 | 0x12380000 | 0xf | 0x11380000 | | |
| 7 | 0x11300000 | 0x10 | 0x11000000 | | |
| 8 | 0x12300000 | 0x11 | 0x14000000 | | |

### Complete switch config diff captured (vendor vs our kernel, all 26 blocks)
63 differing registers, in `archive/diag/regs_vendor_full.txt` vs `archive/diag/regs_kernel_full.txt`
(diff generator inline). Confirms FAM at 0x11100010 = 0x00fa0032 (matches the
decompile's FAM init constant), plus config in the previously-unseen blocks
0x11180000, 0x11380000, 0x12080000, 0x12300000, 0x12380000, 0x12400000.

### THIRD static-replay attempt (complete 52-reg config + 0x121800b4 commit strobe)
Applied every differing switch register + the uni apply strobe on our running
kernel. STILL 100% loss. Static replay is conclusively insufficient (tested 3×
with increasing completeness).

### Why: the bring-up programs INDIRECT tables, not just registers
- L2/ARL MAC table: indirect cmd interface at 0x12180190 (cmd, bit0 start,
  self-clear), 0x194 (result, bit31=hit), 0x198/19c/1a0 (mac/fid/portmap).
- The "mix" layer (hi_kernel_dp_mix_init/mix_port_enable @0x2fc9c): a software
  bitmask `*DAT_0002fd30` of active ports that drives QoS SCHEDULER table commits
  via hi_kernel_dp_qos_operate_table_item + FUN_0002f8dc (more indirect tables).
- glb/uni/l2 set enables via self-clearing strobes and ordered sequences with
  GE-MAC bring-up (mac+0x14/0x20/0x40/0x74/0x7c) + inner-MDIO + mux-lane enables.
None of this indirect/procedural state is reproducible from an MMIO snapshot.

### Bring-up call graph (hi_kernel_dp_init @0x30034)
reg_init → mix_init → glb_init → uni_init → security_init → acl_init → pdu_init
→ l2_init → qos_init → prbs_init. No trailing "fabric on" trigger; enables are
distributed across glb/uni/l2/mix. PON serdes/SPI (glb_spi_*, serdes_wr,
ponbist/ponlink) is GPON-uplink only and can be SKIPPED - the LAN port is reached
via GE MAC + inner MDIO.

### Topology (from vendor CLI, telnet port 26)
LAN = switch port 0, CPU = switch port 12, bridged on VLAN 4094 with MAC learning.
(Decompile's "CPU port 4" is the internal FAM/switch index; the L2 MAC table
reports the logical CPU port as 12.) Vendor CLI: /home/cli/hal/{l2,port,flow},
/home/cli/chip_ext/{mr,mw,phymr,phymw}. Dump cmds printk to dmesg; _get cmds do not.

### CONCLUSION / remaining work
A working NIC requires PORTING hi_bridge's procedural init (glb_init + uni_init +
l2_init + mix/qos indirect-table programming + GE-MAC/MDIO bring-up), not a
register replay. All base addresses, the call graph, the indirect-table protocols,
and the complete target register state are now documented. The dev loop is ready:
`archive/diag/netboot.py` (our kernel) + vendor root shell (telnet :26, mr/mw + CLI) for
side-by-side verification. This is the next big chunk - a multi-stage driver port.

## 2026-07-27 - SOLVED: working NIC via kexec hand-off + VLAN strip

**Bidirectional networking works on mainline Linux 6.12.** host<->ONU ping 0% loss.

### The winning approach: kexec from the vendor kernel
Porting the proprietary switch init (hi_khal+hi_bridge, indirect QoS/MAC/VLAN
tables) is intractable to replay statically.  Instead: let the VENDOR kernel bring
the L2 switch up (it does this perfectly at boot), then **kexec into our mainline
kernel WITHOUT a hardware reset**, so the switch fabric state persists and our PIE
driver inherits a fully-working switch.

Pieces:
1. Vendor 2.6.34 has CONFIG_KEXEC (machine_kexec, sys_kexec_load in kallsyms).
2. `archive/diag/kexecldr.c` - freestanding ARM static binary (raw syscalls, nolibc
   toolchain): mmaps Image/dtb/trampoline from /tmp, kexec_load(entry=trampoline),
   reboot(KEXEC).  Built with arm-linux-gnueabi-gcc, ~2.5KB.
3. Files pushed to the vendor via its own `tftp` client; ~9MB RAM freed by killing
   userspace daemons + drop_caches (Image kimage copy needs ~7.6MB).
4. Trampoline reused as kexec entry - but MUST reset CP15 state the outgoing
   kernel left (TTBR0/1=0, CONTEXTIDR=0, DACR=~0, TLBIALL, ICIALLU, BPIALL,
   dsb/isb).  Without this the incoming kernel data-aborts reading 0x80008000.
5. `archive/diag/kexec_boot.py` - one command: boot vendor, transfer, free RAM, kexec,
   bring up eth0, ping.

### Two driver fixes that made it work
1. **Removed the 0x14880030 "datapath pulse"** from sd5116_open().  That bit-toggle
   resets the whole datapath subsystem, wiping the inherited L2 switch config.  We
   must touch ONLY the PIE (CPU DMA) and leave the fabric alone.
2. **Strip the management VLAN tag on RX.**  The switch presents the CPU port as an
   802.1Q TRUNK: frames arrive tagged VID 4094 (confirmed by dumping a live RX
   buffer: `...81 00 0f fe...`).  Our untagged eth0 was mishandling every tagged
   unicast (that's why only flooded broadcast "worked" and unicast replies were
   lost).  The driver now removes the 4094 tag on RX (memmove the 12-byte L2 header
   over the 4-byte tag).  TX stays untagged - the CPU port's PVID re-tags on ingress,
   so our source MAC gets learned in VID 4094 and host->ONU unicast forwards to CPU.

### Topology recap (vendor CLI, telnet :26)
LAN = switch port 0, CPU = port 12, bridged on VLAN 4094, MAC learning on.
Board MAC BC:98:89:09:5D:98.  With RX de-tagging + TX PVID learning, no manual FDB
entry is needed - the switch learns our MAC naturally.

### Remaining polish (not blocking)
- RX is poll-timer driven (10ms) not IRQ - latency ~12ms avg.  Wiring the real IRQ
  (GIC SPI 87) would cut latency and CPU.
- The NIC only works when reached via kexec from the vendor (needs the vendor to
  init the switch).  A cold U-Boot boot still lacks switch config.  Long-term: port
  the minimal switch bring-up, or ship a two-stage boot (vendor stub -> kexec).

## 2026-07-27 - Native switch bring-up (#2) attempt: config replay works, engines don't

Goal: cold U-Boot boot with no vendor kernel. Implemented sd5116_switch_init()
(driver) that replays the vendor's golden switch state.

### What was reproduced (all direct-mapped, verified on cold boot)
- 131 config-register writes across all switch blocks (archive/diag/sd5116-sw-cfg.h,
  generated from regs_vendor_full.txt + vendor_tables_extra.txt).
- VLAN membership table 0x124c0000+vid*0x10: VLAN 4094 = 0x01190111 (verified).
- Per-port PVID/deftag 0x124d7000: ports 0/4/8 = 0x00010ffe (PVID 4094).
- TPID 0x12000010=0x88a88100. L2 master 0x12180180=0x80000050. FAM 0x11100010
  =0x80fa0032. DP enable 0x11200040=0x80000000. Datapath ctl 0x14880030=0x462000c7.
- Apply strobe 0x121800b4 pulse.
On cold boot EVERY key register reads back matching the vendor. PHY links 1000/Full.

### But: RX=0, no forwarding. The engines are not procedurally initialised.
- The QoS scheduler indirect interface (base 0x12080000: cmd +0x48, data +0x4c.., 
  op = qos_operate_table_item type 0x10) returns register RESIDUE, not table data,
  on cold boot -> the QoS engine is not live even though its config regs are set.
- Adding the datapath-control block (0x14880030 enable + 0x1c/28/2c/34/60/64/84/90)
  did not change it.
=> The L2/QoS/datapath ENGINES have internal state machines that must be walked
through their init sequences (indirect table programming with the engine live,
possibly a clock/reset ordering). A complete register+table snapshot replay is
NOT sufficient - same fundamental "procedural, not state" barrier, now localised
to the indirect engine bring-up.

### Ground-truth resolved this session (for whoever finishes the port)
- Full register base table (26 blocks) + these table bases (ELF g_dp_* symbols):
  VLAN mem 0x124c0000, ARL MAC 0x121c1000, PUB deftag 0x124d7000, MCC group
  0x123c4000, port-iso 0x123c9000, QoS engine 0x12080000.
- l2_init / glb_init / uni_init ordered write sequences (in this file's agent
  notes / archive/diag/switch_init_target.md).
- QoS scheduler op: qos_operate_table_item(type,op,index,4words) via 0x12080000
  +0x48 cmd (bit31 start, op<<30, type<<20, index), +0x4c-58 rdata, +0x5c-68 wdata;
  queue-enable = qos_queue_sch_enable(q,en): bit (q&3)*8+7 in word (q&0xc) of entry q>>4.

### Current status
- WORKING NIC via kexec hand-off (#1): unchanged, still works.
- sd5116_switch_init() is GUARDED (skips if L2 master bit31 already set) so it does
  NOT disturb the kexec path; it runs only on a cold boot, where it configures the
  fabric correctly but the engines don't yet forward.
- Remaining work for #2: RE + implement each engine's procedural init (QoS scheduler
  queue-enable per active port via the indirect iface, and whatever engine/clock
  enable makes the L2 datapath process packets). This is a multi-stage driver port.

## 2026-07-28 - Native cold-boot: PROVEN impossible via register replay

Fixed a guard bug (0x12180180 bit31 is set at power-on, so switch_init was being
skipped on cold boot). With it fixed, switch_init runs the full sequence:
- CRG clocks (U-Boot already leaves 0x10107000/8000/9000 = 0xffffffff),
- datapath reset pulse (0x14880030 clear->set),
- 133-reg config replay + VLAN/deftag/TPID tables,
- GE MAC enable writes (write-only bits, 0x13500000 family),
- DP re-assert (0x11200014/4c/50), FAM/DP/L2 enables, apply strobe.

Full cold-boot vs vendor register diff (archive/diag/regs_coldboot.txt vs regs_vendor_full.txt):
**only 6 registers differ, all dynamic/status/WDT** - the switch config is byte-identical
to the working vendor. YET RX=0, no forwarding. GE MAC MIB counters never increment
(and read 0 on the vendor too - the LAN port does NOT ingress via 0x13500000).

CONCLUSION (definitive, exhaustively tested): the L2 switch forwarding depends on
internal engine STATE-MACHINE state that leaves NO register footprint. The vendor's
hi_bridge init drives each engine through transitions (edges/handshakes/ordering);
replaying the identical final register values does not reproduce the running state.
Register/table snapshot replay CANNOT make this switch forward - proven by an exact
register match with zero forwarding.

Getting the switch running requires EXECUTING an init sequence that triggers the
engine state machines - i.e. the vendor's actual code, which is exactly what the
kexec hand-off runs. A pure-native cold boot would need bit-accurate reproduction of
every engine's init handshake sequence (not just register values), which is
impractical to RE from a static decompile + snapshot.

### Practical outcome
- WORKING NIC via kexec hand-off is the reliable path (runs the vendor's real init).
- sd5116_switch_init() (guarded on 0x12180108==0x00100d00) stays in the driver: it
  correctly INHERITS on the kexec path and does the full (config-correct but
  non-forwarding) native attempt on cold boot, as a documented foundation.
- Recommended product path: automatic two-stage boot (vendor stub -> kexec).
