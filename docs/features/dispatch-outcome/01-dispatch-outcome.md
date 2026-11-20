# 01. dispatch-outcome

## Intent

The configurator must never repeat a write or an actuation whose outcome it does not know, and it must stop the operator for nothing less. Today that rule is written twice: in the worker gateway (`Controller._dispatch`) and in `DeviceAccess.begin` and `finish`, which serve generated tools and device sessions. The two copies disagree. A tool's result is read in about ten places.

This Feature writes both once:

- the **outcome reading**, one pure function that reads a tool's name and result;
- the **dispatch record**, one module that records every dispatch and owns the uncertain effects.

Both follow the owner's five decisions of 2026-11-19. Each Behaviour that carries one names it by this number:

1. Only a write or an actuation can leave an uncertain effect.
2. An effect stays uncertain only when the requested command may have been sent and its execution did not succeed.
3. A restart marks a run only for a recorded pending effect.
4. The operator resolves an uncertain effect of a run with the same statement as for a device session.
5. A cancellation keeps no successful write uncertain. A call that ended without a result stays uncertain.

Everything else stays as it is: what the verdict and the device recovery conclude from a result, the stored names, the client methods. A Deviation is judged against this: one rule in one place, the five decisions, nothing else changed.

Terms. Dispatch, outcome and uncertain effect are defined in `CONTEXT.md`. A *pending effect* is the stored mark of one dispatch, a row of the table `pending_effects`. A run or a device session *is uncertain* while its `uncertain` flag is set. An *execution record* is one record the runtime writes for one execution of an operation: an `interface-result/1` or a `driver-result/1` object. A *trusted result* is one the installed runtime or the configurator itself wrote; only a trusted result may say that a command was never sent. A *trusted tool* is a tool whose results are trusted. A result *declares a stop before its first device call* when its own `transport_open_attempted` is false and its `results` lists no entry. The *statement* is what the operator sends to resolve an uncertain effect.

## Behaviours

**The outcome reading**

- **01-B1** — One success test. `read(name, result).succeeded` is true exactly when the result's `_exit` is 0 or absent and its `ok` is truthy; an absent `ok` takes the value of `available`, and an absent `available` counts as true. The gateway, the dispatch record, `replay.py` and `scripted_worker.py` ask the reading and hold no copy of the test.
- **01-B2** — What each execution sent. For each execution record of a trusted result the reading says whether the transport was opened and whether the requested command may have been sent.
  - Opened: yes, unless the record's `transport_open_attempted` is false.
  - Sent: yes, unless the record shows otherwise. A record shows it in one of three ways: the transport was not opened; a `driver-result/1` has `requested_command_dispatched` false; an `interface-result/1` has a `transcript` list and an `operation` name, and no transcript entry names that operation with an `op` other than `delay`.
  - A record that lacks the field it would need (a `driver-result/1` without `requested_command_dispatched`, an `interface-result/1` without a transcript list or an operation name) counts as sent.
  - A result holds execution records when it is itself one, or when its `results` is a non-empty list in which every item is one. A `driver-result/1` counts only when the configurator itself ran the package's operation (`generated`): a generated tool of a run, or an operation of a device session.
  - The trusted tools are listed with the interface below. The result of any other tool is not trusted, whatever it holds. One result of any tool is trusted whatever its name: the record the configurator writes itself when the provider of a supplied device fails before the call starts. The caller says so (`undispatched`); nothing in a result can.
- **01-B3** — How the call ended for the verdict. `outcome` is `succeeded`, `refused`, `failed` or `inconclusive`, and `detail` names the fault of an inconclusive one. For every result they are what `_live_outcome` gives today, read from the result alone. `refused_before_open` is true when the execution records of a trusted result all have `transport_open_attempted` false, or when a trusted result holds no execution record, declares a stop before its first device call and its own `fault` is not `host`. `host_fault` is what `_host_fault` gives today. `answered` is the part of `_answered` that the result decides: a live `probe_run` that succeeded, or one the runtime attributes to the model after every failed record opened the transport.
- **01-B4** — Whose fault a pending effect was. For one result the reading gives the faults (`model`, `operator`, `host`, `unknown`) of its failed execution records and the records that count as the model's, by the rule `device_recovery.unresolved_outcomes` applies today. It also gives the faults that another failed call adds, by the rule of `device_recovery.diagnostic_faults`. The attribution depends on `generated` and not on the tool's name or on trust: it stays as it is. `device_recovery` keeps the walk over the journal, the checks of each event's own fields and the proof that a call was a generated tool of this run (`_generated`, and the proof's `session_id`); beyond that proof it reads no result itself. Which calls count as another failed call is such a check of the event: the names of its list `LIVE_TOOLS`, which stays where it is, and a generated tool. The recovery reaches the same conclusion as today for the same journal.

