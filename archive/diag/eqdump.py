import onu, re, sys, json
sh = onu.sh if sys.argv[1] == "serial" else onu.tsh
out = {}
for t in ("10","11","12","13","14","20","22","23","25","26","27","50","55"):
    sh("dmesg -c >/dev/null")
    sh(f"echo '{t} 0 80' > /sys/module/sd5116_eth/parameters/eqm_rd", 30)
    o = sh("dmesg | grep EQRD", 30)
    for m in re.findall(r"EQRD (\w+) (\w+): (\w+ \w+ \w+ \w+)", o):
        out[f"{m[0]}:{m[1]}"] = m[2]
json.dump(out, open(sys.argv[2], "w"))
print(len(out), "entries")
