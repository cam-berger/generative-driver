# Fresh reuse implementation record

Completed plan for the [six-stage specification](../specs/2026-10-03-reuse-endpoint.md). Existing Python configurator/adapters were retained; no new required dependency. Core targets macOS/Windows 11+. Implementation used the required subagent-driven workflow, failing behavior tests before fixes, focused checks and recorded dependent failures.

| Done | Task | Required result |
|---|---|---|
| [x] | Shared endpoint: configurator, benchmark, reporting, MCP/setup | Exactly six stages; package-only reuse; completion after acceptance; unsupported saved progress and failed reuse rejected; bounded repair/cancellation retained |
| [x] | Case execution and grading | Stable original image; diagnostic/final phases; six-gate offline/report agreement; unknown scenarios refused before startup; terminal final failure; honest failure/time/usage accounting |
| [x] | Assets and native qualification | Two references per family, meaningful mutants, authenticated current inputs/evaluator, stale-record rejection and nine distinct suite slots |
| [x] | Docs, packaging and integration | Consistent CLI/MCP/skills; wheel/sdist and installed Python 3.11/3.13 checks; replay, admission, score/compare, resource/privacy audits |

Private inventories were migrated/resealed evaluator-side, and matching manifests/version pins published together. Saved grades reproduced; old calibration failed current identity checks. Historical records remain in Git under their original meaning. No model trial, physical operation, merge or push was part of this refactor.

Whole-branch review corrected I1/I2/M1/M3 with regressions and fresh qualification; benign M2 build warnings were deferred. Final rereview corrected residual I1 fault priority at real boundaries. Parent acceptance at `49fafa78c9af8cf3bac8f7d60e1c0b7d4206a58c` left no Important finding open for that snapshot.

See [verification](../../verification.md) and the [frozen index](../../implementation/benchmark-final-verification.json) for measured scope and later follow-ups.
