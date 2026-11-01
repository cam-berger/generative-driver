# Generated-Device Grader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each case its family's grader identity, register generated sets by their manifest hash, and open one generated device at a time. Grade a frozen driver on a generated device step by step, report undecided steps as an `unknown` grade, and qualify a generated set once with its references and mutants.

**Architecture:**
- **Identity.** Family modules leave the shared grading core and case registrations become data, so `grader_identity(case_id=...)` hashes the core, one family and that case's own entry.
- **Grading.** A new `generated` family module grades a device's answer key with its own step executor, through the existing package invoker and a new per-cell state read on the Renode session.
- **Unknown grades.** `validate_records` and `final_record` learn undecided checks and an `unknown` status, which the configurator records and a suite settles.
- **Sets.** `generated_sets` reads a set bundle outside the repository, checks it against its registered manifest hash and decrypts one device at a time. `set_qualification` runs both references on every device and mutants on a sample, then seals its record beside the set.

**Tech Stack:** Python 3.11+ standard library, `cryptography` (AES-GCM through `truth.seal` and `truth.unlock`), `unittest`.

**Spec:** [Benchmark baseline spec](../../../.scratch/benchmark-baseline/spec.md): decisions 7, 10, 11, 15 and 19, the builder-check settlements, and the 2026-11-01 amendments (per-family identity). This is plan 3 of 6 and requires plans 1 and 2 merged.
- **Plan 4** is the bench harness (decisions 17, 20, 21) and running trials on generated devices: their pins, snapshots, missions, a UART-only device provider and the mission read after reuse.
- **Plan 5** is the generator.
- **Plan 6** is the baseline runbook.

## Global Constraints

- Native macOS and Windows 11+: portable paths, argument-array launches, no hardcoded user paths, and outputs outside installed resources. Set bundles and qualification outputs live outside the repository and the installed package.
- Each behavior change: write one failing test, watch it fail, implement, watch it pass ([AGENTS.md](../../../AGENTS.md)).
- Public tests use hand-written fixture devices and fixture sets sealed with the password `'synthetic fixture credential'`, never generator output ([ADR 0005](../../adr/0005-keep-the-device-generator-private.md)).
- The configurator and workers never receive passwords, password paths or plaintext answer keys. An answer key is decrypted only inside grading or qualification.
- Anchors grade as before: the same records give the same checks, ids and verdict. Existing tests change only where a task says so.
- A step whose state cannot be read, or whose sequence did not start from a verified reset, is undecided, never failed. A grade with no failed check and at least one undecided check is `unknown`.
- A refusal counts whoever makes it, while the state stays as the reference leaves it. No reply matches only a reference silence. An error never matches (decision 19).
- Run tests from the repository root with `GD_TEST_WHEEL` and `GD_TEST_SDIST` unset: `PYTHONPATH=src "$GD_PY" -m unittest discover -s tests -p <module>.py`.
- Work on branch `benchmark-baseline/generated-grader`. Commit after each task, ending each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. An anchor's identity must change when any module its grading imports changes, including the shared core and `cryptography`. It must not change when another family's module or another case's registration changes (Tasks 1, 2).
2. A device file whose bytes differ from the manifest, or the wrong password, must be refused before any plaintext is returned (Task 3).
3. A package that prints a host fault while the evaluator's device stays healthy is the driver's `error`, not a fault. A dead device still leaves the grade unscored (Task 5).
4. A driver that declines an out-of-range input before sending matches a reference refusal only if the state read afterwards is the reference's (Task 5).
5. A qualification record for another manifest or another grader identity admits nothing. A set whose sampled mutant passes stays pending (Task 6).

---

### Task 1: Family code leaves the shared grading core

**Files:**
- Modify `src/generative_driver/benchmark_support/`:
  - `registry.py`: the tq9 branch of `require_calibration` (:340-350) takes the family's `contract` through `registrations.native_family_for(manifest['id'])`.
  - `calibration.py`: `REQUIRED_MUTANTS` (:59-65) moves into the family modules. The two `parameter_store` imports (:119-122, :303-306) go through the family. `_FAMILIES` (:16) follows the registrations whose `calibration_kind` is `'family'`.
  - `evaluator.py`: `case_truth` (:28-41) hydrates by the registration's `truth_schema`, not by case ids.
  - `registrations.py`: add `family_module(family)`.
  - `grader_identity.py`: `grader_closure(root=None, *, families=None)`.
