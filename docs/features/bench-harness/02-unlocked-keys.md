# 02. unlocked-keys

## Intent

Decision 17 needs the answer vault unmounted while workers run, but today's code goes back to the password file at 14 places and unlocks a trial's answer key on disk at least twice: at admission and again at grading. A suite couldn't survive the unmount. This Feature makes each process read a password file once and hold it in an object that can only seal and unlock, and makes a trial unlock its answer key once and hand it to the grader. Feature 05 builds the vault gate on this.

Grades, check ids, the sealed envelope format and what a password can open don't change. Every grader identity moves once, which costs nothing because nothing is admitted on main. This guards against mistakes in our own code. It isn't isolation: a worker runs as the same user, so it could still read the harness's memory or an unmounted-too-late file. Docs must say so.

## Behaviours

**The password object**

- **02-B1** — `truth.read_password(path)` reads the file as UTF-8 on every platform, strips it and returns an `EvaluatorPassword`. An empty file raises `ValueError` ("A nonempty evaluator password is required"). A file that isn't UTF-8 raises `ValueError` quoting nothing from the file. An unreadable file raises its `OSError`.
- **02-B2** — An `EvaluatorPassword`'s `repr`, `str` and `format` don't contain the value. `json.dumps`, `pickle.dumps` and `copy.deepcopy` of it, alone or inside a dict, raise `TypeError`.
- **02-B3** — `password.seal(evidence, path)` returns the file's SHA-256, and `password.unlock(path, expected_sha256)` returns the evidence. A wrong password or wrong hash raises `ValueError` as today. An envelope built in the test with `cryptography` directly, following today's format, unlocks, and a file `seal` writes has the same `generative-driver-groundtruth/1` fields, so shipped ciphertexts still open.
- **02-B4** — The whole package reads a password file only through `read_password`, with no exemptions. An `ast` test over `src/` fails on any call of `read_text`, `read_bytes` or `open` whose expression names a password; on any `EvaluatorPassword(...)` construction outside `truth.py`; and on any use of `truth.seal` or `truth.unlock`, which no longer exist as module functions.

**Reading once**

- **02-B5** — `run_suite` reads every supplied password file once, before it first contacts the configurator. A suite whose password files are all deleted after that still provides, grades and seals each trial.
- **02-B6** — An unreadable or empty supplied file ends `run_suite` with no configurator call: `suite.json` says `failed` and `accepted: false`, `launch_suite` returns `ok: false` with the reason, and once the file is fixed the same `request_id` starts afresh. The reason is `The evaluator password file of <case> cannot be read: <error class>`, with no path and no text from the error.
- **02-B7** — A case with no supplied password file still fails only when a slot needs its password, with today's message. A child whose verdict is recorded, or whose effect is uncertain, settles without one.
- **02-B8** — `run_trial` reads its `password_file` once, before it first contacts the configurator, unless its caller passed `password`. An unreadable or empty file leaves the trial `failed` with no device started, and `trial.json`'s reason has the form 02-B6 sets. A trial given no password fails as today, naming the password as required.
- **02-B9** — `score`, `report_suite`, `check_corpus`, `manage`, `prepare_execution`, `exercise_execution`, `assemble_release`, `family_calibration.calibrate`, `benchmark set qualify` and `trial-resume` each read a given file at most once per run, counted with a spy on `read_password`: `report_suite` once per case across all its trials, and `manage rekey` once each for the old and new files.
- **02-B10** — `check_corpus` reports the same statuses as today: `missing-credential` for no entry, `qualification-failed` for an unreadable or wrong password, `qualified` with `evidence_sha256` otherwise.

**Unlocking once**

