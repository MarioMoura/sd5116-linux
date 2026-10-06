#!/usr/bin/env python3
"""kexec into our kernel (inherit vendor fabric) and log the FULL boot console
so the EQM/RGN dump_tables output is captured.  DTB must have dump_tables=1."""
import socket, subprocess, sys, time, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

LOG = "/tmp/claude-1000/-home-mario-onu/9a664151-f913-4377-8d07-d26d0707d35a/scratchpad/kexec_cap.log"
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
    tel(s, "/tmp/kexecldr", 3)
    s.close()

    # Capture the full boot console (dump prints during probe).
    out = serialctl.run(fd, until=r"Type 'ifconfig'", timeout=70)
    with open(LOG, "wb") as f:
        f.write(out)
    txt = out.decode(errors="replace")
    print("=== EQM/RGN dump lines ===")
    for ln in txt.splitlines():
        if "EQM t" in ln or "RGN " in ln or "FDB get" in ln:
            print(ln.strip())
    print("=== booted:", "OK" if "Type 'ifconfig'" in txt else "FAILED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
