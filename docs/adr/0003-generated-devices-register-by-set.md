# Generated devices register by set

Status: proposed in the [benchmark baseline spec](../../.scratch/benchmark-baseline/spec.md).

[ADR 0001](0001-benchmark-registration-and-selection.md) gives every case one code-owned `CaseRegistration`. The device generator makes about 200 cases across sealed scoring sets, development sets and leak-check batches; per-device declarations would put generated lines in source and need a code change for every batch. Instead, each generated set gets one code-owned registration that pins the SHA-256 of its set manifest, which lists each device's image, public mission and answer-key hashes. A device is otherwise a case, with its own image, mission, encrypted answer key and qualification. ADR 0001's rule holds: only the registered set's adapter code is imported, and device data only parameterises it.

## Consequences

A pinned set never changes; replacing the scoring set means a new registration and a new baseline. A device's identity is its set registration plus its manifest entry, so saved pins and reports keep both.
