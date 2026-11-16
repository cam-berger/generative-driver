# 05. driver-package

## Intent

A driver package carries the data that describes the driver: the model, the driver contract (the public description a fresh agent reads), the protocol, the probe evidence and the MCP catalog. It also carries a copy of the kit's runtime, so that it runs on its own. Before the kit uses a package, it compares that copy byte for byte with its own runtime and refuses on any difference. A kit upgrade, even a comment edit in the runtime, therefore stops every earlier driver from working through the kit: device sessions, fresh reuse, live tests and checks.

The driver contract is also mostly empty in practice. Nothing tells an interpret worker that the contract exists, so every generic package ships the description the kit imports from the model: generic sentences, a blanket "not documented" caveat on every operation, and units copied from the model where the worker happened to write one. A unit the firmware does not state reads "Unit: unknown.", the same as a unit nobody looked for.

After this Feature, the kit runs its own installed runtime over a package's data whenever it uses a package, so earlier drivers keep working after an upgrade. The kit keeps reading every data format it has written into a package, and an earlier package keeps the catalog it was built with. In generic runs, the interpret worker gets a guide and a tool for writing the contract, and records a unit the evidence does not state as not stated. The fresh agent then sees that in the catalog. A repair that leaves part of the contract behind its model costs only that part: the package keeps the worker's descriptions wherever they still match.

The package keeps its own copy of the runtime, which standalone use and the benchmark's grader still run. Benchmark runs keep today's interpret instructions, because the benchmark and its tests are not edited in this Run.

## Behaviours

- **05-B1** — Wherever the kit runs an interface package (`interface-package/1` or `/2`), it runs its installed runtime and its own driver entry over the package's data files. The places are:
  - a managed device session's open and each execution, including a session opened before the kit changed;
  - a generated MCP tool call in fresh reuse;
  - `emit_test_live`;
  - the replay in `emit_check`.

  The data files are `driver/model.json`, `probe.json` and, in format 2, `driver/contract.json`, `driver/protocol.json` and `mcp-tools.json`. The kit never imports the Python the package carries. A package is no longer refused because that Python differs from the installed kit's, or names other modules.
- **05-B2** — A package's integrity is checked as today. Every file, its bundled Python included, must match the package's manifest, and the manifest must match `manifest.sha256` and any pin the caller gives. A package that fails is refused as today, so a bundled copy that was altered after emission is still caught.
- **05-B3** — `emit_check` keeps `runtime_current`: whether the package's bundled Python equals the installed kit's.
  - When it is false, `emit_check` now replays the package's evidence on the installed runtime. It reports `replay_status` `passed` or `failed`, and `replay_reason` says that the replay used the installed runtime because the package carries another copy. `ok` is true when integrity holds and that replay passed.
  - The generic emit gate and the benchmark's emit gate still require `runtime_current` for a run's own new package. The generic gate's rejection reason says that the package carries a runtime other than this kit's.
- **05-B4** — The installed runtime reads every data format a kit has written into an interface package:
  - interface models `interface-model/3` to `/6`;
  - `driver-contract/1` and `driver-protocol/1`;
  - MCP catalogs of renderer version `2` and of version `3` (05-B9).

  A package is verified against the rules of the formats it names. An earlier package's catalog therefore still verifies after the rendering for new packages changes, and fresh reuse serves it unchanged. A package that names a format or renderer version the installed kit does not know is refused, with a reason that names it.
- **05-B5** — The tests keep a package that the kit emitted before this Feature's first change, from the smoke's stand-in model with live evidence from the stand-in device, never rewritten. With the kit after this Feature, all of these hold for that package:
  - `emit_check` gives `integrity_ok` true, `runtime_current` false and `replay_status` `passed`;
  - a managed device session over it opens, and an execution of `measure` against a stand-in device returns `ok` true with its temperature;
  - fresh reuse over it is served its `mcp-tools.json` unchanged, and a generated tool call runs.
- **05-B6** — A generic run's interpret workspace holds the driver contract guide as `CONTRACT.md` and the contract tool as `contract.py`, both sealed inputs. The stage's `TASKS.json` instructions tell the worker to write `contract.json` as `CONTRACT.md` describes. A repair's instructions say to keep `contract.json` in step with the revised `model.json`. The guide teaches:
  - the contract is all a fresh agent reads about the driver;
  - start from `contract.py draft` and replace each placeholder;
  - describe each operation, parameter and output by its observable meaning, citing evidence in caveats;
  - write units as UCUM codes such as `Cel`, `hPa`, `%`, `V`, and `1` for a count or ratio. An output's unit must equal its decoder's `unit` in `model.json`, because the contract's unit is projected into the model;
  - when the evidence states no unit for a number, write `null` and add a `unit_not_stated` uncertainty whose message and evidence references say where the evidence was searched. Never guess a unit;
  - run `contract.py check` before finishing.

  Benchmark runs get none of this. Their interpret workspace has neither file, and their brief, model document, validator, `TASKS.json` and prompt are unchanged.
- **05-B7** — `contract.py`, run in the interpret workspace:
  - `draft` writes `contract.json` from `model.json`, as the kit imports it today: generic descriptions, `semantics_unknown` caveats and the model's units. It refuses to overwrite an existing `contract.json`.
  - `check` reports whether `contract.json` describes `model.json`: whether the contract, with the protocol the kit derives from the model, compiles back to the model. A failure names each operation that does not match and its first difference.
  - `check` also lists, as advice, each numeric output whose unit is `null` with no `unit_not_stated` uncertainty, and each operation, parameter or output that still has its draft description. It exits nonzero only when the contract does not compile to the model.

  Neither command needs anything outside the workspace and the Python that runs it.
- **05-B8** — In a generic run, the configurator builds the contract that emit packages from the `contract.json` in the interpreted model's folder, with the protocol derived from the model.
  - An operation's contract entry matches when, compiled with that operation's derived protocol, it gives exactly the model's operation and passes the contract's own checks. Every operation whose entry matches keeps the worker's description, caveats, preconditions and units.
  - An operation whose entry does not match, or that the contract leaves out, gets the entry the kit imports from the model.
  - After that, the preconditions are checked across the merged contract. An operation whose precondition no longer holds there, for example because it observes an operation that got the imported entry, gets the imported entry too, preconditions included. No precondition is dropped from an entry that keeps its description.
  - The contract's `device`, `identity` and `provenance` always come from the model.
  - A `contract.json` that cannot be read as a contract gives every operation the imported entry.
  - The emit stage's notes name each operation that got the imported entry, and why. They also say when the model came with no `contract.json`.
  - A `contract.json` that matches entirely is packaged as the same JSON value, serialized as `emit_package` serializes today.

  `emit_package` called with a model folder that holds both `contract.json` and `protocol.json`, as benchmark runs and tests call it, keeps today's rule: a pair that does not compile to the model is refused.
- **05-B9** — New packages' catalogs use renderer version `3`. It renders as version `2` does, with one exception: a `null` unit on a value that carries an uncertainty of code `unit_not_stated` reads `Unit: not stated by the device.` instead of `Unit: unknown.`, and its warning follows as today. A `null` unit without one still reads `Unit: unknown.`
- **05-B10** — In the smoke, the scripted interpret worker writes `contract.json` with `contract.py`. It gives one operation its own description, and gives the `temperature` output a `null` unit with a `unit_not_stated` uncertainty. The stand-in model's `temperature` decoder loses its `degC` unit, because the stand-in device's `T:21.5` states none. The catalog served to the smoke's reuse stage shows that description and `Unit: not stated by the device.` for `temperature`. The smoke still passes, on the same conditions as today; a test checks the catalog in a kept smoke home.

## Interface and seams

The package format stays `interface-package/2`, with the same manifest fields. The bundled copy and the package's own command line stay as they are. How the kit runs its runtime over a package's files is the builder's choice, within these limits:
- a child process with the existing 300-second limit for executions, and 260 seconds for the replay;
- no import path that reaches the package's Python.

Renderer `2` stays in the runtime beside `3`. `verify_package` chooses by the catalog's `renderer_version`. The constant names, the guide's resource path and how `contract.py` reaches the runtime copy already in the workspace are the builder's choice. The names a worker sees are fixed: `CONTRACT.md`, `contract.py` with `draft` and `check`, the code `unit_not_stated`, and the text `Unit: not stated by the device.` `resources/docs/DRIVER_CONTRACT.md` stays the format reference and gains the renderer `3` rule and the `unit_not_stated` code.

New configurator behaviour (the generic interpret additions and the emit merge) goes in a module of its own, which the configurator calls. Benchmark code and benchmark tests are not edited. Among them, these must keep passing as they are:
- `tests/test_package_benchmark.py`, which asserts `runtime_current` false and the benchmark gate's rejection;
- the benchmark test in `tests/test_owner_generated_integration.py`, which asserts that a mismatched pair is refused;
- the benchmark family tests, in which the grader runs a package's bundled copy.

Tests attach in these places:
- 05-B1 to 05-B5: `call_tool('emit_check' | 'emit_test_live')`, the configurator's device session requests, and fresh reuse's generated tool calls;
- 05-B6 and 05-B8: the configurator's stage preparation;
- 05-B7: `contract.py` run as a child process in a prepared workspace;
- 05-B10: the smoke command.

The 05-B5 package lives in `tests/fixtures/earlier-package/`, beside a README that names the commit that emitted it and the command used. The builder emits it at this Feature's base commit, before any change. The device tests use the smoke's stand-in device.

