# SD5116 switch cold-init target (for native port #2)

> Note: files named `*_decomp.c` and `uboot.bin` referenced below are decompilations and
> binaries of the vendor firmware; they are not part of this repository.

Goal: from a cold U-Boot boot (no vendor kernel), bring the L2 switch up so it
bridges LAN(port0) <-> CPU(port12) on VLAN 4094. Then our PIE driver + RX VLAN
strip = working NIC with no vendor code.

## Register base table (phys; virt = phys+0xe8000000)
idx0 0x11280000  idx1 0x11200000(DP/uni)  idx2 0x11100000(FAM)  idx3 0x12100000
idx4 0x11080000(IPS)  idx5 0x12180000(L2)  idx6 0x12380000  idx7 0x11300000
idx8 0x12300000  idx9 0x12400000  idxa 0x12080000  idxb 0x11180000  idxc 0x12000000
idxd 0x12500000  idxe 0x12480000  idxf 0x11380000  idx10 0x11000000  idx14 0x12200000
idx15 0x12280000  idx13 0x10a70000(PIE)  GE MACs 0x13500000/0x13520000/0x13530000
inner MDIO 0x14600000

## Target indirect-table contents (from vendor CLI, telnet :26)
- sec_vlan (VLAN membership): vlan 4094 = {ETH0(port0), PON, CPU(port12)}
  (also 4041=ETH0 only, 4089=PON,CPU - not needed for us)
- sec_bc_vlan (broadcast): vlan 4094 = {ETH0, CPU, PON}
- L2 MAC table: learned dynamically (no static entry needed once RX de-tags + TX PVID)
- QoS egr scheduler (egr_sch_dump): 8 queues per port, default params
  (port0 qid81-88 pbuf=3500; each port has its 8). May be required for egress.
- Port tag: LAN port0 = PVID 4094, egress untagged; CPU port12 = tagged trunk (VID 4094)

## Full working register state
archive/diag/regs_vendor_full.txt (all 26 blocks from the working vendor). Notable non-default:
  FAM 0x11100010=0x00fa0032; DP 0x11200040=0x80000000, 0x1120007c=0x00000fff,
  0x11200014/18=0x80001d4c/0x1d4c, 0x11200034/38=0xc8; 0x11180020/24/28=0x22/0x44/0x66;
  0x11380020=7,0x11380040=0x1f40; 0x12080008=0x1f01; L2 0x12180108..13c per-port,
  0x12180148/150=0x11000, 0x12180180=0x80000050; 0x12300030=0x1ff01ff,0x12300034=0x148;
  0x12380008=0x800080; 0x12400020/40=0x10500000,0x124000c0=0x7f0041,0x124000f0=0x82;
  IPS 0x11080010=0x00790011.

## Init call graph (hi_kernel_dp_init @0x30034)
reg_init -> mix_init -> glb_init -> uni_init -> security_init -> acl_init -> pdu_init
-> l2_init -> qos_init -> prbs_init. GPON serdes/SPI/ponlink = SKIP.

## Progress 2026-07-28 (cold-native now matches kexec MMIO ~fully; still no forward)
Method that finally worked for diffing: capture the WORKING kexec state live via
devmem and compare (refs saved: archive/diag/ref_kexec_working.txt, ref_kexec_blocks.txt,
ref_kexec_rstctl.txt). Fixes applied to sd5116_switch_init this session:
- RSTCTL 0x14880000 block was missing 0x68=0x05050505, 0x6c=0x00000405,
  0x70=0xaa55a5a5 (magic key), and 0x33 was written to WRONG offset 0x84 instead
  of 0x74. Adding these made DP threshold 0x11200014 load 0x1d4c (was stuck 0x1fff).
- 0x11100010 must be 0x00fa0032 WITHOUT bit31 (I was setting 0x80000000; kexec has
  it clear). Correction: bit31 is not a commit bit, it is the flow-control enable of
  GE port 8 (`FAM_FC_EN` in the driver), off unless pause is negotiated.
- native_switch_init module param added (default 0 = inherit; =1 runs native init).
  DTS bootargs carries sd5116_eth.native_switch_init=1 for cold-boot testing.

State now (cold-native == kexec for ALL these): 0x11100010=00FA0032,
0x11200014=80001D4C, 0x11200040=80000000, 0x12180180=80000050, RSTCTL 0x68/6c/70/74.
ONLY remaining diff: 0x12180194=0 on cold vs nonzero+changing on kexec - this is
the L2 MAC engine's DYNAMIC learning/hash state, 0 because nothing forwards (symptom).

Key finding: TX frames are DMA'd (PIE clears OWN, TX=15) but do NOT egress the CPU
port into the fabric - even broadcast. U-Boot's OWN frames DO egress (TFTP works)
with the SAME switch config, so the blocker is how the kernel PIE feeds the CPU
port vs how U-Boot does, OR an L2/PFE ingress-side table for CPU port12 that is
NOT in MMIO (indirect table / procedural start). Inherit-from-U-Boot also fails
egress (kernel PIE != U-Boot PIE semantics). Next: RE the L2/PFE CPU-port ingress
path in hi_bridge (port12 ingress enable + DP->L2 binding), or capture L2/PFE
indirect tables from kexec state and replay. kexec remains the only working NIC.

## BREAKTHROUGH 2026-07-28 pt2: cold-boot EGRESS works; RX ~90% (final hop left)
Method: dump nonzero words of each indirect-table region from a WORKING kexec
state vs cold-native (driver sd5116_dump_tables, module param dump_tables=1),
diff, program the tables that read 0 on cold. The register snapshot NEVER
captures these (indirect tables read 0 until the vendor programs them).

Tables that were missing on cold and are now programmed in sd5116_fwd_tables_init:
- PA_portcfg  0x120c0000: per-port ingress cfg. word1=0x4000 all ports;
  word0=0x110000 for active ports {0,1,8,9,10,11,12,13}.
- MCC_mcg     0x123c4000: flood/mcast group memberships. [0]=0x4000,
  [1]=0x02002002,[2]=0x02001003,[3]=0x02004000,[4]=0x02001005,[5]=0x02004000,
  [6]=0x02006000, [7..]=0x4000 default.
- IFC_fact    0x12150000: ingress classification word0 per entry (e2=0x800,
  e4=0x11000, e5=0x1001000/w1=0x1000, e6=w1=0x3000, e7=0x1001000, e8/9/a=0x11000).
- OPP_qid1    0x12442800+0x40: queue-id map, 32 words 0x101..0x110 (pattern).
Programming PA_portcfg + MCC_mcg alone made EGRESS work (ONU->host, host learns
.20). IFC_fact + OPP_qid1 added for the RX path.

THE BIG FIX for RX ingress: the LAN port MAC is at **0x14300000** (per-port CBB
MACs 0x14300000 + port*0x80000; only port0 populated). The GE MACs at 0x13500000
are UNUSED (always read 0) - a long red herring. U-Boot leaves 0x14300000 set for
its slower link; after the kernel renegotiates 1000/Full the MAC must match the
vendor config or RX stays off. Writes (from kexec capture):
  0x14300020=9, 0x14300040=0xc0408, 0x14300074=0x1312d0, 0x1430007c=0xea6,
  0x14300014=6 (bit1=RX enable; U-Boot leaves 5=RX off).
After this, host frames INGRESS the fabric: IPS LAN-ingress counter 0x11081054
increments, MAC RX byte/frame counters (0x143000b0/b8) climb.

REMAINING (RX last hop): ingressed frames flood to VLAN-4094 members but DROP at
reason 0x5a (OPP drop_reason table 0x12446800 + port*0x200 + reason*4) on ports
8 and 4. CPU port egress -> PIE RX DMA delivery is the failing hop; L2 learning
(0x12180194) stays 0. Likely the CPU-port(8?)-egress -> PIE-RX-channel (EQM/EPS)
binding. Counters: CPU ingress(PIE->fab)=g_dp_IPS+0x101c=0x1108101c; CPU output
(fab->PIE)=g_dp_EPS_output_cnt+0x20=0x112c1020 (stays 0). uni_cpu_port_cnt_get
reads both. Ports w/ PVID4094 (deftag)= {0,4,8}; CPU is one of {4,8} (both drop
because PIE delivery fails). LAN phys = port 0.

Refs saved: archive/diag/tbl_kexec*.txt, tbl_k4/k5/k6.txt (kexec table dumps),
archive/diag/ref_kexec_*.txt. Decompile: archive/diag/hi_bridge_decomp.c (cbb_reg_init @619 maps
the 0x14300000 MACs; uni_cpu_port_cnt_get @3577 gives counter addrs).

## RX deep-dive 2026-07-28 pt3: forwarding to CPU works, final PIE-delivery hop left
Drop reason enum (rootfs_extracted/home/cli/chip/qos/dropreason_get): reason
0x5a = **NO_EGR** (no egress port). Reason 0x70 = normal flood-to-dead-member.
On kexec, host->ONU works via LEARNED unicast: switch learns board MAC on the CPU
port from egress TX, then host unicast forwards there. On cold, auto-learning does
not populate so unicast had NO_EGR -> added a STATIC FDB entry.

**CPU port = internal port 0x10 (16), NOT 8/12.** Physical LAN=port0; VLAN-4094
members (vlan_mem 0x124cffe0=0x01190111 low nibble 0x111 = ports 0,4,8) are the
flood set - the CPU (16) is reached by UNICAST only, never flood (flood-to-CPU is
NO_EGR even on kexec, count ~5). So RX bootstraps because the ONU TXes first, the
host learns .20, and its unicast reply forwards to the learned/static CPU entry.

L2 MAC table indirect iface (base 0x12180000): write inv-MAC(reverse 6 bytes) to
+0x198, (mac_hi | vid<<16) to +0x19c (vid = VID&0xfff, NO 0x1000 flag), port to
+0x1a0 as (port&0xffff)<<8, cmd (1<<4)|1 to +0x190, poll +0x190 bit0, result +0x194
(bit31=done). GET: same but +0x1a0=0, cmd=1; read port=(+0x1a0 & 0xffffff)>>8.
Board MAC bc:98:89:09:5d:98 learns as port=0x10 (matches kexec exactly, index 0x0e8).
Implemented in sd5116_add_static_mac() - VERIFIED byte-identical to the kexec entry.

Reproduced & VERIFIED-stuck on cold: full OPP_qid1 (0x12442800, offsets 0x40-0x2fc,
queues 0x101-0x158 - the CPU-port entries are at offset>=0x100, header
sd5116-opp-qid1.h); full EQM type-0x10 sched (0..0x40) + type-0x20 channel (TWO
banks 0..0x40 - second bank binds CPU-port channels; sd5116-eqm-sched.h regenerated);
OPP smp_car policer 0x12440040/50; PIE per-channel enables 0x10a7001c..44=1; PIE
MODE low-byte cleared; LAN MAC 0x14300000 for 1G.

**STILL RX=0.** Verified: host unicast to bc:98 is FORWARDED to port 16 (not flooded
- port4 NO_EGR does not increment), frames INGRESS the fabric (IPS 0x11081054
climbs), but never reach the PIE RX ring (0x10a70108/10c unchanged) and CPU-output
cnt 0x112c1020 stays 0. So the final CPU-port(16)->EPS->PIE delivery is the last
broken hop. Everything the vendor's connect_pon_queue (ports 16-23) touches is
reproduced (its "connected" bitmask @.bss 0x60a8 is SW-only; HW effects = OPP_qid1 +
type-0x10 sched, both matched). PIE soft-reset (CTRL|0x10->MODE) TIMES OUT (RST_DONE
never sets) - wrong reset method. Next suspects: OFC (Output Flow Control, fstat
0x112c3000 / fact 0x12350000) between OPP and EPS, or a CPU-port egress "start" not
in any captured table. kexec still = the only 100%-working NIC.

## RX deep-dive 2026-07-28 pt4: frame dropped at OPP ENQUEUE for CPU port 16
Host-side gotcha: the TFTP server (tftpd.socket) can silently stop between
sessions -> U-Boot tftpboot spins printing 'T', netboot.py hangs with no output.
Fix (needs auth): `sudo systemctl start tftpd.socket`; verify `ss -ulnp | grep :69`.
netboot.py also sometimes doesn't exit after boot - kill leftover PIDs via python
os.kill (shell kill/pkill are hook-blocked) before re-opening the serial.

