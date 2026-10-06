#!/usr/bin/env python3
"""Find the longest prefix of the phase 2 drop history that still works
(verified boots), then requeue everything after it."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import initbisect as ib
import cfgbisect as cb

st = json.load(open(ib.STATE))
d = st["dropped"]
lo, hi = 0, len(d)          # prefix lo known good (0 = nothing dropped)
if ib.run(d):
    cb.log(f"validate: all {len(d)} drops PASS")
    sys.exit(0)
while hi - lo > 1:
    mid = (lo + hi) // 2
    ok = ib.run(d[:mid])
    cb.log(f"validate: prefix {mid} -> {'PASS' if ok else 'FAIL'}")
    lo, hi = (mid, hi) if ok else (lo, mid)

rows = [l.split() for l in open(ib.WRITES)]
bad = d[lo:]
cb.log(f"validate: first bad drop #{bad[0]} {rows[bad[0]][1]}:{rows[bad[0]][2]}, requeue {len(bad)}")
groups = {}
for i in bad:
    w, a = rows[i][1], rows[i][2]
    groups.setdefault((w, int(a, 16) >> (16 if w == "eqm" else 12)), []).append(i)
st["dropped"] = d[:lo]
st["todo"] = list(groups.values()) + st["todo"]
json.dump(st, open(ib.STATE, "w"))
