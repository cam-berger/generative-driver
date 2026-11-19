# Spec 12: firmware-bench

## Intent

For the owner, who wants firmware bench adapted now that Generative Driver stands alone. The owner's words of 2026-11-18: "all I want to do is provide the ability to run the driver on example firmware on firmware-bench, and the bench evaluates generative-drivers ability to do that on various examples", and, after Feature 11: "go ahead with firmware-bench once the hosted run passes".

After Feature 11 the benchmark's files are parked in `firmware-bench/`. Its code does not import from there, its 73 test files run nowhere, its documents describe the tool and the benchmark together and link files that are gone, and the code, resources and tests of two dropped cases remain.

This is the last Feature of the Run. It makes the parked folder a package that works on its own, deletes what is outdated in it, and has the test command and the hosted tests run it again.

What is different afterwards:

- `firmware-bench/` holds the package `firmware_bench`, with a build file of its own. It uses the tool as an installed package, through the client interface.
- Its tests pass from its own folder against the two installed packages, and by the test command.
- The code, resources and tests of the two dropped cases, the benchmark's finished plans and notes, and one decision record are deleted.
- It has a front page, five guides, five decision records, a glossary, its rules, its open issues, a licence and a notice of its own. They describe the benchmark as it is.
- The hosted tests have a second workflow. It builds both packages and tests the benchmark on a copy of its folder outside the checkout.
- A hosted job can no longer wait for hours on the fault that stopped one job of Feature 11.

Decisions taken in this Spec. Its Approval covers them.

- The benchmark's modules keep their names and their places relative to each other: `firmware_bench.benchmark` and `firmware_bench.benchmark_support.<module>`. A flatter layout, and a redesign of how the benchmark runs, grades and reports, are work for the benchmark's own repository.
- Kept, though a reader may call them old: the benchmark's paths for a device the operator binds, because a case on real hardware is to be rebuilt; its readers of earlier report formats; and the case `tq9-v3`, which is registered with a mission and has no firmware or answer key yet.
- The benchmark's hosted tests are a workflow file of their own, `.github/workflows/firmware-bench.yml`. So the tool's workflow keeps its one job, and removing the benchmark from this repository stays a short list: the folder, that file, and the places 12-B9 names.
- The fix of 12-B10 touches the tool's side: one test helper, one new tool test, and one line of the tool's workflow with the hash a tool test holds of it. It is here because the fault can stop any hosted job of this Feature too. It is a fault of a test helper, not of the tool. No file under `src/` changes in this Feature.
- The benchmark's folder gets no `uv` lock file in this Run.
- The Reviewer's count of changed lines is not held to a limit. The new module path alone stands in 85 files.

Two facts the owner should know. This Feature does not change them.

- `firmware-bench run --case setup-smoke` checks an installation. It validates a model, emits a package and replays a recorded reply. It starts no service, worker or grader, and shows nothing of the six stages.
- Every other case needs its evaluator password, the emulator, a qualification under the current grader, and a model or a saved candidate. No qualification stands today, since Feature 06 changed the grading code. So the first graded result after this Run needs a fresh qualification of the case first.

Conventions.

- Line numbers are those of commit `7cf6c17`. A file that Feature 11 moved has the numbers it had before the move, but for `firmware-bench/README.md`, which gained two lines at its top.
- A path that begins `benchmark_support/`, `resources/` or `benchmark.py` is under `firmware-bench/src/firmware_bench/`. A test file named without a folder is under `firmware-bench/tests/`.
- "Goes" means the text is cut. Nothing is kept elsewhere unless a rule says where.
- The tool is the package `generative_driver` with `interface_runtime`. The client interface is what `docs/client-interface.md` lists.

## Behaviours

- **12-B1** — The parked code is the package `firmware_bench`.
  - `firmware-bench/src/firmware_bench/__init__.py` is new and holds one docstring: `"""Firmware bench: runs Generative Driver on example firmware and evaluates the driver it emits."""`
  - `firmware-bench/pyproject.toml` holds the text given under `The package`.
  - The modules keep their names, their places relative to each other and their relative imports of each other.
  - Under `firmware-bench/`, every text that spells `generative_driver.benchmark` as a module path spells `firmware_bench.benchmark`: imports, `patch` targets, `-m` launches, program names and documents. This covers `generative_driver.benchmark_support` too. Outside `firmware-bench/` no file changes for this: the scans in the tool's tests and the Specs under `docs/features/` keep the old spelling.
  - These stay as they are, because they are data or the benchmark's own names: the schema `generative-driver-groundtruth/1`, the environment variables `GENERATIVE_DRIVER_BENCH_ROOT`, `GENERATIVE_DRIVER_CASE_ROOT`, `GENERATIVE_DRIVER_HOME` and `GD_TEST_HARNESS_FIXTURE`, the prefix `generative-driver-smoke-`, the report key `benchmark_support_tree_sha256`, and every schema name.
  - The benchmark's own resources are still found from `__file__`: `parents[1]` of a module of `benchmark_support` is the folder of `firmware_bench`, as it was the folder of `generative_driver`.
  - A wheel built from `firmware-bench/` holds `firmware_bench/resources/registrations/cases.json` and `sets.json`, and every file of `firmware_bench/resources/bench/cases/setup-smoke/`. Where `src/` lies beside the tests, the wheel holds every file under `src/firmware_bench/resources/`.
  - With that wheel and the tool's wheel installed and no checkout on the search path, `firmware-bench cases` lists the seven registered cases, and `firmware-bench run --case setup-smoke --output <an empty folder>` passes.

