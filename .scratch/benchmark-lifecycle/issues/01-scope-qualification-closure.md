# Scope qualification to each case's actual grading dependencies

Status: ready-for-agent

The current native calibration identity hashes every Python module in `benchmark_support`. Adding a case registration or a report-only module invalidates all software-case qualification, even when an existing case's grading behavior is unchanged. This refactor explicitly refreshes the supported cases and documents that cost.

Investigate a per-case grading closure with a pinned common evaluator and selected adapter/data dependencies. Keep all behavior-affecting code and dependency versions covered; avoid excluding executable registration code merely because most of its contents look like data. Case assets must remain unable to select arbitrary Python imports.

Use the content-pinned task/verifier approach in the [registration decision and pinned design inputs](../../../docs/adr/0001-benchmark-registration-and-selection.md) as input. Before implementation, demonstrate that adding an unrelated case preserves an existing admission while changing any of its actual grading dependencies invalidates it. Assess dynamic imports, module initialization, shared policies and historical evidence explicitly.

## Answer

### Resolved by plan 1

The [grader identity plan](../../../docs/superpowers/plans/2026-10-30-grader-identity.md) made the identity cover grading code only. `grader_identity()` is the SHA-256 of each Python module the grader imports, plus the `cryptography` version. The system under test (configurator, stage adapters, runtime, toolkit, package template) and report-only modules are outside it, so changing them no longer voids an admission or blocks a comparison. Admission checks it, each run is stamped with it as `evaluator_revision`, and the grade after the run and regrade check that stamp against it.

- **Closure.** It is read from import statements, in function bodies too, starting at the evaluator, evidence, registry, calibration and device-provider modules and at every family module the installed `registrations._CASES` names. `registrations.py` is hashed as the executable code it is. Case manifests, ciphertext and assets cannot add an import to it.
- **Dynamic imports.** `import_module` and `__import__` are not followed. The registry's family loads seed the closure; any other such load must be listed in `ENTRY_MODULES`.
- **Module initialization.** The `__init__.py` of each parent package below `generative_driver` counts, since it runs first. The top-level `generative_driver/__init__.py` does not.
- **Shared policies.** Helpers the grader shared with the system (digests, the tree hash, case roots, labels) moved into closure modules that the system imports. Reference building, stage adapters and legacy recorded grading moved out of the closure.
- **Historical evidence.** A sealed run regrades only under the grader identity it was sealed under, however the system has changed since. Runs sealed before 7f3b258 carry a whole-tree `evaluator_revision`, so they regrade only from a preserved checkout of their revision and are never paired with later runs. A record of the earlier `benchmark-evaluator-identity/2` fails admission with `identity:evaluator_identity`, so every admitted case is requalified once; the anchors' requalification is a runbook step.

### Still open: a per-family identity

The closure still covers every registered family and `registrations.py`, so adding or editing a code-registered case still voids every admission and changes `evaluator_revision`, which blocks comparison with every earlier run. The acceptance above, an unrelated case preserving an existing admission, is not met.
