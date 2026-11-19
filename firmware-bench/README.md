# Firmware bench

Firmware bench measures how well [Generative Driver](https://github.com/cam-berger/generative-driver) recovers a working driver from a device's firmware. It supplies a device, a firmware image and a mission to the tool through the tool's client interface, and grades the frozen driver once, after the run. The tool holds nothing of the benchmark.

| To | Read |
|---|---|
| Hold the answer keys and qualify the graders | [Evaluator's guide](docs/evaluator.md) |
| Add a case | [Case authoring](docs/case-authoring.md) · [Upstream corpus](docs/corpus.md) · [Generated-device interface](docs/generated-device-interface.md) |
| Accept a model lane | [Codex acceptance](docs/codex-acceptance.md) |
| Look up a term | [Glossary](CONTEXT.md) |
| Follow a decision | [Decision records](docs/adr/) |

The [native Modbus pilot](docs/case-authoring.md#native-zephyr-execution) uses the same registry, provider, six-stage runner and reports with an explicitly pinned native executable.

Recover a device interface, emit a portable driver, and have a fresh agent use it. Judge outputs/effects, not source-code similarity.

Current `assistive/1` runs follow acquire → interpret → probe → ground → emit → reuse. Stages hand off artifacts/notes; device differences drive repair; only safety, integrity, operator and host faults stop runs. See [design](https://github.com/cam-berger/generative-driver/blob/main/docs/design.md#assistive-protocol-amendment--2026-10-07).

## Profiles

| Case | Purpose |
|---|---|
| `setup-smoke` | Scripted installation/package replay, no inference |
| `tq9-v2` | Calibrated evaluation, device-driven repair and sealed final behavior |
| `tq9-v3` | Registered with its public mission. Its firmware and answer key are not in this repository yet, so it lists as `pending` |
| `sampled-sensor-v1` | Signed/scaled readings, acquisition and sequence counters |
| `parameter-store-v1` | Banks, pending/committed values, generation, commit and abort |
| `zephyr-modbus-v1` | Upstream Zephyr RTU sample: register/coil effects, unrelated-state preservation and recovery |
| `generated` | A device of a generated set. No set is registered, so it lists as `pending` |

Only `setup-smoke` runs with nothing but the two packages. It checks an installation: it validates a model, emits a package and replays a recorded reply. It starts no service, worker or grader. Every other case needs its evaluator password, the emulator, a qualification under the current grader, and a model or a saved candidate.

[Installed assets](src/firmware_bench/resources/bench/) pin stable original images, encrypted truth and evaluator versions. A listed or publicly `available` case has not authenticated admission for the installed implementation. The three-family core plans nine default trials and requires fresh native qualification before execution. The [catalog and execution gaps](docs/corpus.md#catalog-and-execution-gaps) describe future admissions.

The [upstream corpus](docs/corpus.md) adds seven lineages: one upstream case and six blocked lineages. Freeze all lineages for a pilot or select a debug subset; immutable plans retain excluded slots and reasons. Authenticate selected ready cases with `firmware-bench corpus check`, run them through the existing suite lifecycle and report measured success, selected coverage and full-catalog demonstrated coverage separately. See [case authoring](docs/case-authoring.md) to add cases and the [architecture decision](docs/adr/0001-benchmark-registration-and-selection.md) for the static registration boundary.

## Install and check

```sh
python -m pip install "generative-driver @ git+https://github.com/cam-berger/generative-driver"
python -m pip install -e .
firmware-bench cases
firmware-bench run --case setup-smoke --output "smoke output"
```

While this folder lies in the tool's repository, install the tool from the checkout instead: `python -m pip install -e ..`. Set up the tool's service and Codex as its [setup](https://github.com/cam-berger/generative-driver/blob/main/docs/setup.md) and [Codex](https://github.com/cam-berger/generative-driver/blob/main/docs/codex.md) guides say. Run the tests with `python -m unittest discover -s tests`. A graded run also needs Renode, at the release this page pins, and for building firmware an Arm compiler.

Use a new output directory. Expect `scripted-replay`, `model_benchmark: false`, `verdict: passed`. No account, hardware, native tools or password is needed. In `relocated package`, run `python -m driver replay`.

## Run an agent benchmark

Install [Renode](https://github.com/renode/renode/releases), [Ghidra](https://github.com/NationalSecurityAgency/ghidra/releases) and compatible Java. Native reference setup uses [Renode 1.16.1](https://github.com/renode/renode/releases/tag/v1.16.1), [Ghidra 12.1.3](https://github.com/NationalSecurityAgency/ghidra/releases/tag/Ghidra_12.1.3_build), Java 21 on macOS arm64; native Windows benchmark qualification remains separate. Authenticate/configure an exact runtime/model and total wall-clock budget:

```sh
generative-driver --home /operator/state/benchmark configure --executor codex --command /absolute/path/to/codex --provider openai --model YOUR_MODEL
firmware-bench --home /operator/state/benchmark cases
firmware-bench --home /operator/state/benchmark run --case tq9-v2 --scenario original --case-seed 0 --executor codex --budget-seconds 10800 --password-file /private/evaluator/tq9-v2.password --renode /absolute/path/to/renode --ghidra-home /absolute/path/to/ghidra --java-home /absolute/path/to/java-home --approve-emulator-tools
```

Replace placeholder paths with your own absolute paths. Quote paths containing spaces. Keep the same absolute global `--home` through configure, start, status, resume and report. Emulator approval covers assigned emulator tools only. Use `original` for V2 families. The `case_seed` controls evaluator episodes.

A detached bench harness receives password-file/Renode paths on stdin, pins the case, authenticates admission and starts the device. It freezes the case's inputs in its trial folder and supplies the device descriptor, the frozen firmware and the mission to the configurator, which knows no case. The configurator never receives the password or its path.

Save `trial_id` and `run_id`. `--wait` follows device shutdown and post-run grading/sealing. Records live at `<state>-bench/trials/<trial_id>`. Repeating `--request-id` follows the same trial without another device. To finish an unscored/unposted evaluation, or grade a formerly stopped trial whose run later completed:

```sh
firmware-bench --home /operator/state/benchmark trial-resume TRIAL_DIRECTORY --password-file /private/evaluator/tq9-v2.password --renode /absolute/path/to/renode
```

It requires the trial's password and Renode when grading remains; a sealed/drafted final is never graded twice. A stopped emulated run needs a matching healthy device attached by its harness. Only suite harnesses attach replacements today; start a new single trial instead of continuing a stopped one. If emulator stop was unconfirmed, stop it manually from its record; no command records that reconciliation, so the trial stays ungraded.

### Evidence, repair and calibration

Workers receive tasks/units/grants in `TASKS.json`, analysis paths and TCP wiring. Keep Ghidra outputs/caches/logs in the workspace; inferred physical UART settings stay in notes. TCP proves neither baud nor pins.

Probe/ground return instrument readings and candidate outputs, never grades. Readings can expose firmware-internal state, so results differ from interface-only protocols. Model/device differences and configurator validation defects permit at most two interpretation returns, then continue with the best revision. Ground runs the adapted model using mappings, probe records and the live journal. Repair preserves sealed prior candidates/replies/mappings/journals and writes new root outputs; passwords/answers remain excluded.

Use `validate_capabilities.py` with `CAPABILITY_FORMAT.md` before submission. Mapping errors become handoff notes without I/O. Trusted pre-transport refusals leave no uncertain effect; opening/interrupted execution may require reconciliation.

The sampled sensor's probe and ground assignments include `DEVELOPMENT_CONDITIONS.json`: five public reference inputs
at −12, 32, 0, 7.5 and 18.5625 °C, independent of the hidden final vectors. Select a listed `condition_id` on a live
`interface_execute` or `probe_run` call; omit it to retain the current source. The harness changes the source before
the call, without acquiring or resetting. Acquire a sample and compare its returned value with the instrument
reference and advancing sequence. Readings carry the condition's session, epoch and whether an acquisition occurred
since selection; a retained sample is not a measurement of the requested new input. Zero-only observations cannot
establish scale. The catalog is development evidence, not a new acceptance gate or a tool available to fresh reuse.

For the sampled sensor, probe and ground compare mapped outputs with the configurator's paired instrument readings
using the public `TASKS.json` quantities. Temperature allows `1e-9` °C of floating-point noise; sequence is exact.
Only successful live calls of the assigned model, on the recorded session and matching the mapped task arguments,
supply these differences. A batch probe compares its last sample with the after-reading. Retained reads compare
with the retained measurement; a new acquisition must change the measured sequence. Observed value/unit or sampling
differences enter the existing bounded interpretation repair with model/mapping hashes and dispatch evidence.
Worker claims and malformed/missing mappings alone do not trigger this route. Final evaluation is unchanged.

Owned-emulator recovery requires trusted completed model-failure evidence for every unresolved effectful dispatch, verified old-process disposal and independently verified replacement. The provider stores proof; the configurator commits receipt/session/lease with the handoff. Recovery uses a repair only when revising the model; exhausted repair budget alone does not forbid replacement. This applies to failed frozen-driver writes in reuse too. Retain failure evidence; attached sessions, unknown/host/operator faults, cancellation or unverified replacement stay blocked. Never replay the uncertain write.

Store update discards pending edits, changes only the requested committed cell, preserves others, advances generation once and ends idle. Final grading covers idle/same-bank/different-bank pending updates, commit, abort and complete rejection, with full state checks. The diagnostic contract runs only during native calibration. Rejection needs a private complete-response witness matching captured bytes; candidate framing is insufficient, equivalent requests remain valid.

Admission pins each case's grader identity: the hashes of the grading core and of its family's closure, its own entry in [`cases.json`](src/firmware_bench/resources/registrations/cases.json) and the `cryptography` version. A change to any of them, editing the case's entry included, voids that case's qualification and blocks comparison with its earlier runs; adding a case, editing another family's own modules, and runtime, package template, toolkit, skills and report changes do not. The core still holds some families' code, so editing that code voids every case's qualification ([grader identity](docs/case-authoring.md#grader-identity)).

Keep password files outside checkout/candidate inputs. Workers are not read-isolated: same-user workers could read control endpoints and act outside recorded calls. Endpoints expose no truth. Answer-key separation is password-based, not a sandbox.

### Inspect and score

```sh
generative-driver --home /operator/state/benchmark status RUN_ID
generative-driver --home /operator/state/benchmark result RUN_ID
firmware-bench --home /operator/state/benchmark report RUN_ID --output reports/trial.json
firmware-bench --home /operator/state/benchmark score reports/trial.json --evidence /private/evaluator/run-evidence.enc --password-file /private/evaluator/tq9-v2.password
firmware-bench --home /operator/state/benchmark compare reports/before.json reports/after.json
```

`benchmark-report/3` records the final `equivalent` verdict, and a pass is that verdict: the evaluator's graded equivalence of the frozen driver. The fresh mission is reported beside it and does not change it. Score with the trial's `run-evidence.enc` (`benchmark-run-evidence/2`): verify run/snapshot/case, accepted package and single final, then regrade its records offline. Sidecars seal artifact/note hashes, final/mission checks, public verdict and recovery receipts/journal hash. Pending, stopped or unscored runs score `unscored`.

Preserve accepted artifacts. Compare refuses different protocols, except suite reports on a shared scoring set, which `suite compare` pairs device by device.

## Replay a saved candidate

`--executor replay` exercises all stages without a model. A candidate directory contains `replay.json` (`replay-candidate/1`) and named model/reply/capability files from worker outputs, never case sources. Keep it outside installed resources and bench home.

`stages` lists ordered steps. `{"write":"model.json"}` copies without replacement, only in interpret/probe. Other stages use gateway tools. Arguments can reference assignment values with `{"$context":"model_dir"}` or the latest earlier result containing a key with `{"$result":"key"}`.

```sh
firmware-bench --home /operator/state/benchmark run --case tq9-v2 --scenario original --executor replay --replay-candidate /private/candidates/tq9-v2 --password-file /private/evaluator/tq9-v2.password --renode /absolute/path/to/renode --wait
```

The worker reads only its candidate/assignment and uses recorded gateway calls. The configurator copies the candidate at start, hashes it before/after each stage and stops on tampering. Reports record candidate hash, `replay-emulation`, `model_benchmark: false`; score verifies those against the seal and compare refuses model/replay mixtures.

For suites, add `"replay_candidates":{"tq9-v2":"/private/candidates/tq9-v2",...}` for every case and use `--executor replay`. Suite freeze stores each validated candidate by hash; children get independent copies unaffected by later edits. Reports/comparisons retain candidate hashes and replay labels. Replay never measures model performance.

## What fresh reuse must establish

Fresh reuse gets only the package/objective and permitted operating data, calls its public interface and leaves the required state. For V2 families, equivalence comes from sealed final episodes on a fresh emulator after the run; mission is a separate check.

A final grade never triggers repair. Retain uncertainty and reject incompatible saved workflows. Provided emulators run only during live calls, probe included, and are paused between calls. Pinned `time_policy.probe` text still says continuous time; that describes the case, not this provider. This is not real-time control. See Renode [time](https://renode.readthedocs.io/en/latest/advanced/time_framework.html) and [pause/start](https://renode.readthedocs.io/en/latest/basic/control.html).

## Run a repeated suite

The [Codex acceptance gate](docs/codex-acceptance.md) requires `equivalent_and_mission` for every planned trial; absent mission evidence is not success. Separate equivalence/mission counts remain available.

Default: three families × three repetitions, sequentially; 10,800 seconds per child, 108,000 per suite. Set copied `repetitions` to `1` for three trials. Select settings/budgets before inference.

```sh
generative-driver --home /operator/state/suite configure --executor codex --command /absolute/path/to/codex --model YOUR_MODEL
generative-driver --home /operator/state/suite service start
python -c "from pathlib import Path; from firmware_bench.benchmark import case_root; Path('pilot.json').write_text((case_root() / 'suites/development-pilot.json').read_text(encoding='utf-8'), encoding='utf-8')"
```

Create `/operator/private/suite-options.json` outside the checkout and worker directories. Its `home` must match the absolute global `--home`. Password/Renode paths go only to the detached harness on stdin, never suite tables or children:

```json
{
  "home": "/operator/state/suite",
  "renode": "/absolute/path/to/renode",
  "ghidra_home": "/absolute/path/to/ghidra",
  "java_home": "/absolute/path/to/java-home",
  "evaluator_password_files": {
    "tq9-v2": "/private/evaluator/tq9-v2.password",
    "sampled-sensor-v1": "/private/evaluator/sampled-sensor-v1.password",
    "parameter-store-v1": "/private/evaluator/parameter-store-v1.password"
  },
  "scoped_tool_approval": "emulator"
}
```

```sh
firmware-bench --home /operator/state/suite suite start --manifest pilot.json --executor codex --options /operator/private/suite-options.json --request-id pilot-approved-1
firmware-bench --home /operator/state/suite suite status SUITE_ID
firmware-bench --home /operator/state/suite suite events SUITE_ID --after 0
firmware-bench --home /operator/state/suite suite result SUITE_ID --offset 0 --limit 50
firmware-bench --home /operator/state/suite suite report SUITE_ID --output reports/pilot-approved-1 [--audit-path PATH]...
firmware-bench --home /operator/state/suite suite compare reports/before/suite.json reports/pilot-approved-1/suite.json
```

Save `suite_id`; identical request IDs reconnect. The harness provides devices, grades after runs and settles slots from the status/verdict the configurator recorded. Unscored/pending grades or stops block the suite; an unknown grade (no failed step, an undecided one) settles its slot. `/3` exports store each trial's `scored` outcome (pass, failure, unknown, fault, flagged, running or not_run) and lead with a pass line such as `7/9 pass, 1 unknown, 2 faults, 12 planned` (flagged, running and not-run trials add `, N flagged`, `, N running` and `, N not run` after it, each when there are some). Rows include scrubbed reason, stopped stage and repairs. Markdown adds frequent failing check IDs; status/events explain stops. `--audit-path PATH` (repeat for each path) reads the worker transcripts in the configurator home: a trial whose worker ran a command naming a forbidden path is flagged and not graded: the evaluator's equivalent count (`equivalence`) still holds its driver, and the Markdown says so, but it is no `equivalent_and_mission` success. A saved `/3` report is read back only if each label is the outcome its row shows. A path loses surrounding spaces and trailing `/` or `\`, so the tab-completed `/Volumes/Answer Vault/` finds a command that names the directory bare; a path that is then empty is refused. Results are paginated; preserve every slot.

Use the same owner for cancel and resume:

```sh
firmware-bench --home /operator/state/suite suite cancel SUITE_ID
firmware-bench --home /operator/state/suite suite resume SUITE_ID --options /operator/private/suite-options.json
```

After correction, the harness reattaches a stopped child or grades an unscored one once. Resume reads the password/Renode handles for the harness.

Stopped time counts toward both budgets. Extend suite total with `--suite-budget-seconds`/`--budget-reason`, child total with `--child-budget-seconds`/`--child-budget-reason`; neither extends the other. Preserve prior attempts/costs.

## Scorecard and comparison

A scorecard measures a suite afterwards from the records its runs kept; the system under test records nothing for it, so a stored suite can be measured again. Every trial counts in the pass rate, and every trial with a run is measured, whatever its outcome, except one still running: its records are still moving, so it counts in the pass line alone.

```sh
firmware-bench --home /operator/state/suite suite scorecard SUITE_ID --output reports/pilot-scorecard [--audit-path PATH]...
firmware-bench --home /operator/state/suite suite compare reports/before/suite.json reports/after/suite.json [--leak-check reports/fresh/suite.json]
```

`scorecard` makes the export that `suite report` makes in a temporary folder and writes only `scorecard.json` and `scorecard.md` to `--output`.

The two files say what they measure. `scorecard.json` holds the `suite_id`; whether the suite is `provisional` (still under way, or stopped before its slots settled), its `status` and its `reason`; the `system` (the protocol and the runtime, version, model, reasoning effort and revisions the suite froze) and the `conditions` (scoring set, evaluator revision, execution, evidence track, scope, child budget and its number of overrides, repair limit, tool approval and qualification). A scoring suite's reason names a trial and why it stopped, so its card has none. The Markdown names the system (with its execution and evidence track) and any scoring set under its pass line and gives the joint verdict (an equivalent driver and a successful fresh mission; `joint_verdict` in the JSON, beside each trial's own flag where trials are listed).

The card's `execution` and `conditions.execution` are two fields. A replay suite has both: the top level says how its trials were run (`replay-emulation`, because no model ran them), and `conditions.execution` is what the frozen experiment declared (`actual-agent-emulation`), which a comparison matches on. The Markdown's system line names the first when there is one.

The Markdown opens with the pass line, which names the trials still running and not run after the planned count (`7/9 pass, 1 unknown, 2 faults, 12 planned, 1 running, 1 not run`), each only when there are some. A provisional suite says `Provisional: suite <status>` (and its reason) on the second line; a replay suite then has a line saying no model ran. Below the identity it gives the pass line of each group (`family`, and `level` and `framing` where the rows carry them), the repairs and fresh-reuse claims, and for each stage the attempts, wall and worker seconds, tokens, worker actions, managed calls and device use, summed over the trials, with medians for times and tokens. A sum that only some trials could measure shows how many (`n/N`); `unknown` means none could. A suite that ran a scoring set, a fixed sealed set of generated devices that every compared system runs, gets aggregates only and names no trial, case or run; any other suite adds a row of key, outcome and metrics per trial to `scorecard.json`. No scorecard quotes an audit finding.

`compare` pairs two reports of one scoring set device by device, whatever their protocols; `--leak-check` takes the after system's saved report on a fresh batch of devices (see [leak check](CONTEXT.md)). Other reports compare as whole suites within one protocol.

Limits:

- After a repair, a stage's `wall_seconds` spans the time of the other stages in between.
- A stage skipped without an assignment has `wall_seconds` None.
- Resets without the trial's directory are a lower bound: they come from the configurator's events, plus the device provider's journal where the bench home still holds the directory.
- Worker actions are read only from Codex transcripts.
- Device exchanges are counted once, where the device made them: a replay (`probe_diff`, `emit_check`) is skipped, and an `emit_test_live` is counted in the live record its result names, found inside the run's folder.
- A stage's `exchanges` read `unknown` (None), not a lower bound, when it may have missed some: the live record is missing or unreadable; one of its calls left no result to count (the package printed no JSON object, its process timed out or the host failed around the call); or a call to a device tool started and never finished. A call whose process never started counts zero.
- Replies a live test saw the device reject are not counted as refusals.
- Reading the configurator database leaves a `-shm` and an empty `-wal` file beside it in a quiescent home.

## Metrics and comparisons

Reports retain stage attempts/acceptance, final verdict and package/mapping hashes, budgets, runtime/provider/model/settings, toolchain/skill/case/evaluator identities, environment, time, managed calls, usage and interventions. Artifact and notes hashes are sealed in the trial's run evidence; `driver_result` returns the notes. Tool time overlaps worker time; managed counts exclude arbitrary shell calls. Missing usage stays unknown, failed attempts consume resources.

Current assistive run reports record equivalence, and a pass is the evaluator's graded equivalence verdict; the fresh mission is reported beside it and does not change it, so corpus success needs neither it nor the six accepted handoffs. Preserve failures, exclusions, blocked and unrun slots. Corpus measured success uses only graded passes/failures; selected coverage retains all selected slots and demonstrated coverage retains the entire catalog. Match versions, inputs, modes, seeds, time policies and budgets; label multi-dimension changes as system comparisons. Report repetitions, uncertainty and time/usage coverage.
