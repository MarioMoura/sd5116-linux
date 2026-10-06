#!/usr/bin/env python3
"""
Scan for the MDIO clock enable bit on SD5116.
Catches U-Boot autoboot automatically - just power cycle and run this script.
"""
import serial
import time
import re
import sys

PORT = "/dev/ttyUSB0"
BAUD = 115200
MDIO = 0x13C00000


def wait_for_uboot(ser, timeout=30):
    """Wait for U-Boot prompt, interrupt autoboot if needed."""
    print("Waiting for U-Boot (power cycle the ONU now)...")
    deadline = time.time() + timeout
    buf = b""
    while time.time() < deadline:
        if ser.in_waiting:
            data = ser.read(ser.in_waiting)
            buf += data
            text = buf.decode("ascii", errors="replace")
            # Interrupt autoboot
            if "autoboot" in text.lower() or "hit any key" in text.lower():
                ser.write(b"\r\r\r")
                print("Interrupted autoboot!")
                time.sleep(1)
                ser.reset_input_buffer()
                ser.write(b"\r")
                time.sleep(0.5)
                return True
            # Already at prompt
            if text.strip().endswith("#"):
                print("Already at U-Boot prompt!")
                return True
        time.sleep(0.05)
    print("Timeout waiting for U-Boot")
    return False


def cmd(ser, c, delay=0.5):
    """Send command, wait for prompt, return response."""
    ser.reset_input_buffer()
    ser.write(f"{c}\r".encode())
    time.sleep(delay)
    buf = b""
    # Wait until we see the # prompt
    deadline = time.time() + 3
    while time.time() < deadline:
        if ser.in_waiting:
            buf += ser.read(ser.in_waiting)
            if b"# " in buf:
                break
        time.sleep(0.05)
    return buf.decode("ascii", errors="replace")


def rd(ser, addr):
    """Read a 32-bit register."""
    r = cmd(ser, f"md {addr:#x} 1")
    m = re.search(r"([0-9a-fA-F]{7,8}):\s+([0-9a-fA-F]{8})", r)
    return int(m.group(2), 16) if m else None


def wr(ser, addr, val):
    """Write a 32-bit register."""
    cmd(ser, f"mw {addr:#x} {val:#x}", delay=0.3)


def mdio_alive(ser):
    """Check if MDIO controller responds to writes."""
    wr(ser, MDIO + 0x8, 0xABCD)
    v = rd(ser, MDIO + 0x8)
    wr(ser, MDIO + 0x8, 0)
    return v is not None and v == 0xABCD


def scan_range(ser, start, end, name):
    """Try every register in a range as a potential clock gate."""
    print(f"\nScanning {name} ({start:#010x}-{end:#010x})...")
    for addr in range(start, end, 4):
        orig = rd(ser, addr)
        if orig is None or orig == 0xFFFFFFFF:
            continue
        wr(ser, addr, 0xFFFFFFFF)
        if mdio_alive(ser):
            print(f"\n*** FOUND! {addr:#010x} = 0xFFFFFFFF enables MDIO ***")
            wr(ser, addr, orig)
            for bit in range(32):
                tv = orig | (1 << bit)
                if tv == orig:
                    continue
                wr(ser, addr, tv)
                if mdio_alive(ser):
                    print(f"    Bit {bit} (set {addr:#010x} = {tv:#010x})")
                    return addr, bit
                wr(ser, addr, orig)
            return addr, -1
        wr(ser, addr, orig)
        # Progress every 0x40 bytes
        if (addr - start) % 0x40 == 0 and addr != start:
            print(f"  ...{addr:#010x}", end="", flush=True)
    print(f"\n  Not found in {name}")
    return None, None


def main():
    ser = serial.Serial(PORT, BAUD, timeout=2)

    # Check if already at prompt
    ser.reset_input_buffer()
    ser.write(b"\r")
    time.sleep(0.5)
    resp = ser.read(4096).decode("ascii", errors="replace")

    if "# " not in resp:
        if not wait_for_uboot(ser):
            ser.close()
            return

    # Verify
    v = rd(ser, 0x10100000)
    if v is None:
        print("Cannot read registers!")
        ser.close()
        return
    print(f"Chip ID: {v:#010x}")

    if mdio_alive(ser):
        print("MDIO is already alive!")
        ser.close()
        return

    print("MDIO not responding. Searching for clock enable...")

    # Scan all known register blocks
    ranges = [
        (0x10100000, 0x10101000, "system controller"),
        (0x10101000, 0x10102000, "block 0x10101000"),
        (0x10110000, 0x10111000, "block 0x10110000"),
        (0x10200000, 0x10201000, "block 0x10200000"),
        (0x10300000, 0x10301000, "block 0x10300000"),
        (0x12000000, 0x12001000, "block 0x12000000"),
        (0x13000000, 0x13001000, "block 0x13000000"),
        (0x11000000, 0x11001000, "ethernet MAC base"),
    ]

    for start, end, name in ranges:
        addr, bit = scan_range(ser, start, end, name)
        if addr is not None:
            print(f"\n=== MDIO clock enable: register {addr:#010x} bit {bit} ===")
            ser.close()
            return

    print("\n=== MDIO clock enable NOT FOUND in any scanned range ===")
    print("May need to scan 0x14000000+ or vendor kernel has a complex init sequence.")
    ser.close()


if __name__ == "__main__":
    main()
