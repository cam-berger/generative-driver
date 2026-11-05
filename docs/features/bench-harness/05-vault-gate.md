# 05. vault-gate

## Intent

Decision 17 keeps the answer vault closed while workers run, but today a suite reads the set from disk at each slot, so it needs the vault open throughout. This Feature makes a suite read what it needs into memory, tell the operator it is ready, and start trials only once the set's folder is out of reach. The operator closes the vault by hand. The harness only watches.

This catches a vault left open by accident, and removes the answers from where a careless worker could find them. It isn't isolation: workers run as the same user, so they could read the harness's memory or a copy kept elsewhere, and the check sees only the path it is given. Grades don't change. Editing `truth.py` moves every grader identity once, which costs nothing because nothing is admitted on main. Hand-made cases have no set folder and aren't gated.

## Behaviours

**Reading into memory**

- **05-B1** — `run_suite` on a new set suite reads the manifest, the qualification record and each entry's two sealed device files once, checks each sealed file against its manifest hash, and holds them in memory, before any configurator contact. A damaged or missing file ends the suite in the not-admitted form, and an `OSError` is named by class alone.
- **05-B2** — After that, the suite reads no file of the set. A fresh suite over two fixture devices whose root is removed once it is waiting provides, runs, grades and seals both trials. Two resume harnesses, each with the root removed once it is waiting, do the same for a completed ungraded slot and for a stopped one.
- **05-B3** — The qualification record is decrypted once, at the start. Each device's sealed files stay sealed in memory until its slot: `admit` decrypts `inputs` and `answer` once each, and a grading `resume` decrypts `answer` alone, once, checking it against `answer_key_sha256`, which the registered manifest binds, and not against the mission. Nothing decrypted is kept past its trial.
- **05-B4** — What `admitted` checks and refuses is unchanged, read from what is held. Each slot's `admit` still refuses a reason: the registered hash is compared with `set_registrations()` and the grader identity is recomputed there. The path forms of `load_set`, `open_device` and `admitted` stay for qualification.

**The gate**

- **05-B5** — A new suite's order is: hold the set, run the admission checks and 05-B10, one `ping`, then the gate. The gate checks the set's folder once, and if it is reachable writes `suite.json` with status `awaiting-unmount` and `accepted: false`, and makes no further configurator call until the folder is unreachable. Then it starts the suite as today. No suite is frozen and no budget runs while it waits. The checks that end a suite before the gate make no configurator call, not even `ping`.
- **05-B6** — For a manifest with `set`, `launch_suite` mints and claims a `request_id` when none is given. It returns as soon as the harness says `awaiting-unmount`: `{'ok': True, 'status': 'awaiting-unmount', 'set': <name>, 'request_id': ..., 'suite_dir': ..., 'duplicate': ...}`. It doesn't block. Repeating the same `request_id` follows the same harness, and returns the suite's id once it is accepted. A harness that ended before accepting leaves the request free to start afresh, as today. A suite without `set` is as today.
- **05-B7** — A folder still reachable after the wait limit, one hour by default, ends the harness: `suite.json` `failed`, `accepted: false`, reason `The vault holding set <name> was not closed in time`, with no path. The same `request_id` starts afresh.
- **05-B8** — A folder counts as unreachable when `os.stat` of `<root>/manifest.json` raises `FileNotFoundError` or `NotADirectoryError`, and `os.stat` of the root raises one of those or the root is an empty directory. Anything else keeps it reachable, including `PermissionError`. So a locked volume that still answers keeps the gate shut, and the wait limit ends it.
- **05-B9** — The harness checks again in `_suite_trial`, immediately before `run_trial` and before `reattach`, never before a grading `resume`. A folder reachable again sets `awaiting-unmount` and waits, with a fresh limit, and no worker starts meanwhile. Each poll calls `suite_reserve`. A reply with `ok: True` and a different or missing slot, or `stop` being set, ends the wait with no worker started: `run_suite` then skips `suite_settle`, clears `current` and returns to the top of its loop, so a cancelled suite doesn't stay stuck. A reply whose `ok` isn't true keeps waiting, so a brief configurator outage doesn't kill the harness. The suite's budget keeps running. When the limit passes, the harness ends with 05-B7's reason, blames no trial, and the suite blocks and can resume. A remount during a trial isn't caught, and this wait shows only in `suite.json`.
- **05-B10** — The harness refuses before the gate, in the not-admitted form, when its home, its bench home or the emulator executable lies under the set's root, on a new suite or a resume, or a replay candidate does on a new suite, both sides resolved. A suite could never run with the vault closed.
- **05-B11** — Every set suite is gated, scoring or development. A suite without `set` makes no such check and behaves as today, and its tests don't change.