Localized the final RX hop precisely: host unicast to bc:98 is FORWARDED to CPU
port 16 (not flooded), frame INGRESSES the fabric (IPS 0x11081054 climbs), but is
DROPPED at the OPP ENQUEUE for port 16 - it never enters a queue (EQM type-0x50
depth for the port-16 qids 0x11-0x18 stays 0 under a sustained flood), never
reaches the OFC/EPS (0x112c3000/0x112c0000 unchanged), CPU-out 0x112c1020 stays 0.
Ruled out: OFC engine (fact 0x12350000 / fstat 0x12360000 / 0x112c3000) is ZERO on
kexec too - not the mechanism. OPP_qid1, EQM sched(0x10)/channel(0x20), EPS all
match kexec (verified via EQM indirect read - NOTE: that read HANGS the kexec/vendor
switch state, only safe on cold; use region dumps on kexec).

Tried & rejected: reproduce connect_pon_queue via queue_sch_enable(qid) for ports
16-23 (RE: read EQM t10 idx=qid>>4, OR bit (qid&3)*8+7 into word (qid&0xc)/4, write
back; qid = low byte of OPP_qid1[0x200..0x2fc]).  It DOES set the enable bits
(t10 idx1: 0x0b0a099a -> 0x8b8a899a) but does NOT fix RX and DIVERGES from kexec
(whose snapshot has them clear). Code kept as sd5116_connect_cpu_queues() but
uncalled. So the OPP-enqueue-valid for port 16 is NOT the type-0x10 enable bits.

Remaining: what makes port 16 a VALID OPP egress (enqueue succeeds) on kexec.
connect_pon_queue's only HW effects are OPP_qid1 + queue_sch_enable, both
reproduced, yet enqueue still fails -> suspect a procedural queue-CREATE (EQM
type-0x50 queue alloc) or an OPP per-port egress-enable not in any captured table,
or the CPU-port(16)->PIE binding is established by a handshake the static replay
can't reproduce. kexec = only working NIC.

## Test loop for #2
Cold boot via `netboot.py` (U-Boot, switch at power-on defaults). Driver runs
sd5116_switch_init() before ring setup. Verify: dump switch regs via serial devmem,
diff vs regs_vendor_full.txt; then ping. Iterate.

## RX deep-dive 2026-07-28 pt5: queue-CREATE found; blocker is L2 forward-to-CPU (flood)
DISCOVERY (real): connect_pon_queue -> qos_qmap_table_item_set -> mix_fqid_table_
item_set, and the queue-CREATE is add_queue_to_scheduler writing EQM **type-0x22**
(queue->scheduler bind; bit 0x40000=VALID, [0:4]=sched, [14:17]=weight, bit13 flag)
+ **type-0x23** (priority). Captured from kexec (queues 0x11-0x18 = CPU port 16 map
to sched 0x10, weights 0-7, prio 7-0):
  t22 i11..i18 = 42010 46010 4a010 4e010 52010 56010 5a010 5e010
  t23 i11..i17 = 70000 60000 50000 40000 30000 20000 10000  (i18=0)
Type-0x50 (depth) reads 0 for these queues on kexec too (NOT the differentiator).
Implemented sd5116_create_cpu_queues() (called from switch_init); VERIFIED applied
in HW on cold (devmem t22 i11 read-back = 0x42010, byte-identical to kexec). These
indirect entries were never captured/programmed before - a genuine missing piece.
The FUN_0002f8dc "bitmask @.bss 0x60a8" is SW-only (confirmed via objdump: literal
0x60a8 = .bss offset) - NOT a HW register; connect_pon_queue's HW effects are only
OPP_qid1 bit8 + type-0x10 sched (both already matched) + type-0x22/0x23 (this fix).

BUT RX still 0. Root-caused with reliable host->ONU unicast (user set a static host
ARP: `sudo ip neigh replace 192.168.1.20 lladdr bc:98:89:09:5d:98 dev enp6s0`):
- COLD: host unicast to bc:98 INGRESSES but FLOODS to VLAN-4094 members {0,4,8} and
  drops as NO_EGR at port 8 (drop cnt 0x12446800+8*0x200+0x5a*4 increments at the
  ingress rate). Never forwards to CPU 16. tocpu-reason cnts + CPU queue depths stay 0.
- KEXEC (same host flood): p8 NO_EGR FLAT (~10); CPUout 0x112c1020 climbs; eth0 RX
  climbs; ping 4/4. So the (bc:98,vid4094) L2 lookup HITS->CPU on kexec, MISSES->flood
  on cold. This is BEFORE the OPP enqueue, so the queue-create can't be validated yet.

FDB is a RED HERRING for inspection: the devmem indirect GET (write 0x198/0x19c,
0x1a0=0, cmd=1 to 0x190, read 0x194/0x1a0) is UNRELIABLE for read-back - on the
WORKING kexec board it returns 0x194=0x002400E8 (bit31 CLEAR) and 0x1a0=0 (port 0),
IDENTICAL to cold. So earlier "driver FDB add silently failed / bit27 missing / port
0 vs 16" were readback artifacts, not real. The ARL l2mac table (0x121c1000) is
indirect-only (raw reads = 0). Can't compare FDB entries via devmem.

L2 direct config MATCHES kexec register-for-register: dumped kexec 0x12180000-0x188
== driver sw_cfg.h exactly (0x108=0x00100d00, 0x114=0x00111d03, 0x134=0x00111903,
0x140=0xffff0000, 0x180=0x80000050, etc. all identical). deftag 0x124d7000 (port0
PVID 4094) matches; VLAN-mem 0x124cffe0=0x01190111 matches. So the forwarding
difference is NOT in any direct register - it is procedural indirect L2/ARL state
(FDB learning: kexec LEARNS bc:98 on the CPU port from the ONU's egress TX; a SW
static add does not reproduce a forwarding-valid entry). Same fundamental wall as the
original port (procedural indirect-table init), now isolated to L2-forward-to-CPU.
uni_cpu_port_attr_set just calls set_port_tag_attr(4,..) (tag attrs, not learning).
No "learn/aging" symbols in hi_bridge decompile - likely in hi_khal or a data-driven
init. NEXT: RE the L2/ARL learning enable + CPU-port forward-member setup (hi_khal),
or live-trace the vendor cpu-port L2 bring-up. kexec = still the only 100% NIC.
Boot: cold `python3 archive/archive/diag/netboot.py`; working NIC `python3 archive/archive/diag/kexec_boot.py`.

## RX deep-dive 2026-07-28 pt6: hi_khal decompiled - CPU MAC is LEARNED, not static
Decompiled hi_khal.ko (Ghidra headless, DecompKhal.java -> archive/diag/hi_khal_decomp.c,
1052 funcs). Traced the L2 MAC add path end to end:
  hi_kernel_hal_bridge_mac_add -> __hal_port_remap(port) -> hi_kernel_dp_l2_mac_add
  -> l2_mac_table_item_ind_set. l2_mac_add sets the FDB port field as **1 << port**
  (a BITMASK), and item_ind_set writes it as (bitmask & 0xffff) << 8 into 0x1a0.
**For the CPU port (internal 16): 1<<16 = 0x10000, &0xffff = 0 -> the port field is
0.** So the standard static-add path CANNOT represent the CPU port. __hal_port_remap
maps SDK 0x10->16 (CPU passes through), confirming 16 is the value used. Ports 0-3
(LAN/GE) map to 8-11, so LAN static entries work (1<<8..11 fit in 16 bits); the CPU
port does not.

CONCLUSION (redirects the whole approach): the vendor does NOT static-add the board
MAC bc:98 on the CPU port - it is **hardware-LEARNED** from the ONU's egress TX
(SMAC bc:98 enters the fabric at the CPU port; the switch learns bc:98 -> port16 with
an internal encoding SW can't express). Our sd5116_add_static_mac() is fundamentally
the wrong mechanism for the CPU MAC; that is why cold floods (no learned/usable entry)
while kexec forwards (vendor-learned entry). The devmem FDB GET can't show this (it
reads the same on both).

Per-port learning bitmask 0x121801c0 = 0 on BOTH kexec and cold (from l2_attr_set:
base 0x12180000 +0x1c0, bit/port), and 0x148-0x154 match - so learning is on by
default and NOT the missing knob. The missing piece is whatever makes the CPU-port
INGRESS (PIE->fabric) trigger SMAC learning on cold. CPUin 0x1108101c climbs on cold
(frames DO enter the fabric at the CPU port) yet bc:98 is not learned -> the cold
CPU-port ingress path is missing the learn trigger / a CPU-port ingress attr.
NEXT: RE the CPU-port ingress (learn-on-ingress) config - compare the CPU-port
ingress attr/enable kexec vs cold; or find the vendor's explicit "add cpu mac" /
self-mac trap path if one exists. hi_khal decompile now available: archive/diag/hi_khal_decomp.c.
kexec = still the only 100% NIC.

## RX deep-dive 2026-07-28 pt7: FDB+queue RULED OUT (byte-identical); CPU port gets 0 fabric frames
Added a single-ioremap FDB GET to the driver (devmem's per-word mmap/unmap breaks the
L2 indirect result latch -> every devmem GET wrongly reads not-found; the driver keeps
the region mapped once and reads correctly). Ran it on BOTH kexec (inherit) and cold:
  KEXEC  FDB get vid=0xffe: 0x194=802400e8 0x1a0=00001000  (FOUND, port16, index 0xe8)
  COLD   FDB get vid=0xffe: 0x194=802400e8 0x1a0=00001000  (IDENTICAL - FOUND)
So the CPU FDB entry IS present on cold and BYTE-IDENTICAL to kexec. pt6 was WRONG:
the CPU entry is a plain port-NUMBER encoding (0x1a0=0x10<<8), fully static-addable;
l2_mac_add's `1<<port` bitmask analysis was a red herring (the learned entry uses the
number form). The earlier "add ages out / not found" were ALL devmem-GET artifacts.
EQM queue-create also byte-identical on cold (t22 i11=0x42010). And a FULL diff of the
L2 engine regs 0x12180104-0x188 cold-vs-kexec = 0 differences. deftag/vlan-mem match.

So EVERYTHING readable is identical yet cold FLOODS and kexec FORWARDS. Measured on
cold under host unicast (host static ARP set): lanin +52, p8 NO_EGR +51 (~every frame
floods+drops at port8), **tocpu 0x12445800 +0, cpuout 0x112c1020 +0**. Neither
static-add NOR ONU-TX hardware-learning makes host->bc:98 forward to CPU. The CPU port
(16) receives ZERO frames from the fabric on cold (tocpu stays 0), by unicast OR flood.

CONCLUSION (irreducible): the blocker is NOT the FDB, NOT the queue, NOT any readable
register (all byte-identical to kexec). It is invisible PROCEDURAL datapath state: the
software indirect-add populates the L2 MANAGEMENT table (GET reads it) but evidently
not the datapath lookup SRAM the packet engine consults; and the CPU port is not a
functional fabric EGRESS on cold (tocpu=0). The vendor's full procedural bring-up
establishes this datapath/CPU-egress state with no register footprint the snapshot
captures. This is the maximal isolation of the original "procedural indirect init
can't be replayed" wall. Remaining avenues: live-trace the vendor's L2 datapath/
CPU-egress commit (what it does AFTER writing the indirect tables), or GPL source.
kexec = the only 100% NIC. Boot: netboot.py (cold), kexec_boot.py (working NIC).

