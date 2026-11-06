# 04. set-admission

## Intent

A generated set should reach trials only on complete, native qualification, and a comparison needs to know which set it is scoring. Today `admitted` trusts the record's `passed` status and one device's references, so a record with skipped mutants or no real emulator process would pass. Nothing tells a scoring set from a development set, and `scoring_set` is read but never written.

This Feature makes admission check the whole record, marks scoring sets in the code-owned list, requires a scoring suite to run every device once, and writes `scoring_set`. It also stops a registered set from moving `toolchain_revision`, and protects a scoring set's passed record from being replaced by accident. Grades don't change. Editing the core moves every grader identity once, which costs nothing because nothing is admitted on main. Only the user's real qualification proves the native path: tests prove the check reads the record correctly, not that Renode ran.

## Behaviours

**Which sets are scoring sets**

- **04-B1** — `sets.json` is `generated-set-registrations/2`: `{"schema", "sets": {name: {"manifest_sha256", "scoring"}}}`, with `scoring` a boolean. `set_registrations` still returns name to hash, so its callers stand, and `is_scoring(name, path=None)` returns the mark. Another schema, an unknown or missing field, a non-boolean mark or a bad hash raises `ValueError`. An unregistered name raises `ValueError` from `is_scoring`.
- **04-B2** — Any change to `sets.json` leaves `toolchain_revision` as it was, and any other change under the package still moves it. So adding another set, or changing another set's line or any set's mark, neither moves a comparison dimension nor blocks a suite's resume. Changing a running suite's own registered hash still refuses its resume, through `current_pin`.

**What admission checks**

- **04-B3** — `admitted` returns a reason for each of these in the record, and `[]` only when none applies: `failures` isn't empty; `devices` doesn't name exactly the manifest's ids; a device's two references didn't both pass (`references not passed:<id>`, for every device, not only the one asked about); `sample` isn't what `sample` computes from the manifest; a `mutants` row wasn't rejected or repeats another; a sampled device lacks a required mutant, or a device outside the sample has any mutant row; `runs` doesn't hold exactly `<id>/reference-a` and `<id>/reference-b` for each manifest id and `<device>/mutant-<id>` for each `mutants` row; a run's `native_process_observed` isn't `True`. It never reads `backend`. Today's reasons stay, without repeats. It still never raises for what a file holds, whatever the shape of a field. Each reason is fixed text naming at most a device id, and never copies text out of the record, whose failures can hold a private path.
- **04-B4** — A device's runs must all show the same boot reading. `admitted` returns a reason when they differ, or when a run's reading is missing or empty.
- **04-B5** — A record from fixture devices with no process stand-in is refused with a reason naming the native process. The same set qualified with the stand-in is admitted.
- **04-B6** — A suite on a set with any such reason refuses before its first configurator contact, as a set not admitted does today, and each slot's `admit` refuses the same way.

**Recording the boot reading**

- **04-B7** — `generated.execute` reads the device's startup observation once on the fresh session, before any reset or call, with the same read `DeviceProvider` compares on recovery, and records it in its evidence. Grades and check ids don't change. A recipe's read that raises is a run error, as other device errors are.
- **04-B8** — `qualify` copies each run's reading into its `runs` row. A run that raised has `None` there, even if it left partial evidence. All of a device's runs finish, then `qualify` compares its readings that aren't `None`: when they differ, it adds one failure, `boot reading differs:<device>`, and the set stays pending. No new run is added. The record's schema stays `generated-set-qualification/1`, since no record exists yet.

**A scoring suite**

- **04-B9** — `run_suite` on a scoring set refuses, before its first configurator contact and in the not-admitted form, unless the entries name every device of the manifest exactly once and `repetitions` is `1`. A development set may run any devices, any number of times.
- **04-B10** — `freeze_suite` on a scoring set stores `scoring_set` `{registration, manifest_sha256}`, the set's manifest hash from the pins, in the frozen suite, and `_suite_experiment` copies it into the report's identity. A development set's suite has no such key. A later change to `sets.json` doesn't change a frozen suite's value.
- **04-B11** — A saved report of a scoring suite passes `paired_comparison`'s scoring-set check without a hand-added value, and its scorecard is aggregate-only. A development suite's report is refused by that check, as today.