**Resuming after a crash**

- **05-B12** — `resume_suite` on a set suite, with or without `set_root`, refuses with `ValueError` before any harness starts while the active child is `queued` or `running`, or `stopping` is true, saying to stop it or let it finish. It learns this from `suite_result`'s `experiment.comparison_identity.manifest.set`, `suite_status` and the child's status. A configurator it can't reach goes on to the harness, as today. A hand-made suite is as today. A slot that is `launching` with no attached child, which arises only after a configurator crash, isn't visible here.
- **05-B13** — A resume given `set_root` follows this order: read its password files, `ping`, `suite_result`, then refuse with `accepted: false` and the configurator's own text (`Only a stopped incomplete suite can resume`, `Previous suite work is still stopping`, or the uncertain-effect sentence) unless the suite is `blocked`, `failed` or `cancelled` with `stopping` false and its active child has no uncertain effect. Only then does it hold the set, run the variable's guard, `admitted_held` per entry and 05-B10, and compare `canonical_digest(device_pin(held.manifest, id, held.evidence_sha256))` with each entry's frozen `pin_sha256`. These refuse in the not-admitted form, so a stale or requalified record is caught with the vault still open. No password file gives the reason `no evaluator password file was supplied`, and a wrong password `qualification unreadable: ValueError`, in that form and before `suite_resume`. An unreadable file has already ended the harness in Feature 02's form. Then it waits as 05-B5 says, and calls `suite_resume` once the folder is out of reach. Budget and `_verify_suite` refusals stay in `suite_resume`.
- **05-B14** — A resume's `awaiting-unmount` reply is `{'ok': True, 'suite_id': ..., 'suite_dir': ..., 'status': 'awaiting-unmount', 'set': ...}`, with no `request_id`, and an accepted resume's reply is as today. A resume takes no claim, so the operator follows it with `suite status`, not by repeating it.
- **05-B15** — A resume given no `set_root` doesn't gate. Slots with a recorded verdict or an uncertain effect settle without one, and a slot that needs its device fails, as Feature 03's B5 says.

**What the harness keeps to itself**

- **05-B16** — No configurator call, run spec, file under the configurator home, `suite.json`, `trial.json` or reason holds the set's root, on a successful scripted suite and on the failures of 05-B1, 05-B7 and 05-B10. `SetDevice` holds no path.

## Interface and seams

**`benchmark_support/generated_trials.py`**

```python
@dataclass(frozen=True, repr=False)
class HeldSet:
    registration: str
    manifest: dict              # with manifest_sha256
    record: dict                # the unlocked qualification record
    evidence_sha256: str
    sealed: dict                # device id -> {'inputs': bytes, 'answer': bytes}

@dataclass(frozen=True, repr=False)
class SetDevice:
    held: HeldSet               # replaces set_root
    device_id: str

def hold_set(set_root, registration, device_ids, password) -> HeldSet
def set_unreachable(set_root) -> bool           # 05-B8
```

`admit_device`, `answer_key` and `device_pin`'s callers take the held form. `qualification_digest` becomes the held `evidence_sha256`. `SetDevice` tests change with it. `hold_set` keeps `load_set`'s and `_inside`'s checks, and the reason strings pinned at `tests/test_generated_trials.py:519-534` stand.

**`truth.EvaluatorPassword`** gains `unlock_bytes(data, expected_sha256)`, and `unlock(path, ...)` reads the file and calls it. Each file keeps its own salt, so no derived key is cached; opening one device costs two key derivations, which is nothing beside a trial. No envelope changes.

