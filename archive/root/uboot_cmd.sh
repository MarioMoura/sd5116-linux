#!/bin/bash
# Send a command to U-Boot over serial and capture the response
# Usage: ./uboot_cmd.sh "md 0x13c00000 8"

PORT="/dev/ttyUSB0"
BAUD=115200
TIMEOUT="${2:-2}"

# Configure serial port
stty -F "$PORT" "$BAUD" cs8 -cstopb -parenb raw -echo -hupcl 2>/dev/null

# Flush any pending input
timeout 0.2 cat "$PORT" > /dev/null 2>&1

# Send the command
printf "%s\r" "$1" > "$PORT"

# Capture response
timeout "$TIMEOUT" cat "$PORT" 2>/dev/null
