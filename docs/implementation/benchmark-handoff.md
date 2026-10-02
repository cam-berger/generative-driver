# Benchmark expansion handoff — 2026-10-01

The 17 planned feature tasks were accepted through `0586acfd62911b583e10973e75d1dfbfc0fb2e0b` on `feat/bench`. The final branch review found three Important issues and one Minor. Their combined fix wave began at that base, paused at WIP commit `71c84a33`, and resumed with the user's instruction to finish.

## Fixes ready for scoped review

TQ9 v2 now uses separate private diagnostic and final inventories. All three scenarios have at least three varying sensor stimuli and effect targets in diagnostic, final and maintenance phases, including signed sensor coverage. A constant decoder fails the public toy diagnostics. Original pending material is retained privately; public calibration remains pending. All eight legacy and baseline file hashes still match.

Maintenance retains attempted observations separately from successful detection and repair entries. Repair credit requires an accepted controller route to interpretation at an earlier revision. Unknown claims can recover explicitly; host uncertainty requires explicit reconciliation. Successful controls earn no repair credit. Live results, direct qualification and authenticated regrading agree, including extra successful final revisions. Evidenced failures, resources and every frozen final remain retained; a failed frozen final remains terminal.

Independently established candidate failures carry the model category. The bounded two-slot regression confirms a measured false alarm finishes as a failed measurement and advances only after cleanup. Host, operator and ambiguous evidence remain blocked. The audit covers the same category boundary in acquisition, interpretation, probe, ground, emit and reuse. Expected MCP pagination rejection stderr is captured and asserted.

## Observed verification

Using Python 3.13.12 and `PYTHONPATH=src:tests .venv/bin/python -m unittest`:

- Covering modules: 144 tests passed in 59.491 seconds.
- Final acquisition category checks: 4 tests passed; calibration fixture checks: 24 tests passed.
- Full source discovery: 411 tests in 79.051 seconds, OK with one expected unset-`GD_TEST_WHEEL` archive skip.
- `git diff --check` passed; all eight legacy/baseline SHA-256 commitments matched.

Focused RED/GREEN and unsuccessful runs are retained. The first covering run found an incorrect stderr-marker expectation. The first full run found three calibration tests with the old literal false-alarm category; the fixture was updated to the new producer contract, and the affected module and full suite then passed. No admission or validation check was weakened.

The scripted controller checks use public toy peers and subprocess workers. They establish lifecycle and evidence behavior, not measured native firmware or model performance.

## Remaining delivery gates

Root supplies one scoped re-review of `0586acfd..HEAD`. Then the final verifier runs the scheduled exact-code native refresh and source/installed-wheel Python 3.11/3.13 gates, preserving all attempts and checking baseline bytes again. Calibration must remain pending until matching native evidence is produced; historical native measurements do not qualify this head. Full fix reports, logs, private originals and commitments are retained outside the scratch workspace.

Windows Server CI, Windows 11 native behavior, selected-model execution and physical validation remain external or separately authorized gates. This fix wave ran no native or wheel campaign, model or physical operation, push, PR or merge.