**`generated_sets`** gains `open_held(manifest, device_id, sealed, password, *, answer=False)` beside `open_device`, sharing its checks and `validate_device`, and `admitted_held(held, device_id)` beside `admitted`, sharing its reasons through `qualification_reasons`. `held` needs no new import. `fixture_variable_reasons` stays where it is called.

**The wait.** A loop in `harness`, polling once a second, with the limit in a module constant, `UNMOUNT_WAIT = 3600`, which tests patch as they patch `UNREACHABLE` (`tests/test_suite_harness.py:735`). `_follow_suite` tests `accepted` and an exited harness before `awaiting-unmount`, and treats that status as a reply, so an MCP client isn't held for 900 s. The operator ends a wait by closing the vault, or by stopping the harness, whose pid is in `suite.json`; `_refused` then releases the request as today. The harness clears `current` only when 05-B9's limit passes or its wait ends with no worker started, so no trial is blamed; a real trial failure keeps today's `trial X failed` reason.

**Resume reads.** `suite_result` isn't among the calls that autostart the configurator, so `ping` goes first. The hash to compare is `comparison_identity.entry_pins[i]`'s, not `comparison_identity.manifest_sha256`, which is the suite manifest's. `public_pin` holds `set`, `device_id` and `pin_sha256`, and no `calibration`, which is why the comparison goes through `device_pin`'s digest.

**Tests** use real temp directories and remove, rename or empty them, with `qualified_set()` and `FixtureDevice`. They never touch a real volume or mount. Existing set suite tests that don't test the gate patch `generated_trials.set_unreachable` to return `True`, in-process with `patch.object` or, for a detached harness, in `HARNESS_FIXTURE` (`tests/test_generated_trials.py:1530`) and the inline fixture (`:548`). Because the gate checks before it writes the status, those runs never show `awaiting-unmount`, and `['ping', 'suite_start']` still holds. Gate tests remove the root after seeing the status. Tests that build `SetDevice(...)` from a root change. Feature 03's B5 tests of a lazy set read on resume change as 05-B13 says, including `tests/test_generated_trials.py:741-744`. Tests attach at `hold_set`, `set_unreachable`, `run_suite`, `launch_suite`, `resume_suite`, `admit_device`, `answer_key`, `EvaluatorPassword.unlock_bytes` and a recording `call`.

**Docs.**
- `docs/generated-device-interface.md:176`: the harness holds sealed files, and waits until the set's folder is out of reach, rather than refusing at once.
- `.scratch/benchmark-baseline/spec.md` decision 17: the harness checks the set's folder only. Password files and the generator volume are the operator's to keep closed; a check of them was offered and declined.
- `docs/benchmark-case-authoring.md`: starting and resuming a set suite, step by step.
- `docs/design.md` "Limits of this protocol": a worker can read a copy kept elsewhere, the harness's memory, and plaintext the emulator needs on disk during a run. That includes the device recipe's source files during a start or a replacement, and the observation commands in the device journal.
- `src/generative_driver/benchmark_support/generated_trials.py:3-4`, `mcp.py:134` ("save suite_id"), and new `set_root` option descriptions in `mcp.py` and `benchmark.py`.

## Out of scope

- Closing or opening a vault from code, or detecting one by anything but the path. No `hdiutil`, `diskutil` or the like.
- Checking password files or the generator volume. The user declined it: closing them is the operator's step.
- Sealing, and where a sealed trial goes while the vault is closed (Feature 07). Decisions 17 and 21 still conflict on that, and Feature 07 settles it.
- Moving past a failed slot (Feature 06). Today a slot's failure ends the harness, so each one needs a reopen on resume.
- A claim on repeated resumes, and showing a mid-suite wait in `suite_status`.
- Plaintext the emulator needs on disk during a run. It is documented here and not changed.
- Windows locked-volume behaviour. BitLocker locked in place may answer in a way that keeps the gate shut until the limit; first real use shows.
- A real vault, volume, password, Renode or model run. First real use belongs to the plan 6 runbook. Real filesystem behaviour on an unmounted volume is untested here.

## Deviations
