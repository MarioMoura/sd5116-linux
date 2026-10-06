#!/usr/bin/env python3
"""Diff two dumpregs.py outputs."""
import re, sys

BLK = [("PIE", 0x10a70000), ("PIEAUX1", 0x10a20000), ("PIEAUX2", 0x10a30000),
       ("RSTCTL", 0x14880000), ("MDIO", 0x14600000), ("IPS", 0x11080000),
       ("L2", 0x12180000), ("DP1", 0x11200000),
       ("MAC1", 0x11a01000), ("MAC2", 0x11a02000), ("MAC3", 0x11a03000),
       ("GE0", 0x13500000), ("GE1", 0x13520000), ("GE2", 0x13530000),
       ("GE3", 0x13550000)]


def load(p):
    d = {}
    for line in open(p):
        m = re.match(r"0x([0-9a-f]{8}) = 0x([0-9a-f]{8})", line)
        if m:
            d[int(m.group(1), 16)] = int(m.group(2), 16)
    return d


def blk(a):
    best = None
    for n, b in BLK:
        if a >= b and (best is None or b > best[1]):
            best = (n, b)
    return best


a, b = load(sys.argv[1]), load(sys.argv[2])
common = sorted(set(a) & set(b))
diffs = [x for x in common if a[x] != b[x]]
print(f"compared {len(common)} registers, {len(diffs)} differ\n")
for x in diffs:
    n, base = blk(x)
    print(f"{n:7s} +0x{x-base:04x} (0x{x:08x}): A=0x{a[x]:08x}  B=0x{b[x]:08x}")