- Modify `tq9_v2.py`, `sampled_sensor.py` and `parameter_store.py`: each gains `REQUIRED_MUTANTS` with today's set, verbatim. `zephyr_modbus.py` keeps its own, and the duplicate in `calibration` goes.
- Test: `tests/test_grader_identity.py`

**Interfaces:**
- Produces:
  - `registrations.family_module(family: str) -> ModuleType`: the one native module that the registered cases of `family` name. Raises `ValueError` when there is none, or more than one.
  - `grader_closure(root=None, *, families: tuple[str, ...] | None = None)`: `None` seeds every registered native module, as today. A tuple seeds only those native module names, and `()` walks the core alone.
  - Each family module exposes `REQUIRED_MUTANTS: frozenset[str]`.

- [ ] **Step 1: Write the failing test** `test_the_shared_core_imports_no_family`:

```python
families = {'benchmark_support.' + c.native_module for c in registrations._CASES if c.native_module}
self.assertEqual(families & set(grader_closure(families=())), set())
```

- [ ] **Step 2: Run it.** Expected: FAIL. The core closure holds `benchmark_support.tq9_v2` and `benchmark_support.parameter_store`.
- [ ] **Step 3: Route the three static imports and the family tables through the registrations,** and add the `families` seed.
- [ ] **Step 4: Run `test_grader_identity.py`, `test_benchmark_family_calibration.py`, `test_benchmark_tq9_v2.py`, `test_upstream_modbus.py` and `test_parameter_store_family.py`.** Expected: pass, with no other test changed.
- [ ] **Step 5: Commit** "Keep family code out of the shared grading core".

### Task 2: Each case is admitted and stamped under its family's grader identity

**Files:**
- Create: `src/generative_driver/resources/registrations/cases.json`. It holds today's `_CASES` as data, `{"schema": "benchmark-case-registrations/1", "cases": [{field: value}, ...]}`, outside `resources/bench`, so a case-root override never replaces it.
- Modify `src/generative_driver/benchmark_support/`:
  - `registrations.py`: build `_CASES` from that file, read relative to the package and never through `case_root()`. Refuse duplicate keys, unknown fields and duplicate ids.
  - `grader_identity.py`: `grader_identity(root=None, *, case_id=None)` with schema `benchmark-grader-identity/2`, and `case_revisions(case_ids)`.
  - `calibration.py`: `validate_calibration` (:85) compares with `grader_identity(case_id=manifest['id'])`.
  - `snapshots.py`: `execution_provenance(config, *, case_ids, reuse_profile=None)` (:82).
  - `evaluator.py`: `accepted_driver` (:232).
  - `evidence.py`: `_installed_evaluator` (:319) reads the case from `snapshot['case_pin']['case_id']`.
  - `reference_calibration.py` and `upstream_qualification.py`: the identity stamps and rechecks.
  - `suite_reporting.py`: at :698, each trial is compared with its case's frozen revision.
- Modify `src/generative_driver/configurator.py`: pass the pin's case at `_start_new` (:2149) and `_verify_snapshot` (:2599), and the entry cases at `suite_start` (:1538) and `_verify_suite` (:1886).
- Docs:
  - `.scratch/benchmark-lifecycle/issues/01-scope-qualification-closure.md`: answer "Still open" with this plan, then `Status: resolved`.
  - `docs/adr/0001-benchmark-registration-and-selection.md`, deferred work: registrations are code-owned data, and an identity covers its case's family.
  - `docs/benchmark-case-authoring.md`: the qualification identity.
- Test: `tests/test_grader_identity.py`, `tests/test_benchmark_snapshots.py` (:46-47), `tests/test_benchmark_family_calibration.py` (:111-124)

