#!/usr/bin/env python3
"""Check an interface model the way the toolchain will: structure, evidence, and a conversion that
survives the bytes the device can return.   python3 validate.py <model_dir>"""
import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "_toolkit"))
from generative_driver._toolchain import interpret  # noqa: E402

model_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
r = interpret.model_validate(model_dir=model_dir, run_dir=os.path.join(model_dir, "validation"))
if r.get("ok"):
    print("PASS  model_validate:", r.get("checks_run"), "checks")
else:
    print("FAIL  model_validate")
    for d in r.get("defects", []):
        print("  -", d.get("where"), ":", d.get("what"))
        for e in (d.get("evidence") or [])[:3]:
            print("      ", e)
sys.exit(0 if r.get("ok") else 1)
