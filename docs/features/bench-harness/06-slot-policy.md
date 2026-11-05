# 06. slot-policy

## Intent

A scoring suite runs about 150 trials, and today any trial that doesn't end cleanly stops the suite until someone steps in. Decision 20 says a trial the worker failed counts as a failure and the suite moves on, and a trial the host faulted moves on and gets one rerun at the end. A trial the audit flags isn't graded. This Feature builds that for scoring suites, so one bad trial no longer holds up the rest.

Development suites and hand-made suites behave as today, and their tests don't change. No limit stops a run of faults: the user judged a broken host not to be a concern, so a broken host runs every slot to a fault, visibly. An uncertain write still stops the suite, as AGENTS.md requires. Grades don't change, and no grader identity moves if `evaluator.py` isn't edited.

## Behaviours

**Which suites**

- **06-B1** — The policy applies to a suite whose frozen record holds `scoring_set`, fixed at freeze. Any other suite behaves as today, and `child_disposition`'s signature and behaviour for it stand.

**Moving on**

- **06-B2** — In a scoring suite, a trial that `outcomes.classify` calls a pass, a failure or unknown settles `finished`, and the harness goes on to the next slot. That includes a spent child budget, a no-driver grade, and a safety, integrity or model stop. Settlement and the report call the same function on a projection of the same records, so they agree, with two exceptions: the exporter's `unqualified` verdict counts as a fault with no rerun, and a later hand change to a child reports as `classify` reads it and never starts a rerun.
- **06-B3** — A trial that `classify` calls a fault settles `faulted`, and the harness goes on: a host stop, an operator-category stop that isn't a spent budget, an operator's cancel of that child, or missing evidence. A child still running or stopping is waited for, and a completed one awaiting its grade stops the suite until a resume grades it.
- **06-B4** — A slot whose trial never got a run (a refused `admit`, a provider that wouldn't start, a refused start) settles `faulted` with a closed reason word, `admit-refused`, `provider-failed` or `start-refused`, and the harness goes on. The full text stays in `trial.json`, since it may name a private path. `classify` calls such a row a fault, and the report counts it under stopped, host. If a child exists after a lost start reply, the slot attaches it rather than faulting. On other suites all of this is as today.
- **06-B5** — These still stop a scoring suite, as today: an uncertain effect, even with a spent budget; a child identity mismatch; a configurator it can't reach; a password or root missing at a slot that needs it; the vault not closed in time; the suite's budget running out; a cancel of the suite; and a failed call to record a fault or a flag.
- **06-B6** — A slot whose emulator stop can't be confirmed stops the suite, as does a harness that died. A plain resume, once the child has ended, needs no operator step. Confirmation doesn't change the outcome: a stopped child settles by `classify` alone, so a host, operator or cancel stop gets its rerun and a worker's own stop stays a failure. A completed child with no confirmed stop grades on the resume. A leftover emulator is a wasted process and can't reach a later trial, since each session has its own ports; the docs say so. A resume while the child is still running is refused, as Feature 05's B12 says, with its existing message.
- **06-B7** — Nothing limits faults in a row. A suite whose every slot faults runs every slot and each rerun, and ends `completed` with each counted as a fault.

**Reruns**

- **06-B8** — Once no slot is `pending`, each `faulted` slot reruns once, in ordinal order: a fresh trial in a new trial folder, under a new request id, with the same frozen pin and the full child budget. There is no third attempt. A crash during a rerun resumes that same attempt.
- **06-B9** — The rerun's result stands, whatever it is. A rerun that faults settles `finished` and counts as a fault. The suite's report is `provisional` until every rerun is done.
- **06-B10** — A rerun stays one row: one device has one row and one scored result, so pairing counts the device by the rerun's result and doesn't call it `repeated`. The first attempt is kept as history on the row, with its run id, outcome, tokens (`None` when unreported) and wall seconds. The scorecard gains a block `reruns: {count, first_attempt_tokens, first_attempt_seconds, measured}` and one line in its text. Stage figures, medians and report `resources` don't change.
- **06-B11** — A trial that ran and finished but whose grading hit an evaluator host fault isn't rerun with a new worker at first. It settles `faulted`, keeping its run, and in rerun order the harness regrades the same driver through `resume`. A crash repeats the regrade. A new trial follows only if that regrade is unscored again. A rerun whose grading hits a host fault gets one regrade too, and then stands as a fault. A grade unscored for `integrity:` counts as a failure, on scoring suites, and isn't rerun: `classify` says so, and any other unscored reason there is a host fault.

**Flagged trials**

- **06-B12** — Before settling any ended child with a run, and before grading a completed one, the harness checks its transcripts for commands naming the set's folder, using the existing audit, once the child is terminal. This covers the first grade, a grading `resume`, and a child that stopped while the harness was dead. A flagged trial isn't graded, settles `flagged`, gets no rerun, and the suite goes on. The report counts it under `flagged`, outside the pass rate, whatever the child's own outcome.
- **06-B13** — So a faulted trial with a flagged command settles `flagged`, and a rerun can't erase it. A stopped slot on a resume with no root can't be checked, so it fails at that slot, as a device missing does. Slots with a recorded verdict or an uncertain effect settle without one, as Feature 05's B15 says.
- **06-B14** — The flag is stored before grading would have started, so a crash and resume doesn't grade a flagged trial. What is stored holds each finding's transcript name and `command_sha256`, not the command or the root, so no record, configurator call or reason holds the set's root. A flagged child with an uncertain effect keeps stopping the suite, and settles `flagged` after reconciliation. A flagged trial's handoffs are sealed as for an unconfirmed stop, with no grade.
- **06-B15** — `flagged` is settled everywhere: in `provisional`, in `suite_status.completed`, and in the exported row, whose status stays `flagged` and which isn't listed as unscored. `report_suite` takes the union of stored findings and what `--audit-path` finds, and never overwrites, so an `--audit-path` hit may flag a settled row in the report.

**Resuming**

- **06-B16** — A scoring suite's resume never continues a stopped child on a new device: it settles the slot by the rules above. A completed ungraded child still grades. A missing trial folder for a completed child keeps stopping the suite.
- **06-B17** — A scoring suite's resume refuses an extension of the child budget, since pairing calls any such override incompatible, in one sentence used by `resume_suite` before launch, by `_held_for_resume` with the vault still open, and by `_suite_resume` as the authority. Suite budget extensions stay as today, under the seven-day cap.
- **06-B18** — On a scoring suite, a child that has already ended settles before the suite-budget check, so a paid-for grade isn't left reading `running` when the budget runs out.

## Interface and seams

**`suites.py`** gains `scoring_disposition(...)` beside `child_disposition`, returning `advance`, `fault`, `flag`, `regrade`, `wait` or `block`. It builds a projection for `classify`: the trial `{run_id, status: 'finished', outcome_category: <observed>, audit}` and the report `{workflow_status, elapsed_seconds: updated - created, effective_budget_seconds, equivalence_status, equivalence, reason}`. `classify`'s `pending` maps to `block`. `child_disposition` is untouched, and `_suite_settle` chooses by `scoring_set`.

**`suite_store.py`.** New slot statuses `faulted` and `flagged`. `settle` widens its categories to include `safety` and `integrity` on a scoring suite. `reserve` gains a third query after `pending` and before it marks the suite complete, in ordinal order, for `faulted` slots with a rerun or regrade still due. New row fields:
- `attempt`: 1 or 2. `scoring_disposition` reads it to tell a first attempt's fault (`faulted`) from a rerun's (`finished`), and the scorecard counts reruns from `attempt == 2`.
- `first_attempt`: `{'run_id', 'request_id', 'outcome', 'tokens', 'seconds'}`, written by the configurator only when `attempt` is 1 and the slot settles `faulted`, from `worker_reports` usage and `updated - created`, and cleared if a regrade then grades.
- `no_run_reason`: the closed word of 06-B4. The rerun-start transaction clears it, and a rerun that also never runs sets it again, settling `finished`, which `settle` accepts.
- `regrade`: a marker for a regrade due or spent. A regrade slot returns from `reserve` as `running` with the same request id, keeping `run_id`, and that reservation clears `result_json`, so its second settlement isn't refused. At `attempt` 1, a spent regrade means the next reservation starts the rerun. At `attempt` 2, an unscored grade after a spent regrade settles `finished`, and there is no third attempt.
- `audit`: the stored findings of 06-B14.

Starting a rerun, its new request id, `attempt`, `launching` and a reset `regrade` commit in one transaction, and `run_id`, `result_json` and `no_run_reason` clear. `child_request` keeps reading the row's id, and frozen fields stay checked. Allowlists gain the new fields: `suite_store.py:174`, `suite_reporting.py:353`, `configurator.py:1712`.

**`configurator.py`** gains a call to record a no-run fault, and one to record a flag. Each takes the suite token and ordinal, and refuses a non-scoring suite, a suite not `running` or a slot not reserved. The no-run call attaches a child if `find_request` finds one. The flag call stores findings first, and moves the slot only if the child has no uncertain effect and isn't stopping; otherwise `scoring_disposition` returns `flag` after reconciliation. `_suite_settle` checks an ended child before the budget on a scoring suite. `_suite_result` keeps a `flagged` row's status, and `suite_status.completed` counts it.

**`harness.py`.** The configurator's `_suite_reserve` returns `scoring: 'scoring_set' in frozen`, and `_suite_trial` passes it down, so the harness never reads the live table for this. `_suite_trial` stops raising for a slot that never got a run on a scoring suite, records it with the closed word, and `run_suite` skips `suite_settle` after a recorded fault; a refused or unreachable reply is a stop, with no text matching. It settles a stopped child by the rules above rather than calling `reattach`, and audits it first. The refusals at `harness.py:867` and `:871` relax for a completed child on a scoring suite only. The audit runs in `finish`, in `resume` and in `_suite_trial`'s stopped branch, once the child is terminal, with `set_root` passed down from `run_suite`'s memory; `HeldSet` still holds no path. `finish` and `resume` take the suite token and ordinal for the flag call. Findings never enter `trial.json`'s `done`.

**`outcomes.classify`** gains a keyword, `unscored_integrity_fails=True`, reading `reason` through one helper that strips `Unscored: ` and joined reasons, and uses only the latest unscored event; and a rule that a row with no `run_id` and a `no_run_reason` is a fault. The scoring condition comes from `comparison_identity.scoring_set` at `suite_reporting.py:368` and `:447`. Its results for other suites don't move. `_outcome` gains a branch for a row whose `status` is `flagged` and one for a no-run row, adding their counts without altering the `counts` of non-scoring reports. A report-time `--audit-path` hit on a settled row keeps its outcome and `scored: flagged`, as today.

**`audit.py`** is used as it is, and a helper turns a finding into `{transcript, command_sha256}`. `suite_reporting.py:370` accepts that stored form as well as a report-time finding with `command`.

**Tests** use fixture devices and scripted workers: `GeneratedTrialFixture.staged`, `as_scoring_set` and a set of at least three devices where a scoring suite needs them. A host stop is a runtime returning `failed`/`exit_error`, an operator stop `blocked`/`runtime_unavailable`, a spent budget a short child budget with a sleeping runtime, a grading fault a patched `evaluator.reference_execute`, a failed admit a patched `harness.admit`, and a stop that can't be confirmed a `Process` stand-in whose `poll` stays `None`. Use `wait_harnesses` before reading `suite.json`. Existing suites in `tests/test_suite_service.py` and `tests/test_suite_harness.py` are non-scoring. The scoring suites at `tests/test_generated_trials.py:644-658,899-915,1615` and `tests/test_vault_gate.py:246` end before any slot or finish clean, so they stand. Tests attach at `scoring_disposition`, `classify`, `reserve`, `run_suite`, `report_suite`, `pairing`, the scorecard, and a recording `call`.

**Docs.**
- `.scratch/benchmark-baseline/spec.md` decision 17: a flagged trial is counted and not graded, matching `CONTEXT.md`, rather than "an integrity fault". Decision 20 applies to scoring suites.
- `docs/benchmark-case-authoring.md`: what a scoring suite does at each ending, that no limit stops a run of faults, and that the audit reads Codex transcripts only.
- `docs/design.md`: a leftover emulator after a crash, and the seven-day limit: a suite older than that can't resume, even with approval, so a development run should measure trial time before a scoring run.

## Out of scope

- A limit on faults in a row, an operator's "I checked" step, a list of further forbidden locations, and raising the seven-day cap. The user declined each.
- Development and hand-made suites: they keep stopping at each hiccup.
- Sealing a concluded trial before the next worker starts (Feature 07). Until then, a rerun's worker could read the first attempt's folder, and Feature 07 must cover every concluded trial of a scoring suite, not only graded ones.
- Runtimes other than Codex: `audit.py` reads Codex's events only, and the user plans to run only Codex. Also transcripts outside the run, such as a stopped interpret attempt's temporary folder, and commands that build a path. The audit catches slips.
- Separating a budget stop caused by a dead harness: it counts as a failure, as `classify` says, and that rare case is accepted, as is a safety stop in recovery caused by a harness dying.
- Naming a faulted device beyond the report kept in the harness, and a distinct `faulted` count in pairing: such a device counts as `ungraded`.
- Level and framing in reports (Feature 07).
- Any real model run, password, vault, Renode or set.

## Deviations
