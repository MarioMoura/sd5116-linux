#!/usr/bin/env python3
"""Read the deftag (port default-tag/PVID) table, VLAN 4094 membership, and a
few key CPU-port registers from whatever switch state is live now.  Run against
the working kexec kernel to get the forwarding reference, then against
cold-native to diff."""
import os, time, sys, re
sys.path.insert(0, '.')
import serialctl, termios
fd = serialctl.open_port()


def rd(a):
    termios.tcflush(fd, termios.TCIFLUSH)
    s = f"devmem 0x{a:x}"
    for ch in s:
        os.write(fd, ch.encode()); time.sleep(0.003)
    os.write(fd, b"\r")
    out = serialctl.run(fd, until=r"# $", timeout=3).decode(errors="replace")
    m = re.findall(r"(?m)^0x([0-9A-Fa-f]{8})\s*$", out)
    return m[-1] if m else "????????"


os.write(fd, b"\r"); serialctl.run(fd, until=r"# $", timeout=3)
tag = sys.argv[1] if len(sys.argv) > 1 else "state"
print(f"### {tag} ###")
print("-- deftag 0x124d7000..0x124d7040 --")
for a in range(0x124d7000, 0x124d7044, 4):
    print(f"0x{a:08x} = 0x{rd(a)}")
print("-- VLAN membership 4094/others --")
for a in (0x124cffe0, 0x124cffe4, 0x124cff90, 0x124cff94, 0x124cfc90, 0x124cfc94):
    print(f"0x{a:08x} = 0x{rd(a)}")
print("-- CPU-port / DP / FAM key regs --")
for a in (0x11100010, 0x11200014, 0x11200040, 0x1120004c, 0x11200050,
          0x12180134, 0x12180180, 0x12180194, 0x14180108):
    print(f"0x{a:08x} = 0x{rd(a)}")