- **12-B2** — The benchmark reaches the tool as an installed package, and only through the client interface.
  - Each import of a tool module in the benchmark's code that stays is written absolutely: `from generative_driver.<module> import <names>`, or `from generative_driver import <module>` where it imports a module. It stays in the function or at the module level where it stands today, because tests patch the names at their source. The imports of `interface_runtime` stay as they are.
  - The benchmark finds the tool's folders through names the interface lists: the tool's package folder is `resources_root().parent`, and the runtime's folder is the folder of the imported `interface_runtime`. One new module that is not grading code, `benchmark_support/tool_install.py`, gives both. Grading code does not import it. `snapshots.py` is grading code, so `execution_provenance` takes the two folders from its callers as the keyword arguments `tool` and `runtime`. Both keywords are required: the function has no default for them and imports nothing to find them. Its callers are in `harness.py` and `suite_book.py`, and they pass what `tool_install` gives.
  - `toolchain_revision` in `snapshots.py` is the SHA-256 of three tree hashes joined: the tool's package folder, the runtime's folder, and the folder of the package `firmware_bench` without `resources/registrations/sets.json`. In `run_reporting.py` it is the same without that exclusion, and that module takes the folders from `tool_install` itself. `skills_revision` is the tree hash of `resources_root() / 'toolchain/skills'`. In `suite_reporting.py`, `exporter.toolchain_revision` is the SHA-256 of the tree hashes of the tool's package folder and of the folder of `firmware_bench` joined, and `exporter.benchmark_support_tree_sha256` stays the tree hash of the folder `benchmark_support`. So each value covers the files it covered before the move.
  - A revision is never the hash of nothing: where one of those folders does not exist, the code raises `ValueError` with the text `Revision folder is missing: <path>`.
  - The harness child is started as `-m firmware_bench.benchmark_support.harness`. Its `PYTHONPATH` is the folder that holds the package `firmware_bench` and the folder that holds the tool's package, joined with the system's path separator, once if they are the same folder. It replaces the inherited value, as today.
  - The two placement checks, in `authoring.py` and `evaluator_cli.py`, refuse a folder inside the folder that holds `firmware_bench`, inside the folder that holds the tool's package, or inside a checkout that holds either package, with the texts of today. This widens `authoring.py`, which refuses only the package folder today.
  - The bench harness refuses a replay candidate that lies inside the folder of the package `firmware_bench`, in `trial_store.replay_candidate`, after its refusal of one in the bench home and also when no bench home is given, with the text `A replay candidate is saved worker output, never the installed case sources or groundtruth`. The tool keeps its own refusal for its own resources.
  - The grader identity follows the move. Its walk follows an absolute import that begins `firmware_bench.`, cutting that prefix, where it followed `generative_driver.`. It records an absolute import of `generative_driver` or of `interface_runtime` by its full name, without a hash, and leaves it out of the identity. Module names in an identity stay `benchmark_support.<module>`.
  - The boundary of the grading code holds as before: the closure of every registered case names no module of `generative_driver`, no module of `interface_runtime`, and none of the benchmark modules that `SYSTEM_MODULES` lists. `SYSTEM_MODULES` loses the bare names of tool modules and `interface_runtime`, and the five names of modules that do not exist: `emulated`, `ground`, `cases`, `physical`, `recorded_grading`. The two tests that hold the boundary by that tuple, in `test_grader_identity.py` (186 to 191) and `test_benchmark_surface.py` (1056 to 1062), also fail a closure that holds a name beginning `generative_driver` or `interface_runtime`.
  - The scan that the benchmark's code imports only names the interface lists (`test_benchmark_surface.py`, Behaviour 12 of Feature 08) keeps passing on the code as this Feature leaves it.

- **12-B3** — The code, resources and texts of the two dropped cases are gone.
  - Deleted: `benchmark_support/cases.py`, `physical.py`, `recorded_grading.py`, `tq9_legacy.py`; `resources/bench/cases/tq9/`, `resources/bench/cases/bme280/`, `resources/bench/groundtruth/tq9.enc`, `resources/bench/groundtruth/bme280.enc`.
  - Cut from modules that stay: `case_assets.py` lines 11 to 13 and `mapping_errors` (65 to 86); `mcp_reuse.py` `collect` (46 to 54); `legacy.py` lines 19 to 52; `run_reporting.py` lines 125 to 129; `benchmark.py` lines 604 and 605.
  - `evaluator_cli.py`: line 44 refuses `setup-smoke` and an unregistered case, and names no dropped case. Lines 79 to 102 go. `truth rebuild` without `--authoring-dir` raises `ValueError` with the text `Rebuild requires --authoring-dir`, and it does so first: before the case is resolved, an answer key unlocked or a folder made.
  - `suites.py` line 101 gives `'passed'`.
  - Texts: `benchmark.py` line 186 reads "Explicitly authorize assigned tools for the selected physical binding and effects"; line 187 "Explicitly authorize scoped emulator MCP tools; no physical device approval"; line 202 "V2 sealed evidence (digest checked)"; the two refusals of lines 423 and 427 read `Scoped approval requires a case with that approval scope`. The docstrings of `benchmark.py` 411 to 412, `harness.py` 1784 and `scorecard.py` 393 to 395 name no dropped case.
  - The flags `--approve-bound-device-tools` and `--binding-json` and the harness's paths for a bound device stay. So do the two lines of `suite_reporting.py` (176 and 548) that name `tq9` where they read an earlier report format.

