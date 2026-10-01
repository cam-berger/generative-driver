# Registered Cases and TQ9 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a versioned, independently graded TQ9 v2 with varied behavioral tests and semantic/control maintenance while preserving the existing benchmark.

**Architecture:** Keep the public benchmark functions and the configurator owner. Add a static case registry, immutable execution snapshots, bounded task bindings, a new native emulator adapter and a shared v2 stage engine. Keep final evaluation records sealed and regrade them from saved evidence.

**Tech Stack:** Python 3.11+, unittest, existing SQLite/controller/runtime, existing cryptography envelope, optional native Renode/Ghidra/Java and Arm GNU Toolchain.

**Spec:** [Benchmark expansion specification](../specs/2026-09-30-benchmark-expansion.md), sections 1–6 and 9–10. Read both documents before implementation. Continue with [new families](2026-09-30-benchmark-02-families.md), then [suites](2026-09-30-benchmark-03-suites.md).

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

- A new registered emulator profile must inherit Windows owner/home checks and must never authorize a caller-supplied physical binding; Task 1 tests both entry paths.
- Restart or installation changes must not replace an executed image, seed, scenario or evaluator; Tasks 2 and 5 test tampering and interrupted scenario application.
- A constant answer, duplicate observation, bool-as-number or unit mismatch must not pass multipoint evaluation; Task 3 tests these records through the public grader.
- A changed ambient reading must not be credited as detected firmware drift, and a monitor failure must not become a model defect; Tasks 4 and 5 test both conditions.
- Hidden final failures must end the trial without leaking expected values into grounding, repair prompts or public reports; Tasks 6 and 7 exercise those projections and transitions.

---

## Scope and working conventions

This plan implements the first milestone only. Do not add suite scheduling or two more firmware applications here. Work in the user's requested `feat/bench` branch. At the time of planning the branch starts at `e7cbba3`; inspect current changes before execution and preserve any subsequent user work.

Run commands from the repository root using the activated installation environment's `python`. On macOS the existing environment is `.venv`; on Windows activate `.venv\Scripts\Activate.ps1`. Do not invoke a bare `codex` executable: the user's machine has an unrelated Python program with that name. A configured runtime uses its verified absolute command.

Use one failing behavior and its implementation per vertical slice. The tests below are concrete starting tests; add the explicitly listed neighboring cases in the same owning task. They are contract tests, not model benchmark results. Wait on durable events with a bounded per-transition deadline.

## File map

| File | Responsibility |
|---|---|
| `src/generative_driver/benchmark_support/registry.py` | Static built-in descriptors, manifest/path/hash validation and adapter lookup |
| `src/generative_driver/benchmark_support/legacy.py` | Delegates existing smoke/TQ9/BME behavior without changing legacy case assets |
| `src/generative_driver/benchmark_support/snapshots.py` | Executed public input snapshots and provenance verification |
| `src/generative_driver/benchmark_support/behavior.py` | Canonical task bindings, exact check inventories, record grading and safe feedback |
| `src/generative_driver/benchmark_support/native.py` | New Renode lifecycle with evaluator-only reset/stimulus/observation recipes |
| `src/generative_driver/benchmark_support/scenarios.py` | Pure maintenance decisions and persisted scenario action journal |
| `src/generative_driver/benchmark_support/emulated.py` | Shared v2 seven-stage preparation/check/package/cleanup engine |
| `src/generative_driver/benchmark_support/tq9_v2.py` | TQ9 family plans, canonical observations and contracts |
| `src/generative_driver/benchmark_support/evidence.py` | Sealed final records, report allowlist and pure v2 regrading |
| `src/generative_driver/benchmark_support/authoring.py` | Evaluator-only recipe/build/calibration utilities |
| `src/generative_driver/resources/bench/cases/tq9-v2/` | New manifest and pinned binaries; no plaintext answer source |
| `src/generative_driver/resources/bench/groundtruth/tq9-v2.enc` | New encrypted evaluator bundle |
| `tests/test_case_registry.py`, `tests/test_benchmark_snapshots.py`, `tests/test_benchmark_behavior.py` | Case identity and pure contract tests |
| `tests/test_benchmark_native.py`, `tests/test_benchmark_scenarios.py`, `tests/test_benchmark_v2_evidence.py`, `tests/test_benchmark_tq9_v2.py` | Process, lifecycle, hidden evidence and integrated stage tests |

The existing `benchmark.py`, `configurator.py`, `mcp.py`, `reporting.py`, `ground.py`, and `evaluator_cli.py` remain integration points. Preserve `cases.py`, `evaluate.py`, and `emulator.py` as legacy implementations until a behavior-preserving extraction has its own passing test.

### Task 1: Register existing cases behind every public entry point

**Files:**
- Create: `src/generative_driver/benchmark_support/registry.py`, `src/generative_driver/benchmark_support/legacy.py`, `tests/test_case_registry.py`.
- Modify: `src/generative_driver/benchmark.py` (`main`, `run`, stage/package/cleanup/score dispatch), `src/generative_driver/configurator.py` (`validate_scoped_approval`), `src/generative_driver/mcp.py` (`driver_benchmark_run`), `src/generative_driver/benchmark_support/evaluator_cli.py` (`manage`).
- Test: existing `tests/test_benchmark_run.py`, `tests/test_benchmark_stages.py`, `tests/test_mcp.py`.

