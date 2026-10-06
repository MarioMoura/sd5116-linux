#!/bin/bash
# Generate initramfs cpio archive for SD5116
set -e

WORK=/home/mario/onu/initramfs/rootfs
OUT=/home/mario/onu/initramfs/initramfs.cpio

rm -rf "$WORK"
mkdir -p "$WORK"/{bin,dev,proc,sys,etc,tmp}

# Copy our static init
cp /home/mario/onu/initramfs/init "$WORK/init"
chmod 755 "$WORK/init"

# Create /bin/sh as a symlink to init (won't work, but placeholder)
# The mini-shell in init handles commands via fork+execve
# We need basic commands - let's create tiny shell scripts or use /proc

# Create a minimal /etc/hostname
echo "sd5116" > "$WORK/etc/hostname"

# Create device nodes (mknod needs root, so use the cpio list method instead)
cd "$WORK"
(
    # Use gen_init_cpio format (or just use find|cpio)
    find . -print0 | cpio --null -o --format=newc
) > "$OUT"

echo "Created: $OUT ($(stat -c%s "$OUT") bytes)"
