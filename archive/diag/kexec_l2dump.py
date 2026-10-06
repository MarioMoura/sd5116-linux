#!/usr/bin/env python3
"""kexec into our kernel (inherit working vendor fabric), then via devmem GET the
LEARNED FDB entry for bc:98:89:09:5d:98 (vid 4094) and dump the L2 config region,
so we can diff the working entry/config against the cold static one."""
import socket, subprocess, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios, re

fd = serialctl.open_port()


def boot_vendor():
    subprocess.run(["tuya-switch", "off"], capture_output=True); time.sleep(3)
    subprocess.run(["tuya-switch", "on"], capture_output=True)
    serialctl.run(fd, until=r"System Init OK", timeout=120)
    for _ in range(20):
        if subprocess.run(["ping","-c1","-W1","192.168.1.1"],capture_output=True).returncode==0:
            return True
        time.sleep(2)
    return False


def tel(s, c, wait):
    s.sendall(c.encode()+b"\r\n"); b=b""; end=time.time()+wait
    while time.time()<end:
        try: b+=s.recv(4096)
        except Exception: pass
    return b.decode(errors="replace")


def scmd(s, t=2.0):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s: os.write(fd, ch.encode()); time.sleep(0.012)
    os.write(fd, b"\r"); time.sleep(0.15)
    return serialctl.run(fd, until=r"# $", timeout=t).decode(errors="replace")


def rd(a):
    m=re.findall(r"0x[0-9A-Fa-f]{8}", scmd(f"devmem {a}")); return m[-1] if m else "??"


def main():
    if not boot_vendor(): print("vendor unreachable"); return 1
    serialctl.run(fd, until="XX", timeout=1)
    s=socket.create_connection(("192.168.1.1",26),timeout=8); s.settimeout(0.4)
    time.sleep(1); s.sendall(b"root\r\n"); time.sleep(1)
    for rf in ("Image","sd5116.dtb","trampoline.bin","kexecldr"):
        tel(s,f"tftp -g -l /tmp/{rf} -r {rf} 192.168.1.2",8)
    tel(s,"chmod +x /tmp/kexecldr",1)
    tel(s,"for p in 178 177 176 175 174 173 172 171 170 121 120 92 91 67 24; do kill -9 $p 2>/dev/null; done",2)
    tel(s,"echo 3 > /proc/sys/vm/drop_caches",2)
    tel(s,"/tmp/kexecldr",3); s.close()
    out=serialctl.run(fd, until=r"Type 'ifconfig'", timeout=70)
    if b"Type 'ifconfig'" not in out: print("kexec boot failed"); print(out[-500:]); return 1
    time.sleep(2)
    # bring eth0 up + ping so the switch LEARNS bc:98 on the CPU port (as normal)
    scmd("ifconfig eth0 down"); scmd("ifconfig eth0 hw ether BC:98:89:09:5D:98")
    scmd("ifconfig eth0 192.168.1.20 netmask 255.255.255.0 up"); time.sleep(2)
    scmd("ping -c 3 192.168.1.2 >/dev/null 2>&1 &"); time.sleep(3)
    # GET learned FDB entry for bc:98 vid4094
    inv_lo=0x98|(0x5d<<8)|(0x09<<16)|(0x89<<24); inv_hi=0x98|(0xbc<<8)
    scmd(f"devmem 0x12180198 32 0x{inv_lo:08x}")
    scmd(f"devmem 0x1218019c 32 0x{(inv_hi|(4094<<16)):08x}")
    scmd("devmem 0x121801a0 32 0x0"); scmd("devmem 0x12180190 32 0x1")
    time.sleep(0.2)
    print("=== KEXEC LEARNED FDB entry (bc:98 vid4094) ===")
    print(" 0x190(cmd) =", rd("0x12180190"))
    print(" 0x194(res) =", rd("0x12180194"), "(bit31=found, low=index)")
    print(" 0x1a0(port)=", rd("0x121801a0"))
    print("=== KEXEC L2 config region 0x12180100..0x1218013c ===")
    for a in range(0x12180100,0x12180140,4):
        print(f"  0x{a:x} = {rd(f'0x{a:x}')}")
    print("=== CPU-out counter 0x112c1020 =", rd("0x112c1020"), "(nonzero = RX works on kexec)")
    return 0


if __name__=="__main__":
    sys.exit(main())
