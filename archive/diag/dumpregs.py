#!/usr/bin/env python3
"""Dump datapath register blocks from U-Boot (datapath known-good) or from the
booted kernel, in a diffable format.

Usage:
  dumpregs.py uboot  <out.txt>    power-cycle, break into U-Boot, TFTP (to make
                                  the datapath live), then md the blocks
  dumpregs.py kernel <out.txt>    dump via devmem on the running kernel
"""
import os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

BLOCKS = [
    ("B00_11280",0x11280000,0x100),("DP_11200",0x11200000,0x100),
    ("B02_11100",0x11100000,0x100),("B03_12100",0x12100000,0x100),
    ("IPS_11080",0x11080000,0x100),("L2_12180",0x12180000,0x200),
    ("B06_12380",0x12380000,0x100),("B07_11300",0x11300000,0x100),
    ("B08_12300",0x12300000,0x100),("B09_12400",0x12400000,0x100),
    ("B0a_12080",0x12080000,0x100),("B0b_11180",0x11180000,0x100),
    ("B0c_12000",0x12000000,0x100),("B0d_12500",0x12500000,0x100),
    ("B0e_12480",0x12480000,0x100),("B0f_11380",0x11380000,0x100),
    ("B10_11000",0x11000000,0x100),("B14_12200",0x12200000,0x100),
    ("B15_12280",0x12280000,0x100),("PIE_10a70",0x10a70000,0x300),
    ("GE0_13500",0x13500000,0x80),("GE1_13520",0x13520000,0x80),
    ("GE2_13530",0x13530000,0x80),("RST_14880",0x14880000,0x100),
    ("MDIO_14600",0x14600000,0x40),("MAC_11a01",0x11a01000,0x40),
]


def send(fd, s, until, timeout, slow=True):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        if slow:
            time.sleep(0.03)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=until, timeout=timeout).decode(errors="replace")


def parse_md(text):
    """U-Boot 'md' lines: 'ADDR: w0 w1 w2 w3    ....'"""
    vals = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-f]{8}):((?:\s+[0-9a-f]{8})+)", line.strip())
        if m:
            addr = int(m.group(1), 16)
            for i, w in enumerate(m.group(2).split()):
                vals[addr + i * 4] = int(w, 16)
    return vals


def dump_uboot(out_path):
    fd = serialctl.open_port()
    subprocess.run(["tuya-switch", "off"], capture_output=True)
    time.sleep(3)
    subprocess.run(["tuya-switch", "on"], capture_output=True)
    serialctl.run(fd, until=r"Hit any key", timeout=40, spam_cr=True)
    serialctl.run(fd, until=r"# ", timeout=8, spam_cr=True)

    send(fd, "setenv serverip 192.168.1.2", r"#", 5)
    send(fd, "setenv ipaddr 192.168.1.10", r"#", 5)
    # a real transfer makes the datapath demonstrably live
    for _ in range(3):
        o = send(fd, "tftpboot 0x81000000 trampoline.bin",
                 r"Bytes transferred|error|Usage", 60)
        if "Bytes transferred" in o:
            break
    else:
        sys.exit("U-Boot TFTP failed - datapath not proven live")

    vals = {}
    for name, base, size in BLOCKS:
        for off in range(0, size, 0x40):
            txt = send(fd, f"md 0x{base+off:x} 16", r"#", 10)
            vals.update(parse_md(txt))
    write(out_path, vals)


def dump_kernel(out_path):
    fd = serialctl.open_port()
    vals = {}
    for name, base, size in BLOCKS:
        for off in range(0, size, 4):
            addr = base + off
            txt = send(fd, f"devmem 0x{addr:x}", r"# $", 4, slow=False)
            # the value is on its OWN line ("0xVALUE"); the echo line begins
            # with "devmem" so it can't match a value-only line.
            m = re.findall(r"(?m)^0x([0-9A-Fa-f]{8})\s*$", txt)
            if m:
                vals[addr] = int(m[-1], 16)
    write(out_path, vals)


def write(path, vals):
    with open(path, "w") as f:
        for name, base, size in BLOCKS:
            f.write(f"=== {name} @ 0x{base:08x} ===\n")
            for off in range(0, size, 4):
                v = vals.get(base + off)
                if v is not None:
                    f.write(f"0x{base+off:08x} = 0x{v:08x}\n")
    print(f"wrote {path} ({len(vals)} regs)")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    (dump_uboot if sys.argv[1] == "uboot" else dump_kernel)(sys.argv[2])
