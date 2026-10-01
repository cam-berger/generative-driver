# Durable Benchmark Suites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the six-entry, three-family development pilot as durable sequential trials, preserve every disposition and resource measurement, and compare saved suite reports without launching services, models or devices.

**Architecture:** The existing configurator owns a suite scheduling thread and all child runs; suite clients never own workers or device connections. Separate manifest validation and pure decisions, SQLite bookkeeping, and report aggregation into the three modules specified below. Freeze the manifest, case pins and execution configuration before dispatch, then use deterministic child request IDs to reconcile interrupted dispatch.

**Tech Stack:** Python >=3.11, standard-library SQLite/threading/JSON/unittest, existing MCP 2.x stdio and private native IPC, existing authenticated evaluator evidence through cryptography. No new required dependencies.

**Spec:** [Benchmark expansion specification](../specs/2026-09-30-benchmark-expansion.md), sections 8–10, consuming the registry, snapshots and report/evidence contracts delivered by plans 01 and 02.

## Global Constraints

- Core Python is `>=3.11`; native macOS and Windows 11+ remain supported.
- Original code is Apache-2.0; preserve third-party notices and per-asset license provenance.
- Add no required Python dependencies beyond the existing `mcp>=2.2,<3` and `cryptography>=43`.
- Containers and native analysis tools remain optional; installation smoke never requires inference, hardware, Renode, Ghidra, or an evaluator password.
- The existing background configurator owns runs, workers, device leases, cancellation, and recovery; Goose, Codex, CLI, and suite clients do not become additional owners.
- Run one suite child at a time and one stage at a time per child; never retry an uncertain device effect automatically.
- Preserve TQ9 v1 manifests, binaries, encrypted truth, and the recorded September 28 baseline byte-for-byte; `tq9` continues to mean v1.
- Candidate workers receive no evaluator password, reference source, private expectations, final-test vectors, or maintenance-scenario answer.
- Hashes establish identity and integrity; encryption protects evaluator evidence at rest. Neither establishes operating-system isolation.
- Compare observable outputs, effects, state transitions, and refusals; never require candidate source equality or a preferred code idiom.
- Keep recorded replay, scripted contract fixtures, reference calibration, actual-agent emulation, and physical observations distinguishable.
- Missing usage and unrun stages remain unknown or unrun; retries, failures, interventions, and consumed resources remain visible.
- Existing `benchmark-report/1` and saved v1 scoring remain supported; new records use explicit versioned schemas.
- This branch's planning deliverable does not authorize paid model trials, physical operations, or modification of the frozen baseline.

## Review Focus

1. A client repeats a start after losing its reply, including after a child exists but its suite link was not saved: return the same suite and child; pin in Task 2's interrupted-dispatch test.
2. Configuration or installed assets change while a suite is stopped: preserve the original runtime configuration and block mismatched case snapshots; pin in Tasks 2 and 3's recovery tests.
3. A child says it failed, or stops while a device effect is uncertain: advance only on recorded model failure after cleanup; pin in Task 3's category, cancel and uncertainty tests.
4. Reports contain incomplete trials, unknown tokens, private options or incompatible seeds/budgets: retain planned denominators, use explicit export fields and refuse a paired claim; pin in Task 4's aggregation and comparison tests.
5. A Windows MCP client disconnects, requests another home, or has no owner: retain the existing owner or refuse before creation; pin in Task 5's real stdio tests.

---

## Boundaries, dependencies and file map

Read `AGENTS.md`, `CONTEXT.md`, the spec and the completed plans 01/02 before execution. This plan starts only after their interfaces are available; pending calibration is an explicit run blocker, not a reason to substitute replay for a model trial.

| File | Responsibility |
|---|---|
| Create `src/generative_driver/benchmark_support/suites.py` | Normalize suite manifests, expand stable slots, freeze selected configuration/pins, classify observed child lifecycle and build child start requests |
| Create `src/generative_driver/benchmark_support/suite_store.py` | Durable suite/slot/event storage using the controller's existing SQLite connection context; no agents, devices or independent owner |
| Create `src/generative_driver/benchmark_support/suite_reporting.py` | Allowlisted suite export, descriptive aggregation and pure saved comparison |
| Modify `src/generative_driver/configurator.py` | Suite method dispatch, one scheduler per suite, recorded child outcome category, recovery, budget and cancellation integration |
| Modify `src/generative_driver/benchmark_support/cases.py` | Add trusted fault-category metadata at existing legacy qualification rejections; preserve existing outcomes, routing and immutable assets |
| Modify `src/generative_driver/benchmark.py` | Add `benchmark suite` parser and thin client calls, without replacing legacy run/score/compare |
| Modify `src/generative_driver/mcp.py` | Six thin suite tools with the existing same-home and Windows ownership boundary |
| Modify `src/generative_driver/reporting.py` | Add keyword-only `autostart` to `report_run`; suite export uses the existing owner and pinned report/2 path |
| Create `src/generative_driver/resources/bench/suites/development-pilot.json` | Public six-entry manifest, three repeats by default; contains no vectors, reference answers or credentials |
| Create `tests/suite_fixtures.py` | Manifest builders, bounded public-event waits and explicitly scripted external worker fixtures |
| Create `tests/test_suite_manifest.py`, `tests/test_suite_store.py`, `tests/test_suite_service.py`, `tests/test_suite_reporting.py`, `tests/test_suite_interfaces.py` | Focused public-library, configurator, installed CLI and real MCP contracts |
| Modify `bench/README.md`, `docs/setup.md`, `docs/implementation/benchmark-tdd.md` | Exact suite setup, lifecycle, comparison and observed red/green evidence |

Consume these names exactly from earlier increments:

```text
case_ids() -> tuple[str, ...]
resolve_case(case_id: str) -> CaseDefinition
adapter_for(case_id: str)
pin_case(case_id: str, scenario_id: str | None, case_seed: int) -> dict
snapshot_case(run_dir: Path, pin: dict, provenance: dict) -> dict
read_snapshot(run_dir: Path) -> dict
require_snapshot(run_dir: Path, pin: dict) -> dict
regrade_v2(report: dict, evidence_path: Path, password_file: Path) -> dict
```

New-case `Controller.call('result', ...)` supplies `case_pin`, `execution_snapshot` and the recorded `outcome_category`. `benchmark-report/2` carries those identities, `final_evaluation`, `maintenance`, existing stage counts, usage and interventions. Do not invent alternate registry names or inspect evaluator plaintext to implement a suite.

### Stable manifest and request shape

```json
{
  "schema": "benchmark-suite/1",
  "id": "development-pilot",
  "version": "1",
  "entries": [
    {"case": "tq9-v2", "scenario": "semantic", "case_seed": 0},
    {"case": "tq9-v2", "scenario": "control", "case_seed": 0},
    {"case": "sampled-sensor-v1", "scenario": "semantic", "case_seed": 0},
    {"case": "sampled-sensor-v1", "scenario": "control", "case_seed": 0},
    {"case": "parameter-store-v1", "scenario": "semantic", "case_seed": 0},
    {"case": "parameter-store-v1", "scenario": "control", "case_seed": 0}
  ],
  "repetitions": 3,
  "child_budget_seconds": 10800,
  "suite_budget_seconds": 216000,
  "max_active_children": 1
}
```

This is 18 planned children with a 60-hour suite wall limit, not authorization to execute them. A one-repeat development request changes `repetitions` to `1` and chooses its total suite limit explicitly. Expansion is repeat-major: all six entries in repeat 0, then repeat 1, then repeat 2. Preserve entry order; do not sort the submitted experiment.

Start body: `{'manifest': manifest, 'executor': 'codex', 'options': options, 'request_id': optional_id}`. Options permit `renode`, `ghidra_home`, `java_home`, `evaluator_password_files` keyed by selected case ID, and `scoped_tool_approval: 'emulator'`; none of these private paths is a public result field. Runtime selection comes from the selected home's saved executor configuration and is frozen in full. An MCP options `home` must match its configured home and is removed before storing evaluator options.

## Task 1: Validate, pin and expand a suite without executing it

**Files:** Create `benchmark_support/suites.py`, the public pilot JSON, `tests/suite_fixtures.py` and `tests/test_suite_manifest.py`. All source paths are beneath `src/generative_driver/` as mapped above.

**Interfaces:** Produce `normalize_manifest(manifest: dict) -> dict`, `expand_trials(manifest: dict) -> list[dict]`, `freeze_suite(manifest: dict, executor: str, executor_config: dict, options: dict, provenance: dict) -> dict`, and `child_request(frozen: dict, trial: dict) -> dict`. A frozen suite contains normalized manifest/hash, executor/config, options, entry pins, provenance and expanded slots. Stored private options remain private. Public trial keys are `entry-000-repeat-000`; ordinals start at zero.

