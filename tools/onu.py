#!/usr/bin/env python3
"""ONU test harness: cold (U-Boot TFTP) or kexec boot, shell, TX/RX tests.

  onu.py cold [dtb]        boot staged Image via U-Boot, run net test
  onu.py kexec [dtb]       boot vendor, kexec our Image, run net test
  onu.py sh "cmd"          run a command on the running kernel
"""
import os, re, select, socket, subprocess, sys, time, termios
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl

fd = serialctl.open_port()
HOST_MAC = open("/sys/class/net/enp6s0/address").read().strip()
_n = [0]


def plug(state):
    # the smart plug drops off Wi-Fi now and then; wait for it
    for _ in range(60):
        r = subprocess.run(["tuya-switch", state], capture_output=True, text=True)
        if r.returncode == 0 and "Error" not in r.stdout:
            return
        print(f"  plug {state} failed, retrying", flush=True)
        time.sleep(10)
    sys.exit("smart plug unreachable")


def power_cycle():
    if os.environ.get("ONU_SOFT_RESET"):
        # watchdog reset from the running kernel; no plug needed
        os.write(fd, b"\rreboot -f\r")
        return
    plug("off")
    time.sleep(3)
    plug("on")


def send(s, until, timeout):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.01)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=until, timeout=timeout).decode(errors="replace")


def sh(cmd, timeout=30):
    _n[0] += 1
    m = "__E%d" % _n[0]
    out = send(f"{cmd}; echo {m[:3]}\"\"{m[3:]}", r"\n" + m + r"\r?\n", timeout)
    out = out.replace("\r", "")
    body = out.split("\n", 1)[1] if "\n" in out else ""
    return body.rsplit(m, 1)[0].rstrip("\n")


def catch_uboot():
    power_cycle()
    b = b""
    hit = False
    end = time.time() + 40
    while time.time() < end:
        if hit:
            os.write(fd, b"\r")
        r, _, _ = select.select([fd], [], [], 0.03)
        if r:
            try:
                b += os.read(fd, 4096)
            except BlockingIOError:
                pass
        if not hit and b"Hit any key" in b:
            hit = True
            b = b""
        if b"Booting kernel" in b:
            return False
        if hit and re.search(rb"(^|\n)# ", b):
            time.sleep(0.3)
            serialctl.run(fd, until="XXXX", timeout=0.5)
            return True
        if b"Booting kernel" in b:
            return False
    return False


def wait_shell(timeout):
    out = serialctl.run(fd, until=r"Type 'ifconfig'|Kernel panic|Unable to handle",
                        timeout=timeout)
    open("/tmp/onu_boot.txt", "wb").write(out)
    if b"Type 'ifconfig'" not in out:
        sys.exit("boot failed:\n" + out.decode(errors="replace")[-1500:])
    time.sleep(1)
    serialctl.run(fd, until="XXXX", timeout=1)


def cold(dtb="sd5116.dtb"):
    for _ in range(3):
        if catch_uboot():
            break
    else:
        sys.exit("could not catch U-Boot")
    send("setenv serverip 192.168.1.2", r"#", 5)
    send("setenv ipaddr 192.168.1.10", r"#", 5)
    for addr, name in (("0x80008000", "Image"), ("0x80F80000", dtb),
                       ("0x80F00000", "trampoline.bin")):
        for _ in range(3):
            if "Bytes transferred" in send(f"tftpboot {addr} {name}",
                                           r"Bytes transferred|error|not alive", 90):
                break
        else:
            sys.exit("tftp failed: " + name)
    send("go 0x80F00000", r"Starting kernel|\n", 5)
    wait_shell(180)


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


def vendor_shell():
    end = time.time() + 180
    while time.time() < end:
        if subprocess.run(["ping", "-c1", "-W1", "192.168.1.1"],
                          capture_output=True).returncode == 0:
            break
        time.sleep(1)
    else:
        sys.exit("vendor not reachable")
    for _ in range(10):
        try:
            s = socket.create_connection(("192.168.1.1", 26), timeout=8)
            break
        except OSError:
            time.sleep(3)
    s.settimeout(0.4)
    tel(s, "", 2)
    tel(s, "root", 2)
    return s


def kexec(dtb="inherit.dtb"):
    power_cycle()
    s = vendor_shell()
    serialctl.run(fd, until="XXXX", timeout=1)
    for rf, lf in (("Image", "Image"), (dtb, "sd5116.dtb"),
                   ("trampoline.bin", "trampoline.bin"), ("kexecldr", "kexecldr")):
        tel(s, f"tftp -g -l /tmp/{lf} -r {rf} 192.168.1.2", 8)
    tel(s, "chmod +x /tmp/kexecldr", 1)
    tel(s, "for p in 178 177 176 175 174 173 172 171 170 121 120 92 91 67 24; "
           "do kill -9 $p 2>/dev/null; done", 2)
    tel(s, "echo 3 > /proc/sys/vm/drop_caches", 2)
    tel(s, "/tmp/kexecldr", 3)
    s.close()
    time.sleep(15)


_ts = [None]


def tsh(cmd, timeout=30):
    """Shell over telnetd on the ONU (works when the console is dead)."""
    if _ts[0] is None:
        end = time.time() + 120
        while True:
            try:
                _ts[0] = socket.create_connection(("192.168.15.11", 23), timeout=5)
                break
            except OSError:
                if time.time() > end:
                    sys.exit("ONU telnet not reachable")
                time.sleep(2)
        _ts[0].settimeout(0.3)
        tel(_ts[0], "", 1)
    _n[0] += 1
    m = "__T%d" % _n[0]
    s = _ts[0]
    s.sendall(f"{cmd}; echo {m[:3]}\"\"{m[3:]}\n".encode())
    b = b""
    end = time.time() + timeout
    while time.time() < end and ("\n" + m).encode() not in b:
        try:
            b += s.recv(65536)
        except Exception:
            pass
    out = b.decode(errors="replace").replace("\r", "")
    out = out.split("\n", 1)[1] if "\n" in out else ""
    return out.rsplit("\n" + m, 1)[0]


def echos():
    l = [x.split() for x in open("/proc/net/snmp") if x.startswith("Icmp:")]
    return int(l[1][l[0].index("InEchos")])


def up():
    sh("ifconfig eth0 down")
    sh("ifconfig eth0 hw ether BC:98:89:09:5D:98")
    sh("ifconfig eth0 192.168.1.20 netmask 255.255.255.0 up", 60)
    time.sleep(4)
    sh(f"arp -s 192.168.1.2 {HOST_MAC}")


def nettest(n=6, shell=None):
    shell = shell or sh
    a = echos()
    r = shell(f"ping -c {n} -W 1 192.168.1.2 | tail -1", n * 2 + 10)
    tx = echos() - a
    print(f"TX: host got {tx}/{n} echoes | ONU: {r.strip()}")
    return tx, r


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sh"
    if mode == "cold":
        cold(*sys.argv[2:3])
        time.sleep(5)
        sh(f"arp -s 192.168.1.2 {HOST_MAC}")
        nettest()
    elif mode == "kexec":
        kexec(*sys.argv[2:3])
        nettest(shell=tsh)
    elif mode == "sh":
        print(sh(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 30))
    elif mode == "test":
        nettest()
    elif mode == "tsh":
        print(tsh(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 30))
