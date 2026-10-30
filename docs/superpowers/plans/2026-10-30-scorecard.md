# Scorecard and Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Count trials by the agreed outcome rules, compute every scorecard metric from records trials already keep, flag workers that touch forbidden paths, and compare two systems device by device on a shared scoring set.

**Architecture:** One `outcomes` module classifies a trial and computes the pass rate, and suite and corpus reports both use it. An `audit` module scans Codex transcripts for forbidden paths during suite reporting. A `scorecard` module reads a finished run's events, worker records, transcripts and device exchanges read-only, then aggregates them per stage. A `pairing` module compares two scoring-suite reports device by device, with McNemar's exact test and a leak check. The evaluator's verdict drops the fresh-reuse checks, which stay in the record's `mission`.

**Tech Stack:** Python 3.11+ standard library (`sqlite3` read-only URI, `json`, `math.comb`), `unittest`.

**Spec:** [Benchmark baseline spec](../../../.scratch/benchmark-baseline/spec.md), decisions 1, 2, 4, 12, 13, 17, 19, 20 and 21. Plan 2 of 5; requires [plan 1](2026-10-30-grader-identity.md) merged (`final_record(..., protocol=...)`). The scoring-suite fault rerun (decision 20) and the step grader's unknowns (decision 7) arrive in plan 3; this plan reads them when present.

## Global Constraints

- Native macOS and Windows 11+: portable paths, no hardcoded user paths, outputs outside installed resources.
- Each behavior change: write one failing test, watch it fail, implement, watch it pass ([AGENTS.md](../../../AGENTS.md)).
- The system under test records nothing new for the scorecard (decision 12). Read the configurator database only through `file:<path>?mode=ro` URIs, as `mcp_reuse.owner_evidence` does (`benchmark_support/mcp_reuse.py:92-106`), and never construct a `Controller`, which writes on start.
- Missing telemetry stays `None`, never zero, as `reporting._usage` already treats it.
- A scoring suite's outputs carry aggregates only: no per-trial rows, transcripts or check ids leave the bench harness (decisions 4 and 21).
- Pass line format, verbatim from the spec: `7/9 pass, 1 unknown, 2 faults, 12 planned`.
- Run tests from the repository root: `env -u GD_TEST_WHEEL -u GD_TEST_SDIST PYTHONPATH=src "$GD_PY" -m unittest discover -s tests -p <module>.py [-k <test>]`.
- Work on branch `benchmark-baseline/scorecard`; commit after each task, ending each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A trial stopped by an operator stop whose report lacks `effective_budget_seconds`, as older exports do, must stay a fault rather than be guessed into a failure (Task 2).
2. A worker that wrote no transcript, or wrote non-JSON lines (`unparsed_stdout`), must not stop the count. Its attempt still counts from `stage.assigned`, since a crashed worker leaves no `reports` row (Task 5).
3. A device present in only one of the two compared reports must be listed as excluded, never silently dropped (Task 6).
4. A forbidden path written with backslashes or different case, as on Windows, must still be flagged (Task 4).
5. A scoring suite's scorecard must contain no per-trial rows in either JSON or Markdown (Task 7).

---

### Task 1: Fresh reuse leaves the verdict

**Files:**
- Modify: `src/generative_driver/benchmark_support/evaluator.py` (`final_record`, :342-360), `src/generative_driver/benchmark_support/evidence.py` (`regrade_v3` mirror, :395-401)
- Docs: `docs/design.md`, the "One final verdict" bullet (about :103): fresh reuse is reported beside the verdict
- Test: `tests/test_evaluation.py`, beside the generated-reuse cases at :559 and :636

**Interfaces:**
- Produces: `final_record`'s checks are the behavior checks only, and `equivalent` is true when they all pass. When `final['generated_reuse_required']` is set, the record's `mission` stays within `{name, passed, reason, fault}` (`configurator._evaluation_errors`, :718-731). `passed` is then true only if the mission passed and `generated_reuse_verified` holds. A failed proof gives `reason = 'Missing or inconsistent owner proof of generated MCP reuse'`.

- [ ] **Step 1: Write the failing test** `test_fresh_reuse_is_reported_beside_the_verdict`. Build `final` with passing behavior checks, `generated_reuse_required=True` and `generated_reuse_verified=False`, as the cases at :559 do, then:

```python
made = final_record(final, mission={'name': 'fresh_worker_mission', 'passed': True}, protocol='assistive/1')
self.assertTrue(made['equivalent'])
self.assertNotIn('fresh_worker_mission', [c['id'] for c in made['checks']])
self.assertEqual((made['mission']['passed'], made['mission']['reason']),
                 (False, 'Missing or inconsistent owner proof of generated MCP reuse'))
```

- [ ] **Step 2: Run it.** Expected: FAIL, `equivalent` is False.
- [ ] **Step 3: Move both reuse checks out of `checks` and `diff` into `mission`** in `final_record`; `regrade_v3` keeps calling it, so the offline regrade matches.
- [ ] **Step 4: Run `test_evaluation.py`, `test_benchmark_mcp_reuse.py` and `test_owner_generated_integration.py`.** Expected: pass once assertions that expected reuse inside the verdict are updated to read `mission`.
- [ ] **Step 5: Update `docs/design.md`, then commit** "Report fresh reuse beside the equivalence verdict".

### Task 2: One outcome rule for every trial

**Files:**
- Create: `src/generative_driver/benchmark_support/outcomes.py`
- Test: `tests/test_outcomes.py`

**Interfaces:**
- Produces:
  - `classify(trial: dict, report: dict | None, *, flagged: bool = False) -> str`, returning one of `pass`, `failure`, `unknown`, `fault`, `flagged`, `running`, `not_run`. It reads `trial['run_id']`, `trial['status']` and `trial['outcome_category']`, and the report's `workflow_status`, `equivalence`, `equivalence_status`, `elapsed_seconds` and `effective_budget_seconds`. Rules apply in order:
    1. `not_run` when no `run_id`.
    2. `flagged` when `flagged`.
    3. `running` when the status is pending, launching, running or queued, or `equivalence_status` is `pending`.
    4. A stopped run (status `blocked`, `failed` or `cancelled`) is a `failure` for categories `safety`, `integrity` and `model`, and for an `operator` stop with `elapsed_seconds >= effective_budget_seconds`. Any other stop is a `fault`.
    5. Equivalence status `graded`: `pass` when `equivalent` is true, else `failure`. `no-driver` is a `failure`. `unknown` is `unknown`. Anything else (`unscored`, missing) is a `fault`.
  - `pass_rate(outcomes: list[str]) -> dict` returns `{'passes', 'failures', 'unknown', 'faults', 'flagged', 'running', 'not_run', 'planned', 'rate'}`, with `rate = passes / (passes + failures)` or `None`.
  - `pass_rate_line(rate: dict) -> str` returns `f"{passes}/{passes + failures} pass, {unknown} unknown, {faults} faults, {planned} planned"`, plus `f", {flagged} flagged"` when any.

- [ ] **Step 1: Write the failing tests**, one per rule:

```python
done = {'run_id': 'r', 'status': 'finished', 'outcome_category': 'completed'}
graded = lambda equivalent: {'workflow_status': 'completed', 'equivalence_status': 'graded',
                             'equivalence': {'status': 'graded', 'equivalent': equivalent}}
stopped = lambda category: {'run_id': 'r', 'status': 'blocked', 'outcome_category': category}

def test_grades(self):
    self.assertEqual([classify(done, graded(True)), classify(done, graded(False))], ['pass', 'failure'])
    self.assertEqual(classify(done, {'workflow_status': 'completed', 'equivalence_status': 'no-driver',
                                     'equivalence': {'status': 'no-driver', 'equivalent': False}}), 'failure')
def test_spent_budget_is_a_failure_and_other_operator_stops_are_faults(self):
    spent = {'workflow_status': 'blocked', 'elapsed_seconds': 10800, 'effective_budget_seconds': 10800}
    self.assertEqual(classify(stopped('operator'), spent), 'failure')
    self.assertEqual(classify(stopped('operator'), {**spent, 'elapsed_seconds': 100}), 'fault')
    self.assertEqual(classify(stopped('operator'), {'workflow_status': 'blocked'}), 'fault')
def test_self_caused_stops_fail_and_environment_stops_are_faults(self):
    self.assertEqual([classify(stopped(c), {'workflow_status': 'blocked'}) for c in ('safety', 'integrity', 'model', 'host')],
                     ['failure', 'failure', 'failure', 'fault'])
def test_missing_grade_is_a_fault_pending_is_running_unknown_is_unknown(self):
    self.assertEqual([classify(done, {'workflow_status': 'completed', 'equivalence_status': s, 'equivalence': None if s != 'unknown' else {'status': 'unknown', 'equivalent': None}})
                      for s in ('unscored', 'pending', 'unknown')], ['fault', 'running', 'unknown'])
def test_flagged_and_not_run(self):
    self.assertEqual((classify(done, graded(True), flagged=True), classify({'run_id': None}, None)), ('flagged', 'not_run'))
def test_pass_rate_line(self):
    rate = pass_rate(['pass'] * 7 + ['failure'] * 2 + ['unknown'] + ['fault'] * 2)
    self.assertEqual((rate['rate'], pass_rate_line(rate)), (7 / 9, '7/9 pass, 1 unknown, 2 faults, 12 planned'))
```

