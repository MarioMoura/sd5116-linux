#!/usr/bin/env python3
"""
Scan all MDIO PHY addresses and dump registers on SD5116.
Inner MDIO at 0x14600000 is already alive in U-Boot.
"""
import serial
import time
import re

PORT = "/dev/ttyUSB0"
BAUD = 115200
MDIO = 0x14600000

# Standard MII register names
REG_NAMES = {
    0: "BMCR (Basic Mode Control)",
    1: "BMSR (Basic Mode Status)",
    2: "PHYID1 (PHY ID High)",
    3: "PHYID2 (PHY ID Low)",
    4: "ANAR (Auto-Neg Advertisement)",
    5: "ANLPAR (Auto-Neg Link Partner)",
    6: "ANER (Auto-Neg Expansion)",
    7: "ANNPTR (Next Page Transmit)",
    8: "ANNPRR (Next Page Receive)",
    15: "Extended Status",
}


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


def cmd(ser, c, delay=0.3):
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
    r = cmd(ser, f"md {addr:#x} 1")
    m = re.search(r"([0-9a-fA-F]{7,8}):\s+([0-9a-fA-F]{8})", r)
    return int(m.group(2), 16) if m else None


def wr(ser, addr, val):
    cmd(ser, f"mw {addr:#x} {val:#x}", delay=0.2)


def mdio_read(ser, phy, reg):
    """Perform an MDIO read: phy_addr in bits[6:2], reg_addr in bits[11:7], bit 0=start."""
    command = ((reg & 0x1F) << 7) | ((phy & 0x1F) << 2) | 0x01
    wr(ser, MDIO + 0x4, command)
    # Wait for completion
    for _ in range(50):
        status = rd(ser, MDIO + 0x10)
        if status is not None and (status & 0x7) == 0:
            break
        time.sleep(0.01)
    data = rd(ser, MDIO + 0xC)
    return data & 0xFFFF if data is not None else None


def main():
    ser = serial.Serial(PORT, BAUD, timeout=2)
    ser.reset_input_buffer()
    ser.write(b"\r")
    time.sleep(0.5)
    resp = ser.read(4096).decode("ascii", errors="replace")
    if "# " not in resp:
        if not wait_for_uboot(ser):
            ser.close()
            return

    print("=== Scanning MDIO bus for PHYs (addresses 0-31) ===\n")

    found_phys = []
    for phy in range(32):
        # Read PHY ID registers (2 and 3)
        id1 = mdio_read(ser, phy, 2)
        id2 = mdio_read(ser, phy, 3)
        if id1 is None or id2 is None:
            continue
        if id1 == 0xFFFF and id2 == 0xFFFF:
            continue  # No PHY at this address
        if id1 == 0x0000 and id2 == 0x0000:
            continue  # No PHY at this address
        oui = (id1 << 6) | ((id2 >> 10) & 0x3F)
        model = (id2 >> 4) & 0x3F
        rev = id2 & 0xF
        print(f"PHY {phy}: ID={id1:04x}:{id2:04x}  OUI=0x{oui:06x} Model=0x{model:02x} Rev={rev}")
        found_phys.append(phy)

    if not found_phys:
        print("No PHYs found! Checking if MDIO is responsive...")
        # Try reading reg 0 from PHY 0 and 1
        for phy in [0, 1]:
            val = mdio_read(ser, phy, 0)
            print(f"  PHY {phy} reg 0 = {val:#06x}" if val is not None else f"  PHY {phy} reg 0 = FAILED")
        ser.close()
        return

    # Dump all standard registers for found PHYs
    for phy in found_phys:
        print(f"\n=== PHY {phy} Full Register Dump ===")
        for reg in range(32):
            val = mdio_read(ser, phy, reg)
            if val is None:
                continue
            name = REG_NAMES.get(reg, "")
            extra = ""
            if reg == 0:
                extra = decode_bmcr(val)
            elif reg == 1:
                extra = decode_bmsr(val)
            elif reg == 2:
                extra = f"PHY ID high"
            elif reg == 3:
                extra = f"PHY ID low"
            line = f"  Reg {reg:2d}: 0x{val:04x}"
            if name:
                line += f"  {name}"
            if extra:
                line += f"  [{extra}]"
            print(line)

    ser.close()


def decode_bmcr(val):
    parts = []
    if val & 0x8000: parts.append("Reset")
    if val & 0x4000: parts.append("Loopback")
    if val & 0x2000: parts.append("100Mbps")
    else: parts.append("10Mbps")
    if val & 0x1000: parts.append("AutoNeg-En")
    if val & 0x0800: parts.append("PowerDown")
    if val & 0x0200: parts.append("RestartAN")
    if val & 0x0100: parts.append("Full-Duplex")
    else: parts.append("Half-Duplex")
    return ", ".join(parts)


def decode_bmsr(val):
    parts = []
    if val & 0x0004: parts.append("Link-Up")
    else: parts.append("Link-Down")
    if val & 0x0008: parts.append("AutoNeg-Able")
    if val & 0x0020: parts.append("AutoNeg-Complete")
    if val & 0x0800: parts.append("10T-HD")
    if val & 0x1000: parts.append("10T-FD")
    if val & 0x2000: parts.append("100TX-HD")
    if val & 0x4000: parts.append("100TX-FD")
    return ", ".join(parts)


if __name__ == "__main__":
    main()
