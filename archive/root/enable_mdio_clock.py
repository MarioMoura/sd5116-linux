#!/usr/bin/env python3
"""
Try to enable the inner MDIO clock on SD5116 from U-Boot.

Based on reverse engineering of hi_sysctl.ko's hi_kernel_sysctl_attr_set function.
The system controller registers at 0x10100080/0x10100090 etc. are WRITE-ONLY
(write-to-set semantics), which is why the previous scanner failed.

The inner MDIO (used by hi_bridge for ethernet) is at physical 0x14600000,
NOT at 0x13C00000 (which is a separate, unused MDIO).
"""
import serial
import time
import re
import sys

PORT = "/dev/ttyUSB0"
BAUD = 115200
INNER_MDIO = 0x14600000


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
            if "autoboot" in text.lower() or "hit any key" in text.lower():
                ser.write(b"\r\r\r")
                print("Interrupted autoboot!")
                time.sleep(1)
                ser.reset_input_buffer()
                ser.write(b"\r")
                time.sleep(0.5)
                return True
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
    """Check if inner MDIO controller at 0x14600000 has non-zero registers."""
    # Read command register - should be non-zero if controller is clocked
    v4 = rd(ser, INNER_MDIO + 0x4)
    v10 = rd(ser, INNER_MDIO + 0x10)
    # Also try writing to command register and reading back
    wr(ser, INNER_MDIO + 0x4, 0x00000005)  # PHY0 reg0 read command
    time.sleep(0.1)
    v4_after = rd(ser, INNER_MDIO + 0x4)
    v10_after = rd(ser, INNER_MDIO + 0x10)
    vc = rd(ser, INNER_MDIO + 0xC)

    print(f"  MDIO+0x04={v4:#010x} -> {v4_after:#010x}")
    print(f"  MDIO+0x10={v10:#010x} -> {v10_after:#010x}")
    print(f"  MDIO+0x0C={vc:#010x}")

    # If any register is non-zero, the controller is alive
    return any(v != 0 and v is not None for v in [v4, v10, v4_after, v10_after, vc])


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

    # Verify we're in U-Boot
    v = rd(ser, 0x10100000)
    if v is None:
        print("Cannot read registers!")
        ser.close()
        return
    print(f"Chip ID reg: {v:#010x}")

    # Check MDIO state before any writes
    print("\n=== MDIO state BEFORE clock enable ===")
    alive_before = mdio_alive(ser)
    if alive_before:
        print("Inner MDIO is already alive! No clock enable needed.")
        ser.close()
        return
    print("Inner MDIO is dead (all zeros). Trying clock enables...")

    # Known clock enable registers from hi_sysctl.ko reverse engineering.
    # These are WRITE-ONLY (write-to-set) registers.
    # We try them in groups and test after each group.

    # Group 1: Main clock gate registers
    clock_enables = [
        # (address, value_to_OR, description)
        (0x10100080, 0x00310010, "sysctl clock gate 1 (bits 4,16,20,21)"),
        (0x10100090, 0x00400020, "sysctl clock gate 2 (bits 5,22)"),
    ]

    print("\n=== Trying Group 1: Main clock gates ===")
    for addr, val, desc in clock_enables:
        print(f"  Writing {val:#010x} to {addr:#010x} ({desc})")
        wr(ser, addr, val)

    alive = mdio_alive(ser)
    if alive:
        print("\n*** MDIO ALIVE after Group 1! ***")
        ser.close()
        return

    # Group 2: Additional enables
    print("\n=== Trying Group 2: Additional enables ===")
    extra_enables = [
        (0x10100084, 0xFFFFFFFF, "sysctl aux clock gate"),
        (0x1010008C, 0xFFFFFFFF, "sysctl reset release 1"),
        (0x10100094, 0xFFFFFFFF, "sysctl reset release 2"),
        (0x10100150, 0x00000001, "sysctl final enable bit 0"),
        (0x1010014C, 0x0000001F, "sysctl config (was 0x1F on vendor)"),
    ]
    for addr, val, desc in extra_enables:
        print(f"  Writing {val:#010x} to {addr:#010x} ({desc})")
        wr(ser, addr, val)

    alive = mdio_alive(ser)
    if alive:
        print("\n*** MDIO ALIVE after Group 2! ***")
        ser.close()
        return

    # Group 3: SFC-related and peripheral bus enables
    print("\n=== Trying Group 3: Peripheral bus enables ===")
    periph_enables = [
        (0x10A20100, 0x00000002, "SFC area bit 1"),
        (0x13000170, 0x0000000F, "mode config register"),
        (0x10100038, 0xFFFFFFFF, "sysctl reg 0x38"),
        (0x1010007C, 0xFFFFFFFF, "sysctl reg 0x7C"),
    ]
    for addr, val, desc in periph_enables:
        print(f"  Writing {val:#010x} to {addr:#010x} ({desc})")
        wr(ser, addr, val)

    alive = mdio_alive(ser)
    if alive:
        print("\n*** MDIO ALIVE after Group 3! ***")
        ser.close()
        return

    # Group 4: Try the bridge/PIE area clocks
    print("\n=== Trying Group 4: Bridge/PIE enables ===")
    bridge_enables = [
        (0x10100080, 0xFFFFFFFF, "all bits in clock gate 1"),
        (0x10100090, 0xFFFFFFFF, "all bits in clock gate 2"),
        (0x10100084, 0xFFFFFFFF, "all bits in aux clock"),
        (0x1010008C, 0xFFFFFFFF, "all bits in reset 1"),
        (0x10100094, 0xFFFFFFFF, "all bits in reset 2"),
        (0x10100150, 0xFFFFFFFF, "all bits in final enable"),
    ]
    for addr, val, desc in bridge_enables:
        print(f"  Writing {val:#010x} to {addr:#010x} ({desc})")
        wr(ser, addr, val)

    alive = mdio_alive(ser)
    if alive:
        print("\n*** MDIO ALIVE after Group 4! ***")
    else:
        print("\n=== MDIO still dead after all attempts ===")
        print("The clock enable may require a more complex sequence,")
        print("or the inner MDIO may be gated by a different mechanism.")

        # Last resort: check if 0x14600000 area is even mapped
        print("\n=== Checking if 0x14600000 area is accessible ===")
        for offset in range(0, 0x20, 4):
            v = rd(ser, INNER_MDIO + offset)
            print(f"  {INNER_MDIO + offset:#010x} = {v:#010x}" if v is not None else f"  {INNER_MDIO + offset:#010x} = READ FAILED")

    ser.close()


if __name__ == "__main__":
    main()