**The dispatch record**

- **01-B5** — One journal for three callers. A dispatch of the worker gateway, of a generated tool in a run and of a device session is recorded as `tool.started` before the tool runs and as `tool.finished` when it returns a result. Both events hold `stage`, `assignment_id`, `name`, `actor`, `effectful` and the caller's facts; `tool.finished` also holds `ok`, `result`, `artifacts`, `dispatch_id` (the id of the start, as a string), `effects_reconciled` and `refused_before_open`. `effects_reconciled` is true only for a marked dispatch that left no uncertain effect.
- **01-B6** — (Decision 1) Only a write or an actuation is marked. A dispatch whose operations are all declared `read` is recorded with `effectful` false, leaves no pending effect and never makes its run or session uncertain, also over a channel that resets the device when opened. Opening such a channel still needs the `reset` grant, and needs nothing more. The reply to an abandoned call holds `uncertain_effect` true when that dispatch has a pending effect and leaves the key out otherwise, as today. The reply to the cancellation of an invocation that has no result holds `uncertain_effect` true when its dispatch has a pending effect and false otherwise.
- **01-B7** — (Decision 2) A marked dispatch stays uncertain only when the requested command may have been sent and did not succeed. Each execution record of the result is read on its own, and the effect stays uncertain when at least one record stays:
  - a record with `ok` true clears;
  - a failed record whose transport was not opened clears;
  - a failed record that shows the requested command was never sent clears: the identity read failed, the open failed, or a guard read failed or refused the call;
  - a record that failed after a step of the requested operation went out stays, whatever the fault, also when the model reads the device's reply as a rejection;
  - a `driver-result/1` that does not say whether the command was sent stays;
  - a result whose records all have `ok` true while the call did not succeed stays: something failed that no record shows.
- **01-B8** — (Decision 2) A result without execution records is read as a whole. A marked dispatch with a trusted result is cleared when the call succeeded, or when the result declares a stop before its first device call, with or without a host fault. Otherwise it stays uncertain. A marked dispatch with a result that is not trusted is cleared only by success.
- **01-B9** — (Decision 5) A cancellation keeps no successful write uncertain. A marked dispatch that is cleared by its result is cleared also when its run or session was cancelled while it ran.
- **01-B10** — (Decision 5) A dispatch that ends without a result is recorded as `tool.abandoned`, never as finished, and its pending effect stays. This holds for all three callers. A device session records it too, which it does not do today; the client still gets the error, and the invocation still has no saved result. This holds also when the configurator could not start a package's operation at all, because the checked copy of the package was refused or could not be read back: the call ends without a result, so a write stays uncertain although nothing reached the device.
- **01-B11** — While a run or session is uncertain, reads go on. A write, an actuation and a gateway call that selects a condition of a supplied device are refused before anything is recorded as started, with one text for all three callers. The text begins `Outstanding effect is uncertain`.
- **01-B12** — (Decision 3) After a restart of the configurator, a run that was queued or running is blocked and needs an explicit resume, as today. Its `uncertain_effect` is what it was before the restart. A run granted `write` or `actuate` that has no pending effect is not uncertain and resumes without a statement.
- **01-B13** — After a restart, a device session is blocked when it is not closed and one of its invocations was in flight when the configurator stopped. An invocation was in flight when it has no result and its dispatch is not recorded as `tool.abandoned`. A closed session is never blocked again.
  - The restart does not change the session's flag: the session is uncertain exactly when it was before. The event `device.recovered` holds the flag as `uncertain_effect`, true or false. A session that is still blocked at a later restart is recorded again, as today.
  - A blocked session refuses every invocation, as today.
  - A blocked session with no uncertain effect keeps its binding until it is closed: another `device_open` on that binding is refused. `device_cancel` leaves a blocked session blocked. `device_close` closes it, and then the binding can be opened again. No statement is needed, and `device_reconcile` still refuses a session that is not uncertain.
  - A blocked session with an uncertain effect needs the statement, as today.