**Interfaces:**
- Consumes: `grader_closure(families=...)` from Task 1.
- Produces:
  - `grader_identity(root=None, *, case_id: str | None = None) -> dict` returns `{'schema': 'benchmark-grader-identity/2', 'scope', 'modules', 'registrations', 'dependencies', 'sha256'}`.
    - With `case_id`, `scope` is the case id and `modules` hashes the core plus that case's native module, or the core alone when it has none. `registrations` is that case's own entry.
    - Without `case_id`, `scope` is `'grader'` and every family and every entry is covered.
    - `root` also names the `cases.json` that is read.
  - `case_revisions(case_ids) -> dict` returns `{'evaluator_revision': str, 'evaluator_revisions': {case_id: sha256}}`. The revision is the single sha256 when every case has the same identity, otherwise the `canonical_digest` of the mapping.
  - `execution_provenance(config, *, case_ids, reuse_profile=None)` returns both keys. `evaluator_revision` keeps its place in `comparison_identity`.

- [ ] **Step 1: Write the failing tests** in `test_grader_identity.py`, on a copy of the installed `generative_driver` tree under a temporary root:

```python
def test_a_case_keeps_its_identity_when_unrelated_code_or_cases_change(self):
    before = grader_identity(root, case_id='tq9-v2')['sha256']
    add_case(root, {'id': 'unrelated-v1', 'family': 'unrelated', 'version': '1', 'adapter_key': 'emulator-v2',
                    'schema': 'benchmark-case/2', 'native_module': 'sampled_sensor'})
    touch(root / 'benchmark_support/sampled_sensor.py')
    self.assertEqual(grader_identity(root, case_id='tq9-v2')['sha256'], before)

def test_a_case_identity_changes_with_its_family_the_core_or_its_own_entry(self):
    for change in (lambda: touch(root / 'benchmark_support/tq9_v2.py'),
                   lambda: touch(root / 'benchmark_support/evaluator.py'),
                   lambda: set_version(root, 'tq9-v2', '6')):
        before = grader_identity(root, case_id='tq9-v2')['sha256']
        change()
        self.assertNotEqual(grader_identity(root, case_id='tq9-v2')['sha256'], before)
```

`touch` appends `\n# changed\n`. `add_case` and `set_version` rewrite the copy's `resources/registrations/cases.json`.

In `test_benchmark_snapshots.py`, add two assertions:
- `execution_provenance({}, case_ids=('tq9-v2',))['evaluator_revision']` equals `grader_identity(case_id='tq9-v2')['sha256']`;
- two cases from different families give the digest of `{case_id: sha256}`.

- [ ] **Step 2: Run them.** Expected: FAIL, because `grader_identity()` takes no `case_id`.
- [ ] **Step 3: Move the table, then implement the scoped identity,** passing the case at every stamp and check listed above.
- [ ] **Step 4: Run these modules, then the full suite.** Expected: pass with 4 skips, once fixtures that build a record with `grader_identity()` name its case.
- [ ] **Step 5: Update the three docs, then commit** "Scope the grader identity to a case's family".

### Task 3: Generated sets register by their manifest and open one device at a time

**Files:**
- Create: `src/generative_driver/benchmark_support/generated_sets.py`
- Create: `src/generative_driver/resources/registrations/sets.json` containing `{"schema": "generated-set-registrations/1", "sets": {}}`
- Create: `tests/generated_fixtures.py` (the fixture set writer) and `tests/test_generated_sets.py`
- Docs: `docs/adr/0003-generated-devices-register-by-set.md`. Registrations live in `resources/registrations/sets.json`, outside every grader identity, and a comparison carries the manifest hash.

**Interfaces:**
- Produces:
  - `FRAMINGS = ('text-lines', 'binary-frames', 'register-access')`. Levels are 1 to 5.
  - `set_registrations(path: Path | None = None) -> dict[str, str]`: registered set name → manifest SHA-256, from `sets.json` read relative to the package. Duplicate keys are refused.
  - `seal_device(set_root: Path, device: dict, *, image: bytes, mission: dict, answer_key: dict, password: str) -> dict`:
    - Writes, through `truth.seal`, `devices/<id>.inputs.enc` (holding `{'image_base64', 'mission'}`) and `devices/<id>.answer.enc`.
    - Returns the manifest entry `{id, level, framing, image_sha256, mission_sha256, answer_key_sha256, inputs: {path, sha256}, answer: {path, sha256}}`.
    - `mission_sha256` and `answer_key_sha256` are `canonical_digest`s.
  - `write_manifest(set_root, registration, devices) -> str`: writes `manifest.json` (`{'schema': 'generated-set-manifest/1', 'set': registration, 'devices': [...]}`) and returns its SHA-256.
  - `load_set(set_root: Path, registration: str, *, registrations: dict | None = None) -> dict`: returns the manifest with `manifest_sha256` added, after these checks:
    - the SHA-256 of its bytes matches the registration;
    - schema and set name;
    - unique ids, levels and framings;
    - every path stays inside `set_root`.
  - `open_device(set_root, manifest, device_id, password, *, answer=False) -> dict`:
    - Returns `{'image': bytes, 'mission': dict}`, plus `'answer_key'` when asked.
    - Every file goes through `truth.unlock` with its manifest hash, and every content hash is checked.
    - Any mismatch raises `ValueError` before anything is returned.

