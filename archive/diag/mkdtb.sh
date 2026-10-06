#!/bin/sh
# mkdtb.sh <out-name> "<extra sd5116_eth args>"  -> /srv/tftp/<out-name>
DTC=/home/mario/onu/linux/scripts/dtc/dtc
SRC=/home/mario/onu/linux/arch/arm/boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dtb
BASE="console=ttyS1,115200 earlycon=uart8250,mmio32,0x1010f000,115200n8"
$DTC -q -I dtb -O dts "$SRC" | sed "s|bootargs = \".*\";|bootargs = \"$BASE $2\";|" | $DTC -q -I dts -O dtb -o "/srv/tftp/$1"
$DTC -q -I dtb -O dts "/srv/tftp/$1" | grep bootargs
