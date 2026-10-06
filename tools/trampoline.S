/*
 * Trampoline v4: full boot shim for use with U-Boot "go" command
 * Disables caches and MMU (since "go" doesn't do cleanup_before_linux),
 * then sets up ARM boot protocol registers and jumps to kernel.
 *
 * Usage:
 *   tftpboot 0x80008000 Image
 *   tftpboot 0x80F80000 sd5116.dtb
 *   tftpboot 0x80F00000 trampoline.bin
 *   go 0x80F00000
 */
#ifndef DTB_ADDR
#define DTB_ADDR 0x80F80000
#endif

.text
.global _start

_start:
    /* Enter SVC mode, disable IRQs and FIQs */
    mrs     r0, cpsr
    bic     r0, r0, #0x1f
    orr     r0, r0, #0xd3      @ SVC mode, IRQ+FIQ disabled
    msr     cpsr_c, r0

    /* Disable hardware watchdog FIRST (while U-Boot MMU is still active)
     * WDT at 0x10104000: +0x00=Load, +0x08=Control
     * Also disable possible second WDT at 0x10105000 */
    ldr     r0, =0x10104000
    mvn     r1, #0                 @ 0xFFFFFFFF
    str     r1, [r0, #0x00]        @ WdogLoad: reload to max
    mov     r1, #0
    str     r1, [r0, #0x08]        @ WdogControl: disable
    ldr     r0, =0x10105000
    mvn     r1, #0
    str     r1, [r0, #0x00]
    mov     r1, #0
    str     r1, [r0, #0x08]

    /* Print "T1 " to UART before cache cleanup */
    ldr     r4, =0x1010f000
    mov     r1, #'T'
    bl      uart_putc
    mov     r1, #'1'
    bl      uart_putc
    mov     r1, #' '
    bl      uart_putc

    /* Disable L1 caches and MMU */

    /* Invalidate I-cache */
    mov     r0, #0
    mcr     p15, 0, r0, c7, c5, 0      @ ICIALLU: invalidate I-cache

    /* Clean and invalidate entire D-cache using DCCISW
     * Cortex-A9: 4-way, 256 sets, 32-byte lines
     * set/way format: way[31:30] | set[12:5] | level[3:1]
     */
    mov     r0, #0                     @ way = 0
.dcache_way:
    mov     r1, #0                     @ set = 0
.dcache_set:
    orr     r2, r0, r1                 @ combine way and set
    mcr     p15, 0, r2, c7, c14, 2    @ DCCISW
    add     r1, r1, #(1 << 5)         @ next set (bit 5)
    cmp     r1, #(256 << 5)            @ 256 sets
    blt     .dcache_set
    add     r0, r0, #(1 << 30)        @ next way (bit 30)
    bics    r3, r0, #0                 @ test if wrapped to 0
    bne     .dcache_way
    /* Data Synchronization Barrier */
    mov     r0, #0
    mcr     p15, 0, r0, c7, c10, 4    @ DSB

    /* Disable MMU, D-cache, I-cache */
    mrc     p15, 0, r0, c1, c0, 0     @ read SCTLR
    bic     r0, r0, #0x0001           @ disable MMU (bit 0)
    bic     r0, r0, #0x0004           @ disable D-cache (bit 2)
    bic     r0, r0, #0x1000           @ disable I-cache (bit 12)
    mcr     p15, 0, r0, c1, c0, 0     @ write SCTLR

    /* Instruction Synchronization Barrier */
    mov     r0, #0
    mcr     p15, 0, r0, c7, c5, 4     @ ISB (pre-v7 way)

    /* Invalidate TLBs */
    mcr     p15, 0, r0, c8, c7, 0     @ TLBIALL

    /* DSB + ISB again */
    mcr     p15, 0, r0, c7, c10, 4    @ DSB
    mcr     p15, 0, r0, c7, c5, 4     @ ISB

    /* Print "OK " to confirm cache cleanup completed */
    ldr     r4, =0x1010f000
    mov     r1, #'O'
    bl      uart_putc
    mov     r1, #'K'
    bl      uart_putc
    mov     r1, #' '
    bl      uart_putc

    /* Reset CP15 state that a prior kernel (kexec handoff) may have left:
     * DACR = manager (all domains), TTBR0/1 = 0, context id = 0.
     * With the MMU already disabled above this is belt-and-braces, but it
     * clears residue that can fault the incoming kernel's early boot. */
    mov     r0, #0
    mcr     p15, 0, r0, c2, c0, 0     @ TTBR0 = 0
    mcr     p15, 0, r0, c2, c0, 1     @ TTBR1 = 0
    mcr     p15, 0, r0, c13, c0, 1    @ CONTEXTIDR = 0
    mvn     r0, #0
    mcr     p15, 0, r0, c3, c0, 0     @ DACR = 0xffffffff
    mov     r0, #0
    mcr     p15, 0, r0, c8, c7, 0     @ TLBIALL
    mcr     p15, 0, r0, c7, c5, 0     @ ICIALLU
    mcr     p15, 0, r0, c7, c5, 6     @ BPIALL (flush branch predictor)
    dsb
    isb

    /* Marker 'J' = about to jump to kernel */
    mov     r1, #'J'
    bl      uart_putc
    mov     r1, #'\r'
    bl      uart_putc
    mov     r1, #'\n'
    bl      uart_putc

    /* ARM boot protocol */
    mov     r0, #0              @ mandatory
    ldr     r1, =0x00000AD1     @ machine type (from U-Boot)
    ldr     r2, =DTB_ADDR
    ldr     pc, =0x80008000     @ jump to kernel

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
