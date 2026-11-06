# Author a benchmark case

Add intake evidence first, implement executable support in trusted code, qualify its evaluator, then run actual agents. A catalog entry alone does not supply an adapter, a native backend or a correct evaluator. Use the [operator workflow](benchmark-corpus.md) for selection, authentication, execution and reporting, and the [registration decision](adr/0001-benchmark-registration-and-selection.md) for the trust boundaries.

## Choose the identities and responsibility

| Item | Public responsibility | Private evaluator/operator responsibility |
|---|---|---|
| Lineage | Catalog ID, immutable upstream commit/path/license, origin group, split, architecture/system, protocols, behavior strata, mission requirements, limitations and blockers. | Verify source ancestry, license evidence and reproducible native build. Keep related origins in one split. |
| Case | Stable case ID/version, manifest, original image hashes, ciphertext pin, six-stage inventory and effect grants. | Keep matching encrypted truth, credentials, native observations and qualification evidence. |
| Family | Trusted Python implementation of observations, contracts, evaluation actions and fresh mission checks; static registration selects it. | Independently authored references, recipes, hidden episodes and targeted mutants. |
| Public mission | `benchmark-public-mission/1`: task meanings, typed input bounds, output units, a fresh reuse objective and optional supplied operating conditions. | Hidden expected values and independent check evidence. Public task requirements must agree with both phases. |

A case identifies one pinned experiment; a family supplies its behavioral implementation; a mission names what the worker must accomplish. Reuse an existing family only when its actual semantics fit. A new protocol may require runtime/transport, backend and observer work as well as a family implementation. Registration never makes an arbitrary protocol executable.

## File checklist

Paths below are relative to the repository. Add or update only the files needed for the supported case; preserve third-party notices. The [catalog validator and `AREAS` / `STRATA` enums](../src/generative_driver/benchmark_support/corpus.py) define accepted fields and values. Start from the public [Zephyr mission JSON](../src/generative_driver/resources/bench/missions/zephyr-modbus-v1.json), adapting task requirements rather than hidden answers; the [mission validator](../src/generative_driver/benchmark_support/missions.py) requires `schema`, `case_id`, `tasks` and `reuse_objective`, and permits the optional root field `operating_conditions`. Other root fields are rejected. Each task declares `meaning`, `inputs`, `outputs` and optional `sampling`; inputs have types/bounds and every output has an explicit `unit` or `null`.

