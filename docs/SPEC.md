# SD5116 Reverse Engineering Spec

## Goal

Port a modern Linux kernel (6.x LTS) to the HiSilicon SD5116 SoC, document the process, and contribute the platform support upstream.

## SoC Identification

| Field | Value |
|-------|-------|
| Chip ID register | `0x51161000` |
| Marketing name | HiSilicon SD5116 |
| CPU | ARM Cortex-A9 (single core) |
| CPU ID | `0x414fc091` (implementer 0x41 = ARM, part 0xc09 = Cortex-A9, rev 1) |
| Architecture | ARMv7 |
| Clock | ~330MHz (665 BogoMIPS) |
| APB bus clock | 100MHz |
| Endianness | Little-endian |

## Memory

| Region | Address | Size |
|--------|---------|------|
| System RAM | `0x80000000 - 0x81ffffff` | 32MB |
| Kernel text | `0x80020000 - 0x80201fff` | ~1.9MB |
| Kernel data | `0x80210000 - 0x802602bf` | ~320KB |
| Kernel load address | `0x81008000` | |
| Kernel entry point | `0x81008000` | |

## Interrupt Controller

- **Type**: GIC (ARM Generic Interrupt Controller)
- **Cortex-A9 peripheral base**: `0x10180000`
- **GIC CPU interface**: `0x10180100` (periph_base + 0x100)
- **GIC distributor**: `0x10181000` (periph_base + 0x1000)
- **NR_IRQS**: 160
- Already supported in mainline Linux

### How the GIC addresses were found

The GIC base addresses are not exposed via `/proc/iomem` or any sysfs interface. They were reverse-engineered from the running kernel:

1. `/proc/kallsyms` revealed `sd5116_gic_init_irq` at virtual address `0xc000ec2c`
2. The function resides in the `.init` section (freed after boot), but the physical RAM (`0x8000ec2c`) still contained the code
3. The first instruction (`ldr r3, [pc, #0x228]` = `0xE59F3228`) loads a constant from the literal pool at `0xc000ee5c`
4. Reading the literal pool from RAM revealed ioremap'd virtual addresses:
   - `0xF810F000` = UART1 (confirmed: phys `0x1010F000` from dmesg)
   - `0xF8180100` = GIC CPU interface
   - `0xF8181000` = GIC distributor
5. The ioremap mapping is: `virt 0xF8xxxxxx = phys 0x10xxxxxx`
6. Therefore: GIC CPU interface = `0x10180100`, GIC distributor = `0x10181000`

The address `0x10180000` was confirmed via U-Boot `md` to contain the Cortex-A9 SCU (Snoop Control Unit) register, consistent with the standard Cortex-A9 MPCore private peripheral layout.

## Peripheral Address Map (confirmed)

| Peripheral | Physical Address | Size | IRQ | Source |
|------------|-----------------|------|-----|--------|
| SPI flash ctrl (regs) | `0x00000000` | 256B | -- | /proc/iomem |
| SP810 System Controller | `0x10100000` | 4KB | -- | U-Boot probe + PrimeCell ID (part 0x810, ARM) |
| Unknown peripheral | `0x10110000` | 4KB | -- | U-Boot probe (value 0x00000007, no PrimeCell ID) |
| UART0 (ttyAMA0) | `0x1010e000` | 4KB | 77 | dmesg |
| UART1 (ttyAMA1, console) | `0x1010f000` | 4KB | 78 | dmesg |
| Cortex-A9 SCU | `0x10180000` | 256B | -- | U-Boot probe (value 0x00000001) |
| GIC CPU interface | `0x10180100` | 256B | -- | RE of sd5116_gic_init_irq |
| GIC distributor | `0x10181000` | 4KB | -- | RE of sd5116_gic_init_irq |
| Chip ID / system ctrl | `0x10300000` | 4KB | -- | U-Boot probe (value 0x00010003), dmesg `id:0x51161000` |
| SPI flash ctrl (ext) | `0x10a20000` | 256B | -- | /proc/iomem |
| SPI flash mapping | `0x1c000000` | 64MB | -- | /proc/iomem |

