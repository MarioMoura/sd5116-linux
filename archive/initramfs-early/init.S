/*
 * Minimal init for ARM Linux - pure syscall, no libc needed.
 *
 * Mounts /proc, /sys, /dev (devtmpfs), opens console, then provides
 * a built-in mini-shell with cat/ls/dmesg/devmem implemented via
 * raw syscalls. No external binaries required.
 *
 * Built-in commands:
 *   cat <path>       - read and print a file
 *   ls <path>        - list directory (via getdents64)
 *   dmesg            - read kernel log (via syslog syscall)
 *   devmem <addr>    - read 32-bit word from physical address
 *   help             - show commands
 *
 * Build: arm-none-eabi-as -mcpu=cortex-a9 -o init.o init.S
 *        arm-none-eabi-ld -T init.lds -o init init.o
 */

.syntax unified
.arm

/* Linux ARM EABI syscall numbers */
.equ SYS_exit,       1
.equ SYS_fork,       2
.equ SYS_read,       3
.equ SYS_write,      4
.equ SYS_open,       5
.equ SYS_close,      6
.equ SYS_waitpid,    7
.equ SYS_execve,     11
.equ SYS_mount,      21
.equ SYS_mkdir,      39
.equ SYS_dup,        41
.equ SYS_syslog,     103
.equ SYS_mmap2,      192
.equ SYS_getdents64, 217
.equ SYS_openat,     322

/* Constants */
.equ O_RDONLY,   0
.equ O_RDWR,     2
.equ O_RDWR_SYNC, 0x101002
.equ MS_NOSUID,  2
.equ MS_NODEV,   4
.equ MS_NOEXEC,  8

.equ PROT_READ,  1
.equ PROT_WRITE, 2
.equ MAP_SHARED, 1

.equ SYSLOG_READ_ALL, 3

.equ BUFSZ, 4096

