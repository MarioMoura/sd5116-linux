#!/usr/bin/env python3
"""Dump datapath register blocks from the vendor root shell (telnet port 26)."""
import re, socket, sys, time

HOST, PORT = "192.168.1.1", 26
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


def main(out_path):
    s = socket.create_connection((HOST, PORT), timeout=10)
    s.settimeout(0.4)

    def readto(marker=b"# ", limit=2.0):
        b = b""
        end = time.time() + limit
        while time.time() < end:
            try:
                d = s.recv(4096)
            except socket.timeout:
                if marker in b:
                    break
                continue
            if not d:
                break
            b += d
            if marker in b:
                break
        return b.decode(errors="replace")

    time.sleep(1)
    s.sendall(b"root\r\n"); readto(b"# ")

    vals = {}
    for name, base, size in BLOCKS:
        for off in range(0, size, 4):
            addr = base + off
            s.sendall(f"devmem 0x{addr:x}\r\n".encode())
            resp = readto(b"# ")
            m = re.findall(r"(?m)^0x([0-9A-Fa-f]{8})\s*$", resp)
            if m:
                vals[addr] = int(m[-1], 16)

    with open(out_path, "w") as f:
        for name, base, size in BLOCKS:
            f.write(f"=== {name} @ 0x{base:08x} ===\n")
            for off in range(0, size, 4):
                v = vals.get(base + off)
                if v is not None:
                    f.write(f"0x{base+off:08x} = 0x{v:08x}\n")
    print(f"wrote {out_path} ({len(vals)} regs)")
    s.close()


if __name__ == "__main__":
    main(sys.argv[1])