**Interfaces:**
- Consumes: existing public benchmark signatures and `case_root()`; do not add import-time dependency on Renode.
- Produces: the spec's `CaseDefinition`, `case_ids()`, `resolve_case(case_id)`, `adapter_for(case_id)`, `pin_case(case_id, scenario_id, case_seed)`; also `resource_path(root: Path, relative: str) -> Path` and `load_definition(path: Path, *, resource_root: Path) -> CaseDefinition` for validating packaged manifests without registering arbitrary modules.
- Adapter functions have the same signatures as current `prepare_stage`, `check_stage`, `package_execute`, `cleanup`, `score`. `legacy.py` owns extracted v1 package execution/score wrappers so dispatch never recurses into itself.

- [ ] **Step 1: Add the first registry behavior test.**

```python
import unittest
from generative_driver.benchmark_support.registry import resolve_case, case_ids
from generative_driver.configurator import validate_scoped_approval

class RegistryTests(unittest.TestCase):
    def test_legacy_identity_and_emulator_grant_boundary(self):
        self.assertEqual(resolve_case('tq9').version, '1')
        self.assertEqual(resolve_case('tq9').family, 'tq9')
        self.assertTrue({'setup-smoke', 'tq9', 'bme280'} <= set(case_ids()))
        with self.assertRaises(ValueError):
            resolve_case('../tq9')
        with self.assertRaises(ValueError):
            validate_scoped_approval({'case': 'tq9',
                'scoped_tool_approval': 'emulator',
                'binding': {'host': '127.0.0.1', 'port': 1234}})
```

- [ ] **Step 2: Run the red test.**

Run: `python -m unittest discover -s tests -p test_case_registry.py -v`
Expected: import failure because the registry is absent. A syntax or environment error is not the intended red.

- [ ] **Step 3: Implement static lookup and legacy normalization.**

Use the spec's dataclass. `_BUILTINS` is a literal dictionary keyed by allowed IDs. Resolve adapter keys with a literal dictionary of lazy loaders, not `importlib` on manifest strings. Normalize smoke and legacy descriptors into defensive dictionaries. Validate IDs before joining paths. Hash original manifest bytes, not a reserialized equivalent. This is the dispatch pattern:

```python
def prepare_stage(case_id, stage, run_dir, workspace, accepted=None, options=None):
    from .benchmark_support.registry import adapter_for
    return adapter_for(case_id).prepare_stage(
        case_id, stage, run_dir, workspace, accepted, options)
```

Extract the current implementation before replacing the facade. Convert all remaining hardcoded branches, including CLI choices, truth management, both MCP real-agent checks, and emulator approval. Add `benchmark cases` (JSON descriptors without secrets), `run --scenario` and `run --case-seed`; validate scenario membership and integer seeds before dispatch. Normalize TQ9 v1 as scenario `identity`, seed `0`, execution `actual-agent-emulation`, evidence track `firmware`, scope `full-workflow`, and calibration `{status: legacy-not-required}`. This preserves its old policy and supports legacy scheduling tests without declaring native v2 calibration. Legacy calls retain their defaults and reject unsupported new options. Registry approval requires an emulated execution mode AND `approval_scope == 'emulator'`; preserve rejection of all operator-supplied bindings. Registry discovery needs no password. V2 `run` requires validated `calibration.status == 'passed'` before starting the owner/worker. An unbuilt pending definition may be listed but cannot produce a case pin.

- [ ] **Step 4: Add one red/green slice for each validation and host edge.**

Use `load_definition` with temporary files for duplicate/mismatched IDs, malformed SHA-256, traversal, absolute paths and symlink escape; ensure no device launch occurs. Add MCP tests using the existing stdio harness in `test_mcp.py`: an unknown profile fails before owner creation; a registered emulated fixture rejects a different `options.home`; Windows requires the independently started owner. Use a synthetic registered definition in the test process only, never a public arbitrary adapter-import option.

Add this path/seed test to `RegistryTests`; `resource_path` resolves a manifest-relative resource and rejects absolute/traversing/escaping references before opening it:

```python
    def test_resource_paths_and_seed_types_are_bounded(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import resource_path, pin_case
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'image.bin').write_bytes(b'toy')
            self.assertEqual(resource_path(root, 'image.bin'), root / 'image.bin')
            for path in ('../outside.bin', str(root.parent / 'outside.bin')):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    resource_path(root, path)
        for seed in (True, 1.5, float('nan')):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                pin_case('tq9', None, seed)
```

- [ ] **Step 5: Run focused and legacy regressions.**

Run `python -m unittest discover -s tests -p 'test_benchmark*.py' -v`, then `python -m unittest discover -s tests -p test_mcp.py -v`.
Expected: existing results and error behavior remain compatible. Confirm old assets have no diff with `git diff -- src/generative_driver/resources/bench/cases/tq9 src/generative_driver/resources/bench/groundtruth/tq9.enc bench/baselines`.

- [ ] **Step 6: Commit the slice.**

```sh
git add src/generative_driver/benchmark.py src/generative_driver/benchmark_support/registry.py src/generative_driver/benchmark_support/legacy.py src/generative_driver/benchmark_support/evaluator_cli.py src/generative_driver/configurator.py src/generative_driver/mcp.py tests/test_case_registry.py tests/test_mcp.py
git commit -m "feat(bench): register cases across public entry points"
```

### Task 2: Pin executed case inputs before assigning a worker

**Files:**
- Create: `src/generative_driver/benchmark_support/snapshots.py`, `tests/test_benchmark_snapshots.py`.
- Modify: `configurator.py` (`start`, `_prepare`, resume/result handling), `reporting.py` (`report_run`), `registry.py` (`pin_case`).

