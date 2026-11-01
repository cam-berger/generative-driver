# Scope qualification to each case's actual grading dependencies

Status: resolved

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

### Resolved by plan 3: a per-family identity

Plan 1's closure still covered every registered family and `registrations.py`, so adding or editing a case voided every admission and blocked comparison with every earlier run. On 2026-11-01 the user chose an identity per family, and the [generated-grader plan](../../../docs/superpowers/plans/2026-11-01-generated-grader.md) scopes each identity to a case.

- **Core.** The core is the closure of the five entry modules. It imports no family: it loads one by name through `registrations`.
- **Registrations.** Cases are registered in `resources/registrations/cases.json` (`benchmark-case-registrations/1`). This code-owned data is read relative to `registrations.py`, never through `case_root()`, which an environment variable redirects. A duplicate key, an unknown or missing field or a duplicate id is refused.
- **Case identity.** `grader_identity(case_id=...)` (`benchmark-grader-identity/2`) hashes the core, the closure of the case's native module and the case's own registration entry, plus the `cryptography` version. Without a case it covers every family and every entry.
- **Stamps and checks.** Admission, reference and upstream qualification, the run stamp, the grade after the run and regrade each use their own case's identity. A suite is stamped with each case's revision (`evaluator_revisions`) and compared by its `evaluator_revision`: the case's own revision when the suite has one case, otherwise the digest of the mapping. No two cases share an identity, since each holds its own scope and entry. Each trial is checked against its own case's revision. A suite or report saved before this change names one revision for every case.
- **Acceptance.** `test_a_case_keeps_its_identity_when_unrelated_code_or_cases_change` shows that adding a case or editing another family keeps an identity. `test_a_case_identity_changes_with_its_family_the_core_or_its_own_entry` shows that editing the case's family, the core or its own entry changes it.
- **Historical evidence.** A record under the earlier `benchmark-grader-identity/1` fails admission with `identity:evaluator_identity`, so every admitted case is requalified once more. Runs and suites stamped under it regrade only from a preserved checkout of their revision and are paired with no later run.
