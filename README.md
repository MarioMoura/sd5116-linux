# Linux on the HiSilicon SD5116 (FiberHome AN5506-01-A)

Mainline Linux on a FiberHome AN5506-01-A GPON ONU, turned into a small general purpose
machine. It runs a v7.3-rc6 kernel with new drivers for the SD5116 SoC, boots from its own
SPI flash with A/B updates and rollback, and serves DNS for the home network.

| | |
|---|---|
| SoC | HiSilicon SD5116, single Cortex-A9 |
| RAM | 32 MB (about 26 MB for Linux) |
| Flash | 16 MB SPI NOR (MX25L12833F) |
| Ethernet | 1 Gigabit LAN port behind the SoC's embedded switch |
| Console | UART1, 115200 8N1 |
| Bootloader | vendor U-Boot 2010.03, left untouched |

## Repository layout

| Path | Contents |
|---|---|
| `initramfs/` | the root filesystem built into the kernel: init, `sysupgrade`, `buttond`, `mtdname`, `/etc` files, and the Dockerfile that builds BusyBox and Dropbear |
| `tools/` | `mkflash.py` (flash images), the boot trampoline, `onu.py` and `serialctl.py` (test harness: serial, U-Boot, TFTP, smart plug) |
| `docs/` | write-ups: bring-up log (`BLOG.md`), SoC notes (`SPEC.md`), packet engine (`PIE_RE.md`), switch init (`switch-init.md`), recovery |
| `archive/` | one-off scripts and register dumps from the reverse engineering, kept for reference |
| `docs/dsa-design.md` | why and how the Ethernet became a DSA switch, init minimization |

Not in the repository: kernel trees, toolchain, build output, flash images and vendor
material (firmware dumps, decompiled vendor code).

## Kernel

Branch `sd5116` of [MarioMoura/linux](https://github.com/MarioMoura/linux/tree/sd5116),
on top of v7.3-rc6:

- platform series (10 commits): SoC and board bindings, SoC Kconfig, SPI flash controller
  and watchdog drivers, device tree, multi_v7_defconfig;
- Ethernet series (9 commits): MDIO driver, PIE DMA conduit driver, DSA tagger and switch
  driver, bindings, device tree nodes, multi_v7_defconfig;
- two `LOCAL:` commits not meant for upstream: console bootargs and `sd5116_defconfig`.

The LAN port is the `lan` interface; `eth0` is the internal conduit to the switch.

## Building

Needs `arm-none-eabi-gcc`, Docker and Python 3, the kernel branch cloned into the
repository, and a read-out of the original flash in `flash/orig/` (for the vendor U-Boot
and the board's MAC address):

```sh
git clone -b sd5116 https://github.com/MarioMoura/linux.git linux
tools/build.sh
```

`tools/build.sh` builds the userland binaries (Docker), the trampoline and the U-Boot
environment blobs, then the kernel with the initramfs built in, and finally the flash
images: `flash/out/slot-a.bin`, `slot-b.bin` and `sha256sums` for updates, and
`flash/sd5116-linux.bin`, a full 16 MB image for the CH341A. `LINUX=<path>` points it at
another kernel tree. The kernel config and `initramfs/initramfs_list` find the repository
through `${ONU_ROOT}`, which the script sets.

## Flash layout

| Offset | Size | Name | Contents |
|---|---|---|---|
| 0x000000 | 256 KB | `u-boot` | vendor U-Boot, read-only in Linux |
| 0x040000 | 256 KB | `u-boot-env` | U-Boot settings, primary and mirror copy |
| 0x080000 | 4 MB | `firmware-a` | slot A: trampoline at +0, device tree at +0x1000, kernel at +0x10000 |
| 0x480000 | 4 MB | `firmware-b` | slot B, same layout |
| 0x880000 | 7.5 MB | `data` | JFFS2 mounted at `/data`: host keys, settings, DNS config |

The vendor U-Boot cannot boot a device tree kernel, so it loads a small trampoline
(`tools/trampoline.S`) that turns off the caches and MMU, sets the ARM boot registers with
the device tree address and jumps to the kernel.

## Updating

Copy the images over SSH and run `sysupgrade` on them:

```sh
ssh dns mkdir -p /tmp/fw && scp -O flash/out/* dns:/tmp/fw/
ssh dns 'setsid sysupgrade /tmp/fw </dev/null >/tmp/sysupgrade.log 2>&1 &'
```

SSH makes sure the image comes from you. `sysupgrade` also accepts an `http://` URL, but
the checksums are fetched from the same server, so that only guards against corruption,
not against someone on the LAN serving their own image.

`sysupgrade` checks the image, writes the slot that is not running and reboots into it
once. The new slot is kept only if the LAN port comes up within 60 s; a crash, a hang
(hardware watchdog) or no link brings the previous slot back. Recovery when nothing boots:
[docs/recovery.md](docs/recovery.md).

## On the board

- Init: BusyBox `init` (`/etc/inittab`) runs `/etc/init.d/rcS` once, then `runsv` supervises
  the services and restarts them if they die. Firmware services live in `/etc/service`
  (syslogd, dhcp, ntpd, dropbear, buttond); services installed later go in `/data/service`,
  survive upgrades and are picked up within 5 s. `sv status|up|down|restart <name>` works on
  those, `sv status /etc/service/<name>` on the firmware ones. `reboot` stops the services
  and leaves `/data` read-only first.
- SSH: Dropbear, key login only. Baked-in key `initramfs/etc/ssh/authorized_keys`; more
  keys in `/data/ssh/authorized_keys`. The host key lives in `/data/dropbear/`. `scp` needs
  `-O` (no SFTP server). No telnet; the serial console is the fallback.
- Network: DHCP on `lan` (the router reserves 192.168.15.11), NTP from pool.ntp.org.
  Defaults in `/etc/network.conf`; copy it to `/data/network.conf` to change them
  (`proto=static`, `ipaddr`, `netmask`, `gateway`, `dns`, optional `fallback` address).
- DNS: dnsmasq is not in the firmware. Ansible (infra repository, `ansible/dns.yml`, host
  `dns`) builds it and installs it as the service `/data/service/dnsmasq` with its config.
  The board itself resolves through the DNS servers from DHCP or `network.conf`.
- Logs: `logread` (64 KB ring buffer).
- Buttons: Reset short press reboots, hold 5 s wipes `/data` (at the next boot); Light
  toggles the LEDs.
- Hardware watchdog: fed by BusyBox `watchdog`, resets the board if userspace hangs.

## Upstream

Before sending: `git rebase --signoff v7.3-rc6`, then `scripts/get_maintainer.pl` and
`git format-patch --cover-letter`, as two series: the first 10 commits (platform) and
the next 9 (Ethernet, for the netdev and devicetree lists).
