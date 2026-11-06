# 07. trial-sealing

## Intent

A finished scoring trial's folders hold its driver, its transcripts and clues about how the device works. A later worker runs as the same user and could read them, and a rerun on the same firmware is the worst case. Decision 21 seals each concluded trial and leaves a stub, but its "moves into the answer vault" conflicts with decision 17, which keeps the vault closed while workers run. This Feature settles that: a trial is encrypted in place under the set's password, which the harness already holds, and the operator moves everything into the vault after the suite. It also puts trial folders behind one module.

This catches slips and casual browsing. It isn't isolation: a worker can still read the harness's memory or copy ciphertext, and the runs database stays readable, as the user chose. Development and hand-made suites are untouched. Grades don't change. Adding names to `grader_identity`'s system list moves every identity once, which costs nothing because nothing is admitted on main.

## Behaviours

**Which trials, and when**

- **07-B1** — Every concluded trial of a scoring suite is sealed once it has settled: finished, faulted, flagged, or never run. In `run_suite` that means a settlement of `advance`, `fault`, `flag` or `regrade`, or a no-run `fault` or `advance`. A slot `attached`, `unsettled`, waiting or blocked isn't sealed. Other suites' trials are never sealed, and their folders and tests stay as they are.
- **07-B2** — Before every worker starts, on the harness's start once `suite_resume` has answered and before its first reserve, and on its way out when it is attached to the suite, a sweep finds any concluded trial of its suite still readable, and seals it. So no worker starts beside a readable concluded trial, even after a crash between settling and sealing, or after a flagged trial's earlier seal was lost. Concluded means a slot `finished`, `faulted` or `flagged`, or a `first_attempt` whose `request_id` differs from the slot's current request, as the configurator reports. So a rerun's first attempt is swept, while the current request of an active slot, reserved or not, never is, and a regrade's restored plaintext is safe. The way-out sweep runs before the suite's final status, so a failure there is a stop. A configurator it can't reach makes a way-out sweep do nothing, and the next start handles it.
- **07-B3** — For a concluded trial the sweep decides from what is on disk, and never overwrites a seal. No seal and plaintext present: seal. A valid seal and plaintext: delete files whose hash matches, and put any changed or extra file in a further seal generation. A seal and no plaintext: done. A stub with no local seal and no plaintext: done, as after `store`. A member missing from disk isn't a disagreement, since that is a partial delete. No stub and no plaintext: stop the suite. A sealed part that won't unlock: stop the suite.
- **07-B4** — A sealing, verifying or deleting failure stops the suite for a resume, with a reason naming no path. A resume seals what is left before any worker starts.
- **07-B5** — A scoring suite's resume refuses without its password file, in one sentence, at `resume_suite` when the configurator answers, and in the harness between `ping` and `suite_resume` through `suite_result`. A new scoring suite refuses, after `ping` and before the gate, while another suite on the same set in that bench home has a trial still readable, concluded, abandoned or active: any folder that isn't a stub counts. A new worker on the same firmware beside it is the worst case. Its `suite_result` call happens only when such a folder exists, which amends Feature 05's B5 and keeps `['ping', 'suite_start']` otherwise.
- **07-B6** — Plaintext is never deleted under a seal the real password can't open. A sweep or `store` proves the password once at its top, before sealing anything: it passes if any token of the suite unlocks, or, in a harness that holds the set, by the qualification record it has already unlocked, and it stops the suite if tokens exist and none unlocks. A trial that has a run and a non-null `start_sent` then proves it on its own `evaluation-token.enc` on every pass that would delete, including after a crash, and a missing token there is damage, which stops the suite with nothing deleted. A folder made from a slot's row has a null `start_sent` and no token of its own, so it relies on the proof at the top: when neither a token nor a held set can prove the password, the suite stops with nothing deleted. A resume given the set root proves it where the suite can still resume; otherwise `store` keeps refusing and it is a hand repair, which the docs state as a limit. A no-run trial holds nothing worth losing, so in a suite with no token at all it is sealed without proof.

**What a seal holds**

