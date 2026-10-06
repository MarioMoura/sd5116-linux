import onu, json, sys, time
k = json.load(open("eq_kexec.json")); c = json.load(open("eq_cold.json"))
def apply(types):
    cmds = []
    for key in sorted(k):
        t, i = key.split(":")
        if t in types and k[key] != c.get(key):
            cmds.append(f"echo '{t} {i} {k[key]}' > /sys/module/sd5116_eth/parameters/eqm_wr")
    for n in range(0, len(cmds), 8):
        onu.sh("; ".join(cmds[n:n+8]), 20)
    return len(cmds)
onu.sh(f"arp -s 192.168.1.2 {onu.HOST_MAC}")
for stage in sys.argv[1:]:
    types = stage.split(",")
    print("stage", stage, "wrote", apply(types))
    time.sleep(1)
    onu.nettest(4)