- **12-B4** — The benchmark's tests run from their own folder against installed packages.
  - With both wheels installed, no `PYTHONPATH`, and `firmware-bench/` copied without its `src/` to a folder outside any checkout, `python -m unittest discover -s <copy>/tests` passes.
  - They pass as well from the checkout, by the test command of 12-B7.
  - A test finds the benchmark's code and resources through the imported `firmware_bench`, the tool through the imported `generative_driver`, and the benchmark's documents from its own folder upward: `firmware-bench/` is `parents[1]` of a test file.
  - A child that a test starts gets both package folders. `tool_fixtures.package_env()` gives them, once if they are the same folder, and then the inherited value, as today.
  - The tests of the table `The benchmark's tests` are deleted, changed or kept as it says.
  - `test_benchmark_checkout.py` covers every file of the package `firmware_bench`, code and resources, with `firmware-bench/.gitattributes`: each is byte for byte the same after a checkout that converts line endings, as `tests/test_checkout.py` holds for the tool. Its check of the answer files' hashes stays.
  - `test_benchmark_distribution.py` reads the benchmark's wheel, named by `FB_TEST_WHEEL`, and holds what 12-B1 says of it, in tests named for 12-B1. It skips where the variable is not set.
  - One test named for 12-B4 runs everywhere, in `test_standalone.py`: a child started with `package_env()` and nothing else on its search path imports `firmware_bench` and `generative_driver` and lists the seven registered cases. The existing tests of `test_benchmark_checkout.py` keep their names.

- **12-B5** — The benchmark's documents describe the benchmark as it is, from its own folder.
  - Each document of the table `The benchmark's documents` changes by the rules under it.
  - `firmware-bench/README.md` opens with the text given. `firmware-bench/CONTEXT.md`, `AGENTS.md`, `STANDARDS.md`, `NOTICE`, `LICENSE`, `.gitattributes` and `.gitignore` hold what `The benchmark's documents` gives.
  - Every relative link of a document under `firmware-bench/` resolves inside `firmware-bench/`. A link to a file of the tool is an absolute link to `https://github.com/cam-berger/generative-driver/blob/main/<path>`. Where the folder `src/` is not beside the documents, as on the hosted copy, a link under `src/firmware_bench/` is resolved against the folder of the imported `firmware_bench`.
  - Every command of a code block in a document under `firmware-bench/` whose first word is `firmware-bench` is accepted by the parser of `firmware-bench`. The command is read with its continuation lines joined, a leading variable assignment left out, and each part in square brackets left out with the dots after it.
  - No `.md` file under `firmware-bench/` outside `.scratch/` holds, in any letter case, `bme280`, `legacy tq9`, `--case tq9` not followed by `-v`, `docs/verification.md`, `bench/evidence` or `benchmark-deletion-list`.
  - The front page keeps its link to the release of Renode it pins. The weekly release check reads it there, and `test_reference_tools.py` holds it.

- **12-B6** — The benchmark's outdated records and notes are deleted, and its open issues stay.
  - The table `Records and notes` is carried out: each item is deleted, kept, trimmed or split as it says.

- **12-B7** — The test command runs both test folders.
  - Root `STANDARDS.md` holds the two lines given under `The owner's file`. The owner changes them by hand before the Build, as `How the work goes` says. Every other line of the file stays.
  - The whole test command passes on this machine within the 540 seconds the kit allows.

- **12-B8** — The hosted tests run the benchmark.
  - `.github/workflows/firmware-bench.yml` is new and holds the text given under `The hosted workflow`: one job on macOS and Windows, each with Python 3.11 and 3.13, which builds both distributions, installs both wheels, runs the benchmark's tests on a copy of its folder outside the checkout, and then runs the case `setup-smoke`.

- **12-B9** — Outside `firmware-bench/`, only named places speak of the benchmark's folder, and the tool is untouched.
  - In tracked files outside `firmware-bench/`, `docs/features/` and `tests/`, the texts `firmware-bench` and `firmware_bench` stand only in: the file `.github/workflows/firmware-bench.yml`; lines 28 and 30 of root `STANDARDS.md`; the release check's three places, as Feature 11 left them; and the one paragraph of `README.md` that Feature 11 gave.
  - Among the tool's test files, the three that Feature 11 names hold those texts, and no other. So a tool file outside those three names this Feature as `Feature 12 of the untangle Run` with its Behaviour ID, and does not write this Spec's file name, which holds the folder's name. This covers `tests/supplied_device.py`, `tests/test_supplied_device.py`, `tests/test_tool_commands.py` and `tests/test_tool_documents.py`.
  - So removing the benchmark from this repository is deleting the folder, that workflow file and those places.
  - No file under `src/` changes. Of the tool's documents only `STANDARDS.md` changes, by 12-B7. Outside `firmware-bench/` and this Run's folder, the only other files that change or are new are those that the table `The tool's tests` and 12-B10 name, the new workflow file of 12-B8, and the note `.scratch/test-noise/issues/01-quiet-test-noise.md` of the table `Records and notes`.

- **12-B10** — A hosted job does not wait for hours.
  - `tests/supplied_device.py`: `SuppliedDevice.close()` returns within 10 seconds, whatever its serving thread is doing when it is called. It opens its wake-up connection from a daemon thread, waits at most 5 seconds for the serving thread, and closes the listener itself if the serving thread has not. The serving thread closes the listener when it ends. This is how the benchmark's harness closes (`harness.py` 157 to 198).
  - What the device answers and records does not change, and no existing test changes for it.
  - `.github/workflows/test.yml`: the job `native` gains the line `    timeout-minutes: 60`, directly under the line `  native:`. No other line of that file changes.
  - The job of the new workflow has `timeout-minutes: 150`, as its text gives.