- **07-B7** — A seal holds the run folder except `benchmark/execution.json` and `benchmark/inputs/`; the trial folder except its `*.enc` files, lock files and a writer's temporary files; the original `trial.json`, as a member; and the temporary interpret workspaces of that run. All of that is then deleted. The two kept paths are public to that device's worker, and `_verify_suite` and `report_run` need them. A no-run trial gets a seal with no run folder, and a run with no trial folder gets one made from the slot's row.
- **07-B8** — `private/` is removed first, not sealed. The order is: write the seal and sync it to disk, unlock it and compare every member with a per-file hash list taken from the bytes actually tarred, write the stub and sync it, tell the configurator, delete the plaintext, and check it is gone. A crash at any step leaves a state 07-B3 resolves, and no plaintext is deleted before its seal has verified. A file still growing, such as a leftover emulator's log, can't fail verification.
- **07-B9** — Symlinks aren't followed, and FIFOs and sockets aren't read; each is listed in the seal as skipped. Modes are fixed before reading as well as before deleting, so a worker's `chmod 000` can't stop a seal. Extraction accepts only plain files and folders with relative paths inside the target. Deleting retries as `_private` does, without `ignore_errors`.
- **07-B10** — A run's workspaces come from its `stage.assigned` events with stage `interpret`, read from the configurator. Only a path shaped `…/gd-candidate-*/attempt<N>` outside the run is accepted, and its parent goes too; any other shape stops the suite and deletes nothing, so a worker can't aim the harness at another folder.

**The stub**

- **07-B11** — `trial.json` becomes the stub, with status `sealed`. It holds only: `schema`, `trial_id`, `request_id`, `run_id`, `case`, `start_sent`, a non-null `finished`, and each sealed file's name, hash and time. It holds no level or framing, verdict, outcome, check id, reason, mission result, tokens, tool counts, device id, image, mission or truth hash, set root, vault path, password path or home. `_trial_of`, `_adopt` and `_harness_exited` work from it. The original paths are in `sealed-index.enc`.
- **07-B12** — A sealed run is marked in the configurator. `status` and `result` show `sealed: true`. `resume` and `attach_device` on it refuse with `ValueError`, before `_resume_device`, so a stray resume can't turn a fault into an integrity failure. The runs database keeps its rows, as a stated limit.

**Reading a seal back**

- **07-B13** — One function reads a seal back: into a private temporary folder, one trial at a time, removed afterwards, yielding a root with fixed `run/` and `trial/`, whose workspaces lie outside `run/`. The scorecard, `report_suite` and `--audit-path` use it on a scoring suite, and their figures for a sealed trial equal what they gave before it was sealed, checked on a finished, a flagged and a rerun trial. A trial still plaintext on a blocked suite opens the same way. A scoring report or scorecard with no password raises, rather than give `None` figures.
- **07-B14** — A trial due a regrade is restored to its original paths just before the regrade, in `_suite_trial`'s completed branch, keyed on the on-disk stub, and a repeat changes nothing. After it settles, 07-B3's rule applies: grading writes only to `private/`, `*.enc` and the member `trial.json`, so the old seal stays, `trial.json` goes in a further generation, the stub is rewritten and the plaintext is deleted. No worker runs meanwhile, since a suite has one active child. A restore that fails stops the suite.
- **07-B15** — `benchmark suite unseal <suite id> --trial <run id or trial id>` writes one sealed trial into a folder under the given vault folder, for a person checking a flagged command, and refuses any other location. It needs the password file.

**After the suite**

- **07-B16** — `benchmark suite store` runs the sweep, then moves only a suite's `sealed-*.enc` files under `<vault folder>/sealed-trials/<suite id>/`, verifying each copy by unlocking it before deleting the original. Stubs and other `*.enc` files stay. It accepts a `completed` suite, or any `cancelled` one with nothing stopping, and on a cancelled suite it seals and marks every readable trial, including an unsettled slot's, so cancel then store is how to abandon a suite. A later resume reaching such a slot stops at the failed restore, which fails safe. It refuses on a sweep failure, and never replaces a differing vault file. A failure part way leaves every seal in at least one place, and a repeat finishes the job. Read-back finds a seal in either place, given the vault folder.
- **07-B17** — A scoring suite's scorecard needs the password file and the set root, and takes the vault folder if `store` has run. It refuses while `suite_status` says `queued` or `running`, or `stopping` is true. It joins level and framing in memory by device id from `load_set`, so its groups use them, and it saves neither anywhere. It stays aggregate-only.
- **07-B18** — A scoring suite's `benchmark suite report` needs the password file, the set root and a vault folder, refuses unless the set root resolves under the vault folder and the output lies under it, and refuses in the same states as 07-B17. Comparison still works, since only its counts leave. `suite_result`, `suite_events`, `suite_status` and `suite.json` stay as they are, and the docs say they can show per-trial data.

