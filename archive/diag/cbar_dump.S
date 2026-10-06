/*
 * CBAR dump: read Cortex-A9 peripheral base address and probe GIC/timer
 *
 * Reads CP15 CBAR (Configuration Base Address Register) to find the
 * private peripheral region, then dumps key registers:
 *   - CBAR itself (peripheral base)
 *   - SCU Configuration Register (base + 0x0004)
 *   - GIC Distributor TYPER (base + 0x1004) - tells us # of IRQ lines
 *   - GIC CPU Interface IIDR (base + 0x01FC)
 *   - Global Timer Counter (base + 0x0200)
 *   - Private Timer Control (base + 0x0608)
 *
 * Usage:
 *   tftpboot 0x80F00000 cbar_dump.bin
 *   go 0x80F00000
 */
.text
.global _start

_start:
    /* UART base */
    ldr     r4, =0x1010f000

    /* Print banner: "CBAR:" */
    mov     r1, #'C'
    bl      uart_putc
    mov     r1, #'B'
    bl      uart_putc
    mov     r1, #'A'
    bl      uart_putc
    mov     r1, #'R'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc

    /* Read CBAR - Cortex-A9 peripheral base address */
    mrc     p15, 4, r5, c15, c0, 0     @ read CBAR
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Save CBAR as base pointer */
    mrc     p15, 4, r8, c15, c0, 0

    /* SCU Control Register (base + 0x0000) */
    mov     r1, #'S'
    bl      uart_putc
    mov     r1, #'C'
    bl      uart_putc
    mov     r1, #'U'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    ldr     r5, [r8, #0x00]
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc

    /* SCU Configuration (base + 0x0004) - bits[1:0] = # of CPUs - 1 */
    mov     r1, #'C'
    bl      uart_putc
    mov     r1, #'F'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    ldr     r5, [r8, #0x04]
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* GIC Distributor: TYPER (base + 0x1004) */
    mov     r1, #'G'
    bl      uart_putc
    mov     r1, #'D'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    add     r9, r8, #0x1000        @ GIC Distributor base
    ldr     r5, [r9, #0x04]        @ GICD_TYPER
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc

    /* GIC Distributor: IIDR (base + 0x1008) */
    mov     r1, #'I'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    ldr     r5, [r9, #0x08]        @ GICD_IIDR
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* GIC CPU Interface: IIDR (base + 0x01FC) */
    mov     r1, #'G'
    bl      uart_putc
    mov     r1, #'C'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    add     r9, r8, #0x0100        @ GIC CPU Interface base
    ldr     r5, [r9, #0xFC]        @ GICC_IIDR
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Global Timer: Counter Low (base + 0x0200) */
    mov     r1, #'G'
    bl      uart_putc
    mov     r1, #'T'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    add     r9, r8, #0x0200        @ Global Timer base
    ldr     r5, [r9, #0x00]        @ counter low
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc
    ldr     r5, [r9, #0x04]        @ counter high
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc

    /* Global Timer: Control (base + 0x0208) */
    mov     r1, #'C'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    ldr     r5, [r9, #0x08]        @ control
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Private Timer: Load (base + 0x0600) */
    mov     r1, #'P'
    bl      uart_putc
    mov     r1, #'T'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    add     r9, r8, #0x0600        @ Private Timer base
    ldr     r5, [r9, #0x00]        @ load value
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc
    ldr     r5, [r9, #0x04]        @ counter value
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc
    ldr     r5, [r9, #0x08]        @ control
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Watchdog: Load (base + 0x0620) */
    mov     r1, #'W'
    bl      uart_putc
    mov     r1, #'D'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    add     r9, r8, #0x0620        @ Watchdog base (within private timers)
    ldr     r5, [r9, #0x00]        @ load
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc
    ldr     r5, [r9, #0x04]        @ counter
    bl      print_hex
    mov     r1, #' '
    bl      uart_putc
    ldr     r5, [r9, #0x08]        @ control
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Read MIDR for reference */
    mov     r1, #'I'
    bl      uart_putc
    mov     r1, #'D'
    bl      uart_putc
    mov     r1, #':'
    bl      uart_putc
    mrc     p15, 0, r5, c0, c0, 0  @ MIDR
    bl      print_hex

    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Done - loop forever */
    mov     r1, #'!'
    bl      uart_putc
1:  b       1b

/*
 * print_hex: print r5 as 8-char hex string
 */
print_hex:
    push    {lr}
    mov     r6, #8
.hex_loop:
    mov     r7, r5, lsr #28
    cmp     r7, #10
    addlt   r1, r7, #'0'
    addge   r1, r7, #('A' - 10)
    bl      uart_putc
    mov     r5, r5, lsl #4
    subs    r6, r6, #1
    bne     .hex_loop
    pop     {pc}

/*
 * uart_putc: write char in r1 to UART base in r4
 */
uart_putc:
    ldr     r3, =0x100000
2:  ldr     r2, [r4, #0x14]
    tst     r2, #0x20
    bne     3f
    subs    r3, r3, #1
    bne     2b
    bx      lr
3:  str     r1, [r4, #0x00]
    bx      lr
