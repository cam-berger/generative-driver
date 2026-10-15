# Obligation-ledger verdict for generic runs

Draft for review, 2026-10-15. It would amend the "One final verdict" item of the [assistive protocol amendment](../../design.md) for generic runs only. No code has changed.

## Intent

Today a generic run is equivalent when `emit_check` replayed recorded device exchanges, every live reuse operation succeeded, and one ran a function of the frozen package beyond identity. One function says little about how much of the firmware's interface the driver reproduces. After [REA's obligation ledgers](https://github.com/morluto/rea/blob/b3b5c8b1334cae6b5c0644e4c5750a1e075646cd/docs/reconstruction-obligation-ledgers.md), each function the driver should reproduce becomes an obligation, only evidence of comparable authority closes one, and the verdict reports which closed.

## What stays

- The verdict is computed once at finalize, after fresh reuse, from the configurator's own records, as `<run_id>:final`. No stage reads it or is reopened by it, and it never stops or gates a run: only safety, integrity, operator and host faults do. Host stops for an unreadable frozen package, safety blocks for uncertain effects, reachability as of fresh reuse and no regrading are unchanged.
- Benchmark runs record no ledger. The evaluator grades the frozen driver once after the run; `progress.equivalence = "pending"`, `equivalence/1` and `benchmark-report/3` are unchanged.

## Obligations

- **Interface:** identity, plus each function, by operation name, of every interpreted model an accepted interpret handoff carried and of the frozen package's model. A function asks the device something identity's own steps do not, as today; operations that only wait or repeat identity's requests fold into identity, so padding adds nothing. A register model (`interface-model/1`) is one obligation, settled by its package's live suite.
- **Behavior:** each task in `TASKS.json`. For a generic run the configurator writes one, `requested`, from the operator's goal, so a ledger is never empty.

Only the interpret worker, through the models it hands off, and the operator, through the start request, add obligations. A claim may open an obligation but never close or withdraw one: an operation a later revision dropped or renamed stays, open. The configurator derives the list mechanically from accepted records and adds none; no answer key, evaluator record or benchmark truth reaches it.

## Evidence and outcomes

Evidence counts only when the configurator recorded it live on the connection it supplied, from a call that ran the frozen bytes:

- **Package evidence:** a fresh-reuse `emit_test_live` of the frozen package, hashed before dispatch, whose executed snapshot carries the frozen manifest, from any reuse attempt that received the package.
- **Model evidence:** a live probe, ground or reuse call of a model whose `model_sha256` equals the frozen package's. It closes only when `emit_check` verified the package by a replay of exchanges recorded live (`REPLAYED`), which shows the package's own runtime reproducing live exchanges of that model. Without that bridge it can only refute.

Worker reports, seeded package-versus-model checks, replays of supplied exchanges, calls refused before contact and calls of other bytes close nothing. An inconclusive call (an operator, host or transport fault after dispatch, or no recorded result) neither closes nor refutes, and voids no earlier success on the same bytes.

Each obligation takes the first outcome that applies:

1. **refuted:** a call of the frozen bytes failed after the device opened, and the runtime attributed the failure to the driver. This refutes the operation that failed (identity, if its check failed) and, in fresh reuse, the behavior.
2. **closed:** a qualifying execution of the function passed; any qualifying execution closes identity, which runs first. The behavior closes only on package evidence: a function passed and no reuse call of the frozen bytes failed, as today.
3. **unknown:** only inconclusive calls of the frozen bytes ran it.
4. **limited:** the frozen driver has it, but an operator or host limit kept it from being tested.
5. **open:** anything else: never run, refused for the worker's own arguments, or absent from the frozen driver (the behavior is absent from a driver with no function). Without one package that passed `emit_check`, every obligation is open whatever the device access, as today.

| Current case | Ledger outcome |
|---|---|
| `UNBOUND`: no device bound | Limited (operator) |
| `UNREACHABLE`: resets on open, no reset grant | Limited (operator) |
| `UNHEARD`: no reset grant, every listen failed for the operator, host or transport | Limited (operator or host) |
| Package channel resets on open, no reset grant | Limited: its runtime refuses its bytes, probes included |
| `UNGRANTED`: grants permit no function | Per function: limited if its effect is not granted; the behavior is limited when none is |
| `UNLISTENED`: no worker listened, which needs no grant | Not a limit: untested obligations stay open |
| Refusal naming an operator or host fault | Limited, for the operations the call named |
| Inconclusive call | Unknown, unless other evidence settles it |
| `NEVER_RAN`, `IDENTITY_ONLY` | Functions and behavior open; identity may close |

The first four rows limit every obligation the frozen driver has.

## Verdict

The ledger's status is the first of refuted, open, unknown, limited and closed that applies. `equivalent` is false for refuted or open, null for unknown or limited, and true only when every obligation closed: unknown, refused and unscored results never aggregate to a pass. The record keeps its shape:

```json
{"equivalent": false, "basis": "self-evidence", "rule": "obligation-ledger/1", "status": "open", "closed": 5, "total": 8,
 "checks": [{"id": "operation:set_mode", "source": "interpret", "outcome": "open", "passed": false, "authority": null, "evidence": []}],
 "diff": [{"id": "operation:set_mode", "reason": "never ran on the frozen bytes"}]}
```

`checks` has one entry per obligation; `passed` is true for closed, false for refuted or open and null otherwise, and a closed entry names its `authority` (`package` or `model`) and evidence. Readers of `equivalent`, `passed` and `diff` keep working; lookups of today's two check ids change, and the frozen package's hash gets its own field.

This is stricter than today. A driver that meets its objective but leaves granted functions untested is open, so not equivalent; one whose untested functions the grants exclude is limited, so unscored. Fewer generic runs will be equivalent; the closed count shows how far each got.

## Reports and comparability

- Generic reports and the dashboard show the status, closed of total and the obligation table; `run.finalized` carries the status and counts.
- No new protocol label is needed: stages, stops, repair and benchmark grading are unchanged, and since reports of different protocols are never compared, a new label would split the benchmark series for nothing. The verdict carries `rule: "obligation-ledger/1"` instead; a generic verdict without it used the one-function rule. Reports never aggregate or compare generic verdicts of different rules, nor self-evidence with an evaluator grade.
- Recorded verdicts keep their meaning. An unfinished generic run finalizes under the ledger from records assistive/1 already keeps (accepted handoffs, the emit check, the frozen package, tool events with argument digests and full results); a missing field closes nothing.

## Limits

- The total is the pipeline's own inventory plus the goal: a function interpret never found is invisible, and closed of total is not comparable across runs. Only an independent evaluator measures the whole interface.
- One passing execution closes a function. REA obligations also name case kinds (positive, negative, malformed) that evidence must cover; requiring them would measure equivalence over inputs and is left out.
- The behavior closes when a function ran toward the objective, not when its meaning is confirmed; meaning needs an independent channel such as an operator observation.
- A rename across revisions shows as one open obligation and one new one.

## Compared with shrinking the verdict

[Goal acceptance](../../implementation/2026-10-09-goal-acceptance.md) proposes false only when the frozen bytes failed on the device or the live replay failed, null whenever they never ran live beyond identity, and refusals recorded word for word. That removes `UNLISTENED` and most of the four reachability cases, which take about 399 lines and 1,555 test lines today.

| | Shrink | Ledger |
|---|---|---|
| Size | Smaller; deletes code | One evidence-to-outcome rule replaces the cases, but inventory, attribution and the bridge are new: likely today's size |
| Information | One bit; true still rests on one function, and null merges operator limits with the pipeline's omissions (no listen, reuse ran nothing), excusing skipped work | Which obligations closed; omissions kept apart from limits |
| Misgrades | Removes the class behind the pyserial misgrade: a misread host fault yields only null | Keeps it: a host fault misread as none turns limited into open; the proposed single fault classifier narrows it |
| Evidence | Fresh reuse and the emit check, as today | Also probe and ground calls of the frozen bytes, so a frozen model that failed in probe is refuted |

Recommendation: adopt the ledger, built on shrink's per-call records (refusals word for word, no `UNLISTENED` case). It measures how much of the firmware's interface the driver reproduces, which is the purpose, and stops calling a one-function driver equivalent. If the verdict only needs to label a run, shrink alone is cheaper.

## Verification

One failing test and its implementation per slice, through the configurator/worker seam with scripted workers: package and bridged model closure; none from a seeded check or supplied replay; refutation by a probe of the frozen model; each table row; identity-only and padded inventories; a dropped operation; the record's `passed` mapping and rule label; a benchmark run with no ledger. These are scripted contract checks, not model, emulator or physical evidence. On acceptance, `docs/design.md` and `CONTEXT.md` are amended.
