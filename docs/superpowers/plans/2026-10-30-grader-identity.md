# Grader Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The identity that admission and comparison check covers only grading code, so the system under test can change without voiding a scoring set's admission or blocking comparison with a stored baseline.

**Architecture:** A new `grader_identity` module computes the grader's closure: the static import closure of the grading entry modules plus every registered family module, hashed file by file. Helpers the grader borrows from the system move into small closure modules that the system imports instead; reference building, stage adapters and legacy recorded grading move out of grading modules. Final grading accepts a frozen package by its recorded digest and runs the package's own bundled runtime. Admission, `evaluator_revision` and regrade then check the grader identity.

**Tech Stack:** Python 3.11+ standard library (`ast`, `hashlib`, `importlib.metadata`), `unittest`.

**Spec:** [Benchmark baseline spec](../../../.scratch/benchmark-baseline/spec.md), "Settled from the builder check", first bullet; [issue 01](../../../.scratch/benchmark-lifecycle/issues/01-scope-qualification-closure.md). Plan 1 of 5: grader identity, scorecard, generated sets and harness, private generator, baseline runbook.

## Global Constraints

- Native macOS and Windows 11+: argument-array process launches, portable paths, no hardcoded user paths, outputs outside installed resources.
- Each behavior change: write one failing test, watch it fail, implement, watch it pass ([AGENTS.md](../../../AGENTS.md)).
- The bench harness alone holds passwords; nothing here passes passwords, their paths or plaintext answers to the configurator or workers.
- Grading executes no system-under-test code except the frozen package itself, in its own `python -I -S -B` subprocess as `package_invoker` already does.
- Any change to a closure file changes the grader identity and voids admission until requalification; that is intended.
- Run tests from the repository root: `env -u GD_TEST_WHEEL -u GD_TEST_SDIST PYTHONPATH=src "$GD_PY" -m unittest discover -s tests -p <module>.py [-k <test>]`, where `GD_PY` is the main checkout's `.venv/bin/python`.
- Work on branch `benchmark-baseline/grader-identity` from `main`; commit after each task, ending each message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A family module the registry imports by name (`native_module` in `registrations.py`) must count toward the identity; a missed dynamic import would let a grading change pass admission unnoticed (Task 1).
2. A package whose bundled runtime differs from this checkout's, as every package from a changed system will, must be graded instead of being recorded as an evaluator host fault (Task 2).
3. A package altered after freezing must still be refused before any device access once `emit_check` no longer runs (Task 2).
4. A qualification record carrying the old `benchmark-evaluator-identity/2` must fail admission with `identity:evaluator_identity`, never raise (Task 6).
5. A sealed run regraded after a system change but under the same grader must regrade; one regraded under a changed grader must be refused (Task 6).

---

### Task 1: Compute the grader's closure and identity

**Files:**
- Create: `src/generative_driver/benchmark_support/grader_identity.py`
- Test: `tests/test_grader_identity.py`

**Interfaces:**
- Produces:
  - `ENTRY_MODULES = ('benchmark_support.evaluator', 'benchmark_support.evidence', 'benchmark_support.registry', 'benchmark_support.calibration', 'benchmark_support.device_provider')`, names relative to `generative_driver`.
  - `SYSTEM_MODULES`: `configurator`, `toolkit`, `_toolchain`, `agents`, `client`, `setup`, `benchmark`, `reporting`, `replay`, `device_access`, `analysis`, and in `benchmark_support`: `emulated`, `cases`, `ground`, `physical`, `legacy`, `harness`, `suites`, `suite_store`, `suite_reporting`, `corpus`, `authoring`, `reference_calibration`, `upstream_qualification`; plus the top-level `interface_runtime`.
  - `grader_closure(root: Path | None = None) -> tuple[str, ...]`: sorted module names reachable from `ENTRY_MODULES` and every `benchmark_support.<native_module>` in `registrations._CASES`, following every `import` and `from` statement, including those inside function bodies. `root` is a `generative_driver` package directory and defaults to the installed one. Standard-library and third-party imports end the walk; `interface_runtime` imports are kept as `interface_runtime...` names so Task 5 can forbid them.
  - `grader_identity(root: Path | None = None) -> dict`: `{'schema': 'benchmark-grader-identity/1', 'modules': {name: sha256_of_file}, 'dependencies': {'cryptography': importlib.metadata.version('cryptography')}, 'sha256': canonical_digest(body)}`, with `canonical_digest` from `snapshots.py`.

- [ ] **Step 1: Write the failing tests**