**Redoing a passed qualification**

- **04-B12** — `qualify` on a scoring set whose existing record is `passed`, unlocks under the given password, and has the current grader identity, refuses with `ValueError` before any run and leaves the file unchanged, unless called with `replace=True`. `benchmark set qualify --replace-passed` passes it. A record under another identity, a pending record, a missing or unreadable one, and a development set qualify as today.

**The test variable**

- **04-B13** — With `GD_TEST_HARNESS_FIXTURE` set in its environment, the harness refuses a set named in the shipped `sets.json`, at start and at each slot's `admit`, with a reason naming the variable. It reads the shipped file by the package's own path, not through `_SETS_FILE`. Tests' own tables and every set not in the shipped list run as today. If this grows past one small function and its test, drop it and report that as a Deviation: the user has accepted that outcome, since the risk is low.

## Interface and seams

**`benchmark_support/generated_sets.py`**

```python
def is_scoring(registration, path=None) -> bool
def sample(devices) -> list[str]                  # moved from set_qualification
def qualification_reasons(record, manifest) -> list[str]   # pure: 04-B3, 04-B4
```

`qualification_reasons` holds only 04-B3's new checks and 04-B4, so it is pure and Feature 05 can feed it what it holds in memory. `admitted` keeps its signature and today's checks: registration, manifest hash, grader identity, status and the asked-for device. It skips `qualification_reasons` when the manifest was refused, since it has none to pass, and removes repeated reasons. A dropped extra mutant isn't detected, because the record holds no list of the key's mutants and its tag authenticates it; `required_mutants` is used only for the "sampled device lacks one" check. `sample` moves because `generated_sets` is in the generated family's closure and no grading module may import `set_qualification`; `set_qualification` imports it back. The required mutants come from `family_for('generated').required_mutants`. Whether the references are distinct isn't read here: the device validator's rule refuses that at `open_device`, and the manifest hash binds the key.

**Run rows.** A `runs` row is `{backend, native_process_observed, boot_reading}`, with `boot_reading` the `values` of the startup observation. Run names stay `<device>/reference-a|b` and `<device>/mutant-<id>`. The emission session isn't recorded: only the grading session counts, as today.

**The boot read.** `generated.execute` uses `device_provider.observed_values` at its top and puts the result in every save of its evidence, `raw.json`, where `qualify` reads it at `output/runs/<name>/raw.json`. This edits only the generated family, so only its identity moves for this part. `generated.py` is the only family that records it.

**Redoing a passed record.** `qualify` checks 04-B12 only `if registrations is None and is_scoring(registration)`, after `load_set` and before `open_device`, and reuses the identity it already computed. `is_scoring` reads the table itself, since `set_registrations` drops the mark. A `registrations=` dict holds no mark, so such a call counts as a development set. The CLI passes `replace` only when the flag is set. Tests of 04-B12 patch `_SETS_FILE`.

**The fingerprint.** `snapshots.execution_provenance` hashes the package without `resources/registrations/sets.json`, through a keyword on `integrity.tree_hash`. Other callers of `tree_hash` are unchanged, including `reporting.py:305` and `suite_reporting.py:758`, which record the exporting installation rather than a run's implementation.

**Where scoring is read.** `harness._set_device_pins` is the only place holding both the manifest and the entries, so 04-B9 lives there. `freeze_suite` reads `is_scoring` and takes the hash from `device_pins`. `current_pin` is unchanged.

**The guard.** One small function in `generated_trials`, called from `_set_device_pins` and `admit_device`. It holds the shipped path in a module constant. A test passes its own shipped path to it.

**Tests attach** at `set_registrations`, `is_scoring`, `execution_provenance`, `admitted`, `qualification_reasons`, `qualify`, `benchmark set qualify`, `run_suite`, `freeze_suite`, `_suite_experiment`, `paired_comparison`, the scorecard, and the guard. 04-B2's test copies the package and `interface_runtime` to a temp directory, patches `snapshots.__file__` to the copy, and changes `sets.json` and another file there, in the manner of `PackageCopy` (`tests/test_grader_identity.py:26`); it never edits the shipped file. Nothing here proves a real emulator process: that is the user's qualification.

