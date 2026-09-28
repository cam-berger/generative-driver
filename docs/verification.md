# Verification record

## Baseline snapshot — 2026-09-28

On macOS arm64 with Python 3.13.12, a fresh wheel was installed in a separate environment and tested from outside the checkout. All 80 tests passed, including the distribution archive gate, real stdio MCP connections, background reconnect/cancellation, checked handoffs, replay, package relocation and scoring contracts. Scripted runtime fixtures make no model-performance claim.

The independent native TQ9 reference calibration passed probe, emission, fresh package reuse, seeded identity refusal and revised-package reuse. A deliberately incorrect decoder was rejected. Selected reference evidence is inside the password-encrypted groundtruth bundle.

The actual Codex baseline and final release verification will be recorded here after execution. This initial snapshot blocks physical evaluation until the managed evaluator connection hook is complete.

## Remaining qualification

Windows CI is configured but has not run in this local session. Goose has contract coverage but no live Goose trial. No physical BME280 baseline has been performed. Password encryption protects groundtruth at rest; worker filesystem isolation is unverified.
