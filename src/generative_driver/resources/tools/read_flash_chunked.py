#!/usr/bin/env python3
"""Dump a whole flash in 1 MB pieces with retries, for USB links that drop under a long transfer.
    python read_flash_chunked.py <port> <size_mb> <out.bin> [baud]
Assumes the chip is already in download mode (uses --before no-reset --after no-reset)."""
import subprocess, sys, os, hashlib
port, size_mb, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
baud = sys.argv[4] if len(sys.argv) > 4 else "460800"
esptool = [sys.executable, "-m", "esptool"]
parts = []
for i in range(size_mb):
    off = i * 1048576; f = f"{out}.part{i}"
    for attempt in range(1, 5):
        r = subprocess.run([*esptool, "-p", port, "-b", baud, "--before", "no-reset", "--after", "no-reset",
                            "read-flash", str(off), "0x100000", f], capture_output=True, text=True)
        if r.returncode == 0 and os.path.getsize(f) == 1048576:
            print(f"part {i} ok (attempt {attempt})"); break
        err = [l for l in (r.stdout + r.stderr).splitlines() if "ERROR" in l]
        print(f"part {i} attempt {attempt} failed: {err[-1][:90] if err else 'no output'}")
        baud = "230400"  # back off after a failure
    else:
        sys.exit(f"gave up on part {i}")
    parts.append(f)
with open(out, "wb") as o:
    for f in parts:
        o.write(open(f, "rb").read()); os.remove(f)
h = hashlib.sha256(open(out, "rb").read()).hexdigest()
print(f"{h}  {out}")
open(os.path.join(os.path.dirname(out), "SHA256SUMS"), "a", encoding="utf-8").write(f"{h}  {out}\n")
