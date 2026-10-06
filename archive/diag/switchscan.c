/* Fast /dev/mem scanner (nolibc). Fork per base so an unbacked region that
 * SIGBUSes only kills that child; the parent continues. Static ARM for vendor. */

static unsigned int parsehex(const char *s)
{
	unsigned int v = 0;
	if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X'))
		s += 2;
	while (*s) {
		char c = *s++;
		unsigned d;
		if (c >= '0' && c <= '9') d = c - '0';
		else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
		else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
		else break;
		v = (v << 4) | d;
	}
	return v;
}

int main(int argc, char **argv)
{
	if (argc < 2)
		return 1;
	unsigned int want = parsehex(argv[1]);
	unsigned long bases[] = {
		0x11000000,0x11080000,0x11100000,0x11180000,0x11200000,0x11280000,
		0x11300000,0x11380000,0x111c0000,0x112c0000,0x112c8000,0x12000000,
		0x12080000,0x120c0000,0x12100000,0x12150000,0x12180000,0x121c0000,
		0x12200000,0x12240000,0x12280000,0x12300000,0x12350000,0x12380000,
		0x123c0000,0x12400000,0x12440000,0x12480000,0x124c0000,0x124d0000,
		0x12500000,0x14180000,0x14300000,0x14880000,
	};
	unsigned n = sizeof(bases) / sizeof(bases[0]);
	unsigned long span = 0x10000;
	int fd = open("/dev/mem", O_RDONLY);
	if (fd < 0)
		return 1;
	for (unsigned i = 0; i < n; i++) {
		int pid = fork();
		if (pid == 0) {
			void *p = mmap(0, span, PROT_READ, MAP_SHARED, fd, bases[i]);
			if (p == (void *)-1)
				exit(0);
			volatile unsigned int *r = p;
			for (unsigned long o = 0; o < span / 4; o++) {
				if (r[o] == want)
					printf("%08x = %08x nb=%08x\n",
						(unsigned)(bases[i] + o * 4),
						r[o], r[o + 1]);
			}
			exit(0);
		}
		waitpid(pid, 0, 0);
	}
	return 0;
}