- **01-B14** — (Decision 4) One statement resolves an uncertain effect of a run and of a device session: `effect_resolution` is `confirmed_safe`, and `observation` is an object with a non-empty `source`, a non-empty `message` and `prior_execution_stopped` true. Non-empty means text that is not blank; anything that is not text is refused. A caller's own refusals come before the check of the statement, as today: a `respond` while a worker of the run is still stopping is refused for that.
  - For a run, `respond` reads `effect_resolution` from its parameters or, when absent there, from the observation, as today.
  - A `respond` or a `device_reconcile` that states a resolution without all of this is refused with one text that names `confirmed_safe`, `source`, `message` and `prior_execution_stopped`. It changes nothing.
  - A complete statement clears the pending effects and the flag. The event (`operator.response` for a run, `device.reconciled` for a session) lists the dispatches it resolved as `resolved_dispatch_ids`.
  - A `respond` with a complete statement on a run that is not uncertain is accepted and lists no dispatch. A `respond` that states no resolution stays an operator message and resolves nothing.
- **01-B15** — The verified replacement of a supplied device resolves the run's pending effects and clears its flag in the same transaction as the stage's handoff, as today. The event `emulator.recovered` keeps its fields.
- **01-B16** — A flag an earlier version set stays. A run or session that is uncertain with no pending effect stays uncertain until the operator's statement, which then lists no dispatch. A restart, a cancellation and the end of a read do not clear it. No replacement is offered for it, as today: the recovery needs a pending effect.
- **01-B17** — A failed cleanup marks nothing. When giving up what a run holds raises, the failure is logged and recorded as `cleanup.failed`. The run keeps the status and reason its stage left and does not become uncertain. A run that is uncertain after its verdict, or uncertain while it awaits its grade, is still blocked for safety with today's texts, whether or not a cleanup failed. The status text `Cleanup failed; operator reconciliation required` no longer exists.
- **01-B18** — The documents state the new behaviour.
  - `docs/extending-drivers-and-mcp.md`: a read over a channel that resets on open needs the `reset` grant and leaves no uncertain effect; which write stays uncertain; what a session is after a restart, and how a blocked session without an uncertain effect is closed and reopened.
  - `docs/setup.md`: the statement, field by field, with an example `observation.json` for `respond` that holds `effect_resolution` itself, because the command line and the MCP server send the observation alone; that a restart leaves a run uncertain only for a pending write or actuation.
  - `docs/design.md`: a failed cleanup is an event and never an uncertain effect.
  - `docs/codex.md`: an abandoned call leaves an uncertain effect only when it was a write or an actuation.
  - The client skill, in both copies that a test keeps identical (`plugins/generative-driver/skills/generative-driver/SKILL.md` and `src/generative_driver/resources/client/skills/generative-driver/SKILL.md`): an observation that resolves an uncertain effect holds the statement's fields.
  - A new `docs/adr/0011-what-leaves-an-effect-uncertain.md` records the five decisions in one short text: the context (the rule was written twice, and the operator was stopped for effects that could not have happened), the five decisions, and why (the operator is stopped only when a write or an actuation may really have gone out, and nothing is cleared silently). Both new modules cite it in their module doc.
  - No sentence in these files or in `docs/client-interface.md` describes the old rule. `tests/test_guides.py` binds the code blocks of the guides against the command line and the SDK, and they keep passing.

## Interface and seams

### The outcome reading: `src/generative_driver/dispatch_outcome.py`

One entry, no database, no service:

```python
read(name, result, *, generated=False, undispatched=False) -> Reading
```

`name` is the tool's name as dispatched. `generated` says the configurator itself ran an operation of a package (`DeviceAccess.invoke_package`): a generated tool of a run, or an operation of a device session. `undispatched` says the configurator wrote the result itself because the call never started. `result` is the tool's result; anything that is not an object reads as a failed result with no execution record. `Reading` is immutable and has:

| Attribute | Meaning |
|---|---|
| `succeeded` | the success test |
| `trusted` | the result is a trusted result |
| `executions` | one item per execution record, each with `ok`, `opened`, `sent`; empty when the result holds none |
| `leaves_uncertain` | a marked dispatch with this result stays uncertain |
| `refused_before_open` | the value stored in `tool.finished` |
| `outcome`, `detail` | the verdict's reading |
| `answered`, `host_fault` | as `_answered` (its result part) and `_host_fault` today |
| `pending_faults`, `model_failures` | the faults of the failed execution records of a pending effect, and the records that are the model's |
| `other_faults` | the faults this result adds as another failed call |

`outcome`, `detail`, `answered` and `host_fault` do not depend on `generated`, on `undispatched` or on trust. `pending_faults`, `model_failures` and `other_faults` depend on `generated` and not on `name` or on trust. The module holds the one list of trusted tools and says why each is on it:

- `interface_execute`, `probe_run`: the toolkit runs the installed runtime over the model.
- `emit_test_live`: the toolkit runs the installed runtime over a checked copy of the package. Its result is a summary and holds no execution record, so it is read as a whole (01-B8), as today.
- a call read with `generated`: the configurator runs the installed runtime in a child process over a checked copy of the package's data.

The module names no tool that is not on the list; its tests use a name of their own for one. A result read with `undispatched` is trusted whatever the tool.

The verdict reads the result, not the event's stored `ok` or `refused_before_open`. For every event the configurator wrote they agree.

### The dispatch record: `src/generative_driver/dispatch_record.py`

```python
DispatchRecord(connect, lock, write_event)
```

`connect()` is a context manager that gives a database connection and commits on exit (`Controller._db`). `lock` is the configurator's re-entrant lock. `write_event(owner_id, kind, payload, db)` writes one event and returns its id (`Controller._event`). The record creates `pending_effects` when it is constructed. The configurator constructs one record, before it recovers runs, and `DeviceAccess` uses the same one. It gives the record callables that look `_db` and `_event` up at each call, because three tests replace them on a built `Controller`.

```python
begin(owner_id, *, kind, name, actor, effects, stage=None, assignment_id=None, facts=None,
      condition=False, before_mark=None, after_start=None) -> int | None
finish(owner_id, dispatch_id, result, *, kind, name, actor, generated=False, undispatched=False,
       stage=None, assignment_id=None, facts=None, artifacts=None, after_finish=None) -> int
abandon(owner_id, dispatch_id, *, name, actor, reason, stage=None, assignment_id=None, error=None) -> bool
uncertain(owner_id, *, kind, db=None) -> bool
pending(owner_id, db=None) -> list
resolve_by_operator(owner_id, *, kind, effect_resolution, observation, db=None) -> list
resolve_by_replacement(owner_id, db) -> list
marks(effects) -> bool    # module function
```

`kind` is `run` or `session` and selects the table that holds the flag. `effects` are the declared effects of the operations the call runs; the record alone decides that `write` and `actuate` mark. Its module function `marks(effects) -> bool` says so. The gateway asks it for the one check it makes before `begin` only for a marking call, the model check of `probe_run` and `interface_execute`. `effects` is any iterable of names; a name other than `write` and `actuate` marks nothing.

A method given a `db` runs in the caller's transaction and takes no lock. Without one, `uncertain`, `pending` and `resolve_by_operator` take the lock and a transaction of their own.

- `begin` holds the lock and one transaction. In this order it: refuses an owner that is not active or an assignment that is not current; calls `before_mark(db)`; refuses a marking call or a `condition` call while the owner is uncertain; sets the flag and, after the start, inserts the pending effect, both only for a marking call; writes `tool.started`; calls `after_start(db, dispatch_id)`. It returns the id of the start.
  - A run is active when it is `running` and not cancelled; a session when it is `open` and not cancelled. An owner that has no row is not active. A run's dispatch names its assignment. An assignment is current when it is `active` and of the run's stage and revision. The texts are `Device operation requires active ownership` for a session and `Worker assignment became inactive before dispatch` for a run or its assignment. A generated tool call of a run that is not active therefore gets the run's text, where today it gets the session's.
  - `before_mark` may raise to refuse the dispatch, or return `False` (and only `False`) to give it up without an error; `begin` then records nothing and returns `None`. The gateway makes its deadline and budget check there. `DeviceAccess` checks there that the configurator is not stopping, with today's text, and that the request was not cancelled while queued, and inserts its invocation row in `after_start`.
  - The refusal while uncertain is `Outstanding effect is uncertain; nothing that would change the device is sent until the operator resolves it`.
