# Booting Linux 6.12 on a HiSilicon SD5116 GPON ONU

## The Target

The FiberHome AN5506-01-A is a GPON Optical Network Unit -- a small fiber-to-ethernet converter used by ISPs. Inside it runs a HiSilicon SD5116 SoC, a chip with no public datasheet and no mainline Linux support. The vendor kernel is a heavily patched 2.6.34 from 2015.

The goal: boot a modern Linux 6.12 kernel on this board from scratch, using only a serial console and TFTP.

**Hardware:**
- SoC: HiSilicon SD5116 (Cortex-A9 r4p1, single core, ~200MHz peripheral clock)
- RAM: 32MB @ 0x80000000
- Flash: MX25L12833F 16MB SPI NOR
- UART: Two 16550A-compatible UARTs (console on UART1 @ 0x1010f000)
- U-Boot: 2010.03-svn361466 (Sep 2015)
- Chip ID: 0x51161110

## Phase 1: First Contact

The board has a stock U-Boot from 2015 with TFTP support. The serial console runs at 115200 baud on UART1. From the vendor kernel's boot log, I extracted the key hardware information: UART addresses, memory layout, APB bus clock (100MHz), and SPI flash chip.

The first attempt was straightforward: cross-compile a minimal ARM kernel, wrap it as a uImage, and boot via `bootm`. Silence after "Starting kernel ...".

## Phase 2: Is Anything Running?

The zImage decompressor prints "Uncompressing Linux..." via `putc()`, but only when `CONFIG_DEBUG_UNCOMPRESS` is enabled -- which requires `!ARCH_MULTIPLATFORM`. Since ARCH_MULTI_V7 implies ARCH_MULTIPLATFORM, the decompressor's putc is a no-op. The lack of output didn't mean a crash in the decompressor; it meant we couldn't see it.

I wrote a bare-metal UART diagnostic (`archive/diag/uart_test.S`) -- 24 bytes that write "HI1" to UART1 at 0x1010f000. Loaded via TFTP and executed with U-Boot's `go` command:

```
tftpboot 0x80F00000 diag.bin
go 0x80F00000
```

Output: `HI1`. UART1 confirmed working. U-Boot's `go` command works. The tool chain is correct.

## Phase 3: The bootm Trap

Next, I tried booting the uncompressed `Image` directly (bypassing the zImage decompressor). This produced a real kernel error: "Error: invalid dtb and unrecognized/unsupported machine ID". Progress!

The error meant the kernel was executing but couldn't find the DTB. `CONFIG_ARM_APPENDED_DTB` only works with zImage, not raw Image. I needed to pass the DTB address in register r2 per the ARM boot protocol.

I wrote a trampoline -- a small shim that sets up the ARM boot protocol registers (r0=0, r1=machine_type, r2=DTB address) and jumps to the kernel at 0x80008000. The trampoline is loaded at 0x80F00000, the DTB at 0x80700000, and the kernel Image at 0x80008000.

First attempt with `bootm` wrapping the trampoline as a uImage: the trampoline's debug output "T1" repeated in an infinite loop. Something was overwriting the kernel.

**Key discovery:** this ancient U-Boot (2010.03) ignores the load address in the uImage header and always extracts the payload to 0x80008000. Since that's where the kernel Image was loaded, the 308-byte trampoline overwrote the first 308 bytes of the kernel. The trampoline jumped to 0x80008000 and executed... itself. Loop.

**Fix:** use `go` instead of `bootm`. The `go` command just jumps to an address without unpacking anything. But `go` doesn't call `cleanup_before_linux()`, so caches and MMU are still active from U-Boot.

## Phase 4: The Trampoline

The trampoline grew to v4 with full cache/MMU cleanup:

```asm
_start:
    /* Enter SVC mode, disable IRQs */
    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd3
    msr     cpsr_c, r0

    /* Invalidate I-cache */
    mcr     p15, 0, r0, c7, c5, 0

    /* Clean+invalidate D-cache by set/way */
    /* ... 4 ways, 256 sets ... */

    /* Disable MMU, D-cache, I-cache */
    mrc     p15, 0, r0, c1, c0, 0
    bic     r0, r0, #0x0001        @ MMU
    bic     r0, r0, #0x0004        @ D-cache
    bic     r0, r0, #0x1000        @ I-cache
    mcr     p15, 0, r0, c1, c0, 0

    /* TLB invalidate, DSB, ISB */

    /* ARM boot protocol */
    mov     r0, #0
    ldr     r1, =0x00000AD1        @ machine type
    ldr     r2, =0x80700000        @ DTB address
    ldr     pc, =0x80008000        @ jump to kernel
```

