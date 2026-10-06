/*
 * Minimal UART diagnostic for SD5116
 * Writes "HI1\r\n" to UART1 (0x1010f000) and "HI0\r\n" to UART0 (0x1010e000)
 * in an infinite loop. If you see output, the UART address is confirmed
 * and U-Boot is jumping correctly.
 *
 * DesignWare APB UART, reg-shift=2 (registers at 4-byte offsets):
 *   THR = offset 0x00 (transmit)
 *   LSR = offset 0x14 (line status, bit 5 = THRE)
 */
.text
.global _start

_start:
    /* Disable IRQs */
    mrs     r0, cpsr
    orr     r0, r0, #0xc0
    msr     cpsr_c, r0

loop:
    /* === Write "HI1\r\n" to UART1 at 0x1010f000 === */
    ldr     r4, =0x1010f000

    mov     r1, #'H'
    bl      uart_putc
    mov     r1, #'I'
    bl      uart_putc
    mov     r1, #'1'
    bl      uart_putc
    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* === Write "HI0\r\n" to UART0 at 0x1010e000 === */
    ldr     r4, =0x1010e000

    mov     r1, #'H'
    bl      uart_putc
    mov     r1, #'I'
    bl      uart_putc
    mov     r1, #'0'
    bl      uart_putc
    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* Small delay */
    ldr     r0, =0x100000
1:  subs    r0, r0, #1
    bne     1b

    b       loop

/*
 * uart_putc: write character in r1 to UART base in r4
 * Clobbers: r2, r3
 */
uart_putc:
    /* Wait for THRE (bit 5 of LSR at offset 0x14) */
    ldr     r3, =0x100000       @ timeout counter
2:  ldr     r2, [r4, #0x14]     @ read LSR
    tst     r2, #0x20           @ THRE set?
    bne     3f
    subs    r3, r3, #1
    bne     2b
    bx      lr                  @ timeout, give up
3:  str     r1, [r4, #0x00]     @ write THR
    bx      lr