- `finish` holds the lock and one transaction; for the gateway that adds the lock, which its finish does not hold today. It reads the result with `dispatch_outcome.read`, passing `generated` and `undispatched` on. For a marked dispatch that the reading clears, it deletes the pending effect and sets the flag to whether the owner still has a pending effect. For any other dispatch it leaves the pending effects and the flag alone. It writes `tool.finished`, calls `after_finish(db, event_id)` and returns the event's id. A dispatch was marked when it has a pending effect: nothing resolves a pending effect while its call is in flight. `artifacts=None` is stored as an empty object, and where a key of `facts` equals one of the record's own, the record's wins. `DeviceAccess` saves the invocation's result in `after_finish`.
- `abandon` writes `tool.abandoned` with `stage`, `assignment_id`, `name`, `dispatch_id`, `reason`, `actor` and, for `error=(type name, message)`, `type` and `message`. It keeps the pending effect and returns whether the dispatch has one. It uses a transaction of its own, and a session's event holds `stage` and `assignment_id` as null. The callers keep their log entries and their replies.
- `uncertain` says whether the owner's flag is set; for an owner with no row it is false. `pending` gives the owner's pending effects in dispatch order, each with `dispatch_id` (an integer, as stored), `assignment_id` and `actor`.
- `resolve_by_operator` checks the statement and raises `Resolving an uncertain effect requires effect_resolution "confirmed_safe" and an observation with source, message and prior_execution_stopped true` when it is incomplete. Otherwise it deletes the owner's pending effects, clears the flag and returns the resolved dispatch ids as strings. The caller makes its own checks first (the run is stopped; nothing is in flight; a session is not closed and is uncertain) and writes its own event, in the same transaction when it passes `db`.
- `resolve_by_replacement` does the same for a run without a statement, in the caller's transaction.

Only the record writes the `uncertain` flag of `runs` and `device_sessions`, and only its SQL names `pending_effects`. Code that already holds an owner's row may keep reading its `uncertain` column; `status`, `resume`, the binding claim and the restart may ask `uncertain`. Both say the same.

### The callers

- `Controller._dispatch`: its mark block becomes one `begin` and its finish block one `finish`. `_abandoned` calls `abandon` and takes the reply's `uncertain_effect` from it. `_device_call` lets its caller know when the call never started, in a way no tool result can imitate: not by a field of the result. The gateway and `worker_tool` then pass `undispatched=True` to `finish`. If the return value of `_device_call` changes shape for this, the test in `tests/test_service_log.py` that calls it follows. The restart loop no longer derives the flag from the grants. `respond` and the recovery commit in `_accept` call the two resolve methods. `_settle_release` records every failure as its event and changes neither flag nor status for a failed cleanup; whether the run awaits its grade no longer depends on a failed cleanup.
- `DeviceAccess`: `worker_tool` and `execute` call the record and pass `generated=True` to `finish`; `begin` and `finish` of `DeviceAccess` go. `execute` calls `abandon` when the package call raises, then lets the error pass. `_effectful` keeps only its grant check. `reconcile` calls `resolve_by_operator`. The restart blocks as 01-B13 says and no longer sets the flag. The lease check that `claim_binding` and `claim_probe` share (`_claim`) treats a blocked session, and only a session, as holding its lease until it is closed. `stop` leaves the status of a blocked session `blocked` when it cancels, and still records the cancellation. The reply of `device_cancel_invocation` for an invocation in flight says `uncertain_effect` true only when its dispatch has a pending effect.
- `device_recovery.unresolved_outcomes` takes the pending effects from the record and the faults from the reading.
- `_fresh_reuse`, `_probe_records`, `_device_diff`, the emit part of `_check` and `supplied/stages._host` call `read` where they call `_live_outcome`, `_answered` or `_host_fault` today. They pass an event's `result` as it is stored: a missing result is no object and reads as failed, where an empty object would read as succeeded.

Removed with this Feature: `_toolchain_fault`, `_host_fault`, `_before_contact`, `_declared_refusal`, `_live_outcome` and `_answered` in `configurator.py`; the `actor == 'evaluator'` branches of the finish (no caller passes that actor, so every `tool.finished` holds `result` and `artifacts`); the marking and the blocking of a failed cleanup.

Unchanged and kept with the callers: the grant checks; the gateway's deadline and budget check, made before the mark inside the same lock; request ids and the cancellation of queued requests; the instrument readings around a call on a supplied device; `tool.refused`.

### Parts that can be built side by side

- **A**: `dispatch_outcome.py` and `tests/test_dispatch_outcome.py`. New files only.
- **B**: `dispatch_record.py` and `tests/test_dispatch_record.py`. New files only. Only `finish` uses A, through `succeeded`, `leaves_uncertain` and `refused_before_open`; the other methods need nothing of A.
- **D**: the documents. They share no file with A, B or C.
- **C**: the callers and their behaviour tests. C needs A and B.

### Tests