**Interfaces:**
- Consumes: `pin_case` and `CaseDefinition`; controller request-ID deduplication.
- Produces: `snapshot_case(run_dir: Path, pin: dict, provenance: dict) -> dict`, `read_snapshot(run_dir: Path) -> dict`, `require_snapshot(run_dir: Path, pin: dict) -> dict`; schema `benchmark-execution-snapshot/1`.
- Snapshot fields: `case_pin`, `public_files` (relative path to hash), `executed` (toolchain/skills/evaluator implementation hashes, runtime configuration and environment), `snapshot_sha256`. Store at `benchmark/execution.json`; snapshot files at `benchmark/inputs/`.

- [ ] **Step 1: Write the tamper/resume test.**

```python
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark_support.registry import pin_case
from generative_driver.benchmark_support.snapshots import snapshot_case, require_snapshot

class SnapshotTests(unittest.TestCase):
    def test_changed_input_cannot_resume_as_original(self):
        with tempfile.TemporaryDirectory(prefix='bench snapshot ') as temp:
            root = Path(temp)
            pin = pin_case('tq9', None, 0)
            saved = snapshot_case(root, pin, {'toolchain_revision': 'test-fixture'})
            self.assertEqual(saved['case_pin']['case_seed'], 0)
            image = root / 'benchmark' / 'inputs' / 'firmware.bin'
            image.write_bytes(image.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'hash'):
                require_snapshot(root, pin)
```

- [ ] **Step 2: Run red.**

Run: `python -m unittest discover -s tests -p test_benchmark_snapshots.py -v`
Expected: snapshot module import fails.

- [ ] **Step 3: Implement immutable snapshot creation and verification.**

Copy each validated public input from the case into a temporary sibling directory, verify bytes, then atomically publish the directory and execution record. Create once; an existing snapshot must validate exactly. Store password-file handles only in evaluator-owned configuration, never `execution.json`. Compute a canonical digest with standard JSON:

```python
def canonical_digest(value):
    import hashlib
    import json
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()
```

Define `canonical_digest` in `snapshots.py`; later modules import it. Persist the pin and effective executor configuration before `Controller._spawn`. Verify the snapshot before every prepared assignment and on explicit resume. For old runs without snapshots preserve legacy behavior and the existing provenance limitation; never fabricate historical executed metadata.

- [ ] **Step 4: Exercise idempotence and exporter drift.**

Add tests that repeat `snapshot_case` with the same inputs and obtain the same identity; alter scenario/seed/evaluator hash and require rejection; mutate the installed manifest after capture and verify new report metadata still comes from the snapshot; ensure two requests with identical `request_id` select the same run even after user runtime configuration edits. Update controller deduplication to compare the originally pinned intent before resolving mutable defaults. Existing checks for a changed explicit request remain errors.

- [ ] **Step 5: Run green and commit.**

Run `python -m unittest discover -s tests -p test_benchmark_snapshots.py -v` and `python -m unittest discover -s tests -p test_reporting.py -v`.

```sh
git add src/generative_driver/benchmark_support/snapshots.py src/generative_driver/benchmark_support/registry.py src/generative_driver/configurator.py src/generative_driver/reporting.py tests/test_benchmark_snapshots.py tests/test_reporting.py
git commit -m "feat(bench): pin executed case assets and provenance"
```

### Task 3: Bind varying canonical inputs and reject incomplete evidence

**Files:** Create `src/generative_driver/benchmark_support/behavior.py`, `tests/test_benchmark_behavior.py`.

**Interfaces:**
- Consumes: validated candidate model parameter/output definitions and an evaluator-owned exact check inventory.
- Produces: `bind_task(capabilities: dict, task: str, inputs: dict, model: dict) -> dict`, `validate_records(contract: dict, records: list[dict]) -> dict`, `project_feedback(records: list[dict]) -> dict`.
- Contract schema `benchmark-behavior/1`: `artifact_sha256` and nonempty `checks`. Each check has `id`, `revision`, `kind` (`number`, `boolean`, `text`), `expected`, `unit`, `channel`; numeric checks also require nonnegative finite `absolute_tolerance`.
- Each measured record has matching `id`, `revision`, `artifact_sha256`, `unit`, `channel`, plus `value` and optional private evidence references. Channels are `independent-monitor` or `runtime-transcript`. Return `{verdict, passed, total, checks}`; structurally invalid inventories raise `ValueError`, invalid/missing measured evidence yields `failed` with reasons.

- [ ] **Step 1: Write varying-input tests.**

```python
import unittest
from generative_driver.benchmark_support.behavior import bind_task, validate_records

class BehaviorTests(unittest.TestCase):
    def test_each_target_reaches_the_candidate_parameter(self):
        model = {'operations': {'set_percent': {'parameters': {
            'percent': {'type': 'integer', 'minimum': 0, 'maximum': 100}}}}}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {
            'set_output': {'operation': 'set_percent', 'constants': {},
                'inputs': {'fraction': {'parameter': 'percent', 'scale': 100, 'offset': 0}},
                'outputs': {}}}}
        first = bind_task(mapping, 'set_output', {'fraction': .25}, model)
        second = bind_task(mapping, 'set_output', {'fraction': .75}, model)
        self.assertEqual(first, {'operation': 'set_percent', 'parameters': {'percent': 25}})
        self.assertEqual(second['parameters'], {'percent': 75})
        with self.assertRaises(ValueError):
            bind_task(mapping, 'set_output', {'fraction': True}, model)

    def test_boolean_cannot_pass_as_temperature(self):
        check = {'id': 'toy/read/temperature', 'revision': 0, 'kind': 'number',
                 'expected': 1.0, 'absolute_tolerance': .01, 'unit': 'degC',
                 'channel': 'independent-monitor'}
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': [check]}
        record = {'id': check['id'], 'revision': 0, 'value': True, 'unit': 'degC',
                  'channel': 'independent-monitor', 'artifact_sha256': 'a' * 64}
        self.assertEqual(validate_records(contract, [record])['verdict'], 'failed')
```

