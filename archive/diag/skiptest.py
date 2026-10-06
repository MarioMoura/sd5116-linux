#!/usr/bin/env python3
"""Cold-boot with sd5116_eth.skip_blocks=<mask> and check host<->board ping.

  skiptest.py <out.txt> <mask> [<mask> ...]     masks as ints (0 = baseline)
"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import onu

DTC = "/home/mario/onu/linux/scripts/dtc/dtc"
SRC = "/home/mario/onu/linux/arch/arm/boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dtb"


def make_dtb(mask):
    dts = subprocess.run([DTC, "-q", "-I", "dtb", "-O", "dts", SRC],
                         capture_output=True, text=True, check=True).stdout
    dts = dts.replace('bootargs = "', f'bootargs = "sd5116_eth.skip_blocks={mask} ', 1)
    out = subprocess.run([DTC, "-q", "-I", "dts", "-O", "dtb"], input=dts.encode(),
                         capture_output=True, check=True).stdout
    open("/srv/tftp/skip.dtb", "wb").write(out)


def ping_ok(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        if subprocess.run(["ping", "-c", "3", "-W", "1", "192.168.1.20"],
                          capture_output=True).returncode == 0:
            return True
    return False


def run(mask):
    make_dtb(mask)
    for _ in range(4):
        try:
            onu.cold("skip.dtb")
            break
        except SystemExit as e:
            print(f"  boot retry ({e})", flush=True)
    else:
        return "BOOTFAIL"
    return "PASS" if ping_ok() else "FAIL"


if __name__ == "__main__":
    out = open(sys.argv[1], "a")
    for m in map(int, sys.argv[2:]):
        bits = [i for i in range(32) if m >> i & 1]
        r = run(m)
        line = f"mask={m:#010x} bits={bits} -> {r}"
        print(line, flush=True)
        out.write(line + "\n")
        out.flush()