- [ ] **Step 2: Run them.** Expected: ERROR, no module `outcomes`.
- [ ] **Step 3: Implement the three functions** in `outcomes.py`.
- [ ] **Step 4: Run them.** Expected: 6 pass.
- [ ] **Step 5: Commit** "Classify trials by one outcome rule".

### Task 3: Suite and corpus reports lead with the pass rate

**Files:**
- Modify: `src/generative_driver/benchmark_support/suite_reporting.py` (`aggregate_suite` :250-335, `_saved_report` :350-375, `_summary` :517-550), `src/generative_driver/benchmark_support/corpus.py` (`report_corpus` :322-434)
- Docs: `docs/benchmark-corpus.md`, the `measured_success` row
- Test: `tests/test_assistive_reports.py` (`assistive_fixture`, :367), `tests/test_corpus.py` (`measured_fixture`, :19)

**Interfaces:**
- Consumes: `classify`, `pass_rate` and `pass_rate_line` from Task 2.
- Produces:
  - Assistive suites export `benchmark-suite-report/3`: `/2` plus `pass_rate` (from `pass_rate`) and a `scored` field on every trial row. `_saved_report` validates `/2` and `/3` and recomputes `pass_rate` from the rows.
  - `_summary`'s first line is the pass line followed by the protocol, and the trial table gains a "Scored" column.
  - The corpus counts a row `passed` when `classify` gives `pass` and `failed` when it gives `failure`. The accepted-stages and mission conditions at :372-379 go, and the limitations sentence at :427-434 becomes "Measured success counts a pass when the evaluator grades the frozen driver equivalent; fresh reuse is reported beside it."

- [ ] **Step 1: Write the failing tests**:

```python
def test_assistive_suite_reports_the_pass_rate(self):  # test_assistive_reports.py
    suite, trials, reports, experiment = assistive_fixture()
    result = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
    self.assertEqual({k: result['pass_rate'][k] for k in ('passes', 'failures', 'unknown', 'faults', 'running', 'not_run', 'planned')},
                     {'passes': 1, 'failures': 2, 'unknown': 0, 'faults': 4, 'running': 1, 'not_run': 1, 'planned': 9})
    self.assertTrue(_summary({**result, 'status': 'completed', 'reason': None}).startswith('1/3 pass, 0 unknown, 4 faults, 9 planned'))

def test_equivalent_driver_counts_without_fresh_reuse_or_accepted_stages(self):  # test_corpus.py
    # plan and suite as measured_fixture (:19) builds them, with one equivalent trial whose mission
    # passed is False and whose reuse stage was not accepted
    self.assertEqual(report_corpus(plan, suite)['counts']['passed'], 1)
```

- [ ] **Step 2: Run them.** Expected: FAIL, `KeyError: 'pass_rate'`, and the corpus counts the trial as unscored.
- [ ] **Step 3: Implement both report changes** and update the docs row.
- [ ] **Step 4: Run `test_assistive_reports.py`, `test_suite_reporting.py` and `test_corpus.py`.** Expected: pass once assertions on the old headline and the old corpus rule are updated.
- [ ] **Step 5: Commit** "Lead suite and corpus reports with the graded pass rate".

### Task 4: Flag workers that touch forbidden paths

**Files:**
- Create: `src/generative_driver/benchmark_support/audit.py`
- Modify: `suite_reporting.report_suite` (:553) and the `suite report` parser (`benchmark.py:127`, handler :245)
- Test: `tests/test_audit.py`

