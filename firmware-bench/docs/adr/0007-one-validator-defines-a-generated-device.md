# One validator defines a generated device

Status: proposed with the [generated-device interface](../generated-device-interface.md).

The interface review of 2026-11-02 settled that one validator in this repository defines a valid generated device, run when the generator seals a device and when the evaluator opens one. Three modules could hold it: the generated family's grading module (`generated.py`), the set bundle module (`generated_sets.py`) or a module of its own.

## Decision

A new module, `benchmark_support/generated_device.py`, is the executable form of the interface document and nothing else. It has two entry points:
- `validate_device(device, *, image, mission, key=None)` checks every rule over the plaintext device: the identity part of its manifest entry (`id`, `level`, `framing`), the image, the public mission and, with a key, the key's own rules, its agreement with the mission and the framing, and what its references must show.
- `answer_key(key)` checks the key's own rules, for a grader that holds only the key, and the module derives the key's check ids.
- `validate_identity(device)` checks the identity part alone, which the manifest checks share.

Both raise ValueError naming the first defect. `seal_device` runs `validate_device` before writing anything. `open_device` runs it after decrypting; without `answer=True` it covers the inputs alone. `qualify` relies on `open_device` and keeps only the checks that need emulator runs. `generated.py` imports `answer_key` and the check ids from the new module, so the validator lies inside the generated family's grader identity.

## Considered

- **Inside `generated.py`.** No new module, but grading and the interface would share one module of more than 600 lines, and sealing would import the family's grading code.
- **Inside `generated_sets.py`.** The bundle module lies outside every grader identity, so a change to what the grader accepts would not change the identity. The grader would also import bundle and encryption code.

## Consequences

A rule change changes the generated family's grader identity, which voids every generated set's qualification, and changes the version of the schema it touches. The private generator calls `seal_device` and `write_manifest` from the installed package and may call `validate_device` in its own tests; the validator holds no device's design, so [ADR 0005](0005-keep-the-device-generator-private.md) holds. Grading code may not import the driver runtime (`interface_runtime`), so the validator reads the references' driver models as data.
