#!/usr/bin/env python3
"""Dump datapath register blocks from the running vendor kernel over telnet."""
import re, socket, sys, time

HOST = "192.168.1.1"
BLOCKS = [
    ("PIE",    0x10a70000, 0x300),
    ("RSTCTL", 0x14880000, 0x100),
    ("MDIO",   0x14600000, 0x40),
    ("IPS",    0x11080000, 0x80),
    ("L2",     0x12180000, 0x200),
    ("DP1",    0x11200000, 0x80),
    ("DP2",    0x13500000, 0x80),
]


def main(out_path):
    s = socket.create_connection((HOST, 23), timeout=10)
    s.settimeout(2)

    def drain():
        buf = b""
        try:
            while True:
                d = s.recv(4096)
                if not d:
                    break
                buf += d
        except socket.timeout:
            pass
        return buf

    time.sleep(1); drain()
    s.sendall(b"root\r\n"); time.sleep(1); drain()
    s.sendall(b"\r\n"); time.sleep(1); drain()

    vals = {}
    for name, base, size in BLOCKS:
        for off in range(0, size, 4):
            addr = base + off
            s.sendall(f"devmem 0x{addr:x}\r\n".encode())
            time.sleep(0.03)
            resp = drain().decode(errors="replace")
            m = re.findall(r"0x([0-9A-Fa-f]{8})", resp)
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