**One module**

- **07-B19** — `benchmark_support/trial_store.py` is the only code that knows the bench-home layout of suites and trials. An `ast` test over `src/`, except `trial_store`, fails on `'suites'`, `'trials'` or `'-bench'` as a `/` operand, in `joinpath` or `Path()` arguments, or inside a glob pattern, with one allowlisted line for the export layout at `suite_reporting.py:791`. `harness`, `scorecard`, `suite_reporting`, `suites` and the configurator's readers of the bench home call it. `harness.bench_home` stays as an alias for tests. Writes of `trial.json` and `suite.json` stay in `harness`. Closure modules keep taking paths as arguments, and the configurator keeps writing active runs.

## Interface and seams

**`benchmark_support/trial_store.py`**, new, outside the grader closure. A harness's folder and a suite differ: each launch and resume makes a new `suites/suite-run-<uuid>`, so a suite's trials can span several. `suite_dir`'s key is that harness key. `sweep`, `store` and `opened` take the suite id, scan the whole bench home, and select by the prefix `suite:<id>:trial:` of `trial.json`'s `request_id`. Several folders can share one request id, after a `launching` slot is reserved again with no run, and all of them are sealed.

```python
def bench_home(home) -> Path
def suite_dir(home, key) -> Path
def trial_dirs(home, suite_id=None) -> list[Path]
def find_trial(home, suite_id, key) -> Path | None
def read_trial(trial) -> dict
def seal_trial(trial, run_dir, workspaces, password, call, token) -> dict      # returns the stub: 07-B6 to 07-B11
def sweep(home, suite_id, password, call, token=None, *, proven=False) -> None                       # 07-B2, 07-B3
def restore(trial, password) -> None                                           # 07-B14
@contextmanager
def opened(home, suite_id, trial, password, *, vault=None) -> Iterator[Path]   # 07-B13
def store(home, suite_id, vault, password, call) -> None                       # 07-B16
```

**The seal's format.** A tar.gz is split into parts of at most 64 MiB, and each is passed as base64 in one JSON field to the existing `password.seal`, beside the trial. `sealed-index.enc` holds the per-file hash list, the original paths, what was skipped and the parts' hashes. Files are numbered by a generation unique within the trial, such as `sealed-<generation>-<n>.enc` and `sealed-<generation>-index.enc`, and the stub records the generations; `opened` and `restore` take each member's latest generation. `truth.py` isn't edited. After each seal, open the file `r+b` and `fsync`, with `F_FULLFSYNC` on macOS where available; sync the folder on POSIX only, and the stub the same way. An unreadable `trial.json` beside a seal stops the suite. Memory stays bounded by the part size.

**The configurator** gains a call to mark a run sealed, taking the suite token, ordinal and `run_id`, accepting the slot's `run_id` or `first_attempt.run_id`, refusing only a non-scoring suite, or a bad token while a harness is attached, and repeatable, since the delete branch sends it again. With no harness attached it takes no token: it only restricts a run, so this gives nothing away, and `store` uses it, with `sweep`'s `token=None`. `status`, `result`, `resume` and `attach_device` read the mark. `_verify_suite` is unchanged: it loops over every slot, but reads only the kept paths.

**`harness.py`.** Both sweep sites pass `proven=held is not None`, since `hold_set` has already decrypted the record under that password. `run_suite` seals after a settlement 07-B1 names, finding the folder through `trial_store` by request or run id, in both its normal path and its `recorded` branch, and calls `sweep` where 07-B2 says. `_seal_flagged` still writes the evidence seal; a folder seal never depends on it, since `evaluator.seal` can raise on purpose. `sealing_failed` on a flagged trial is resolved by the sweep. A no-run slot's request id comes from its ordinal and `attempt`.