- **02-B11** — A trial that runs to a grade decrypts its answer key exactly once, counted with a spy on `EvaluatorPassword.unlock` filtered to the truth path, for a `fresh-final` case (`tq9-v2`) and a `recorded-reuse` case (`tq9`). The evaluation token and draft still unlock as they do today.
- **02-B12** — For a case whose truth lives outside the package, and while the harness that admitted the trial follows its run, deleting or replacing the truth file or the password file, or replacing the truth file with a symlink out of the root, doesn't stop the run and changes neither its grade nor whether it seals. So a worker can't turn a failing run into a fault by changing the truth file. The test makes the change in the scripted `acquire` worker, with real `_prepare` and `_check`, on `tq9-v2` and `tq9`, in three variants per case: both files deleted, both replaced, and the truth file swapped for a symlink out of the root (skipped where the OS refuses one). `reference_execute` may stay patched. Each variant is compared with one undisturbed trial of its case. The run reaches `completed` with all six accepted handoffs, the trial is `evaluated` and sealed, its equivalence equals an undisturbed trial's apart from `evidence_sha256`, and the unlocked evidence has `evaluation_error` `None` and a non-null `final`.
  - **Limits, unchanged by this Feature.** A truth file inside the package is part of `toolchain_revision`, so changing a shipped one during the stages stops the run at the next stage as integrity, as changing any package file does. A `case.json` edited outside the package during the stages is no longer seen until grading, where it gives an `integrity:` unscored verdict, which the pass rate counts as a fault; today's stage check made it a failure. Generated sets sit in the vault, which Feature 05 unmounts during runs, so no worker can reach theirs. `resume`, `reattach` and a later slot of the same case open the file again and fail on a changed one, as 02-B17, 02-B18 and 02-B22 and today's handling set: a later slot's failed `admit` ends the suite harness. Feature 05 reads generated sets into memory, and Feature 06 owns moving past a failed slot.
- **02-B13** — `evaluator.py` and `recorded_grading.py` hold no call of `case_truth`, `truth_for_case` or `admitted_truth`, by `ast`. They get the answer key only by calling `truth_source`.
- **02-B14** — `grade` doesn't call `truth_source` for a run with no driver, no capability mapping or an unreadable model, nor when the draft holds the final; `recorded_grading.grade` doesn't for no driver, an invalid mapping, no recorded reuse, or a held final. These are today's cases, in today's order.
- **02-B15** — An `UnlockedTruth` whose `sha256` isn't the run pin's `truth_sha256` isn't graded: the trial's verdict is unscored with reason `integrity: …`, and the trial seals as other integrity failures do.
- **02-B16** — `UnlockedTruth`'s `repr` shows no answer values, and `json.dumps` of it raises `TypeError`. No configurator call, no file under the configurator home, and no trial or suite record holds a password value, a password path or a truth sentinel, checked with a recording `call` on a scripted suite that succeeds and on the failures of 02-B6 and 02-B8.
- **02-B17** — `resume` takes the password object and decrypts the answer key only if it grades, at most once: never when the verdict is recorded, the final is held, or the run was stopped without a pending grade. A password that doesn't unlock the trial's token is refused before anything else, as today.
- **02-B18** — `reattach` decrypts the answer key once, in its `admit`, and the reattached run is graded from that truth.

**Admission**

- **02-B19** — `admitted_truth(pin, password)` returns `(UnlockedTruth or None, admission)` and accepts and refuses what `require_calibration` does today, in the same order: a changed pin, or a v2 pin without passed calibration, raises before any decrypting, and a schema-1 pin returns `(None, {'ok': True})` without decrypting. `require_calibration(pin, password)` returns just the admission.
- **02-B20** — `admit` returns `(provider, admission, truth)`. It decrypts once for every kind of case, and the provider still receives only the recipe. A bound-device case authenticates the password as it does today and keeps the truth for its grade.
- **02-B22** — Neither grading nor `adapter_for` opens the truth file. Grading still checks the pin: a changed manifest or a changed pinned truth hash raises `IntegrityError`, and an image or asset that no longer matches fails as today. `adapter_for` holds no pin, and raises only what `load_definition` raises with `check_truth=False`. `pin_case` by default still opens the file, so `admit`, a configurator `start` and a suite resume refuse a missing or replaced truth file with today's errors.

**Generated sets**

- **02-B21** — `seal_device`, `open_device`, `seal_qualification`, `admitted` and `qualify` take an `EvaluatorPassword`. A plain string raises `AttributeError` or `TypeError` and writes no file.

## Interface and seams

**`benchmark_support/truth.py`** is the only way to reach a password or the cipher.

```python
def read_password(path) -> EvaluatorPassword
class EvaluatorPassword:          # value held privately; no accessor returns it
    def __init__(self, value: str)     # raises ValueError when empty; tests build fixtures with it
    def seal(self, evidence, path) -> str
    def unlock(self, path, expected_sha256)
@dataclass(frozen=True, repr=False)
class UnlockedTruth:
    payload: dict    # hydrated by the case's family
    sha256: str      # the manifest's truth hash it was unlocked under
```