- [ ] **Step 2: Run red.**

Run: `python -m unittest discover -s tests -p test_benchmark_behavior.py -v`
Expected: missing behavior module.

- [ ] **Step 3: Implement bounded conversion with no dynamic code.**

Numeric bindings use `value * scale + offset`. Require finite inputs/coefficient/results, exact integer conversion where needed, and the candidate parameter's bounds. A binding with `kind: "copy"` copies an enum/string or bounded numeric scalar after the destination type/range/enum check. No missing/extra canonical inputs, unused destination parameters, duplicate destinations or collisions with constants are allowed. Validate output name/unit mappings against the model; do not transform decoded outputs to conceal a wrong model.

```python
def numeric_value(value):
    import math
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Expected a finite numeric value')
    return value

def affine_value(value, scale, offset, parameter):
    result = numeric_value(value) * numeric_value(scale) + numeric_value(offset)
    numeric_value(result)
    if parameter['type'] == 'integer':
        if result != int(result):
            raise ValueError('Integer parameter requires exact conversion')
        result = int(result)
    if not parameter['minimum'] <= result <= parameter['maximum']:
        raise ValueError('Canonical input exceeds candidate parameter bounds')
    return result
```

Both functions belong in `behavior.py`. Grade exact inventories before comparing values; do not collapse repeated IDs into a dictionary before checking for duplicates. `project_feedback` emits only diagnostic task ID, observed value/unit and a generic defect reason. It has no access to private `expected`, tolerance or final records.

- [ ] **Step 4: Add paired positive and negative evidence tests.**

For the literal toy contract above, `value=1.005` passes and `value=100.5` fails. Add subtests for constant results at three different expected values, missing/extra/duplicate IDs, NaN/Infinity, wrong unit, wrong revision/artifact/channel, string-as-number, negative tolerance, parameter collisions and integer rounding. For `kind: copy`, exercise two allowed bank enums and rejection of a third. Two differently named operations with equivalent bindings must both pass the same observable targets.

Add the following record test to `BehaviorTests`; all values are synthetic and unrelated to scored case vectors:

```python
    def test_exact_inventory_and_units_cannot_be_bypassed(self):
        checks = [{'id': f'toy/{i}/sample', 'revision': 0, 'kind': 'number',
                   'expected': value, 'absolute_tolerance': .01, 'unit': 'degC',
                   'channel': 'independent-monitor'}
                  for i, value in enumerate((-2.5, 0.0, 6.25))]
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': checks}
        records = [{'id': row['id'], 'revision': 0, 'value': row['expected'],
                    'unit': 'degC', 'channel': 'independent-monitor',
                    'artifact_sha256': 'a' * 64} for row in checks]
        self.assertEqual(validate_records(contract, records)['verdict'], 'passed')
        wrong = [records[:-1], records + [records[0]],
                 [dict(r, value=0) for r in records],
                 [dict(r, unit='raw') for r in records],
                 [dict(r, value=float('nan')) for r in records],
                 [dict(r, revision=1) for r in records],
                 [dict(r, artifact_sha256='b' * 64) for r in records]]
        for rows in wrong:
            with self.subTest(rows=rows):
                self.assertEqual(validate_records(contract, rows)['verdict'], 'failed')
```

- [ ] **Step 5: Run green and commit.**

```sh
python -m unittest discover -s tests -p test_benchmark_behavior.py -v
git add src/generative_driver/benchmark_support/behavior.py tests/test_benchmark_behavior.py
git commit -m "feat(bench): grade bounded multipoint behavioral contracts"
```

### Task 4: Add evaluator-controlled native stimuli and new TQ9 assets

**Files:**
- Create: `src/generative_driver/benchmark_support/native.py`, `src/generative_driver/benchmark_support/authoring.py`, `tests/test_benchmark_native.py`.
- Create packaged assets: `resources/bench/cases/tq9-v2/case.json`, `firmware.bin`, `firmware-semantic.bin`, `firmware-identity.bin`; `resources/bench/groundtruth/tq9-v2.enc` (all under `src/generative_driver/`).
- Modify: `registry.py` to register pending `tq9-v2`; `evaluator_cli.py` for v2 evaluator-only build/calibration.
- Private authoring files: `<evaluator-authoring-root>/tq9-v2/source/`, `build.json`, `recipe.json`, `contracts.json`, reference models and mutation definitions. The root must be outside repository, installed resources and candidate workspaces.

**Interfaces:**
- Produces `NativeSession.start(*, renode: Path, image: Path, recipe: dict, timeout_seconds: float = 60.0)`, `NativeSession.attach(info: dict, recipe: dict)`, `.info`, `.binding`, `.reset(initial: dict[str,int]) -> dict`, `.stimulate(values: dict[str,int]) -> dict`, `.observe() -> dict`, `.set_running(running: bool) -> None`, `.stop() -> None`.
- Observation shape: `{values: {name: int | list[int]}, reads: list[dict], time: float, physical: False}`. `.info` and recipes are evaluator-only. `.binding` exposes only UART TCP host/port.
- Produces `authoring.build_case(authoring_dir: Path, compiler: Path, output_dir: Path) -> dict` and `authoring.calibrate(case_id: str, options: dict) -> dict`; both invoke subprocesses with argument lists. They never invoke an agent. Plan 02's `asset_build` and `calibration` are inventory/admission wrappers around these shared runners. Calibration returns `benchmark-calibration/1` with case/evaluator identity, source/contract `input_hashes`, image hashes, two reference results, required/observed mutant inventory, scenario outcomes, tool versions and actual native-process provenance. It never embeds its eventual ciphertext hash.

