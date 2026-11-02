# Generated devices register by set

Status: proposed in the [benchmark baseline spec](../../.scratch/benchmark-baseline/spec.md).

[ADR 0001](0001-benchmark-registration-and-selection.md) gives every case one code-owned `CaseRegistration`. The device generator makes about 200 cases across sealed scoring sets, development sets and leak-check batches; per-device declarations would put generated lines in source and need a code change for every batch. Instead, a generated set is a bundle outside this repository: a plaintext manifest and, per device, an encrypted public-inputs file and an encrypted answer key. The set's registration pins the SHA-256 of the manifest, which lists each device's image, public mission and answer-key hashes. A device is otherwise a case, with its own image, mission and encrypted answer key; its set holds one qualification record for all its devices. ADR 0001's rule holds: only registered adapter code is imported, and device data only parameterises it.

Set registrations are code-owned data in `resources/registrations/sets.json` (set name → manifest SHA-256), installed and read beside `cases.json`. They sit outside every grader identity, so registering a set or a leak-check batch voids no case's admission; a comparison carries the manifest hash, which the identity does not. The generated family's adapter, the code that grades any generated device from its decrypted answer key, is one code-owned entry in `cases.json`.

## Consequences

A pinned set never changes; replacing the scoring set means a new registration and a new baseline. A device's identity is its set registration plus its manifest entry, so saved pins and reports keep both.
