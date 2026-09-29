# Selected baselines

| Run | Execution | Result | Interpretation |
|---|---|---|---|
| [Codex TQ9 — 2026-09-28](codex-tq9-2026-09-28.md) | Actual Codex agents, native macOS, Renode observations | All seven final gates passed, including drift repair and fresh reuse | One development run across six executing snapshots, with host repairs, one model repair and a user pause. |

Each baseline includes reviewed `benchmark-report/1` measurements, a readable summary and execution context. Keep failed stages, unknown usage, runtime/model versions, case/toolchain hashes, budget and environment. Measured values may be public; oracle expectations and tolerances, raw transcripts, credentials, absolute user paths, vendor documents and bulk historical runs are excluded.

Scripted setup/reference calibration does not belong in a model-performance table. The encrypted groundtruth bundle preserves selected reference observations. Follow [the benchmark guide](../README.md) to run and compare new trials.