## RX deep-dive 2026-07-29 pt8: CPU-egress path (OPP/EPS/EQM_dp) also byte-identical
Captured OPP_smp_car(0x12440000), OPP_cpu_attr(0x12440400), EPS in/out(0x112c0000/
0x112c1000), EQM_dp direct(0x11200000-0x60) from kexec (driver dump, inherit) vs cold.
Result: ALL match except two NON-CAUSAL items:
- 0x1120004c cold=0 kexec=0x1400: this is the EQM indirect RDATA[0] register (residue
  of the last indirect read), NOT config - a devmem write of 0x1400 does not stick
  (read-only/auto). (So switch_init's write of 0x4c is a no-op misconception.)
- OPP_smp_car per-port 0x20 policer values slightly misplaced (0xc8 vs 0xe0/0xf8,
  ports ~9-11, not the CPU port). Fixing them live changed nothing.
OPP_cpu_attr(0x12440400) = all-zero on BOTH. EQM_dp direct regs all covered by sw_cfg
and match. Applying both diffs on the live cold board: cpuout/tocpu still +0, p8 still
increments (floods), eth0 RX still 0.

FINAL (this line of investigation is exhausted): EVERY readable region - L2 config,
FDB entry (single-mapping GET), EQM queue-create (t22/t23), IPS, OPP (smp_car+cpu_attr),
EPS, EQM_dp, deftag, VLAN membership - is byte-identical between working-kexec and
broken-cold. Yet cold FLOODS host->bc:98 to {0,4,8}/NO_EGR and never forwards to CPU
port16 (tocpu=0, cpuout=0), by unicast OR flood, with neither static-add nor HW-learning
changing it. The blocker has ZERO MMIO footprint: it is the vendor's procedural
forwarding-DATAPATH commit (internal lookup/forwarding SRAM the packet engine consults,
not exposed as a readable register/indirect table). Capture-and-replay CANNOT cross
this. Only remaining paths: (1) live-trace the vendor's datapath commit (what hi_bridge/
hi_khal write to the forwarding engine AFTER the indirect-table adds), (2) GPL source.
kexec = the only 100% NIC. (Host-side gotcha reconfirmed: tftpd.socket dropped again mid-
session -> all boots failed until `sudo systemctl start tftpd.socket`.)

## RX deep-dive 2026-07-29 pt9: BREAKTHROUGH - datapath ignores mgmt FDB; learning works on port0, NOT CPU port
Two RELIABLE driver-based tests (not devmem) cracked the mechanism:
1. cpu_port=4 (driver writes bc:98->port4 in the MGMT table at 0x12180190, single-mapping,
   boot-confirmed): host unicast to bc:98 STILL floods to port8 (p8 +18 = lanin +16, p4 +4
   noise). => the L2 DATAPATH IGNORES the management FDB table entirely. My whole FDB-write
   approach targets a table the packet engine does not consult. The datapath uses a SEPARATE
   hardware hash, populated by HW LEARNING (mgmt index 0xe8 != datapath hash index).
2. ONU->host egress on cold: p8_NO_EGR +0 => ONU->host is UNICAST, not flooded => host_mac
   IS LEARNED on port0. So HW learning + datapath lookup WORK on cold.

So the gap is now precise: learning into the datapath hash works for the LAN port (0) but
bc:98 is never learned on the CPU port (16). host->bc:98 floods because bc:98 is absent from
the datapath hash; the ONU's TX (SMAC bc:98 via CPU-port ingress) is NOT being learned, while
external frames on port0 ARE. This is CPU-PORT-INGRESS-LEARNING specific.
(Corrects pt7/pt8 "everything identical, invisible wall": the wall is the datapath hash CONTENT,
and it IS reachable via learning - just not on the CPU port on cold.)