.section .text
.global _start
_start:
    /* Set up stack */
    ldr sp, =stack_top

    /* ---- mount proc ---- */
    ldr r0, =str_none
    ldr r1, =str_proc
    ldr r2, =str_proc_fs
    ldr r3, =(MS_NOSUID|MS_NODEV|MS_NOEXEC)
    mov r4, #0
    mov r7, #SYS_mount
    svc #0

    /* ---- mount sysfs ---- */
    ldr r0, =str_none
    ldr r1, =str_sys
    ldr r2, =str_sysfs
    ldr r3, =(MS_NOSUID|MS_NODEV|MS_NOEXEC)
    mov r4, #0
    mov r7, #SYS_mount
    svc #0

    /* ---- mkdir /dev ---- */
    ldr r0, =str_dev
    ldr r1, =0755
    mov r7, #SYS_mkdir
    svc #0

    /* ---- mount devtmpfs ---- */
    ldr r0, =str_devtmpfs
    ldr r1, =str_dev
    ldr r2, =str_devtmpfs
    mov r3, #0
    mov r4, #0
    mov r7, #SYS_mount
    svc #0

    /* ---- disable watchdog via /dev/mem ---- */
    /* open /dev/mem */
    ldr r0, =str_devmem_path
    mov r1, #O_RDWR
    mov r2, #0
    mov r7, #SYS_open
    svc #0
    cmp r0, #0
    blt skip_wdt

    mov r10, r0              @ save fd

    /* mmap2(NULL, 4096, PROT_READ|PROT_WRITE, MAP_SHARED, fd, 0x10104) */
    /* page offset for mmap2 = phys_addr >> 12 = 0x10104000 >> 12 = 0x10104 */
    mov r0, #0
    ldr r1, =0x1000
    mov r2, #(PROT_READ|PROT_WRITE)
    mov r3, #MAP_SHARED
    mov r4, r10              @ fd
    ldr r5, =0x10104         @ page offset
    mov r7, #SYS_mmap2
    svc #0

    cmn r0, #4096
    bhi skip_wdt_close

    mov r11, r0              @ save mmap base

    /* Write 0xFFFFFFFF to load register (+0x00) */
    mvn r1, #0
    str r1, [r11, #0x00]
    /* Write 0 to control register (+0x08) */
    mov r1, #0
    str r1, [r11, #0x08]

    /* Save mmap base for later kicks */
    ldr r1, =wdt_mmap_base
    str r11, [r1]

skip_wdt_close:
    /* close /dev/mem */
    mov r0, r10
    mov r7, #SYS_close
    svc #0

skip_wdt:
    /* ---- open /dev/console ---- */
    ldr r0, =str_console
    mov r1, #O_RDWR
    mov r2, #0
    mov r7, #SYS_open
    svc #0
    /* fd 0 = stdin */
    mov r7, #SYS_dup
    svc #0
    /* fd 1 = stdout */
    mov r7, #SYS_dup
    svc #0
    /* fd 2 = stderr */

    /* ---- print banner ---- */
    ldr r0, =str_banner
    bl puts

    /* ---- print dmesg on boot ---- */
    bl cmd_dmesg

    /* ---- shell loop ---- */
shell_loop:
    /* kick watchdog: reload counter to max */
    ldr r0, =wdt_mmap_base
    ldr r0, [r0]
    cmp r0, #0
    beq 1f
    mvn r1, #0
    str r1, [r0, #0x00]     @ WdogLoad = 0xFFFFFFFF
    mov r1, #0
    str r1, [r0, #0x08]     @ WdogControl = 0 (keep disabled)
1:
    ldr r0, =str_prompt
    bl puts

    /* read line */
    mov r0, #0
    ldr r1, =cmdbuf
    mov r2, #255
    mov r7, #SYS_read
    svc #0

    cmp r0, #1
    ble shell_loop

    /* null-terminate, strip newline */
    ldr r1, =cmdbuf
    sub r2, r0, #1
    mov r3, #0
    strb r3, [r1, r2]

    /* skip empty */
    ldrb r3, [r1]
    cmp r3, #0
    beq shell_loop

    /* ---- parse command ---- */
    ldr r1, =cmdbuf

    /* "cat " ? */
    ldr r2, =str_cmd_cat
    bl startswith
    cmp r0, #0
    bne do_cat

    /* "ls " ? */
    ldr r1, =cmdbuf
    ldr r2, =str_cmd_ls
    bl startswith
    cmp r0, #0
    bne do_ls

    /* "dmesg" ? */
    ldr r1, =cmdbuf
    ldr r2, =str_cmd_dmesg
    bl streq
    cmp r0, #0
    bne do_dmesg

    /* "devmem " ? */
    ldr r1, =cmdbuf
    ldr r2, =str_cmd_devmem
    bl startswith
    cmp r0, #0
    bne do_devmem

    /* "help" ? */
    ldr r1, =cmdbuf
    ldr r2, =str_cmd_help
    bl streq
    cmp r0, #0
    bne do_help

    /* unknown command */
    ldr r0, =str_unknown
    bl puts
    b shell_loop

/* ========== cat <path> ========== */
do_cat:
    /* r0 = pointer past "cat " */
    mov r4, r0              @ save path pointer

    mov r0, r4
    mov r1, #O_RDONLY
    mov r2, #0
    mov r7, #SYS_open
    svc #0

    cmp r0, #0
    blt cat_err
    mov r5, r0              @ fd

cat_loop:
    mov r0, r5
    ldr r1, =iobuf
    mov r2, #BUFSZ
    mov r7, #SYS_read
    svc #0

    cmp r0, #0
    ble cat_done

    mov r2, r0              @ bytes to write
    mov r0, #1              @ stdout
    ldr r1, =iobuf
    mov r7, #SYS_write
    svc #0
    b cat_loop

cat_done:
    mov r0, r5
    mov r7, #SYS_close
    svc #0
    b shell_loop

cat_err:
    ldr r0, =str_open_err
    bl puts
    b shell_loop

/* ========== ls <path> ========== */
do_ls:
    mov r4, r0              @ path

    mov r0, r4
    mov r1, #O_RDONLY
    mov r2, #0
    mov r7, #SYS_open
    svc #0

    cmp r0, #0
    blt ls_err
    mov r5, r0              @ fd

ls_getdents:
    mov r0, r5
    ldr r1, =iobuf
    mov r2, #BUFSZ
    mov r7, #SYS_getdents64
    svc #0

    cmp r0, #0
    ble ls_done

    mov r6, #0              @ offset into buffer
    mov r8, r0              @ total bytes

ls_entry:
    cmp r6, r8
    bge ls_getdents

    ldr r1, =iobuf
    add r1, r1, r6

    /* getdents64: d_ino(8) d_off(8) d_reclen(2) d_type(1) d_name... */
    /* d_reclen at offset 16 */
    ldrh r9, [r1, #16]      @ d_reclen

    /* d_name at offset 19 */
    add r0, r1, #19
    bl puts

    ldr r0, =str_newline
    bl puts

    add r6, r6, r9
    b ls_entry

ls_done:
    mov r0, r5
    mov r7, #SYS_close
    svc #0
    b shell_loop

ls_err:
    ldr r0, =str_open_err
    bl puts
    b shell_loop

/* ========== dmesg ========== */
do_dmesg:
    bl cmd_dmesg
    b shell_loop

cmd_dmesg:
    push {lr}
    /* syslog(SYSLOG_READ_ALL, buf, len) */
    mov r0, #SYSLOG_READ_ALL
    ldr r1, =iobuf
    ldr r2, =BUFSZ
    mov r7, #SYS_syslog
    svc #0

    cmp r0, #0
    ble 1f

    mov r2, r0
    mov r0, #1
    ldr r1, =iobuf
    mov r7, #SYS_write
    svc #0
1:
    pop {pc}

/* ========== devmem <hex_addr> ========== */
do_devmem:
    mov r4, r0              @ pointer to hex string

    /* parse hex address */
    mov r0, r4
    bl parse_hex
    mov r4, r0              @ physical address

    /* open /dev/mem */
    ldr r0, =str_devmem_path
    mov r1, #O_RDWR
    mov r2, #0
    mov r7, #SYS_open
    svc #0

    cmp r0, #0
    blt devmem_err
    mov r5, r0              @ fd

    /* mmap2(NULL, 4096, PROT_READ|PROT_WRITE, MAP_SHARED, fd, page) */
    /* page offset = addr >> 12 for mmap2 */
    mov r6, r4, lsr #12     @ page offset
    mov r0, #0               @ addr = NULL
    ldr r1, =0x1000          @ length = 4096
    mov r2, #(PROT_READ|PROT_WRITE)
    mov r3, #MAP_SHARED
    mov r7, #SYS_mmap2
    /* r4 = fd, r5 = offset - but ARM EABI uses r4,r5 */
    /* Actually mmap2 args: r0=addr r1=len r2=prot r3=flags r4=fd r5=offset */
    push {r4, r5}
    mov r4, r5               @ fd
    mov r5, r6               @ page offset
    svc #0
    pop {r4, r5}

    cmn r0, #4096            @ MAP_FAILED = -1 .. -4095
    bhi devmem_map_err

    /* read value at offset within page */
    mov r6, r0               @ mmap base
    ldr r1, =0xFFF
    and r1, r4, r1            @ offset within page
    ldr r0, [r6, r1]         @ read 32-bit value

    /* print "0x" prefix */
    push {r0}
    ldr r0, =str_hex_prefix
    bl puts
    pop {r0}

    /* print value as hex */
    bl print_hex32

    ldr r0, =str_newline
    bl puts

    /* close fd */
    mov r0, r5
    mov r7, #SYS_close
    svc #0

    b shell_loop

devmem_err:
    ldr r0, =str_devmem_openerr
    bl puts
    b shell_loop

devmem_map_err:
    ldr r0, =str_mmap_err
    bl puts
    mov r0, r5
    mov r7, #SYS_close
    svc #0
    b shell_loop

/* ========== help ========== */
do_help:
    ldr r0, =str_help
    bl puts
    b shell_loop

/* ========== utility functions ========== */

/* puts: print null-terminated string at r0 */
puts:
    push {r4, lr}
    mov r4, r0
    /* find length */
    mov r1, r0
    mov r2, #0
1:  ldrb r3, [r1], #1
    cmp r3, #0
    addne r2, r2, #1
    bne 1b

    cmp r2, #0
    beq 2f
    mov r0, #1
    mov r1, r4
    mov r7, #SYS_write
    svc #0
2:  pop {r4, pc}

/* startswith: check if string at r1 starts with string at r2 */
/* returns r0 = pointer past prefix if match, 0 if no match */
startswith:
    push {r4, r5, lr}
    mov r4, r1              @ save original
    mov r5, r2              @ save prefix
1:  ldrb r3, [r2], #1
    cmp r3, #0
    beq 2f                  @ end of prefix = match
    ldrb r0, [r1], #1
    cmp r0, r3
    bne 3f                  @ mismatch
    b 1b
2:  mov r0, r1              @ return pointer past prefix
    pop {r4, r5, pc}
3:  mov r0, #0
    pop {r4, r5, pc}

/* streq: check if strings at r1 and r2 are equal */
/* returns r0 = 1 if equal, 0 if not */
streq:
    push {lr}
1:  ldrb r3, [r1], #1
    ldrb r0, [r2], #1
    cmp r3, r0
    bne 2f
    cmp r3, #0
    bne 1b
    mov r0, #1
    pop {pc}
2:  mov r0, #0
    pop {pc}

/* parse_hex: parse hex string at r0 (with optional 0x prefix) */
/* returns value in r0 */
parse_hex:
    push {r4, lr}
    /* skip 0x prefix */
    ldrb r1, [r0]
    cmp r1, #'0'
    bne 1f
    ldrb r1, [r0, #1]
    cmp r1, #'x'
    addeq r0, r0, #2
    cmp r1, #'X'
    addeq r0, r0, #2
1:
    mov r2, #0              @ accumulator
2:  ldrb r1, [r0], #1
    cmp r1, #0
    beq 3f

    /* convert hex digit */
    cmp r1, #'0'
    blt 3f
    cmp r1, #'9'
    ble 4f
    cmp r1, #'a'
    blt 5f
    cmp r1, #'f'
    ble 6f
5:  cmp r1, #'A'
    blt 3f
    cmp r1, #'F'
    bgt 3f
    sub r1, r1, #('A' - 10)
    b 7f
6:  sub r1, r1, #('a' - 10)
    b 7f
4:  sub r1, r1, #'0'
7:  mov r2, r2, lsl #4
    orr r2, r2, r1
    b 2b

3:  mov r0, r2
    pop {r4, pc}

/* print_hex32: print r0 as 8-digit hex */
print_hex32:
    push {r4, r5, r6, lr}
    mov r4, r0
    mov r5, #8
    ldr r6, =hexbuf
1:  mov r1, r4, lsr #28
    cmp r1, #10
    addlt r1, r1, #'0'
    addge r1, r1, #('a' - 10)
    strb r1, [r6], #1
    mov r4, r4, lsl #4
    subs r5, r5, #1
    bne 1b
    mov r1, #0
    strb r1, [r6]

    ldr r0, =hexbuf
    bl puts
    pop {r4, r5, r6, pc}

/* ========== data ========== */
.section .rodata
str_none:       .asciz "none"
str_proc:       .asciz "/proc"
str_proc_fs:    .asciz "proc"
str_sys:        .asciz "/sys"
str_sysfs:      .asciz "sysfs"
str_dev:        .asciz "/dev"
str_devtmpfs:   .asciz "devtmpfs"
str_console:    .asciz "/dev/console"
str_devmem_path: .asciz "/dev/mem"
str_prompt:     .asciz "# "
str_newline:    .asciz "\n"
str_hex_prefix: .asciz "0x"
str_open_err:   .asciz "error: cannot open\n"
str_devmem_openerr: .asciz "error: cannot open /dev/mem\n"
str_mmap_err:   .asciz "error: mmap failed\n"
str_unknown:    .asciz "unknown command (type 'help')\n"

str_cmd_cat:    .asciz "cat "
str_cmd_ls:     .asciz "ls "
str_cmd_dmesg:  .asciz "dmesg"
str_cmd_devmem: .asciz "devmem "
str_cmd_help:   .asciz "help"

str_banner:
    .ascii "\n"
    .ascii "=== SD5116 Linux 6.12 ===\n"
    .ascii "Built-in shell (cat, ls, dmesg, devmem, help)\n"
    .asciz "\n"

str_help:
    .ascii "Commands:\n"
    .ascii "  cat <path>        read a file\n"
    .ascii "  ls <path>         list a directory\n"
    .ascii "  dmesg             kernel log\n"
    .ascii "  devmem <addr>     read 32-bit from physical address\n"
    .asciz "  help              this message\n"

.section .bss
.align 2
wdt_mmap_base: .space 4
cmdbuf:    .space 256
iobuf:     .space 4096
hexbuf:    .space 16
           .space 4096
stack_top:
