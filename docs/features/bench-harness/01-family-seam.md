# 01. family-seam

## Intent

A grader identity is only honest if editing one family's code cannot void another case's admission. Today it can: six core modules and `execution.py` branch on case and family names, and `upstream.py` and `native_firmware.py` sit in every identity. So an edit to Zephyr's code would void a generated set's qualification. This Feature gives the grading core one way to reach a family, so the core names none, and each family's code changes only the identities of the cases that use it. Grading results do not change.

The core is `grader_closure(families=())`. Nothing is admitted on main, so moving every identity once here costs nothing; shipped calibration records stay stale, as they are today, until the user requalifies at pinning.

## Behaviours

- **01-B1** — `family_for(case_id)` returns a `Family` for every registered case, and the `Family` class has one attribute per hook in the table below. An unregistered id raises `ValueError`.
- **01-B2** — `family_from(registration, hooks)` raises `ValueError` naming the case and the hook when: a hook name is not in the table; a graded case lacks a required hook; a graded case declares both `execute` and `build_plan`, or neither. A graded case is one whose `adapter_key` is `emulator-v2`.
- **01-B3** — A family that omits an optional hook gets the table's default.
- **01-B4** — A case with no module gets every default, and calling a required hook on it raises `ValueError` naming the case. So does `tq9_legacy` for any required hook it does not declare, and reading `required_mutants` on either raises the same way. `reuse_mission` for a case with no module reports `passed: None`, as it does today for `bme280`.
- **01-B5** — For a family that declares `build_plan`, `Family.execute` runs that plan for the given `phase` through `execute_plan`. A family that declares `execute` runs its own. `reference_execute` and the diagnostic path of `run_reference` have one call path and no `hasattr`, and a plan family's records, contract and grade are unchanged.
- **01-B6** — No module in the core names a registered case or family, with no exemptions. The test reads the names from the registration table and walks the core's source with `ast`, ignoring docstrings. The names are each row's `id`, `family` and non-null `native_module`, each also in its underscore form. It fails on a string constant equal to a name, and on a string constant or identifier containing a name; `generated`, an ordinary word in this codebase (`generated_mcp_reuse`), is matched by equality only. `renode` and `native-sim` are backends, not names, and stay allowed.
- **01-B7** — `upstream`, `native_firmware` and the native comparison code are outside the core. Editing any of them changes `zephyr-modbus-v1`'s identity and leaves every other case's unchanged, shown on a copied package through `grader_identity(root, case_id=...)`.
- **01-B8** — Editing `tq9_legacy` changes only `tq9`'s identity. Editing `families.py` changes every case's.
- **01-B9** — In the whole grader closure, `families.py` is the only module that calls `import_module` or `__import__` to load code. `emulated_actions.py:149`'s `__import__('time')` becomes a plain import.
- **01-B10** — `resolve_case`, `pin_case`, `method_for` and the catalog row of `tq9`, `bme280` and `setup-smoke` return exactly what they return today, checked against literal expected values.
- **01-B11** — `instruments.units`, `instruments.public_observation` and `reuse_mission` return today's values for `tq9`, `tq9-v2` and one other family.
- **01-B12** — The provider's descriptor offers development conditions for `sampled-sensor-v1` when its recipe has `source_q4`, the same catalog as today, and none for any other case.
- **01-B13** — A case's hydration comes from its family, never from the truth's own `schema` field: a family truth given to a case with no `hydrate_truth` comes back unlocked and unhydrated.
- **01-B14** — `require_calibration` accepts and refuses the same evidence as today for a tq9-shaped record and a family-truth record, through `admission_valid`. A family with the default `admission_valid` is refused with the existing "missing, stale, or incomplete" error.
- **01-B15** — `validate_calibration` applies a family's extra rules from its hooks alone: `parameter-store`'s required scenarios, `zephyr-modbus`'s subset match on a mutant's failed checks, `tq9`'s `analysis` input commitment, `sampled-sensor`'s extra inventory file.
- **01-B16** — Each caller starts a session through `Family.start_session`. The default starts Renode and refuses any other recipe backend with `ValueError`, as does the default `validate_execution` for a manifest whose method is `native-sim`. `zephyr-modbus-v1` with a `native-sim` recipe starts `NativeFirmwareSession`. The test mocks the session classes, since native sessions need Linux.
- **01-B17** — The provider still loads nothing that decrypts: in a fresh process, after importing `device_provider` and calling `family_for` for each registered case, `sys.modules` holds none of the modules `test_the_provider_imports_nothing_that_decrypts_truth` forbids. Family modules import such code inside their hook functions.

## Interface and seams

**`benchmark_support/families.py`**, new and in the core, is the only way the grader closure reaches a family.

- `family_for(case_id: str) -> Family` loads the module the row's `native_module` names and calls `family_from`.
- `family_from(registration: CaseRegistration, hooks: dict) -> Family` checks and fills. Tests attach here for malformed families.
- `Family` is a frozen dataclass with one attribute per hook. `required_mutants` is a property over a private field, so reading it can raise.

A family module declares a module-level `HOOKS` dict, hook name to value. Helpers stay private to it, with one exception: `reference_calibration` imports tq9's moved helpers directly from `tq9_v2`, because it is calibration code outside every identity and those helpers are no part of the grading interface. Names `registrations.native_family_for`, `registrations.family_module` and `instruments.family_for` are removed, and their callers use `family_for`, including those outside the core (`reference_calibration`, `upstream_qualification`). Lookups by family name become lookups by case id.

| Hook | Graded case | Default | Replaces |
|---|---|---|---|
| `contract(pin, truth, phase)` | required | raises | |
| `observations(raw, canonical_task_result, inputs)` | required | raises | |
| `reuse_passed(events, capabilities)` | required | raises | `case_assets.py:104` |
| `required_mutants` | required | raises on read; `frozenset()` is not a default | `REQUIRED_MUTANTS` |
| `execute(session, invoke, pin, contract, truth, evidence_path, *, phase, healthy_device_failures)` | this or `build_plan` | runs `build_plan` | `evaluator.py:92`, `reference_calibration.py:105` |
| `build_plan(pin, truth, phase)` | this or `execute` | raises | |
| `hydrate_truth(truth)` | optional | returns `truth` | `evaluator.py:39`, `registry.py:281` |
| `admission_valid(pin, truth, record)` → bool | optional | `False` | `registry.py:293-350` |
| `validate_phase(phase)` | optional | does nothing | `calibration.py:301` |
| `required_scenarios` | optional | `frozenset()` | `calibration.py:117` |
| `mutant_checks_may_exceed_expected` | optional | `False` | `calibration.py:157` |
| `input_commitments` | optional | the `inventory` set | `calibration.py:103` |
| `inventory_files` | optional | `frozenset()` | `calibration.py:25` |
| `instrument_units()` | optional | `observations.monitor_units` | `instruments.py:21-25` |
| `public_observation(raw)` | optional | projects `observations` onto the units | `instruments.py:31-37` |
| `development_conditions(recipe)` | optional | `None` | `device_provider.py:149`, with `SENSOR_CONDITIONS` |
| `start_session(*, recipe, **arguments)` | optional | Renode | `execution.py:26` |
| `validate_execution(manifest, truth)` | optional | refuses `native-sim` | `execution.py:43-53` |

**Moves.** tq9's admission code (`tq9_candidate`, `tq9_execution_inputs`, `validate_record`, `CORE_MUTANTS`, and the `registry.py:335-350` branch) goes to `tq9_v2`. The shared family-truth admission (`registry.py:293-333`) goes to `family_support`, and its three families bind it. `upstream.py` is imported only by `zephyr_modbus` and `upstream_qualification`. `compare_executions` and `load_plan` move to a new `native_comparison.py`, imported by `zephyr_modbus`, `rehosting` and `upstream_build`. `evaluator.py:75`'s default of `tq9-v2` goes; a pin must name its case.