### Unmapped regions (confirmed via U-Boot)

These addresses return `0xffffffff` or cause a hang:

- `0x10120000 - 0x10170000`: all `0xffffffff`
- `0x10190000 - 0x101a0000`: all `0xffffffff`
- `0x10140000 - 0x10150000`: all `0xffffffff`
- `0x10301000`, `0x10302000`: cause U-Boot hang (bus error)

### Address mirroring

The `0x10200000 - 0x10250000` range mirrors the `0x10300000` region in an alternating pattern every `0x20000`, suggesting incomplete address decoding:

```
0x10200000: 00100003 (variant of 0x10300000 value)
0x10210000: 35137041 (same as 0x10310000)
0x10220000: 00010003 00011003 00018003 (same as 0x10300000)
0x10230000: 35137041 (same as 0x10310000)
0x10240000: 00010003 00011003 00018003 (same as 0x10300000)
0x10250000: 35137041 (same as 0x10310000)
```

## Virtual Address Mapping (vendor kernel)

| Virtual range | Physical range | Purpose |
|---------------|---------------|---------|
| `0xC0000000 - 0xC1FFFFFF` | `0x80000000 - 0x81FFFFFF` | Kernel lowmem (RAM, 32MB) |
| `0xF8000000+` | `0x10000000+` | ioremap'd peripherals |

## Timer

| Instance | IRQ | Notes |
|----------|-----|-------|
| System Timer + Free Timer | 79 | `SD5116 System Timer Tick, Free Timer` |
| timer2_3 | 80 | Secondary timer |

- Clocksource: `timer1` (custom HiSilicon timer, not ARM architected timer)
- Timer resolution: 10ms (10000000 nsecs)
- Likely SP804-compatible dual timer (common in HiSilicon SoCs)
- Mainline driver: `drivers/clocksource/timer-sp.c`
- **Base address: unknown** (needs probing -- likely near `0x10100000` area based on hi3519 layout, or could be the Cortex-A9 private timer at `0x10180600`)

## Watchdog

- `hi_wdt` -- HiSilicon watchdog, auto-mode
- Likely SP805-compatible
- Mainline: `drivers/watchdog/sp805_wdt.c`
- **Base address: unknown**

## SPI Flash Controller

| Register | Address |
|----------|---------|
| Control registers | `0x00000000 - 0x000000ff` |
| Extended registers | `0x10a20000 - 0x10a200ff` |
| Flash mapping window | `0x1c000000 - 0x1fffffff` (64MB) |

Driver: `Spi Flash Controller V300` (HiSilicon proprietary). Chip: MX25L12833F (Macronix 16MB SPI NOR, SOIC-8).

The mainline `hisi-sfc` driver (`drivers/mtd/spi-nor/controllers/hisi-sfc.c`) supports HiSilicon SPI flash controllers. Needs testing.

## Ethernet / Network

| Interface | MAC | Type |
|-----------|-----|------|
| eth0 | `bc:98:89:09:5d:98` | LAN port (virtual device) |
| pie0 | -- | PON interface (fiber) |
| wan0 | `bc:98:89:09:5d:9a` | WAN virtual interface |
| br0 | -- | Bridge (eth0) |
| tel0 | -- | Telephony/VoIP |

All network devices are `../../devices/virtual/net/` -- the MAC/switching is handled by the `hi_pie` kernel module (Packet Interface Engine), not a standard NIC driver. IRQ 119 for PIE.

This is the **hardest peripheral to support**. The ethernet path goes through:
```
eth0 -> hi_kvif (virtual interface) -> hi_pie (packet engine) -> hi_bridge (L2) -> hardware
```

Related IRQs:
| IRQ | Name | Purpose |
|-----|------|---------|
| 119 | PIE | Packet Interface Engine |
| 124 | hi_dp_3ah | Data plane |
| 125 | hi_dp_l2 | L2 switching |
| 126 | hi_dp_l3 | L3 routing |
| 130 | hi_dp_prbs_send | PRBS test |
| 134 | hi_dp_gpon_lp | GPON low priority |
| 141 | hi_dp_gpon | GPON main |
| 142-144 | hi_dp_gpon_* | GPON rogue/silent/FWI |

