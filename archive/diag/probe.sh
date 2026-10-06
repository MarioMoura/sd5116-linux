# usage: sh probe.sh ADDR...   zero each reg, ping, report CPU-in/up_map deltas, restore
for r in "$@"; do
  v=$(devmem $r)
  devmem $r 32 0
  c0=$(devmem 0x1108101c); u0=$(devmem 0x124471c0)
  ping -c 2 -W 1 192.168.1.2 >/dev/null 2>&1
  c1=$(devmem 0x1108101c); u1=$(devmem 0x124471c0)
  devmem $r 32 $v
  echo "P $r was=$v cpuin+$((c1-c0)) upmap+$((u1-u0))"
done
echo PROBE_DONE
