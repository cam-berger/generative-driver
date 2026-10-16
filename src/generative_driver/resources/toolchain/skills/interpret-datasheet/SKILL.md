---
name: interpret-datasheet
description: Turns a part's datasheet into an interface model (model.json plus convert.py) for the driver compiler's class 2 front-end. Use when acquire_classify landed on the datasheet rung and the workspace holds pages/ of extracted datasheet text and a wiring note.
---

# Interpret a datasheet

Use only workspace `pages/NNN.txt`, `pages/index.json`, `WIRING.md` and `INTERFACE_MODEL.md`; read any supplied PDF by page range. No bus access or web search for drivers/examples. On retry, revise using `defects.json` instead of restarting.

Write:

- `model.json`: recommended continuous-reading initialization with modest oversampling, all allowed `identity.expect` IDs, calibration reads in `state`, and page provenance for every entry. Explain initialization in notes; leave unsupported values null.
- `convert.py`: standard-library `convert(inputs: dict) -> dict`, named quantities/units from the datasheet. Preserve its compensation arithmetic and intermediate rounding; name the chosen sanctioned version.
- `NOTES.md`: choices, ambiguity and first proposed bus checks.

Use the configured Python 3.11+ interpreter (`python3` below; full path on Windows). Run `python3 validate.py .` and fix every defect. Work only here. Stop when validation passes or after two hours.