## Interface and seams

### The package

`firmware-bench/pyproject.toml`:

    [build-system]
    requires = ["setuptools>=77"]
    build-backend = "setuptools.build_meta"

    [project]
    name = "firmware-bench"
    version = "0.1.0"
    description = "Run Generative Driver on example firmware and evaluate the driver it emits"
    readme = "README.md"
    requires-python = ">=3.11"
    license = "Apache-2.0"
    dependencies = ["generative-driver>=0.1.0", "cryptography>=43"]

    [project.urls]
    Repository = "https://github.com/cam-berger/generative-driver"

    [project.scripts]
    firmware-bench = "firmware_bench.benchmark:main"

    [tool.setuptools]
    include-package-data = false

    [tool.setuptools.packages.find]
    where = ["src"]
    namespaces = false

    [tool.setuptools.package-data]
    firmware_bench = ["resources/**/*"]

    [tool.setuptools.exclude-package-data]
    "*" = ["**/__pycache__/*", "**/*.pyc", "**/*.pyo"]

The implementer removes the untracked `__pycache__` folders under `firmware-bench/`, since Python imports such a leftover as an empty package.

### The benchmark's tests

Every test file of `firmware-bench/tests/` follows 12-B1 in its imports, its `patch` targets and its child scripts. Beyond that:

| File | What happens |
|---|---|
| `test_assistive_wording.py` | Deleted. It pins the wording of documents of both sides and of a deleted record. |
| `test_benchmark_client_interface.py` | Deleted. It scans two imports that 12-B2 rewrites. |
| `test_benchmark_legacy_cases_dropped.py` | Deleted. Its constant `GRADING_CODE` moves unchanged into `test_benchmark_supplied_stages.py`, which uses it. |
| `test_benchmark_asset_build.py` | The class `LegacyBuildTests` (316 to 343) goes. One new test holds the refusal of 12-B3. |
| `test_benchmark_run.py` | The test of lines 24 to 28 holds the new refusal text. |
| `test_boundary.py` | The two tests on the deletion list (178 to 197) go. The tests that no benchmark test imports the tool's test folder are skipped where that folder is not beside `firmware-bench/`. The test of `package_env()` holds both folders. |
| `test_benchmark_surface.py` | The tests that read `docs/client-interface.md` (Behaviour 15 of Feature 08) are skipped where that file is not two folders above the tests. The test of the three hashed folders (234 to 243) holds 12-B2. The pinned digest of the `report.json` of form `1` (`BEFORE['1']`, lines 68 to 72, asserted at 224 to 232 and 263 to 268) is computed anew, since `toolchain_revision` joins three hashes, and its comment says so. The tests of Behaviour 12 of Feature 08 follow 12-B2: where they pin an import they pin the absolute form, the assertion on `physical` goes, the sources they plant use the absolute form, and `is_tool` takes the benchmark to be `firmware_bench`. |
| `test_benchmark_distribution.py` | Rewritten for 12-B4. Its tests of the tool's source archive go. |
| `test_benchmark_checkout.py` | Follows 12-B4. |
| `test_reference_tools.py` | Reads `firmware-bench/README.md`, found as `parents[1] / 'README.md'`. |
| `test_replay_executor.py` | The row of line 298 names `resources_root() / 'smoke'`, a folder of the tool's own resources, and expects the same refusal. One new test holds the harness's refusal of 12-B2. |
| `test_grader_identity.py`, `test_generated_trials.py`, `test_suite_reporting.py` | Hold 12-B2: the copies they make and the names they pin follow the two packages. `test_generated_trials.py` (877 to 892) copies the folder of `firmware_bench` alone and keeps its patch of `snapshots.__file__`; it no longer copies the runtime, which the suite book takes from `tool_install`. |
| `test_benchmark_snapshots.py` (48, 55, 61, 63), `test_suite_store.py` (49), `test_suite_harness.py` (1193), `test_benchmark_v2_evidence.py` (64), `test_benchmark_runs.py` (152), `test_benchmark_suite_book.py` (259), `test_grader_identity.py` (47 to 49) | Each calls `execution_provenance` itself, so it passes `tool` and `runtime`: the folders `tool_install` gives, or the copies the test made where it made some. |
| `test_device_provider.py`, `test_trial_sealing.py`, `test_benchmark_helper_copies.py`, `test_family_seam.py`, `test_generated_sets.py`, `test_unlocked_keys.py`, `test_set_qualification.py`, `test_benchmark_suite_book.py`, `suite_fixtures.py`, `tool_fixtures.py` | A path into the checkout or into the tool's package becomes a path through the imported package it means. `suite_fixtures.py` reads missions and the smoke case through `case_root()`. |
| `benchmark_family_fixtures.py` | The unused `ToyTransport` goes. |
| `test_benchmark_suite_book.py` | `test_07_B9_a_book_starts_finds_and_seals_a_child_of_the_real_service_through_its_public_calls` waits until its child has ended with nothing of it left stopping before it settles the child. Today it waits only for the child's `status`, and it failed for that on one hosted job of Feature 10. No assertion changes. |

Every other test of the folder keeps its name and its assertions.

A test that this table does not name may still fail after the Build's changes: because it reads something that Feature 11 moved, changed or deleted on the tool's side, or because of 12-B2 or 12-B3. Such a test follows the row it resembles: it reads the benchmark's own counterpart, it follows the changed code with its assertions kept, or it goes where its subject is gone. The report names each such test with its reason. Where no row fits, the implementer reports a Deviation.