- [ ] **Step 1: Pin complete validation before sending stimulus commands.**

```python
import unittest
from unittest.mock import patch
from generative_driver.benchmark_support.native import NativeSession

class NativeTests(unittest.TestCase):
    def test_out_of_range_stimulus_never_reaches_monitor(self):
        recipe = {'stimuli': {'sample': {'minimum': -128, 'maximum': 127,
            'command': 'sysbus WriteDoubleWord 0x50000000 {value}'}},
            'observations': {}, 'reset_commands': []}
        session = NativeSession.attach({'monitor_port': 1,
            'binding': {'host': '127.0.0.1', 'port': 2}}, recipe)
        with patch.object(session, '_monitor') as monitor:
            with self.assertRaises(ValueError):
                session.stimulate({'sample': 128})
            monitor.assert_not_called()
```

The command/address above belong to a synthetic contract fixture, not the scored device. `_monitor(command: str) -> str` is a private NativeSession boundary around the actual monitor connection.

- [ ] **Step 2: Run red, then implement native lifecycle and typed recipes.**

Run: `python -m unittest discover -s tests -p test_benchmark_native.py -v`
Expected: absent module. Reuse the known Renode launch/connection pattern while keeping legacy `RenodeSession` unchanged. Validate recipe source filenames, one-line monitor commands, integer ranges and observation widths/counts before I/O. Restrict template substitution to the single integer token `{value}`; reject arbitrary fields, traversal and booleans.

```python
def render_stimuli(recipe, values):
    if not set(values) <= set(recipe['stimuli']):
        raise ValueError('Unknown stimulus')
    rendered = []
    for name, value in values.items():
        spec = recipe['stimuli'][name]
        if type(value) is not int or not spec['minimum'] <= value <= spec['maximum']:
            raise ValueError('Stimulus outside integer bounds')
        command = spec['command']
        if (command.count('{value}') != 1 or '{' in command.replace('{value}', '')
                or '}' in command.replace('{value}', '')):
            raise ValueError('Invalid stimulus command template')
        rendered.append(command.replace('{value}', str(value)))
    return rendered
```

Define `render_stimuli` in `native.py`. `.stimulate` renders every command first, then sends them. Preserve raw responses; require a parseable value for every requested observation. A lost monitor raises a host fault. Pause between package calls, with virtual time running only within declared operations. Stop only this session's process; clean ephemeral plaintext sources.

- [ ] **Step 3: Author TQ9 v2 in evaluator storage.**

Use `benchmark truth unlock --case tq9` into a new evaluator-only authoring directory to inspect the owned source/build evidence; do not open or modify the frozen baseline run/environment. Keep legacy payload/binaries unchanged. Write a new source revision with a controllable sensor input, the same observable output task, and a stable-ID semantic variant that changes temperature encoding. Add the identity-only variant as a separate image. Use the private source's actual peripheral API to implement the stimulus recipe; verify commands through native reference execution before declaring the adapter calibrated.

Versioned `build.json` records an ordered argv-list per compile/link/objcopy step, exact compiler version, source hashes and expected output filenames. `build_case` only substitutes validated authoring/output/tool paths and uses `subprocess.run(argv, cwd=authoring_dir, check=True, timeout=120)`. Reject paths escaping those directories; never execute arbitrary manifest-provided shell text. Store newly computed output hashes in the new case manifest, not historical ones.

- [ ] **Step 4: Test recipe/build failures with controlled external processes.**

Add tests for unknown stimulus, bool/incomplete observation, monitor timeout, path spaces and a fake compiler process returning a nonzero code. Assert no success marker or public calibration claim is written after a failure. The fake process tests launcher contracts only. New case metadata remains `calibration: {status: pending}` until Task 7's real reference gate.

- [ ] **Step 5: Run green and commit only distributable assets.**

Run `python -m unittest discover -s tests -p test_benchmark_native.py -v`. Review `git diff --stat`; no authoring source/password/reference plaintext may be staged.

```sh
git add src/generative_driver/benchmark_support/native.py src/generative_driver/benchmark_support/authoring.py src/generative_driver/benchmark_support/evaluator_cli.py src/generative_driver/benchmark_support/registry.py src/generative_driver/resources/bench/cases/tq9-v2 src/generative_driver/resources/bench/groundtruth/tq9-v2.enc tests/test_benchmark_native.py
git commit -m "feat(bench): add evaluator-controlled native TQ9 v2 assets"
```

### Task 5: Distinguish semantic drift, unchanged controls and host faults

**Files:** Create `src/generative_driver/benchmark_support/scenarios.py`, `tests/test_benchmark_scenarios.py`; modify `configurator.py` only where existing routing must accept the new trusted decision result.

**Interfaces:**
- Produces `maintenance_decision(*, scenario: str, claim: str, diagnostic: dict, repaired: bool) -> dict`; claims are `unchanged`, `drift`, `unknown` and include independent evidence separately in the diagnostic.
- Diagnostic shape: `{evaluable: bool, fault: str | None, contradiction: bool, evidence_ids: list[str], requalified: bool, fresh_reuse_passed: bool}`.
- Produces `ScenarioJournal(run_dir: Path)`, `.begin(nonce: str, action_sha256: str) -> dict`, `.applied(nonce: str, observation: dict) -> dict`, `.observed(nonce: str, evidence_sha256: str) -> dict`, `.state() -> dict`. Files stay evaluator-owned; conflicting nonce/hash or an unfinished `applying` action blocks repeat application. `observed` requires the matching applied action and commits the independent evidence hash; reusing it with different evidence is an error.

