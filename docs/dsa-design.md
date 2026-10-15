# SD5116 Ethernet: conduit + DSA switch (done, branch `sd5116`)

## Split

| Piece | File | Compatible |
|---|---|---|
| Conduit | `drivers/net/ethernet/hisilicon/sd5116-pie.c` | `hisilicon,sd5116-pie` |
| Switch | `drivers/net/dsa/sd5116.c` | `hisilicon,sd5116-switch` |
| Tagger | `net/dsa/tag_sd5116.c` (`sd5116-8021q`) | |
| MDIO | `drivers/net/mdio/mdio-sd5116.c` | `hisilicon,sd5116-mdio` |

Ports: 0 = PON (never a VLAN member), 4 = CPU (fixed-link to the PIE), 8 = LAN (`lan`, PHY at MDIO 1). Modelled after `drivers/net/dsa/mt7628.c`.

## Init minimization (archive/diag/cfgbisect.py, archive/diag/initbisect.py)

Cold boot + ping per test, greedy group drop, FAIL counted only if repeated, every boot verified via /proc/cmdline.

- 133-entry captured register table: none left (the 3 survivors were VLAN 4094 PVIDs and flood entry, now programmed by tag_8021q).
- Indirect queue tables (EQM types 0x13, 0x22, 0x23) and the OPP queue map hold garbage at power-on, so every entry fails alone: written fully, as formula loops and 20 row bases.
- Kept singles: scheduler 0x3d, CPU queue 9 enable (profile 0x1a), thresholds 0x01 and 0x1a, channels 4 and 8, policer, IFC actions 2 and 4, LAN MAC mode (now phylink).
- Lesson: the old driver accepted tagged and untagged CPU frames, so "add PVID on ingress" looked unneeded; DSA needs it.

## Two VLANs per user port

The switch floods frames back to their ingress port (port_iso table has no effect), so with one VLAN per port every LAN broadcast was echoed onto the LAN and other hosts' MACs got learned on the ONU port (their DHCP broke). Now the tag_8021q VLAN (PVID, 3080) has only the CPU as member, and the tagger sends in `SD5116_TX_VID` (3080 - 1024 = 2056), which has only the LAN port. Egress action 1 strips the outer tag whatever its VID (5 only strips the PVID). Check: switch counter 0x112c1028 (flood copies out of `lan`) only grows with the board's own broadcasts (3 in 30 s on 2026-10-09, against hundreds of LAN broadcasts received), not with LAN traffic.

## Security

- User ports push the PVID on tagged and priority-tagged ingress too (LAN cannot pick a VLAN).
- Setup flushes the FDB and clears the flood entry of all 4096 VLANs.
- RX buffers are 16 KB (engine length field max), there is no RX size limit in the descriptor.

## LAN MAC (GE port 8, base `GE_MAC(8)`)

Found in `hi_bridge.ko` and by register sweeps; verified over a direct cable (USB adapter with pause support).

- Link: `mac_link_up` writes the speed and duplex (+0x14) inside the +0x18 update latch, then sets the port enable (+0x1c bit 0, vendor `set_port_en`); `mac_link_down` clears it.
- Flow control, sent: FAM `FAM_FC(n)` bit 31 enables it with XON/XOFF thresholds (reset value 0x00fa0032 has it off), plus `IPS_FC_CTRL`.
- Flow control, received: +0x50 bit 0. The MAC honoured received pause frames by default even with flow control off; it is now set only when negotiated. Found by a sweep under continuous pause: only this bit let traffic out.
- MTU: +0x100 is the max frame length (bits 13:0, FCS included, reset 1518, the vendor writes 2000). Only TX is limited (oversize frames count in the TX over-2000 counter); RX passed frames above it. Max MTU 1978 = 2000 - 22, frames up to 1996 bytes verified both ways. The vendor MTU API is software only.
- Statistics: MIB counters at +0x400 (RX) and +0x4d0 (TX), exposed through ethtool.
- +0x000 is a status register (read only, harmless).
- The PIE restarts its rings on a transmit timeout.

Open: once, moving the cable from the router to the adapter hung the board until the watchdog reset it; not reproduced. The vendor has a PHY reset workaround ("isolate err", "OUT OF LOCKING") we do not port.

## Not supported (YAGNI)

Bridge offload, FDB ops, VLAN filtering, PON port, VLAN sub-interfaces need CONFIG_VLAN_8021Q.