New: `firmware-bench/tests/test_standalone.py`. It holds:

- 12-B1: the package's layout, its build file, and the smoke case through `python -m firmware_bench.benchmark`.
- 12-B4: the one test that 12-B4 gives.
- 12-B2: the revisions and their refusal of a missing folder, that `snapshots.py` imports nothing of the tool, the harness child's module and search path, and the placement checks.
- 12-B5: the links, the command lines, the names that no document holds, and the opening of the front page.
- 12-B6: the files that are gone and the files that stay.
- 12-B8: the matrix of the new workflow and the two commands of its last step, each with `check=True`. It is skipped where the workflow file is not two folders above the tests.

What the Reviewer checks from the diff and the report, beyond the tests: of 12-B3 the cut passages; of 12-B5 the passages by document and the given texts; and 12-B6 item by item.

### The tool's tests

| File | What happens |
|---|---|
| `tests/test_tool_alone.py` | Holds 12-B7 and 12-B9 in tests named for them. `NAMED_PLACES` gains `STANDARDS.md` with two lines. The scan leaves out the file `.github/workflows/firmware-bench.yml`, as it leaves out the folder. `test_11_B10_outside_the_parked_folder_the_records_and_the_tests_only_four_places_name_it` holds six places, and its name says so. The two lines of `The owner's file` stand here as constants. This stays the one tool test file that may hold the benchmark's names as text. |
| `tests/test_tool_documents.py` | The two lines it pins for Behaviour 8 of Feature 11 are the lines of `The owner's file`, taken from `tests/test_tool_alone.py`, as it takes the paragraph of the front page. `test_11_B8_the_glossary_keeps_the_tools_rows_and_the_rules_one_test_folder` holds both test folders, and its name says so. The word scan of Behaviour 4 of Feature 11 leaves out those two lines too; `test_11_B4_the_scan_leaves_out_the_two_named_places_and_nothing_else` holds the four places, and its name says so. |
| `tests/test_tool_commands.py` | The SHA-256 it holds of the workflow's text before the last step (`BEFORE_THE_STEP`) is computed anew for the one added line of 12-B10, and its comment says so. One test named for 12-B10 holds that line and that it stands under `  native:`. |
| `tests/test_supplied_device.py` | New. It holds 12-B10: it forces the order that stopped the hosted job. A request is held inside the device by the device's lock, `close()` is called from another thread, and the request is let go. Then the request was answered and `close()` has returned within 10 seconds. A second test holds that a device closed while idle still answers nothing afterwards and leaves no thread serving. |

Every other tool test keeps its name and its assertions. Names and docs in the tool's tests that call the folder parked stay as they are.

### The benchmark's documents

| Document | Was, before Feature 11 |
|---|---|
| `firmware-bench/README.md` | `bench/README.md` |
| `firmware-bench/docs/evaluator.md` | `bench/groundtruth/README.md` |
| `firmware-bench/docs/case-authoring.md` | `docs/benchmark-case-authoring.md` |
| `firmware-bench/docs/corpus.md` | `docs/benchmark-corpus.md` |
| `firmware-bench/docs/generated-device-interface.md` | `docs/generated-device-interface.md` |
| `firmware-bench/docs/codex-acceptance.md` | `docs/codex-acceptance.md` |
| `firmware-bench/docs/adr/0001`, `0003`, `0005`, `0006`, `0007` | `docs/adr/`, the same names |

Rules for every one of these documents:

1. Names follow 12-B1. A path under `src/generative_driver/` to a benchmark file becomes the path under `src/firmware_bench/`. A path is written from `firmware-bench/`, so `firmware-bench/tests/x.py` is `tests/x.py`. A document is named by its place of today, also where code, a resource or a test file names it: `families.py`, `generated_device.py`, `generated_sets.py`, `resources/bench/corpora/seven-area-pilot.json` line 67 and `resources/bench/cases/zephyr-modbus-v1/NOTICE.md` line 17 among them.
2. Links follow 12-B5. A link to a deleted file goes, with the words that only lead to it. Where a sentence then says nothing, it goes.
3. A command name of before Feature 09 is the command of today: `benchmark <x>` is `firmware-bench <x>`.
4. A sentence that speaks only of a dropped case, of the earlier gated protocol, of a result measured in the past, or of a record format that the owner moved past on 2026-11-15 goes. The passages below are those the facts found. The implementer cuts each, and reports any further one it cuts under this rule as a Deviation.

Passages that go or change, by document and line:

- `firmware-bench/README.md`: lines 1 to 12, which the opening below replaces; the table rows of lines 18 and 23; line 25, the link to measured results; line 27, "with published native qualification evidence"; lines 29 to 38, which the new section `Install and check` replaces; line 54, last sentence; line 92, the sentence on recorded calibrations; line 106, the words "; corpus success no longer requires it or the six accepted handoffs", so that the sentence ends "does not change it."; line 108, the two sentences from "Regrade earlier evidence" to "keep gated semantics."; line 126, sentences 3 and 4; line 168, the words from "; `/2` exports keep their old counts" to the closing bracket after "unrun", the words ", as earlier studies counted a stopped trial", and the last sentence, "Physical and gated results stay separate."; line 177, the sentence on pre-harness suites; line 192, the words "of earlier studies" alone, since the code still writes `joint_verdict`; line 198, the link to the tool's glossary becomes a link to `CONTEXT.md`; line 208; lines 212 to 228 (the section on the optional physical trial); line 234, both sentences on earlier gated results and on the deleted record; lines 237 to 239 (the section on verification evidence).
- `firmware-bench/docs/evaluator.md`: line 5, last two sentences; in lines 14 and 25, `--case tq9` becomes `--case tq9-v2` and `tq9.password` becomes `tq9-v2.password`; line 15, the rebuild without an authoring folder, goes, and with it the sentence of line 18 on what that rebuild uses and verifies; line 77, the two sentences from "Sidecars with six acceptance gates" to "never compare the two series."; line 79, the sentence "They pin execution, observations, six gates and final grades.", while the sentences before and after it stay; line 85, the link to the deleted record. Lines 87 to 89, the third-party notice, stay.
- `firmware-bench/docs/case-authoring.md`: line 26, the words in brackets; line 72, the one link becomes two, "[`calibration.inventory`](../src/firmware_bench/benchmark_support/calibration.py) and [`asset_build.assemble_release`](../src/firmware_bench/benchmark_support/asset_build.py)", since `inventory` lies in `calibration.py`; line 108, "preserve old evidence/checkouts so historical reports retain their meaning"; line 224, the paragraph `Retire`; line 231, the link to the deleted record; line 18 says that paths are relative to the benchmark's folder.
- `firmware-bench/docs/corpus.md`: lines 3 and 9, "with native qualification evidence" and "admitted"; line 19, last sentence; line 26, the link to the deleted record; line 46, the last sentence, on legacy `/1` plans; line 103, the words in brackets that begin "(a `benchmark-suite-report/2` export".
- `firmware-bench/docs/generated-device-interface.md`: line 3 reads "Status: specified on 2026-11-02."; line 10 links `../CONTEXT.md`; line 186, the two sentences on version 1 and on the earlier schema name.
- `firmware-bench/docs/codex-acceptance.md`: line 9, the first sentence, which only leads to the deleted record, and the two sentences on `test_acquisition_pipeline.py`, a test of the tool; the sentence on `test_assistive_reports.py` and the last sentence stay; lines 13 and 15, the sentences on prior gated measurements and on "this integration"; line 40, last sentence.
- Decision records: line 3 of 0003, 0005 and 0006 reads "Status: accepted."; line 3 of 0007 reads "Status: accepted with the [generated-device interface](../generated-device-interface.md)."; 0001 loses lines 27 and 29, and its line 34 stays, with its two links to issues pointing at their place of today; 0003 line 7, the words in brackets read "(set name → manifest SHA-256 and whether the set is a scoring set)"; 0006 line 5 links the tool's `AGENTS.md` absolutely; 0007 line 9 says "three entry points".

