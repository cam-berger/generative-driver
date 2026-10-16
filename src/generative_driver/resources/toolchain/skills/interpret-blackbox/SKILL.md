---
name: interpret-blackbox
description: Turns a bus scan and register sweep snapshots of an unknown part into an interface model for the driver compiler's class 4 front-end. Use when acquire_classify found no description and the workspace holds scan.json and sweep.json.
---

# Interpret an unknown register device

Use only `scan.json`, timed/labelled `sweep.json` snapshots of registers 0x00–0xFF, `WIRING.md` and `INTERFACE_MODEL.md`. On retry, revise from `defects.json`.

Write:

- `model.json`: identify using the lowest constant register whose value is neither 0x00 nor 0xFF, with that value as `identity.expect`. Add one `reg_read` per contiguous changing run to measurements; read other constant runs once as state. Keep init empty and list every write in `safety.never`. Cite snapshot IDs for every entry. This class never writes.
- `convert.py`: no compensation; return each measured run as a big-endian integer with unit `raw`.
- `NOTES.md`: each run's stimulus association, width, possible signedness, uncertainty and proposed next checks.

Do not identify a part from its address or fill unseen registers from memory. Use the configured Python 3.11+ interpreter (`python3` below; full path on Windows). Run `python3 validate.py .` and fix every defect. Work only here. Stop when validation passes or after one hour.