```python
class GraderClosureTests(unittest.TestCase):
    def copy_package(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'generative_driver'
        shutil.copytree(Path(generative_driver.__file__).parent, root, ignore=shutil.ignore_patterns('__pycache__'))
        return root

    def test_closure_follows_imports_inside_functions(self):
        self.assertIn('benchmark_support.behavior', grader_closure())  # final_record imports it in its body

    def test_closure_includes_registered_family_modules(self):
        self.assertLessEqual({'benchmark_support.parameter_store', 'benchmark_support.sampled_sensor',
                              'benchmark_support.tq9_v2', 'benchmark_support.zephyr_modbus'}, set(grader_closure()))

    def test_grading_change_changes_identity(self):
        root = self.copy_package(); before = grader_identity(root)['sha256']
        path = root / 'benchmark_support' / 'behavior.py'
        path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        self.assertNotEqual(grader_identity(root)['sha256'], before)
```

- [ ] **Step 2: Run `-p test_grader_identity.py`.** Expected: ERROR, `No module named 'generative_driver.benchmark_support.grader_identity'`.
- [ ] **Step 3: Implement `grader_closure` and `grader_identity`.** Parse each module with `ast`; resolve `ImportFrom.level` against the module's package, and treat `from .pkg import name` as importing `pkg.name` when that file exists.
- [ ] **Step 4: Run the module.** Expected: 3 tests pass.
- [ ] **Step 5: Commit** "Compute the grader's import closure and identity".

### Task 2: Grade a frozen package by its own bytes

**Files:**
- Modify: `src/generative_driver/benchmark_support/evaluator.py` (`reference_execute`, the given-package branch at about lines 95-109)
- Test: `tests/test_benchmark_family_calibration.py`, beside the toy sampled-sensor grading test at lines 212-298

**Interfaces:**
- Produces: `accept_frozen_package(package: Path, expected_sha256: str) -> str` in `evaluator.py`. It returns the digest and raises `ValueError('Frozen package changed before final episode')` when the digest differs. It never calls `emit_check` and never compares the bundled runtime with this checkout's.

- [ ] **Step 1: Write the failing test** `test_package_with_a_different_bundled_runtime_is_graded`. Reuse that test's `Device`, `Session`, pin, truth and capabilities; emit a package with `probe_run` and `emit_package` as it does at lines 272-277; append `'\n# another runtime\n'` to the package's `interface_runtime/engine.py`; update that file's hash in `manifest.json['files']` and rewrite `manifest.sha256` so the package is internally consistent. Then:

```python
graded = reference_execute(pin, truth, toy_sample_model(), caps, renode=Path('/toy'), image=Path('/toy'),
    output_dir=out / 'other-runtime', package=package, expected_package_sha256=digest(package))
self.assertEqual(graded['grade']['verdict'], 'passed')
```

- [ ] **Step 2: Run it.** Expected: FAIL with `ValueError: Reference package integrity failed`, because `emit_check` reports `runtime_current` false.
- [ ] **Step 3: Replace the `emit_check` call and its `runtime_current` requirement** in the given-package branch with `accept_frozen_package(package, expected_package_sha256)`. Leave the package-emission branch for Task 5.
- [ ] **Step 4: Run the module.** Expected: all pass, including the existing assertion that a package changed after freezing raises `Frozen package` without device access.
- [ ] **Step 5: Commit** "Grade frozen packages with their bundled runtime".

### Task 3: Grading records carry the run's protocol

**Files:**
- Modify: `src/generative_driver/benchmark_support/evaluator.py` (`record` at :197, `final_record` at :342, `grade` at :256, run-evidence payload at :520-536), `src/generative_driver/benchmark_support/evidence.py` (:114-124, :177-179, :327-328, :369-371)
- Test: `tests/test_evaluation.py`

**Interfaces:**
- Produces: `record(status, equivalent, checks, diff, frozen, capabilities_sha256=None, mission=None, method='fresh-final', *, protocol: str)` and `final_record(final, capabilities_sha256=None, mission=None, *, protocol: str)`. `grade` passes `result['progress']['protocol']`. Evidence uses the sealed payload's `protocol` for report projection and checks a report's protocol against the payload's, not against `configurator.PROTOCOL`.

- [ ] **Step 1: Write the failing test** `test_records_carry_the_runs_protocol`:

```python
made = record('graded', True, [{'id': 'a', 'passed': True}], [], 'e' * 64, protocol='learning/1')
self.assertEqual(made['protocol'], 'learning/1')
```

- [ ] **Step 2: Run it.** Expected: FAIL with `TypeError` for the unexpected `protocol` keyword.
- [ ] **Step 3: Add the keyword and thread it** through `final_record`, `grade`, the run-evidence payload and `evidence.py`, removing their `configurator.PROTOCOL` imports. `_evaluation_errors` in `configurator.py` keeps requiring its own `PROTOCOL` for records posted to its runs.
- [ ] **Step 4: Run `test_evaluation.py`, `test_benchmark_v2_evidence.py` and `test_assistive_reports.py`.** Expected: all pass.
- [ ] **Step 5: Commit** "Stamp grades with the run's own protocol".

