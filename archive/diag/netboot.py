#!/usr/bin/env python3
"""Power-cycle the ONU, break into U-Boot, TFTP-load and boot the staged kernel.

Usage: netboot.py [--no-cycle]
Prints the kernel boot log; exits non-zero if any stage fails.
"""
import os, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

FILES = [("0x80008000", "Image"), ("0x80F80000", "sd5116.dtb"),
         ("0x80F00000", "trampoline.bin")]


def send(fd, s, until, timeout):
    """U-Boot's console drops fast input; type at ~30 chars/sec."""
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.03)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=until, timeout=timeout).decode(errors="replace")


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
        for attempt in range(3):
            out = send(fd, f"tftpboot {addr} {name}",
                       r"Bytes transferred|error|Usage|not alive", 90)
            if "Bytes transferred" in out:
                print(f"loaded {name} @ {addr}")
                break
            print(f"retry {name}: {out.strip().splitlines()[-1:]}")
        else:
            sys.exit(f"FAILED to load {name}")

    print("=== booting ===")
    log = send(fd, "go 0x80F00000",
               r"Type 'ifconfig'|Kernel panic|Unable to handle", 90)
    print(log[-4000:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
