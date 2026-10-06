#!/usr/bin/env python3
"""Resolve Ghidra DAT_xxxx literal-pool addresses in hi_bridge.ko to real symbols.

For a relocatable ARM object the literal holding a global's address carries an
R_ARM_ABS32 relocation whose symbol is usually a section (.bss/.data) plus an
inline addend stored in .text.  Map (section, addend) back to the symbol that
contains it.
"""
import re, subprocess, sys, bisect

KO = sys.argv[1] if len(sys.argv) > 1 else \
    "/home/mario/onu/rootfs_extracted/lib/hsan/ko/driver/hi_bridge.ko"

def run(*a):
    return subprocess.run(a, capture_output=True, text=True).stdout

# section index -> (name, offset, size)
secs, order = {}, []
for ln in run("readelf", "-SW", KO).splitlines():
    m = re.match(r"\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)", ln)
    if m:
        secs[int(m.group(1))] = (m.group(2), int(m.group(5), 16), int(m.group(6), 16))

# symbols per section, sorted by value
syms = {}
for ln in run("readelf", "-sW", KO).splitlines():
    f = ln.split()
    if len(f) < 8 or not f[0].endswith(":"):
        continue
    try: val = int(f[1], 16); size = int(f[2])
    except ValueError: continue
    ndx, name = f[6], f[7]
    if not ndx.isdigit() or name.startswith("$"):
        continue
    syms.setdefault(int(ndx), []).append((val, size, name))
for k in syms: syms[k].sort()

def sym_at(ndx, off):
    lst = syms.get(ndx, [])
    if not lst: return None
    i = bisect.bisect_right([v for v, _, _ in lst], off) - 1
    if i < 0: return None
    v, sz, n = lst[i]
    return "%s+0x%x" % (n, off - v) if off != v else n

# relocations in .text: offset -> (type, symidx, symname)
rels = {}
cur = None
for ln in run("readelf", "-rW", KO).splitlines():
    m = re.match(r"Relocation section '(\S+)'", ln)
    if m: cur = m.group(1); continue
    if cur != ".rel.text": continue
    f = ln.split()
    if len(f) >= 5 and re.fullmatch(r"[0-9a-f]{8}", f[0]):
        rels[int(f[0], 16)] = (f[2], int(f[1], 16) >> 8, f[4] if len(f) > 4 else "")

text_off = secs[1][1]
blob = open(KO, "rb").read()

def resolve(addr):
    r = rels.get(addr)
    inline = int.from_bytes(blob[text_off + addr:text_off + addr + 4], "little")
    if not r:
        return "no-reloc raw=0x%08x" % inline
    typ, symidx, symname = r
    # find the section index this symbol belongs to
    for ln in run("readelf", "-sW", KO).splitlines():
        f = ln.split()
        if f and f[0] == "%d:" % symidx:
            ndx = f[6]
            if ndx.isdigit():
                s = sym_at(int(ndx), inline)
                return "%s -> %s (%s+0x%x)" % (typ, s or "?", secs[int(ndx)][0], inline)
            return "%s -> %s raw=0x%08x" % (typ, symname, inline)
    return "%s sym%d raw=0x%08x" % (typ, symidx, inline)

if __name__ == "__main__":
    for a in sys.argv[2:]:
        addr = int(a, 16)
        print("DAT_%08x: %s" % (addr, resolve(addr)))