NEXT (concrete): make CPU-port(16) ingress learn its SMAC. Options: (a) find the CPU-port
ingress learn-enable/attr the vendor sets (uni_cpu_port_attr_set/set_port_tag_attr(4,..),
or a per-port learn bit for port16 - 0x121801c0 reads 0 on both so it's elsewhere); (b) find
the datapath-hash STATIC add interface (separate from 0x12180190) and add bc:98->port16
directly at the datapath hash index. Note: mgmt GET/writes at 0x12180190 are the WRONG table.
Board test rig: host static ARP set; cpu_port module param overrides the FDB port for tests.
kexec = only 100% NIC.

## pt10: fork(b) + learn-enable both dead ends; blocker = CPU-port datapath entry population
- fork(b) (make CPU a flood member): flood-to-CPU is NO_EGR even on kexec (by design) -
  MCC_mcg flood groups exclude port16; CPU is reached by UNICAST only. Not the path.
- Forcing per-port learn 0x121801c0=0x1ffff (port16 bit set) + ONU-TX priming: bc:98
  still not learned, host->bc:98 still floods, cpuout=0. So 0x1c0 is not the CPU-port
  learn lever, and CPU-injected (PIE TX) frames are not learned into the datapath hash.
- The two-table split is confirmed: driver single-mapping GET reads the MANAGEMENT table
  (bc:98 present, byte-identical kexec/cold); the DATAPATH hash (what the packet engine
  consults) is separate, populated by HW learning. Learning works for external ports
  (host_mac->port0 proven) but NOT for the CPU port on cold. No known SW interface writes
  the datapath hash directly; mgmt 0x12180190 does not.
Blocker (final, precise): get bc:98->port16 into the DATAPATH hash. Mechanism the vendor
uses is unidentified - not per-port learn (0x1c0), not a found station-MAC trap register,
not the mgmt FDB. Candidates remain: a CPU-port ingress "learn CPU-injected SMAC" enable,
or a static datapath-hash add the vendor does at init (no register footprint found). This
needs the vendor init SOURCE or a proper live-trace of hi_bridge writing the datapath at
boot. kexec = only 100% NIC.

## pt11 (2026-07-29) MAJOR: found the datapath SRAM + the forwarding-valid bit + learning-only
Scanned the ARL SRAM for the board MAC (driver in-kernel scan). Found the DATAPATH MAC
table is directly memory-mapped at **0x121c1000** (16-byte entries, index = 0x121c1000 +
idx*16). The working kexec bc:98 entry lives at idx 0xe8 (0x121c1e80):
  +0 = 0x89095D98 (inv mac lo)   +4 = 0x0FFEBC98 (vid0xffe | 0xbc98)
  +8 = 0x40001000 (word2)        +C = 0x00000002
**word2 bit30 (0x40000000) = the forwarding-valid bit.** On COLD my management-added
entry lands at the SAME idx 0xe8 but word2 = 0x20001000 (bit29) - so the entry IS present
but NOT forwarding-valid. Writing bit30 directly: hardware converts 30->29. Writing the
full entry: word0 is the commit trigger (write word0 LAST or it zeroes the rest); it then
STICKS momentarily with bit30 but the HARDWARE EVICTS it within seconds. cmd0x11 (static)
-> bit29 entry, evicted. cmd0x21 (dynamic) -> no entry created. So the datapath table is
HARDWARE-MANAGED (learning only); SW/management adds do not produce a persistent
forwarding-valid entry.

Learning test (heavy bidir traffic + SRAM scan): background segment MACs DO learn (4
entries, some bit30 some bit29) but neither host_mac (port0) nor bc:98 (CPU port16) was in
the table. So learning works generally but NOT for the CPU-port SMAC. (Note port-field
encoding puzzle: kexec bc:98 word2=0x1000, background learned entries word2=0x10000 - the
w2 port field format is still not fully decoded; may be a bitmask vs number mismatch worth
revisiting.)

BLOCKER (precise, hardware-gated): bc:98 must be LEARNED on CPU port16 to get a persistent
bit30 datapath entry at 0x121c1e80. CPU-port ingress learning does not happen on cold;
0x121801c0 per-port learn had no effect; management/direct SRAM writes are evicted. The
vendor's CPU-port-learn mechanism has no register footprint found. This is the true final
barrier. Leads left: (1) exact w2 port-field format (0x1000 vs 0x10000) - maybe my entries
even have the wrong port and a correctly-encoded LEARNED entry differs; (2) the fabric
CPU-port ingress "learn CPU-injected SMAC" enable (not 0x1c0); (3) watch the vendor learn
bc:98 live (boot vendor, clear 0x121c1e80, retrigger, diff). kexec = only 100% NIC.

## pt12 (2026-07-29) MAJOR CORRECTIONS + comprehensive ruling-out
Two session-long errors corrected on the working kexec board:
1. **RX does NOT use an ARL/FDB entry for bc:98.** During ACTIVE host->ONU traffic with
   RX working (cpuout climbing), bc:98 is NOT in the ARL table 0x121c1000 at all. The pt11
   "datapath entry at 0x121c1e80" was incidental/aged and RX works without it. host->bc:98
   reaches CPU as a clean 1:1 unicast/flood-to-CPU (cpuout=lanin), NOT via a MAC lookup.
   bc:98 is NOT stored as a trap MAC in ANY scanned register (all engine blocks first 0x400,
   LAN MAC 0x14300000, PUB MAC tables 0x124d6000/db000/dc000/d9000 - all clean).
2. **The correct RX indicator is cpuout=0x112c1020 (EPS output), NOT the OPP tocpu-reason
   0x12445800.** tocpu-reason stays 0 even on kexec where RX works (+0 under flood). ALL my
   earlier "tocpu=0 -> no CPU delivery" conclusions were measuring the wrong counter. Under
   host flood: KEXEC cpuout+61=lanin+61, p4/p8+0 (no flood to 4,8); COLD cpuout+0, p8 climbs
   (floods to 4,8). So the flood/forward SET differs: kexec LAN->{CPU}, cold LAN->{4,8}.

Comprehensively ruled out (ALL match kexec byte-for-byte OR zero-on-both): ARL table,
every engine register block, PA_portcfg (idx0,1,8-13 EN on both; CPU idx16=0 on both - my
active list matches exactly), MCC_mcg, VLAN4094 membership (0x124cffe0=0x01190111,
0x124cffe4=1), LAN MAC block, PUB MAC tables, OPP smp_car/cpu_attr(zero), EPS pie_chid(zero),
EQM queue-create(0x22/0x23)/sched(0x10)/channel(0x20)/depth(0x50 all idle-0).

BLOCKER (irreducible): the VLAN-4094 LAN(0)->CPU(16) forwarding + CPU-port egress delivery
to PIE (cpuout) works on kexec and not cold, with ZERO readable register/table/SRAM
difference. Every observable is reproduced. This is the fundamental procedural-datapath wall.
The flood-set difference (kexec ->{CPU}, cold ->{4,8}) is the sharpest remaining clue - the
per-port egress-ACTIVE state differs (kexec: 0,16 active / 4,8 inactive; cold: 0,4,8 active /
16 inactive) with no found register. kexec = only 100% NIC.

## pt13 (2026-07-29) VENDOR INSTRUMENTATION - live bridge toggle-diff
Booted the VENDOR kernel (2.6.34, root shell telnet:26). Findings:
- Vendor uses a STANDARD Linux bridge: br0 (MAC bc:98, IP .1) with member eth0 (the CPU/PIE
  port). hi_bridge is the hardware offload. host->bc:98 reaches CPU because bc:98 is br0's
  local MAC and LAN<->CPU is the bridge offload. /dev/mem+devmem+insmod all available; NO
  ftrace/kprobes; kallsyms present.
- Built static ARM nolibc /dev/mem tools (switchscan/switchdump). NOTE: broad native scans
  HANG the switch bus (confirmed - froze the scan process); nolibc mmap2/printf also flaky on
  2.6.34 (fell back to busybox devmem shell loops, which work).
- **Decisive live experiment**: `brctl delif br0 eth0` (tears down LAN->CPU offload; connectivity
  DID drop, proving hi_bridge offloads bridge membership) then `addif`, dumping safe register
  regions before/after via devmem. DIFF result: changes are ~ENTIRELY COUNTERS (EPS in/out
  0x112c00xx/0x112c10xx, ARL-engine 0x121c00xx - all incrementing). The ONLY non-counter change:
  OPP smp_car (policer) per-port shift at 0x124400a8(0->0x20)/0xb0(0x20->0)/0xf8(0x20->0).
- CONCLUSION: even directly instrumenting the WORKING vendor and toggling the exact LAN->CPU
  forwarding reveals NO clear forwarding-enable register - only counters + a subtle OPP policer
  reshuffle. Deepest confirmation that the forwarding/CPU-delivery state has no reproducible
  register footprint capture-replay can copy. (Caveat: dumped a bounded safe region set; a
  forwarding reg could sit in an un-dumped/bus-hang region. The OPP smp_car 0x124400a8/b0/f8
  shift is the only concrete lead and matches an earlier kexec-vs-cold smp_car discrepancy.)
- The only instrumentation that could go deeper is a kmod hooking hi_bridge's writel() - needs
  the vendor kernel's exact build env (Module.symvers/config); insmod rejects vermagic mismatch,
  so not feasible with what's on the board. kexec = only 100% NIC.

## pt14 (2026-08-11) **PORT NUMBERING CRACKED** - CPU port is internal 4, NOT 16
Source: rootfs_extracted/home/cli/hal/port/port_add documents the HAL port enum, and
hi_khal's __hal_port_remap maps HAL->internal:
  HAL ETH0-4 = 0-4   -> internal 8-11(+)
  HAL PON0/1 = 9/10  -> internal 0/3
  HAL PRBS   = 11    -> internal 3
  **HAL CPU0 = 12    -> internal 4**   (CPU1-3 = 13-15)
So: **internal 0 = PON, internal 4 = CPU, internal 8 = LAN(ETH0)**.
This is CONFIRMED by three independent facts that now all line up:
 - VLAN4094 membership 0x124cffe0 low half = 0x0111 = bits 0,4,8 = PON+CPU+LAN.
 - PUB_port_deftag 0x124d7000 stride 4: idx 0,4,8 = 0x00010ffe (PVID 4094) = same 3 ports.
 - FDB port field is a BITMASK: l2_mac_add uses (1<<port), item_ind_set writes
   (bitmask&0xffff)<<8. For CPU port 4: (1<<4)<<8 = **0x1000** = exactly the kexec entry's
   0x1a0 value. (My driver wrote cpu_port=0x10 as a NUMBER <<8 = 0x1000 - accidentally the
   same value, which is why the entry looked byte-identical. It is CORRECT, by luck.)

CONSEQUENCES - these invalidate/correct earlier conclusions:
 - **pt9 is INVALID**: the `cpu_port=4` test wrote (4<<8)=0x400 = bitmask bit2 = port 2 (a
   non-member), so of course it flooded. It did NOT prove "the datapath ignores the mgmt FDB".
 - The CPU port's OPP_qid1 entries are at offset 0x80 (port4*0x20) = qids 0x109-0x110 =>
   **CPU queue indices 0x09-0x10**, NOT 0x11-0x18. sd5116_create_cpu_queues() programmed the
   WRONG queues (0x11-0x18 belong to scheduler 0x10). Verified: EQM t22 i09-i10 (scheduler
   field = 4 = CPU) are ALREADY PRESENT AND IDENTICAL on both cold and kexec => the CPU
   queue-create was never the problem at all.
 - Drop counters: port8 = LAN (ingress port; NO_EGR there = normal don't-forward-to-source
   during FLOOD), port4 = CPU. Cold: p8 NO_EGR climbs at ingress rate (=> FLOODING => FDB
   MISS). Kexec: p8 flat, cpuout=lanin 1:1 (=> FDB HIT, unicast to CPU port 4).
 - STP state (port_stp_state_set) applies only to internal ports 8-12 (guard `local_14-8U<=4`)
   so it does NOT apply to the CPU port - not the lever.
Vendor CLI tree (rootfs_extracted/home/cli/) documents the whole chip API incl. function
names: hal/l2/* (l2_mac_dump, l2_mac_table_getbyport, l2_learn_*), hal/port/*, vif/*
(hi_kernel_vif_cmd_bind(ifname,port,svlan,cvlan), br_portadd), chip/uni/cpu_port_attr_* (VLAN
tag actions only). hi_cli invokes these kernel functions BY NAME from userspace = a live
semantic instrumentation path on the vendor. hi_kvif.ko decompiled -> archive/diag/hi_kvif_decomp.c.
STILL OPEN: why the (bc:98, vid4094) lookup MISSES on cold while the entry is present with the
correct bitmask. Next: use the vendor's own CLI (hal l2_mac_dump / l2_mac_table_getbyport) to
dump the WORKING FDB semantically, and compare with cold.

## pt15 (2026-08-11) **VENDOR CLI GIVES A SEMANTIC FDB DUMP** - CPU entry is LEARNED, not static
The vendor CLI runs kernel funcs by name: `cli /home/cli/<group>/<cmd>` (usage:
`cli SCRIPT [-f idx] -v key val...`; output goes to the KERNEL LOG - read with dmesg).
`cli /home/cli/hal/l2/l2_mac_dump` on the WORKING vendor prints the real FDB:
    index   mac                  vlan   port   static_en
    232     bc:98:89:09:5d:98    4094   12     0      <-- BOARD MAC, HAL port 12 = CPU, LEARNED
    508     d8:5e:d3:f0:92:c2    4094   0      0      <-- HOST MAC, HAL port 0 = ETH0/LAN
    (+ ~18 more LAN MACs on vlan 4094 port 0; mcast on vlan 4088 port 9 static_en=1)
This CONFIRMS pt14 end-to-end: printed port is the HAL number (internal->HAL via
__hal_port_remap_sdk), so bc:98 internal port 4 prints as HAL 12 (CPU0), and the LAN MACs
internal 8 print as HAL 0 (ETH0). index 232 = 0xE8 -> table addr 0x121c1000 + 232*0x10 =
**0x121c1e80** = exactly the address where I found the entry. Everything is consistent.
Entry decode (from l2_mac_table_item_get): table = 0x121c1000, stride 0x10; w0/w1 = inverted
MAC; vlan = (w1 & 0xfffffff)>>16; **port = log2((w2>>8) & 0xffff)** (BITMASK, so CPU port4 ->
w2 port field 0x1000); w2 bit31 -> another flag.

**THE KEY SEMANTIC DIFFERENCE: the vendor's bc:98 entry has static_en=0 => it is DYNAMICALLY
LEARNED, not static-added.** This matches the raw-bit difference measured in pt11: the working
(learned) entry has w2 bit30 (0x40001000) while my driver's STATIC add produces bit29
(0x20001000), and writing bit30 by hand is converted back to bit29 / evicted. So the hardware
distinguishes learned vs static entries, and only the LEARNED form forwards to the CPU port.
=> **Our static FDB add can never work; bc:98 must be LEARNED on CPU-port ingress.**
On the vendor that happens because the normal TX path (hi_kernel_eth0_tx_send ->
eth0_tx_specialsend with ZEROED 14-byte metadata in skb->cb) injects the frame so the fabric
does a normal lookup+LEARN of SMAC bc:98 on the CPU port. A non-zero metadata block =
"special send" = forced egress port (bypasses lookup/learn). Mainline's PIE TX writes only
word0 = OWN|BIT14|len - so **BIT14 (or the missing/!=vendor descriptor metadata) may be
exactly what makes mainline's injected frames skip L2 learn**, leaving bc:98 unlearned.
NEXT EXPERIMENTS (in order):
 1. On cold, dump the ARL table after ONU TX and check whether ANY entry with SMAC bc:98
    appears (use the pt15 decode; index = hash, so scan all 0x121c1000+i*0x10).
 2. Compare mainline's TX descriptor word0 bits against the vendor PIE's actual hardware
    descriptor build (hi_pie_decomp, the fn at DAT_...+0x364 called from tx_specialsend);
    try clearing BIT14 / matching the vendor's exact word0 so the frame is learned.
 3. Use `cli /home/cli/hal/l2/l2_mac_add` on the VENDOR to add a STATIC entry and dump it -
    confirms the static-vs-learned bit encoding directly.

## pt16 (2026-08-11) exp#1 + exp#2 results
exp#2 (TX BIT14): added module param `tx_bit14` (sd5116-eth.c; default true). Cold boot with
`sd5116_eth.tx_bit14=0` => TX word0 = OWN|len (no BIT14). Result: **RX still dead**
(cpuout 0x112c1020 = 0, eth0 RX = 0) even after priming with ONU->host TX. So BIT14 is NOT
the learn-suppressor. (Param left in place, default true = original behaviour.)
exp#1 (bc:98 in ARL on cold): the driver scan finds bc:98 ONLY at 0x121c1e80 - i.e. the entry
my own static add wrote at probe (idx 0xE8) - and nothing else. No LEARNED bc:98 entry ever
appears on cold, before or after ONU TX. Confirms: CPU-port ingress learning does not happen on
cold, and the static entry (w2 bit29) does not forward. Still consistent with pt15.
=> Next lever must be elsewhere: the fabric's CPU-port INGRESS learn enable/attr, or the exact
PIE TX descriptor/metadata the vendor uses (hi_pie: eth0_tx_specialsend writes a 14-byte block
into skb->cb; the HW descriptor is built by the fn at DAT_0001239c+0x364 - RE that next to get
the real vendor word0/word1/word2/word3 for a normal learn-path TX).
BEST REMAINING EXPERIMENT: use the vendor CLI to prove the static-vs-learned encoding:
`cli /home/cli/hal/l2/l2_mac_add -v ...` then `l2_mac_dump` + read 0x121c1000+idx*0x10 to see
whether a STATIC add gets bit29 and whether it forwards on the VENDOR (if a static entry fails
to forward on the vendor too, static adds are definitively a dead end and only learning works).

## pt17 (2026-08-11) FDB/LEARNING RULED OUT; blocker = CPU-port(4) EGRESS delivery to PIE
Experiments (all on the reliable driver path or the vendor CLI, not devmem):
- fdb_cmd=0x21 (dynamic add) from the driver: creates **NO entry** (0x194=0x002400e8, bit31
  clear; ARL idx232 all zeros). fdb_cmd=0x11 (static): entry created but w2 bit29, no forward.
  => SW cannot create the bit30 "forwarding-valid" form. Only HW learning can.
- fdb_cmd=0 (skip the static add entirely, in case it squatted the hash slot): after 40 ONU TX
  frames the ARL slot stays EMPTY => **cold never learns bc:98 at all** (LAN-port learning does
  work on cold - background MACs appear). The static entry was NOT blocking anything.
- **CPU-port learn-enable register FOUND by controlled experiment**: on the vendor,
  `cli /home/cli/hal/l2/l2_learn_set -v port 12 lrn_en 0` changes exactly ONE register:
  **0x12180118: 0x00110903 -> 0x00110803, i.e. BIT8 = MAC-learn enable for internal port 4**.
  0x12180118 = 0x12180108 + 4*4 => confirms L2 base 0x12180000 and per-port reg = base+0x108+
  port*4 (matches l2_attr_set RE). NOTE: this bit is ALREADY SET on cold (0x00110903 both) -
  so learning is enabled on cold; it just never happens for CPU-port ingress.
- **THE DECISIVE ONE**: on the vendor, with CPU learning OFF *and* the bc:98 entry DELETED,
  host->ONU ping still works 4/4. => **RX does NOT depend on the FDB entry or on learning.**
  This confirms pt12 (bc:98 absent from the ARL while RX worked) and RETIRES the whole
  FDB/learning line (pt9,pt11,pt15,pt16 hypotheses).
CONCLUSION / REFRAME: host->bc:98 reaches the CPU as a **VLAN-4094 FLOOD member** (members =
internal {0 PON, 4 CPU, 8 LAN}), not via a MAC lookup. On cold the frame IS replicated toward
port 4 (p4 NO_EGR does NOT increment) but never appears at EPS output (cpuout 0x112c1020 = 0).
**So the break is strictly CPU-port(4) EGRESS -> EPS/PIE delivery.**
Correct port-4 egress objects to compare next (all previously examined under the WRONG port
number 16): OPP_qid1 offset 0x80 (port4*0x20) = qids 0x109-0x110 = queue idx 0x09-0x10 (these
EXIST and are identical cold vs kexec); the EQM type-0x10 scheduler entry for scheduler 4 and
the type-0x20 channel entry that binds scheduler 4 -> the CPU/PIE channel; EPS pie_chid for
port 4. sd5116_create_cpu_queues() targets the WRONG queues (0x11-0x18) and should be removed
or repointed at 0x09-0x10.

## pt18 - hi_bridge.ko decompiled; EQM egress model cracked; NO_EGR localised

**New RE asset:** `hi_bridge.ko` (the real switch datapath driver, 260 `hi_kernel_dp_*`
functions) decompiled to `hi_bridge_decomp.c`. Contains `hi_kernel_dp_init()` ->
`{mix,glb,uni,security,acl,pdu,l2,qos,prbs}_init`, i.e. the authoritative cold-init.
`hi_kernel_dp_5116_table_init()` confirms virt = phys + 0xe8000000.

**Vendor bulk register read:** `cli /home/cli/chip_ext/mr -v addr <VIRT> len <bytes>`
dumps to dmesg. Takes a VIRTUAL address (phys + 0xe8000000) - a physical address
oopses the module. Much faster than devmem loops.

**EQM egress model (from qos_init + live dumps):**
- t20 channel->scheduler: word0[6:0] = sched. ch4 and ch15 -> sched 0x20 (= port4*8).
- t22 queue->scheduler bind: bit 0x40000 = valid, [4:0] = sched, [17:14] = weight,
  bit 0x2000 set for eth ports, clear for CPU.
- t23 queue priority = (7-i) << 16.
- t10 per-queue scheduler enable: idx = qid>>4, byte = qid&3 in word (qid&0xc)/4,
  bit7 = enable, low 7 bits = t13 threshold-profile id.
- t13 = depth/WRED thresholds per profile. CPU queues use profile 0x1a, others 0x1c.
- OPP_qid1 port 4 (CPU) -> queues 0x09-0x10; port 8 (LAN) -> queues 0x51-0x58.

**Cold vs vendor (measured):**
- t10, t20, OPP_qid1: IDENTICAL on cold. Not the blocker.
- t22, t23, t13: ALL ZERO on cold, populated on vendor. REAL GAP - now programmed by
  the driver (`sd5116_create_cpu_queues`, module param `cpu_egress`). Verified to
  read back matching vendor. Did NOT fix forwarding.

**Whole-fabric register diff** (22 blocks x 0x200, vendor via `mr` vs cold via driver
`RGN` dump): only ~104 differing words. All were written to cold live via devmem
(0x11080124, 0x11380120/4, 0x12100120-148, 0x12180194-19c, 0x121c0000-0x121c01f4,
0x124c0000+). NO effect - NO_EGR still increments.

**Key correction: cold-boot TX is ALSO broken.** Verified with a host-side
`nc -u -l 9999` listener and a static ARP on both ends: the host receives nothing.
The earlier "egress works on cold" premise is wrong. Both directions die the same way.

**Drop counter is indexed by INGRESS port** (0x12446800 + port*0x200 + reason*4):
p4 = CPU-ingress (our TX), p8 = LAN-ingress (our RX). Reason 90 = NO_EGR
(confirmed via `chip/qos/dropreason_get` enum).

**Control experiment on the vendor:** NO_EGR p4 stays 0 while 0x112c1020 climbs
70->100 during 5 pings. So NO_EGR p4 is a genuine cold-only fault signal, and
0x112c1020 (stride 8, index 4) is confirmed as the CPU-port egress counter.

**Ruled out this round:** OPP_smp_car (0x12440040-5c is 0x81/0xbf/0x81 on BOTH
vendor and cold), OPP_cpu_attr and OPP_tfrt (all zero on both), MCC port_iso /
bct (zero on cold, no isolation), OPP register block 0x12400000 (identical),
EPS register block 0x11280000 (2 trivial diffs, written, no effect), EQM t10/t20.

**Status:** the forwarding engine produces an empty egress port set for every
frame regardless of ingress port. Not caused by any single-word register
difference in the 22 blocks dumped - so the missing state is in a table or a
procedural init step that leaves no static register footprint.

**Next lead:** the CPU TX path may need a hardware header. `hi_kernel_eth0_tx_send`
-> `hi_kernel_eth0_tx_specialsend` writes a 14-byte field at descriptor context
+0x1c..+0x29 (zeroed for the normal path). Worth checking whether the PIE TX
descriptor is larger than the 16 bytes the driver currently writes.

## pt19 - static-diff approach exhausted; TX descriptor cleared; remaining gaps applied

**PIE TX descriptor decoded** (from `eth_transmit` in hi_pie_decomp.c, 16-byte HW desc):
- word0: bit15 OWN, bit14 set on last desc, bits[13:0] length
- word1: buffer phys addr
- word2: `& 0xffe001ff | (cb[0x1c] & 0xfff) << 9`; low 6 bits = `1 << port` egress
  bitmask, used ONLY for directed/special send
- byte12 bit0 = "directed send" flag, set only when skb->cb[0x23] is 1 or 2
The special header lives in skb->cb (`eth_special_process` memcpy's 14 bytes to
skb+0x1c). For a NORMAL send the whole cb is zero, so no header is required.
Our driver already zeroes word2/word3 -> TX descriptor format is CORRECT.
`hi_pie_5115_from_cpu_dport_init` etc. are software lookup arrays, not HW regs.

**Verified matching on cold (so all ruled out):**
- VLAN 4094 entry 0x124cffe0 = `01190111 00000001` (stride 0x10/VLAN, 4094*0x10
  = 0xffe0 confirms the stride). deftag 0x124d7000 idx 0/4/8 = 0x00010ffe.
- L2 per-port regs 0x12180108 + port*4: port4 = 0x00110903, port8 = 0x00111d03
  (bits[1:0] = 3 = forwarding). Matches `dp_l2_init`'s mask 0x1f18 -> ports
  3,4,8,9,10,11,12 get `|= 3`. STP state is correct on cold.
- OPP_smp_car port4 (0x12440040) = `81 bf 81 00` on BOTH vendor and cold.

**Wide diffs done and applied:** 22 blocks x 0x200, then 8 blocks x 0x800, then
10 table regions x 0x200. Every differing word was written to cold live:
0x11080124/0x11080724, 0x11380120/4, 0x12100120-148, 0x12100204, 0x12180194-19c,
0x12180300, 0x121c0000-0x121c01f4, 0x124c0000+, 0x1248020c/210/214/218 (these two
are verbatim `dp_glb_init` chip-type-4 writes: 0x01c00180 / 0x000101ff),
0x120c0024-74, 0x123c4100-1fc (MCC mcg, all 0x4000), 0x12150028-a8 (IFC_fact).
**None of it changed the outcome - NO_EGR still increments on every frame.**

Note: writes to PA_port_cfg (0x120c0044) do NOT stick (read back 0), so some
tables need indirect/CLI access rather than direct stores - a caveat for any
"just replay the final register values" strategy.

**Conclusion:** the static-value replay approach is exhausted. Everything that can
be made to match does match, yet the forwarding engine still resolves an empty
egress set for every frame on every ingress port. The missing state must be
procedural - order-dependent writes, self-clearing trigger bits, or internal
SRAM/free-list structures that leave no readable footprint.

**Two remaining options:**
1. Faithfully port `hi_kernel_dp_init()` from hi_bridge_decomp.c:
   `mix -> glb -> uni -> security -> acl -> pdu -> l2 -> qos -> prbs`. Now tractable:
   the full decompilation exists and `dp_5116_reg_init`/`table_init` give every
   base address (virt = phys + 0xe8000000). Large but bounded.
2. Automate the kexec hand-off (vendor kernel -> mainline), which yields a 100%
   working NIC today.

## pt20 - porting hi_kernel_dp_init(): tooling built, base map recovered

**New tool: `archive/diag/relmap.py`.** Resolves Ghidra's opaque `DAT_xxxxxxxx` literals in
hi_bridge.ko to real symbols via the .rel.text relocations (Ghidra loads .text at
0x10000, so `DAT_0001xxxx` = file offset `0xxxxx`). This is what makes the
decompilation readable - without it every register base is an unnamed pointer.

**Register base map** (from `dp_5116_reg_init`, virt = phys + 0xe8000000; the
globals are a contiguous .bss array starting at `g_dp_EPS_reg_5116_s`):

    EPS   0x11280000    EQM   0x11200000    FAM   0x11100000    IFC   0x12100000
    IPS   0x11080000    L2    0x12180000    MCC   0x12380000    MEMP  0x11300000
    OFC   0x12300000    OPP   0x12400000    PA    0x12080000    PE    0x11180000
    PFE   0x12000000    PTC   0x12500000    PUB   0x12480000    PRBS  0x11380000
    DP    0x11000000    ITF   0x14000000    NNI   0x14280000    L3    0x12200000
    NAPT  0x12280000    IOMUX 0x14900000    PIE_TAB 0x10a702dc

**Map validated** against two independent known-good values: `glb_init` writes
EPS+0x18c = 10 and PUB+0x20c = 0x01c00180, both of which had shown up in the
cold-vs-vendor diff at exactly 0x1128018c and 0x1248020c.

**glb_init fully decoded.** The Ghidra "unrecoverable jumptable" is trivial (it only
picks a software field); the function is linear. Its writes are: IPS+0x08[10:0]=0x3c,
GEMAC[i]+0x100[13:0]=2000, ITF+0x108 |= 1, ITF+0x110 &= ~1, and for chip type 4:
SYSCTL+0x60, EPS+0x18c=10, EQM+0x74/0x78/0x7c=1/1/0xfff, PE+0x20/0x24/0x28=0x22/0x44/0x66,
PUB+0x20c/0x210. All were already correct on cold except the ones already applied.

**NEW GAP FOUND: `ITF_GLB + 0x108` (0x14000108) = 1 on vendor, 0 on cold** - the
engine enable from glb_init. Also 0x14000130 (=3), PA 0x12080204/0208, IOMUX
0x14900028/0200. Blocks never dumped before (PA regs are 0x12080000, NOT the
0x120c0000 table; plus ITF/NNI/IOMUX).

**Applied at probe time, in vendor order** (module param `dp_init_port`), not via
post-hoc devmem - this was a genuinely different experiment, since engine enables
may only work before the rest of the fabric comes up. Verified landed:
ITF+0x108=1, ITF+0x130=3, PUB+0x20c, EPS+0x18c, VLAN table, MCC mcg.
**Still no forwarding.**

**Theory definitively killed:** `hi_kernel_hal_port_add(port)` -> `__port_enable()`
does **NOTHING for internal port 4 (CPU)** - it only acts on port 0 (PON, via
epon/gpon attr) and ports 8-12 (eth, via `dp_uni_eth_port_attr_set`). So
`port_del 12` is a no-op on the CPU port, and the earlier "cold looks like the CPU
port was deleted" reading of the 0x124400a0/0xc0 toggle was coincidence.

**STRUCTURAL FINDING - why static replay cannot work:** some config registers do
not accept plain writes. `PA_port_cfg` 0x120c0044 reads back 0 after writing
0x4000, and ARL 0x121c0100 reads back 0x18 after writing 0xf. These need their
proper indirect/table access mechanism (like the EQM 0x48 cmd port), not stores.
Any "replay the final register values" strategy is therefore incomplete by
construction - which is consistent with every replay attempt so far failing.

**Next:** find the indirect access mechanism for the PA and ARL blocks (compare
with the EQM cmd/rdata/wdata pattern at +0x48/+0x4c/+0x5c), and port
`dp_uni_eth_port_attr_set` (the eth-port enable path reached by port_add).

## pt21 - long-table write order, reset-gate diff, and a CORRECTION

**Correction to pt20.** The claim that PA/ARL registers "do not accept plain writes
and need indirect access" was WRONG. They are writable; they simply have narrow
valid fields, so the probe values were being masked:
    0x12080204 <- 0x12345678 reads 0x00340078   (reserved bits)
    0x121c0100 <- 0x12345678 reads 0x00000678   (12-bit field)
    0x123c4100 <- 0x12345678 reads 0x02345678   (26-bit field)
    0x120c0044 <- 0x4000 reads 0x4000; <- 0xffffffff still reads 0x4000
                                                (2-bit field at [15:14])
So "indirect-only tables" is not the explanation for the replay failures.

**Real finding: wide tables commit on the word0 write.**
`hi_kernel_dp_common_long_table_cpy()` writes `[3],[2],[1]` then `[0]` LAST, and
`hi_kernel_dp_ip_filter_mode_set()` follows the same order. Implemented as
`sd5116_long_write()` in the driver. `hi_kernel_dp_security_init()` =
PA_port_cfg entry0 word0 |= 0x10000, OPP+0x20 |= 0x10000000 (already correct on
cold), then `ip_filter_mode_set(port,1)` for ports 0..13 -> word1[15:14] = 1.
Ported and confirmed applied: 0x120c0000 reads 0x00110000 on cold afterwards,
i.e. the |= 0x10000 took effect.

**Reset/clock gate diff.** 0x14880000 block (never previously dumped) differs in
exactly one word:
    0x14880054   vendor=0x0004ac02   cold=0x0007fc02
Cold has bits 12,14,16,17 EXTRA set. Writing the vendor value on cold takes
(reads back 0x0004ac02) but does not restore forwarding, and does not make the
NNI block readable (0x14280000 still returns the 0x89abcdef bus-error pattern).

**NEW: the PA_port_cfg window ALIASES on cold.** After writing 0x12345678 to
0x120c0000 and 0x4000 to 0x120c0044 (entry 4 word1), BOTH entry 0 and entry 4
read back `12345678 00004000 00000000 00000000`. On the vendor these are distinct
(0x4000 present at 0x120c0024/34/44/54/64/74 but not 0x04/0x14). So on cold the
PA table's address decode is collapsed - a per-port table is behaving as a single
aliased entry. That is a strong sign the PA block is still not fully brought out
of reset / not clocked, independent of 0x14880054.

**Next:** find what un-aliases the PA table window (i.e. the PA block's real
clock/reset/enable). The 0x89abcdef reads from NNI_SDSIF are the same class of
symptom and may share a cause. Candidate places to look: the remaining
0x14880000 sysctl space beyond 0x100, and `g_dp_SYSCTL_reg_cbb_s` /
`g_dp_GEMAC_reg_cbb_s` which glb_init touches (SYSCTL+0x60).

## pt22 - RETRACTION of the pt21 "aliasing" claim; board hang invalidated a run

**The pt21 PA_port_cfg "aliasing" conclusion is RETRACTED.** Re-reading the actual
command sequence, both observations are fully explained without aliasing:
  - entry0 and entry4 both showed word0 = 0x12345678 because the probe script had
    explicitly written 0x12345678 to BOTH 0x120c0000 and 0x120c0040.
  - entry0 and entry4 both showed word1 = 0x4000 because the ported
    `security_init` sets word1[15:14] = 1 for EVERY port 0..13, so port 0 legitimately
    holds 0x4000; entry 4 had been re-written to 0x4000 in the same script.
No independent write was ever made to a single entry and observed in another.
There is no evidence of aliasing, and therefore no evidence from this for the
"PA block is not clocked / not out of reset" theory either.

**A later run was invalid: the board hung.** After writing 0x14880054 = 0x0004ac02
(the vendor value for the one differing reset-gate word), a subsequent run had
EVERY read return 0 - including 0x12180108, 0x124cffe0 and a value written
moments earlier - and `uptime` returned empty, i.e. the shell/board was dead, not
the PA block. The write-order experiment (order A vs order B) executed against
that dead board and its "both orders fail" result is meaningless. Discarded.

**Caution:** do not write 0x14880054 on cold again without re-testing carefully;
it is a plausible (unproven) cause of the hang, and it never helped anything.

**Still open and untested:** whether the ported `security_init` actually leaves
PA_port_cfg matching the vendor (0x4000 in word1 for the ports the vendor has it).
A single verify.py read after boot_sec showed 0x120c0044 = 0, which contradicts
the ported code; that needs a clean re-measurement before any conclusion.

## pt23 - PA gap closed; OFC/IFC/PE/PUB/L3 tables ruled out

Environment notes (cost me a false "dead board" call in pt22):
- `pyserial` exists ONLY in `/home/mario/.local/python/bin/python3`. Any other
  interpreter -> ModuleNotFoundError -> "0 bytes on serial" that looks like dead
  hardware. Board was fine the whole time.
- `tftpd.socket` keeps going inactive; that is the `FAILED to load Image` cause.

Confirmed from hi_bridge decomp + vendor reads:
- `0x120c0000` = `g_dp_PA_port_cfg_table_5116_s`, per-port, stride 0x10, 14 ports.
- `ip_filter_mode_set` writes word3->word2->word1->word0 DIRECTLY (not via
  long_table_cpy) and sets word1[15:14]=mode. Same descending order as
  `sd5116_long_write`, so that helper is right.
- Vendor PA word0 = 0x00110000 on ports {0,1,8..13}, zero on {2..7}.
  bit16 written only by `dp_security_attr_set` {0,4,8..12};
  bits[21:20] by `set_port_tag_attr`. Both are HAL/port-bringup level, NOT
  reached by `dp_init` -> genuine cold-boot gap.
  PORTED: security_init now sets word0 |= 0x110000 for p==0||p==1||p>=8
  (CPU port 4 deliberately excluded, matching vendor).
  VERIFIED on cold boot: 0x120c0000/0x120c0080/0x120c00d0 all read 0x00110000,
  word1 0x00004000. PA block matches vendor exactly now.
- RESULT: forwarding still broken. NO_EGR (reason 90) still counts on both
  p4 ingress (0x15) and p8 ingress (0x75). PA was a real gap but not the cause.

Corrections to earlier claims:
- The regs I once called "OPP_smp_car CAR @0x12440040" are
  `OPP_reg + 0x20 + port*4`, per-port attrs. 00000081/000000bf were p8..p13.
- `security_init` ORs bit28 into OPP+0x20, but vendor reads 0 on every port
  (`security_attr_set` clears it with flag=0). Omitting that write is correct.

Ruled out this round (cold == vendor, byte for byte):
- OFC_fact 0x12350000, OFC regs 0x12380000-0x1238005c  (OFC was my top suspect
  as "output forwarding control"; it is identical -> dead end)
- IFC_fstat 0x12160000, IFC_fact 0x12150000 (also confirms the IFC port correct)
- All-zero on BOTH cold and vendor: PE_tt 0x111c0000, PE_user_cmd 0x111c2000,
  PE_tt_act 0x111c4000, EPS_pie_chid 0x112c8000, SFC 0x12440800,
  OPP_gem_map 0x12441800, PUB_cmf 0x124d0000, PUB_tag_profile0 0x124d4000,
  PUB_tag_profile1 0x124df000, PUB_nhp 0x124d9000, PUB_tt1 0x124dd000,
  PUB_tt2 0x124de000, L3_otrt0 0x12240000, L3_tsct 0x12240200,
  MCC_gemp_iso 0x123c5000
- `dp_uni_init` = MDIO + PHY LED + GPIO(0x14?)/IOMUX only. No forwarding state.
  Nothing to port for the egress problem.
- `EPS_pie_chid` is written only by `dp_vqm_attr_set`, and only when vqm mode==1
  (entries +0x10,+0x90,+0x110,+0x190 = 1). Vendor has it all zero -> vqm off.

Still open: egress set resolves empty in both directions. Remaining unported
init: dp_mix_init, dp_acl_init, dp_pdu_init, dp_prbs_init, bulk of dp_qos_init.

## pt24 - IFC ingress rule table found and ported (real gap, not the fix)

Method fix first: the driver dumped at probe time, and after a kexec hand-off
the fabric is still settling, so several blocks misread (some as zero, some
skipped). Added a `dump_now` module param (module_param_cb, 0200) that re-runs
the same dump LATE from userspace. Both boots now yield 252 identical-format
lines. The earlier "kexec reads zero for 0x12100000-0x12480000" and the
19-block diff built on it were artifacts and are RETRACTED - live devmem on
kexec reads those blocks fine.

Confirmed by cold-vs-kexec (same driver both sides, late dump):
- Most of the 136 differing words are COUNTERS. Proved empirically: 0x121c0000
  ticked 0x91BF->0x91D8 (+25) and +0x1c 0x3292->0x329A (+8) across 5 pings.
  The big ARL delta (0x2f59 vs 0x244) just means kexec forwarded thousands of
  packets and cold forwarded none. Symptom, not cause.
- Corollary: the 0x244/0x1f6/0x20 constants the driver wrote to
  0x121c0000+0x00/0x04/0x1c/0x24 are landing on COUNTER registers. Meaningless.
  Remove them.

THE REAL FIND - IFC ingress rule table at 0x12100120:
- 0x12100120 = command register. bit31 = go (SELF-CLEARING, verified: wrote
  0x8000000a, read back 0x0000000A), bit30 = write, low bits = index.
- 0x12100124..0x134 = READ-RESULT window (5 words).
- 0x12100138..0x148 = WRITE-DATA window (5 words).
  Same shape as the EQM block (cmd@+0x48, rdata@+0x4c, wdata@+0x5c).
- Write sequence that works: wdata -> 0x138..0x148, then cmd = 0xC0000000|idx.
  Verified on hardware: readback via 0x8000000a returned the written values.
- The old port wrote the READ window and then the cmd - so nothing ever
  committed. That is why 0x12100124.. always read 0 on cold.
- Where the old constants came from: 0x08/0x10/0x1313/0x00102c00/0x0003780a is
  verbatim IFCT entry 0x0a, i.e. a latched leftover in the result window from
  the vendor's last read, mistaken for plain config.
- Cold had ALL 16 entries empty. kexec has 10 populated (idx 1..0x0a).
  Entry 06 = 89095d98 0000bc98 ... contains the board MAC bc:98:89:09:5d:98.
- PORTED: sd5116_ifc_rules_init() programs entries 1..0x0a via the wdata
  window. VERIFIED on cold boot: table now reads back identical to kexec.

RESULT: forwarding still broken. NO_EGR still counts on both paths.
  p4 reason90 = 0x16, p8 reason90 = 0x169, plus NEW p4 reason112 = 4.
  eth0 TX bytes nonzero but EPS output cnt (0x112c1000 + port*8) is 0 on every
  port - nothing egresses anywhere.

CORRECTION (mine): I briefly reported "p4 NO_EGR 0x15 -> 0, TX path fixed".
  That was a wrong address. p4 reason90 is at 0x12446800 + 4*0x200 + 90*4 =
  0x12447168. I read 0x12446b68, which is PORT 1 (idle -> 0). Drop counters are
  cumulative, NOT read-clear (three reads returned a stable 0x17C). The IFC fix
  did not change the drop behaviour.

Tooling notes:
- /tmp/shell.py gets wiped between sessions; recreate it (uses archive/diag/serialctl).
- The kexec serial shell is flaky AFTER kexec_boot.py exits. Reliable pattern:
  put probe commands INSIDE the harness (kexec_late.py / kexec_ifc.py) and read
  results back with `dmesg | grep`, not from the live console stream.
- tcpdump on the host needs root; use /sys/class/net/enp6s0/statistics instead.

## pt25 - VLAN wide-table + EQM scheduler binds ported (real gaps, still not the fix)

Method: with `dump_now` late dumps on both boots, re-diffed and filtered
counters by re-running and discarding anything that DRIFTS between runs.
Confirmed drifting (= counters, ignore): 0x121c0100-0x121c01f0
(0x12/0x17 -> 0x14/0x19), 0x121c0210/0220/023c group (0x0d/0x07 -> 0x0e/0x09),
0x124400a8-f8 (0x20 moves between ports), 0x11180214-224.
0x1120005c/0x11200060 sits in the EQM WDATA window (+0x5c..+0x68) - leftovers
from the last write, not config.

Gap 2 - PUB_vlan_mem 0x124c0000 is a WIDE (4-word) table:
  vendor writes it via common_long_table_cpy; the port used a bare word0
  sw_write, which left word1 = 1 (kexec/vendor has word1 = 0).
  PORTED: sd5116_long_write with { 0x01190000, 0, 0, 0 } for all 4096 entries.
  VERIFIED: 0x124c0004 now reads 0.

Gap 3 - EQM scheduler binds missing on cold (t22 = queue->sched, t23 = prio):
  kexec has, cold had nothing, for:
    t22 idx 0x01-0x08 = 0x40000 | (n<<14) | 0x2000 | 0x03   (sched 3)
    t22 idx 0x11-0x18 = 0x40000 | (n<<14) | 0x2000 | 0x10   (sched 0x10)
    t22 idx 0x19-0x1f = 0x40000 | (n<<14) | 0x2000 | 0x11   (sched 0x11)
    t23 for all of the above = (7 - n) << 16  (descending priority)
  Same formula as the already-ported LAN queues 0x51-0x58, different sched.
  Also fixed: t23 idx09 was written as 0x00070005; vendor has 0x00070004.
  PORTED + VERIFIED: EQM t22/t23 now match kexec EXACTLY (45 entries, 0 diffs).
  NOT landing: t50 idx09 (kexec 0x13421342, cold stays 0) - the 0x50 depth
  table seems to need a different write path than cmd|0x40000000. Open.

RESULT: still 100% loss. p4 reason90 = 0x0B, p8 reason90 = 0x8F,
  p4 reason112 = 4 (unchanged by the EQM fix). EPS output cnt 0 on every port.

State of the search: after closing IFC rules, VLAN word1 and EQM t22/t23 there
are NO stable structured differences left in the 27 register blocks or in the
six EQM table types. The remaining delta must be in an indirect table whose
command interface I have not identified yet - exactly how the IFC table stayed
invisible (a register dump only ever showed its stale result window).
Next: find the other cmd/rdata/wdata windows (look for the EQM/IFC shape:
cmd reg with self-clearing bit31, then 4-5 rdata words, then 4-5 wdata words)
in the OFC 0x12380000, 0x12300000, 0x12400000 and PUB 0x12480000 blocks.

## pt26 - the ARL/FDB engine does not commit on cold  <-- best lead

Ruled out this round:
- Indirect interfaces are ENUMERABLE: hi_kernel_dp_indirect_table_op_check has
  only 3 call sites -> EQM (base+0x48, wait bit31) and L2 FDB (base+0x108, wait
  bit16). There is no third hidden cmd/data window to hunt for.
- No OFC command window: 0x12380120 does not preserve an index (writes ignored),
  unlike IFC 0x12100120 which reads back 0x0000000A. Probe was safe/negative.
- ACL ifc/ofc table setters use the MEMORY-MAPPED IFC_fact 0x12150000 /
  IFC_fstat 0x12160000 / OFC_fact 0x12350000, all already compared identical.
  So the 0x12100120 window is a separate thing in the IFC register block.
- The FDB add's second step writes g_dp_IPS_PON_reg_cbb_s (+0x110/+0x10c/+0x108)
  = the PON-side MAC filter. Not the CPU/LAN path, not our bug.

THE FIND - FDB writes silently do not commit on cold:
- Enabled the add (bootargs had sd5116_eth.fdb_cmd=0, i.e. add SKIPPED - a stale
  debug leftover). With fdb_cmd=0x21 the command REPORTS success:
  0x190 = 0x00000020 (go bit clear), 0x194 = 0x002400e8 (index 0xe8).
- But ARL SRAM 0x121c1000 + 0xe8*0x10 stays all-zero, and a scan of the whole
  table gives nonzero = 0 of 256 on cold.
- kexec has 8 entries, including exactly the one we try to add:
    ARLT e8: 89095d98 0ffebc98 00001000 00000002   (MAC bc:98:89:09:5d:98)
- The driver's register values are CORRECT - they match the kexec entry word
  for word: 0x198 = 89095d98 -> w0, 0x19c = 0x0FFEBC98 -> w1,
  0x1a0 = 0x1000 -> w2 (w3 = 2 is hardware-set).
- Strong corroboration: kexec entries 0x88 and 0x9c hold 5e7fffff/0ff80100 and
  5e7ffffb/0ff80100 - those are the values THIS driver writes to
  0x12180198/0x1218019c during init. On kexec those writes land as FDB entries;
  on cold they do not. The learning/write engine is simply not operational.

Why this explains everything: with an empty FDB every frame is unknown-unicast
-> flood -> and the flood domain resolves empty -> NO_EGR on BOTH ingress
directions, which is exactly the symptom (p4 and p8 both reason90).

NEXT: find what enables the ARL/FDB write engine. Candidates: 0x12180100 /
0x12180104 (the vendor reads +0x104 bit0 before composing +0x10c), an ARL
block enable, or a clock/reset gate for ARL. Compare 0x12180100-0x12180110
cold vs kexec - note the RGN dump only showed 0x12180108 = 1 on both, so the
enable is likely a bit the dump's nonzero-row filter hid, or lives elsewhere.

## pt27 - FDB hypothesis REFUTED (but the engine defect is real)

Follow-up on pt26. Two separate facts, do not conflate them:

(a) The FDB write engine genuinely does not commit on cold. Confirmed again:
    reset via L2+0x1b0 bit0 (from hi_kernel_dp_l2_mac_table_reset; base
    g_dp_L2_reg_5116_s = 0x12180000, and its +0x40100..+0x40234 clears land on
    0x121c0100.. - which independently CONFIRMS that ARL range is counters)
    self-clears but changes nothing; the hash insert still writes no SRAM.
    L2 control regs 0x12180100-0x1218011c are BYTE-IDENTICAL cold vs kexec
    (104: 01010005, 108: 00100d00, 10c/110: 00101900, 114: 00111d03,
    118: 00110903, 11c: 00101900), so it is NOT a missing enable bit there.

(b) An empty FDB is NOT what breaks forwarding. Proved by bypassing the engine:
    - ARL SRAM is DIRECTLY writable on cold (wrote 0xdeadbeef to 0x121c1000,
      read it back). Only the hash-insert path is broken, not the memory.
    - Wrote the CPU entry byte-for-byte identical to kexec's:
        0x121c1e80 = 89095d98 0ffebc98 00001000 00000002   (idx 0xe8)
      Verified it reads back correctly.
    - Also installed the host MAC d8:5e:d3:f0:92:c2 at idx 0x1fc (index taken
      from the engine itself: program 0x198/0x19c/0x1a0, issue cmd 0x21, read
      the index out of 0x194 even though the write does not land - a usable
      hash oracle) plus a static ARP on the ONU so nothing needs flooding.
    - RESULT: still 100% loss. p4 reason90 0x07->0x0D, p8 reason90 ->0x12B,
      CPU-out 0x112c1020 still 0, eth0 RX packets 0.
    The RX direction is the clean test - its entry is verbatim kexec - and a
    KNOWN destination MAC still resolves an empty egress set. So the flood /
    unknown-unicast theory from pt26 is refuted.

Net: the egress set resolves empty even for a known unicast destination with a
correct FDB entry. The fault is downstream of the L2 lookup, not in it.
Next candidates: the port/channel the FDB entry's w2 encodes (CPU w2 = 0x1000
= port 0x10 << 8; the LAN entries in kexec use 0x00010000 / 0x20010000 /
0x40010000 / 0x60010000, i.e. a different field layout worth decoding), and
whatever consumes that field on the egress side.

## pt28 - CORRECTION: the FDB engine works; the add was using the wrong command

pt26/pt27 claimed "the FDB write engine does not commit on cold". That is WRONG
and is retracted. What actually happens:

- hi_kernel_dp_l2_mac_table_item_ind_set returns SUCCESS only when bit31 of
  0x12180194 is set:
      uVar6 = 0xfc000105;                          /* error */
      if (*(int *)(iVar4 + 0x194) < 0) uVar6 = 0;  /* success iff bit31 */
  Our adds returned 0x002400e8 - bit31 CLEAR - i.e. the hardware was REJECTING
  the add, not silently dropping it. I had not been checking the status bit.
- Proof the engine works: adding the host MAC d8:5e:d3:f0:92:c2 with cmd 0x21
  returned 0x839801FC (bit31 SET) and the ENGINE wrote SRAM by itself:
      0x121c2fc0 = D3F092C2 0FFED85E 20010000 00000002
  Note w2 came back 0x20010000 although 0x00010000 was supplied - the hardware
  composes the upper bits itself, and the result matches kexec's LAN entries
  (cc/e0/f4/68 = 00010000/20010000/40010000/60010000, so those upper bits are a
  chain/hash field and the port field is 0x100 for LAN).
- The CPU MAC add is rejected with cmd 0x21 (dynamic) but ACCEPTED with
  cmd 0x11 (static): returns 0x802400E8, bit31 set, and the engine writes
      0x121c1e80 = 89095D98 0FFEBC98 00001000 00000002
  which is byte-for-byte kexec's entry 0xe8.
  => The driver's comment claiming "static add produces bit29 and does NOT
     forward, so use the dynamic form 0x21" is wrong; 0x21 never landed at all.
  APPLIED: bootargs sd5116_eth.fdb_cmd=0x11.

Field layout of the add, from the decomp (offsets on L2 base 0x12180000):
  0x198 = inverted MAC low
  0x19c = MAC high | vlan << 16   (vlan |= 0x1000 if param+0xc == 1)
  0x1a0 = (port & 0xffff) << 8 | (p10 & 1) << 25 | (p1c & 0xff)
          | (p20 & 1) << 26 | (p2c & 1) << 24
  0x190 = type << 4 | 1, type 1 = static, 2 = dynamic; poll bit0
  0x194 = bit31 status, bits 16-27 aux, bits 0-11 index

STILL BROKEN even so: with BOTH directions' FDB entries present and correct
(CPU 0xe8 engine-written, host 0x1fc engine-written) plus a static ARP,
ping is still 100% loss, CPU-out 0x112c1020 = 0, p4 reason90 0x0D, p8 0xD6.
So pt27's conclusion stands: the fault is downstream of the L2 lookup.

## pt29 - PA ptype action table ported (5th real gap, still not the fix)

Closed a coverage hole first: several TABLES sit outside the 27 register blocks
the dump walked (MCC mcg 0x123c4000, port_iso 0x123c9000, OPP gem_map/qid1/
qid2/tfrt/stat, SFC, port_deftag, PA ptype_act). Added them to the dump.

Result: cold 72 rows vs kexec 109.
- 0x12448800 (OPP_stat) differs but the values are SRAM noise - they change
  randomly between runs (aebbfb9f/aebbfbdf...). Ignore.
- REAL GAP: PA_ptype_act 0x120c1300 + idx*0x10 is COMPLETELY EMPTY on cold and
  fully populated on kexec, indices 1..0x1f:
      04000005 00050000   at idx 5 and idx 8..0x0f
      3c000038 003c0000   at all the others (1-4, 6, 7, 0x10-0x1f)
  This is the per-parsed-packet-type action table.
  PORTED: sd5116_pa_ptype_act_init() using sd5116_long_write (wide table).
  VERIFIED on cold: 0x120c1310 = 3C000038, 0x120c1350 = 04000005.

RESULT: still 100% loss. CPU-out 0, p4 reason90 0x0A, p8 reason90 0x24.

Running tally of REAL cold-boot gaps found and closed, none of which changed
the symptom: PA port cfg word0, IFC ingress rule table, VLAN wide-table word1,
EQM t22/t23 scheduler binds, fdb_cmd 0x21->0x11, PA ptype action table.
Every one was verified to land by reading it back and matching kexec.

Working hypothesis to test next: the repeated pattern is "table empty on cold,
populated on kexec, writes land fine, behaviour unchanged". That smells less
like N missing tables and more like ONE missing global enable/start that gates
the datapath, with all these tables being config the vendor happens to fill in.
Look for a datapath "go" bit: candidates are the ITF block 0x14000000
(0x108 engine enable already ported, 0x130 = 3), a per-port enable on the
switch side for internal ports 4 and 8, and the reset/clock block 0x14880000
(0x14880054 is flagged in pt22 as a suspected hang cause - handle with care).

## pt30 - BREAKTHROUGH: MCC bct flood domain was empty; NO_EGR is GONE

How it was found (the method that finally worked):
1. Bisection from the WORKING side first: on a kexec boot, wrote COLD's values
   into every diff I had previously dismissed as "counters/noise"
   (OPP 0xa8/b8/d8, IFC_fact 0x54/64/74, EQM wdata 0x5c/60, EPS 0x11180220).
   Forwarding survived ALL of them -> the counter classification was correct and
   none of the readable diffs matter. Culprit had to be in unexamined space.
2. Realised the table dumps only ever covered the FIRST 0x200 of each table,
   while the tables are huge (VLAN 64KB; the FDB is >=512 entries - the host MAC
   hashed to 0x1fc, past my 256-entry scan).
3. Added a per-4KB checksum sweep (sum+xor per chunk) over the whole table
   space - cheap way to find WHICH chunk differs without dumping everything.
   Chunks zero on cold but populated on kexec: 0x121c2000/3000/4000 (FDB pages
   of learned MACs), 0x12445000 + 0x12447000 (counters), 0x124d6000, and
   *** 0x123c3000 ***.
4. 0x123c3000 is the upper page of MCC_bct - the BROADCAST/FLOOD table.
   Dumped it: only three live entries, and the table is indexed by VLAN ID with
   4-byte entries (0x123c0000 + vid*4):
       vid 4094 (0x123c3ff8) = 0x00000401   <- our VLAN
       vid 4089 (0x123c3fe4) = 0x00000404
       vid 4041 (0x123c3f24) = 0x00000406
   Cold had ZERO for all of them: no flood domain for VLAN 4094, so every
   flooded frame resolved an empty egress set.

PORTED: sd5116_bct_init(). VERIFIED on cold: 0x123c3ff8 = 0x00000401.

RESULT - the failure mode MOVED for the first time this session:
  reason90 (NO_EGR) is now ZERO on both p4 and p8 (it counted on every previous
  boot). Frames now resolve an egress set and fail LATER:
      p4 (CPU ingress) reason112 = 0x12
      p8 (LAN ingress) reason104 = 0x3C
  Ping still fails, CPU-out 0x112c1020 still 0.

NEXT: identify drop reasons 104 and 112. They are direction-specific, so likely
egress-side VLAN/tag filtering, MTU, CAR policing or queue admission. Note
0x123c4000 (MCC_mcg) checksum also differs slightly (0cffa00a vs 0c1fa00a) and
bct's 0x401 may be a multicast-group ID indexing mcg rather than a port bitmap -
worth decoding next.

## pt31 - drop reasons decoded by comparison; VLAN member mask is the new target

Method note that unblocked this: instead of guessing what drop reasons mean,
I dumped the drop counters on the WORKING kexec boot (added a DROP scan to the
driver dump). Result:
    kexec (works):  p4 r104=0x10, p4 r112=9, p8 r112=0x5b, p8 r90=4
    cold  (broken): p4 r112,      p8 r104
=> r112 occurs on BOTH -> benign. r90 even occurs on kexec -> benign in small
   numbers. The ONLY cold-specific drop is *** p8 r104 *** (LAN ingress, i.e.
   the RX direction). That is now the single fault signature to chase.

CORRECTION - EPS counters are NOT usable on cold:
  EPS_input_cnt (0x112c0000+p*8) and EPS_output_cnt (0x112c1000+p*8) read ZERO
  on every port on cold, while OPP drop counters clearly increment. So frames
  do reach OPP and the EPS counters simply are not running on cold.
  I have been using 0x112c1020 == 0 as "RX not delivered" all session; that
  evidence is unreliable. Use eth0 RX/TX packet counts (driver level) instead.

Also ported (fidelity, no behaviour change): MCC_mcg - the vendor fills all
1024 entries with 0x4000, the driver filled only 64. Now matches.

NEW TARGET - VLAN table word2 = port member mask:
  kexec VLAN entries: 01190000 00000000 0107fe00 00000000
  cold:               01190000 00000000 00000000 00000000
  All 32 sampled kexec entries carry w2 = 0x0107fe00 uniformly. An empty member
  mask on ingress is a very plausible cause of p8 r104.
  This was invisible earlier because when the driver wrote only w0 with
  sw_write it LEFT the vendor's w1/w2 intact on kexec, so the diff never showed
  it; it only appeared after the port switched to a full 4-word long_write.
  ATTEMPTED: writing w2 directly (0x124cff88 = 0x0107fe00) does NOT stick -
  reads back 0. Like the FDB's w2, this field is hardware-composed, not
  directly writable. It must be established through the proper operation.
  That operation is hi_kernel_dp_l2_bc_add (decomp line 14551): it reads
  bct[vid] (*DAT_00025ad4 + vid*4), tests bit 0x400 - which CONFIRMS the bit10
  "valid" + low-bits "group id" decoding of bct from pt30 - and allocates /
  updates a broadcast group, from which the member mask is derived.
  NEXT: port l2_bc_add's group-allocation path, or find the register write
  inside it that populates the member mask.

## pt32 - VLAN w2 hypothesis REFUTED; ptype_act table extended

RETRACTION of pt31's target. Tested it on the working side before porting
l2_bc_add (which would have been a lot of work):
  On the kexec boot that FORWARDS FINE, VLAN 4094's w2 (0x124cff88) reads
  0x00000000 - identical to cold - and ping still works.
  The 0x0107fe00 entries I saw were vids 26-31 (low VIDs we do not use), not
  4094. So VLAN word2 is NOT the blocker and l2_bc_add does not need porting
  for this. Lesson: check WHICH index a table difference is at before assuming
  it applies to the VLAN actually in use.
  (Also note the first w2 test on kexec returned blank reads + "ping False" -
  that was an unresponsive shell, not a result. Re-ran to get a valid answer.)

Ported (real gap, verified, no behaviour change):
  PA_ptype_act is LARGER than the 0x20 entries assumed in pt29. kexec has rows
  through 0x120c15c0 = index 0x2c, cold had them zero. Extended the port to
  indices 1..0x2c, with w2 = 0x00100000 at indices 0x24 and 0x25.
  VERIFIED on cold: 0x120c1500 = 0x3C000038.

Classified as NOISE, do not chase: PA 0x120c0800-0x120c09f0 differs between
boots but the values are uninitialised SRAM (scattered single-bit differences
in otherwise identical-shaped words, e.g. 050c1680 vs 05041680). Same for
0x120c1e40+ and OPP_stat 0x12448800.

STATE: cold-specific drop is still p8 reason104 (LAN ingress / RX direction).
eth0 TX increments, RX stays 0. All other drop reasons now match the working
kexec profile.

## pt33 - drop reasons decoded from the vendor table; VLAN 4094 members were being wiped

Authoritative slot->name map: hi_khal.ko `g_qos_drop_reason` (.data, 128 ptrs, read via
relocations; hi_kernel_hal_qos_drop_reason_get prints slot i with name[i], 1:1, no offset).
Relevant: 90 = no_egr (matches pt30), 104 = egr_vlan, 106 = egr_8021x_stp, 112 = up_map,
121 = ecc/parity(free). The 5650 table (g_qos_drop_reason_5650) is a different chip, ignore.
802.1X auth bitmap = EPS 0x11280034 (bit = internal port), already matches kexec.

So the cold-only p8 r104 is an EGRESS VLAN filter drop. Cause found in our driver:
the sw_cfg replay writes VID 4094 = 0x01190111 (members 0,4,8), then the pt31 "VLAN table
default" loop rewrites ALL 4096 entries with w0 = 0x01190000, wiping the member mask.
pt32 missed it by reading 0x124cff88 (= vid 4088); vid 4094 is 0x124cffe0.
Vendor live (verified): 0x124cffe0 = 01190111 00000001 00000000 00000000.
FIX: after the fill, long_write the golden 0x124cxxx0 entries from sd5116_sw_cfg
{w0, 1, 0, 0}. Built; cold-boot test BLOCKED (host->board serial TX dead, U-Boot
can't be interrupted).

## pt34 - VLAN fix verified on cold; cold TX is ALSO broken; CPU frames die as up_map

Tooling: U-Boot is caught reliably by sending a space every 20ms from power-on
(waiting for the "Hit any key" banner is too late). TX ground truth = host
/proc/net/snmp Icmp InEchos with a static ARP for the host on the ONU (no root needed).

- pt33 fix verified: cold 0x124cffe0 = 01190111 00000001 0 0, and the p8 r104
  egr_vlan drop is GONE. Remaining drops: r112 up_map only.
- Cold TX is broken too, and was BEFORE the fix (pre-fix build bisected: 0 InEchos).
  pt32's "TX increments" was only the driver counter.
- Every CPU frame dies as up_map: CPU-in 0x1108101c and p4 r112 0x124471c0 rise 1:1
  (both 0x35 after N pings). So CPU-ingress frames are sent to PON only, never LAN.
- tx_bit14=0: frames do not enter the fabric at all (CPU-in flat). bit14 is required.
- Cold FDB learns bc:98 as 89095D98 1000BC98 40001000 = port 4, vid field 0x000 with
  the 0x1000 flag (dp struct field after port; CLI struct suggests "untagged").
  Host MAC d8:5e is correctly in vid 4094 port 8 (0x121c2fc0).
- Ruled out (identical cold vs vendor): PA_port_cfg 0..16, PUB deftag 0..16,
  OPP 0x12400020+p*4, PFE TPID 0x12000010/14, LAN MAC 0x14300000 except
  0x78/0xac/0xb0/0xb8 (writing vendor values live changed nothing).
- Vendor FDB never learns bc:98 from CPU and has many vid-0 entries on port 4
  (no flag), so vendor CPU TX path differs; the clean comparison is kexec+our
  kernel, but that kexec run HUNG after "Type 'ifconfig'" (console + net dead).
NEXT: get a working kexec run, ping, dump FDB to see which VLAN our CPU frames get
there (4094 vs 0|0x1000). If 4094, find what classifies CPU ingress (IFC rules,
PIE channel attrs). 0x124d6000 = PUB_lm_wan_mac (still differs, not yet examined).

## pt35 - kexec harness over telnet; pt34 "classification" theory RETRACTED

Tooling: initramfs now sets MAC, brings up eth0 and starts `busybox telnetd`; archive/diag/onu.py
drives everything (kexec|cold|tsh|sh). After kexec the serial console stalls, so kexec
runs are driven over telnet 192.168.1.20:23. Bootargs no longer carry dump_tables=1
(that made eth0-up take minutes and looked like a hang). archive/diag/mkdtb.sh builds DTB
variants: sd5116.dtb (native init) and inherit.dtb (no native init).

Results:
- kexec + inherit.dtb: 6/6 both ways. kexec + native init (sd5116.dtb): ALSO 6/6, so
  our native init (incl. the 0x14880030 pulse) does not break vendor state.
- RETRACTION of pt34: on the WORKING kexec board each CPU frame ALSO counts p4 up_map
  1:1 (baseline cpuin+2 upmap+2 for 2 pings), and the FDB there ALSO has a bc:98 entry
  in vid 0|0x1000 (0x121c3c50) next to the vid-4094 one. So the PON copy of a flood
  dies as up_map normally. On cold the LAN copy is lost WITHOUT any OPP drop counter.
- probe.sh (self-restoring zero-one-reg test) over PIE 0x10a70004..0x3d8: no single
  register changes the counter signature; 0x10a70200 = 0 stops TX entirely.
- 0x124d6000 (lm_wan_mac) is empty on working kexec too: not it.

BLOCKER: U-Boot no longer receives serial input (board->host output fine, even piped
picocom cannot stop autoboot) so no cold boots. Vendor U-Boot has no hush parser,
so an auto-TFTP bootcmd cannot fall back safely; flash env left untouched.
NEXT (needs cold boot): where the LAN copy dies - LAN MAC TX counters 0x14300400+,
OPP/EPS egress for port 8, host-side NIC error counters.

## pt36 - SOLVED: cold-boot networking works (EQM type 0x11 queue thresholds)

Method: EQM queue counters (indirect type 0x51, idx = queue; vendor
hi_kernel_dp_qos_queue_cnt_get) read via new module params eqm_rd="type start n"
(dmesg EQRD lines) and eqm_wr="type idx w0 w1 w2 w3".
- Working kexec: queues 0x51 (CPU->LAN) and 0x09 (LAN->CPU) count in w3 (pass).
- Cold: the same queues count in w0 (drop). The EQM dropped every frame at enqueue
  in BOTH directions, which is why no OPP drop counter showed it and why LAN MAC TX
  counters (0x143004d0/4d8/508) stayed 0.
- Diffed EQM types 0x10-0x55 cold vs kexec (archive/diag/eqdump.py). Never-before-compared
  type 0x11 (per-queue thresholds, 32 entries, aliased every 0x20) is all zero on cold.
  Writing type 0x11 alone fixes it (types 0x26/0x25/0x27/0x22/0x23 not needed).
FIX: sd5116_eqm_thresh_init() writes the 19 vendor entries (queues 0x01-0x10,
0x19, 0x1a, 0x1c) after sd5116_eqm_sched_init().
VERIFIED: repeated clean cold boots (U-Boot TFTP, no vendor kernel): 6/6 ONU->host,
100/100 host->ONU, telnet works.

Also fixed: kernel clock ran at 0.833x. The A9 global/TWD timers are fed by
PERIPHCLK = 83.33 MHz, not the 100 MHz APB clock; added periph_clk to sd5116.dtsi.
Verified ratio 1.0003. DTS bootargs no longer carry dump_tables=1.

## pt37 - interrupts and init minimization (2026-10-05, branch sd5116-upstream)

PIE interrupts: GIC SPI 87 (measured via GICD_ISPENDR3 bit 23 with sources unmasked).
PIE+0x04 = IRQ status (W1C), PIE+0x08 = IRQ mask (1 = masked; the old "0x7ff channel
enables" note was wrong: 0x7ff just masks RX bit 0 and TX-done bit 8). Vendor unmasks
0xfff00100 after napi_complete. Driver now: ISR acks/masks + napi; poll unmasks RX|TX.
Latency 14 ms -> 0.5-1.1 ms; 8 MB HTTP download in 1.9 s.

Minimization (archive/diag/skiptest.py, skip_results*.txt): each of 30 init blocks skipped on a
cold boot, pass = host<->board ping. Required: datapath pulse 0x14880030, sw_cfg replay,
bct, LAN MAC 0x14300000, EQM sched, EQM thresholds (type 0x11), fwd tables, CPU queues.
All other 22 (GPIO/"CRG" writes, 0x1488001c-2c/34-64/68-90, dp_init port incl. IFC,
PA ptype, CAR, security, VLAN fill, MCC mcg, ARL cfg, bit31 enables, DP reassert,
0x121800b4 strobe, ITF 0x1418010c, static FDB) removed together: 3/3 cold boots pass.
Driver 2305 -> 1318 lines, no module params. Next candidate: bisect the 133-entry
sd5116_sw_cfg replay table.