- [ ] **Step 1: Write the failing tests** with a two-device fixture set (`d001`, `d002`, both level 1 and `text-lines`), sealed with `PASSWORD = 'synthetic fixture credential'`:

```python
def test_a_set_opens_only_under_its_registered_manifest_hash(self):
    sha = write_fixture_set(root)
    self.assertEqual(load_set(root, 'fixture-v1', registrations={'fixture-v1': sha})['manifest_sha256'], sha)
    with self.assertRaisesRegex(ValueError, 'not the registered manifest'):
        load_set(root, 'fixture-v1', registrations={'fixture-v1': '0' * 64})

def test_a_device_opens_alone_and_its_answer_key_only_when_asked(self):
    manifest = load_set(root, 'fixture-v1', registrations={'fixture-v1': write_fixture_set(root)})
    device = open_device(root, manifest, 'd001', PASSWORD)
    self.assertEqual((device['image'], sorted(device)), (FIXTURE_IMAGE, ['image', 'mission']))
    self.assertIn('answer_key', open_device(root, manifest, 'd001', PASSWORD, answer=True))
```

Two more tests:
- `test_a_changed_file_or_the_wrong_password_is_refused_before_plaintext`: one flipped byte in `devices/d002.inputs.enc`, and the password `'wrong'`, each raise `ValueError`.
- `test_a_path_that_leaves_the_set_is_refused`: an entry whose `inputs.path` is `'../d001.inputs.enc'` raises `ValueError` in `load_set`.

Until Task 5, the fixture answer key is a placeholder `{'schema': 'generated-answer-key/1'}`.

- [ ] **Step 2: Run them.** Expected: ERROR, no module `generated_sets`.
- [ ] **Step 3: Implement `generated_sets.py`** with `truth.seal`, `truth.unlock` and `snapshots.canonical_digest`.
- [ ] **Step 4: Run `test_generated_sets.py` and `test_grader_identity.py`.** Expected: pass.
- [ ] **Step 5: Update ADR 0003, then commit** "Register generated sets by their manifest hash".

### Task 4: An undecided check makes an unknown grade that a suite settles

**Files:**
- Modify `src/generative_driver/benchmark_support/`:
  - `behavior.py`: in `validate_records` (:90-118), a record carrying `undecided: <reason>` makes its check `{'passed': None, 'reason': <reason>}`. The verdict is `'unknown'` when no check failed and one is undecided.
  - `evaluator.py`: `final_record` (:351-364) gives status `unknown` with `equivalent` None, and `record` keeps `passed` None.
  - `labels.py`: `equivalence_label` gives `unknown` for that status.
  - `evidence.py`: `regrade_v3` (:394) accepts a sealed `unknown`.
  - `suites.py`: `child_disposition` (:285-295) advances on `unknown`.
- Modify `src/generative_driver/configurator.py`:
  - `_evaluation_errors` (:660) accepts (`unknown`, None) when every check passed or is undecided, at least one is undecided, and a diff names each one.
  - `_record_evaluation` (:2859) records it as the final verdict, as it records `graded`.
- Test: `tests/test_evaluation.py`, and `tests/test_suite_service.py` beside `test_a_child_graded_after_its_run_advances_only_with_a_graded_or_no_driver_record`

**Interfaces:**
- Produces:
  - A check row's `passed` is True, False or None.
  - `final_record` status is `graded` when every check is decided and `unknown` when none failed and one is undecided. Other statuses are as before.
  - `passed` counts the True rows. `diff` names each failed or undecided check with its reason. Plan 2's `outcomes.classify` already maps the `unknown` status.