**Existing tests that change.**
- The qualified fixtures (`tests/test_generated_trials.py:53`, `tests/test_set_qualification.py:74`) qualify with `process=True`. `test_each_run_records...` (`tests/test_set_qualification.py:120-124`) reads that fixture's record, so it moves to `PendingSetTests` with its own one-device `run_qualify` at the default `process=False`, and expects each row to hold `native_process_observed: False` and the fixture's `boot_reading`. 04-B5's refusal test also uses `process=False`.
- `tests/test_set_qualification.py:222,282` assert membership in the reasons, since a record with failures now has more than one. `:257-258` expect the new row.
- `tests/test_generated_family.py:260` expects the `cpu PC` read first. `tests/test_generated_sets.py:36,42,50,52,63` and `tests/test_grader_identity.py:213` follow the table's new form.
- The shared fixture table stays `scoring: false`, because one-device suites would otherwise be refused. Scoring tests use a second table with the same hash.

**Docs.**
- `docs/generated-device-interface.md`: `:89`, qualification now checks the boot reading; the registration table's new form; what admission checks. Steer the generator to a read that is stable at boot.
- `docs/benchmark-case-authoring.md`: marking a scoring set, `--replace-passed`.
- `docs/design.md`'s "Limits of this protocol": a process that runs as the same user, or the test variable, can replace any of these checks; the guard catches mistakes only.

## Out of scope

- Level and framing in reports, an in-memory join at scorecard time (Feature 07), and sealing.
- Reading sets into memory (Feature 05), though `qualification_reasons` is shaped for it.
- Moving past a failed slot, reruns and a device tried twice after a fault (Feature 06).
- Telling a development set from a leak-check batch: no code reads that difference.
- Pid or image proof in a set's record: the boolean is ruling 44's rule, and qualification wrote that image itself.
- Removing `GD_TEST_HARNESS_FIXTURE`. It stays for tests.
- Any real qualification, set, password, vault, Renode or model run. Requalification is the user's, at pinning.

## Deviations

- `run_suite` refuses a device named twice with `Duplicate suite entry`, not the not-admitted form — the refusal still comes before any contact, so only the message differs.
- Own fixed text for the reasons the Spec left open — 04-B3 asks for fixed text naming at most a device id, and an id appears only when the manifest lists it.
- Added `qualification mutants malformed` and `qualification malformed` — fixed text, needed for 04-B3's "never raises", and they refuse rather than admit.
- A boot reading that isn't a non-empty object counts as missing — fails closed, and serves complete qualification.
- `raw.json` stores only `values` as `boot_reading` — the Spec defines the row as the values, and the raw monitor responses aren't needed to compare.
- `qualify` records `boot_reading: None` when a finished run's evidence can't be read, including valid JSON that isn't an object — fails closed: admission refuses with `boot reading missing`, and a test covers it. JSON nested too deep still raises `RecursionError` out of `qualify`, reachable only by damaging the file and before anything is sealed.
- Names the Spec left open (`tree_hash(exclude=)`, `fixture_variable_reasons`, `_SHIPPED_SETS`, `_registered`, `_passed_under`) — they implement seams the Spec describes; the guard stays one function, so it wasn't dropped.
- The refusal text names `--replace-passed` even when `qualify` is called directly — only tells the user how to proceed.
- Test reshaping (`FixtureDevice(pc=)`, `registrations_table`, split fixture classes, renamed and moved tests, a reworded comment) — supports the Spec's listed test changes, with no product scope.
- 04-B12's "qualifies as today" rows patch `open_device` to show `qualify` got past the check — each row fails if the check refuses, and real re-qualifications are covered elsewhere.
- The 04-B8 test wraps `reference_calibration.calibration_run` to damage a finished run's `raw.json` — the real function still runs, and a real run leaves no other way to reach this path.
- Doc additions on scoring suites, aggregate-only scorecards and a rules-table row, and a "Boot reading" row in `CONTEXT.md` — they describe what was built and define the new term, with no change in behaviour.
