import onu, re, sys
def q():
    onu.sh("dmesg -c >/dev/null")
    onu.sh("echo '51 0 140' > /sys/module/sd5116_eth/parameters/eqm_rd", 30)
    o = onu.sh("dmesg | grep EQRD", 30)
    return {m[0]: m[1:] for m in re.findall(r"EQRD 51 (\w+): (\w+) (\w+) (\w+) (\w+)", o)}
sh = onu.sh if sys.argv[1] == "serial" else onu.tsh
onu.sh = sh
sh(f"arp -s 192.168.1.2 {onu.HOST_MAC}")
a = q()
sh("ping -c 20 -i 0.2 -W 1 192.168.1.2 >/dev/null", 20)
b = q()
for k in sorted(b, key=lambda x: int(x, 16)):
    if b[k] != a.get(k):
        print(k, a.get(k), "->", b[k])
print("entries", len(b))