- [ ] **Step 1: Add the first failing normalization/expansion test.** Put the literal manifest above in `pilot_manifest()` in `tests/suite_fixtures.py`; return a new dictionary on every call using `json.loads` of the JSON literal. Add this test:

```python
import copy
import unittest
from suite_fixtures import pilot_manifest

class ManifestTests(unittest.TestCase):
    def test_repeat_slots_preserve_six_entries_and_three_families(self):
        from generative_driver.benchmark_support.suites import normalize_manifest, expand_trials
        manifest = normalize_manifest(pilot_manifest())
        slots = expand_trials(manifest)
        self.assertEqual(len(slots), 18)
        self.assertEqual([s['entry_index'] for s in slots[:6]], list(range(6)))
        self.assertEqual([s['repeat_index'] for s in slots], [0]*6+[1]*6+[2]*6)
        self.assertEqual(slots[6]['trial_key'], 'entry-000-repeat-001')
        self.assertEqual(len({s['case'] for s in slots}), 3)
        self.assertEqual({s['case_seed'] for s in slots}, {0})
        changed = copy.deepcopy(manifest)
        changed['repetitions'] = 1
        self.assertEqual(len(expand_trials(changed)), 6)
```

- [ ] **Step 2: Run the red test.** `PYTHONPATH=src:tests python -m unittest test_suite_manifest.ManifestTests.test_repeat_slots_preserve_six_entries_and_three_families -v`. Expect an import failure because the suite module does not exist. On PowerShell set `$env:PYTHONPATH='src;tests'` before the same `python -m unittest` command. Use the installed development environment's Python.

- [ ] **Step 3: Implement normalization and expansion.** Reject unknown top-level/entry fields, blank ID/version, empty entries, duplicate `(case, scenario, case_seed)` entries, bool/noninteger seeds and repetition counts, unsupported schemas, nonfinite/nonpositive budgets and any concurrency other than integer 1. Bound each budget to seven days, matching current child limits; reject a child budget greater than the suite budget. Do not read optional tools or truth in this code.

```python
import copy
import hashlib
import json
import math

def normalize_manifest(manifest):
    required = {'schema','id','version','entries','repetitions',
                'child_budget_seconds','suite_budget_seconds','max_active_children'}
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ValueError('Suite manifest fields are incomplete or unknown')
    result = copy.deepcopy(manifest)
    if result['schema'] != 'benchmark-suite/1':
        raise ValueError('Unsupported suite schema')
    if any(not isinstance(result[k], str) or not result[k].strip() for k in ('id','version')):
        raise ValueError('Suite id and version must be nonblank strings')
    if type(result['repetitions']) is not int or result['repetitions'] < 1:
        raise ValueError('Repetitions must be a positive integer')
    if type(result['max_active_children']) is not int or result['max_active_children'] != 1:
        raise ValueError('Suites support one active child')
    for key in ('child_budget_seconds','suite_budget_seconds'):
        value = result[key]
        if type(value) not in (int,float) or not math.isfinite(value) or not 0 < value <= 604800:
            raise ValueError('Suite budgets must be finite, positive and at most seven days')
    if result['child_budget_seconds'] > result['suite_budget_seconds']:
        raise ValueError('Child budget exceeds suite budget')
    if not isinstance(result['entries'], list) or not result['entries']:
        raise ValueError('Suite requires entries')
    identities = set()
    for entry in result['entries']:
        if not isinstance(entry, dict) or set(entry) != {'case','scenario','case_seed'}:
            raise ValueError('Invalid suite entry fields')
        if any(not isinstance(entry[k], str) or not entry[k].strip() for k in ('case','scenario')):
            raise ValueError('Case and scenario must be nonblank strings')
        if type(entry['case_seed']) is not int:
            raise ValueError('Case seed must be an integer, never a model seed')
        identity = (entry['case'], entry['scenario'], entry['case_seed'])
        if identity in identities:
            raise ValueError('Duplicate suite entry')
        identities.add(identity)
    return result

def expand_trials(manifest):
    manifest = normalize_manifest(manifest)
    return [dict(entry, ordinal=repeat*len(manifest['entries'])+index,
                 entry_index=index, repeat_index=repeat,
                 trial_key=f'entry-{index:03d}-repeat-{repeat:03d}')
            for repeat in range(manifest['repetitions'])
            for index, entry in enumerate(manifest['entries'])]
```

- [ ] **Step 4: Run green, then add one validation test at a time.** Add subtests rejecting `True` and `float('nan')` as seeds, repetition `True`, concurrency `2`, duplicate entries, unknown keys and infinite budgets. Each mutation must raise `ValueError`. Add a separate registry-backed freeze test showing `setup-smoke` and `bme280` cannot enter the emulated aggregate, and unknown cases fail before any home/service storage exists. Run `python -m unittest test_suite_manifest -v` after each red/green slice.

```python
def test_invalid_numeric_or_duplicate_entries_are_rejected(self):
    from generative_driver.benchmark_support.suites import normalize_manifest
    mutations = [lambda m: m['entries'][0].update(case_seed=True),
                 lambda m: m['entries'][0].update(case_seed=float('nan')),
                 lambda m: m.update(repetitions=True),
                 lambda m: m.update(max_active_children=2),
                 lambda m: m.update(suite_budget_seconds=float('inf')),
                 lambda m: m['entries'].append(dict(m['entries'][0]))]
    for mutate in mutations:
        manifest = pilot_manifest()
        mutate(manifest)
        with self.subTest(manifest=manifest), self.assertRaises(ValueError):
            normalize_manifest(manifest)

def test_replay_physical_and_unknown_cases_do_not_freeze(self):
    from generative_driver.benchmark_support.suites import freeze_suite
    for case in ('setup-smoke','bme280','unknown-case'):
        manifest = pilot_manifest()
        manifest['entries'] = [{'case':case,'scenario':'control','case_seed':0}]
        with self.subTest(case=case), self.assertRaises(ValueError):
            freeze_suite(manifest, 'codex', {'command':['scripted-runtime']}, {}, {})
```

- [ ] **Step 5: Implement freezing and child request projection.** Call `resolve_case` and `pin_case` for every entry. Calibration is an object with `status`; require `passed` for every v2/new case. Only registry-normalized legacy TQ9 may use `legacy-not-required`. Require execution `actual-agent-emulation`, scope `full-workflow` and one evidence track (the pilot uses `firmware`). Preserve the exact pins, including scenario/seed. Canonical hash encoding is `json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')`; `manifest_sha256` hashes the normalized public manifest only. Defensive-copy config/options/provenance. Reject missing saved executor commands and options outside the allowlist above. Apply `validate_scoped_approval` to each projected child before admitting the suite; this uses plan 01's registered emulator policy.

```python
def freeze_suite(manifest, executor, executor_config, options, provenance):
    from .registry import resolve_case, pin_case
    normalized = normalize_manifest(manifest)
    allowed = {'renode','ghidra_home','java_home','evaluator_password_files','scoped_tool_approval'}
    if set(options) - allowed:
        raise ValueError('Unknown suite options')
    if executor not in ('codex','goose') or not executor_config.get('command'):
        raise ValueError('Configure a worker runtime before starting a suite')
    if options.get('scoped_tool_approval') not in (None,'emulator'):
        raise ValueError('Suites permit only explicit emulator approval')
    selected = {e['case'] for e in normalized['entries']}
    handles = options.get('evaluator_password_files', {})
    if not isinstance(handles, dict) or set(handles)-selected or any(
            not isinstance(v,str) or not v for v in handles.values()):
        raise ValueError('Evaluator handles must name selected cases and file paths')
    definitions = [resolve_case(e['case']) for e in normalized['entries']]
    if any(c.execution != 'actual-agent-emulation' for c in definitions):
        raise ValueError('Suite requires emulated actual-agent cases')
    if len({c.evidence_track for c in definitions}) != 1:
        raise ValueError('Mixed evidence tracks cannot share a suite aggregate')
    for case in definitions:
        calibration = case.manifest.get('calibration', {})
        status = calibration.get('status') if isinstance(calibration, dict) else None
        allowed_status = 'legacy-not-required' if case.id == 'tq9' else 'passed'
        if status != allowed_status:
            raise ValueError('Suite contains a case without required reference calibration')
        if case.manifest.get('scope') != 'full-workflow':
            raise ValueError('Stage-local evidence cannot enter a full-workflow suite')
    pins = [pin_case(e['case'], e['scenario'], e['case_seed']) for e in normalized['entries']]
    encoded = json.dumps(normalized, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    return copy.deepcopy({'manifest':normalized,'manifest_sha256':hashlib.sha256(encoded).hexdigest(),
        'request':{'manifest':normalized,'executor':executor,'options':options},
        'executor':executor,'executor_config':executor_config,'options':options,'scope':'full-workflow',
        'entry_pins':pins,'entry_effects':[list(c.default_effects) for c in definitions],
        'provenance':provenance,'trials':expand_trials(normalized)})

def child_request(frozen, trial):
    options = copy.deepcopy(frozen['options'])
    password_files = options.pop('evaluator_password_files', {})
    approval = options.pop('scoped_tool_approval', None)
    if trial['case'] in password_files:
        options['evaluator_password_file'] = password_files[trial['case']]
    pin = copy.deepcopy(frozen['entry_pins'][trial['entry_index']])
    return {'goal':'Execute one registered benchmark trial with checked evidence.',
            'case':trial['case'], 'case_pin':pin,
            'executor':frozen['executor'],
            'executor_config':copy.deepcopy(frozen['executor_config']),
            'case_options':options, 'request_id':trial['child_request_id'],
            'effects':list(frozen['entry_effects'][trial['entry_index']]),
            'scoped_tool_approval':approval,
            'budget_seconds':frozen['manifest']['child_budget_seconds']}
```