- [ ] **Step 1: Write control and host-fault decisions as a single red test.**

```python
import unittest
from generative_driver.benchmark_support.scenarios import maintenance_decision

class ScenarioTests(unittest.TestCase):
    def test_control_false_alarm_and_host_failure_are_distinct(self):
        good = {'evaluable': True, 'fault': None, 'contradiction': False,
                'evidence_ids': ['control/read'], 'requalified': False,
                'fresh_reuse_passed': True}
        control = maintenance_decision(scenario='control', claim='unchanged',
                                       diagnostic=good, repaired=False)
        self.assertTrue(control['ok'])
        alarm = maintenance_decision(scenario='control', claim='drift',
                                     diagnostic=good, repaired=False)
        self.assertFalse(alarm['ok'])
        self.assertTrue(alarm['maintenance']['false_alarm'])
        unavailable = dict(good, evaluable=False, fault='host', evidence_ids=[])
        blocked = maintenance_decision(scenario='semantic', claim='drift',
                                       diagnostic=unavailable, repaired=False)
        self.assertEqual(blocked['fault'], 'host')
        self.assertIsNone(blocked.get('route'))
```

- [ ] **Step 2: Run red and implement the decision table.**

Run: `python -m unittest discover -s tests -p test_benchmark_scenarios.py -v`
Expected: absent scenarios module. An evaluable control with `unchanged`, no contradiction and fresh reuse passes. A control drift claim fails with `false_alarm=True`. A semantic/identity scenario needs both an evidence-backed worker drift claim and evaluator contradiction before returning `{ok: True, route: 'interpret'}`. Missing claim or evidence fails; a host/operator fault blocks without revision. A repaired scenario passes only after requalification and fresh reuse, and never routes again. Read the worker claim from `maintenance.json` (`benchmark-maintenance-claim/1`, `claim`, `evidence_ids`) inside its workspace; require the report artifact hash and current-assignment event IDs to match. Keep the existing stage report schema: `needs_revision` means a drift claim; `completed` means unchanged. The neutral objective requires this artifact in both cases.

Implement the journal transition core explicitly:

```python
def next_action_state(current, nonce, action_sha256):
    if current.get('status') == 'applying':
        raise ValueError('Scenario effect requires reconciliation')
    if current.get('status') in ('applied', 'observed'):
        if (current['nonce'], current['action_sha256']) != (nonce, action_sha256):
            raise ValueError('Scenario action identity changed')
        return current
    return {'status': 'applying', 'nonce': nonce, 'action_sha256': action_sha256}
```

Define it in `scenarios.py`. Journal writes use a temporary file plus `os.replace` and the existing controller's serialized run ownership; never leave a file open across replacement on Windows. Persist `applying` before I/O and `applied` after verifiable acknowledgement. An already applied action returns state without executing again.

- [ ] **Step 3: Add interruption and semantic-repair tests.**

Create a journal, call `begin`, discard/recreate the object and require `begin` to raise reconciliation instead of applying twice. Complete a separate action with `applied`, reload, and assert its nonce and stimulus hashes remain unchanged. Exercise evidenced semantic drift through the real controller scripted-worker seam; verify one maintenance cycle, a distinct assignment ID, and refusal of the old gateway. A second inferred change in the same child cannot consume another maintenance cycle. Assert identical neutral maintain objectives for control and semantic scenarios.

Add this interrupted-action test to `ScenarioTests`:

```python
    def test_reopened_applying_journal_requires_reconciliation(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.scenarios import ScenarioJournal
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = ScenarioJournal(root)
            self.assertEqual(first.begin('action-1', 'a' * 64)['status'], 'applying')
            reopened = ScenarioJournal(root)
            with self.assertRaisesRegex(ValueError, 'reconciliation'):
                reopened.begin('action-1', 'a' * 64)
```

- [ ] **Step 4: Run green and commit.**

```sh
python -m unittest discover -s tests -p test_benchmark_scenarios.py -v
python -m unittest discover -s tests -p test_service_repair.py -v
git add src/generative_driver/benchmark_support/scenarios.py src/generative_driver/configurator.py tests/test_benchmark_scenarios.py tests/test_service_repair.py
git commit -m "feat(bench): require evidenced drift and preserve scenario recovery"
```

### Task 6: Freeze final submissions and regrade sealed evidence offline

**Files:** Create `src/generative_driver/benchmark_support/evidence.py`, `tests/test_benchmark_v2_evidence.py`; modify `benchmark.py` (`score`, parser), `reporting.py`, `benchmark_support/ground.py`.

**Interfaces:**
- Consumes: `validate_records`, executed snapshots, accepted package hashes, existing `truth.seal`/`truth.unlock`.
- Produces `seal_run_evidence(payload: dict, path: Path, password_file: Path) -> dict` returning `{path, sha256}`; `regrade_v2(report: dict, evidence_path: Path, password_file: Path) -> dict`; `public_v2_report(payload: dict) -> dict`.
- Extend public `score(report, password_file=None, *, evidence_path=None)`. New CLI `--evidence` is required for v2 saved regrading. Old report/1 calls remain unchanged.
- Sealed payload schema `benchmark-run-evidence/1` has `case_pin`, `execution_snapshot`, `evaluations`, `accepted_gates`, `maintenance`. Each evaluation has `phase`, `revision`, `contract`, `records`, and `frozen_artifact_sha256`; initial and repaired submissions remain separate entries. Report/2 contains their allowed identity/measurement summaries and `final_evaluation.evidence_sha256`.