| File | Required change |
|---|---|
| [src/generative_driver/resources/bench/corpora/seven-area-pilot.json](../src/generative_driver/resources/bench/corpora/seven-area-pilot.json) or a versioned custom catalog | Add the lineage using the validator's exact fields/enums and this public catalog as a template. Start with `case: null` and explicit blockers until executable evidence exists. A custom catalog is selected with `corpus plan --catalog PATH`. |
| `src/generative_driver/resources/registrations/cases.json` | Add an entry naming every `CaseRegistration` field: `id`, `family`, `version`, `adapter_key`, `schema`, `native_module`, `mission_case`, `truth_schema`, `build_kind`, `calibration_kind`, `grading_method`, `execution`, `evidence_track`, `approval_scope`, `default_effects`, `default_scope`, `requires_calibration`, `has_case_file` and `single_image`, with `null` where none applies. A new case uses `fresh-final`, `null` for the four manifest fields, `full-workflow` and `true` for the last three. A duplicate key, an unknown or missing field or a duplicate id is refused. The public manifest must agree with its trusted identity. |
| `src/generative_driver/benchmark_support/<family>.py` | Declare a module-level `HOOKS` dict, one entry per [family hook](#family-interface) the family needs, with helpers private to the module. Validate unavailable/malformed observations. Import anything that decrypts a truth inside the hook function that needs it, since the device provider loads this module. |
| `src/generative_driver/benchmark.py` and dispatchers in `src/generative_driver/benchmark_support/` | Inspect `registry.py`, `adapters.py`, `emulated.py`, `emulator.py`, `evaluator.py`, `evaluator_cli.py`, `upstream_build.py`, `upstream_qualification.py`, `reference_calibration.py`, `calibration.py` and the affected runtime/provider for unsupported behavior. `emulator-v2` uses `emulated.py`'s `prepare_stage`, `observe_stage`, `package_call`, `record_call` and `score`; use it only when that lifecycle fits. New adapter/build/calibration keys need trusted implementations, not just strings in registration. |
| `src/generative_driver/resources/bench/missions/<case-id>.json` | New `emulator-v2` cases must provide a mission under their own case ID for interpret/probe/ground task contracts and instrument evidence. Declare canonical task names, inputs, units, fresh objective and any supplied `operating_conditions`; avoid case-name branches in prompts. Registration's `mission_case` selects only the existing fresh-reuse objective path (legacy `tq9` uses `tq9-v2`); it is not an all-stage mission alias. |
| `src/generative_driver/resources/bench/cases/<case-id>/case.json` and `firmware.bin` | Publish reviewed identity, one original image, provenance, scenario `original`, all six required stages, effects, time policy, limitations and calibrated ciphertext/evidence hashes. |
| `src/generative_driver/resources/bench/groundtruth/<case-id>.enc` | Publish encrypted, hash-pinned truth. For upstream cases it binds identity, image, source/build, recipe, disjoint phases, two references, mutants and the exact public mission. Keep native modules in code; assets and decrypted bundles never choose imports. |
| `src/generative_driver/resources/bench/platforms/`, `licenses/`, `cases/<case-id>/NOTICE.md`, `cases/<case-id>/licenses/` | Add needed platform/transport resources with hashes, provenance and notices. Backend observations must be independent of the candidate's decoded replies. |
| `tests/` | Cover public case resolution, identity tampering, family loading, mission/observation/reuse dispatch and plan/report behavior through existing seams. Observe a behavior test fail before implementing each code change, then pass. |
| Operator-private directory outside checkout/package/workers | Store source/build provenance, authoring inventory, recipes/oracles/references/mutants, raw qualification logs and separately held password files. Do not copy these into worker inputs. |

The single registration is consumed by case resolution, native-family loading, instruments, reuse and upstream truth validation. Loaders import only that code-owned module when needed. Catalogs, manifests and encrypted truth are data; adding Python paths to them cannot extend the system. An unused family's unavailable optional tools must not prevent another family from loading.

`operating_conditions`, when present, must be a list of 1–16 nonblank strings, each at most 1000 characters. Use it for public operating facts such as provider timing or required pacing. The same text reaches interpret, probe and ground in their task contract and fresh reuse in its objective. Keep hidden values, reference answers and private recipes out of this field. It describes operating conditions and grants no additional tools or effects. Changing it changes the public mission and requires matching authenticated inventory, fresh qualification and a new plan.

Dynamic `development_conditions` are a separate provider facility: probe/ground may receive a `DEVELOPMENT_CONDITIONS.json` catalog of selectable experimental inputs and use `condition_id` on live calls. A mission's `operating_conditions` does not create that catalog or select a source, and fresh reuse does not receive the development catalog.

## Follow the known Zephyr case

`zephyr-modbus` is the lineage; `zephyr-modbus-v1` is the case; `zephyr-modbus` is the native family. Its registration selects `emulator-v2`, `zephyr_modbus.py`, `benchmark-upstream-truth/1`, and the existing upstream build/calibration implementations. The manifest's `corpus_identity` must match the catalog's lineage, origin group, mission ID and immutable source commit.

The public mission asks for register/coil operations and unrelated-state preservation. Its operating conditions describe the paused-between-calls provider and a 50 ms execution delay between request exchanges, including automatic identity-to-operation exchanges. Place that delay inside the model operation while its live call is active; sleeping between tool calls cannot advance device time. This is a conservative emulator scheduling margin; acceptance checks values, refusals and effects, without measuring physical UART timing or throughput.

`zephyr_modbus.py` projects independent emulator memory and GPIO readings, produces diagnostic/final evaluation plans and checks package-only reuse. Its six mutants target constant reads, byte order, register/coil selection, omitted writes and request CRC. The UART bridge is protocol-independent; it does not implement Modbus for the candidate. The [operator guide](benchmark-corpus.md#rebuild-and-requalify-zephyr) gives the native build and qualification commands.

## Create the first encrypted inventory

The existing `truth calibrate` command reads an already registered manifest and encrypted inventory. For a new upstream case, implement its trusted support and place its public mission in the selected asset tree first. Privately author `upstream-truth.json` against [`upstream.hydrate_truth`](../src/generative_driver/benchmark_support/upstream.py): `schema: "benchmark-upstream-truth/1"`, `case_id`, `family`, `inventory_state: "pending"`, `images`, `source`, `build`, `recipe`, `phases` (`diagnostic` and `final`), `references` (`a` and `b`), `mutations`, and the exact `public_mission`. Author native episodes/references/oracles for that device; copying Zephyr's hidden expectations cannot qualify a different device. Author a matching public manifest using the [Zephyr manifest](../src/generative_driver/resources/bench/cases/zephyr-modbus-v1/case.json) as a field example, with the new trusted identity, provenance, image hashes and `calibration.status: "pending"`.

Use the existing [`EvaluatorPassword.seal`](../src/generative_driver/benchmark_support/truth.py) emitter to create the initial ciphertext and pin its returned digest in that manifest. For example, run this as an operator-private Python script after authoring both input JSON files; it creates pending assets, not qualification evidence:

```python
import json
from pathlib import Path
from generative_driver.benchmark_support.truth import read_password
from generative_driver.benchmark_support.upstream import hydrate_truth

private = Path("/operator/private/new-case-authoring")
payload = json.loads((private / "upstream-truth.json").read_text(encoding="utf-8"))
manifest = json.loads((private / "case-template.json").read_text(encoding="utf-8"))
assert payload["inventory_state"] == "pending"
assert (payload["case_id"], payload["family"]) == (manifest["id"], manifest["family"])
hydrate_truth(payload)  # Validates against trusted registration and public mission.
case = manifest["id"]
digest = read_password(private.parent / (case + ".password")).seal(payload, private / (case + ".enc"))
manifest["truth"] = {"path": "groundtruth/" + case + ".enc", "sha256": digest}
manifest["calibration"] = {"status": "pending"}
(private / "case.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
```

Copy the pending pair and its matching image into a complete operator asset tree; select that tree to run the implemented native qualifier. Keep its catalog lineage blocked until qualification succeeds. The allowlisted synthetic authoring format instead uses [`asset_build.inventory` and `assemble_release`](../src/generative_driver/benchmark_support/asset_build.py); that emitter currently supports sampled-sensor and parameter-store, so another family needs reviewed trusted support. There is no asset-defined plugin or universal new-protocol bootstrap command.

## Family interface

A family hook is one thing the grading core asks of a family. A family module declares `HOOKS`, hook name to value, and the core reads it only through `families.family_for(case_id)`, which returns a frozen `Family` with one attribute per hook. A graded case is one whose `adapter_key` is `emulator-v2`. `families.family_from` refuses, naming the case and the hook, an unknown hook name, a graded case that lacks a required hook, and a graded case that declares both `execute` and `build_plan`, or neither. A case with no module gets every default, and using a required hook of a case that does not declare it raises `ValueError` naming the case.

| Hook | Graded case | Default |
|---|---|---|
| `contract(pin, truth, phase)` | required | raises |
| `observations(raw, canonical_task_result, inputs)`, with `monitor_units` and any `runtime_units` | required | raises |
| `reuse_passed(events, capabilities)` | required | raises |
| `required_mutants`, the `frozenset` of mutant IDs a qualification record must list exactly and show rejected | required | raises on read |
| `execute(session, invoke, pin, contract, truth, evidence_path, *, phase, healthy_device_failures)` | this or `build_plan` | runs `build_plan` through `execute_plan` |
| `build_plan(pin, truth, phase)` | this or `execute` | raises |
| `hydrate_truth(truth)` | optional | returns `truth` |
| `admission_valid(pin, truth, record)`, a bool; `family_support.admission_valid` serves a family qualified from an authenticated inventory | optional | `False`, so the case is refused admission |
| `validate_phase(phase)` | optional | does nothing |
| `required_scenarios`, a `frozenset` every run's checks must cover | optional | `frozenset()` |
| `mutant_checks_may_exceed_expected`, a bool | optional | `False`: a mutant fails exactly its expected checks |
| `input_commitments`, the `frozenset` of a record's `input_hashes` names | optional | `source`, `contract`, `recipe`, `references`, `mutations`, `build`, `inventory` |
| `inventory_files`, a `frozenset` of authoring files beyond the shared ones | optional | `frozenset()` |
| `instrument_units()` | optional | `observations.monitor_units` |
| `public_observation(raw)`, `{values, units}` | optional | projects `observations` onto the units |
| `development_conditions(recipe)` | optional | `None` |
| `start_session(*, recipe, **arguments)` | optional | Renode; refuses any other recipe backend |
| `validate_execution(manifest, truth)` | optional | refuses a `native-sim` manifest |
| `current_pin(pin, *, check_truth=True)` | optional | `pin_case(pin['case_id'], pin['scenario_id'], pin['case_seed'], check_truth=...)` |

## Qualify, activate, then measure

1. Record immutable source/license evidence and build twice to the same firmware bytes. Verify backend startup/reset and independent observations. Define separate diagnostic and final episodes and their complete ending-state checks; do not infer an oracle from the candidate's own replies.
2. Run native image analysis and two independently valid references. Both must pass their required episodes; final episodes must execute emitted packages. Freeze each mutant's targeted failed check IDs before running it, then demonstrate rejection of every required mutant. Host faults, missing tools, skipped episodes or public `calibration.status: passed` text cannot qualify a case.
3. Use the matching trusted qualification command to emit the replacement manifest and encrypted bundle. For the three synthetic families, follow [native groundtruth qualification](../bench/groundtruth/README.md#native-reference-qualification), including private authoring inventory recovery for sampled-sensor/store. Qualify against the case's current grader identity (below); a change to its grading code voids existing evidence without changing the case version.
4. Review the pair and explicitly copy it into a complete operator asset tree using the [activation example](benchmark-corpus.md#rebuild-and-requalify-zephyr). Publish a new catalog revision that changes the row from `case: null` to the registered case ID and removes each blocker only when its evidence is resolved; any remaining blocker keeps the lineage blocked. The case manifest's `corpus_identity` must match the promoted catalog row. `GENERATIVE_DRIVER_BENCH_ROOT` selects the tree but neither installs outputs nor requalifies them. Preserve matching images, missions, platforms, catalogs and notices; keep raw authoring evidence private. Restart the owner with the selected tree, freeze a fresh plan and run `benchmark corpus check` with operator-only password handles.
5. Run the normal actual-agent six-stage pipeline: acquire → interpret → probe → ground → emit → reuse. Reuse starts a fresh worker with only the package, objective and permitted operating data. Retain all outcomes and obtain the harness's final equivalence and separate mission evidence. Native reference calibration and scripted/replay tests do not measure model performance.

Changing the image, mission/acceptance behavior, grading code, native recipes, references or mutant inventory requires qualification again. Version changed expectations and preserve old evidence/checkouts so historical reports retain their meaning. Software qualification leaves physical transport, timing and plant behavior open until measured separately.

### Grader identity

A qualification pins its case's grader identity (`benchmark-grader-identity/2`). It holds the SHA-256 of every Python module the grader imports for that case, the case's own registration entry and the `cryptography` version. The modules are the core, followed from the evaluator, evidence, registry, calibration and device-provider modules, and the closure of the case's `native_module`, if it names one. Admission requires the record's `evaluator_identity` to equal the installed identity of the manifest's case. A run's `evaluator_revision` is that identity's digest, and a sealed run regrades only under the identity it was sealed under. A suite records each case's revision as `evaluator_revisions` and is compared by its `evaluator_revision`: the case's own revision when the suite has one case, otherwise the digest of the mapping. No two cases share an identity, since each holds its own scope and entry. Each trial is checked against its own case's revision.

The system under test (configurator, toolkit, stage tools, interface runtime, package template, reports and suites) is outside every identity: changing it voids no qualification and blocks no regrade, and a run's `runtime_sha256` is provenance only. A change to a core module, `registrations.py` included, changes every case's identity. A change to any other grading module changes only the identities of the cases whose closure holds it: a family module changes its own family's, and `family_support` those of the three families that import it. A registration entry changes only its own case's identity. Adding a case or editing another family's own modules therefore keeps a case's identity. Requalify before running the default development suite; a record of an earlier identity schema fails admission with `identity:evaluator_identity`. `corpus check` authenticates only the selected upstream cases.

List the whole closure with `python -c "from generative_driver.benchmark_support.grader_identity import grader_closure; print(*grader_closure(), sep=chr(10))"`; pass `families=('tq9_v2',)` for the core and one family, or `families=()` for the core alone. The core names no case or family and loads a family only through `families.py`, the one closure module that calls `import_module`; the walk does not follow that load, so the closure seeds each family itself.

## Generated sets

A generated set is a batch of devices that the private device generator writes as one bundle, kept in the answer vault, outside the repository and the installed package ([ADR 0003](adr/0003-generated-devices-register-by-set.md)). Each device is a case of the code-owned `generated` family, which grades it from the device's own decrypted answer key. The [generated-device interface](generated-device-interface.md) specifies the bundle, a plaintext manifest, each device's sealed inputs and answer key and the sealed qualification record, and the rules each device meets.

**Register** a set by adding its name to `sets` in `src/generative_driver/resources/registrations/sets.json`, with `manifest_sha256`, the SHA-256 of its `manifest.json` bytes, and `scoring`, true for a scoring set and false for a development set. A set opens only while its manifest has that hash, so a changed device needs a new manifest and registration. Set registrations lie outside every grader identity and the toolchain revision: registering a set or changing a mark voids no admission, moves no comparison dimension and blocks no suite's resume, though changing a running suite's own hash refuses its resume. A suite of a scoring set must name every device of the set once, with one repetition, and its report names the set as `scoring_set`, frozen when the suite starts, which `suite compare` needs; its scorecard is aggregate-only. A development set may run any devices, any number of times.

**Qualify** a registered set once, with the answer vault mounted and no worker running:

```sh
python -m generative_driver benchmark set qualify --set generated-development-v1 \
  --root /path/to/set --password-file /path/to/set.password \
  --renode /path/to/renode --output /path/to/vault/set-qualification
```

The command emits and grades both reference drivers on every device, and every mutant on a sample: the first two devices by id of each level and framing. The set passes when every reference grades `passed` and every sampled mutant grades `failed` on at least the checks it targets. Before any run, each device is checked against the [generated-device interface](generated-device-interface.md) as it opens, so a device without two references whose identity operations send different commands, or without the mutants `wrong-scale`, `constant-output` and `wrong-state`, stops the command. An `unknown` grade, with undecided checks, neither passes a reference nor rejects a mutant. The set stays pending when the generated family's grader identity at the end of the runs differs from the one at their start. The command prints the status, the failures and each run's seconds, and exits 1 unless the set passed; qualify a small development set first to time a full one. It seals the record into `qualification.enc`, so requalifying never changes the manifest hash. For each run the record also names the backend, whether the grader observed a native emulator process, which tells a record made on test fixtures from one made under Renode, and the run's boot reading, the startup observation's values, read at the start of its grade; a device whose runs read differently adds a failure, `boot reading differs:<device>`, and the set stays pending. A scoring set whose record passed under the current grader identity is not qualified again by accident: the command refuses before any run, unless given `--replace-passed`. A record under another identity, a pending or unreadable one and a development set qualify as usual. `--root` and `--output` must lie outside the repository, and `--output` must be new or empty. The output receives a plaintext copy of the record and each run's evidence, which includes the reference drivers and the hidden checks, so keep it in the answer vault; qualification makes its directories readable by their owner only. Qualification runs reference and mutant drivers on the emulator; it measures no model.

`generated_sets.admitted` admits a device for a trial only when the set's record decrypts, names the set and the manifest hash its registration pins, carries the `generated` case's current grader identity and status `passed`, shows both references passed on that device, and shows the whole set natively and completely qualified ([what admission checks](generated-device-interface.md#rules-only-an-emulator-run-can-check)). Otherwise it returns the reasons, each fixed text naming at most a device id.

### Run a set as a suite

A suite names one set and its devices. Its manifest has a top-level `set`, and each entry names a device:

```json
{"schema": "benchmark-suite/1", "id": "my-suite", "version": "1", "set": "my-set-v1",
 "entries": [{"case": "generated", "scenario": "original", "case_seed": 0, "device_id": "0000d001"}],
 "repetitions": 1, "child_budget_seconds": 10800, "suite_budget_seconds": 86400, "max_active_children": 1}
```

Its options name the set's directory and key its password file by the set's name, as `suite_reporting.report_suite` keys its `password_files`:

```json
{"set_root": "/path/to/my-set-v1", "evaluator_password_files": {"my-set-v1": "/path/to/my-set-v1.password"},
 "renode": "/path/to/renode"}
```

`set_root` reaches the bench harness on its stdin, as password files do. The harness refuses the suite, before it contacts the configurator, unless the set is registered and qualified, every entry's device is admitted, and neither the configurator home, the bench home, the emulator executable nor a replay candidate lies under the set's folder. A device pin (the harness builds it and the configurator freezes it) never holds the level or framing. A trial on a set's device is resumed through its suite (`benchmark suite resume`, with the same options): `benchmark trial-resume` has no set root and refuses it. Its grade reports fresh reuse as not judged.

Workers never run with the vault open. To start a suite:

1. Open the answer vault, and start the suite with the options above. Save the `request_id` it returns; one is minted if you give none.
2. The call returns `status: awaiting-unmount` as soon as the harness holds the set in memory. No suite is frozen yet and no budget runs.
3. Close the answer vault, password files and generator volume by hand. The harness checks only the set's folder, once a second: it is out of reach when `manifest.json` is gone and the folder is gone or empty. A locked volume that still answers keeps the gate shut.
4. Repeat the start with the same `request_id` and inputs. It follows the same harness and returns the `suite_id` once the suite is accepted.

A folder still reachable after an hour ends the harness with `The vault holding set <name> was not closed in time`, and the same `request_id` starts afresh. To end a wait early, stop the harness, whose `pid` is in the `suite.json` of its `suite_dir`. If the folder is reachable again before a later trial or a continued one, the harness waits again in the same way, and only `suite.json` shows it: the suite's budget keeps running, a cancel ends the wait, and at the limit the suite blocks and can resume. A remount during a trial is not caught.

To resume a stopped suite:

1. Stop the suite or let it finish: `benchmark suite resume` refuses while its active child is queued or running, or anything of it is still stopping.
2. Open the vault and run the resume with the same options. The harness asks the configurator whether the suite can resume, then holds the set and refuses a set no longer admitted, or a record other than the one each device pin names, with the vault still open.
3. The call returns `status: awaiting-unmount` with the `suite_id`. Close the vault as above. A resume takes no claim, so follow it with `benchmark suite status`, not by repeating it.

A resume with no `set_root` does not wait: slots with a recorded verdict or an uncertain effect settle without one, and a slot that needs its device, or a stopped child's audit, fails.

A resume of a scoring suite never continues a stopped child on a new device, and refuses a child budget extension, since pairing calls any such override incompatible. A suite budget extension stays available, under the seven-day cap.

### What a scoring suite does at each ending

A suite whose frozen record holds `scoring_set` goes on past one bad trial. Development and hand-made suites stop at each stop, as they always did.

| The trial | Slot settles | Then |
|---|---|---|
| Passes, fails or is unknown, including a spent child budget, no driver, a grade unscored for `integrity:`, or a safety, integrity or model stop | `finished` | Next slot |
| Stops for the host or an operator with budget left, is cancelled alone, or leaves no evidence | `faulted` | One rerun at the end |
| Never got a run: `admit-refused`, `provider-failed` or `start-refused` | `faulted`, with that word | One rerun at the end; the full text stays in `trial.json` |
| Ran, but grading hit an evaluator host fault | `faulted`, keeping its run | One regrade of the same driver at the end, then a rerun if it is unscored again |
| Ran a command naming the set's folder | `flagged` | Not graded, no rerun, outside the pass rate |

A rerun is a fresh trial in a new trial folder, under a new request id, with the same frozen pin and the full child budget. Once no slot is pending, faulted slots rerun in ordinal order. The rerun's result stands: a rerun that faults counts as a fault, after one regrade if its grading hit a host fault, and there is no third attempt. It stays one row, with the first attempt kept as history, and the scorecard's `reruns` block gives that attempt's cost. The report stays `provisional` until every rerun is done.

No limit stops a run of faults: on a broken host every slot and every rerun faults, the suite ends `completed`, and each counts as a fault.

These still stop the suite until a resume: an uncertain effect, even with a spent budget; a child identity mismatch; a configurator the harness cannot reach; a password or root missing at a slot that needs it; the vault not closed in time; the suite's budget running out; a cancel of the suite; a failed call to record a fault or a flag; an emulator stop that cannot be confirmed; a dead harness; and a completed child still awaiting its grade, or whose grade could not be sealed or posted.

The audit reads Codex transcripts only, and only commands that name the set's folder in their text. It catches slips. A flagged trial's stored findings hold each transcript's name and its command's SHA-256, never the command or the root. `report_suite`'s `audit_paths` add to those findings and never overwrite them.

### Sealing a scoring suite's trials

Each concluded trial of a scoring suite (finished, faulted, flagged or never run) is sealed in place under the set's password: its run folder but for `benchmark/execution.json` and `benchmark/inputs/`, its trial folder but for `*.enc` and lock files, and its temporary interpret workspaces. `trial.json` becomes a stub with status `sealed`, and the run shows `sealed: true` and refuses `resume` and `attach_device`. A failure to seal stops the suite with a reason that names no path, and a resume seals what is left before any worker starts. So a scoring suite's resume needs its password file, and a new scoring suite refuses while another suite of its set has a trial still readable. Development and hand-made suites are not sealed.

Nothing is sealed or deleted until the password is proved: a sweep or `store` with anything left to seal first needs one evaluation token of the suite to unlock, or a harness that holds the set, and a trial with a run and a start needs its own token, whose loss stops the suite. A limit: a run with no trial folder and no token left in its suite relies on a resume given the set root. A suite that cannot resume and has nothing to prove on cannot be stored, and that is a hand repair: `store` keeps refusing, and the person restores or removes those files.

With the vault open, once the suite is settled:

- `benchmark suite store <suite id> --password-file <file> --vault-folder <folder>` seals what is left, then moves each seal under `<folder>/sealed-trials/<suite id>/`, unlocking each copy before its original goes. It takes a completed suite, or a cancelled one with nothing stopping. Stubs and other `*.enc` files stay. A repeat finishes a move cut short, and it never replaces a vault file that differs.
- To abandon a suite, cancel it, then store it. That seals every readable trial, an unsettled slot's too. A later resume that reaches such a slot stops at the failed restore.
- `benchmark suite scorecard` of a scoring suite needs `--password-file` and `--set-root`, and `--vault-folder` once `store` has run. It refuses while the suite is queued or running or anything of it is stopping, and it stays aggregate-only.
- `benchmark suite report` of a scoring suite needs `--password-file`, `--set-root` and `--vault-folder`, and refuses in the same states, or unless the set root and `--output` lie under the vault folder, since the report holds per-trial data. `benchmark suite compare` still reads it, as only its counts leave.
- `benchmark suite unseal <suite id> --trial <run id or trial id> --password-file <file> --vault-folder <folder> --output <folder under it>` writes one sealed trial out for a person checking a flagged command. It refuses any other location.

`gd report <run>` on a sealed run writes files into the run's folder, and the next sweep seals them. `suite_result`, `suite_events`, `suite_status` and `suite.json` are unchanged and can show per-trial data.

## Add, unselect or retire

**Add** a lineage by updating a versioned catalog, with blockers until admission is complete; implement and qualify its registered case before claiming execution. Never substitute a synthetic development case for an upstream lineage.

**Unselect** a lineage for one experiment with repeated `--include LINEAGE` or `--exclude LINEAGE REASON` while creating a new plan. Its catalog slots remain visible as excluded, and full-catalog demonstrated coverage retains them. Preserve the original plan/report when a completed trial failed.

**Retire** a case from future experiments by publishing a new catalog/selection and documenting why. Preserve its historical case/lineage/mission IDs, static registration and retained assets/evidence needed to read old pins and reports. Do not rename another case into its ID or delete rows from saved plans. Removing a registry entry is a compatibility change, not an experiment-selection operation.


## Native Zephyr execution

Prefer authentic host-compiled application/protocol code when a qualified board backend is unavailable. For the Zephyr pilot, the interrupt-capable native TTY driver and GPIO emulation replace board I/O; application handlers, parsing and state logic remain upstream. The final `zephyr.exe` (retained as `firmware.elf`) is the executable, whereas the intermediate `zephyr.elf` is not. Supply the agent with the bytes actually executed and disclose its symbols/DWARF, substitutions and hardware gaps.

The optional backend requires Linux (a Linux VM is suitable on macOS/Windows). Its clock policy is `host-clock-with-process-pauses`: the process pauses between calls, while `--rt` catches up to the continuing host clock on resume. The adapter verifies a private executable copy against the pinned source before launch/reset. It preserves source bytes and does not retry uncertain writes. [Measured results and setup failures](verification.md#native-modbus-pilot--2026-10-27) remain distinct from these operating instructions.

Run these commands in a writable evaluator directory with an installed package,
the clean pinned Zephyr source, host GCC, west and Zephyr build dependencies.
`pyelftools` is needed only when authoring the native observation recipe. Password
files belong to the evaluator; workers receive neither their paths nor contents.

```sh
python -m generative_driver benchmark corpus build --case zephyr-modbus-v1 \
  --backend native-sim --source /path/to/zephyr --compiler /path/to/gcc \
  --west /path/to/west --output native-build

python -m generative_driver benchmark corpus prepare-execution --build native-build \
  --password-file /path/to/evaluator.password --output native-intake

GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-intake" \
  python -m generative_driver benchmark corpus exercise-execution --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --output native-observations

python -m generative_driver benchmark corpus exercise-execution --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --renode /path/to/renode --output renode-observations

python -m generative_driver benchmark corpus compare-execution \
  native-observations/observations.json renode-observations/observations.json --output comparison.json

python -m generative_driver benchmark corpus prepare-execution --build native-build \
  --password-file /path/to/evaluator.password --native-record native-observations/observations.json \
  --reference-record renode-observations/observations.json --output native-qualified-inputs

GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-qualified-inputs" \
  python -m generative_driver benchmark truth calibrate --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --ghidra-home /path/to/ghidra \
  --java-home /path/to/jdk --output native-calibration
```

Stop if comparison or calibration fails. Native calibration requires the measured
comparison records, checks the backend and executed image, runs two distinct
reference drivers through diagnostic/final episodes, and rejects the declared
mutants. Pending metadata cannot admit an agent.

After successful calibration, copy its `case.json` to
`native-qualified-inputs/cases/zephyr-modbus-v1/case.json` and its
`zephyr-modbus-v1.enc` to
`native-qualified-inputs/groundtruth/zephyr-modbus-v1.enc`. These are external
resources; installed resources and previous results stay unchanged.

Use the existing runtime configuration and trial command with this resource root:

Install the complete matching Codex distribution, including its tool-host helper
and Bubblewrap resource. The Linux environment must support the worker sandbox's
namespaces and mounts. Verify these before spending model inference on a trial.

```sh
GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-qualified-inputs" \
  python -m generative_driver benchmark run --case zephyr-modbus-v1 --executor codex \
  --password-file /path/to/evaluator.password --home "$PWD/native-state" \
  --output "$PWD/native-trial" --ghidra-home /path/to/ghidra --java-home /path/to/jdk \
  --approve-emulator-tools --wait
```

The case keeps the same lineage and mission. `execution_setup.method=native-sim`
and the native ELF hash distinguish its execution from the ARM variant. The
generic software-emulation execution category alone does not identify a backend.
All six handoffs, a fresh package-only mission and the final evaluator verdict
remain separately reviewable. One trial establishes feasibility for that trial;
it does not estimate a success rate across firmware families.

Implementation references: [native simulator](https://docs.zephyrproject.org/4.2.0/boards/native/native_sim/doc/index.html),
[pinned native TTY driver](https://github.com/zephyrproject-rtos/zephyr/blob/413b789deb391d3a37d06b463288a5fe765ee57e/drivers/serial/uart_native_tty.c),
[pinned RTU application](https://github.com/zephyrproject-rtos/zephyr/blob/413b789deb391d3a37d06b463288a5fe765ee57e/samples/subsys/modbus/rtu_server/src/main.c).