- [ ] **Step 1: Write the failing tests** in `test_evaluation.py`:

```python
def test_an_undecided_check_makes_an_unknown_grade_and_a_failure_outweighs_it(self):
    contract = contract_of(('s1/0/answer', 'value'), ('s1/0/state:mode', 2))
    decided = [record_for(contract, 's1/0/answer', 'value')]
    undecided = decided + [{**record_for(contract, 's1/0/state:mode', None), 'undecided': 'unreadable state'}]
    made = final_record(final_of(contract, undecided), protocol='assistive/1')
    self.assertEqual((made['status'], made['equivalent'], [c['passed'] for c in made['checks']]), ('unknown', None, [True, None]))
    self.assertEqual(made['diff'], [{'id': 's1/0/state:mode', 'reason': 'unreadable state'}])
    self.assertEqual(_evaluation_errors({**made, 'evidence_sha256': 'a' * 64}), [])
    failed = final_record(final_of(contract, [record_for(contract, 's1/0/answer', 'error')] + undecided[1:]), protocol='assistive/1')
    self.assertEqual((failed['status'], failed['equivalent']), ('graded', False))
```

`contract_of`, `record_for` and `final_of` are small helpers in the test module. They build a `benchmark-behavior/1` contract (a text check and a number check) and its records.

Add `test_an_unknown_grade_settles_its_slot` to `test_suite_service.py`:

```python
self.assertEqual(child_disposition({'stopping': False, 'uncertain_effect': False, 'status': 'completed',
                                    'outcome_category': 'completed', 'equivalence': {'status': 'unknown'}}), 'advance')
```

- [ ] **Step 2: Run them.** Expected: FAIL. The undecided check fails and the record is `graded`.
- [ ] **Step 3: Implement the undecided rows and the `unknown` status** in the seven places listed.
- [ ] **Step 4: Run `test_evaluation.py`, `test_suite_service.py`, `test_outcomes.py` and `test_benchmark_v2_evidence.py`.** Expected: pass.
- [ ] **Step 5: Commit** "Report undecided checks as an unknown grade".

### Task 5: A generated device's frozen driver is graded step by step

**Files:**
- Create: `src/generative_driver/benchmark_support/generated.py`
- Add this entry to `cases.json`:

```json
{"id": "generated", "family": "generated", "version": "1", "adapter_key": "emulator-v2", "schema": "benchmark-case/2",
 "native_module": "generated", "truth_schema": "generated-answer-key/1", "calibration_kind": "set"}
```

  Case listings show it as unavailable, as they show `tq9-v3`.
- Modify: `native.py` adds `NativeSession.read_state(cells)`. In `evaluator.py`, `reference_execute` (:55-101) runs `family.execute` when the family has one, and `execute_plan` otherwise.
- Modify: `tests/generated_fixtures.py` gains a text-line fixture device with a memory map and a monitor, its answer key, two reference models and the mutants.
- Test: `tests/test_generated_family.py`

