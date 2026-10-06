#!/usr/bin/env python3
"""List R_ARM_ABS32 literals inside a .text range of hi_bridge.ko, resolved to
the symbol they point at.  Ghidra address = file .text offset + 0x10000."""
import re, subprocess, sys, bisect
KO="/home/mario/onu/rootfs_extracted/lib/hsan/ko/driver/hi_bridge.ko"
def run(*a): return subprocess.run(a,capture_output=True,text=True).stdout
secs={}
for ln in run("readelf","-SW",KO).splitlines():
    m=re.match(r"\s*\[\s*(\d+)\]\s+(\S+)\s+(\S+)\s+([0-9a-f]+)\s+([0-9a-f]+)\s+([0-9a-f]+)",ln)
    if m: secs[int(m.group(1))]=(m.group(2),int(m.group(5),16),int(m.group(6),16))
symlines=run("readelf","-sW",KO).splitlines()
syms={}; symidx={}
for ln in symlines:
    f=ln.split()
    if len(f)<8 or not f[0].endswith(":"): continue
    try: val=int(f[1],16)
    except ValueError: continue
    i=int(f[0][:-1]); ndx=f[6]; name=f[7]
    symidx[i]=(ndx,name)
    if ndx.isdigit() and not name.startswith("$"):
        syms.setdefault(int(ndx),[]).append((val,name))
for k in syms: syms[k].sort()
def sym_at(ndx,off):
    l=syms.get(ndx,[])
    if not l: return None
    i=bisect.bisect_right([v for v,_ in l],off)-1
    if i<0: return None
    v,n=l[i]; return n if off==v else "%s+0x%x"%(n,off-v)
rels={}; cur=None
for ln in run("readelf","-rW",KO).splitlines():
    m=re.match(r"Relocation section '(\S+)'",ln)
    if m: cur=m.group(1); continue
    if cur!=".rel.text": continue
    f=ln.split()
    if len(f)>=4 and re.fullmatch(r"[0-9a-f]{8}",f[0]):
        rels[int(f[0],16)]=(f[2],int(f[1],16)>>8)
toff=secs[1][1]; blob=open(KO,"rb").read()
lo,hi=int(sys.argv[1],16),int(sys.argv[2],16)
for a in sorted(rels):
    if not (lo<=a<hi): continue
    typ,si=rels[a]
    if typ!="R_ARM_ABS32": continue
    inline=int.from_bytes(blob[toff+a:toff+a+4],"little")
    ndx,name=symidx.get(si,("?","?"))
    if name.startswith("."):          # section symbol: resolve via addend
        tgt=sym_at(int(ndx),inline) if ndx.isdigit() else name
    else:                             # named global: reloc names it directly
        tgt=name if inline==0 else "%s+0x%x"%(name,inline)
    print("ghidra DAT_%08x  file 0x%05x  -> %-40s raw=0x%08x"%(a+0x10000,a,tgt or name,inline))