Pin identity and scenario are evaluator-owned; the adapter receives the pin through plan 01's existing options projection. Do not put the suite manifest, password mapping, scenario name or neighboring trial context into a worker prompt. `entry_effects` comes from `CaseDefinition.default_effects`, constrained by the saved explicit approval; it is not supplied by a worker.

- [ ] **Step 6: Commit the independently reviewable manifest feature.** After `test_suite_manifest` passes and `git diff --check` is clean: `git add src/generative_driver/benchmark_support/suites.py src/generative_driver/resources/bench/suites/development-pilot.json tests/suite_fixtures.py tests/test_suite_manifest.py` then `git commit -m "feat: validate and freeze benchmark suite manifests"`.

## Task 2: Persist slots and reconcile the child-dispatch crash window

**Files:** Create `benchmark_support/suite_store.py`, `tests/test_suite_store.py`; modify `configurator.py:Controller.__init__`, `Controller.call` and `Controller.start` only at their suite/storage integration points.

**Interfaces:** `SuiteStore(open_db)` receives the controller's context-manager factory; it never creates a daemon. Public persistence operations are `create(frozen, request_id=None) -> dict`, `get(suite_id) -> dict`, `reserve(suite_id) -> dict | None`, `attach(suite_id, ordinal, child_request_id, run_id) -> None`, `settle(suite_id, ordinal, result) -> None`, `set_state(suite_id, status, reason=None) -> None`, `events(suite_id, after=0) -> dict`, `trials(suite_id, offset=0, limit=50) -> dict`, and `recover() -> None`. These are the suite library's persistence boundary; tests call them and the public Controller API, never inspect SQLite rows to assert behavior.

- [ ] **Step 1: Add the failing interrupted-dispatch regression.** In `tests/suite_fixtures.py`, add `sqlite_context(path)` returning a `contextmanager` that opens real SQLite, sets row factory, WAL and 20-second busy timeout, yields within `with connection:`, and closes in `finally`. Add `legacy_manifest(repetitions=1)` returning the Task 1 schema with one `{case:'tq9', scenario:'identity', case_seed:0}` entry and both budgets `120`. This fixture uses the registered historical case only to test scheduling; it reads no truth and runs no emulator.

Plan 01 must normalize legacy `tq9` in memory to allow `identity`, execution `actual-agent-emulation`, evidence track `firmware`, scope `full-workflow`, and calibration `{'status':'legacy-not-required'}`. Verify that registry contract before running these fixtures; do not rewrite its old manifest or mark an uncalibrated new case as legacy.

```python
from contextlib import contextmanager
import sqlite3

def sqlite_context(path):
    @contextmanager
    def open_db():
        db = sqlite3.connect(path, timeout=20)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=20000')
            with db:
                yield db
        finally:
            db.close()
    return open_db

def legacy_manifest(repetitions=1):
    return {'schema':'benchmark-suite/1','id':'scripted-contract','version':'1',
        'entries':[{'case':'tq9','scenario':'identity','case_seed':0}],
        'repetitions':repetitions,'child_budget_seconds':120,
        'suite_budget_seconds':120,'max_active_children':1}
```

```python
import tempfile
import unittest
from pathlib import Path
from generative_driver.configurator import Controller
from suite_fixtures import legacy_manifest, sqlite_context

class StoreTests(unittest.TestCase):
    def test_created_child_is_reused_after_lost_attachment(self):
        from generative_driver.benchmark_support.suites import freeze_suite, child_request
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            controller = Controller(home)
            try:
                frozen = freeze_suite(legacy_manifest(), 'codex',
                    {'command':['missing-scripted-runtime'], 'model':'frozen-model'}, {}, {})
                store = SuiteStore(sqlite_context(home/'runs.sqlite3'))
                suite = store.create(frozen, 'one-request')
                store.set_state(suite['suite_id'], 'running')
                reserved = store.reserve(suite['suite_id'])
                started = controller.call('start', child_request(frozen, reserved))
                # Simulate the persisted boundary before attach, not a timing race:
                # the child exists; discard the store object that was to attach it.
                reopened = SuiteStore(sqlite_context(home/'runs.sqlite3'))
                same = reopened.reserve(suite['suite_id'])
                self.assertEqual(same['child_request_id'], reserved['child_request_id'])
                repeated = controller.call('start', child_request(frozen, same))
                self.assertEqual(repeated['run_id'], started['run_id'])
                self.assertTrue(repeated['duplicate'])
                reopened.attach(suite['suite_id'], same['ordinal'],
                                same['child_request_id'], repeated['run_id'])
                self.assertEqual(reopened.trials(suite['suite_id'])['trials'][0]['run_id'],
                                 started['run_id'])
            finally:
                controller.close()
```

- [ ] **Step 2: Run red.** `python -m unittest test_suite_store.StoreTests.test_created_child_is_reused_after_lost_attachment -v` with the Task 1 development import environment. Expect missing `SuiteStore`.

- [ ] **Step 3: Add durable storage.** Use `CREATE TABLE IF NOT EXISTS` in the existing database. Table contracts:

```sql
CREATE TABLE IF NOT EXISTS suites (
 id TEXT PRIMARY KEY, request_id TEXT UNIQUE, request_json TEXT NOT NULL,
 frozen_json TEXT NOT NULL, status TEXT NOT NULL, reason TEXT,
 created REAL NOT NULL, updated REAL NOT NULL,
 budget_seconds REAL NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS suite_trials (
 suite_id TEXT NOT NULL, ordinal INTEGER NOT NULL, trial_json TEXT NOT NULL,
 child_request_id TEXT NOT NULL UNIQUE, run_id TEXT UNIQUE,
 status TEXT NOT NULL, outcome_category TEXT NOT NULL DEFAULT 'unknown',
 result_json TEXT, PRIMARY KEY(suite_id, ordinal));
CREATE TABLE IF NOT EXISTS suite_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, suite_id TEXT NOT NULL,
 kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
```

`create` uses a single write transaction to insert the suite, all pending slots and `suite.started`. Prefix deterministic child request IDs with the assigned suite ID: `suite:<suite_id>:trial:<ordinal>`. `request_json` is canonical normalized submitted manifest/executor/options before looking up mutable installation/configuration state. For a duplicate caller request ID, compare that request and return the saved frozen suite; do not repin current assets or reread changed runtime settings before deduplicating. Add a `find_request(request_id, canonical_request) -> dict | None` operation and call it before freezing in `Controller.suite_start`.

`reserve` returns the existing `launching`/`running` slot first. Otherwise, under `BEGIN IMMEDIATE`, reject cancelled/non-running suites, select the lowest pending ordinal, mark it `launching`, and append `trial.launching`; commit before invoking a child. The persisted `child_request_id` already exists from creation. Never keep this transaction open during `Controller.start`.

```python
def attach(self, suite_id, ordinal, child_request_id, run_id):
    with self.open_db() as db:
        row = db.execute('SELECT * FROM suite_trials WHERE suite_id=? AND ordinal=?',
                         (suite_id, ordinal)).fetchone()
        if row is None or row['child_request_id'] != child_request_id:
            raise ValueError('Unknown or stale suite slot')
        if row['run_id'] not in (None, run_id):
            raise ValueError('Suite slot already owns a different child')
        if row['run_id'] == run_id:
            return
        db.execute("UPDATE suite_trials SET run_id=?,status='running' WHERE suite_id=? AND ordinal=?",
                   (run_id, suite_id, ordinal))
        db.execute('INSERT INTO suite_events(suite_id,kind,payload,created) VALUES(?,?,?,?)',
                   (suite_id, 'trial.started', json.dumps({'ordinal':ordinal,'run_id':run_id}), time.time()))
```

`get` returns private frozen storage only to controller/library callers. Public status/result projection is separate. `trials` validates integer non-bool `offset >= 0`, `1 <= limit <= 100`, returns ordered rows plus `next_offset`, and exposes only ordinal/key/case/scenario/seed/repeat/run ID/lifecycle/category. `events` uses integer cursor and ordered pages of at most 500, matching run events. `settle` refuses mismatched children, uncertain results or `stopping: true`; save only the lifecycle/category and public report identity needed for scheduling. Repeated identical settlement is idempotent. `recover` blocks queued/running suites and appends `suite.recovered`; it does not start or retry children.

