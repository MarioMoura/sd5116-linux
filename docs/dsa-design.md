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

## Security

- User ports push the PVID on tagged and priority-tagged ingress too (LAN cannot pick a VLAN).
- Setup flushes the FDB and clears the flood entry of all 4096 VLANs.
- RX buffers are 16 KB (engine length field max), there is no RX size limit in the descriptor.

## Not supported (YAGNI)

Bridge offload, FDB ops, VLAN filtering, PON port, VLAN sub-interfaces need CONFIG_VLAN_8021Q.
