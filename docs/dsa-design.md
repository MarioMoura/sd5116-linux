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

## Not supported (YAGNI)

Bridge offload, FDB ops, VLAN filtering, PON port, VLAN sub-interfaces need CONFIG_VLAN_8021Q.