**Reports.** `trial_metrics` takes a `run_dir` argument, which is the opened copy on a scoring suite. `_inside`, `_kept` and `_replacements` already resolve by position. `report_run` needs no branch: `state.json` only feeds `case_state`, which the public report drops. The refusals of 07-B18 sit in the public `report_suite` entry, with an internal core that the scorecard calls with its temporary output. A new `password` argument on both stays separate from `password_files`, which switches on regrading. New CLI: `--password-file`, `--set-root` and `--vault-folder` on `suite report` and `scorecard`, and the `suite store` and `suite unseal` commands. These paths never reach the configurator.

**Identity.** `SYSTEM_MODULES` gains `benchmark_support.trial_store` and `benchmark_support.generated_trials`, so a grading module importing either fails the boundary test. `tests/test_grader_identity.py:197` gains the two names.

**Tests** stay cheap: the Done limit is 540 s and a full run takes about 360 s, and each concluded trial costs at least four key derivations. Measure first. `ScoringFixture` may replace the key cost by patching a private `trial_store` helper, except in the end-to-end tests and the new module; this is an allowed exception to STANDARDS' limit on mocking, because the real path is covered there. Seal, stub, crash-step, symlink, FIFO, delete-failure, wrong-password and workspace-shape tests live in a new module on plain temp folders, with no `qualified_set`. Add at most six end-to-end tests inside `tests/test_slot_policy_flags.py`, which already pays for its set. The vault folder is an ordinary temp folder. Tests that read trial files after a scoring suite use `opened`, including `tests/test_generated_trials.py:1614-1630`, `tests/test_slot_policy.py:634` and `tests/test_slot_policy_flags.py:230,270,311,337,452`; `ReportFixture.report` changes in one place, as do direct `suite_scorecard` calls at `tests/test_slot_policy_flags.py:43,73` and `tests/test_generated_trials.py:1623`. Tests attach at `trial_store`'s functions, `run_suite`, `report_suite`, the scorecard, the two commands and a recording `call`.

**Docs.**
- `.scratch/benchmark-baseline/spec.md` decision 21 and line 59, and `docs/adr/0006-*`: sealed in place under the set's password, moved to the vault after the suite.
- `docs/design.md` "Limits of this protocol": the runs database stays readable, as do `suite_result`, `suite_events`, `suite_status` and `suite.json`; the audit doesn't flag reading another run's folder; workspaces outside these paths aren't covered.
- `docs/benchmark-case-authoring.md`: `suite store`, `suite unseal`, what a scoring scorecard and report need, that cancel then store is how to abandon a suite, and that `gd report <run>` on a sealed run writes files that the next sweep seals.

## Out of scope

- Sealing the runs database's rows, and aggregate-only views of `suite_result`, `suite_events`, `suite_status` and `suite.json`. The user chose to leave these as stated limits.
- Further forbidden locations in the audit, which the user declined in Feature 06.
- Opening or closing a vault from code. The vault folder is only a path.
- Capping how much a trial writes (Feature 08). Bounded parts keep sealing's memory small, whatever a trial wrote.
- Development and hand-made suites.
- Windows delete behaviour with a leftover emulator holding a log open: a failed delete stops the suite, which fails safe, though it needs a person on Windows. Real Windows behaviour is unverified, and so are real generated-device sizes.
- Any real vault, password, Renode, set or model run.

## Deviations

