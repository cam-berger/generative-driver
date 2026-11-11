# Benchmark review, 2026-11-11

Status: needs-triage

Considerations for the benchmark from a read of the whole project on 2026-11-11, written down because the benchmark is moving to its own repository. Nothing here is accepted or implemented. The kit-side findings of the same review are built in the `kit-fixes` Run (`docs/features/kit-fixes/`).

Line numbers are those of `46c2b2e`. "Read" means the code path was read for this note; "reported" means a reviewing agent read it and it was not rechecked.

## Considerations

| Issue | In one line | Checked |
|---|---|---|
| [01](issues/01-no-held-out-result.md) | No result exists on devices the system was not developed against | read |
| [02](issues/02-pass-rate-leaves-out-crashes.md) | A crashed worker leaves the pass rate's denominator and gets a rerun | read |
| [03](issues/03-no-intervals-or-variance.md) | No rate carries an interval, and run-to-run variance is never measured | read |
| [04](issues/04-emulator-monitor-port.md) | The emulator's monitor is an open local port for the whole trial | read |
| [05](issues/05-audit-reads-nothing-as-clean.md) | The transcript audit treats a missing transcript as a clean one | read |
| [06](issues/06-earlier-probes-stay-readable.md) | An earlier system's probes of the same device stay readable in the runs database | reported |
| [07](issues/07-any-byte-voids-qualification.md) | Any byte changed in grading code voids every qualification | read |
| [08](issues/08-no-mutant-tests-refusals.md) | No required mutant gets a refusal or a silence wrong | read |
| [09](issues/09-fresh-reuse-unjudged.md) | Fresh reuse is not judged on generated devices, yet a joint verdict is printed | read |
| [10](issues/10-harness-size-and-operation.md) | The bench harness holds about fifteen jobs in one file and needs hand steps at every stop | reported |
| [11](issues/11-calibration-staleness-is-hidden.md) | A stale calibration reads as passed until the password is supplied | read |
| [12](issues/12-one-case-gets-extra-help.md) | One case's workers get an extra input file that the others do not | read |

Suggested order: 02 and 03 before the baseline runs, because its numbers are fixed once it does and both sit outside the grader identity. Then 04 to 06, which decide how far "workers see only the UART" can be claimed. 07 is cheapest while every calibration is already stale.

## The seam with the kit

What the move has to cut, measured at `46c2b2e`:

- Size. `benchmark.py` and `benchmark_support/` are 16,312 lines of Python; the rest of the kit is 17,179. About 62 of the 94 test modules are benchmark tests by name, and `resources/bench` adds 4.1 MB to the wheel.
- The kit imports the benchmark. `configurator.py` imports `benchmark_support` in 38 places, including at module level for `IntegrityError`, `digest` and the verdict labels, so those helpers belong to the kit, not the benchmark. `reporting.py` (6), `mcp.py` (4), `cli.py` and `replay.py` (1 each) import it too.
- The configurator holds benchmark behaviour: suite scheduling (the `_suite_*` methods and every `suite_*` request), `record_evaluation`, `attach_device` and the evaluation-token check. Seven of the fifteen MCP tools are benchmark tools.
- The benchmark imports the kit: `client` (15 places), `configurator` (14), `toolkit` (12), and `setup`, `replay`, `reporting`, `device_client` and `analysis`. `grader_identity.SYSTEM_MODULES` already lists which modules are the system under test, so it is the written boundary to start from.
- What the benchmark needs from the kit at run time is small: start a run with a device descriptor, an admission hash and a token hash; read status, events and handoffs; post one verdict; drive a suite's slots. Those requests are the interface the two repositories would share.
- Standards that must hold across the split: grading code imports nothing from the system under test, and passwords never reach the configurator or workers ([STANDARDS.md](../../STANDARDS.md)).

## What the benchmark showed about the kit

- Runs rediscover the same lessons. The sampled sensor needed the same temperature repair in three of three trials in both October studies. The parameter store needed the same rejection repair until the validator was taught the rule, after which it went from 1/3 to 3/3 with no repair ([verification](../../docs/verification.md)). Turning a repair that recurs into a check that runs before the device is touched is the pattern that worked.
- One trial spent a repair on a refusal the device was right to make: 283 worker seconds and 817k tokens ([lifecycle issue 03](../benchmark-lifecycle/issues/03-expected-refusal-repair.md), still open).
- Cost sits in reading. On 27 October, 99% of tokens were input and interpretation took 47% of them, against 34% of worker time. The `kit-fixes` Run takes this up.
- A run never records the Codex version its workers ran: `runtime_version` is read from a `version` key that `configure` never writes. The baseline plan assumes every trial records it.
- The serial test suite took 32 minutes on 2026-11-11 (2,054 tests, 4 skipped, all passing); the slowest tests are set-qualification and scoring-suite fixtures. The kit's own suite gets much shorter once they move.

## Already recorded elsewhere

- [Bench harness notes](../../docs/features/bench-harness/noticed.md): about fifty smaller findings from building the harness, most still open.
- [Benchmark baseline](../benchmark-baseline/spec.md), [lifecycle follow-ups](../benchmark-lifecycle/spec.md) and [effect labels](../effect-labels/spec.md).
- [Limits of the protocol](../../docs/design.md#assistive-protocol-amendment--2026-10-07): what the design already says workers can read.
