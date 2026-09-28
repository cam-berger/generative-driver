---
name: interpret-blackbox
description: Turns a bus scan and register sweep snapshots of an unknown part into an interface model for the driver compiler's class 4 front-end. Use when acquire_classify found no description and the workspace holds scan.json and sweep.json.
---

# Interpret a black box

Nothing describes this device. You are given what the bus showed: which addresses acknowledged, and n snapshots of every register of one address over time, sometimes labelled with what a person was doing. Produce an interface model that a generic runner can execute to identify the device and read the registers that carry measurements, with the raw values as the output.

## Inputs, in the workspace

- `scan.json`: the addresses that acknowledged.
- `sweep.json`: snapshots of registers 0x00 to 0xFF with timestamps, and a label per snapshot when a stimulus was applied (for example still, breath, thumb).
- `WIRING.md`, the bus and master.
- `INTERFACE_MODEL.md`, the schema.
- `defects.json`, on a retry only. Revise; do not start over.

## Outputs, in the workspace

- `model.json`: identify as the lowest register that is constant across every snapshot and is neither 0x00 nor 0xFF, with the observed value as `identity.expect`; measure as one `reg_read` per contiguous run of registers that changed across snapshots; state as the constant runs that are not the identity, read once; init empty. `safety.never` lists every write: this class never writes. Provenance for every entry is the snapshot ids that showed it.
- `convert.py`: the identity function, returning each measured run as a big-endian integer with unit `raw`.
- `NOTES.md`: and the hypothesis for each run: which stimulus it moved with, its width, whether it looks signed, and what you would try next on the bus. Low confidence is expected.

## Rules

- Use the configured Python 3.11+ interpreter for `python3` commands; on Windows use its full path.
- Run `python3 validate.py .` before you finish and fix every defect it reports.
- Do not name the part or use any memory of parts with this address to fill in registers the sweep did not show moving.
- Work only in this workspace. Stop when `validate.py` passes or after one hour.