Module-level `truth.seal` and `truth.unlock` go. `_key` and the envelope stay as they are. `UnlockedTruth` lives here so nothing new is imported by the device provider, which still imports nothing that decrypts.

**Where a path may still appear.** A path is read at a process or command entry and goes no further. These take a path and read it: `run_suite`, `run_trial` (each from its request), `benchmark.score`, `suite_reporting.report_suite`, `preflight.check_corpus`, `evaluator_cli.manage`, `upstream_build.prepare_execution`, `rehosting.exercise_execution`, `asset_build.assemble_release`, `family_calibration.calibrate` (from its sibling `<case>.password`), and `main` in `benchmark.py` for `set` and `trial-resume` and in `harness.py` for `--resume`. `launch_suite`, `harness.launch` and suite options still carry paths to the detached harness on its stdin, unchanged. Password paths stay out of the configurator, its records and every command line.

**Everything else takes the object, named `password`:** `evaluation_token`, `admit`, `_follow_run`, `finish`, `conclude`, `resume`, `reattach`, `needs_grading`, `grade`, `_draft`, `evaluator.seal`, `recorded_grading.grade`, `seal_run_evidence_v2`, `seal_run_evidence`, `regrade_v2`, `regrade_v3`, the adapters' `score`, and the generated-set functions in 02-B21. `truth_for_case(case_root, manifest, password)` raises its "required" `ValueError` when `password` is `None`. The native `calibrate` functions (`reference_calibration`, `upstream_qualification`, `authoring`) read `options['evaluator_password']`, and the option filters in `reporting.py:166`, `snapshots.py:42` and `device_provider.py:26` also drop that key. `run_trial(trial_dir, request, *, password=None, ...)` gains the keyword, which `_suite_trial` uses.

**Moves.** `case_truth` moves from `evaluator.py` to `registry.py`, keeps its manifest choice, and is now `case_truth(case_id, run_dir, password) -> UnlockedTruth`. `device_recipe` is now `device_recipe(case_id, truth)`. `admitted_truth` is new in `registry.py`, with `require_calibration` over it.

**The pin check at grading.** `load_definition`, `resolve_case` and `pin_case` gain a keyword `check_truth=True`. With `False`, `load_definition` still validates the manifest's truth hash and applies the lexical refusals of `resource_path` (`registry.py:121-125`) to the truth path, but skips the truth file's presence and hash check at `registry.py:218-222` and the `.resolve()` containment check at `:127-128`, so a symlink raises nothing. The lexical check is a helper of its own, and `resource_path` stays as it is, because images, assets and snapshots rely on its symlink refusal. With the default, `load_definition` does all of this as today. Two callers pass `False`: `evaluator.accepted_driver` at `evaluator.py:244`, and `adapters.adapter_for`, which reads only the case's `adapter_key` on every stage and package call. `benchmark.score` also goes through `adapter_for`, so a missing or changed truth file now fails it with the file's or `unlock`'s own error instead of `Case truth is unbuilt` or `Case truth hash mismatch`; grades don't change, because `unlock` still checks the pinned hash. `_verify_snapshot`'s `toolchain_revision`, the configurator's checks at `configurator.py:1909` and `:2150`, `snapshots.py:60`, and every other caller are unchanged.

**Getting the truth to the grader.** `conclude`, `finish`, `_follow_run`, `grade` and `recorded_grading.grade` take `truth_source`, a function of no arguments returning an `UnlockedTruth`. In a trial it returns what `admit` unlocked. In `resume` it calls `case_truth` with the run directory. A failure inside it reaches `conclude`'s existing handlers. The grader checks `sha256` against the pin (02-B15) in one helper in `evaluator.py`, which `recorded_grading` also uses. Decrypting is per trial, not per process: a suite harness runs every slot in one process, and each slot's `admit` decrypts again, while a `resume` or `reattach` later in that process reuses the suite's password but not an earlier truth. The docstrings of `run_trial` and `run_suite`, which accept a path in a failure's reason today, change to match 02-B6.

**Tests attach** at `read_password`, `EvaluatorPassword`, `run_suite`, `launch_suite`, `run_trial`, `resume`, `reattach`, `admit`, `admitted_truth`, `require_calibration`, `grade`, `recorded_grading.grade`, `check_corpus`, the commands of 02-B9, and an `ast` walk over `src/`. Tests that write fixtures use `EvaluatorPassword('literal')` or `read_password`. Existing tests change for the new signatures. Their expected values hold, with one intended exception: `test_benchmark_tq9_v2.py:932` expects `truth_callers` to show an unlock at admission and another at grading, and now shows only the first.

