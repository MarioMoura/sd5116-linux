#!/bin/sh
cd "$(dirname "$0")"
python3 cfgbisect.py >> cfgbisect.out 2>&1
grep -q DONE cfgbisect.log || { echo "phase1 stopped"; exit 1; }
cp Image.phase2 /srv/tftp/Image
[ -s initwrites.txt ] || python3 initbisect.py dump >> initbisect.out 2>&1
python3 initbisect.py >> initbisect.out 2>&1
tail -1 initbisect.log