- `tests/test_dispatch_outcome.py`: table tests through `read` alone. Rows are results as the tools and the runtime write them. They cover the success test, each row of the two decision-2 Behaviours, `refused_before_open`, the four outcomes with their details, `host_fault`, `answered` and the fault attribution.
- `tests/test_dispatch_record.py`: the record on a temporary SQLite database, with a lock and an event writer of the test, no `Controller` and no worker. The database holds only what the record reads: `runs` and `device_sessions` (`id`, `status`, `cancelled`, `uncertain`, and `stage` for runs), `assignments` (`id`, `run_id`, `state`, `payload`), `progress` (`run_id`, `payload`) and the events the writer stores.
- The five decisions are also tested through the configurator's client calls (`start`, `status`, `respond`, `resume`, `mcp_tool`, `device_open`, `device_execute`, `device_status`, `device_reconcile`, `device_close`), with scripted workers and scripted devices.

Existing tests that change:

- `tests/test_owner_generated_integration.py`: `test_reset_grant_and_open_failure_keep_uncertainty` (a write whose open failed before the command was sent now leaves no uncertain effect); `test_resetting_session_read_keeps_failed_open_uncertain_and_retains_binding` and `test_resetting_assigned_read_marks_pending_before_open_and_blocks_another_open` (a read over a resetting channel marks nothing); the recovery test that inserts into `pending_effects` by SQL makes its pending effect through the record.
- `tests/test_configurator.py`: `test_recovery_preserves_uncertainty_until_operator_reconciles_it` (after a restart a run with a write grant and no pending effect is not uncertain and resumes; a second case holds a pending effect and needs the statement).
- `tests/test_client_surface.py`: the test of the failed-cleanup status text (08-B8 of an earlier Run) becomes a test that a release that raises leaves status and flag alone. `tests/test_service_log.py` keeps its test of the `cleanup.failed` event and log entry. The two tests in `tests/test_configurator.py` that patch `_release` to set the flag after the verdict reach nothing that is removed; they stay, apart from the statement the first one sends.
- `tests/test_device_sessions.py`: `test_owner_loss_blocks_a_session_while_its_old_child_may_still_run` (the interrupted call is a read: the session is blocked and refuses another invocation, `uncertain_effect` is false, the binding cannot be opened again until `device_close`, and can afterwards).
- `tests/test_service_log.py`: the test that inserts into `pending_effects` by SQL makes its pending effect through the record.
- Every test that sends `respond` with `confirmed_safe` sends the complete statement: three in `tests/test_configurator.py`, one in `tests/test_tool_process.py` and one in `tests/test_service.py`. A test that asserts one of the old refusal texts (of `respond`, of `device_reconcile`, of a call refused while an effect is uncertain) asserts the new text.
- Tests that import `_host_fault`, `_toolchain_fault`, `_live_outcome` or `_declared_refusal` (`tests/test_configurator.py`, `tests/test_package.py`) call `dispatch_outcome.read` and assert `host_fault`, `detail`, `outcome` and `refused_before_open`. A test fixture whose event and result disagree is corrected to what the configurator would have written.

## Out of scope

- `src/interface_runtime/`, `src/generative_driver/client.py`, the command line and the MCP server are not changed, their help texts included.
- No stored event kind, event field, table, column, client method, status value or status field is renamed. The class `Outcome` of the stage decision in `configurator.py` keeps its name.
- No stored data is migrated.
- The package-format Run rewrote the same places just before this Feature: `Controller._dispatch`, the verdict's reuse check around `_fresh_reuse` and `_frozen_package`, `DeviceAccess.worker_tool`, the session's open and describe code, `docs/extending-drivers-and-mcp.md` and `docs/setup.md`. There this Feature changes only the lines a Behaviour needs: no reformatting, no reordering, no renaming, no tidying. `CONTEXT.md` is not changed.
- What that Run left for a later clean-up: the gateway's code for a worker's call of `emit_test_live`, which no stage assigns, and the readings of `_live_outcome` that fresh reuse no longer reaches. This Feature moves the rule of `_live_outcome` whole and reads a result of `emit_test_live` as the gateway does today.
- Clearing the effect of a package's operation that the configurator could not start (01-B10).
- The rules of `_fresh_reuse` and `_device_diff` beyond their calls of the reading, and the success test of the emit check in `_check`.
- Which effect a replacement may resolve, and how the recovery asks the provider.
- How the gateway computes deadlines and budgets; request ids; cancellations of queued requests; instrument readings; grants.
- Recording `tool.abandoned` at a restart for dispatches that were in flight when the configurator stopped.
- The texts that refuse `resume`, `attach_device` and `record_evaluation` while a run is uncertain.

## Deviations
