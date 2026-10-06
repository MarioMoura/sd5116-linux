#!/usr/bin/env python3
"""Serial console driver for the ONU (115200 8N1 on /dev/ttyUSB0).

Usage:
  serialctl.py cmd "printenv" [--timeout 3]     send a command, print response
  serialctl.py watch --until REGEX [--timeout 60] [--spam-cr]
                                                capture until pattern seen
  serialctl.py raw "text"                       send text without trailing CR

All traffic is appended to ~/onu/serial.log.
"""
import glob, os, re, sys, time, select, termios, argparse

LOG = os.path.expanduser("~/onu/serial.log")


def open_port():
    ports = sorted(glob.glob("/dev/ttyUSB*"))
    if not ports:
        sys.exit("no /dev/ttyUSB* device")
    fd = os.open(ports[0], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0                      # iflag: raw
    attrs[1] = 0                      # oflag: raw
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
    attrs[3] = 0                      # lflag: raw, no echo
    attrs[4] = termios.B115200
    attrs[5] = termios.B115200
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return fd


def run(fd, send=None, until=None, timeout=3.0, spam_cr=False, quiet_end=0.5):
    log = open(LOG, "ab", buffering=0)
    buf = b""
    if send is not None:
        os.write(fd, send.encode())
    deadline = time.time() + timeout
    last_rx = time.time()
    last_spam = 0.0
    pat = re.compile(until.encode()) if until else None
    while time.time() < deadline:
        if spam_cr and time.time() - last_spam > 0.2:
            try:
                os.write(fd, b"\r")
            except OSError:
                pass
            last_spam = time.time()
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                data = os.read(fd, 4096)
            except OSError:
                data = b""
            if data:
                buf += data
                log.write(data)
                last_rx = time.time()
                if pat and pat.search(buf):
                    break
        elif send is not None and not pat and time.time() - last_rx > quiet_end and buf:
            break                     # response finished (quiet after output)
    log.close()
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["cmd", "watch", "raw"])
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--timeout", type=float, default=3.0)
    ap.add_argument("--until", default=None)
    ap.add_argument("--spam-cr", action="store_true")
    a = ap.parse_args()

    fd = open_port()
    if a.mode == "cmd":
        termios.tcflush(fd, termios.TCIFLUSH)
        out = run(fd, send=a.text + "\r", until=a.until, timeout=a.timeout)
    elif a.mode == "raw":
        out = run(fd, send=a.text, until=a.until, timeout=a.timeout)
    else:
        out = run(fd, until=a.until, timeout=a.timeout, spam_cr=a.spam_cr)
    sys.stdout.write(out.decode(errors="replace"))
    if a.until and not re.search(a.until.encode(), out):
        sys.exit(2)


if __name__ == "__main__":
    main()
