# 03. generated-trials

## Intent

A generated device cannot run as a trial today. Everything from suite start to grade assumes one case id means one manifest at one root, a suite cannot list two devices, and comparisons collapse all devices into one. This Feature lets a suite name one set and its devices, and runs each device through the six stages to a grade, on hand-written fixture devices with scripted workers. The pairing code then counts trials device by device, though real set reports reach it only once Feature 04 records their scoring set.

A generated device's files are in the answer vault, which the configurator can't open and Feature 05 will unmount. So the harness builds each device's pin and the configurator only freezes it. The configurator takes the harness's word for the image and answer hashes; that is within the same-user limit in `docs/design.md`, not isolation. Grades of existing cases don't change. Editing the core moves every grader identity once, which costs nothing because nothing is admitted on main.

## Behaviours

**A suite of one set**

- **03-B1** — `normalize_manifest` accepts a manifest with a top-level `set`, a nonblank string, whose entries are each `{case, scenario, case_seed, device_id}`. `case` is a case whose registration has `calibration_kind` `set`, `scenario` is `original`, `case_seed` is `0`, and `device_id` passes `validate_identity`'s id rule. `repetitions` keeps today's rule. A manifest without `set` normalizes exactly as today, so existing suite hashes don't move.
- **03-B2** — Each of these raises `ValueError` from `normalize_manifest`: an entry with `device_id` in a manifest without `set`; a `generated` entry in a manifest without `set`; a hand-made case in a manifest with `set`; an entry of a set manifest without `device_id`; the same `device_id` twice.
- **03-B3** — Suite options take `set_root`, a path, beside `evaluator_password_files`, which a set suite keys by set name. `set_root` goes to the harness on its stdin, as password files do, and `refuse_secrets` and `_refuse_secrets` refuse it anywhere else. `resume_suite` passes it the same way.
- **03-B4** — `run_suite` starting a set suite, before its first configurator contact, needs `set_root` and the set's password file, loads the set with `load_set`, and builds every entry's pin. A missing root or password, an unknown device, a set that isn't registered, an unreadable or wrong password or any reason from `generated_sets.admitted` ends it as an unreadable password file does (Feature 02's B6): no configurator call, `suite.json` `failed` and `accepted: false`, `launch_suite` `ok: false`, and the same `request_id` starts afresh once fixed. The reason is `The generated set <set> is not admitted: <reasons>`, or Feature 02's form for a password file. An `OSError` from `load_set` is named by class alone, so no path appears.
- **03-B5** — A suite with `set` resumed after its harness stopped reads its password files as today and doesn't load the set before contact. A slot that needs its device builds its `SetDevice` from its frozen pin's `set` and `device_id`, in the same lazy step as its password, which it looks up by `set`. A slot whose verdict is recorded or whose effect is uncertain needs neither. A root or password missing on resume fails only at a slot that needs it, as Feature 02's B7 says.

**The device pin**