**Docs.**
- `docs/design.md:106-107`: the harness reads its password once and unlocks the answer key once, and neither is written down. Its "Limits of this protocol" paragraph gains that a worker running as the same user could read the harness's memory.
- `docs/benchmark-case-authoring.md:67`: the sealing example uses `read_password(...).seal(payload, path)`.
- `.scratch/benchmark-baseline/spec.md` decision 17: remove the "today it rereads password files" clause.

## Out of scope

- The vault gate: mounts, reachability checks and reading sets into memory (Feature 05).
- Generated devices' truth, pinning or trials (Feature 03). `generated_sets` only changes its password type.
- Removing `seal_run_evidence`. No source calls it, but two test files build older evidence with it, so it converts like the rest.
- Sending the password value on the harness's stdin, or erasing it from memory: Python can't guarantee erasure, and the path stays the private handoff.
- Any change to an envelope, contract, check id or grade. A password with a byte-order mark stays as it reads today.
- Any real password, vault or model run. Requalification is the user's, at pinning.

## Deviations

- A rewrap script cut the opening words of 14 docstrings in a80ebc7, restored after it — a slip inside this Feature's own diff, all read whole, no scope effect.
- `_refuse_unsafe_path` repeats the two lexical checks of `resource_path` — the Spec requires a helper of its own and `resource_path` unchanged.
- `test_02_B22_a_suite_resume...` has no RED, and the three B7 tests have RED only by mutation — they pin behaviour the Spec says stays as it is.
- `test_02_B16_an_unlocked_truth...` and `test_02_B9_the_command_reads_its_file_once` get RED from a temporary revert — process only; the saved excerpts fit.
- Legacy B11, legacy B15 and recorded B14 get RED from temporary mutations — process only; the saved excerpts fit.
- RED for new names is mostly `AttributeError`/`TypeError`, and two B17 REDs stay signature-only — a disclosure, not a scope change; two other B17 tests have REDs that fit.
- Other existing expected values changed with B11/B20 (`test_legacy_cases.py`, `test_evaluation.py` `unlocked[0][1]`) — the same removal of the second unlock as the Spec's named exception.
- "Truth unlocked" guards now patch `evaluator.answer_key` or pass `truth=` — they guard the same thing, now that a trial's truth comes from `admit`.
- Tests with fake password paths now write real temporary files — follows from reading at command entry.
- `seal_device`'s "no password" row and `''` in the wrong-password subtest are gone — B21 replaces that refusal, and `EvaluatorPassword('')` cannot be built.
- Names the Spec left open: `evaluator.answer_key`, `registry._refuse_unsafe_path`, `harness._read_supplied`, `_supplied_passwords`, `_UnreadablePassword` — the Spec asks for these helpers without naming them, and only `answer_key` is public.
- Test support added (`HarnessTrialFixture`, `LegacyTrialFixture`, `SealedTrialFixture.staged_trial`, `legacy_case`, `file_changes`, `symlinks_refused`, `PASSWORD_TEXT` and others) — test-only, serving the seams the Spec agrees.
- `truth_source` is a required keyword on `finish` — the Spec lists `finish` among the functions that take it.
- `benchmark.score` reads a given file at entry, so an unreadable file raises even for a report naming no evidence — traces to "A path is read at a process or command entry"; nothing is unlocked and no grade changes.
- `check_corpus` reads before admission, so an unreadable file on a schema-1 pin is `qualification-failed` — 02-B10 states it; `admitted_truth` itself still admits schema-1 without decrypting.
- `check_corpus` and `run_suite` read a shared file once, and `report_suite` reads lazily per case — fits 02-B5 and 02-B9.
- `family_calibration.calibrate` gives `Evaluator password handle required` for an absent, empty or non-UTF-8 file — fits 02-B1's "quoting nothing"; for non-UTF-8 this replaces a decode error, which is harmless.
- `tests/test_unlocked_keys.py` and `tests/test_legacy_cases.py` hold added lines over 120 columns — no scope effect, neighbouring tests do the same; wrap them when these files are next touched.
- The refused-symlink skip was shown only with `Path.symlink_to` patched in a scratch run, and nothing ran on Windows — 02-B12's skip happens in the test's own thread, and the Reviewer reran it: `OK (skipped=2)`.