## Other Peripherals

### GPIO
- Module: `hi_gpio`
- Base address: unknown (needs probing)
- No `/sys/class/gpio/` entries exported

### I2C
- Module: `hi_i2c`
- Base address: unknown

### SPI (bitbang/bus, not flash)
- Module: `hi_spi`
- Base address: unknown

### IPC (Inter-Processor Communication)
- `/dev/hi_ipc` (misc device 20)
- Module: `hi_kipc`
- Suggests a secondary processor or coprocessor (common in GPON SoCs for the PON MAC)

## Kernel Modules (vendor)

All prefixed `hi_` and licensed as proprietary (`'HSAN Tech.'`):

| Module | Size | Purpose |
|--------|------|---------|
| `hi_kbasic` | 23KB | Base platform support |
| `hi_sysctl` | 8KB | System control registers |
| `hi_pie` | 31KB | Packet Interface Engine (ethernet) |
| `hi_bridge` | 211KB | L2 bridge/switch |
| `hi_gpon` | 35KB | GPON MAC |
| `hi_epon` | 31KB | EPON MAC |
| `hi_khal` | 429KB | Hardware Abstraction Layer |
| `hi_l3` | 90KB | L3 routing |
| `hi_oam` | 36KB | OAM (Operations, Admin, Maintenance) |
| `hi_kploam` | 55KB | GPON PLOAM |
| `hi_kvif` | 28KB | Virtual Interface Framework |
| `hi_kmc_adapter` | 20KB | Multicast adapter |
| `hi_knet_adapter` | 6KB | Network adapter |
| `hi_diag_chip` | 144KB | Chip diagnostics |
| `hi_khrw` | 2KB | Hardware read/write |
| `hi_timer` | 2KB | Timer driver |
| `hi_gpio` | 2KB | GPIO driver |
| `hi_i2c` | 5KB | I2C driver |
| `hi_spi` | 14KB | SPI driver |
| `hi_mdio` | 3KB | MDIO bus (PHY management) |
| `hi_sci` | 19KB | Smart Card Interface |

## U-Boot Environment

```
bootargs=mem=32M console=ttyAMA1,115200 root=/dev/mtdblock3 rootfstype=cramfs mtdparts=hi_sfc:256k(boot),256k(env),1536k(kernel),5888k(rootfs_app),1536k(kernel),5888k(rootfs_app),1M(cfg)
bootcmd=sf probe 1;sf read 0x80300000 0x80000 0x180000;bootm 0x80300000
bootdelay=2
baudrate=115200
ipaddr=192.168.15.222
serverip=192.168.15.10
netmask=255.255.255.0
bootfile="uImage"
ethaddr=BC:98:89:09:5D:98
ver=U-Boot 2010.03-svn361466 (Sep 07 2015 - 12:36:03)
```

TFTP boot command: `tftpboot 0x80300000 uImage && bootm 0x80300000`

## Serial Console

- **Header**: JP2 (3 pins)
- **Baud**: 115200 8N1
- **Voltage**: 3.3V
- **UART**: ttyAMA1 at `0x1010f000`
- **U-Boot access**: Spam key during first 2s of boot to interrupt autoboot

## Vendor Kernel Details

| Field | Value |
|-------|-------|
| Version | `2.6.34.10_sd5116` |
| Build date | Mon Feb 5 15:36:51 CST 2018 |
| Toolchain | crosstool-NG 1.13.2 - hsan-5115, GCC 4.4.6 |
| C library | uClibc |
| Image format | uImage (uncompressed) |
| Root filesystem | cramfs on `/dev/mtdblock3` |
| Kernel config | Not available (no `/proc/config.gz`, no IKCFG marker in binary) |

## What We Have

