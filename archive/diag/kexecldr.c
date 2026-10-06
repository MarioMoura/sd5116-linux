/*
 * Freestanding ARM kexec loader for the vendor 2.6.34 kernel.
 * Loads Image@0x80008000, dtb@0x80f80000, trampoline@0x80f00000, entry=trampoline,
 * then reboot(KEXEC).  No libc: raw EABI syscalls (r7=nr, swi 0).
 *
 * Purpose: hand off from the vendor kernel (which has fully initialised the L2
 * switch) into our mainline kernel WITHOUT a hardware reset, so the switch
 * datapath state persists and our PIE driver inherits a working fabric.
 */

typedef unsigned int u32;
typedef unsigned long size_t;

static inline long sys3(long nr, long a, long b, long c)
{
	register long r7 asm("r7") = nr;
	register long r0 asm("r0") = a;
	register long r1 asm("r1") = b;
	register long r2 asm("r2") = c;
	asm volatile("swi 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2) : "memory");
	return r0;
}
static inline long sys4(long nr, long a, long b, long c, long d)
{
	register long r7 asm("r7") = nr;
	register long r0 asm("r0") = a;
	register long r1 asm("r1") = b;
	register long r2 asm("r2") = c;
	register long r3 asm("r3") = d;
	asm volatile("swi 0" : "+r"(r0) : "r"(r7), "r"(r1), "r"(r2), "r"(r3) : "memory");
	return r0;
}
static inline long sys6(long nr, long a, long b, long c, long d, long e, long f)
{
	register long r7 asm("r7") = nr;
	register long r0 asm("r0") = a;
	register long r1 asm("r1") = b;
	register long r2 asm("r2") = c;
	register long r3 asm("r3") = d;
	register long r4 asm("r4") = e;
	register long r5 asm("r5") = f;
	asm volatile("swi 0" : "+r"(r0)
		     : "r"(r7), "r"(r1), "r"(r2), "r"(r3), "r"(r4), "r"(r5)
		     : "memory");
	return r0;
}

#define SYS_exit	1
#define SYS_read	3
#define SYS_write	4
#define SYS_open	5
#define SYS_close	6
#define SYS_lseek	19
#define SYS_mmap2	192
#define SYS_reboot	88
#define SYS_kexec_load	347

#define O_RDONLY	0
#define PROT_READ	1
#define MAP_PRIVATE	2

#define KEXEC_ARCH_ARM	(40 << 16)
#define RB_MAGIC1	0xfee1dead
#define RB_MAGIC2	672274793
#define RB_KEXEC	0x45584543

struct kexec_segment {
	const void *buf;
	size_t bufsz;
	const void *mem;
	size_t memsz;
};

static void puts_(const char *s)
{
	const char *p = s;
	while (*p) p++;
	sys3(SYS_write, 1, (long)s, p - s);
}
static void putx(u32 v)
{
	char b[11] = "0x00000000";
	for (int i = 0; i < 8; i++) {
		int nib = (v >> ((7 - i) * 4)) & 0xf;
		b[2 + i] = nib < 10 ? '0' + nib : 'a' + nib - 10;
	}
	sys3(SYS_write, 1, (long)b, 10);
	sys3(SYS_write, 1, (long)"\n", 1);
}

/* map a file read-only, return base and set *sz */
static void *mapfile(const char *path, u32 *sz)
{
	long fd = sys3(SYS_open, (long)path, O_RDONLY, 0);
	if (fd < 0) { puts_("open fail: "); puts_(path); puts_("\n"); return 0; }
	long end = sys3(SYS_lseek, fd, 0, 2 /*SEEK_END*/);
	sys3(SYS_lseek, fd, 0, 0);
	void *m = (void *)sys6(SYS_mmap2, 0, end, PROT_READ, MAP_PRIVATE, fd, 0);
	if ((long)m < 0) { puts_("mmap fail\n"); return 0; }
	*sz = end;
	return m;
}

static size_t roundup(size_t v) { return (v + 0xfff) & ~0xfffUL; }

void _start(void)
{
	u32 isz = 0, dsz = 0, tsz = 0;
	void *img = mapfile("/tmp/Image", &isz);
	void *dtb = mapfile("/tmp/sd5116.dtb", &dsz);
	void *tra = mapfile("/tmp/trampoline.bin", &tsz);

	if (!img || !dtb || !tra) { sys3(SYS_exit, 1, 0, 0); }

	puts_("Image sz "); putx(isz);
	puts_("dtb sz   "); putx(dsz);
	puts_("tramp sz "); putx(tsz);

	static struct kexec_segment seg[3];
	seg[0].buf = img; seg[0].bufsz = isz; seg[0].mem = (void *)0x80008000; seg[0].memsz = roundup(isz);
	seg[1].buf = dtb; seg[1].bufsz = dsz; seg[1].mem = (void *)0x80f80000; seg[1].memsz = roundup(dsz);
	seg[2].buf = tra; seg[2].bufsz = tsz; seg[2].mem = (void *)0x80f00000; seg[2].memsz = roundup(tsz);

	long r = sys4(SYS_kexec_load, 0x80f00000, 3, (long)seg, KEXEC_ARCH_ARM);
	puts_("kexec_load ret "); putx(r);
	if (r != 0) { puts_("LOAD FAILED\n"); sys3(SYS_exit, 1, 0, 0); }

	puts_("rebooting into new kernel...\n");
	sys4(SYS_reboot, RB_MAGIC1, RB_MAGIC2, RB_KEXEC, 0);
	puts_("reboot returned?!\n");
	sys3(SYS_exit, 0, 0, 0);
}
