---
name: interpret-firmware
description: Turns a decompiled firmware image into an interface model (model.json plus convert.py) for the driver compiler's class 1 front-end. Use when acquire_classify landed on the flash rung and the workspace holds decomp.c, a function table, a strings cross-reference and a disassembly.
---

# Interpret a firmware image

The firmware already on the controller is a driver somebody wrote who knew the hardware. You are given the decompiler's export of it and nothing else about the board, and no access to the bus. Produce an interface model that a generic runner can execute to reproduce what the firmware does to the device, plus the conversion code that reproduces the firmware's arithmetic.

## Inputs, in the workspace

- `decomp.c`, every function decompiled; `functions.txt`, the function table with addresses; `strings_xref.txt`, strings and the functions that reference them; `disassembly.txt`.
- `rom_symbols.txt`, when present: names for ROM calls. Absent in a stripped image; the job is the same.
- `WIRING.md`, when present. If absent, the bus pins are part of what you find in the firmware.
- `INTERFACE_MODEL.md`, the schema. Follow it exactly.
- `defects.json`, on a retry only. Revise; do not start over.

## Outputs, in the workspace

- `model.json`: the bus and address the firmware uses; the init writes in the firmware's order with its delays; the identity read if the firmware makes one and the value it checks; the periodic read sequence and its period; the state reads the firmware keeps. Provenance for every entry is a function name and offset in `decomp.c`.
- `convert.py`: the firmware's arithmetic ported as written, including its casts, widths and sign handling. If something looks wrong against what you know of arithmetic, port it anyway and say so in NOTES.md; the model describes the firmware, and the bench decides who is right. Standard library only.
- `NOTES.md`: which functions carried the evidence, what you could not settle, what you would check first on the bus.

## Rules

- One model per bus device. If the firmware drives more than one, write `device_1/`, `device_2/` each with the three files and say in NOTES.md which is which.
- Run `python3 validate.py .` before you finish and fix every defect it reports.
- Leave a value null rather than guess. Do not use the part's name, if you infer one, to fill in anything the firmware does not do.
- Work only in this workspace. Stop when `validate.py` passes or after two hours.
