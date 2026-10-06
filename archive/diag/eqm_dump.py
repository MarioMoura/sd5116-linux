#!/usr/bin/env python3
"""Dump EQM indirect tables from vendor root shell (telnet 26)."""
import re, socket, sys, time

HOST, PORT = "192.168.1.1", 26
EQM = 0x11200000

def main(out_path, types):
    s = socket.create_connection((HOST, PORT), timeout=15)
    s.settimeout(0.4)
    def rd(limit=2.0):
        b=b""; end=time.time()+limit
        while time.time()<end:
            try: d=s.recv(4096)
            except socket.timeout:
                if b"# " in b: break
                continue
            if not d: break
            b+=d
            if b"# " in b: break
        return b.decode(errors="replace")
    def sh(cmd):
        s.sendall((cmd+"\r\n").encode()); return rd()
    def dm(addr):
        r = sh(f"devmem 0x{addr:x}")
        m = re.findall(r"(?m)^0x([0-9A-Fa-f]{8})\s*$", r)
        return int(m[-1],16) if m else None
    time.sleep(1); s.sendall(b"root\r\n"); rd()
    out=[]
    for t, n in types:
        for idx in range(n):
            cmd = 0x80000000 | (t<<20) | idx
            sh(f"devmem 0x{EQM+0x48:x} 32 0x{cmd:x}")
            words=[dm(EQM+0x4c+4*i) for i in range(4)]
            out.append(f"t{t:02x} idx{idx:02x}: " + " ".join("%08x"%(w if w is not None else 0) for w in words))
    open(out_path,"w").write("\n".join(out)+"\n")
    print("\n".join(out))

if __name__=="__main__":
    main(sys.argv[1], [(0x13,0x20),(0x50,0x20),(0x22,0x60)])