**Legacy.** `tq9`'s row names a new `tq9_legacy` module holding `instrument_units`, `public_observation` and `reuse_passed` (the moved `mission_done`). `bme280` and `setup-smoke` have no module. What the core branched on by their names becomes `CaseRegistration` fields: grading method, the evidence track, approval scope, effects and scope of `registry.py:218-221,260`, whether a pin needs calibration or a case file, and the one-image exemption of `registry.py:47`. The builder names the fields. Every row carries them, so every identity moves once. A schema stays `benchmark-case-registrations/1` and `benchmark-grader-identity/2`.

**Tests attach** at `family_for`, `family_from`, `grader_identity(root, case_id=...)` on a copied package, `grader_closure`, `pin_case`, `resolve_case`, `require_calibration`, `validate_calibration`, `reference_execute`, `instruments`, `reuse_mission` and the device provider's descriptor. Existing tests of the removed names change to the new interface; their expected values do not.

**Docs.** `docs/benchmark-case-authoring.md` gains the hook table as the family interface and loses its "As built" paragraph; its closure instructions name `families.py`. Correct spec.md's "As built" bullet, whose untangling is now done. `docs/benchmark-corpus.md:135` and issue 01:31 become true and stay. Update `CONTEXT.md` with **family hook**.

## Out of scope

- The roughly 22 sites naming a case or family outside the core (`harness.py:201`, `suites.py:69`, `suite_reporting.py`, `reference_calibration.py` and others): they move no identity. Report them, except where a removed name forces a call-site edit.
- Generated-device admission and pinning, which stay refused here and belong to Features 03 and 04.
- Removing the `truth_schema` field. `upstream.py:22` still reads it.
- Any change to a contract, check id, grade or stored truth format.
- Any real run, password or vault. Requalification is the user's, at pinning.

## Deviations

- CONTEXT.md not edited by Build — the orchestrator added the row, which meets the Spec's Docs line.
- Committed as it went, with e99a7e7 not green alone — process only, and the Feature diff is unaffected.
- Registration fields named `grading_method`, `execution`, `evidence_track`, `approval_scope`, `default_effects`, `default_scope`, `requires_calibration`, `has_case_file`, `single_image`, with `default_effects` a JSON list — the Spec leaves the names to the builder, every row carries them and the schema string is unchanged.
- `inventory()` takes a case id and `calibration._FAMILIES` holds case ids — follows "Lookups by family name become lookups by case id".
- `input_commitments` holds the whole set of names — a fair reading of "the `inventory` set"; it replaces `calibration.py:103` exactly.
- A no-module case raises "Unknown native family: <case>", and `tq9_legacy` raises "Case declares no <hook> hook: tq9" — B4 asks for a `ValueError` naming the case, and `reuse_mission`'s reason is kept.
- `run_reference` loses its tq9-v2 default — same rule as `evaluator.py:75`, on a line the removed name forced to change; all three source callers name the case.
- `REQUIRED_MUTANTS` constants stay in the family modules — the core and calibration code read them through the hook; no scope change.
- `calibration.input_hashes` and `REQUIRED_SCENARIOS` stay put — the Spec's move list omits them and neither names a family, so B6 holds.
- New public names `execution.start_renode`, `family_support.admission_valid`, `family_support.hydrate_family_truth`, and `validate_setup` needs `manifest['id']` — needed for the default session, the shared binding and B16's per-case validation.
- `test_the_generated_identity_covers_the_core_and_the_generated_family` also expects `benchmark_support.missions` — direct result of B7 moving `upstream` out of the core, and it serves the Intent.
- `generated.execute` gained unused `pin` and `phase` — the Spec's hook table sets this signature.
- Existing tests changed for the new interface — each change traces to a removed name or to B13/B16; expected values hold apart from the `missions` item above.
- `NativeFirmwareSession` only mocked, nothing run on Linux or Windows — B16 says the test mocks the session classes.