With this trampoline and `go`, the kernel received correct registers and a clean CPU state. But still silence.

## Phase 5: Instrumenting head.S

With no output from the kernel, I added raw UART writes directly into `arch/arm/kernel/head.S` at every critical step of the boot sequence:

```asm
/* Print 'K' at stext entry */
ldr  r3, =0x1010f000
98:  ldr  r4, [r3, #0x14]    @ read LSR
     tst  r4, #0x20           @ THRE?
     beq  98b
     mov  r4, #'K'
     str  r4, [r3, #0x00]
```

Markers were placed at: stext entry (K), SVC mode (1), processor lookup (2), PHYS_OFFSET (3), vet_atags (4), fixup_pv_table (5), create_page_tables (6), proc init (7), before __enable_mmu (8), at __enable_mmu entry (9).

First result: `K123456789`. All pre-MMU steps pass. The crash was during the MMU enable.

## Phase 6: The Register Corruption Bug

The debug prints at markers 6, 7, and 8 used r3/r4 as scratch registers. But r4 holds the page table physical address returned by `__create_page_tables`. By the time `__enable_mmu` executed `mcr p15, 0, r4, c2, c0, 0` to load TTBR0, r4 contained `0x38` (ASCII '8') instead of `0x80004000`.

The MMU was enabled pointing at address 0x38 as the page table base. Instant crash.

**Fix:** changed markers 6, 7, 8 to use r5/r6 instead of r3/r4.

But this wasn't the only issue. Even with r4 preserved, the output was still `K123456789`. I added two more markers inside `__turn_mmu_on` using `movw`/`movt` (no literal pool needed):

- 'M' before the MMU-enable MCR, via physical UART (0x1010f000)
- 'V' after the ISB, via virtual UART (0xf810f000)

Result: `K123456789MV`. The MMU transition works. The virtual UART mapping works.

## Phase 7: Into C Code

A 'W' marker at the start of `__mmap_switched` confirmed the jump to virtual addressing. Markers 'a' through 'd' traced through BSS clearing, stack setup, and the call to `start_kernel()`.

Then raw UART writes in C (`volatile unsigned int *uart = (volatile unsigned int *)0xf810f000; uart[0] = 'S';`) confirmed entry into `start_kernel()`.

Result: `K123456789MVWabcdSefg`. The kernel enters C code, runs through early init, disables IRQs, initializes the boot CPU. All good.

## Phase 8: The Missing Machine Descriptor

Inside `setup_arch()`, the crash occurred at `setup_machine_fdt()`. The DTB has `compatible = "hisilicon,sd5116"` but the only `DT_MACHINE_START` in `mach-hisi/hisilicon.c` matches `"hisilicon,hi3620-hi4511"`. No match means `dump_machine_table()`, which loops forever.

**Fix:** added a DT_MACHINE_START entry for the SD5116:

```c
static const char *const sd5116_compat[] __initconst = {
    "hisilicon,sd5116",
    NULL,
};

DT_MACHINE_START(SD5116, "HiSilicon SD5116 (Flattened Device Tree)")
    .map_io     = sd5116_map_io,
    .dt_compat  = sd5116_compat,
MACHINE_END
```

## Phase 9: The UART FIFO Overflow

With the machine descriptor fix, output appeared to stop at `setup_arch` marker '2'. But the real problem was subtler: the debug prints were blasting characters to the UART without checking THRE (Transmitter Holding Register Empty). The 16550 FIFO holds 16 characters. We were writing 30+ characters in quick succession. Characters were being dropped, making it *look* like the kernel stopped when it actually continued.

**Fix:** proper THRE-waiting debug function:

```c
static void __init sd5116_dbg(char c)
{
    volatile unsigned int *u = (volatile unsigned int *)0xf810f000;
    while (!(u[5] & 0x20))
        ;  /* wait for THRE */
    u[0] = c;
}
```

With reliable UART output: `[ABCDEFGgH` -- deep into `setup_arch()`.

## Phase 10: The earlycon Crash

`parse_early_param()` crashed. The bootargs contained `earlycon=uart8250,mmio32,0x1010f000,115200n8`. The earlycon setup code was crashing during ioremap of the UART physical address.

**Fix:** replaced `earlycon` with `earlyprintk` in the device tree bootargs. ARM's `earlyprintk` uses the DEBUG_LL infrastructure (which we already had working) instead of the earlycon ioremap path.

```dts
bootargs = "console=ttyS1,115200 earlyprintk";
```

Result: the first real kernel messages appeared.

## Phase 11: No Timer