- [ ] **Step 1: Test that public projection removes private material by construction.**

```python
import json
import unittest
from generative_driver.benchmark_support.evidence import public_v2_report

class V2EvidenceTests(unittest.TestCase):
    def test_public_export_uses_allowlisted_fields(self):
        private = {'schema': 'benchmark-run-evidence/1',
            'case_pin': {'id': 'fixture', 'version': '2'},
            'records': [{'id': 'hidden/0/value', 'value': 9}],
            'contract': {'checks': [{'expected': 'PRIVATE SENTINEL'}]},
            'source_files': {'main.c': 'PRIVATE SENTINEL'},
            'unexpected_new_field': 'PRIVATE SENTINEL',
            'final_evaluation': {'verdict': 'failed', 'passed': 0, 'total': 1}}
        public = public_v2_report(private)
        self.assertEqual(public['schema'], 'benchmark-report/2')
        self.assertEqual(public['final_evaluation']['total'], 1)
        self.assertNotIn('PRIVATE SENTINEL', json.dumps(public))
        self.assertNotIn('records', public)
```

- [ ] **Step 2: Run red and implement explicit export shapes.**

Run: `python -m unittest discover -s tests -p test_benchmark_v2_evidence.py -v`
Expected: missing evidence module. Define allowlists for nested identity, metrics, stage attempts and maintenance fields; unknown fields never propagate. A public `case_pin` excludes evaluator-local paths and recipes. Final check summaries expose counts/verdicts, not hidden input/answer pairs.

```python
def seal_run_evidence(payload, path, password_file):
    from .truth import seal
    password = password_file.read_text(encoding='utf-8').strip()
    digest = seal(payload, path, password)
    return {'path': path.name, 'sha256': digest}
```

Before sealing, verify each final evaluation's records refer to that revision's frozen accepted package. `regrade_v2` unlocks with the report's ciphertext hash, checks its complete case/scenario/snapshot/artifact identity, recomputes every evaluation, and requires every accepted gate plus the appropriate maintenance result. Original and repaired package hashes may differ; a repaired pass cannot overwrite an earlier failed attempt. Reject incompatible evaluator code/hash. It does not trust a saved `verdict` or start a service. Public truth management keeps passwords outside logs/argv values; the only argument is the password-file path.

- [ ] **Step 3: Exercise independent regrading and hidden-feedback boundaries.**

Build a synthetic encrypted payload using `truth.seal` and a temporary test-only password. Correct records pass; changing public verdict to `passed` cannot make bad measured values pass. Swapped ciphertext, missing required records/gates, wrong scenario/artifact/evaluator hash and a modified package all fail. Include two revisions with different frozen hashes, require both remain in the regraded evidence, and reject a record moved between them. Patch `client.call` and `subprocess.Popen` to raise if invoked during saved scoring. For grounding, project only accepted diagnostic evidence and assert the final-record sentinel never enters worker inputs.

- [ ] **Step 4: Add final-failure controller behavior.**

Have the scripted integrated case return a failed frozen final check. Require terminal trial failure with `fault='model'` and no `route`; no new interpretation assignment follows. Diagnostic failures still use the existing bounded repair path. Preserve per-attempt evaluator results; do not overwrite old checks with a last-pass-only record. `report_run` emits report/2 only for v2 cases with snapshots; report/1 remains the legacy form.

- [ ] **Step 5: Run green and commit.**

```sh
python -m unittest discover -s tests -p test_benchmark_v2_evidence.py -v
python -m unittest discover -s tests -p test_benchmark_score.py -v
python -m unittest discover -s tests -p test_reporting.py -v
git add src/generative_driver/benchmark_support/evidence.py src/generative_driver/benchmark_support/ground.py src/generative_driver/benchmark.py src/generative_driver/reporting.py tests/test_benchmark_v2_evidence.py tests/test_benchmark_score.py tests/test_reporting.py
git commit -m "feat(bench): seal final evidence and support offline v2 grading"
```

### Task 7: Connect the complete TQ9 v2 workflow and calibrate its evaluator

**Files:**
- Create: `src/generative_driver/benchmark_support/emulated.py`, `src/generative_driver/benchmark_support/tq9_v2.py`, `tests/test_benchmark_tq9_v2.py`.
- Modify: `registry.py`, `authoring.py`, `evaluator_cli.py`, new TQ9 v2 manifest/bundle, `bench/README.md`, `bench/groundtruth/README.md`, `docs/support.md`, `docs/codex.md`, `docs/goose.md`.

**Interfaces:**
- `emulated.py` implements the five adapter entry points from Task 1 with the unchanged public signatures. `score` delegates v2 saved grading to `evidence.py`.
- Family modules export `contract(pin: dict, truth: dict, phase: str) -> dict`, `build_plan(pin: dict, truth: dict, phase: str) -> list[dict]`, `observations(raw: dict, canonical_task_result: dict, inputs: dict) -> dict`.
- Phases: `diagnostic`, `final`, `maintenance`. The engine supplies current `artifact_sha256` in an evaluator-local copy of truth to construct a contract. It never edits the encrypted source bundle or adds this data to worker inputs. The fresh worker must execute its own new visible mission; evaluator-only final calls cannot stand in for fresh-agent reuse. Preserve separate actor labels for both.
- Plan actions: `{episode, step, kind}` with `kind` in `reset`, `stimulate`, `call`, `observe`; reset/stimulate add `values`; call adds `task`, `inputs`, `grants`; observe adds `checks` (full unique check IDs). Observations map the check-name suffix to measured values. Calls produce normal runtime result fields `ok`, `outputs`, `units`, `transcript`.

