# SD5116 Linux 6.12 Port - TODO

## Status: Kernel boots to "No working init found" via TFTP

## Completed
- [x] DT_MACHINE_START for SD5116
- [x] Device tree (sd5116.dtsi + board .dts) with UARTs, GIC, SCU, timers
- [x] earlycon working (uart8250,mmio32,0x1010f000,115200n8)
- [x] DEBUG_LL removed, clean build process
- [x] Peripheral address discovery via U-Boot md probing
- [x] Vendor module reverse engineering (hi_spi.ko, hi_mdio.ko, hi_sysctl.ko, hi_bridge.ko)
- [x] Git branch `sd5116-port` with clean commit
- [x] Root shell on vendor kernel via telnet (root@192.168.1.1:26, no password)
- [x] Automated serial U-Boot command interface (pyserial)
- [x] Identified SFC V300 at 0x10A20000 (flash: MX25L128 16MB)
- [x] Discovered inner MDIO at 0x14600000 (the actual working MDIO)
- [x] Confirmed system controller clock regs are write-only (write-to-set)

## Peripheral Map (discovered)

| Physical Address    | Peripheral                          | Status       |
|---------------------|-------------------------------------|--------------|
| 0x10100000          | System controller (chip ID, clocks) | Confirmed    |
| 0x10100004          | Soft reset register                 | From sysctl  |
| 0x10100080          | Clock gate 1 (write-only, set bits) | From sysctl  |
| 0x10100084          | Clock gate aux (write-only)         | From sysctl  |
| 0x1010008C          | Reset release 1 (write-only)        | From sysctl  |
| 0x10100090          | Clock gate 2 (write-only, set bits) | From sysctl  |
| 0x10100094          | Reset release 2 (write-only)        | From sysctl  |
| 0x1010014C          | Config register (reads 0x1F)        | Vendor kernel|
| 0x10100150          | Final enable (write-only)           | From sysctl  |
| 0x10100800          | Hardware chip ID (reads 0x51161110) | Confirmed    |
| 0x10103000          | Pin mux controller                  | Identified   |
| 0x10104000          | Timer/watchdog (SoC-specific)       | Identified   |
| 0x10106008          | Peripheral config register          | From sysctl  |
| 0x1010E000          | UART0 (snps,dw-apb-uart)           | In DT        |
| 0x1010F000          | UART1 - console (snps,dw-apb-uart) | In DT        |
| 0x10110000          | GPIO or pin config                  | Identified   |
| 0x10180000          | ARM Cortex-A9 private peripherals   | In DT        |
| 0x10180200          | ARM global timer                    | In DT        |
| 0x10180600          | ARM TWD timer                       | In DT        |
| 0x10A20000-0x10A200FF | SPI Flash Controller regs (SFC V300) | Confirmed |
| 0x11000000          | Ethernet MAC (4 ports, 0x40000)     | Mapped       |
| 0x13000170          | Mode config register (write-only)   | From sysctl  |
| 0x13C00000          | MDIO controller (UNUSED/dead)       | Dead         |
| 0x14100000          | Unknown peripheral (0x89ABCDEF)     | Probed       |
| 0x14200000          | Unknown peripheral (0x00000001)     | Probed       |
| 0x14300000          | Unknown peripheral (0x00000180)     | Probed       |
| 0x14400000          | Unknown peripheral (0x89ABCDEF)     | Probed       |
| 0x14600000          | **Inner MDIO** (ethernet PHY bus)   | **CONFIRMED**|
| 0x14880000          | SPI Flash Controller (alt access)   | Confirmed    |
| 0x14900000          | SPI Flash Controller (CS config)    | Confirmed    |
| 0x1C000000-0x1FFFFFFF | SPI flash memory window           | Confirmed    |

## System Controller Clock Registers (WRITE-ONLY)

These registers use write-to-set semantics: writing a bit enables a clock/release,
but reading always returns 0. This is why the automated scanner failed.

From hi_sysctl.ko `hi_kernel_sysctl_attr_set` reverse engineering (chip_id 1,2,3 path):

| Register    | Known Bits    | Purpose                              |
|-------------|---------------|---------------------------------------|
| 0x10100080  | 4, 16, 20, 21 | Clock enables for various peripherals |
| 0x10100084  | unknown       | Auxiliary clock gate                  |
| 0x1010008C  | unknown       | Reset release 1                       |
| 0x10100090  | 5, 22         | Clock enables for various peripherals |
| 0x10100094  | unknown       | Reset release 2                       |
| 0x10100150  | 0             | Final peripheral enable               |
| 0x10A20100  | 1             | SFC bus enable                        |
| 0x13000170  | 0-3           | Peripheral mode configuration         |

## MDIO Controller Details

### Inner MDIO at 0x14600000 (the one that works)
- Used by hi_bridge.ko for ethernet PHY management
- Virtual address in vendor kernel: 0xFC600000
- Register layout (from hi_bridge.ko disassembly):
  - +0x00: Mode/control register (init clears bits 0-2)
  - +0x04: Command register
    - bits[6:2]: PHY address (0-31)
    - bits[11:7]: Register address (0-31)
    - bits[27:12]: Write data (16-bit, for writes only)
    - bit 0: Start operation
    - bit 1: Write flag (0=read, 1=write)
  - +0x0C: Read data (16-bit result)
  - +0x10: Status register
    - bits[2:0]: Busy flags (non-zero = busy)
    - bit 3: Completion flag 1
    - bit 4: Completion flag 2
