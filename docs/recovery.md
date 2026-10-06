# Recovery

Normal updates go over the network (`sysupgrade`, see the README) and fall back to the
previous slot on their own. These steps are for when the board does not boot at all.

## U-Boot over serial (no flash write)

Serial console: UART1, 115200 8N1. Press a key at "Hit any key" to get the U-Boot prompt,
then boot a kernel from TFTP (server 192.168.1.2, files from the kernel build and
`tools/trampoline.bin`):

```
setenv serverip 192.168.1.2
setenv ipaddr 192.168.1.10
tftpboot 0x80008000 Image
tftpboot 0x80F80000 sd5116.dtb
tftpboot 0x80F00000 trampoline.bin
go 0x80F00000
```

`tools/onu.py cold <dtb>` does this automatically. From the booted system,
`sysupgrade -s a|b <url>` rewrites a slot and makes it the default.

## CH341A programmer and SOIC8 clip

Chip: MX25L12833F (16 MB SPI NOR). Connect the CH341A to the 25XX header, clip onto the
chip (pin 1 at the dot), board powered off.

```sh
sudo flashrom -p ch341a_spi                                     # detect
sudo flashrom -p ch341a_spi -c "MX25L12833F" -r backup1.bin     # read twice and compare
sudo flashrom -p ch341a_spi -c "MX25L12833F" -r backup2.bin
cmp backup1.bin backup2.bin
sudo flashrom -p ch341a_spi -c "MX25L12833F" -w flash/sd5116-linux.bin   # write full image
```

`flash/sd5116-linux.bin` (from `tools/mkflash.py image`) holds the vendor U-Boot, the U-Boot
environment and both slots, with an empty `/data`. Keep your own read-out of the original
vendor flash to go back to the stock firmware; it is not part of this repository.

If the CH341A shows up as 1a86:5523 (serial mode) instead of 5512 (SPI mode), run
`sudo rmmod ch341`, replug it and check `lsusb | grep 1a86`.
