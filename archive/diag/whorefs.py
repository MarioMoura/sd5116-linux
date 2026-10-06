#!/usr/bin/env python3
"""Which functions reference a given global in hi_bridge.ko?"""
import re,subprocess,sys,bisect
KO="/home/mario/onu/rootfs_extracted/lib/hsan/ko/driver/hi_bridge.ko"
def run(*a): return subprocess.run(a,capture_output=True,text=True).stdout
funcs=[]
for ln in run("readelf","-sW",KO).splitlines():
    f=ln.split()
    if len(f)>=8 and f[3]=="FUNC":
        try: funcs.append((int(f[1],16),int(f[2]),f[7]))
        except ValueError: pass
funcs.sort()
starts=[a for a,_,_ in funcs]
def fn(off):
    i=bisect.bisect_right(starts,off)-1
    if i<0: return "?"
    a,sz,n=funcs[i]
    return n if off<a+sz else "?"
symidx={}
for ln in run("readelf","-sW",KO).splitlines():
    f=ln.split()
    if len(f)>=8 and re.fullmatch(r"\d+:",f[0]): symidx[int(f[0][:-1])]=f[7]
pat=sys.argv[1]; cur=None; hits={}
for ln in run("readelf","-rW",KO).splitlines():
    m=re.match(r"Relocation section '(\S+)'",ln)
    if m: cur=m.group(1); continue
    if cur!=".rel.text": continue
    f=ln.split()
    if len(f)>=4 and re.fullmatch(r"[0-9a-f]{8}",f[0]):
        nm=symidx.get(int(f[1],16)>>8,"")
        if re.search(pat,nm): hits.setdefault(fn(int(f[0],16)),set()).add(nm)
for k in sorted(hits): print("%-50s %s"%(k,",".join(sorted(hits[k]))))
