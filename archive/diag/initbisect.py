#!/usr/bin/env python3
"""Phase 2: find which of the numbered switch init writes are needed.

  initbisect.py dump     boot with init_dump=1, save the write list
  initbisect.py          bisect (resumable, state in initbisect.json)

cfg_skip is fixed to the phase 1 result (cfgbisect.json "dropped").
"""
import json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cfgbisect as cb
import onu

DIR = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(DIR, "initbisect.json")
WRITES = os.path.join(DIR, "initwrites.txt")
cb.LOG = os.path.join(DIR, "initbisect.log")


def words(idx, n):
    w = [0] * n
    for i in idx:
        w[i // 32] |= 1 << (i % 32)
    return ",".join(hex(x) for x in w)


def args(skip, dump=False):
    cfg = json.load(open(os.path.join(DIR, "cfgbisect.json")))["dropped"]
    a = f"sd5116_eth.cfg_skip={words(cfg, 5)} sd5116_eth.init_skip={words(skip, 40)}"
    return a + (" sd5116_eth.init_dump=1 loglevel=8" if dump else "")


def run(skip, dump=False):
    cb.make_dtb_args(args(skip, dump))
    want = args(skip, dump).split()[1]
    for _ in range(4):
        try:
            onu.cold("skip.dtb")
            # stale serial output can fake a boot; check what really runs
            if want not in onu.sh("cat /proc/cmdline"):
                raise SystemExit("running kernel has the wrong cmdline")
            break
        except SystemExit as e:
            cb.log(f"  boot retry ({str(e)[:80]})")
    else:
        sys.exit("cannot boot")
    return cb.ping(56, 60) and cb.ping(1400, 15)


def dump():
    run([], dump=True)
    out = open("/tmp/onu_boot.txt", "rb").read().decode(errors="replace")
    rows = re.findall(r"initw (\d+) (\w+) ([0-9a-f]{8}) ([0-9a-f]{8})", out)
    open(WRITES, "w").write("".join(" ".join(r) + "\n" for r in rows))
    print(len(rows), "writes")


def main():
    rows = [l.split() for l in open(WRITES)]
    st = json.load(open(STATE)) if os.path.exists(STATE) else None
    if not st:
        groups = {}
        for n, what, a, _ in rows:
            if a == "14880030":
                continue    # reset pulse: skipping it keeps U-Boot's state
            key = (what, int(a, 16) >> (16 if what == "eqm" else 12))
            groups.setdefault(key, []).append(int(n))
        st = {"dropped": [], "kept": [], "todo": list(groups.values())}
    desc = lambda g: f"{rows[g[0]][1]} {rows[g[0]][2]}..{rows[g[-1]][2]} ({len(g)})"
    while st["todo"]:
        g = st["todo"].pop(0)
        # single FAILs can be noise: confirm with a second boot
        ok = run(st["dropped"] + g) or run(st["dropped"] + g)
        cb.log(f"skip {desc(g)} -> {'PASS' if ok else 'FAIL'}")
        if ok:
            st["dropped"] += g
        elif len(g) == 1:
            st["kept"] += g
        else:
            h = len(g) // 2
            st["todo"][:0] = [g[:h], g[h:]]
        json.dump(st, open(STATE, "w"))
    # re-confirm each kept write: needed only if dropping it fails twice more
    for i in sorted(st.get("kept", [])):
        if i in st.get("confirmed", []):
            continue
        if run(st["dropped"] + [i]) or run(st["dropped"] + [i]):
            st["dropped"].append(i)
            st["kept"].remove(i)
            cb.log(f"recheck {rows[i][1]}:{rows[i][2]} -> not needed")
        else:
            st.setdefault("confirmed", []).append(i)
        json.dump(st, open(STATE, "w"))
    cb.log(f"DONE kept {len(st['kept'])}: " +
           " ".join(f"{rows[i][1]}:{rows[i][2]}={rows[i][3]}" for i in sorted(st["kept"])))


if __name__ == "__main__":
    dump() if sys.argv[1:] == ["dump"] else main()