**Interfaces:**
- Consumes: undecided records from Task 4; the registrations from Task 2.
- Produces:
  - The answer key, schema `generated-answer-key/1`:
    - `recipe`: a `NativeSession` recipe, with reset commands and observations.
    - `state`: `{cell: {'address': int, 'width': 8|16|32}}`.
    - `reset`: `{cell: int}`, the state a verified reset leaves.
    - `sequences`: `[{'id', 'steps': [...]}]`. Each step is `{'task', 'inputs', 'grants', 'answer', 'outputs'?, 'refusal_rx_hex'?, 'state'}`, where:
      - `answer` is `'value'`, `'refusal'` or `'no_reply'`;
      - `outputs` is `{name: {'value', 'tolerance'}}`;
      - `refusal_rx_hex` is a string;
      - `state` is `{cell: int}`.
    - `mission`: `{'state': {cell: int}}`.
    - `references`: `{'a' | 'b': {'model', 'capabilities'}}`.
    - `mutants`: `[{'id', 'model', 'capabilities', 'expected_failed_checks'}]`.
  - `generated.contract(pin, truth, phase) -> dict` is a `benchmark-behavior/1` contract with these check ids:
    - `<sequence>/<step>/answer`, a text check;
    - `<sequence>/<step>/out:<name>`, a number check, only for a `value` step;
    - `<sequence>/<step>/state:<cell>`, a number check with tolerance 0 on channel `device-state`.
  - `generated.classify_answer(result: dict, step: dict) -> str` returns one of:
    - `value`: `ok` is true;
    - `refusal`: either the driver declined before contact, or the device replied the step's `refusal_rx_hex` to the requested operation. Declining before contact means `transport_open_attempted` is false, or no transcript entry exists for the requested operation, with fault `model` or `operator`;
    - `no_reply`: identity was verified and the requested operation timed out with nothing received (field names as `interface_runtime/engine.py` writes them);
    - `error`: anything else.
  - `generated.execute(session, invoke, contract, truth, evidence_path, *, healthy_device_failures) -> list[dict]`:
    - For each sequence it calls `session.reset({})`, then `read_state`. A reset state other than `truth['reset']` makes every record in that sequence undecided (`'unverified reset'`).
    - For each step it calls the task through `run_call` with the step's grants, classifies the answer, reads the state and writes one record per check. An unreadable cell is undecided (`'unreadable state'`).
    - A host fault on a healthy device is the driver's `error`. On an unhealthy device it raises, as `execute_plan` does.
  - `NativeSession.read_state(cells) -> {cell: int | None}` sends one `sysbus ReadByte`, `ReadWord` or `ReadDoubleWord 0x%08x` per cell. A rejected, timed-out or malformed read gives None.
  - `generated.observations` has `monitor_units = {}`.
  - `REQUIRED_MUTANTS = frozenset({'wrong-scale', 'constant-output', 'wrong-state'})`.
  - `reuse_passed(events, capabilities)` returns None until plan 4 reads the device after reuse.

- [ ] **Step 1: Write the failing tests** against the fixture device, which behaves like this:
  - `MODE?` replies `MODE:<n>`;
  - `MODE <n>` sets the mode and replies `OK` when 0 ≤ n ≤ 3, and replies `ERR` otherwise;
  - `PING` is never answered;
  - the mode lives at `0x20000000`.

```python
def test_a_correct_driver_passes_every_step_including_a_refusal_and_a_silence(self):
    self.assertEqual(self.grade(REFERENCE_A)['grade']['verdict'], 'passed')

def test_a_driver_that_declines_an_out_of_range_mode_before_sending_matches_the_devices_refusal(self):
    # REFERENCE_B bounds mode to 0..3 in its capabilities, so bind_task refuses 9 without I/O and the mode stays 2.
    self.assertEqual(self.grade(REFERENCE_B)['grade']['verdict'], 'passed')

def test_a_driver_that_reports_the_devices_refusal_as_a_value_fails_that_step(self):
    graded = self.grade(MUTANT_ERR_AS_VALUE)
    self.assertIn('s1/1/answer', [c['id'] for c in graded['grade']['checks'] if c['passed'] is False])

def test_an_unreadable_cell_or_an_unverified_reset_is_undecided_never_failed(self):
    self.assertEqual(self.grade(REFERENCE_A, unreadable={0x20000000})['grade']['verdict'], 'unknown')
    self.assertEqual(self.grade(REFERENCE_A, reset_mode=1)['grade']['verdict'], 'unknown')

def test_a_package_host_fault_on_a_healthy_device_is_the_drivers_error(self):
    self.assertEqual(classify_answer({'ok': False, 'transcript': [], 'error': {'fault': 'model', 'reported_fault': 'host',
                                      'classified': 'healthy-device'}}, STEP), 'error')
```

`self.grade(model, ...)` works in two steps:
1. It emits the model's package against the fixture device with `reference_calibration.run_reference`, as `emit_toy_package` does (`tests/test_benchmark_family_calibration.py:272`).
2. It grades that package with `reference_execute` under `pin = {'case_id': 'generated', 'scenario_id': 'original', 'case_seed': 0, 'revision': 0}`, with `NativeSession.start` patched to return the fixture's session.

