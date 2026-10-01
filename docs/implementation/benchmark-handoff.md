# Benchmark expansion handoff — paused 2026-10-01

The 17 planned feature tasks were accepted through `0586acfd62911b583e10973e75d1dfbfc0fb2e0b` on `feat/bench`. The final branch review then identified three Important findings and one Minor: TQ9 diagnostic/final inventory separation and coverage; maintenance retry versus accepted repair accounting and live/offline agreement; trusted model-failure categories and suite advancement; expected MCP validation stderr capture.

This checkpoint is incomplete WIP, committed at the user's pause request. It is not final acceptance or current native qualification.

## Current changes and observed evidence

TQ9 now selects explicit private phase inventories for diagnostics. The pending v2 encrypted bundle has diagnostic, final and maintenance inventories for all three scenarios, with at least three varying sensor stimuli and output targets. Diagnostic and final values are separate. Original pending material is retained privately. Legacy v1 and baseline assets were not edited. Public v2 calibration remains pending.

Maintenance now records append-only attempts separately from successful initial detection and repair entries. Repair requires an accepted controller maintenance handoff routed to interpretation at an earlier revision. Live and saved evidence use a shared history rule; evidenced failures remain retained. Saved grading permits extra successful final revisions while retaining failed-final rejection. New private entries carry assignment identity and phase; sealed gates retain route. Historical successful sidecars without attempt history retain compatibility.

Known maintenance failures now carry model categories. Missing acquisition, capability and package artifacts also carry model categories. This audit is incomplete; remaining gate branches require inspection. Expected MCP validation stderr is captured in its test, but that amended test has not yet run.

Focused commands used `PYTHONPATH=src:tests .venv/bin/python -m unittest`:

- Phase coverage/separation plus control false-alarm category: RED, two expected failures; GREEN, 2 tests passed.
- Maintenance recovery: initial RED included an unsupported new helper argument; corrected behavior RED reproduced invented repair credit for unknown/host retries, erased false alarms and missing route verification. Extra-final RED also failed as expected. Final focused GREEN, 4 tests passed: control retries, retained false alarm, semantic route binding, and extra successful finals with failed-final rejection.
- Missing candidate artifacts: RED, three expected subtest failures; GREEN, 1 test passed with three stage subtests.
- `git diff --check`: passed at checkpoint.

No covering module run, full source test suite, native campaign or wheel campaign has run against this checkpoint. No model run, physical operation, push, PR or merge occurred.

## Required continuation

1. Read the final review, final-fix brief, rulings and constraints in the retained SDD handoff. Resume from this WIP commit, using `0586acfd` as the change-review base. Preserve private originals and all unsuccessful evidence.
2. Finish the exact category audit. Add the bounded two-slot regression proving an evidenced maintenance false alarm settles as model failure and advances only after cleanup; ambiguous, host and operator cases must remain blocked.
3. Complete maintenance producer/consumer validation. The focused extra-final regression passed, but direct authenticated export agreement, all affected gate combinations and actual repair transition coverage still need the covering module run and self-review. Check new-history coherence, accepted versus attempted identity, historical compatibility and frozen-final terminality.
4. Update old toy expectations affected by the private history change. In particular, the old evaluability test expects failed attempts under `initial`; it must inspect `attempts`. The new recovery class currently inherits the existing sealed-evidence test class, unintentionally rerunning those inherited tests; remove that duplication while retaining shared fixture helpers. Check the scripted TQ9 hidden-wrong mode after introducing multipoint diagnostics, and keep a genuine final-only failure fixture.
5. Run focused RED/GREEN for unfinished changes, then covering tests for evidence, scenarios, TQ9, suite reporting/service/manifest/store and interfaces. Run one full source suite, self-review and make the completed fix commit/report. Retain commands, counts and failures.
6. Root supplies one scoped re-review, then the already scheduled exact-code native refresh and source/installed-wheel Python 3.11/3.13 gates, plus baseline byte/hash checks. Refresh calibrated outputs only from measured final evidence. Current pending admission must remain closed until then.

Windows Server CI, Windows 11 native behavior, selected model execution and physical validation remain external or unauthorized gates. Historical native and wheel evidence does not qualify this checkpoint. No further campaign or publication is authorized by this handoff; the user paused work.
