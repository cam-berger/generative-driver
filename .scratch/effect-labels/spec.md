# Worker effect labels decide grants and uncertainty

Status: resolved

The gateway checks the operator's effect grant against the effect a model declares for each operation, and marks a dispatch uncertain only when that declaration is write or actuate. Workers write those declarations. On UART, TCP and USB bulk/interrupt nothing checks them, so a state-changing request labelled `read` runs under the default read-only grant and is never marked uncertain, even when it is interrupted after reaching the device. Found by code reading at `origin/main` 41469aa on 2026-10-30; the label gap is reproduced below.

## Evidence

Line numbers are those of 41469aa.

- The runtime runs any operation labelled `read` without a grant (`src/interface_runtime/engine.py:767`). The gateway reads the same label from `model_dir/model.json` (`operation_effects`, `src/generative_driver/configurator.py:342`), and `_dispatch` sets `runs.uncertain` and a `pending_effects` row only for write/actuate (`configurator.py:3543`–`3594`). New runs and MCP `driver_start` default to `["read"]` (`configurator.py:2113`, `mcp.py:38`).
- Interpret writes the assigned model. Probe and ground workers may run their own variants anywhere in their workspace (`configurator.py:3485`–`3491`), which the stage lists as `other_model_runs`. The identity operation runs before every operation and must be labelled `read` (`engine.py:474`), so its request is unchecked too.
- Classification is structural only for register models, where a `reg_write`/`raw_write` step counts as a write (`configurator.py:351`), and for USB control transfers, by direction bit (`engine.py:318`). Both bound data direction, not side effects: clear-on-read registers and vendor IN requests can still change state.
- A restarted run is held uncertain only if write or actuate was granted (`configurator.py:1250`). Instrument comparisons cover only sampled-sensor-v1 calls of the assigned model's mapped tasks (`benchmark_support/instrument_evidence.py:29`, `:64`).
- Reproduction, at `c817348`: `DeviceRepairTests.test_a_state_changing_request_a_worker_labels_a_read_is_marked_uncertain_before_it_reaches_the_device`. A scripted probe worker under the default grant runs a variant whose `heater_on` is labelled `read` and sends `H1`. The loopback line device receives `ID`, then `H1`, and never answers, so the request is interrupted after the transport opened. The test expects the dispatch marked uncertain and fails: `(effectful, uncertain_effect)` is `(False, False)`, and the run completes. The [decision](#decision) replaced it.
- Time of check to time of use (TOCTOU), reproduced by the (a) test under [Result](#result): the gateway reads `model.json` to classify and validate, then `interface_execute` and `probe_run` read it again (`_toolchain/interface.py:50`, `:96`, `:124`). The digest taken before dispatch decides which model ran (`_ran_assigned_model`, `configurator.py:447`), and nothing compares it with the `model_sha256` the tool reports. A file rewritten during a call runs bytes other than those classified and attributed: with a write grant, a declared write runs unmarked; under any grant, a run of other bytes can count as the assigned model's.

## Options

**(a) Run a snapshot.** Before classification the gateway copies the model directory, bounded, into a configurator-owned directory, then classifies, validates, hashes and runs that copy, whose digest still identifies an assigned input. What was classified is then what ran and what is attributed, and a tool-reported hash that differs is an integrity stop. `emit_test_live` already runs a verified snapshot. This closes the TOCTOU only; labels stay claims. Workers are not write-isolated, so it removes the workspace race, not deliberate tampering with configurator files. A moderate gateway change.

**(b) State the limit.** In `docs/design.md` (Limits of this protocol) and `docs/support.md`: grants bound declared effects; only register writes and USB control direction are checked structurally; elsewhere a `read` label is its author's claim, so a read-only grant does not show the device was left unchanged, and an interrupted read is not uncertain. Pin the wording with a `LIMITS` pattern in `tests/test_assistive_wording.py`. Small, and needed with any other option.

**(c) Observe state changes on provided emulators.** The configurator already reads the instrument before and after each live call. When a dispatch labelled `read` changes a quantity its family declares as state, record it: for the assigned model, a device difference that returns the model to interpret; for a worker's variant, a stage note. Nothing stops, and nothing is uncertain, because the after-reading observed the effect. This generalizes sampled-sensor's `retained read acquired a new sample` (`instrument_evidence.py:98`). Each family must declare its state quantities, since readings mix state with measurements. The code lives under `benchmark_support`, so it changes the evaluator implementation and needs a fresh native calibration before the nine-trial test. Emulated benchmarks only.

**(d) Stop trusting read labels for the uncertain-write record.** Mark every live dispatch without a structural classification as a pending effect, reconciled as writes are now (success, or a refusal declared before contact). It is the only option that turns the committed test green as written. Every read that fails after contact on UART, TCP or USB bulk (timeouts, framing errors, wrong replies: the normal course of probing) would then refuse later live calls and stop the run for safety until the operator reconciles it, turning the main repair input into new stops.

Not proposed: limiting live probe and ground calls to the assigned model. It removes the variant route but takes an exploration tool from workers, and interpret's labels remain claims.

## Recommendation and test plan

Implement (a) and (b) now, (c) as its own ticket timed with the next calibration, and not (d). With that choice the committed test is replaced, not made green: (a) gets a red test that rewrites the model from the test's `call_tool` stand-in, which runs after classification; (b) gets a red wording pattern; (c) would reuse the mislabelled `H1` variant on a provided emulator whose state quantity changes, expecting a device difference or a note. Choosing (d) keeps the test as written.

## Decision

On 2026-10-30 the user chose (a) and (b). (c) waits for the next calibration as [issue 01](issues/01-observe-read-state-changes.md); (d) is not pursued. The label test from `c817348` could pass only under (d), so it was replaced.

## Result

- (a): a live `probe_run` or `interface_execute` runs a copy of its model directory that the gateway makes under `runs/<run>/snapshots/` before it reads the model, and removes when the call returns (`_copy_model`, `_dispatch` and `_tool` in `configurator.py`). The copy keeps the directory digest, so attribution and fresh reuse's frozen-driver check are unchanged. A directory holding a symlink is refused before contact. `DeviceRepairTests.test_a_model_the_worker_rewrites_during_a_call_runs_as_the_gateway_read_it` rewrites the worker's variant after the gateway's read: without the copy the device received the rewritten write `H1` while the record said read; with it, the classified query `H?`, and no copy is left behind.
- Narrower than option (a) as written: no size bound, because the gateway already reads every byte of the directory to hash it and an assigned model's directory holds whatever interpret wrote; and no comparison with the tool's reported hash, because the copy sits outside the workspace under a fresh name, so changing it means tampering with configurator files, the documented limit while no sandbox exists. `emit_test_live` keeps its own verified package snapshot, but the gateway still classifies its effects from the package before that snapshot: [issue 02](issues/02-pin-live-test-classification.md).
- (b): the limit is stated in `docs/support.md` and the design's "Limits of this protocol", and the copy in its "The device drives repair". `EFFECT_LABELS` in `tests/test_assistive_wording.py` pins the limit; it failed before the docs changed.
- Full suite on macOS with Python 3.13: 1016 tests in 492 s, OK, with the four skips of 41469aa.
