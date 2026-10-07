#!/bin/sh
# Build everything for a release: userland binaries, trampoline, U-Boot env blobs,
# the kernel with the initramfs built in, and the flash images in flash/out.
#
#   tools/build.sh                         kernel tree in <repo>/linux-sd5116
#   LINUX=/path/to/kernel tools/build.sh
set -e

ONU_ROOT=$(cd "$(dirname "$0")/.." && pwd)
LINUX=${LINUX:-$ONU_ROOT/linux-sd5116}
CROSS_COMPILE=${CROSS_COMPILE:-arm-none-eabi-}
# the kernel config and initramfs_list refer to files as ${ONU_ROOT}/...
export ONU_ROOT LINUX

cd "$ONU_ROOT"
docker build -q --output type=local,dest=initramfs/out initramfs >/dev/null
cp initramfs/out/busybox initramfs/out/dropbearmulti initramfs/out/dnsmasq initramfs/
make -s -C tools CROSS_COMPILE="$CROSS_COMPILE"
python3 tools/mkflash.py env

make -s -C "$LINUX" ARCH=arm CROSS_COMPILE="$CROSS_COMPILE" sd5116_defconfig
make -s -C "$LINUX" ARCH=arm CROSS_COMPILE="$CROSS_COMPILE" -j"$(nproc)" \
	zImage hisilicon/sd5116-fiberhome-an5506-01a.dtb
python3 tools/mkflash.py image