**Interfaces:**
- Produces:
  - `audit_commands(run_dir: Path, forbidden: tuple[str, ...]) -> list[dict]` scans every `_agent_events.jsonl` under `run_dir`. It flags each `item.completed` item of type `command_execution` whose `item.command` contains a forbidden path once backslashes become slashes and both sides are case-folded; a list command is joined with spaces. It returns `[{'transcript': relative_path, 'command': command[:300]}]`. Lines that are not JSON are skipped.
  - `report_suite(suite_id, output=None, *, audit_paths=())` adds `audit` findings to each row and passes `flagged=bool(findings)` to `classify`. Its CLI flag is `--audit-path`, repeatable.

- [ ] **Step 1: Write the failing tests**:

```python
def test_flags_a_command_reading_the_vault(self):
    self.write(run / 'work/probe/a1/_agent_events.jsonl', [
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'cat "/Volumes/Answer Vault/evaluator/x.password"'}},
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'ls workspace'}}], extra_line='not json')
    self.assertEqual([f['transcript'] for f in audit_commands(run, ('/Volumes/Answer Vault',))], ['work/probe/a1/_agent_events.jsonl'])
def test_windows_paths_and_case_still_match(self):
    self.write(run / 'work/ground/a2/_agent_events.jsonl', [
        {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': ['type', 'C:\\Vault\\GD\\key.txt']}}])
    self.assertEqual(len(audit_commands(run, ('c:/vault/gd',))), 1)
```

- [ ] **Step 2: Run them.** Expected: ERROR, no module `audit`.
- [ ] **Step 3: Implement `audit_commands`**, then thread `audit_paths` through `report_suite` and the CLI.
- [ ] **Step 4: Run `test_audit.py` and `test_suite_reporting.py`.** Expected: pass.
- [ ] **Step 5: Commit** "Flag worker commands that touch forbidden paths".

### Task 5: Trial metrics from retained records

