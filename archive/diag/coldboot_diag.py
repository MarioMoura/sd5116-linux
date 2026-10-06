#!/usr/bin/env python3
"""Cold-boot the current kernel, bring up eth0, ping the host, report both
directions.  Also snapshots the host neighbor table so we can tell whether the
ONU's egress (ONU->host) reaches the wire even when replies don't come back."""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

FILES = [("0x80008000", "Image"), ("0x80F80000", "sd5116.dtb"),
         ("0x80F00000", "trampoline.bin")]
ONU_MAC = "bc:98:89:09:5d:98"


def send(fd, s, until, timeout):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.03)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=until, timeout=timeout).decode(errors="replace")


def scmd(fd, s, t=4):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.004)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=r"# $", timeout=t).decode(errors="replace")


def main():
    fd = serialctl.open_port()
    if "--no-cycle" not in sys.argv:
        subprocess.run(["tuya-switch", "off"], capture_output=True)
        time.sleep(3)
        subprocess.run(["tuya-switch", "on"], capture_output=True)

    serialctl.run(fd, until=r"Hit any key", timeout=40, spam_cr=True)
    serialctl.run(fd, until=r"# ", timeout=8, spam_cr=True)
    send(fd, "setenv serverip 192.168.1.2", r"#", 5)
    send(fd, "setenv ipaddr 192.168.1.10", r"#", 5)
    for addr, name in FILES:
        for _ in range(3):
            out = send(fd, f"tftpboot {addr} {name}",
                       r"Bytes transferred|error|Usage|not alive", 90)
            if "Bytes transferred" in out:
                break
        else:
            sys.exit(f"FAILED to load {name}")
    print("=== booting ===")
    log = send(fd, "go 0x80F00000",
               r"Type 'ifconfig'|Kernel panic|Unable to handle", 90)
    if "Type 'ifconfig'" not in log:
        print(log[-1500:]); sys.exit("kernel did not reach shell")

    # capture the switch: line from our driver
    for ln in log.splitlines():
        if "switch:" in ln or "L2 switch" in ln:
            print("DRIVER:", ln.strip())

    scmd(fd, "ifconfig eth0 down")
    scmd(fd, "ifconfig eth0 hw ether BC:98:89:09:5D:98")
    scmd(fd, "ifconfig eth0 192.168.1.20 netmask 255.255.255.0 up")
    time.sleep(3)
    # generate egress: ARP for host, then ping
    subprocess.run(["ip", "neigh", "flush", "dev", "enp6s0"], capture_output=True)
    pres = scmd(fd, "ping -c 4 192.168.1.2", 10).replace("\r", "")
    print("--- ONU->host ping ---")
    print(pres[-400:])
    stats = scmd(fd, "ifconfig eth0", 4).replace("\r", "")
    for ln in stats.splitlines():
        if "packets" in ln or "RX bytes" in ln:
            print("STAT:", ln.strip())

    # host side: did we ever hear the ONU's MAC?  (egress proof, no root)
    neigh = subprocess.run(["ip", "neigh", "show", "dev", "enp6s0"],
                           capture_output=True, text=True).stdout
    heard = [l for l in neigh.splitlines() if ONU_MAC in l.lower()]
    print("--- host heard ONU MAC? ---")
    print("\n".join(heard) if heard else "NO - host never received a frame from the ONU")
    return 0


if __name__ == "__main__":
    sys.exit(main())
