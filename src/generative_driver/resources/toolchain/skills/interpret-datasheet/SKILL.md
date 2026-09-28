---
name: interpret-datasheet
description: Turns a part's datasheet into an interface model (model.json plus convert.py) for the driver compiler's class 2 front-end. Use when acquire_classify landed on the datasheet rung and the workspace holds pages/ of extracted datasheet text and a wiring note.
---

# Interpret a datasheet

You are given a datasheet and a wiring note, nothing else about the device, and no access to the bus. Produce an interface model a generic runner can execute to identify the part, initialize it and read its measurements, plus the conversion code that turns raw bytes into the quantities the part measures.

## Inputs, in the workspace

- `pages/NNN.txt`, the text and tables of each datasheet page, and `pages/index.json`, section titles by page. If the PDF itself is present, read it by page ranges.
- `WIRING.md`, how the part is connected and what the bus master is.
- `INTERFACE_MODEL.md`, the schema. Follow it exactly.
- `defects.json`, on a retry only: what `model_validate`, `model_provenance_audit` or `probe_diff` rejected. Revise the model; do not start over.

## Outputs, in the workspace

- `model.json`. Choose an initialization the datasheet recommends for continuous reading at modest oversampling and say why in NOTES.md. Fill `identity.expect` with every chip id the datasheet allows. Put calibration reads under `state`. Give every entry a provenance line with a page reference.
- `convert.py` with `convert(inputs: dict) -> dict`, keys named for the quantities in the units the datasheet uses, reproducing the datasheet's own compensation arithmetic with its intermediate rounding, not a textbook approximation. When the datasheet gives more than one sanctioned version, implement one and name it in NOTES.md. Standard library only.
- `NOTES.md`: what the datasheet left ambiguous and what you chose, and what you would check first on the bus.

## Rules

- Use the configured Python 3.11+ interpreter for `python3` commands; on Windows use its full path.
- Run `python3 validate.py .` before you finish and fix every defect it reports.
- Leave a value null rather than guess when the datasheet does not say.
- Do not search the web for drivers or example code. The datasheet is the description.
- Work only in this workspace. Stop when `validate.py` passes or after two hours.