- [ ] **Step 4: Run green, then add deduplication/configuration and pagination tests.** Add tests that `create` with the same request returns the same suite, a changed manifest under that request raises, a second concurrent reservation returns the existing slot, conflicting `attach` raises, and 101 requested results raises. For frozen configuration, change the home's `config.json` after the suite exists; duplicate `suite_start` must return the original ID/configuration and a resumed slot's `child_request` must still contain `frozen-model`. Use public suite status/events/result, not SQL queries. Run `python -m unittest test_suite_store test_suite_manifest -v`.

```python
def test_storage_reservation_and_duplicate_requests_are_idempotent(self):
    from copy import deepcopy
    from generative_driver.benchmark_support.suites import freeze_suite
    from generative_driver.benchmark_support.suite_store import SuiteStore
    with tempfile.TemporaryDirectory() as temporary:
        store = SuiteStore(sqlite_context(Path(temporary)/'runs.sqlite3'))
        frozen = freeze_suite(legacy_manifest(2), 'codex', {'command':['scripted']}, {}, {})
        first = store.create(frozen, 'request')
        self.assertEqual(store.create(frozen, 'request')['suite_id'], first['suite_id'])
        changed = deepcopy(frozen)
        changed['request']['manifest']['repetitions'] = 3
        with self.assertRaises(ValueError):
            store.create(changed, 'request')
        store.set_state(first['suite_id'], 'running')
        one, two = store.reserve(first['suite_id']), store.reserve(first['suite_id'])
        self.assertEqual(one['child_request_id'], two['child_request_id'])
        store.attach(first['suite_id'], one['ordinal'], one['child_request_id'], 'run-one')
        with self.assertRaises(ValueError):
            store.attach(first['suite_id'], one['ordinal'], one['child_request_id'], 'run-other')
        with self.assertRaises(ValueError):
            store.trials(first['suite_id'], limit=101)
```

- [ ] **Step 5: Commit storage after focused green.** `git add src/generative_driver/benchmark_support/suite_store.py src/generative_driver/configurator.py tests/suite_fixtures.py tests/test_suite_store.py` then `git commit -m "feat: persist suite trials and reconcile child dispatch"`.

## Task 3: Integrate scheduling, failure categories, cancellation and recovery

**Files:** Modify `configurator.py:Controller.call`, `start`, `_run`, `_state`, `close`; extend `benchmark_support/suites.py` and `suite_store.py`; modify only trusted failure metadata in `benchmark_support/cases.py:check_stage`; create `tests/test_suite_service.py`.

**Interfaces:** Handle `suite_start/status/events/result/cancel/resume` before `_row(run_id)`. Add `Controller.suite_start(params)`, `_run_suite(suite_id, wake)`, `_spawn_suite(suite_id)` and `_suite_call(method, params)`. Add pure `child_disposition(result: dict) -> str`, returning `wait`, `advance`, or `block`. Recorded `outcome_category` enum is `completed`, `model`, `host`, `operator`, `cancelled`, `unknown`; public clients cannot assign it. Suite status fields are `ok`, `suite_id`, `status`, `active_child_id`, `planned`, `completed`, `reason`, `created`, `updated`, `stopping`.

- [ ] **Step 1: Add the failure-category red test.** This public pure decision test does not rely on error-message spelling:

```python
import unittest

class DecisionTests(unittest.TestCase):
    def test_only_a_recorded_settled_model_failure_advances(self):
        from generative_driver.benchmark_support.suites import child_disposition
        stopped = {'status':'blocked','stopping':False,'uncertain_effect':False,
                   'outcome_category':'model','reason':'An arbitrary localized message'}
        self.assertEqual(child_disposition(stopped), 'advance')
        for category in ('host','operator','unknown','cancelled'):
            self.assertEqual(child_disposition(dict(stopped, outcome_category=category)), 'block')
        self.assertEqual(child_disposition(dict(stopped, uncertain_effect=True)), 'block')
        self.assertEqual(child_disposition(dict(stopped, stopping=True)), 'wait')
        self.assertEqual(child_disposition(dict(stopped, status='running')), 'wait')
```

- [ ] **Step 2: Run red, then implement the decision.** `python -m unittest test_suite_service.DecisionTests -v` must fail on the missing function, then pass after:

```python
def child_disposition(result):
    if result.get('stopping') or result['status'] in ('queued','running'):
        return 'wait'
    if result.get('uncertain_effect'):
        return 'block'
    category = result.get('outcome_category', 'unknown')
    if category == 'completed' and result['status'] == 'completed':
        return 'advance'
    if category == 'model' and result['status'] in ('failed','blocked'):
        return 'advance'
    return 'block'
```

- [ ] **Step 3: Add and persist trusted child categories.** Migrate the existing `runs` table by inspecting `PRAGMA table_info(runs)` and adding `outcome_category TEXT NOT NULL DEFAULT 'unknown'` only if absent. Extend `_state(..., outcome_category=None)` and its event payload; include the saved category in status/result. On completed/cancelled lifecycle transitions save the corresponding category. Runtime launch/process failures and caught infrastructure exceptions are `host`; unavailable operator references/permissions are `operator` only when a trusted checker says so. After independent failed checks, use `checked.get('fault', 'unknown')` if it is `model`, `host` or `operator`; exhausted model repairs preserve `model`. A worker report's own fault claim never supplies this field. Missing monitor evidence stays host/unknown, not model. A maintenance-cycle limit must retain the trusted evaluator category rather than invent one.

```python
# In Controller.__init__, within its existing migration connection:
columns = {r['name'] for r in db.execute('PRAGMA table_info(runs)')}
if 'outcome_category' not in columns:
    db.execute("ALTER TABLE runs ADD COLUMN outcome_category TEXT NOT NULL DEFAULT 'unknown'")

# At a trusted independent-check failure site in _run:
category = checked.get('fault', 'unknown')
if category not in {'model','host','operator'}:
    category = 'unknown'
self._state(run_id, 'blocked', checked.get('reason', 'Independent checks failed'),
            outcome_category=category)
```

Reset the category to `unknown` when explicitly resuming a child; retain old categories in durable events. The `_state` update and its category event must share the same database transaction. Do not let a later cancellation race turn an uncertain effect into a model failure eligible for suite continuation.

At the existing legacy TQ9 semantic qualification rejection in `benchmark_support/cases.py:check_stage`, add `'fault': 'model'` to the host-verified rejection dictionary if absent. Keep its existing `ok`, reason, feedback and route unchanged. This metadata identifies that specific independently evaluated candidate defect; it must not tag every failed legacy check as model-owned. Add an assertion to the scripted qualification regression that the recorded category is `model` after repair exhaustion, so the two-child fixture cannot accidentally pass by guessing from its message.

- [ ] **Step 4: Add a scripted two-child service regression and run red.** Move the external acquisition/unqualified-model script pattern from `tests/test_service_repair.py` into `write_scripted_case_worker(directory)` in `tests/suite_fixtures.py`, preserving the original test. The helper writes a Python subprocess that reads the assigned prompt, imports the real public `client.call`, uses `acquire_firmware_artifact` for acquisition, emits its hashed artifact report, and copies the public `setup-smoke/model.json` into an interpretation workspace. It does not sleep or emulate success: the actual TQ9 qualification gate must reject this unrelated model through the existing repair limit. Label the helper and test explicitly as a scripted contract fixture, never a model baseline. Use the full existing script as the body, removing only its `DEFECTS.json` twenty-second hold.

```python
import sys
from pathlib import Path
import generative_driver

def write_scripted_case_worker(directory):
    path = Path(directory)/'scripted_suite_worker.py'
    source = '''import sys,json,pathlib,hashlib,shutil
sys.path.insert(0,PACKAGE_PARENT)
from generative_driver.client import call
from generative_driver.toolkit import resources_root
prompt=sys.stdin.read()
if prompt.startswith('Execute exactly'):
    task=json.loads(prompt.split('\\n',1)[1])
    args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
    identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
    image=pathlib.Path(task['inputs'][0])
    out=call('tool',{**identity,'name':'acquire_firmware_artifact','arguments':{
        'source_path':str(image),'expected_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
        'origin':'provided_binary'}},args[args.index('--home')+1])
    assert out.get('available'),out
    report={'status':'completed','summary':'scripted import','artifacts':[
        {'path':out[k],'sha256':hashlib.sha256(pathlib.Path(out[k]).read_bytes()).hexdigest(),'kind':'evidence'}
        for k in ('artifact','provenance')],
        'checks':[{'name':'import','status':'pass','evidence':[out['provenance']]}],'unresolved':[]}
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
else:
    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')
'''
    source = source.replace('PACKAGE_PARENT', repr(str(Path(generative_driver.__file__).resolve().parents[1])))
    path.write_text(source, encoding='utf-8')
    return [sys.executable, str(path)]

def wait_suite(call, suite_id, home, predicate):
    import time
    hard_stop, idle_stop, cursor = time.monotonic()+60, time.monotonic()+10, 0
    last_event, child_cursors = None, {}
    while time.monotonic() < min(hard_stop, idle_stop):
        state = call('suite_status', {'suite_id':suite_id}, home)
        page = call('suite_events', {'suite_id':suite_id,'after':cursor}, home)
        if page['cursor'] > cursor:
            cursor, idle_stop = page['cursor'], time.monotonic()+10
            last_event = page['events'][-1]
        child = state.get('active_child_id')
        if child:
            child_page = call('events', {'run_id':child,'after':child_cursors.get(child,0)}, home)
            if child_page['cursor'] > child_cursors.get(child,0):
                child_cursors[child] = child_page['cursor']
                idle_stop, last_event = time.monotonic()+10, child_page['events'][-1]
        if predicate(state):
            return state
        if state['status'] in ('failed','blocked','cancelled','completed'):
            raise AssertionError({'unexpected_terminal':state,'last_event':last_event})
        time.sleep(.02)
    raise AssertionError({'wait_expired':state,'last_event':last_event})
```