**Files:**
- Create: `src/generative_driver/benchmark_support/scorecard.py`
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Produces `trial_metrics(home: Path, run_id: str, *, trial_dir: Path | None = None) -> dict`, schema `benchmark-trial-metrics/1`. It holds `run_id`, `repairs` (the run's `progress.repairs`), and `claim` with `claimed_success`, `mission_passed` and `agrees`. Under `stages`, each stage holds `attempts`, `wall_seconds`, `worker_seconds`, `managed_calls` (`{name: count}`), `worker_actions` (`{command_execution, mcp_tool_call, file_change}`), `device` (`{exchanges, writes, refusals, resets}`) and `tokens` (`{input, cached_input, output, reasoning_output, total}`). Definitions:
  - `attempts`: `stage.assigned` events for the stage. `wall_seconds`: the last `stage.accepted` or `stage.skipped` time minus the first `stage.assigned` time, or `None`.
  - `worker_seconds`: the sum of `elapsed_seconds` over the stage's `reports` rows. `tokens`: those rows' usage summed, with `total = input + output`; every counter is `None` unless every attempt reported usage.
  - `managed_calls`: `tool.started` events by `data.name`.
  - `worker_actions`: `item.completed` events by `item.type` in the stage's transcripts. These are `runs/<run>/work/<stage>/*/_agent_events.jsonl`, plus for interpret `runs/<run>/preparation/*/interpret/attempt*/` and `runs/<run>/benchmark/tools/interpret/attempt*/`.
  - `device.exchanges`: transcript entries with `op` `exchange` or `usb_control` and status `completed`, inside `tool.finished` `data.result`, including each `results[]` item. `device.writes`: `tool.started` with `data.effectful`. `device.refusals`: `tool.refused` events, plus `tool.finished` with `data.refused_before_open`, plus entries with `device_rejected`. `device.resets`: `emulator.recovered` events, plus `<trial_dir>/device/journal.jsonl` rows of kind `replaced`, each attributed to the stage active at its time.
  - `claim`: `claimed_success` is true when the reuse report of the latest accepted reuse handoff has status `completed` and every check is `pass`. `mission_passed` is the run's `equivalence.mission.passed`. `agrees` compares the two when both are known.

- [ ] **Step 1: Write the failing test** `test_metrics_come_from_a_scripted_runs_records`. Drive a scripted run with a temporary home, following `tests/test_configurator.py:1140-1175` (`Controller(home)`, `scripted_stages`, `controller.call('start', ...)`, `wait_stopped`, toolkit patched). The probe worker's `work` writes `_agent_events.jsonl` into `request.workspace` with two `command_execution` items, one `file_change` and one non-JSON line. It calls `probe_run` once through `controller.call('tool', {**gateway_identity(request), 'name': 'probe_run', ...})`, and the patched toolkit returns one completed `exchange` entry. Every `runtime(request)` returns a completed result with `elapsed_seconds` 2 and usage `{'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 5, 'reasoning_output_tokens': 0}`. Close the controller, then:

```python
before = event_count(home)
metrics = trial_metrics(home, run)
probe = metrics['stages']['probe']
self.assertEqual(probe['worker_actions'], {'command_execution': 2, 'mcp_tool_call': 0, 'file_change': 1})
self.assertEqual((probe['managed_calls'], probe['device']['exchanges'], probe['tokens']['total']), ({'probe_run': 1}, 1, 15))
self.assertEqual(metrics['stages']['acquire']['attempts'], 1)
self.assertEqual(metrics['stages']['acquire']['worker_actions'], {'command_execution': 0, 'mcp_tool_call': 0, 'file_change': 0})
self.assertEqual(event_count(home), before)  # read-only: event_count counts events rows over a ?mode=ro connection
```

- [ ] **Step 2: Run it.** Expected: ERROR, no module `scorecard`.
- [ ] **Step 3: Implement `trial_metrics`** with a read-only URI connection, reading the same rows `Controller.call('result')` assembles (`configurator.py:1390-1422`) and pairing `tool.started` with `tool.finished` by `dispatch_id`.
- [ ] **Step 4: Run it.** Expected: pass.
- [ ] **Step 5: Commit** "Compute trial metrics from retained records".

### Task 6: Compare two systems device by device on one scoring set

**Files:**
- Create: `src/generative_driver/benchmark_support/pairing.py`
- Modify: `suite_reporting.compare_suites` (:378-411) and the `suite compare` parser (`benchmark.py:130`, handler :240-243)
- Docs: `src/generative_driver/resources/client/skills/generative-driver/SKILL.md:16`: reports on a shared scoring set are compared device by device, and other series stay separate
- Test: `tests/test_pairing.py`

**Interfaces:**
- Consumes: `/3` suite reports from Task 3, whose `experiment.comparison_identity` carries `scoring_set = {'registration', 'manifest_sha256'}`. Plan 3 sets it; tests build it.
- Produces:
  - `mcnemar_exact(only_before: int, only_after: int) -> float` returns the two-sided exact binomial `min(1, 2 * sum(comb(n, k) for k <= min(b, c)) / 2**n)`, or 1.0 when `n = 0`.
  - `leak_p(scoring_passes, scoring_n, fresh_passes, fresh_n) -> float` is the one-sided Fisher exact probability of a scoring-set pass count at least this high when both rates are equal.
  - `paired_comparison(before: dict, after: dict, *, leak_check: dict | None = None) -> dict` returns schema `benchmark-paired-comparison/1` with these fields:
    - `compatible` and `incompatible_fields`: these require the same `scoring_set`, `evaluator_revision`, `child_budget_seconds` and repair limit.
    - `changed_dimensions`: protocol, toolchain and skills revisions, and the runtime configuration fields.
    - `comparison_kind`: as in `benchmark.compare`.
    - `devices`: `{'both', 'neither', 'only_before', 'only_after'}` counts, keyed by trial `case`.
    - `excluded`: every device absent from either report or not `pass` or `failure` in both.
    - `pass_rate`: `{'before', 'after'}`.
    - `mcnemar_p`.
    - `leak_check`: `None`, or `{'scoring_rate', 'fresh_rate', 'p', 'suspected': p < 0.05}`, taken from the after-system's fresh-batch report.
  - `compare_suites` returns `paired_comparison` when both reports carry a `scoring_set`, and otherwise keeps today's behavior. `suite compare` gains `--leak-check REPORT`.

- [ ] **Step 1: Write the failing tests**:

```python
def test_mcnemar_exact(self):
    self.assertEqual((mcnemar_exact(0, 0), mcnemar_exact(0, 6), mcnemar_exact(2, 8)), (1.0, 0.03125, 0.109375))
def test_devices_pair_across_protocols(self):
    before = suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure', 'd2': 'failure', 'd3': 'fault'})
    after = suite_report('learning/1', {'d0': 'pass', 'd1': 'pass', 'd2': 'failure', 'd3': 'pass', 'd4': 'pass'})
    result = paired_comparison(before, after)
    self.assertTrue(result['compatible']); self.assertIn('protocol', result['changed_dimensions'])
    self.assertEqual(result['devices'], {'both': 1, 'neither': 1, 'only_before': 0, 'only_after': 1})
    self.assertEqual(sorted(result['excluded']), ['d3', 'd4']); self.assertEqual(result['mcnemar_p'], 1.0)
