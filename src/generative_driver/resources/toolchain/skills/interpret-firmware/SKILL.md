---
name: interpret-firmware
description: Turns a decompiled firmware image into an interface model (model.json plus convert.py) for the driver compiler's class 1 front-end. Use when acquire_classify landed on the flash rung and the workspace holds decomp.c, a function table, a strings cross-reference and a disassembly.
---

# Interpret decompiled firmware

Reproduce the firmware's device behavior and arithmetic using only workspace evidence; no bus access. Inputs: `decomp.c`, `functions.txt`, `strings_xref.txt`, `disassembly.txt`, optional `rom_symbols.txt`/`WIRING.md`, and `INTERFACE_MODEL.md`. Infer missing pins from firmware; on retry, revise from `defects.json`.

Write one model per device; use `device_1/`, `device_2/`, etc. for multiple devices and identify them in notes.

- `model.json`: bus/address, ordered initialization/delays, identity read/value if present, periodic reads/period and retained state reads. Cite function and decompilation offset for each entry.
- `convert.py`: standard library only; preserve firmware arithmetic, casts, widths and signedness, even apparent mistakes. Explain those in notes.
- `NOTES.md`: evidence functions, ambiguity and proposed first bus checks.

Leave unknown values null. An inferred part name cannot justify behavior absent from the firmware. Use the configured Python 3.11+ interpreter (`python3` below; full path on Windows). Run `python3 validate.py .` and fix every defect. Work only here. Stop when validation passes or after two hours.