- [ ] **Step 1: Write a real public preparation test before adding the adapter.**

```python
import json
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark import prepare_stage

class TQ9V2Tests(unittest.TestCase):
    def test_new_acquisition_contains_only_supplied_binary_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = prepare_stage('tq9-v2', 'acquire', root/'run', root/'worker',
                options={'scenario_id': 'semantic', 'case_seed': 0,
                         'evaluator_password_file': '/private/never-copy'})
            self.assertEqual(len(prepared['inputs']), 1)
            self.assertEqual(Path(prepared['inputs'][0]).suffix, '.bin')
            projected = json.dumps(prepared)
            self.assertNotIn('/private/never-copy', projected)
            self.assertNotIn('scenario_id', projected)
            self.assertEqual(prepared['context']['origin'], 'provided_binary')
```

- [ ] **Step 2: Run red, then implement one stage at a time.**

Run: `python -m unittest discover -s tests -p test_benchmark_tq9_v2.py -v`
Expected: missing/new adapter preparation, not a dependency or password failure. Implement acquire and its actual hash acceptance first; next sealed interpretation; next capability mapping and diagnostic probe; then grounding, package checks, fresh reuse/final tests, and maintenance. Reuse toolkit calls (`interpret_run/collect`, `model_validate`, `emit_package/check`) and controller acceptance, never worker self-reported success. Each next stage gets a failing public-seam test before implementation.

The independent action runner follows this order:

```python
def run_call(session, invoke, request):
    session.set_running(True)
    try:
        return invoke(request['operation'], request['parameters'])
    finally:
        session.set_running(False)
```

Define `run_call` in `emulated.py`; `invoke` is a supplied closure around the existing runtime/package operation, with binding and effect grants owned by the controller. Observation happens after pausing, over the evaluator channel. Persist raw evidence and each check ID before computing verdicts. No candidate-supplied arbitrary monitor command is allowed. A package tool returns permitted observations for the worker's diagnostic mission; final private episodes are executed evaluator-side after the worker submission is frozen.

- [ ] **Step 3: Add full scripted workflow contract tests.**

Use the external scripted-worker pattern in `tests/test_service_repair.py` and synthetic encrypted case assets. Run semantic and control children independently. Assert all seven stage names, accepted-artifact hashes, fresh workspace/package-only reuse, one semantic maintenance cycle, zero control maintenance cycles, stale gateway rejection, and terminal failure after a hidden wrong-value result. Inspect assignments for truth/scenario sentinels. Use durable `stage.assigned`/`run.revision` events with bounded deadlines; test both unavailable monitor and uncertain application recovery.

- [ ] **Step 4: Implement and run evaluator-only calibration.**

Expose `benchmark truth calibrate --case tq9-v2 --password-file FILE --renode PATH --ghidra-home PATH --java-home PATH --output DIR`. It calls `authoring.calibrate`; it never calls configured model runtimes. Validate two distinct correct reference models and the wrong-scale/constant-output/wrong-state/false-drift mutants against original, semantic and control scenarios, plus the identity variant. Calibration records pin source/image/contract hashes without self-referencing their enclosing ciphertext. Store a new encrypted bundle containing calibration results and exact tool versions; the public manifest records the calibration evidence commitment. Failed or skipped native tests leave `calibration: {status: pending}` and block actual-agent starts.

The implementation executor obtains path values from documented installation settings; do not hardcode the current user's paths. `FILE`, `PATH` and `DIR` in this command are user-supplied CLI metavariables, not missing implementation choices. Tests use temporary files and explicit fixture executable paths.

- [ ] **Step 5: Verify the installed distribution and document the scope.**

Run `python -m unittest discover -s tests -v`, `python -m build`, then the existing installed-wheel CI procedure in `.github/workflows/test.yml` on macOS/Windows Python 3.11/3.13. Installation smoke must still be free of optional dependencies. Document `tq9` versus `tq9-v2`, registered case listing, diagnostic versus sealed final feedback, scenario selection, password handling, calibration and offline score commands. A native Windows emulator result is claimed only after that actual backend gate runs; Windows Python CI alone does not establish it.

- [ ] **Step 6: Commit and hand off the calibrated case interfaces.**

```sh
git add src/generative_driver/benchmark_support/emulated.py src/generative_driver/benchmark_support/tq9_v2.py src/generative_driver/benchmark_support/registry.py src/generative_driver/benchmark_support/authoring.py src/generative_driver/benchmark_support/evaluator_cli.py src/generative_driver/resources/bench/cases/tq9-v2 src/generative_driver/resources/bench/groundtruth/tq9-v2.enc tests/test_benchmark_tq9_v2.py bench/README.md bench/groundtruth/README.md docs/support.md docs/codex.md docs/goose.md
git commit -m "feat(bench): qualify TQ9 v2 across discovery and maintenance"
```

## Completion evidence and handoff

Deliver registry compatibility results, installed-wheel checks, v2 scripted contract results, actual native calibration records, and an unchanged hash inventory for legacy TQ9/baseline files. No model success claim follows from these tests. If an optional native backend is unavailable, retain pending calibration and name that unmet gate; do not fabricate it or silently substitute replay.

Read Plan 02 against these interfaces before proceeding. Implementation begins only after the user reviews this plan set and selects the execution approach, as required by the writing-plans skill.