- [ ] **Step 2: Run them.** Expected: ERROR, no module `generated`.
- [ ] **Step 3: Implement `generated.py`, `read_state` and the `execute` hook.**
- [ ] **Step 4: Run `test_generated_family.py`, `test_benchmark_family_calibration.py` and `test_grader_identity.py`.** Expected: pass. `grader_identity(case_id='generated')` covers `generated` and the core.
- [ ] **Step 5: Commit** "Grade a generated device's driver step by step".

### Task 6: A generated set is qualified once with its references and mutants

**Files:**
- Create: `src/generative_driver/benchmark_support/set_qualification.py`. Like `reference_calibration`, it stays outside the grader closure; add it to `SYSTEM_MODULES`.
- Modify: `generated_sets.py` (`admitted` and the qualification seal) and `src/generative_driver/benchmark.py` (`benchmark set qualify`).
- Docs: `docs/benchmark-case-authoring.md` gains a "Generated sets" section covering the bundle layout, registration and the qualification command.
- Test: `tests/test_set_qualification.py`

**Interfaces:**
- Consumes: `load_set` and `open_device` (Task 3); `generated` (Task 5); `grader_identity(case_id='generated')` (Task 2).
- Produces:
  - `qualify(set_root, registration, password, *, renode, output_dir, registrations=None) -> dict`:
    - For every device, it emits references `a` and `b` and grades them (`reference_calibration.run_reference`, then `evaluator.reference_execute`).
    - The sample is the first two devices by id of each (level, framing). For each sampled device it emits and grades every mutant.
    - A reference passes with verdict `passed`. A mutant is rejected when its verdict is `failed` and its `expected_failed_checks` are among the failed checks.
    - Each run records its seconds.
    - The record, schema `generated-set-qualification/1`, is `{schema, registration, manifest_sha256, grader_identity, status: 'passed'|'pending', devices: {id: {'a': bool, 'b': bool}}, sample: [ids], mutants: [{device, id, rejected, failed_checks}], failures: [str], seconds: {run: float}}`.
    - It is sealed with the set's password into `set_root/qualification.enc`, beside the manifest and never inside a device's answer key, so requalifying never changes the manifest hash. A plaintext copy goes to `output_dir`.
  - `generated_sets.admitted(set_root, registration, device_id, password, *, registrations=None) -> list[str]` returns the reasons the device is not admitted, or `[]`. A device is admitted when all of these hold:
    - the record decrypts;
    - it names the registration and the manifest hash that registration pins;
    - its `grader_identity` equals `grader_identity(case_id='generated')`;
    - its status is `passed`;
    - the device's two references passed.
  - CLI: `benchmark set qualify --set NAME --root DIR --password-file FILE --renode PATH --output DIR`. It prints the status and each run's seconds. The password file is read once and never printed.

- [ ] **Step 1: Write the failing tests** with the two-device fixture set from Tasks 3 and 5, and `NativeSession.start` patched to return fixture sessions:

```python
def test_a_set_qualifies_when_references_pass_everywhere_and_sampled_mutants_fail(self):
    record = qualify(root, 'fixture-v1', PASSWORD, renode=Path('/toy'), output_dir=out, registrations=REGISTERED)
    self.assertEqual((record['status'], record['sample'], record['failures']), ('passed', ['d001', 'd002'], []))
    self.assertEqual(admitted(root, 'fixture-v1', 'd001', PASSWORD, registrations=REGISTERED), [])
```

Two more tests:
- `test_a_failing_reference_or_a_passing_mutant_leaves_the_set_pending`. Seal reference `b` as the refusal-as-value mutant, and seal a mutant that is a copy of reference `a`. Each leaves status `'pending'`, with a failure naming the device and the run.
- `test_admission_needs_the_registered_manifest_and_the_current_family_identity`. After qualifying:
  - patching `grader_identity` to another sha256 gives a reason naming the identity;
  - registrations that pin another manifest hash give a reason naming the manifest.

- [ ] **Step 2: Run them.** Expected: ERROR, no module `set_qualification`.
- [ ] **Step 3: Implement `qualify`, `admitted` and the command.**
- [ ] **Step 4: Run `test_set_qualification.py`, `test_generated_sets.py` and `test_grader_identity.py`, then the full suite.** Expected: pass with 4 skips.
- [ ] **Step 5: Update the authoring guide, then commit** "Qualify a generated set once with its references and mutants".
