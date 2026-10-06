#!/usr/bin/env python3
"""Find which sd5116_sw_cfg entries are needed (cold boot + ping per test).

Greedy group testing: try skipping a group on top of everything already
dropped; on PASS keep it dropped, on FAIL split in halves and recurse.
State is kept in cfgbisect.json so the run can be resumed.
"""
import json, os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onu

DIR = os.path.dirname(os.path.abspath(__file__))
DTC = "/home/mario/onu/linux/scripts/dtc/dtc"
SRC = "/home/mario/onu/linux/arch/arm/boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dtb"
HDR = "/home/mario/onu/linux/drivers/net/ethernet/hisilicon/sd5116-sw-cfg.h"
STATE = os.path.join(DIR, "cfgbisect.json")
LOG = os.path.join(DIR, "cfgbisect.log")

addrs = re.findall(r"\{ (0x[0-9a-f]+), 0x[0-9a-f]+ \}", open(HDR).read())
N = len(addrs)


def log(s):
    print(s, flush=True)
    open(LOG, "a").write(time.strftime("%H:%M:%S ") + s + "\n")


def make_dtb(skip):
    words = [0] * 5
    for i in skip:
        words[i // 32] |= 1 << (i % 32)
    make_dtb_args("sd5116_eth.cfg_skip=" + ",".join(hex(w) for w in words))


def make_dtb_args(arg):
    dts = subprocess.run([DTC, "-q", "-I", "dtb", "-O", "dts", SRC],
                         capture_output=True, text=True, check=True).stdout
    dts = dts.replace('bootargs = "', f'bootargs = "{arg} ', 1)
    out = subprocess.run([DTC, "-q", "-I", "dts", "-O", "dtb"], input=dts.encode(),
                         capture_output=True, check=True).stdout
    open("/srv/tftp/skip.dtb", "wb").write(out)


def ping(size, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if subprocess.run(["ping", "-c", "3", "-W", "1", "-s", str(size), "192.168.15.11"],
                          capture_output=True).returncode == 0:
            return True
    return False


def run(skip):
    make_dtb(skip)
    for _ in range(4):
        try:
            onu.cold("skip.dtb")
            break
        except SystemExit as e:
            log(f"  boot retry ({str(e)[:80]})")
    else:
        sys.exit("cannot boot")
    return ping(56, 60) and ping(1400, 15)


def main():
    st = json.load(open(STATE)) if os.path.exists(STATE) else None
    if not st:
        blocks = {}
        for i, a in enumerate(addrs):
            blocks.setdefault(int(a, 16) >> 16, []).append(i)
        st = {"dropped": [], "kept": [], "todo": list(blocks.values())}
    while st["todo"]:
        g = st["todo"].pop(0)
        ok = run(st["dropped"] + g)
        log(f"skip {[addrs[i] for i in g]} -> {'PASS' if ok else 'FAIL'}")
        if ok:
            st["dropped"] += g
        elif len(g) == 1:
            st["kept"] += g
        else:
            h = len(g) // 2
            st["todo"][:0] = [g[:h], g[h:]]
        json.dump(st, open(STATE, "w"))
    log(f"DONE kept {len(st['kept'])}: {[addrs[i] for i in sorted(st['kept'])]}")


if __name__ == "__main__":
    main()
