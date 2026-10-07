#!/usr/bin/env python3
"""SD5116 flash images and update files.

  mkflash.py env     write U-Boot env blobs into initramfs/etc/uboot/ (before kernel build)
  mkflash.py image   write flash/out/slot-{a,b}.bin, sha256sums and flash/sd5116-linux.bin
                     (needs tools/trampoline-flash.bin: make -C tools; LINUX=<kernel tree>)

Flash: 0x000000 U-Boot | 0x040000 env | 0x080000 slot A | 0x480000 slot B | 0x880000 data.
Slot:  trampoline-flash @0, dtb @0x1000, zImage @0x10000.
"""
import hashlib, os, struct, subprocess, sys, zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINUX = os.environ.get("LINUX", f"{ROOT}/linux-sd5116")
FLASH = 16 << 20
ENV_OFF, ENV_SIZE = 0x40000, 0x20000
SLOTS = {"a": 0x080000, "b": 0x480000}
SLOT_SIZE = 0x400000
DTB_OFF, ZIMG_OFF = 0x1000, 0x10000
DTC = f"{LINUX}/scripts/dtc/dtc"
DTB = f"{LINUX}/arch/arm/boot/dts/hisilicon/sd5116-fiberhome-an5506-01a.dtb"
ENV_DIR = f"{ROOT}/initramfs/etc/uboot"
OUT = f"{ROOT}/flash/out"


def slot_cmd(s):
    base = SLOTS[s]
    # U-Boot allows 16 words per command; the one-shot setenv needs <= 14 here
    return ("sf probe 1;"
            f"sf read 0x80F00000 0x{base:x} 0x{ZIMG_OFF:x};"
            f"sf read 0x80008000 0x{base + ZIMG_OFF:x} 0x{SLOT_SIZE - ZIMG_OFF:x};"
            "go 0x80F00000")


def env_block(bootcmd, slot):
    vendor = open(f"{ROOT}/flash/orig/onu_backup-2026-05-30.bin", "rb").read()
    vendor = vendor[ENV_OFF + 4:ENV_OFF + ENV_SIZE]
    old = dict(e.split(b"=", 1) for e in vendor.split(b"\0\0")[0].split(b"\0") if b"=" in e)
    env = {"ethaddr": old[b"ethaddr"].decode(), "baudrate": "115200",
           "ipaddr": "192.168.1.10", "serverip": "192.168.1.2",
           "netmask": "255.255.255.0", "bootdelay": "2", "stdin": "serial",
           "stdout": "serial", "stderr": "serial",
           "boot_a": slot_cmd("a"), "boot_b": slot_cmd("b"),
           "slot": slot, "bootcmd": bootcmd}
    data = b"\0".join(f"{k}={v}".encode() for k, v in env.items()) + b"\0\0"
    data = data.ljust(ENV_SIZE - 4, b"\0")
    return struct.pack("<I", zlib.crc32(data) & 0xffffffff) + data


def words(cmd):
    return len(cmd.split())


def envs():
    assert words(slot_cmd("a")) + 2 <= 16
    other = {"a": "b", "b": "a"}
    out = {}
    for s in "ab":
        out[f"env-{s}.bin"] = env_block(slot_cmd(s), s)
        # boot s once; U-Boot first restores the other slot as the default
        once = f"setenv bootcmd '{slot_cmd(other[s])}';saveenv;{slot_cmd(s)}"
        out[f"env-try-{s}.bin"] = env_block(once, other[s])
    return out


def slot_dtb(s):
    dts = subprocess.run([DTC, "-q", "-I", "dtb", "-O", "dts", DTB],
                         capture_output=True, text=True, check=True).stdout
    assert 'bootargs = "' in dts
    dts = dts.replace('bootargs = "', f'bootargs = "slot={s} ', 1)
    return subprocess.run([DTC, "-q", "-I", "dts", "-O", "dtb"], input=dts.encode(),
                          capture_output=True, check=True).stdout


def slot_blob(s):
    tramp = open(f"{ROOT}/tools/trampoline-flash.bin", "rb").read()  # DTB at +0x1000
    dtb = slot_dtb(s)
    zimg = open(f"{LINUX}/arch/arm/boot/zImage", "rb").read()
    assert len(tramp) <= DTB_OFF and len(dtb) <= ZIMG_OFF - DTB_OFF
    assert len(zimg) <= SLOT_SIZE - ZIMG_OFF, "kernel too big for slot"
    # the zImage decompressor moves itself above the unpacked kernel; it must
    # stay below the trampoline + DTB that U-Boot loads at 0x80F00000
    end = next(int(l.split()[0], 16) for l in open(f"{LINUX}/System.map")
               if l.rstrip().endswith(" _end")) - 0x40000000
    assert end + len(zimg) < 0x80F00000, "decompressor would overwrite the DTB"
    b = bytearray(b"\xff" * (ZIMG_OFF + len(zimg)))
    b[0:len(tramp)] = tramp
    b[DTB_OFF:DTB_OFF + len(dtb)] = dtb
    b[ZIMG_OFF:] = zimg
    return bytes(b)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "env":
        os.makedirs(ENV_DIR, exist_ok=True)
        for name, blob in envs().items():
            # U-Boot saveenv writes a mirror at +0x20000; keep both copies equal
            open(f"{ENV_DIR}/{name}", "wb").write(blob + blob)
        print("env blobs ->", ENV_DIR)
    elif cmd == "image":
        os.makedirs(OUT, exist_ok=True)
        sums = []
        img = bytearray(b"\xff" * FLASH)
        img[0:ENV_OFF] = open(f"{ROOT}/flash/orig/onu_backup-2026-05-30.bin", "rb").read()[0:ENV_OFF]
        img[ENV_OFF:ENV_OFF + 2 * ENV_SIZE] = envs()["env-a.bin"] * 2
        for s, base in SLOTS.items():
            blob = slot_blob(s)
            open(f"{OUT}/slot-{s}.bin", "wb").write(blob)
            sums.append(f"{hashlib.sha256(blob).hexdigest()}  slot-{s}.bin")
            img[base:base + len(blob)] = blob
        open(f"{OUT}/sha256sums", "w").write("\n".join(sums) + "\n")
        open(f"{ROOT}/flash/sd5116-linux.bin", "wb").write(img)
        print(f"{OUT}/slot-[ab].bin ({len(blob)} bytes), flash/sd5116-linux.bin")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