```python
class ServiceTests(unittest.TestCase):
    def test_model_failure_finishes_a_measurement_and_starts_the_next_slot(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        from suite_fixtures import legacy_manifest, write_scripted_case_worker, wait_suite
        with tempfile.TemporaryDirectory(prefix='suite scripted ') as temporary:
            configure('codex', write_scripted_case_worker(Path(temporary)), home=temporary)
            try:
                suite = call('suite_start', {'manifest':legacy_manifest(2),
                    'executor':'codex','options':{},'request_id':'two-scripted-children'}, temporary)
                state = wait_suite(call, suite['suite_id'], temporary,
                                   lambda s: s['status'] == 'completed')
                page = call('suite_result', {'suite_id':suite['suite_id'],'offset':0,'limit':100}, temporary)
                self.assertEqual(state['completed'], 2)
                self.assertEqual(len({t['run_id'] for t in page['trials']}), 2)
                self.assertEqual([t['outcome_category'] for t in page['trials']], ['model','model'])
                events = call('suite_events', {'suite_id':suite['suite_id']}, temporary)['events']
                kinds = [e['kind'] for e in events]
                self.assertLess(kinds.index('trial.finished'),
                                [i for i,k in enumerate(kinds) if k == 'trial.started'][1])
            finally:
                call('shutdown', {}, temporary)
```

Add imports `tempfile`, `Path` at the test module top. Implement `wait_suite(call, suite_id, home, predicate)` in the fixture module: poll public status/events; refresh a ten-second inactivity deadline only on an increased durable event cursor; retain a 60-second outer bound; fail early on unexpected failed/cancelled/blocked states with the last status/event. Sleep briefly only between unsuccessful polls. Do not assume an assignment exists after a fixed sleep. For tests expecting a blocked/cancelled state, evaluate `predicate` before the unexpected-terminal check.

- [ ] **Step 5: Implement the scheduler around existing child methods.** Track scheduler threads separately from run workers, with a wake event and a per-suite lifecycle lock. Dispatch and cancellation share that lock, but never join a worker or hold a database write transaction under it. The controller reserves a slot, commits, calls `self.start(child_request(...))`, attaches the returned run, and observes `self.call('status', {'run_id':...})`. It cannot call an agent, toolkit or emulator directly.

```python
# Core of Controller._run_suite; store mutations each own a short transaction.
trial = self._suites.reserve(suite_id)
if trial is None:
    self._suites.set_state(suite_id, 'completed')
    return
if trial['run_id'] is None:
    started = self.start(child_request(frozen, trial))
    self._suites.attach(suite_id, trial['ordinal'], trial['child_request_id'], started['run_id'])
    trial['run_id'] = started['run_id']
observed = self.call('status', {'run_id':trial['run_id']})
decision = child_disposition(observed)
if decision == 'advance':
    self._suites.settle(suite_id, trial['ordinal'], observed)
elif decision == 'block':
    self._suites.set_state(suite_id, 'blocked', observed.get('reason'))
    return
else:
    wake.wait(.1)
    wake.clear()
```

Put this step in a loop that first checks closing/cancelled state and `created + effective_suite_budget_seconds`. Re-read the stored frozen suite, not `config.json`, and recheck cancellation while holding the lifecycle lock immediately before child start. If cancellation arrives during that synchronous start, attach its result before releasing the lock so cancellation finds the child. The suite wall limit blocks new dispatch and cancels an active child through its existing cancellation path; an interrupted uncertain effect remains uncertain. Successful child creation alone does not settle a slot.

For `suite_cancel`, persist `cancelled=1` and `suite.cancel_requested` first; then cancel the attached child outside the write transaction and wait for public `stopping` to clear within the existing shutdown bounds. Report `stopping:true` while cleanup remains outstanding. Recheck a reserved-but-unattached slot by deterministic child request ID after the scheduler has drained; never create a child merely to cancel it. Add a read-only `Controller.find_request(request_id) -> dict | None` that returns an existing child's ID/status using the existing unique run request ID; use this for reconciliation where creation is not allowed. Keep the suite cancelled, and never dispatch the next slot.

On daemon startup invoke `SuiteStore.recover` after existing child recovery. `suite_resume` verifies every pinned snapshot/implementation before any dispatch, reconciles an existing child via `find_request`, and calls its existing `resume` only when necessary and allowed. An already running child is observed, not resumed again. An uncertain child returns the existing reconciliation instruction. No child is replaced to escape its budget or evidence checks. Public resume may accept `suite_budget_seconds` plus a nonblank `budget_reason`, increasing only the absolute total from original suite creation; repeat totals add no time and emit no duplicate extension. Child budget extension remains the existing explicit child `resume` operation. Store suite extension events and preserve original/effective totals.

- [ ] **Step 6: Add lifecycle regressions one at a time.** Add actual tests for: missing runtime gives `host` and blocks before slot two; updating saved runtime configuration while blocked preserves the first frozen command on resume; forced owner process loss followed by reconnect emits recovery and starts no child before explicit resume; a cancelled fixture worker exits before a next trial appears; and an outstanding delayed external tool reports stopping/uncertainty and prevents advance. Reuse the real delayed localhost HTTP/TCP fixtures from `tests/test_service.py`/`test_service_lifecycle.py`; do not replace controller collaborators with mocks. For asset mismatch use a copied temporary execution snapshot, change its bytes after a stop, and assert public resume refusal before a new assignment. Add public budget tests preserving original creation time and repeated-target idempotence. Run `python -m unittest test_suite_service test_service_repair test_service_lifecycle -v`.

```python
def test_host_blockage_preserves_the_child_and_frozen_configuration(self):
    from generative_driver.client import call
    from generative_driver.setup import configure
    from suite_fixtures import legacy_manifest, wait_suite
    with tempfile.TemporaryDirectory() as home:
        configure('codex', ['missing-first-runtime'], model='first-model', home=home)
        request = {'manifest':legacy_manifest(2),'executor':'codex','options':{},'request_id':'freeze'}
        try:
            suite = call('suite_start', request, home)
            blocked = wait_suite(call, suite['suite_id'], home, lambda s:s['status']=='blocked')
            original_child = blocked['active_child_id']
            configure('codex', ['missing-second-runtime'], model='second-model', home=home)
            self.assertEqual(call('suite_start', request, home)['suite_id'], suite['suite_id'])
            self.assertTrue(call('suite_resume', {'suite_id':suite['suite_id']}, home)['ok'])
            again = wait_suite(call, suite['suite_id'], home, lambda s:s['status']=='blocked')
            self.assertEqual(again['active_child_id'], original_child)
            result = call('result', {'run_id':original_child}, home)
            self.assertEqual(result['agent']['model'], 'first-model')
            self.assertEqual(result['outcome_category'], 'host')
            trials = call('suite_result', {'suite_id':suite['suite_id']}, home)['trials']
            self.assertEqual(sum(t.get('run_id') is not None for t in trials), 1)
        finally:
            call('shutdown', {}, home)

def test_cancellation_drains_the_active_child_without_dispatching_another(self):
    from generative_driver.client import call
    from generative_driver.setup import configure
    from suite_fixtures import legacy_manifest, wait_suite
    with tempfile.TemporaryDirectory() as home:
        configure('codex', [sys.executable,'-c','import time; time.sleep(120)'], home=home)
        try:
            suite = call('suite_start', {'manifest':legacy_manifest(2),'executor':'codex','options':{}}, home)
            active = wait_suite(call, suite['suite_id'], home, lambda s:bool(s['active_child_id']))
            stopped = call('suite_cancel', {'suite_id':suite['suite_id']}, home)
            self.assertEqual(stopped['status'], 'cancelled')
            self.assertFalse(stopped['stopping'])
            self.assertFalse(call('status', {'run_id':active['active_child_id']}, home)['stopping'])
            trials = call('suite_result', {'suite_id':suite['suite_id']}, home)['trials']
            self.assertEqual(sum(t.get('run_id') is not None for t in trials), 1)
        finally:
            call('shutdown', {}, home)

def test_uncertain_child_never_advances_even_when_reported_as_model_failure(self):
    from generative_driver.benchmark_support.suites import child_disposition
    result = {'status':'blocked','outcome_category':'model','stopping':False,'uncertain_effect':True}
    self.assertEqual(child_disposition(result), 'block')
```

