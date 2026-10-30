# Scope qualification to each case's actual grading dependencies

Status: resolved

The current native calibration identity hashes every Python module in `benchmark_support`. Adding a case registration or a report-only module invalidates all software-case qualification, even when an existing case's grading behavior is unchanged. This refactor explicitly refreshes the supported cases and documents that cost.

Investigate a per-case grading closure with a pinned common evaluator and selected adapter/data dependencies. Keep all behavior-affecting code and dependency versions covered; avoid excluding executable registration code merely because most of its contents look like data. Case assets must remain unable to select arbitrary Python imports.

Use the content-pinned task/verifier approach in the [registration decision and pinned design inputs](../../../docs/adr/0001-benchmark-registration-and-selection.md) as input. Before implementation, demonstrate that adding an unrelated case preserves an existing admission while changing any of its actual grading dependencies invalidates it. Assess dynamic imports, module initialization, shared policies and historical evidence explicitly.

## Answer

Resolved by the [grader identity plan](../../../docs/superpowers/plans/2026-10-30-grader-identity.md). Admission, `evaluator_revision` and regrade check `grader_identity()`: the SHA-256 of each Python module the grader imports, plus the `cryptography` version. The system under test and report-only modules are outside it, so changing them voids no admission and blocks no regrade.

- **Closure.** It is read from import statements, in function bodies too, starting at the evaluator, evidence, registry, calibration and device-provider modules and at every family module `registrations.py` names. `registrations.py` is hashed as the executable code it is. Case manifests, ciphertext and assets cannot add an import to it.
- **Dynamic imports.** `import_module` and `__import__` are not followed. The registry's family loads seed the closure; any other such load must be listed in `ENTRY_MODULES`.
- **Module initialization.** A module's parent packages count, since their `__init__.py` runs first.
- **Shared policies.** Helpers the grader shared with the system (digests, the tree hash, case roots, labels) moved into closure modules that the system imports. Reference building, stage adapters and legacy recorded grading moved out of the closure.
- **Historical evidence.** A sealed run regrades only under the grader identity it was sealed under, however the system has changed since. A record of the earlier `benchmark-evaluator-identity/2` fails admission with `identity:evaluator_identity`, so every admitted case is requalified once; the anchors' requalification is a runbook step.
- **Not done: per-case closures.** One identity covers every registered family, so adding a registration or family still voids every admission.