def test_another_scoring_set_is_incompatible(self):
    other = suite_report('assistive/1', {'d0': 'pass'}, manifest_sha256='9' * 64)
    self.assertEqual(paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), other)['incompatible_fields'], ['scoring_set'])
def test_leak_check(self):
    self.assertLess(leak_p(10, 10, 2, 10), 0.05); self.assertGreater(leak_p(6, 10, 5, 10), 0.05)
def test_compare_suites_routes_scoring_sets(self):
    self.assertEqual(compare_suites(suite_report('assistive/1', {'d0': 'pass'}), suite_report('learning/1', {'d0': 'pass'}))['schema'],
                     'benchmark-paired-comparison/1')
```

Here `suite_report(protocol, outcomes, manifest_sha256='a' * 64)` is a test helper returning a `/3` report: one trial row per device with `case` and `scored`, and a comparison identity holding `scoring_set`, `evaluator_revision 'b' * 64`, the protocol, and `child_budget_seconds 10800`.

- [ ] **Step 2: Run them.** Expected: ERROR, no module `pairing`.
- [ ] **Step 3: Implement `pairing.py`**, route `compare_suites`, add the CLI flag and update `SKILL.md`.
- [ ] **Step 4: Run `test_pairing.py` and `test_suite_reporting.py`.** Expected: pass.
- [ ] **Step 5: Commit** "Compare systems device by device on a shared scoring set".

### Task 7: Scorecard command

**Files:**
- Modify: `src/generative_driver/benchmark_support/scorecard.py`, plus the parser (`benchmark.py:104-131`) and handler (:240-246)
- Docs: `bench/README.md`, a "Scorecard and comparison" section after "Run a repeated suite", with the two commands
- Test: `tests/test_scorecard.py`

**Interfaces:**
- Consumes: `report_suite` (Task 4), `trial_metrics` (Task 5), `pass_rate` and `pass_rate_line` (Task 2).
- Produces:
  - `suite_scorecard(report: dict, *, home: Path, trial_dirs: dict[str, Path] | None = None) -> dict`, schema `benchmark-scorecard/1`. It holds `pass_rate` and `pass_line`. Under `stages`, every metric is summed across trials, and a `median` sits beside each time and token figure. It also holds `groups`, the pass rate per value of each grouping key the trial rows carry (`family` today; `level` and `framing` from plan 3).
  - Per-trial rows (trial key, scored outcome, metrics) are included only when the report has no `scoring_set`.
  - `benchmark suite scorecard SUITE_ID --output DIR [--audit-path PATH]...` exports the suite report into a temporary directory, as the corpus report does at `benchmark.py:219-227`, then writes `scorecard.json` and `scorecard.md`. The Markdown's first line is the pass line.

- [ ] **Step 1: Write the failing tests**:

```python
def test_scoring_suite_scorecard_has_aggregates_only(self):
    report = scoring_report({'d0': 'pass', 'd1': 'failure'})  # /3 report with scoring_set and run ids r0, r1
    # METRICS maps r0 and r1 to benchmark-trial-metrics/1 dicts whose probe tokens total 10 and 20
    with patch('generative_driver.benchmark_support.scorecard.trial_metrics', side_effect=lambda home, run_id, **_: METRICS[run_id]):
        card = suite_scorecard(report, home=self.home)
    self.assertNotIn('trials', card); self.assertEqual(card['pass_line'], '1/2 pass, 0 unknown, 0 faults, 2 planned')
    self.assertEqual(card['stages']['probe']['tokens']['total'], 30)
def test_cli_writes_json_and_markdown(self):
    # patch report_suite and trial_metrics; run benchmark.main(['--home', h, 'suite', 'scorecard', 'S', '--output', out])
    self.assertTrue((out / 'scorecard.md').read_text().startswith('1/2 pass'))
    self.assertNotIn('d0', (out / 'scorecard.md').read_text())
```

- [ ] **Step 2: Run them.** Expected: FAIL, `suite_scorecard` undefined, and the parser rejects `scorecard`.
- [ ] **Step 3: Implement `suite_scorecard`**, the Markdown writer and the CLI, then add the `bench/README.md` section.
- [ ] **Step 4: Run `test_scorecard.py`, then the full suite.** Expected: pass, with 4 skips.
- [ ] **Step 5: Commit** "Add the suite scorecard command".