Docs:
- a new ADR, `docs/adr/0008-…`, records the decision: the kit runs its installed runtime over a package's data, and keeps every format version it has written readable, while the package's own copy serves standalone use and the grader;
- `docs/extending-drivers-and-mcp.md` states that compatibility rule;
- `docs/codex.md` says that earlier drivers keep working after a kit upgrade;
- `.scratch/benchmark-review/issues/13-kit-changes-not-adopted.md` records three things: benchmark interpret workers are not taught the contract, `emit_check` now replays a package whose copy differs, and renderer `3` changes new catalogs and the toolchain revision.

## Out of scope

- Register packages (from `interface-model/1`, carrying worker-written conversion code), which keep today's currency check.
- Recording which kit built a package, or any change to the package format or manifest.
- Teaching benchmark workers, and any change to the shared interpret brief, the model documents, `validate.py` or the interpret prompt.
- Checking that a unit is a valid UCUM code, a unit line for values that are not numbers, and units for parameters beyond what the guide says.
- A configurator check of the contract at the end of interpret; the worker's `check` and the emit notes cover it.
- Re-rendering an earlier package's catalog with new wording, and running a package newer than the installed kit.
- Changes to how a package runs on its own, or to how the grader runs it.
- Loading optional transport libraries (for example the serial library) in managed sessions; the builder lists what it sees under Noticed.

## Deviations
- The kit checks the whole package against its manifest, copies it, then builds a run folder from its installed `interface_runtime` and driver template plus the package's data files, and a `-I -B` child runs it (`-I -S -B` in sessions) — the builder's choice the Spec allows; nothing puts the package's Python on the import path, and the 300 s and 260 s limits are unchanged.
- A session's `open` keeps the package's verified copy, and each execution rebuilds the run folder from it and the kit installed at that moment — this is how 05-B1 covers a session opened before the kit changed, and 05-B2 still catches an altered kept copy.
- `emit_test_live`'s `manifest_sha256` names the package's own manifest, not the run folder's — it keeps the value the reuse verdict compares against.
- A model format the kit does not know is refused by name, and `is_interface_package` accepts any `interface-package/*` so an unknown package format is refused by name rather than sent to the register-package refusal — 05-B4.
- New refusal wording names the unknown package, catalog, renderer, contract, protocol or model format — 05-B4.
- `contracts.py` gains public `describes` and `check_preconditions` — they serve 05-B7 and 05-B8; new packages bundle them like any runtime change.
- `emit_check`'s `replay_reason` reads "The replay ran on the installed runtime, because the package carries another copy of the runtime" — it carries the meaning 05-B3 asks for.
- `_emit_check_failure` is shared with the benchmark's emit gate, so its rejection text for a package with another runtime changes too — it traces to 05-B3; benchmark verdicts do not change, no benchmark code or test changed, and issue 13 records it.
- Renderer `3` is the default, and the `unit_not_stated` text applies to every known version except `2` — renderer versions are limited to `2` and `3`, so this is exactly 05-B9; SDK application tools also render as `3`.
- 05-B8's notes go under `configurator.driver_contract`; an operation the contract describes but the model lacks is left out with a note; a merged contract that still fails the final compile falls back to the full import; emit preparation writes the model-derived `protocol.json`; a folder whose model the runtime refuses is left as it is — all within the 05-B8 rule.
- `contract.py` prints JSON reports, takes an optional model-folder argument and reaches the runtime through the workspace's `_toolkit`; the guide and tool live in `resources/toolchain/contract/`; `test_configurator`'s interpret input lists gain both files; `MANIFEST.in` grafts `tests/fixtures`; docs beyond the Spec's list were kept accurate — the builder's choice or a direct consequence, with no scope change.
- 05-B2's RED comes from a temporary mutation, and two guard tests (05-B6 benchmark workspaces, 05-B8 `emit_package`'s refusal) have none — 05-B2 keeps today's behaviour, so a mutation is the right proof; 05-B6 and 05-B8 have other tests with valid RED.
- The upgrade test runs its child with `PYTHONPATH` set to a copy of the kit, not `package_env()` — it has to run a different kit, and an assertion proves which kit ran.
- The fresh-reuse tests run through `scripted_stages` — the fresh-reuse seam the Spec names, without a model run.
- Fix round 1: eight lines wrapped to 120 columns; four stale docstrings and comments rewritten; three module-only names made private; the new tests' temporary folders tolerate cleanup errors — no change to behaviour; addresses the Standards and the Windows cleanup risk.
- `tools.json` is left stale and unedited — benchmark workers read it and the Spec keeps their instructions unchanged; it is listed in Noticed.
- Nothing ran on Windows — the Windows CI run is the first check there.