The last pure decision test complements the existing public delayed-effect test; add the suite wrapper to that fixture so both the child result and suite status are inspected. For recovery, construct `SuiteStore` over the same temporary database after an actual stopped controller, call `recover()`, and assert a saved `launching` slot is blocked with one `suite.recovered` event, an unchanged child request ID and no new `trial.started`. The interrupted-dispatch test in Task 2 independently covers the lost child attachment boundary. The final installed service gate additionally exercises process loss; neither pure state tests nor graceful shutdown alone claims process-loss coverage.

- [ ] **Step 7: Commit scheduler and lifecycle after focused green.** `git add src/generative_driver/configurator.py src/generative_driver/benchmark_support/suites.py src/generative_driver/benchmark_support/suite_store.py src/generative_driver/benchmark_support/cases.py tests/suite_fixtures.py tests/test_suite_service.py` then `git commit -m "feat: schedule benchmark suites through the configurator"`.

## Task 4: Export allowlisted aggregates and compare saved experiments

**Files:** Create `benchmark_support/suite_reporting.py`, `tests/test_suite_reporting.py`; modify `reporting.py:report_run` only to accept keyword-only `autostart=True` and forward it on every client request. Preserve its legacy behavior; current export is service-backed, not offline.

**Interfaces:** `aggregate_suite(manifest: dict, trials: list[dict], reports: dict[str,dict], qualification: str) -> dict`, `report_suite(suite_id: str, home=None, output=None, *, evidence_files=None, password_files=None) -> dict`, and `compare_suites(before, after) -> dict`. Public schema is `benchmark-suite-report/1`; comparison schema is `benchmark-suite-comparison/1`. Report export uses the existing owner with `autostart=False`; offline comparison reads only its supplied JSON. Regrading uses plan 01's `regrade_v2`, never a new evaluator/device call.

- [ ] **Step 1: Add a literal aggregation fixture and failing denominator test.** The report dictionary below is a public toy fixture. In live export, `qualification` is chosen only after reading trusted controller records or successfully regrading sidecars; it is not accepted from submitted report files.

```python
import unittest

class AggregateTests(unittest.TestCase):
    def test_unstarted_slots_and_failed_resources_remain_in_the_result(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        from suite_fixtures import legacy_manifest
        manifest = legacy_manifest()
        manifest['entries'] = [{'case':case,'scenario':scenario,'case_seed':0}
            for case,scenario in [('a','semantic'),('b','control'),('c','control')]]
        trials = [
            {'trial_key':'a','ordinal':0,'family':'one','case':'a','scenario':'semantic',
             'repeat_index':0,'case_seed':0,'status':'finished','run_id':'r1','outcome_category':'completed'},
            {'trial_key':'b','ordinal':1,'family':'one','case':'b','scenario':'control',
             'repeat_index':0,'case_seed':0,'status':'finished','run_id':'r2','outcome_category':'model'},
            {'trial_key':'c','ordinal':2,'family':'two','case':'c','scenario':'control',
             'repeat_index':0,'case_seed':0,'status':'pending','run_id':None,'outcome_category':'unknown'}]
        reports = {
            'r1':{'verdict':'passed','worker_seconds':4,'usage':{'total_tokens':10,'observed_total_tokens':10}},
            'r2':{'verdict':'failed','worker_seconds':6,'usage':{'total_tokens':None,'observed_total_tokens':7}}}
        result = aggregate_suite(manifest, trials, reports, 'recorded-controller-verdicts')
        self.assertEqual(result['counts']['planned'], 3)
        self.assertEqual(result['counts']['not_run'], 1)
        self.assertEqual(result['success'], {'passed':1,'denominator':3,'rate':1/3})
        self.assertTrue(result['provisional'])
        self.assertEqual(result['worker_seconds'], 10)
        self.assertIsNone(result['usage']['total_tokens'])
        self.assertEqual(result['usage']['observed_total_tokens'], 17)
        self.assertEqual(result['family_macro_success'], .25)
```

- [ ] **Step 2: Run red.** `python -m unittest test_suite_reporting.AggregateTests -v` must fail on the missing aggregation module/function.

- [ ] **Step 3: Implement descriptive aggregation with explicit fields.** Count success only from a qualified completed child report with all required accepted/evaluator gates. Treat a settled model failure as a finished failed measurement. Pending slots remain not-run, even when the suite is cancelled; separately report cancellation of already started children. Planned is every expanded slot, never only reports found on disk. Family success divides by that family's planned slots, then equally averages family rates.

```python
def aggregate_suite(manifest, trials, reports, qualification):
    if qualification not in {'recorded-controller-verdicts','regraded-encrypted-evidence'}:
        raise ValueError('Suite aggregates require qualified trial evidence')
    planned = len(manifest['entries']) * manifest['repetitions']
    if (planned < 1 or len(trials) != planned
            or sorted(t['ordinal'] for t in trials) != list(range(planned))
            or len({t['trial_key'] for t in trials}) != planned):
        raise ValueError('Aggregate requires every planned slot exactly once')
    passed = [t for t in trials if t['status'] == 'finished'
              and reports.get(t.get('run_id'), {}).get('verdict') == 'passed'
              and t['outcome_category'] == 'completed']
    started = [t for t in trials if t.get('run_id')]
    finished = [t for t in trials if t['status'] == 'finished']
    observed = [reports[t['run_id']] for t in started if t['run_id'] in reports]
    families = sorted({t['family'] for t in trials})
    rates = {family: sum(t['family'] == family for t in passed) /
             sum(t['family'] == family for t in trials) for family in families}
    token_values = [(r.get('usage') or {}).get('total_tokens') for r in observed]
    known = [(r.get('usage') or {}).get('observed_total_tokens') for r in observed]
    all_known = len(observed) == len(started) and bool(started) and all(type(v) is int for v in token_values)
    seconds = [r.get('worker_seconds') for r in observed]
    seconds_complete = (len(observed) == len(started) and bool(started)
                        and all(type(v) in (int, float) for v in seconds))
    return {'schema':'benchmark-suite-report/1','qualification':qualification,
        'counts':{'planned':len(trials),'started':len(started),'finished':len(finished),
                  'failed':sum(t['outcome_category']=='model' for t in finished),
                  'blocked':sum(t['status']=='blocked' for t in trials),
                  'cancelled':sum(t['status']=='cancelled' for t in trials),
                  'not_run':len(trials)-len(started)},
        'success':{'passed':len(passed),'denominator':len(trials),'rate':len(passed)/len(trials)},
        'provisional':len(finished)!=len(trials),
        'family_success':rates,'family_macro_success':sum(rates.values())/len(rates),
        'worker_seconds':sum(seconds) if seconds_complete else None,
        'observed_worker_seconds':sum(v for v in seconds if type(v) in (int,float))
                                  if any(type(v) in (int,float) for v in seconds) else None,
        'usage':{'total_tokens':sum(token_values) if all_known else None,
                 'observed_total_tokens':sum(v for v in known if type(v) is int) if any(type(v) is int for v in known) else None,
                 'reported_trials':sum(type(v) is int for v in token_values), 'started_trials':len(started)}}
```

Before this aggregation core, validate each slot against the exact frozen `expand_trials` entry, including repeat, scenario, seed and case, and validate durations as finite/nonnegative and token counts as nonnegative integers excluding bool. Add tests rejecting a deleted slot, duplicate ordinal, swapped case, negative/non-finite duration and invented family. Family comes from the frozen registry pin. Add a missing-report test: known consumed time is retained as `observed_worker_seconds`, while the total remains `None`. Expand this return with public schema projections, not recursive copying of input dictionaries. Allowed experiment fields: manifest/hash; ordered public pins; executor identity/settings; execution snapshot hashes; execution/evidence-track/time policy; original/effective budgets and interventions. Allowed per-trial fields: key, ordinal, entry/repeat index, case/family/scenario/seed, run ID, lifecycle/category, verdict, stage summary, first-attempt/repaired counts, maintenance flags, final passed/total counts, observed duration/usage and report hash. Exclude commands, evaluator options, absolute paths, monitor endpoints and private check vectors. Use `benchmark-report/2` allowlists from plan 01; do not treat key-name redaction as sufficient.