- **03-B6** — `device_pin(manifest, device_id, evidence_sha256)` returns a pin with exactly `pin_case`'s keys plus `set` and `device_id`. It holds `manifest_sha256` (the set's), `truth_sha256` (the answer file's `sha256`), `image_hashes` `{'firmware.bin': image_sha256}`, `calibration` `{'status': 'passed', 'evidence_sha256': evidence_sha256}`, and a `manifest` and other fields from one constant template in `generated.py`. Two devices' pins differ only in `device_id` and those hashes.
- **03-B7** — No pin, run spec or configurator call holds a device's `level` or `framing`, the set root, or any part of the mission beyond its hash; no file under the configurator home holds the level, framing or root. Checked on a successful scripted suite with a recording `call`. The mission and image themselves do reach the home through the snapshot and stage inputs, as they reach a worker.
- **03-B8** — The configurator freezes the pins the harness sends at `suite_start` and starts each child from its frozen pin. A set suite whose `suite_start` lacks a pin for an entry, carries one for another device, or carries one `current_pin` refuses, is refused. `start_suite` with no configurator up checks only the selection, since it holds no pins.
- **03-B9** — `current_pin` of a family that doesn't declare it returns `pin_case(pin['case_id'], pin['scenario_id'], pin['case_seed'], check_truth=...)`, and each of the five sites refuses a pin that differs from it, as today. A site also refuses a pin whose case, scenario or seed differ from the request's. A start with no `case_pin` still uses `pin_case`. So every existing case's refusals and errors are unchanged, for a changed manifest, image or truth file.
- **03-B10** — `generated`'s `current_pin` rebuilds the pin from the template and the pin's own hashes, and requires it to equal the pin, and requires its `manifest_sha256` to be what `sets.json` registers for its `set`. It raises `ValueError` otherwise, and opens no file of the set. So an extra `level` key or a wrong set hash is refused by the configurator, and a correct set hash with a wrong device hash by `admit`.
- **03-B11** — `pin_case('generated', ...)` still raises `PendingCaseError`, so `benchmark run --case generated` and corpus plans stay refused.

**Admission**

- **03-B12** — `admit` for a set device calls `generated_sets.admitted`, and raises `ValueError` naming every reason when it returns any. No flag or test switch bypasses it: a fixture set gets past it only by qualifying with `set_qualification.qualify` under a registration table.
- **03-B13** — `admit` for a set device refuses a pin whose hashes differ from the set's manifest entry, opens the device with `open_device(..., answer=True)` once, runs `validate_setup`, and returns `(provider, admission, truth)` with `admission` naming the qualification record's digest and `truth` an `UnlockedTruth` of the answer key with `sha256` the answer file's hash. The provider receives only the recipe.
- **03-B14** — A slot's `admit` writes the device's image and canonical mission JSON to `<trial_dir>/public-inputs/`, and the provider starts from that image.
- **03-B15** — The configurator's snapshot copies those two files from the `public_inputs` path the harness sends, checks each against the pin's hash, and refuses a mismatch. After that, the run reads them only from its snapshot. A case with no `public_inputs` copies from its case root as today.

**Running the stages**

- **03-B16** — `adapter_for` reads the case's `adapter_key` from its registration and opens no file. This narrows Feature 02's B22: a changed manifest or image no longer raises from `adapter_for`, and `_verify_snapshot` and each start still catch it.
- **03-B17** — During a run, stage preparation, mapping checks and the reuse objective read a set device's mission from the run's snapshot. A hand-made case reads its mission as today.
- **03-B18** — A stage's objective, scope and inputs on a device whose descriptor offers no readings don't mention readings, and its live call results and journal hold no `before_observation` or `observation`. The sentence that the emulator runs only inside each live call stays. A case with readings gets today's exact text and fields, checked against literals for `tq9-v2`. The test reads `_prepare` and `_check` output. `STAGE_BOUNDARY` in `agents.py` and the package-execute tool description stay as they are, as shared guidance.
- **03-B19** — The `generated` row in `cases.json` gives every generated trial the same effects, `['write', 'actuate']`, and the `emulator` approval scope, so the worker's grant reveals nothing about the design.

**Grading**

- **03-B20** — A trial on a fixture device, with a scripted worker through real `_prepare` and `_check`, reaches `completed` with all six accepted handoffs and a grade. A worker whose emitted driver is a reference passes (`equivalent: True`), and one whose driver is a mutant fails (`equivalent: False`) with that mutant's checks failed.
- **03-B21** — `resume` of a slot's trial in a suite grades from the answer key unlocked through the set, and `reattach` admits through the set as 03-B13 says. `benchmark trial-resume` and `harness --resume` on a generated trial refuse with `ValueError` saying to resume it through its suite, since neither has the set root.
- **03-B22** — A family whose `reuse_passed` returns `None` doesn't judge fresh reuse. `generated` is one. Its report shows `{'name': 'fresh_worker_mission', 'passed': None, 'reason': 'This case does not judge fresh reuse'}`, on every grade path, including no driver and no mapping, whether or not the fresh worker called the driver's tools, in the first grade and in a regrade. The owner-proof failure at `evaluator.py:190-191` and `:358-359` doesn't apply to it. The scorecard's fresh-reuse count and claim agreement treat it as they treat any `passed: None`. For every other family the mission result is as today, including `reuse_passed` returning `False`.

**Suites and pairing**

- **03-B23** — `device_id` flows from the entry into the trial slot, the suite row and the report, and a row validates with it. A hand-made suite's rows hold no such key.
- **03-B24** — `pairing` keys both outcomes and per-case pins on `(case, device_id or None)`. Saved reports over two fixture devices, each with a hand-added `scoring_set` as `tests/test_pairing.py:73` does, pair as two devices, not `repeated`. A device only one report tried counts `absent`. Hand-made reports pair as today, and pairing's output still names no device. A set manifest with `repetitions` above 1 counts its devices `repeated`.
- **03-B25** — A scripted suite over two fixture devices with one candidate driver ends `finished`, with two graded trials, one report row each, both passing. `report_suite` regrades both, with `password_files` keyed by the set's name.

## Interface and seams

**`benchmark_support/generated_trials.py`**, new, outside the core, is the harness's way to a set device. It imports `generated_sets` and `generated`.

```python
@dataclass(frozen=True, repr=False)
class SetDevice:
    set_root: Path          # lives in the harness's memory; no JSON form
    registration: str
    device_id: str

def device_pin(manifest, device_id, evidence_sha256) -> dict
def qualification_digest(set_root, password) -> str      # canonical_digest of the unlocked record
def admit_device(device, pin, password, trial_dir, *, renode=None) -> tuple   # (provider, admission, truth): 03-B12 to 03-B14
def answer_key(device, password) -> UnlockedTruth
```

`run_trial`, `reattach` and `resume` gain a keyword `device: SetDevice | None`, which `_suite_trial` passes. `admit` gains the keywords `device` and `pin`, passed only for a set device, so existing test doubles of `admit` stand: `run_trial` takes the pin from `start['case_pin']` and `reattach` from `result['case_pin']`. `run_suite` pops `set_root` from its request, as it pops password files. `generated_sets` keeps its functions; `admitted` is unchanged here.

**The family hook.** `current_pin(pin, *, check_truth=True) -> dict` joins the hook table of `families.py`, optional, with the 03-B9 default. It replaces the recompute at `registry.py:305`, `evaluator.py:244`, `snapshots.py:61`, `configurator.py:1909` and `:2150`. `:244` also requires `pin['case_id'] == case_id`, and `:2150` the pin's case, scenario and seed to match the start. `generated.py` declares it, holds the pin template and imports `generated_sets` inside the hook, so `generated_sets` joins only the generated family's closure. No core module imports it, and 01-B6, 01-B9 and 01-B17 still hold. Tests redirect the table by patching `generated_sets._SETS_FILE`; production has no option or variable that changes it. `test_generated_family.py:472-474` changes for that closure.

**Pin and evidence.** `set` and `device_id` join `evidence.PIN`; pins of other cases don't hold them. The template's constants:
- From the `generated` row: `case_id`, `case`, `id` and `family` are `generated`; `case_version`, `evaluator_version` and the manifest's versions are `1`.
- `scenario_id` is `original`, `case_seed` is `0`, `scenarios` is `['original']`, and `required_stages` is the six stages.
- Copied from `parameter-store-v1`'s `case.json`: `execution` `actual-agent-emulation`, `evidence_track` `firmware`, `scope` `full-workflow`, and its top-level `time_policy`.

The pin's `manifest` has `schema` `benchmark-case/2`, the fields above that a manifest holds, `images`, `assets` `{'mission.json': mission_sha256}`, `truth` `{'sha256': …}` and `default_effects` `['write', 'actuate']`, and no `time_policy`. `artifact_name` then gives `firmware.bin`.

**Row or manifest.** Only the `generated` row in `cases.json` fills `execution`, `evidence_track`, `approval_scope` and `default_effects`, with those same values and `emulator`. One helper returns a row's value when it isn't null and `resolve_case`'s otherwise, for the sites that read only those fields: `configurator.py:107`, `:147`, and `preflight_selection`. So hand-made rows don't move and those sites keep their changed-case refusal.

**Public inputs.** `public_inputs` joins `HARNESS_KEYS` in `suites.py`. It is a path inside the trial folder, as the control endpoint already is, and it holds only what the worker receives. `snapshots.snapshot_case` takes it as its source. The mission file holds exactly the bytes `canonical_digest` hashes. The folder stays with the trial; a `reattach` writes it again.

**Suite start.** `suite_start` gains `device_pins`, `{device_id: pin}`, frozen with the suite. `preflight_selection` takes definitions from the row helper for a set manifest; `freeze_suite` checks each pin through `current_pin` as 03-B8 says. Allowlists that gain `device_id`: `suite_store.py:174`, `suite_reporting.py:352` and `configurator.py:1690`. `SECRET` gains `set_root`. `report_suite` keeps its parameter `password_files`, keyed by `manifest['set']` for a set manifest (`suite_reporting.py:689-699,735-736`). Replay candidates stay keyed by case, so one candidate serves every device of a suite; a per-device reference or mutant stays in 03-B20's single trials.

**Worker wording.** `instrument.units` reaches the adapters through `case_options` (`configurator.py:129-131`), and an absent key means readings are offered, so hand-made cases don't change. Conditional on it: `INSTRUMENT` (`emulated.py:17-20`, keeping its emulator-pause sentence), the reuse objective (`:324`), the observe scopes (`:522`, `:573`), `ground.py:62-72`, `candidate_context.py:94-95`. The `observe()` calls at `configurator.py:2963-2967` still run, since they also refuse a call when the provider fails; only the result and journal fields go. Shared guidance that doesn't promise a reading stays, such as `interpret-interface/SKILL.md:13`.

**Fresh reuse.** `case_assets.reuse_mission` and `evaluator.reuse_mission` are seams for 03-B22. `evaluator.reuse_mission` first calls `family.reuse_passed([], {})`, catching what `case_assets.reuse_mission` catches, and `None` returns the literal. No other family returns `None`: they return `False` or raise. `_generated_mission` returns a mission with that reason unchanged, through one shared reason constant, so a regrade reads it from the sealed mission and needs no case id. The owner-proof flags at `evaluator.py:330-331` and the checks at `evidence.py:237-240,401-403` stay as they are.

**Fixtures.** `tests/generated_fixtures.py`'s session gains the `Process` stand-in that `tests/suite_fixtures.py:175-178` uses, so a provider stop is confirmed. Tests of 03-B20 use the scripted `work` of `SealedTrialFixture.staged_trial` (`tests/test_unlocked_keys.py:500`) under `run_suite` with a one-entry set manifest, since a generated trial has no route outside a suite. 03-B25 follows `ReplaySuiteTests` (`tests/test_replay_executor.py:417`). `reference_execute` isn't patched: the fixture session serves it.

**Tests attach** at `normalize_manifest`, `run_suite`, `launch_suite`, `resume_suite`, `device_pin`, `family_for(...).current_pin`, `admit`, `run_trial`, `resume`, `reattach`, `adapter_for`, both `reuse_mission`s, `pairing`, `report_suite`, and a recording `call`. `test_02_B22_adapter_for...` changes as 03-B16 says.

**Docs.**
- `docs/generated-device-interface.md:171`: a pin names the set, its manifest hash, the device id, and the hashes of the image, the answer file and the qualification record, never the level or framing. `:89`: `observations` must read the same value on every boot, since recovery compares a replacement's reading with the first boot's. Say that Feature 04's qualification checks this; no code checks it yet.
- `docs/adr/0003-generated-devices-register-by-set.md`: the same amendment to its last sentence.
- `docs/benchmark-case-authoring.md`: a set suite's manifest and options.

## Out of scope

- Reading sets into memory and refusing a reachable root (Feature 05). This Feature reads the set from disk at start and at each `admit`, as Feature 02 reads truth.
- Tightening `admitted`, checking that every run was native and that each boot's reading matched, and recording `scoring_set` (Feature 04). Until then `paired_comparison` refuses a real set report, and its tests add `scoring_set` by hand. Level and framing in reports (Features 04 and 07).
- Faults, reruns, audit flags and a device tried twice (Feature 06), and sealing or keeping device ids out of what leaves the harness (Feature 07). A report row names its device here; the report stays with the harness.
- Judging fresh reuse against the answer key's `mission.state`: no Feature holds it yet, and the next Breakdown revision should weigh adding one.
- Suites of several sets, device-keyed replay candidates, and any route for a generated trial outside a suite.
- The generator, real Renode, a real set, vault or password, model runs, and Windows or Linux runs. Whether a real device's boot reading is stable is untested here.

## Deviations

- `FixtureDevice.session` and `fixture_start` take the `Process` stand-in as a keyword, `process=False` by default — test-only; the Spec's aim, a confirmed provider stop, holds for trials, and `tests/test_set_qualification.py:123` stays true.
- `generated.py` holds the template in a public `device_pin`, with `ARTIFACT` and `MISSION`, and `generated_trials.device_pin` wraps it — the Spec asks for one template in `generated.py` and leaves the name open.
- The row helper is `registry.case_selection`, answering from the row alone only when all four fields are filled — does what "Row or manifest" says; only the `generated` row fills all four.
- `device_pins` is in the stored request, and a non-set suite sent pins is refused — both follow from freezing pins and don't widen scope.
- `freeze_suite` also refuses a differing `case_id` or `set` — falls under "carries one for another device".
- `_set_device_pins` normalizes, reports unknown devices first and removes repeated reasons — keeps 03-B4's reason form and changes no outcome.
- Own wording for missing-input reasons — the Spec fixes only the form.
- `admit_device` compares the pin using the pin's own evidence value, and `_provided_device` compares the evidence — 03-B13 asks for hashes, and the evidence is still checked on a new start.
- `OSError` is named by class alone in `admit_device` and `answer_key` too — stricter, and serves 03-B7's rule that no root reaches a record.
- `reattach` with no `device` has no explicit refusal — confirmed: `_suite_trial` is its only caller, and it builds the device first.
- `_expected_pin` gives `case_seed must be an integer` for a non-`int` options seed and `Expected case pin differs...` for a malformed or differing pin, including an unknown scenario on a pinned start — within 03-B9's scoped "unchanged".
- `KeyError` instead of `IntegrityError` at `evaluator.py:250` for a pin with no `scenario_id` — follows the Spec's literal hook, and `require_snapshot` compares the pin first.
- New signatures the Spec leaves open (`load_mission`, `task_contract`, `_required_tasks`, `_capability_guidance`, `_mapping_errors`, `device_evidence`, `repair_sources`, `snapshot_case`, `_suite_trial`) — each carries `run_dir`, `readings` or `public_inputs` as 03-B15, 03-B17 and 03-B18 need.
- New public names (`generated_device.validate_device_id`, `snapshots.canonical_bytes`, `device_client.offers_readings`, `emulated.PAUSED`, `case_assets.NOT_JUDGED`, `generated_trials.PUBLIC_INPUTS`), and `record_call`'s options gain `device` — each serves a Behaviour or the Spec's shared-constant seam.
- `qualification_digest` reads the private `generated_sets._QUALIFICATION_FILE` — the Spec keeps `generated_sets` unchanged.
- `suite_reporting.py` checks a pin's `device_id` against its entry — under 03-B23's "a row validates with it"; hand-made entries compare None with None.
- Existing tests changed (`test_family_seam.py`, `test_generated_family.py`, `test_case_registration.py`, `test_unlocked_keys.py`, `test_corpus_preflight.py`) — named by the Spec or forced by the new hook.
- Over-long lines: `emulated.py:329`, `suite_reporting.py:353` and a 121-column comment at `tests/test_generated_trials.py:1340` — no scope effect; the two source lines were already long.
- Test-first wasn't kept everywhere, with mutation and stub REDs instead — process only; the RED evidence was judged and fits.
- `test_03_B25` now waits for the harness to exit before reading `suite.json` — the test's wait read the file between detach and final write, an order that predates this Feature. The window was only reproduced by widening it, so linking the one failure to it is a strong inference; the status assertion still runs.
- Docs amended as listed, nothing real run — the Spec's own list, and the out-of-scope items were not run.