```
timer_probe: no matching timers found
```

The kernel booted to the point of needing a clocksource but found none. The device tree had the correct timer nodes (addresses confirmed by the CBAR diagnostic), but the `ARM_GLOBAL_TIMER` driver wasn't being compiled.

The Kconfig entry has `bool "..." if COMPILE_TEST`, making it invisible unless explicitly selected by a platform. Other HiSilicon platforms (like HIP01) select it, but there was no SD5116 platform entry.

**Fix:** added `ARCH_SD5116` to `mach-hisi/Kconfig`:

```kconfig
config ARCH_SD5116
    bool "Hisilicon SD5116 family (GPON ONU)"
    depends on ARCH_MULTI_V7
    select ARM_GLOBAL_TIMER
```

## Phase 12: The CBAR Diagnostic

To populate the device tree with correct addresses for the GIC, timers, and SCU, I wrote a bare-metal diagnostic that reads the Cortex-A9 CBAR register (CP15 c15, 4) and probes the peripheral block:

```asm
mrc     p15, 4, r5, c15, c0, 0     @ read CBAR
```

Results:

```
CBAR:10180000
SCU:00000001 CF:00000000
GD:0000FC04 I:0102043B
GC:3901243B
GT:00000000 00000000 C:00000000
PT:00000000 00000000 00000000
WD:00000000 00000000 00000000
ID:414FC091
```

This gave us the complete memory map:

| Peripheral | Address |
|---|---|
| SCU | 0x10180000 |
| GIC CPU Interface | 0x10180100 |
| Global Timer | 0x10180200 |
| Private Timer | 0x10180600 |
| GIC Distributor | 0x10181000 |

Single core (CF=0), 160 IRQ lines (TYPER ITLinesNumber=4), Cortex-A9 r4p1.

## Boot

```
tftpboot 0x80700000 sd5116.dtb
tftpboot 0x80008000 Image
tftpboot 0x80F00000 trampoline.bin
go 0x80F00000
```

```
T1 OK E59F314C
K123456789MVWabcdSefgh[ABCDEFGgH
Booting Linux on physical CPU 0x0
Linux version 6.12.31 (arm-none-eabi-gcc 14.2.0)
CPU: ARMv7 Processor [414fc091] revision 1 (ARMv7), cr=10c53c7d
OF: fdt: Machine model: FiberHome AN5506-01-A
clocksource: arm_global_timer: mask: 0xffffffffffffffff max_cycles: 0x171024e7e0
Calibrating delay loop... 200.00 BogoMIPS (lpj=1000000)
Serial: 8250/16550 driver, 2 ports
1010e000.serial: ttyS0 at MMIO 0x1010e000 (irq = 17) is a 16550A
1010f000.serial: ttyS1 at MMIO 0x1010f000 (irq = 18) is a 16550A
printk: legacy console [ttyS1] enabled
Freeing unused kernel image (initmem) memory: 1024K
Run /sbin/init as init process
Kernel panic - not syncing: No working init found.
```

Linux 6.12 boots on the HiSilicon SD5116. 32MB RAM detected, both UARTs probed with working interrupts, global timer running at 100MHz, real console output on ttyS1. The "No working init" panic is the good kind -- it means the kernel completed initialization and is looking for userspace.

## What's Left

- Build a minimal initramfs with busybox for a shell
- Ethernet driver (the whole point of a GPON ONU)
- SPI NOR flash driver for persistent storage
- Clean up the debug prints from head.S, head-common.S, init/main.c, and setup.c
- Upstream the device tree and platform support

## Tools Used

- `arm-none-eabi-gcc` 14.2.0 cross-compiler
- Bare-metal assembly diagnostics for UART, cache, and peripheral probing
- U-Boot TFTP boot with `go` command (not `bootm`)
- A trampoline binary to bridge U-Boot's `go` and the ARM boot protocol
- Raw UART register writes for pre-console debugging
- Linux 6.12.31 mainline kernel

## Files

```
archive/diag/
  uart_test.S       - UART identification diagnostic
  trampoline.S      - Boot shim (cache cleanup + ARM boot protocol)
  trampoline.lds    - Linker script (load at 0x80F00000)
  cbar_dump.S       - Cortex-A9 peripheral base address dump

linux/arch/arm/
  boot/dts/hisilicon/sd5116.dtsi          - SoC device tree
  boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dts  - Board device tree
  configs/sd5116_defconfig                - Kernel config
  mach-hisi/Kconfig                       - Added ARCH_SD5116
  mach-hisi/hisilicon.c                   - Added DT_MACHINE_START for SD5116
```