The front page, `firmware-bench/README.md`, opens with this text, in place of its lines 1 to 12. The heading of line 13 and the table of cases under it follow:

    # Firmware bench

    Firmware bench measures how well [Generative Driver](https://github.com/cam-berger/generative-driver) recovers a working driver from a device's firmware. It supplies a device, a firmware image and a mission to the tool through the tool's client interface, and grades the frozen driver once, after the run. The tool holds nothing of the benchmark.

    | To | Read |
    |---|---|
    | Hold the answer keys and qualify the graders | [Evaluator's guide](docs/evaluator.md) |
    | Add a case | [Case authoring](docs/case-authoring.md) · [Upstream corpus](docs/corpus.md) · [Generated-device interface](docs/generated-device-interface.md) |
    | Accept a model lane | [Codex acceptance](docs/codex-acceptance.md) |
    | Look up a term | [Glossary](CONTEXT.md) |
    | Follow a decision | [Decision records](docs/adr/) |

If a sentence of lines 7 to 12 says something of the benchmark as it is that this text does not, the implementer keeps it below the table and reports it.

The table of cases keeps its columns. It gains a row for `tq9-v3`, "Registered with its public mission. Its firmware and answer key are not in this repository yet, so it lists as `pending`", and a row for `generated`, "A device of a generated set. No set is registered, so it lists as `pending`". Below the table stands:

    Only `setup-smoke` runs with nothing but the two packages. It checks an installation: it validates a model, emits a package and replays a recorded reply. It starts no service, worker or grader. Every other case needs its evaluator password, the emulator, a qualification under the current grader, and a model or a saved candidate.

The section `Install and check` reads as below. Its four command lines stand in a block fenced as `sh`, like the other blocks of the page:

    ## Install and check

        python -m pip install "generative-driver @ git+https://github.com/cam-berger/generative-driver"
        python -m pip install -e .
        firmware-bench cases
        firmware-bench run --case setup-smoke --output "smoke output"

    While this folder lies in the tool's repository, install the tool from the checkout instead: `python -m pip install -e ..`. Set up the tool's service and Codex as its [setup](https://github.com/cam-berger/generative-driver/blob/main/docs/setup.md) and [Codex](https://github.com/cam-berger/generative-driver/blob/main/docs/codex.md) guides say. Run the tests with `python -m unittest discover -s tests`. A graded run also needs Renode, at the release this page pins, and for building firmware an Arm compiler.

`firmware-bench/CONTEXT.md` is written anew from the copy that lies there. It has this frame. The rows of its first table are the rows of the copy's lines 26 to 29, 31 to 36 and 38 to 58, unchanged but for line 33, whose second sentence reads "The bench harness builds it and freezes it with the trial's snapshot."

    # Domain of the benchmark

    The terms of firmware bench. The terms of the tool it evaluates, such as configurator, stage, handoff, package and fresh reuse, are in the tool's [CONTEXT.md](https://github.com/cam-berger/generative-driver/blob/main/CONTEXT.md).

    | Term | Meaning |
    |---|---|
    (the thirty-one rows)

    What the benchmark adds to four of the tool's terms:

    | Term | The benchmark's part |
    |---|---|
    | Equivalence | For a benchmark run the final verdict is the evaluator's grade. |
    | Effect grant | The grader grants each hidden step's effects. |
    | Instrument observation | Generated devices offer none for now: workers see only their UART. |
    | Public mission | Hidden vectors remain evaluator-owned. A generated mission states only each task's effect; ranges, refusal conditions, framing and the mapping onto firmware commands stay hidden. |

    The pass rate scores safety and integrity stops, like a spent budget, as failures, and other operator stops and host stops as faults.

`firmware-bench/AGENTS.md` is written anew:

    # Firmware bench

    Before implementation, read [CONTEXT.md](CONTEXT.md) and the [front page](README.md).

    - Develop through one public seam, the command `firmware-bench`: run, score, compare. For each code change, observe one behavior test fail, implement it, then observe it pass.
    - Use the tool only as an installed package, through its client interface. The benchmark's code imports no other name of it.
    - The bench harness holds evaluator passwords; the configurator and workers receive neither passwords nor their paths or plaintext answers. Describe password separation accurately; hashes are not a sandbox.
    - Keep worker claims, accepted handoffs and evaluator verdicts separate. Score observable outputs/effects, not candidate source-code equality.
    - Distinguish model runs, scripted tests, replay, emulation and physical evidence. Launch model baselines or physical operations only when explicitly assigned by the primary agent.
    - Keep docs concise, concrete and executable. Preserve notices.
    - Track open issues as local Markdown under `.scratch/<feature>/`.

`firmware-bench/STANDARDS.md` is written anew. It is the root `STANDARDS.md` of commit `7cf6c17` with these differences:

- Line 12 becomes two items: "Grading code imports nothing from the system under test, the bench harness or the reporting code, so a grader identity covers grading code only and changing anything else keeps a qualification. `tests/test_grader_identity.py` checks the boundary." and "Evaluator passwords stay in the bench harness. The configurator and workers never receive a password, its path or a plaintext answer."
- In line 26 the sentence on `PYTHONPATH` goes.
- Line 28 reads: ``Test command: `ls tests/test_*.py | xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\n" "$out"; exit $s'` ``
- Line 30 stays: the test files are `tests/test_*.py`.
- Line 37 names `tests/tool_fixtures.py` where it names `tests/package_env.py`.
- Line 38 reads "- Tests use temporary homes. They never touch a real password, vault, device or model run."

Its links to `AGENTS.md` and `CONTEXT.md` then lead to the benchmark's own files.

`firmware-bench/NOTICE`:

    Firmware bench
    Copyright 2026 Generative Driver contributors

    Third-party materials retain their original notices and licenses. See
    docs/evaluator.md and the notices beside each case for the provenance of
    emulator models. Renode, Zephyr and compilers are separately distributed
    under their respective licenses.

`firmware-bench/LICENSE` is a copy of the root `LICENSE`. `firmware-bench/.gitattributes` is a copy of the root `.gitattributes`. `firmware-bench/.gitignore` holds the lines 1 to 11 of the root `.gitignore` and the two lines `runs/` and `groundtruth/plaintext/`.

### Records and notes

| Item | What happens |
|---|---|
| `firmware-bench/docs/adr/0004-compare-systems-through-a-scoring-set.md` | Deleted. It reasons from two protocols whose results the owner moved past, and its rule stands in the benchmark's glossary under `Comparison`. |
| `firmware-bench/.scratch/benchmark-baseline/spec.md` | Deleted. Its built part is finished. Its open steps 6 to 10 (lines 103 to 107) are kept word for word as `firmware-bench/.scratch/baseline/issues/01-open-steps-of-the-baseline.md`, under a title and the line "Status: needs-triage". |
| `firmware-bench/.scratch/benchmark-review/bench-harness-noticed.md`, `firmware-bench/.scratch/benchmark-review/issues/12-one-case-gets-extra-help.md` | Deleted. Notes of a finished Run, and an issue whose premise no longer holds. |
| `firmware-bench/.scratch/benchmark-review/spec.md` with issues 01 to 11 and 13 | Kept. The spec loses its section "The seam with the kit" (lines 29 to 38), its row for issue 12 and its links to deleted files. |
| `firmware-bench/.scratch/benchmark-lifecycle/issues/02-…` and `03-…` | Kept. |
| `firmware-bench/.scratch/benchmark-baseline/issues/01-quiet-test-noise.md` | Split. Its two items on the tool's files go to `.scratch/test-noise/issues/01-quiet-test-noise.md` at the root, which names no file of the benchmark and neither spelling of its folder. Its item on `upstream_qualification.py` goes to `firmware-bench/.scratch/test-noise/issues/01-quiet-test-noise.md`. The folder `firmware-bench/.scratch/benchmark-baseline/` is then gone. |

In a kept issue or spec, rules 1 to 3 of `The benchmark's documents` hold. A sentence that gives a past result or a line number of an old commit stays: an issue is a note of its day.

### The owner's file

Root `STANDARDS.md`. Lines 28 and 30 read as below. Every other line stays.

    Test command: `ls tests/test_*.py firmware-bench/tests/test_*.py 2>/dev/null | PYTHONPATH="$PWD/src:$PWD/firmware-bench/src" xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\n" "$out"; exit $s' && node tests/dashboard-client.test.js`

    Test files: `tests/test_*.py`, `firmware-bench/tests/test_*.py`

### The hosted workflow

`.github/workflows/firmware-bench.yml`:

    name: Firmware bench
    on:
      push:
      pull_request:
    permissions:
      contents: read
    jobs:
      installed:
        timeout-minutes: 150
        strategy:
          fail-fast: false
          matrix:
            os: [macos-latest, windows-latest]
            python: ['3.11', '3.13']
        runs-on: ${{ matrix.os }}
        steps:
          - uses: actions/checkout@v7
          - uses: actions/setup-python@v7
            with:
              python-version: ${{ matrix.python }}
          - name: Build both distributions
            run: |
              python -m pip install build
              python -m build
              python -m build firmware-bench
          - name: Install both wheels
            shell: python
            run: |
              import glob, subprocess, sys
              tool, = glob.glob('dist/*.whl')
              bench, = glob.glob('firmware-bench/dist/*.whl')
              subprocess.run([sys.executable, '-m', 'pip', 'install', tool, bench], check=True)
          - name: Test on a copy outside the checkout
            shell: python
            run: |
              from pathlib import Path
              import glob, os, shutil, subprocess, sys, tempfile
              repo = Path.cwd()
              with tempfile.TemporaryDirectory(prefix='firmware bench ') as work:
                  copy = Path(work) / 'firmware-bench'
                  shutil.copytree(repo / 'firmware-bench', copy, ignore=shutil.ignore_patterns('src', 'dist', 'build', '*.egg-info', '__pycache__'))
                  env = dict(os.environ)
                  env.pop('PYTHONPATH', None)
                  env['FB_TEST_WHEEL'] = str(Path(glob.glob('firmware-bench/dist/*.whl')[0]).resolve())
                  subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(copy / 'tests'), '-v'], cwd=work, env=env, check=True)
                  subprocess.run([sys.executable, '-m', 'firmware_bench.benchmark', 'run', '--case', 'setup-smoke', '--output', str(Path(work) / 'smoke output')], cwd=work, env=env, check=True)

The time limit of 150 minutes is about twice what the benchmark's tests took on the slowest hosted job of Feature 10, Windows with Python 3.11: 71 minutes. The tool's job took 22 minutes there in Feature 11, so its limit is 60.

### How the work goes

The owner's file. After this Spec's Approval the session that runs this Run prepares root `STANDARDS.md` as a whole file outside the repository and gives the owner one command that copies it. The session checks it against `The owner's file` and commits it before the Build begins. On the tree as it is before the Build the new test command fails: the benchmark's tests do not import yet, and four tool tests, in two files, hold the old lines. The Build makes it pass.

The Build. It is one Build. A sound order is: the fix of 12-B10, since every later hosted run can meet the fault; the package and the tool's folders (12-B1, 12-B2); the dropped cases (12-B3); the tests (12-B4); the documents and notes (12-B5, 12-B6); the tool's tests and the workflow (12-B7 to 12-B9).

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise. The report lists every existing test that changed or went, with the rule that covers it, and names every assertion that went. A test reads every document as UTF-8, named so.

The installed state on this machine. The implementer builds both wheels from a copy of the tree outside the clone, installs them into a fresh environment there, and runs the last step of each workflow as its file gives it. If that environment cannot be built here, the report says so, and the hosted run is the evidence.

Identities. Moving and editing grading code changes every grader identity and `toolchain_revision`. No admission is lost, since none stands.

The time of the test command. With both folders it took 408 to 417 seconds at the end of Feature 10, of the 540 the kit allows. If it takes more than 480 seconds on this machine after the Build, the report says so.

The hosted tests. Windows does not run on this machine. So the session that runs this Run commits the state of the Build to the Run's branch before the Feature is Done and pushes it, and gives the implementer and the Reviewer the runs' results. The Feature is Done when both workflows have passed on all eight of their jobs for the tree the Reviewer passed, or when the owner accepts a run as it stands. A job that passes at a second attempt has passed; the session records why the first did not. This is a rule and not a numbered Behaviour.

A hosted failure. The session tells the owner what failed, whether it reads as a fault in a test, a document or a build file or as a fault in product code, and what a fix costs. The owner chooses between accepting the run and a fix. A fix of a test, a document or a build file is made in this Feature and reported as a Deviation.

## Out of scope

- A redesign of the benchmark: how it runs a case, grades it and reports it. Its commands, flags and record formats stay, but for what 12-B3 names.
- Names of the benchmark's code that only tests reach, and its readers of earlier report formats. They stay.
- A qualification of any case, a model run, and any physical operation.
- The 46 names of the tool that the benchmark's tests import beyond the client interface, `Controller` among them. The tests keep them.
- The keywords `autostart` and `read_timeout` and the variable `GENERATIVE_DRIVER_HOME`, which the benchmark uses and the interface document does not name. The document is not edited here.
- A lock file and a release check of the benchmark's own, and the declaration of `pyelftools`, which one of its modules imports.
- The tool. No file under `src/` changes. What Feature 11 noticed in the tool's code and documents stays noted: the wording of two tool descriptions in `mcp.py`, the docstrings that still speak of the benchmark, and the tool's leftovers that serve any client or nobody.
- Moving `firmware-bench/` to a repository of its own, deleting it from this one, and this Run's own folder. They are the owner's, after the Run.
- The generator volume and the private repository. No session opens either.

## Deviations