### Task 4: Shared helpers move into closure modules

**Files:**
- Create: `src/generative_driver/benchmark_support/integrity.py`, `case_assets.py`, `labels.py`, `adapters.py`, `family_calibration.py`
- Modify: `configurator.py`, `benchmark.py`, `reporting.py`, `replay.py`; in `benchmark_support/`: `evaluator.py`, `evidence.py`, `registry.py`, `cases.py`, `emulated.py`, `emulated_actions.py`, `calibration.py`, `reference_calibration.py`, `snapshots.py`, `missions.py`, `rehosting.py`, `evaluator_cli.py`
- Test: `tests/test_grader_identity.py`

**Interfaces:**
- Produces, as closure modules the system imports from:
  - `integrity.py`: `digest(path)` and `class IntegrityError(ValueError)`, moved unchanged from `configurator.py:82` and `:388`; `tree_hash(root)`, moved from `reporting._tree_hash` (`reporting.py:161`).
  - `case_assets.py`: `STAGES` and `case_root()` from `benchmark.py`; `case_inputs` (was `emulated._inputs`, :89); `accepted_artifact` (was `emulated._artifact`, :106); `write_json` (was `cases._write`, :11); `mission_done` (`cases.py:63`); `reuse_mission` (was `emulated._mission`, :390).
  - `labels.py`: `equivalence_label` from `configurator.py`; `execution_label` from `replay.py`.
  - `calibration.validate_record`, moved from `reference_calibration.py:105`, so tq9-v2 admission stays in the closure.
- Produces, as system modules: `adapters.adapter_for(case_id)` with its loaders from `registry.py:103-141`; `family_calibration.calibrate`, moved from `calibration.py:255` (`evaluator_cli.py:25` imports it there).
- `configurator.py`, `reporting.py`, `benchmark.py` and `replay.py` re-import the moved names under their old names, so their callers stay unchanged.

- [ ] **Step 1: Write the failing test**:

```python
    def test_closure_excludes_shared_system_modules(self):
        shared = ('configurator', 'benchmark', 'reporting', 'replay', 'client', 'setup', 'benchmark_support.cases',
                  'benchmark_support.emulated', 'benchmark_support.legacy', 'benchmark_support.authoring')
        self.assertEqual([m for m in grader_closure() if m in shared], [])
```

- [ ] **Step 2: Run it.** Expected: FAIL listing those modules.
- [ ] **Step 3: Create `integrity.py`** and switch closure modules to it. Point the test patches of `reporting._tree_hash` (`tests/test_benchmark_snapshots.py:216`, `tests/test_assistive_reports.py:193`, `tests/test_reporting.py:179`) at `generative_driver.benchmark_support.integrity.tree_hash`.
- [ ] **Step 4: Create `case_assets.py` and `labels.py`** and switch closure modules to them.
- [ ] **Step 5: Create `adapters.py` and `family_calibration.py`**, and move `validate_record` into `calibration.py`.
- [ ] **Step 6: Run the test, then the full suite.** Expected: pass, with today's 4 skips.
- [ ] **Step 7: Commit** "Move the helpers the grader shares with the system into closure modules".

### Task 5: Reference building and recorded grading leave the grader

**Files:**
- Create: `src/generative_driver/benchmark_support/recorded_grading.py`
- Modify: `evaluator.py` (`reference_execute`, recorded-reuse grading at :418-480), `emulated_actions.py` (`model_invoker` at :145), `reference_calibration.py`, `upstream_qualification.py`, `harness.py` (grading dispatch)
- Test: `tests/test_grader_identity.py`; move the `package_final=False` cases of `tests/test_benchmark_family_calibration.py` (about :260) to `run_reference`

**Interfaces:**
- Produces:
  - `reference_calibration.run_reference(pin, truth, model, capabilities, *, renode, image, output_dir, phase, private_dir=None) -> dict`. It holds `reference_execute`'s diagnostic `model_invoker` path and its package emission through `emit_package`, and returns the same result shape, with `package_dir` and `package_sha256` when it emitted a package.
  - `model_invoker`, moved into `reference_calibration.py`.
  - `evaluator.reference_execute(pin, truth, capabilities, *, renode, image, output_dir, package, expected_package_sha256, private_dir=None, healthy_device_failures=False)` grades frozen packages only. The `model` and `phase` parameters go; Step 3 updates every caller, Task 2's test included.
  - `recorded_grading.grade(case_id, trial_dir, run_dir, result, *, password_file, protocol)` grades legacy tq9 and bme280. The harness calls it when `method_for(case_id) == 'recorded-reuse'`; legacy cases are not in the baseline.

- [ ] **Step 1: Write the failing tests**:

```python
    def test_closure_excludes_system_modules(self):
        closure = grader_closure()
        self.assertEqual([m for m in closure if any(m == s or m.startswith(s + '.') for s in SYSTEM_MODULES)], [])

    def test_system_change_keeps_identity(self):
        root = self.copy_package(); before = grader_identity(root)['sha256']
        for name in ('configurator.py', 'toolkit.py', 'benchmark.py', 'benchmark_support/emulated.py',
                     'benchmark_support/harness.py', 'benchmark_support/reference_calibration.py'):
            path = root / name; path.write_text(path.read_text(encoding='utf-8') + '\n# changed\n', encoding='utf-8')
        self.assertEqual(grader_identity(root)['sha256'], before)
```

- [ ] **Step 2: Run them.** Expected: FAIL naming `toolkit`, `_toolchain`, `interface_runtime` and `benchmark_support.physical`.
- [ ] **Step 3: Move the emission and diagnostic branches into `run_reference`.** Calibration runners call `run_reference` for diagnostic runs and package emission, then `reference_execute` for final grading.
- [ ] **Step 4: Move recorded-reuse grading into `recorded_grading.py`** and dispatch on `method_for` in the harness.
- [ ] **Step 5: Run the tests.** Expected: pass. If the closure still names a system module, apply the same rule: a helper the grader needs moves into a closure module that the system imports, and system behavior moves out of the closure.
- [ ] **Step 6: Run the full suite.** Expected: pass, with 4 skips.
- [ ] **Step 7: Commit** "Keep reference building and legacy grading out of the grader".

### Task 6: Admission, revisions and regrade check the grader identity

**Files:**
- Modify: `calibration.py` (`validate_calibration`, :60-110), `snapshots.py` (`execution_provenance`, :80-94), `evidence.py` (`_installed_evaluator`, :316-319), `reference_calibration.py` (records `evaluator_identity` at :149 and :299), `upstream_qualification.py` (:39, :139-140). Remove `calibration_identity`, `GRADING_TOOLCHAIN` and `CALIBRATION_ANALYSIS`.
- Docs: `docs/benchmark-case-authoring.md` (qualification identity), `CONTEXT.md` (Availability / admission: "against the current grader identity"), and `.scratch/benchmark-lifecycle/issues/01-scope-qualification-closure.md` (an `## Answer` linking this plan, then `Status: resolved`).
- Test: `tests/test_benchmark_family_calibration.py` (`AdmissionTests.fixture`, :42), `tests/test_benchmark_snapshots.py`, `tests/test_benchmark_v2_evidence.py`; rewrite `tests/test_benchmark_tq9_v2.py:1663` and `:1671` against `grader_identity`.

**Interfaces:**
- Consumes: `grader_identity()` from Task 1.
- Produces:
  - Admission requires `record['evaluator_identity'] == grader_identity()`. A run's `runtime_sha256` is provenance only: it must be a sha256, not the live runtime's digest.
  - `execution_provenance(...)['evaluator_revision'] == grader_identity()['sha256']`, while `toolchain_revision` and `skills_revision` keep hashing the system.
  - Regrade compares a sealed `evaluator_revision` with `grader_identity()['sha256']`.

- [ ] **Step 1: Write the failing tests**:

```python
    def test_admission_accepts_grader_identity_with_any_reference_runtime(self):
        manifest, record = self.fixture()
        record['evaluator_identity'] = grader_identity()
        for run in record['runs']: run['runtime_sha256'] = 'f' * 64
        self.assertEqual(validate_calibration(manifest, record), [])

    def test_admission_refuses_the_previous_identity_schema(self):
        manifest, record = self.fixture()
        record['evaluator_identity'] = {'schema': 'benchmark-evaluator-identity/2', 'files': {}, 'dependencies': {}, 'sha256': '0' * 64}
        self.assertIn('identity:evaluator_identity', validate_calibration(manifest, record))

    def test_evaluator_revision_is_the_grader_identity(self):  # test_benchmark_snapshots.py
        self.assertEqual(execution_provenance({})['evaluator_revision'], grader_identity()['sha256'])
```

In `test_benchmark_v2_evidence.py`, add `test_regrade_follows_the_grader_not_the_system`. Seal with `SealedEvidenceFixture` (:53). Patching `integrity.tree_hash` to change only the toolchain revision still regrades. Patching `evidence.grader_identity` to return another `sha256` is refused with the existing evaluator-mismatch message.

- [ ] **Step 2: Run them.** Expected: FAIL with `identity:evaluator_identity`, `run evidence:<id>` and unequal revisions.
- [ ] **Step 3: Implement the three checks** with `grader_identity`, and delete `calibration_identity` and its constants.
- [ ] **Step 4: Run these modules, then the full suite.** Expected: pass, with 4 skips.
- [ ] **Step 5: Update the three docs, then commit** "Admit and compare against the grader identity".

Every admitted case needs requalification after this plan lands; the anchors' requalification is a runbook step (plan 5).