- Vendor kernel state: cmd=0x1E84 (last read: PHY1 reg29), data=0x0040, status=0x10

### Old MDIO at 0x13C00000 (NOT USED)
- Referenced by hi_mdio.ko but reads as all zeros even with vendor kernel running
- Writes via devmem have no effect
- This MDIO controller is likely for GPON PHY management (not ethernet)

## Vendor Kernel Info

### Access
- Telnet: root@192.168.1.1 port 26, no password (BusyBox shell)
- CLI: admin/1234 on serial console (restricted vendor shell, not useful)
- U-Boot: interrupt autoboot within 2 seconds, no password

### Kernel details
- Chip ID: 0x51161110 (from 0x10100800)
- RAM: 32MB at 0x80000000-0x81FFFFFF
- APB bus clock: 100MHz
- Flash: MX25L128 16MB SPI NOR (JEDEC 0xC2 0x20 0x18)
- SFC: HiSilicon SPI Flash Controller V300 at 0x10A20000
- Network: eth0 (BC:98:89:09:5D:98), pie0, wan0, tel0, br0
- Module load order: hi_sysctl -> hi_mdio -> hi_pie -> hi_gpio -> hi_i2c -> hi_spi -> hi_timer -> hi_bridge

### MTD partitions (16MB flash)
```
0x000000-0x040000 : "boot"       (256K)
0x040000-0x080000 : "env"        (256K)
0x080000-0x200000 : "kernel"     (1.5M)
0x200000-0x7C0000 : "rootfs_app" (5.75M)
0x7C0000-0x940000 : "kernel"     (1.5M) - backup
0x940000-0xF00000 : "rootfs_app" (5.75M) - backup
0xF00000-0x1000000: "cfg"        (1M)
```

## Next Steps

### 1. Enable MDIO clock from U-Boot (IN PROGRESS)
- [ ] Run enable_mdio_clock.py to test clock enable sequences
- [ ] Find which specific bits enable the inner MDIO at 0x14600000
- [ ] Document the exact clock enable sequence
- Script: /home/mario/onu/enable_mdio_clock.py

### 2. Write MDIO driver
- [ ] Custom driver for inner MDIO at 0x14600000
- [ ] Register layout is unique (not compatible with mainline hns_mdio.c)
- [ ] Need clock enable figured out first (step 1)
- [ ] Device tree binding and compatible string

### 3. Ethernet MAC driver
- [ ] Ethernet MAC at 0x11000000, 4 ports at 0x40000 spacing
- [ ] Vendor driver is hi_pie.ko + hi_bridge.ko (custom, complex)
- [ ] DMA/buffer descriptor addresses still unknown
- [ ] May be able to use fixed-link in DT to skip MDIO initially

### 4. SPI Flash Controller driver
- [ ] SFC V300 at 0x10A20000 - mainline has hifmc100 driver, may be compatible
- [ ] Memory window at 0x1C000000
- [ ] Flash: MX25L128 16MB

### 5. Build initramfs with busybox
- [ ] Cross-compile busybox for ARM
- [ ] Create minimal initramfs (init script, /dev, /proc, /sys)
- [ ] Bundle into kernel image via CONFIG_INITRAMFS_SOURCE
- [ ] Needed when there's a driver to test

### 6. Find GPL/SDK source code
- [ ] Search for SD5116/SD5115 GPL source releases
- [ ] Search for FiberHome AN5506 GPL source
- [ ] Check OpenWrt for HiSilicon GPON support

### 7. Additional peripherals (lower priority)
- [ ] GPIO driver (0x10110000)
- [ ] Pin mux driver (0x10103000)
- [ ] System controller / clock driver (0x10100000)
- [ ] I2C (vendor has hi_i2c.ko)
- [ ] SCI - smart card interface (vendor has hi_sci.ko)
- [ ] GPON MAC (vendor has hi_gpon.ko, hi_epon.ko)

## Build

```bash
export ARCH=arm
export CROSS_COMPILE=arm-none-eabi-
export KBUILD_OUTPUT=/home/mario/onu/build

# Kernel
make sd5116_defconfig
make -j$(nproc)

# DTB
make hisilicon/sd5116-fiberhome-an5506-01a.dtb

# Copy to TFTP
cp $KBUILD_OUTPUT/arch/arm/boot/zImage /home/mario/onu/tftp/
cp $KBUILD_OUTPUT/arch/arm/boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dtb /home/mario/onu/tftp/sd5116.dtb
```

## U-Boot boot commands

```
setenv serverip 192.168.1.100
setenv ipaddr 192.168.1.1
tftp 0x80300000 zImage
tftp 0x80700000 sd5116.dtb
bootz 0x80300000 - 0x80700000
```

## Key files

- arch/arm/boot/dts/hisilicon/sd5116.dtsi - SoC device tree
- arch/arm/boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dts - board device tree
- arch/arm/mach-hisi/hisilicon.c - machine descriptor
- arch/arm/configs/sd5116_defconfig - kernel config

## Tools

- Serial: /dev/ttyUSB0 at 115200 baud (needs `sudo chmod 666` after USB replug)
- Python: `py` alias -> ~/.local/python/bin/python (pyserial installed)
- Cross compiler: arm-none-eabi-
- Disassembler: arm-none-eabi-objdump
- Scripts:
  - /home/mario/onu/scan_mdio_clock.py - clock bit scanner (historical, wrong MDIO address)
  - /home/mario/onu/enable_mdio_clock.py - focused clock enable for inner MDIO at 0x14600000