Add descriptive per-case/scenario/stage counts and repeated latency min/median/max using `statistics.median`; publish the sample count and whether the set includes failed trials. First-attempt success requires every required stage accepted on its first attempt and final/maintenance pass; a repaired success remains a separate count. False-alarm denominator is evaluable control trials, alongside the all-planned overall denominator. Use recorded repair duration only; absent duration stays `None`. Keep suite wall time, child wall durations, worker time and tool time separate: they overlap. No billing estimate or independence-based confidence interval is generated.

- [ ] **Step 4: Add redaction and pure comparison tests one at a time.** Include private fields with unfamiliar key names and absolute paths in input records and assert none appear in exported JSON. Add these comparison assertions using two saved suite report dictionaries whose `experiment` field contains the complete ordered identity projection:

```python
def test_changed_seed_or_budget_refuses_a_paired_claim(self):
    from copy import deepcopy
    from generative_driver.benchmark_support.suite_reporting import compare_suites
    before = {'schema':'benchmark-suite-report/1',
              'experiment':{'manifest_sha256':'a'*64,'entries':[{
                  'case':'toy','case_version':'1','evaluator_version':'1',
                  'evaluator_sha256':'b'*64,'image_sha256':'c'*64,'truth_sha256':'d'*64,
                  'scenario':'control','case_seed':0}], 'repetitions':3,
                  'execution':'actual-agent-emulation','evidence_track':'firmware','scope':'full-workflow',
                  'time_policy':'declared-simulation-time',
                  'budget_policy':{'child':10,'suite':60}},
              'dimensions':{'agent':{'model':'one'},'toolchain':'same'},
              'success':{'passed':1,'denominator':3,'rate':1/3},'usage':{'total_tokens':None}}
    after = deepcopy(before)
    after['dimensions']['agent']['model'] = 'two'
    self.assertTrue(compare_suites(before, after)['compatible'])
    after['experiment']['entries'][0]['case_seed'] = 1
    self.assertFalse(compare_suites(before, after)['compatible'])
    after = deepcopy(before)
    after['experiment']['budget_policy']['suite'] = 61
    self.assertFalse(compare_suites(before, after)['compatible'])
    self.assertIsNone(compare_suites(before, before)['tokens_delta'])
```

Implement `_read_saved(value)` to load a supplied path or defensively copy a supplied dictionary. `compare_suites` validates schema, exact `experiment` identity, and trial-key coverage before computing paired differences. The experiment projection includes suite content, ordered case/scenario/image/truth/evaluator pins and implementation hashes, seed, repeats, execution/evidence track, time policy and effective budget/intervention policy. Model/runtime/skills/toolchain are `dimensions`; list changed dimensions and label multiple changes `combined-system`. Always return both success counts/denominators beside latency; unknown token pairs produce `None`. A compatibility refusal must not present its deltas as a paired improvement.

- [ ] **Step 5: Implement report export and prove offline comparison.** `report_suite` obtains paginated suite/child records from the same configured owner using `autostart=False`, invokes `report_run(..., autostart=False)` for saved child evidence and retains both pinned execution and exporter identity. It writes `suite.json`, `report.md` and relative `trials/<trial-key>/report.json` files in an explicitly selected output directory. Verify stored controller verdicts and required gates, or regrade all started trial sidecars using case-keyed evaluator password handles. If a sidecar is absent or mismatches, refuse the regraded qualification instead of trusting the report's verdict. Incomplete unstarted slots remain visible; they need no invented sidecar. Hash each exported trial report. Extend the existing v2 evidence tests with two toy sidecars to exercise aggregate regrading; do not read actual encrypted answer sets for unit tests.

Add a subprocess test invoking `compare_suites` on saved files with `GENERATIVE_DRIVER_HOME` pointing to an empty temporary directory; assert no `service.json`, `runs.sqlite3` or worker directory is created. This proves comparison is offline. Do not claim service-backed `report_suite` is offline. Run `python -m unittest test_suite_reporting test_reporting test_benchmark_score -v`.

```python
def test_comparison_does_not_start_an_owner(self):
    import json, os, subprocess, sys, tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        saved = root/'suite.json'
        # Reuse the complete literal before dictionary from the preceding test
        # via a module-level comparison_fixture() returning a defensive copy.
        saved.write_text(json.dumps(comparison_fixture()), encoding='utf-8')
        home = root/'unused owner'
        program = ('import json,sys; from generative_driver.benchmark_support.suite_reporting '
                   'import compare_suites; print(json.dumps(compare_suites(sys.argv[1],sys.argv[1])))')
        completed = subprocess.run([sys.executable,'-c',program,str(saved)],
            env={**os.environ,'GENERATIVE_DRIVER_HOME':str(home)}, capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(completed.stdout)['compatible'])
        self.assertFalse((home/'service.json').exists())
        self.assertFalse((home/'runs.sqlite3').exists())
```

`comparison_fixture()` is the test module's public toy-data helper: move the complete `before` literal from Step 4 into that function and return it directly; each call constructs a fresh dictionary. It performs no file reads or imports of evaluator truth. Do not invent a second incompatible fixture shape.

- [ ] **Step 6: Commit the report feature after focused green.** `git add src/generative_driver/benchmark_support/suite_reporting.py src/generative_driver/reporting.py tests/test_suite_reporting.py` then `git commit -m "feat: export and compare qualified benchmark suite reports"`.

## Task 5: Expose the same durable owner through CLI and MCP

**Files:** Modify `benchmark.py:main`, `mcp.py`; create `tests/test_suite_interfaces.py`; modify `bench/README.md`, `docs/setup.md`, `docs/implementation/benchmark-tdd.md`. Keep generic `driver_start` and legacy benchmark commands intact.

**Interfaces:** CLI is `benchmark suite start|status|events|result|cancel|resume|report|compare`. Start accepts `--manifest FILE`, `--executor`, `--options FILE`, `--request-id`; lifecycle commands accept `suite_id`; events accepts `--after`; result accepts `--offset/--limit`; resume accepts `--suite-budget-seconds/--budget-reason`; report accepts `--output`; compare accepts two saved paths. Existing leading `--home` selects the owner. MCP tools are `driver_benchmark_suite_start(manifest, executor='codex', options=None, request_id=None)`, `driver_benchmark_suite_status(suite_id)`, `driver_benchmark_suite_events(suite_id, after=0)`, `driver_benchmark_suite_result(suite_id, offset=0, limit=50)`, `driver_benchmark_suite_cancel(suite_id)`, and `driver_benchmark_suite_resume(suite_id, suite_budget_seconds=None, budget_reason=None)`.

- [ ] **Step 1: Add the CLI red test.** Use the existing `tests/test_cli.py` subprocess pattern, the registered legacy manifest fixture and a deliberately missing configured runtime. Start through `python -m generative_driver --home <temporary> benchmark suite start --manifest <file> --executor codex --request-id cli-once`, parse `suite_id`, then call status/result through a second subprocess. Assert the returned child becomes host-blocked, duplicate start returns the same suite, pagination excludes private options, and cancelled/resumed state is visible through the normal service. Always shut down the fixture owner in `finally`. Add an offline CLI compare test with a nonexistent home and assert it remains without service/database files.

```python
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from suite_fixtures import legacy_manifest, wait_suite

class SuiteCliTests(unittest.TestCase):
    def test_separate_cli_processes_reconnect_to_the_same_suite(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='suite CLI ') as temporary:
            root = Path(temporary)
            configure('codex', ['missing-scripted-runtime'], home=root)
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps(legacy_manifest()), encoding='utf-8')
            def invoke(*args):
                done = subprocess.run([sys.executable,'-m','generative_driver','--home',str(root),
                    'benchmark','suite',*args], capture_output=True, text=True, check=True)
                return json.loads(done.stdout)
            try:
                first = invoke('start','--manifest',str(manifest),'--request-id','cli-once')
                self.assertTrue(first['ok'])
                wait_suite(call, first['suite_id'], root, lambda s:s['status']=='blocked')
                again = invoke('start','--manifest',str(manifest),'--request-id','cli-once')
                self.assertEqual(first['suite_id'], again['suite_id'])
                self.assertEqual(invoke('status',first['suite_id'])['status'], 'blocked')
                page = invoke('result',first['suite_id'],'--offset','0','--limit','1')
                self.assertEqual(page['trials'][0]['outcome_category'], 'host')
                self.assertNotIn('evaluator_password', json.dumps(page))
                self.assertEqual(invoke('cancel',first['suite_id'])['status'], 'cancelled')
            finally:
                call('shutdown', {}, root)
```

- [ ] **Step 2: Run red and implement CLI dispatch.** Run `python -m unittest test_suite_interfaces.SuiteCliTests -v`; expect the missing `suite` subparser. In `benchmark.main`, add the subparser and these dispatch rules:

```python
if args.command == 'suite':
    from .client import call
    from .benchmark_support.suite_reporting import report_suite, compare_suites
    if args.action == 'compare':
        result = compare_suites(args.before, args.after)
    elif args.action == 'report':
        result = report_suite(args.suite_id, output=args.output)
    else:
        if args.action == 'start':
            params = {'manifest':json.loads(Path(args.manifest).read_text(encoding='utf-8')),
                      'executor':args.executor,
                      'options':json.loads(Path(args.options).read_text(encoding='utf-8')) if args.options else {},
                      'request_id':args.request_id}
        else:
            params = {'suite_id':args.suite_id}
            for key in ('after','offset','limit','suite_budget_seconds','budget_reason'):
                if hasattr(args, key) and getattr(args, key) is not None:
                    params[key] = getattr(args, key)
        result = call('suite_'+args.action, params)
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result.get('ok', result.get('compatible', True)) else 1
```

Validate the full manifest/case registry before calling a client that may autostart. CLI uses the normal native owner policy; its `report` branch uses the existing-owner export path from Task 4. Do not place suite start inside a long-running CLI loop.

- [ ] **Step 3: Add the real MCP Windows boundary regression.** Use existing `tests/test_mcp.py` imports (`ClientSession`, `StdioServerParameters`, `stdio_client`) and explicitly simulate only the OS policy with an external Python MCP process, as existing tests already do:

```python
class SuiteMcpTests(unittest.TestCase):
    def test_windows_suite_does_not_create_an_owner(self):
        asyncio.run(self.absent_owner())

    async def absent_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            params = StdioServerParameters(command=sys.executable,
                args=['-c', "import platform; platform.system=lambda:'Windows'; from generative_driver.mcp import main; main()"],
                env={'GENERATIVE_DRIVER_HOME':temporary}, cwd=temporary)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    response = await session.call_tool('driver_benchmark_suite_start', {
                        'manifest':legacy_manifest(), 'executor':'codex', 'options':{}})
                    result = json.loads(response.content[0].text)
                    self.assertFalse(result['ok'])
                    self.assertIn('service start', result['reason'])
            self.assertFalse((Path(temporary)/'service.json').exists())
            self.assertFalse((Path(temporary)/'runs.sqlite3').exists())
```

Import `asyncio`, `json`, `sys`, `tempfile`, `unittest`, `Path` and `legacy_manifest` in this module. Run `python -m unittest test_suite_interfaces.SuiteMcpTests.test_windows_suite_does_not_create_an_owner -v`; expect unknown tool before implementation.

- [ ] **Step 4: Add thin MCP tools and keep same-home checks.** Start validates manifest/registered execution first, resolves any `options.home`, rejects a different resolved path, strips matching home, and then calls the same private IPC facade. On Windows pass `autostart=False` on the actual start, not only a preliminary ping. The other five tools use existing `_call`, which already applies Windows policy. Their bodies only construct the parameter dictionary and forward it; never create a Controller in an MCP process.

```python
@server.tool()
def driver_benchmark_suite_start(manifest: dict, executor: str = 'codex',
                                 options: dict | None = None, request_id: str | None = None) -> str:
    from .client import call, default_home
    from .benchmark_support.suites import normalize_manifest
    from .benchmark_support.registry import resolve_case
    normalized = normalize_manifest(manifest)
    for entry in normalized['entries']:
        case = resolve_case(entry['case'])
        if case.execution != 'actual-agent-emulation':
            raise ValueError('Suite requires registered emulated cases')
    selected = dict(options or {})
    home = selected.pop('home', None)
    if home and Path(home).expanduser().resolve() != default_home().resolve():
        return json.dumps({'ok':False,'reason':'Suite options.home must match the configured MCP home'})
    return json.dumps(call('suite_start', {'manifest':normalized,'executor':executor,
        'options':selected,'request_id':request_id}, autostart=platform.system() != 'Windows'))
```

- [ ] **Step 5: Add reconnect, home and pagination tests one at a time.** Start the fixture owner from the parent test process, save its ping PID, then connect real stdio, start a suite and close that connection. Reconnect with a second MCP client and assert same suite ID/owner PID, retained trial events and no `suite.recovered` event. Reuse the scripted worker from Task 3 with an explicit fixture gate if needed to observe a running child; release the gate from the test through a temporary file, not a fixed sleep. Submit a different home and assert rejection with no storage there; equivalent relative/absolute home is allowed. Request `limit=101` and a bool cursor and expect explicit validation errors. Loop result pages to recover every planned slot exactly once, without raw child transcripts/password handles. Run `python -m unittest test_suite_interfaces test_mcp -v`.

```python
async def reconnect_and_reject_other_home(self):
    from generative_driver.client import call
    from generative_driver.setup import configure
    with tempfile.TemporaryDirectory() as temporary:
        root, other = Path(temporary), Path(temporary)/'different owner'
        configure('codex', [sys.executable,'-c','import time; time.sleep(120)'], home=root)
        owner = call('ping', {}, root)
        params = StdioServerParameters(command=sys.executable, args=['-m','generative_driver.mcp'],
            env={'GENERATIVE_DRIVER_HOME':str(root)}, cwd=root)
        try:
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    refused = await session.call_tool('driver_benchmark_suite_start', {
                        'manifest':legacy_manifest(2),'options':{'home':str(other)}})
                    self.assertFalse(json.loads(refused.content[0].text)['ok'])
                    reply = await session.call_tool('driver_benchmark_suite_start', {'manifest':legacy_manifest(2)})
                    suite = json.loads(reply.content[0].text)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    reply = await session.call_tool('driver_benchmark_suite_status', {'suite_id':suite['suite_id']})
                    self.assertEqual(json.loads(reply.content[0].text)['suite_id'], suite['suite_id'])
                    events = await session.call_tool('driver_benchmark_suite_events', {'suite_id':suite['suite_id']})
                    self.assertNotIn('suite.recovered', [e['kind'] for e in json.loads(events.content[0].text)['events']])
                    rows = []
                    for offset in (0,1):
                        page = await session.call_tool('driver_benchmark_suite_result',
                            {'suite_id':suite['suite_id'],'offset':offset,'limit':1})
                        rows.extend(json.loads(page.content[0].text)['trials'])
                    self.assertEqual([r['ordinal'] for r in rows], [0,1])
            self.assertEqual(call('ping', {}, root, autostart=False)['pid'], owner['pid'])
            self.assertFalse((other/'service.json').exists())
        finally:
            call('shutdown', {}, root)

def test_suite_reconnects_without_changing_owner_or_home(self):
    asyncio.run(self.reconnect_and_reject_other_home())
```

- [ ] **Step 6: Document and commit the interface.** Add exact commands to `bench/README.md`: select/configure runtime, start the independent owner on Windows, inspect/copy the public manifest, start with explicit budgets and emulator approval, save suite ID, reconnect/cancel/resume, export, and compare saved JSON. Document the default 18 fresh trials as three families × two paired scenarios × three repetitions, with a one-repeat override and explicit inference authorization. Explain stopped wall time, separate child/suite extension, provisional reports, calibration blockers, no physical aggregate, and the service-backed export versus offline compare distinction. Add the observed TDD outcomes to `docs/implementation/benchmark-tdd.md`; do not claim planned/native/model tests already ran. `git add src/generative_driver/benchmark.py src/generative_driver/mcp.py tests/test_suite_interfaces.py bench/README.md docs/setup.md docs/implementation/benchmark-tdd.md` then `git commit -m "feat: expose durable benchmark suites through CLI and MCP"`.

## Deliverable and verification gates

- [ ] Self-review spec coverage: sections 8–10 map to Tasks 1–5; v2 private evidence and case calibration remain the preceding plans' responsibility, consumed here without duplicating evaluators.
- [ ] Verify each red/green result is recorded from an actual focused run during implementation; the examples in this planning document are not executed evidence.
- [ ] Run the complete legacy/new `unittest` suite once the independently reviewed tasks are green. Use existing installed-wheel CI on native macOS and Windows Server runners with Python 3.11/3.13, outside the checkout and with paths containing spaces. Preserve the distinction between those runners and a manually verified Windows 11 workstation.
- [ ] Run installation smoke, registry discovery and suite structural tests without optional native tools, inference or passwords. Pending native reference calibration must remain pending.
- [ ] Verify pure saved comparison and v2 regrading do not create a service, model process or device connection. Verify allowlisted exports contain no evaluator handles or private source/monitor data.
- [ ] Report remaining native reference-calibration and explicit inference gates separately. Starting the development pilot requires the selected runtime/model, accepted budgets and explicit execution authorization after review; neither this plan nor a passing scripted fixture provides it.

## Handoff

This plan delivers a durable suite API, preserved child evidence, machine-classified outcomes, descriptive aggregates and offline comparisons. Execute after plans 01/02 supply their named interfaces, keep each task independently reviewable, and retain the frozen September baseline unchanged. Planning completion means this file is saved and self-reviewed; it does not mean any implementation, commit, calibration or model run described above has occurred.