- [x] Full 16MB flash dump (`onu_backup.bin`)
- [x] Extracted cramfs rootfs (`rootfs_extracted/`)
- [x] All vendor kernel modules (`.ko` files in rootfs)
- [x] Complete dmesg log
- [x] `/proc/iomem`, `/proc/interrupts`, `/proc/cpuinfo`, `/proc/meminfo`
- [x] `/proc/kallsyms` dump (`log` file)
- [x] Telnet root access to running device
- [x] Serial console access (JP2, UART adapter connected)
- [x] U-Boot access (serial, autoboot interrupt)
- [x] TFTP boot configured and tested (ping OK)
- [x] GIC base addresses reverse-engineered
- [x] Cross-compilation toolchain (GCC 14.2.0 arm-linux-gnueabi)
- [x] Linux 6.12.x LTS kernel source (`linux/`)

## What We Need

### Phase 1: Reconnaissance
- [x] Probe GIC base address
- [x] Get serial console access
- [x] Check U-Boot environment for TFTP/netboot support
- [ ] Probe timer base address (try Cortex-A9 private timer at `0x10180600`, or SP804 at nearby addresses)
- [ ] Dump all `hi_*` kernel modules and reverse-engineer register addresses with Ghidra
- [ ] Find system control register base (from `hi_sysctl` module)
- [ ] Map the full peripheral address space via `devmem` probing

### Phase 2: Minimal Boot
- [ ] Write a minimal device tree (`.dts`) for SD5116
- [ ] Configure mainline kernel 6.12 with: ARM multi-platform, Cortex-A9, GIC, timer, DW UART
- [ ] Cross-compile with `arm-linux-gnueabi-` toolchain
- [ ] Boot via U-Boot TFTP
- [ ] Target: get to serial console output and kernel panic (no rootfs) = success

### Phase 3: Ethernet
- [ ] Reverse-engineer `hi_pie` module for ethernet register map
- [ ] Determine if the ethernet MAC is a known IP block (Synopsys DesignWare GMAC / stmmac is common in HiSilicon)
- [ ] Write or adapt an ethernet driver
- [ ] Target: ping from the device

### Phase 4: Full System
- [ ] SPI flash controller driver (try mainline `hisi-sfc`)
- [ ] Root filesystem on SPI flash
- [ ] GPIO, I2C, SPI bus drivers
- [ ] Target: self-hosted boot from flash with network

### Phase 5: Upstream
- [ ] Clean up device tree bindings
- [ ] Write dt-binding YAML schemas
- [ ] Submit patches to `linux-arm-kernel@lists.infradead.org`
- [ ] Blog series documenting the entire RE process

## Reference SoCs in Mainline

Similar HiSilicon ARM SoCs already in the kernel that can serve as templates:

| SoC | Kernel path | CPU | Relevance |
|-----|-------------|-----|-----------|
| hi3519 | `arch/arm/boot/dts/hisilicon/hi3519.dtsi` | Cortex-A7 | Camera SoC, GIC at 0x10300000 area, SP804 timers, PL011 UARTs |
| sd5203 | `arch/arm/boot/dts/hisilicon/sd5203.dts` | ARM926 | Telecom SoC, DW APB UART, SP804 timers |

The **hi3519** is the best reference for peripheral IP blocks (GIC, SP804, PL011). The **sd5203** is the closest by product family (also a telecom/GPON SoC from HiSilicon).

## Key Risks

1. **Ethernet**: The `hi_pie` / `hi_bridge` stack is 400KB+ of proprietary code. If the MAC isn't a standard IP block (stmmac/DesignWare), writing a driver from scratch via RE is a major effort.
2. **Timer**: If it's not SP804-compatible or the Cortex-A9 private timer, we need to RE the timer registers too. Base address is still unknown.
3. **32MB RAM**: A modern 6.x kernel will use more RAM than 2.6.34. May need aggressive config stripping to leave enough for userspace.
4. **Kernel binary is compressed/obfuscated**: The `.init` and `.text` sections in the flash dump have high entropy -- cannot be disassembled directly from the flash image. Must read from physical RAM on a running system instead.
