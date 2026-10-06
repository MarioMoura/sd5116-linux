#!/usr/bin/env python3
"""Attach to the ONU's kernel shell and pull the 'TBL ...' dump lines from
dmesg into a file. Usage: grab_tbl.py <outfile>"""
import os, time, sys
sys.path.insert(0, '.')
import serialctl, termios
fd = serialctl.open_port()


def scmd(s, t=30):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode()); time.sleep(0.004)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=r"# $", timeout=t).decode(errors="replace")


os.write(fd, b"\r"); serialctl.run(fd, until=r"# $", timeout=4)
out = scmd("dmesg | grep TBL", 40)
lines = [l.strip() for l in out.splitlines() if "TBL " in l and "grep" not in l]
# strip kernel timestamp prefix "[  12.34] " if present
clean = []
for l in lines:
    i = l.find("TBL ")
    clean.append(l[i:])
open(sys.argv[1], "w").write("\n".join(clean) + "\n")
print(f"wrote {len(clean)} TBL lines to {sys.argv[1]}")
for l in clean:
    print(l)