- `trial_store` has public names beyond the Spec's list (`new_trial_dir`, `solo_trial_dir`, `trials_under`, `readable_suites`, `run_workspaces`, `SealError`, `Unreachable`, `SEALED`, `TOKEN`) — they serve 07-B19's layout rule and the harness's needs, with no scope change.
- `bench_home(home=None)` keeps its optional argument — this makes `harness.bench_home` the plain alias 07-B19 asks for.
- `_trial_of` takes a list of folders — the glob has to move into `trial_store` under 07-B19; behaviour is unchanged.
- "Before every worker starts" is a sweep at the top of `run_suite`'s loop — no trial can conclude between that sweep and the worker, and it also covers the reservation that completes the suite.
- The call is named `suite_sealed`, the column `runs.sealed`, and marking adds no event — the Spec leaves the names open, and no new event keeps 07-B13's figures equal.
- `suite_start` and `suite_resume` replies carry `scoring_set`, stripped from `suite.json` — the registration is no secret, and the harness needs it before its first reserve without reading the live table of sets.
- A resume whose `suite_result` was lost stops right after attaching, in the 07-B5 sentence — it fails closed and starts no worker.
- With a set root and no password file, a scoring resume gets 07-B5's sentence, not Feature 05's B13 reason — the Spec asks for one sentence.
- `_refuse_beside_readable` counts a suite the configurator can't name as the same set — it fails closed, in line with 07-B5's worst case.
- A failed seal stays stopped, but the way-out sweep may retry it — the failure is kept, the suite still blocks, and nothing is deleted before verifying.
- The detach reason on a sealing failure is the closed text, with no `see .../suite.json` — 07-B4 requires no path.
- The top proof runs only when the sweep or `store` has something to seal — it keeps 07-B3's "a seal and no plaintext: done". `_move_to_vault` still unlocks each copy before its original goes, and a copy left in the vault is valid ciphertext that a repeat reuses.
- "No stub and no plaintext" stops while the sweep plans, before any row is sealed — it serves "before sealing anything". The cost: one such row leaves every other concluded trial of that suite readable until a person repairs it, with no worker of that suite starting meanwhile.
- Only a trial with a run and a start proves on its own token — 07-B6's own wording; a no-run trial's token still counts towards the top proof.
- `_seal_settled` has no top proof and takes no `proven` keyword — a folder with a run and a start proves on its own token, a folder with a run and a null `start_sent` goes to the sweep, which proves or stops, and a no-run trial is 07-B6's "nothing worth losing".
- `seal_trial` called directly on a folder with a run and a null `start_sent` does no proof of its own — its only two callers are `_sweep` after its top proof and `_seal_settled` behind its guard.
- `opened` needs a password for plaintext too — fits 07-B13's "raises with no password"; a wrong one can still open plaintext, but only for the operator.
- `_is_run_of_suite` reads the runs database read-only — the Spec's `opened` has no `call`, `scorecard` already reads this database directly, and a missing database or a failed read refuses.
- `report_suite` expands `~` in `output` for every suite — one normalised path is the fix for the check and the export disagreeing.
- `harness.TOKEN` imports `trial_store.TOKEN` — one name in one place.
- A stub with no local seal but late plaintext seals it in a further generation — this makes the documented `gd report <run>` after `store` resolve, and no seal is overwritten.
- A stopped, unsettled slot found as a stub restores before its audit — otherwise it would settle unaudited; on a stored slot it is 07-B16's failed restore.
- `restore` writes the original `trial.json` bytes itself, an exception to 07-B19 — byte identity avoids a needless generation, and it is one contained write.
- A file that shrinks while being tarred fails the seal and stops the suite — it fails safe, and a resume seals it.
- An unfinished generation's parts stay beside the trial and are moved by `store` — follows "never overwrites a seal", and read-back ignores them.
- `store` refuses an absent vault folder, a completed suite still `stopping`, and a non-scoring suite — narrower acceptance that keeps a closed mount point unfilled, with no harness racing it.
- `suite unseal` has a required `--output`, new or empty, under the vault folder, and its body is in `benchmark.py` — realises 07-B15's location rule.
- `refuse_unsettled_scoring` is in `suites.py`, and the report's refusals fire inside `_export_suite` through `check` — the entry still supplies the refusals, with no extra call.
- `ScoringFixture`'s stand-in also patches `_sync_file` — the Spec's cost exception; the real path runs in `tests/test_trial_sealing.py` and the five end-to-end tests.
- Existing tests changed in `tests/test_scorecard.py`, `tests/test_vault_gate.py` and `tests/test_slot_policy.py`, and the round-two test changes — each follows from 07-B5, 07-B6 or 07-B17, and none weakens what the test checks.
- `.scratch/benchmark-baseline/spec.md` changed at decision 21 and lines 22 and 102, not "line 59" — line 59 is about names; the lines that speak of sealing were changed.
- Tests were written in groups, with report, scorecard and CLI code before their tests, resting on mutation REDs — it breaks the test-first rule in `STANDARDS.md` and `AGENTS.md`, which the user has been told; scope is unchanged, and the excerpts sampled fail for the Behaviour's reason.
