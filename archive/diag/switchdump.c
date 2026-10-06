/* Dump known-SAFE switch register regions (all words) as "addr val" lines.
 * Only regions verified readable via devmem this session (no bus-hang holes).
 * nolibc static ARM. */

int main(int argc, char **argv)
{
	struct { unsigned long base; unsigned long size; } rg[] = {
		{ 0x12180000, 0x400 }, { 0x120c0000, 0x200 }, { 0x123c4000, 0x100 },
		{ 0x124cff00, 0x100 }, { 0x11200000, 0x100 }, { 0x112c0000, 0x100 },
		{ 0x112c1000, 0x100 }, { 0x12440000, 0x500 }, { 0x121c0000, 0x100 },
		{ 0x121c1000, 0x2000 }, { 0x12150000, 0x40 }, { 0x11180000, 0x40 },
		{ 0x11080000, 0x40 }, { 0x12300000, 0x100 }, { 0x12350000, 0x100 },
	};
	unsigned n = sizeof(rg) / sizeof(rg[0]);
	int fd = open("/dev/mem", O_RDONLY);
	if (fd < 0)
		return 1;
	for (unsigned i = 0; i < n; i++) {
		void *p = mmap(0, rg[i].size, PROT_READ, MAP_SHARED, fd, rg[i].base);
		if (p == (void *)-1)
			continue;
		volatile unsigned int *r = p;
		for (unsigned long o = 0; o < rg[i].size / 4; o++)
			printf("%08x %08x\n",
				(unsigned)(rg[i].base + o * 4), r[o]);
		munmap(p, rg[i].size);
	}
	return 0;
}
