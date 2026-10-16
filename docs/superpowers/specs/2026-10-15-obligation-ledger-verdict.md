# Obligation-ledger verdict for generic runs

Accepted 2026-10-15 in a narrow form: the verdict judges only the obligations the operator's grants allow. It amends the "One final verdict" item of the [assistive protocol amendment](../../design.md) for generic runs only. No code has changed.

## Intent

Today a generic run is equivalent when `emit_check` replayed recorded device exchanges, every live reuse operation succeeded, and one ran a function of the frozen package beyond identity. One function says little about how much of the firmware's interface the driver reproduces. After [REA's obligation ledgers](https://github.com/morluto/rea/blob/b3b5c8b1334cae6b5c0644e4c5750a1e075646cd/docs/reconstruction-obligation-ledgers.md), each function the driver should reproduce becomes an obligation, only evidence of comparable authority closes one, and the verdict reports which closed, judging those the operator's grants allow.

## What stays

- The verdict is computed once at finalize, after fresh reuse, from the configurator's own records, as `<run_id>:final`. No stage reads it or is reopened by it, and it never stops or gates a run: only safety, integrity, operator and host faults do. Host stops for an unreadable frozen package, safety blocks for uncertain effects, reachability as of fresh reuse and no regrading are unchanged.
- Benchmark runs record no ledger. The evaluator grades the frozen driver once after the run; `progress.equivalence = "pending"`, `equivalence/1` and `benchmark-report/3` are unchanged.

## Obligations

- **Interface:** identity, plus each function, by operation name, of every interpreted model an accepted interpret handoff carried and of the frozen package's model. A function asks the device something identity's own steps do not, as today; operations that only wait or repeat identity's requests fold into identity, so padding adds nothing. A register model (`interface-model/1`) is one obligation, settled by its package's live suite.
- **Behavior:** each task in `TASKS.json`. For a generic run the configurator writes one, `requested`, from the operator's goal, so a ledger is never empty.

Only the interpret worker, through the models it hands off, and the operator, through the start request, add obligations. A claim may open an obligation but never close or withdraw one: an operation a later revision dropped or renamed stays, open. The configurator derives the list mechanically from accepted records and adds none; no answer key, evaluator record or benchmark truth reaches it.

**Scope.** An obligation lies within the grants when the run's grants allow what it needs:
- the effect its operation declares in the latest model that has it, the frozen model first (an absent effect is actuate, as the gateway reads it);
- the reset that opening the device performs, when its model's channel declares one or the run had observed the device resetting on open by fresh reuse.

A call refused for an effect the grants exclude places the operations it named outside them too. Identity is a read, so it lies within the grants unless it needs an ungranted reset. The behavior lies outside the grants when the frozen driver has functions and none of them lies within.

## Evidence and outcomes

Evidence counts only when the configurator recorded it live on the connection it supplied, from a call that ran the frozen bytes:

- **Package evidence:** a fresh-reuse `emit_test_live` of the frozen package, hashed before dispatch, whose executed snapshot carries the frozen manifest, from any reuse attempt that received the package.
- **Model evidence:** a live probe, ground or reuse call of a model whose `model_sha256` equals the frozen package's. It closes only when `emit_check` verified the package by a replay of exchanges recorded live (`REPLAYED`), which shows the package's own runtime reproducing live exchanges of that model. Without that bridge it can only refute.

Worker reports, seeded package-versus-model checks, replays of supplied exchanges, calls refused before contact and calls of other bytes close nothing. An inconclusive call (an operator, host or transport fault after dispatch, or no recorded result) neither closes nor refutes, and voids no earlier success on the same bytes.

Each obligation takes the first outcome that applies:

1. **refuted:** a call of the frozen bytes failed after the device opened, and the runtime attributed the failure to the driver. This refutes the operation that failed (identity, if its check failed) and, in fresh reuse, the behavior.
2. **closed:** a qualifying execution of the function passed; any qualifying execution closes identity, which runs first. The behavior closes only on package evidence: a function passed and no reuse call of the frozen bytes failed, as today.
3. **unknown:** only inconclusive calls of the frozen bytes ran it.
4. **limited:** it lies outside the grants, or the frozen driver has it but an operator or host limit kept it from being tested.
5. **open:** anything else, such as never run, refused for the worker's own arguments, or absent from the frozen driver (the behavior is absent from a driver with no function). Without one package that passed `emit_check`, every obligation within the grants is open whatever the device access, as today.

| Current case | Ledger outcome | Verdict |
|---|---|---|
| A function whose effect is not granted, such as a write under read-only grants | Limited, outside the grants | Listed, not judged |
| `UNGRANTED`: no function within the grants | Functions and the behavior outside the grants | Unscored, as today: only identity is judged |
| `UNREACHABLE`: device resets on open, no reset grant | Every obligation outside the grants: each connection is an ungranted reset | Unscored, as today |
| Package channel resets on open, no reset grant | Every obligation of the driver outside the grants | Unscored, as today |
| `UNBOUND`: no device bound | Limited within the grants (operator) | Unscored |
| `UNHEARD`: no reset grant, every listen failed for the operator, host or transport | Limited within the grants (operator or host) | Unscored |
| Refusal naming an operator or host fault | Limited within the grants, for the operations it named | Unscored |
| Inconclusive call | Unknown, unless other evidence settles it | Unscored |
| `UNLISTENED`: no worker listened, which needs no grant | Not a limit: untested obligations stay open | Not equivalent |
| `NEVER_RAN`, `IDENTITY_ONLY` | Granted functions and the behavior open; identity may close | Not equivalent |

The verdict column assumes no judged obligation is refuted or open; either makes the run not equivalent.

## Verdict

The verdict judges only the obligations within the grants. Those outside are listed as limited and never make a run unscored or not equivalent by themselves.

Among the judged obligations, the status is the first of refuted, open, unknown, limited and closed that applies. When the behavior lies outside the grants, closed becomes limited, because identity alone shows no function.

`equivalent` follows from the status:
- false for refuted or open;
- null for unknown or limited;
- true for closed: every judged obligation closed, and the run is **equivalent within the granted effects**.

Unknown, refused and unscored results never aggregate to a pass. The record keeps its shape:

```json
{"equivalent": true, "basis": "self-evidence", "rule": "obligation-ledger/1", "status": "closed",
 "grants": ["read"], "judged": 6, "closed": 6, "outside_grants": 2, "total": 8,
 "checks": [{"id": "operation:set_mode", "source": "interpret", "granted": false, "outcome": "limited",
             "passed": null, "authority": null, "evidence": [], "reason": "write is not granted"}],
 "diff": []}
```

`checks` has one entry per obligation:
- `granted` says whether the obligation was judged;
- `passed` is true for closed, false for refuted or open, and null otherwise;
- a closed entry names its `authority` (`package` or `model`) and its evidence.

`diff` names each judged obligation that did not close, so obligations outside the grants never appear there. Readers of `equivalent`, `passed` and `diff` keep working. Lookups of today's two check ids change, and the frozen package's hash gets its own field.

This is stricter than today within the grants, and neutral outside them:
- A driver that meets its objective but leaves granted functions untested is open, so not equivalent.
- A write function under read-only grants is listed and does not count, so such a run can be equivalent within the granted effects.

## Reports and comparability

- Generic reports and the dashboard show the status, closed of judged, the number outside the grants and the obligation table. While any obligation lies outside the grants, the label reads "equivalent within the granted effects" and names the grants. `run.finalized` carries the status, the grants and the counts.
- No new protocol label is needed. Stages, stops, repair and benchmark grading are unchanged. Reports of different protocols are never compared, so a new label would split the benchmark series for nothing.
- The verdict carries `rule: "obligation-ledger/1"` instead; a generic verdict without it used the one-function rule. Reports never aggregate or compare generic verdicts of different rules, nor self-evidence with an evaluator grade.
- Recorded verdicts keep their meaning. An unfinished generic run finalizes under the ledger from records that assistive/1 already keeps: accepted handoffs, the emit check, the frozen package, and tool events with argument digests and full results. A missing field closes nothing.

## Limits

- The total is the pipeline's own inventory plus the goal, so a function interpret never found is invisible. Closed of judged is not comparable across runs. Only an independent evaluator measures the whole interface.
- Equivalence within the granted effects says nothing about functions outside them. Only wider grants let a run judge those.
- One passing execution closes a function. REA obligations also name case kinds (positive, negative, malformed) that evidence must cover. Requiring them would measure equivalence over inputs, and is left out.
- The behavior closes when a function ran toward the objective, not when its meaning is confirmed. Meaning needs an independent channel, such as an operator observation.
- A rename across revisions shows as one open obligation and one new one.

## Compared with shrinking the verdict

[Goal acceptance](../../implementation/2026-10-09-goal-acceptance.md) proposes a smaller rule:
- false only when the frozen bytes failed on the device or the live replay failed;
- null whenever the frozen bytes never ran live beyond identity;
- refusals recorded word for word.

That removes `UNLISTENED` and most of the four reachability cases, which take about 399 lines and 1,555 test lines today.

| | Shrink | Ledger |
|---|---|---|
| True means | One function beyond identity ran and nothing failed | Every obligation within the grants closed: equivalent within the granted effects |
| Size | Smaller; deletes code | One evidence-to-outcome rule replaces the cases, but inventory, attribution, the bridge and grant scope are new: likely today's size |
| Information | One bit. Null merges operator limits with the pipeline's omissions (no listen, reuse ran nothing), excusing skipped work | Which granted obligations closed. Ungranted ones are listed apart, and omissions are kept apart from limits |
| Misgrades | Removes the class behind the pyserial misgrade: a misread host fault yields only null | Keeps it: a host fault misread as none turns limited into open. The proposed single fault classifier narrows it |
| Evidence | Fresh reuse and the emit check, as today | Also probe and ground calls of the frozen bytes, so a frozen model that failed in probe is refuted |

On 2026-10-15 the user chose the ledger in this narrow form. It is built on shrink's per-call records: refusals are recorded word for word, and there is no `UNLISTENED` case. It:
- measures how much of the granted interface the driver reproduces;
- stops calling a one-function driver equivalent;
- does not count against a run what the operator chose not to grant.

## Verification

Each slice is one failing test and its implementation, through the configurator/worker seam with scripted workers. The tests cover:
- package and bridged model closure, and no closure from a seeded check or a supplied replay;
- refutation by a probe of the frozen model;
- each table row, including a write function under read-only grants that is listed but not judged, and a run with no function within the grants that stays unscored;
- identity-only and padded inventories;
- a dropped operation;
- the record's `granted` and `passed` mapping and its rule label;
- a benchmark run with no ledger.

These are scripted contract checks, not model, emulator or physical evidence. When this is implemented, `docs/design.md` and `CONTEXT.md` are amended.
