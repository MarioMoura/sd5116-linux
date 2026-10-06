#!/usr/bin/env python3
"""
Probe watchdog registers from the mini-shell before it reboots.
Boots the kernel via trampoline, then quickly reads candidate
watchdog addresses via devmem.
"""
import serial
import time

PORT = '/dev/ttyUSB0'
BAUD = 115200

def wait_for(ser, text, timeout=30):
    """Wait for text to appear in serial output."""
    buf = b''
    end = time.time() + timeout
    while time.time() < end:
        if ser.in_waiting:
            data = ser.read(ser.in_waiting)
            buf += data
            print(data.decode('latin-1'), end='', flush=True)
            if text.encode() in buf:
                return True
        else:
            time.sleep(0.05)
    return False

def send_cmd(ser, cmd, delay=0.3):
    """Send a command and wait briefly."""
    ser.write((cmd + '\n').encode())
    time.sleep(delay)
    # Read response
    if ser.in_waiting:
        data = ser.read(ser.in_waiting)
        print(data.decode('latin-1'), end='', flush=True)
        return data.decode('latin-1')
    return ''

def wait_for_uboot(ser):
    """Wait for U-Boot prompt by catching autoboot."""
    print("[*] Waiting for U-Boot...")
    if wait_for(ser, 'Hit any key', timeout=60):
        ser.write(b'\n')
        time.sleep(0.5)
        ser.read(ser.in_waiting)
        print("\n[*] Got U-Boot prompt")
        return True
    return False

ser = serial.Serial(PORT, BAUD, timeout=1)
time.sleep(0.5)
ser.reset_input_buffer()

print("[*] Waiting for U-Boot (power cycle the ONU)...")
if not wait_for_uboot(ser):
    print("[!] Timeout waiting for U-Boot")
    exit(1)

# Boot sequence
cmds = [
    'tftp 0x80008000 Image',
    'tftp 0x80F80000 sd5116.dtb',
    'tftp 0x80F00000 trampoline.bin',
]

for cmd in cmds:
    send_cmd(ser, cmd, delay=5)

# Boot
send_cmd(ser, 'go 0x80F00000', delay=0.5)

# Wait for mini-shell prompt
print("\n[*] Waiting for mini-shell...")
if not wait_for(ser, 'Built-in shell', timeout=30):
    print("[!] Shell not reached")
    exit(1)

time.sleep(0.5)

# Quickly probe watchdog candidate addresses
addrs = [
    # System controller area
    '0x10100000', '0x10100004', '0x10100008', '0x1010000C',
    # Timer/watchdog blocks
    '0x10104000', '0x10104004', '0x10104008', '0x1010400C',
    '0x10104010', '0x10104014', '0x10104018',
    '0x10105000', '0x10105004', '0x10105008',
    # Cortex-A9 private timer/watchdog area
    '0x10180600', '0x10180604', '0x10180608',
    '0x10180620', '0x10180624', '0x10180628', '0x1018062C',
    '0x10180630', '0x10180634',
]

print("\n[*] Probing watchdog addresses...")
for addr in addrs:
    send_cmd(ser, f'devmem {addr}', delay=0.3)

# Try to cat some sysfs too
send_cmd(ser, 'ls /sys/class', delay=0.3)
send_cmd(ser, 'ls /sys/bus/mdio_bus/devices', delay=0.3)

# Wait and collect any remaining output
print("\n[*] Waiting for more output...")
time.sleep(15)
if ser.in_waiting:
    print(ser.read(ser.in_waiting).decode('latin-1'))

ser.close()
print("\n[*] Done")
