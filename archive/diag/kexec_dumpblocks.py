#!/usr/bin/env python3
"""Full kexec hand-off cycle: boot vendor (which inits the L2 switch), transfer
our kernel, free RAM, kexec into it preserving the switch fabric.  Then bring up
eth0 and report ping both ways.  Assumes /srv/tftp has current Image/dtb/
trampoline.bin/kexecldr and host is 192.168.1.2."""
import socket, subprocess, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

fd = serialctl.open_port()


def boot_vendor():
    subprocess.run(["tuya-switch", "off"], capture_output=True)
    time.sleep(3)
    subprocess.run(["tuya-switch", "on"], capture_output=True)
    serialctl.run(fd, until=r"System Init OK", timeout=120)
    for _ in range(20):
        if subprocess.run(["ping", "-c1", "-W1", "192.168.1.1"],
                          capture_output=True).returncode == 0:
            return True
        time.sleep(2)
    return False


def tel(s, c, wait):
    s.sendall(c.encode() + b"\r\n")
    b = b""
    end = time.time() + wait
    while time.time() < end:
        try:
            b += s.recv(4096)
        except Exception:
            pass
    return b.decode(errors="replace")


def scmd(s, t=3):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.004)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=r"# $", timeout=t).decode(errors="replace")


def main():
    if not boot_vendor():
        print("vendor not reachable"); return 1
    serialctl.run(fd, until="XX", timeout=1)
    s = socket.create_connection(("192.168.1.1", 26), timeout=8)
    s.settimeout(0.4)
    time.sleep(1); s.sendall(b"root\r\n"); time.sleep(1)
    for rf in ("Image", "sd5116.dtb", "trampoline.bin", "kexecldr"):
        tel(s, f"tftp -g -l /tmp/{rf} -r {rf} 192.168.1.2", 8)
    tel(s, "chmod +x /tmp/kexecldr", 1)
    tel(s, "for p in 178 177 176 175 174 173 172 171 170 121 120 92 91 67 24; "
           "do kill -9 $p 2>/dev/null; done", 2)
    tel(s, "echo 3 > /proc/sys/vm/drop_caches", 2)
    print("firing kexec:", tel(s, "/tmp/kexecldr", 3)[-80:])
    s.close()

    out = serialctl.run(fd, until=r"Type 'ifconfig'", timeout=70)
    open(os.environ.get("BOOTLOG", "/tmp/kexec_boot.txt"), "w").write(
        out.decode(errors="replace"))
    ok = b"Type 'ifconfig'" in out
    print("kernel boot:", "OK" if ok else "FAILED")
    if not ok:
        print(out.decode(errors="replace")[-600:]); return 1

    scmd("ifconfig eth0 down")
    scmd("ifconfig eth0 hw ether BC:98:89:09:5D:98")
    scmd("ifconfig eth0 192.168.1.20 netmask 255.255.255.0 up")
    time.sleep(4)
    blocks = ("0x12100000","0x12180000","0x121c0000","0x12300000",
              "0x12380000","0x12400000","0x12440000","0x12480000")
    for b in blocks:
        out = scmd("i=0; while [ $i -lt 64 ]; do a=$((%s+i*4)); "
                   "v=$(devmem $a); [ \"$v\" != \"0x00000000\" ] && "
                   "echo \"K $(printf %%x $a) $v\"; i=$((i+1)); done; echo BDONE" % b, 90)
        print(out.replace("\r",""))
    print("ONU->host:", scmd("ping -c 3 192.168.1.2", 8).replace("\r", "")[-160:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
