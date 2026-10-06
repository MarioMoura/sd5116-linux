#!/usr/bin/env python3
"""Cold-boot the staged kernel via U-Boot TFTP, bring up eth0, and test BOTH
directions.  A successful `ping` FROM the ONU proves RX works (the ONU must
receive the echo replies - the hop that has been failing on cold boot)."""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

FILES = [("0x80008000", "Image"), ("0x80F80000", "sd5116.dtb"),
         ("0x80F00000", "trampoline.bin")]
fd = serialctl.open_port()


def send(s, until, timeout):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.03)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=until, timeout=timeout).decode(errors="replace")


def scmd(s, t=4):
    return send(s, r"# $", t)


def main():
    subprocess.run(["tuya-switch", "off"], capture_output=True)
    time.sleep(3)
    subprocess.run(["tuya-switch", "on"], capture_output=True)

    serialctl.run(fd, until=r"Hit any key", timeout=40, spam_cr=True)
    serialctl.run(fd, until=r"# ", timeout=8, spam_cr=True)
    send("setenv serverip 192.168.1.2", r"#", 5)
    send("setenv ipaddr 192.168.1.10", r"#", 5)
    for addr, name in FILES:
        for _ in range(3):
            if "Bytes transferred" in send(f"tftpboot {addr} {name}",
                    r"Bytes transferred|error|Usage|not alive", 90):
                break
        else:
            sys.exit(f"FAILED to load {name}")
    print("=== booting ===")
    log = send("go 0x80F00000",
               r"Type 'ifconfig'|Kernel panic|Unable to handle", 180)
    open(os.environ.get("BOOTLOG","/tmp/bootlog.txt"),"w").write(log)
    if "Type 'ifconfig'" not in log:
        print(log[-1500:]); sys.exit("boot failed")
    # grep the queue-create confirmation from the boot log
    for ln in log.splitlines():
        if "queues" in ln or "fabric configured" in ln:
            print("BOOT:", ln.strip())

    scmd("ifconfig eth0 down")
    scmd("ifconfig eth0 hw ether BC:98:89:09:5D:98")
    scmd("ifconfig eth0 192.168.1.20 netmask 255.255.255.0 up")
    time.sleep(3)
    # Prime learning: ONU TXes first so the host learns .20 (egress).
    print("--- ONU->host ping (egress + RX of replies = full path) ---")
    r = scmd("ping -c 4 192.168.1.2", 10).replace("\r", "")
    print(r[-400:])
    # CPU-output counter: nonzero => frames reached PIE RX from the fabric.
    print("--- CPU-out cnt 0x112c1020 (nonzero = RX delivered) ---")
    print(scmd("devmem 0x112c1020", 4).replace("\r", "")[-120:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
