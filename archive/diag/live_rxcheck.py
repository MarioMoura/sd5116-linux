#!/usr/bin/env python3
"""Talk to the already-booted ONU over serial: bring eth0 up, ping the host
from the ONU, and read eth0 RX counters.  RX packets > 0 = RX works."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serialctl, termios

fd = serialctl.open_port()


def cmd(s, until, timeout):
    termios.tcflush(fd, termios.TCIFLUSH)
    for ch in s:
        os.write(fd, ch.encode())
        time.sleep(0.03)
    os.write(fd, b"\r")
    return serialctl.run(fd, until=until, timeout=timeout).decode(errors="replace")


# wake the console
print("wake:", cmd("", r"#", 3)[-40:].replace("\r", " "))
cmd("ifconfig eth0 down", r"#", 3)
cmd("ifconfig eth0 hw ether BC:98:89:09:5D:98", r"#", 3)
cmd("ifconfig eth0 192.168.1.20 netmask 255.255.255.0 up", r"#", 4)
time.sleep(3)
print("=== ONU -> host ping (match on summary) ===")
r = cmd("ping -c 4 192.168.1.2", r"packets received|packet loss", 18)
print(r.replace("\r", ""))
print("=== eth0 counters (RX packets>0 = RX delivered) ===")
r = cmd("ifconfig eth0", r"TX packets|TX bytes", 5)
print(r.replace("\r", ""))
