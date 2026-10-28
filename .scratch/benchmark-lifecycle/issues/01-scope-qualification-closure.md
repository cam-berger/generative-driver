# Scope qualification to each case's actual grading dependencies

Status: needs-triage

The current native calibration identity hashes every Python module in `benchmark_support`. Adding a case registration or a report-only module invalidates all software-case qualification, even when an existing case's grading behavior is unchanged. This refactor explicitly refreshes the supported cases and documents that cost.

Investigate a per-case grading closure with a pinned common evaluator and selected adapter/data dependencies. Keep all behavior-affecting code and dependency versions covered; avoid excluding executable registration code merely because most of its contents look like data. Case assets must remain unable to select arbitrary Python imports.

Use the content-pinned task/verifier approach in the [registration decision and pinned design inputs](../../../docs/adr/0001-benchmark-registration-and-selection.md) as input. Before implementation, demonstrate that adding an unrelated case preserves an existing admission while changing any of its actual grading dependencies invalidates it. Assess dynamic imports, module initialization, shared policies and historical evidence explicitly.
