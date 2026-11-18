# Spec 11: final-push

## Intent

For the owner, who wants two things that each work alone. Generative Driver writes a driver and an MCP server for a new device. Firmware bench runs that tool on example firmware and evaluates how well it did. After Feature 10 no tool module imports the benchmark and the tests lie in two folders. But the benchmark's code and resources still lie inside the tool's package, the documents describe both together, and legacy items of both remain.

This is the last Feature of the Run. It makes the benchmark a package of its own in `firmware-bench/`, takes everything of the benchmark out of the tool, and deletes what is outdated on both sides. It stands in place of the Features documents, two-packages and cleanup, by the owner's decision of 2026-11-18.

What is different afterwards:

- `src/` holds the tool alone. Its wheel holds no benchmark file, it no longer requires `cryptography`, and `doctor` no longer looks for the two programs that only the benchmark runs.
- `firmware-bench/` holds the benchmark: its package `firmware_bench`, its tests, a front page, five guides, five decision records, a glossary, its rules, its open issues, a licence and a notice. It uses the tool as an installed package.
- The past results, the code and resources of the two dropped cases, finished plans and notes, and this Run's deletion list with its marker lines and its check are deleted.
- The hosted tests have two jobs. One builds and tests the tool alone. One builds both packages and tests the benchmark on a copy of its folder outside the checkout.

Decisions taken in this Spec. Its Approval covers them.

- The benchmark's modules keep their names and their places relative to each other: `firmware_bench.benchmark` and `firmware_bench.benchmark_support.<module>`. A flatter layout is work for the benchmark's own repository.
- Kept, though a reader may call them old: the Specs of the kit-fixes Run in `docs/features/kit-fixes/`, because 19 source lines and 17 test files cite them as the decision behind a module, as `STANDARDS.md` asks; the parked learning-loop spec with its research note, because it is parked and not given up; the benchmark's paths for a device the operator binds, because a case on real hardware is to be rebuilt; the benchmark's readers of earlier report formats; and the case `tq9-v3`, which is registered with a mission and has no firmware or answer key yet.
- The benchmark's folder gets no `uv` lock file in this Run.
- The Reviewer's count of changed lines is not held to a limit. The moves alone are several thousand lines.

Two facts the owner should know. This Feature does not change them.

- `firmware-bench run --case setup-smoke` checks an installation. It validates a model, emits a package and replays a recorded reply. It starts no service, worker or grader, and shows nothing of the six stages.
- Every other case needs its evaluator password, the emulator, a qualification under the current grader, and a model or a saved candidate. No qualification stands today, since Feature 06 changed the grading code. So the first graded result after this Run needs a fresh qualification of the case first.

Conventions.

- Line numbers are those of commit `ffa9880`.
- A sentence of `docs/design.md` is named by its line and its place on it: 113.2 is the second sentence of line 113. The bold title that opens a list item is not counted as a sentence.
- The tool's documents are `README.md`, `CONTRIBUTING.md`, `NOTICE`, `AGENTS.md`, `CONTEXT.md`, `STANDARDS.md`, the `.md` files directly in `docs/`, in `docs/adr/` and in `docs/agents/`, and both copies of the client skill. The research note, the issues under `.scratch/` and the Specs under `docs/features/` are records and are not among them.
- "Goes" means the text is cut. Nothing is kept elsewhere unless a rule says where.
- The marker lines are the two comment lines of Feature 01 that set off a benchmark passage. This Spec does not print them.

## Behaviours

- **11-B1** — The benchmark's code and resources are the package `firmware_bench`.
  - The moves of the table `The package` are made. Afterwards `src/generative_driver/` holds no `benchmark.py`, no `benchmark_support/`, no `resources/bench/` and no `resources/registrations/`, tracked or untracked.
  - The modules keep their names, their places relative to each other and their relative imports of each other.
  - Under `src/` and `firmware-bench/`, and in the documents that stay or move, every text that spells `generative_driver.benchmark` as a module path spells `firmware_bench.benchmark`: imports, `patch` targets, `-m` launches and program names. This covers `generative_driver.benchmark_support` too. The scans in the tool's tests and the Specs under `docs/features/` keep the old spelling beside the new one.
  - These stay as they are, because they are data or the benchmark's own names: the schema `generative-driver-groundtruth/1`, the environment variables `GENERATIVE_DRIVER_BENCH_ROOT`, `GENERATIVE_DRIVER_CASE_ROOT` and `GENERATIVE_DRIVER_HOME`, the prefix `generative-driver-smoke-`, the report key `benchmark_support_tree_sha256`, and every schema name.
  - `firmware-bench/pyproject.toml` holds the text given under `Interface and seams`.
  - A wheel built from `firmware-bench/` holds `firmware_bench/resources/registrations/cases.json` and `sets.json`, and every file of `firmware_bench/resources/bench/cases/setup-smoke/`. Where `src/` lies beside the tests, it holds every file under `src/firmware_bench/resources/`. With that wheel and the tool's wheel installed and no checkout on the search path, `firmware-bench cases` lists the seven registered cases and `firmware-bench run --case setup-smoke --output <an empty folder>` passes.

- **11-B2** — The benchmark reaches the tool as an installed package, and only through the client interface.
  - Each import of a tool module in the benchmark's code that stays is written absolutely: `from generative_driver.<module> import <names>`, or `from generative_driver import <module>` where it imports a module. It stays in the function or at the module level where it stands today, because tests patch the names at their source. The imports of `interface_runtime` stay as they are.
  - The benchmark finds the tool's folders through names the interface lists: the tool's package folder is `resources_root().parent`, and the runtime's folder is the folder of the imported `interface_runtime`. One new module that is not grading code, `benchmark_support/tool_install.py`, gives both. Grading code does not import it. `snapshots.py` is grading code, so `execution_provenance` takes the two folders from its callers as the keyword arguments `tool` and `runtime`. Its callers are in `harness.py` and `suite_book.py`, and they pass what `tool_install` gives.
  - `toolchain_revision` in `snapshots.py` is the SHA-256 of three tree hashes joined: the tool's package folder, the runtime's folder, and the folder of the package `firmware_bench` without `resources/registrations/sets.json`. In `run_reporting.py` it is the same without that exclusion, and that module takes the folders from `tool_install` itself. `skills_revision` is the tree hash of `resources_root() / 'toolchain/skills'`. In `suite_reporting.py`, `exporter.toolchain_revision` is the SHA-256 of the tree hashes of the tool's package folder and of the folder of `firmware_bench` joined, and `exporter.benchmark_support_tree_sha256` stays the tree hash of the folder `benchmark_support`. So each value covers the files it covered before the move.
  - A revision is never the hash of nothing: where one of those folders does not exist, the code raises `ValueError` with the text `Revision folder is missing: <path>`.
  - The harness child is started as `-m firmware_bench.benchmark_support.harness`. Its `PYTHONPATH` is the folder that holds the package `firmware_bench` and the folder that holds the tool's package, joined with the system's path separator, once if they are the same folder. It replaces the inherited value, as today.
  - The two placement checks, in `authoring.py` and `evaluator_cli.py`, refuse a folder inside the folder that holds `firmware_bench`, inside the folder that holds the tool's package, or inside a checkout, with the texts of today. This widens `authoring.py`, which refuses only the package folder today.
  - The bench harness refuses a replay candidate that lies inside the folder of the package `firmware_bench`, in `trial_store.replay_candidate`, after its refusal of one in the bench home and also when no bench home is given, with the text `A replay candidate is saved worker output, never the installed case sources or groundtruth`. The tool keeps its own refusal for its own resources.
  - The grader identity follows the move. Its walk follows an absolute import that begins `firmware_bench.`, cutting that prefix, where it followed `generative_driver.`. It records an absolute import of `generative_driver` or of `interface_runtime` by its full name, without a hash, and leaves it out of the identity. Module names in an identity stay `benchmark_support.<module>`.
  - The boundary of the grading code holds as before: the closure of every registered case names no module of `generative_driver`, no module of `interface_runtime`, and none of the benchmark modules that `SYSTEM_MODULES` lists. `SYSTEM_MODULES` loses the bare names of tool modules and `interface_runtime`, and the five names of modules that do not exist: `emulated`, `ground`, `cases`, `physical`, `recorded_grading`. The two tests that hold the boundary by that tuple, in `test_grader_identity.py` (186 to 191) and `test_benchmark_surface.py` (1056 to 1062), also fail a closure that holds a name beginning `generative_driver` or `interface_runtime`.
  - The scan that the benchmark's code imports only names the interface lists (`test_benchmark_surface.py`, 08-B12) keeps passing on the moved code.

- **11-B3** — The tool's package holds nothing of the benchmark.
  - Root `pyproject.toml` has no script `firmware-bench`, no marker lines, and the dependencies `["mcp>=2.2,<3", "jsonschema>=4.20,<5"]`. `uv.lock` agrees with it.
  - A wheel built from the root holds no path with `benchmark`, `resources/bench` or `resources/registrations` in it, and no `firmware_bench`.
  - `doctor` no longer reports `renode` or `arm-none-eabi-gcc`. It reports `codex`, `java` and `podman` as today.
  - `generative-driver smoke` passes, and the tool's tests pass, with no `firmware-bench/` folder present and `firmware_bench` not importable.
  - Nothing else of the tool's behaviour changes. The refusals of a client's request keys, the code identity and the client interface with its document stay as they are; `docs/client-interface.md` is not edited.

- **11-B4** — The code, resources and texts of the two dropped cases are gone.
  - Deleted: `benchmark_support/cases.py`, `physical.py`, `recorded_grading.py`, `tq9_legacy.py`; `resources/bench/cases/tq9/`, `resources/bench/cases/bme280/`, `resources/bench/groundtruth/tq9.enc`, `resources/bench/groundtruth/bme280.enc`; `benchmark_support/README.md`.
  - Cut from modules that stay: `case_assets.py` lines 11 to 13 and `mapping_errors` (65 to 86); `mcp_reuse.py` `collect` (46 to 54); `legacy.py` lines 19 to 52; `run_reporting.py` lines 125 to 129; `benchmark.py` lines 604 and 605.
  - `evaluator_cli.py`: line 44 refuses `setup-smoke` and an unregistered case, and names no dropped case. Lines 79 to 102 go. `truth rebuild` without `--authoring-dir` raises `ValueError` with the text `Rebuild requires --authoring-dir`, and it does so first: before the case is resolved, an answer key unlocked or a folder made.
  - `suites.py` line 101 gives `'passed'`.
  - Texts: `benchmark.py` line 186 reads "Explicitly authorize assigned tools for the selected physical binding and effects"; line 187 "Explicitly authorize scoped emulator MCP tools; no physical device approval"; line 202 "V2 sealed evidence (digest checked)"; the two refusals of lines 423 and 427 read `Scoped approval requires a case with that approval scope`. The docstrings of `benchmark.py` 411 to 412, `harness.py` 1784 and `scorecard.py` 393 to 395 name no dropped case.
  - The flags `--approve-bound-device-tools` and `--binding-json` and the harness's paths for a bound device stay. So do the two lines of `suite_reporting.py` (176 and 548) that name `tq9` where they read an earlier report format.

- **11-B5** — The benchmark's tests run from their own folder against installed packages.
  - With both wheels installed, no `PYTHONPATH`, and `firmware-bench/` copied without its `src/` to a folder outside any checkout, `python -m unittest discover -s <copy>/tests` passes.
  - They pass as well from the checkout, by the test command of 11-B12.
  - A test finds the benchmark's code and resources through the imported `firmware_bench`, the tool through the imported `generative_driver`, and the benchmark's documents from its own folder upward: `firmware-bench/` is `parents[1]` of a test file.
  - A child that a test starts gets both package folders, as the harness child does. `tool_fixtures.package_env()` gives them.
  - The tests of the table `The benchmark's tests` are deleted, changed or kept as it says.
  - `test_benchmark_checkout.py` covers every file of the package `firmware_bench`, code and resources, with `firmware-bench/.gitattributes`: each is byte for byte the same after a checkout that converts line endings, as `tests/test_checkout.py` holds for the tool. Its check of the answer files' hashes stays.
  - `test_benchmark_distribution.py` reads the benchmark's wheel, named by `FB_TEST_WHEEL`, and holds what 11-B1 says of it.

- **11-B6** — The tool's tests need nothing of the benchmark, and the deletion list is gone.
  - Deleted: `docs/benchmark-deletion-list.md`, `tests/deletion_marking.py`, `tests/test_deletion_marking.py`, `tests/test_service_without_legacy_cases.py`.
  - No file of the repository outside `docs/features/untangle/` holds a marker line. The passages that stood between marker lines go with them, but where 11-B13 gives a line.
  - `tests/test_tool_boundary.py` stays the tool's boundary test: no tool module and no tool test reaches `firmware_bench`.
  - The tool tests that held a marker line, the removal, the workflow's last step, the doctor's five programs or a benchmark resource hold what this Spec gives. The table `The tool's tests` names them.

- **11-B7** — The benchmark's documents lie in `firmware-bench/` and describe the benchmark as it is.
  - The moves of the table `The benchmark's documents` are made, and each moved document changes by the rules under it.
  - `firmware-bench/README.md` opens with the text given, and `firmware-bench/CONTEXT.md`, `AGENTS.md`, `STANDARDS.md`, `NOTICE`, `LICENSE`, `.gitattributes` and `.gitignore` hold what `Interface and seams` gives.
  - Every relative link of a document under `firmware-bench/` resolves inside `firmware-bench/`. A link to a file of the tool is an absolute link to `https://github.com/cam-berger/generative-driver/blob/main/<path>`. Where the folder `src/` is not beside the documents, as on the hosted copy, a link under `src/firmware_bench/` is resolved against the folder of the imported `firmware_bench`.
  - Every line of a code block in a document under `firmware-bench/` that begins `firmware-bench` is accepted by the parser of `firmware-bench`, once a part in square brackets is left out.
  - No `.md` file under `firmware-bench/` outside `.scratch/` holds, in any letter case, `bme280`, `legacy tq9`, `--case tq9` not followed by `-v`, `docs/verification.md`, `bench/evidence` or `benchmark-deletion-list`.

- **11-B8** — The tool's documents hold no benchmark text.
  - Each place the table `The tool's documents, place by place` names is changed as it says.
  - No line of a tool's document holds one of these words, in any letter case: `bench`, `harness`, `suite`, `trial`, `scorecard`, `scoring set`, `answer key`, `answer vault`, `groundtruth`, `leak check`, `pass rate`, `mutant`, `renode`. Left out: the paragraph of `README.md` that points to the benchmark (11-B14), the two lines of `STANDARDS.md` that hold the test command and the test files, and its sentence on "the serial suite" of tests.
  - Every relative link of a tool's document resolves.
  - Nothing else in a tool's document changes.

- **11-B9** — `docs/design.md` is the tool's design.
  - The items and sentences that the tables under `docs/design.md` name are cut or reworded as they say. Every other sentence stays word for word.

- **11-B10** — The client skill is the tool's alone.
  - Both copies hold the text given under `Interface and seams`, and are equal.
  - Every tool name of the form `driver_…` in it is a tool of the MCP server.

- **11-B11** — Outdated records and notes are deleted, and open issues lie with their side.
  - The table `Records and notes` is carried out: each item is deleted, kept, or moved as it says.

- **11-B12** — The owner's three files hold the texts given under `The owner's files`.
  - The owner changes them by hand before the Build, as `How the work goes` says.

- **11-B13** — The build files, the workflow and the release check follow.
  - `MANIFEST.in` loses lines 6, 10, 11, 12 and 15 to 17.
  - `.gitignore` loses lines 13 and 14, and line 18 reads `# Generated local device packages and reports stay outside Git.`
  - `.gitattributes` line 1 reads `# Hashes of source and resource bytes must be the same on every host.`
  - `NOTICE` lines 7 to 10 read: "Third-party materials retain their original notices and licenses. External Codex, Ghidra, compiler, serial and USB dependencies are separately distributed under their respective licenses.", wrapped as the file wraps.
  - `.github/workflows/test.yml` holds the two jobs given under `The hosted workflow`.
  - `.github/scripts/tool_releases.py`: `WATCHED` holds the two workflow patterns and `firmware-bench/README.md`. Its module doc names that file where it named `bench/README.md`. The text of an issue loses the sentence "Upgrading changes benchmark evidence." and the words "and re-run native calibration before comparing results across versions"; the sentence before them ends "and run the affected workflows."
  - `.github/workflows/tool-releases.yml` line 3 names `firmware-bench/README.md` where it named `bench/README.md`. `.github/dependabot.yml` stays as it is.
  - `src/generative_driver/setup.py`: the doctor's table of programs loses `renode` and `arm-none-eabi-gcc`.

- **11-B14** — Outside `firmware-bench/`, only named places speak of the benchmark's folder.
  - In tracked files outside `firmware-bench/` and `docs/features/`, the texts `firmware-bench` and `firmware_bench` stand only in: the two lines of `STANDARDS.md` with the test command and the test files; the job `firmware-bench` of `.github/workflows/test.yml`; the release check's three places of 11-B13; one paragraph of `README.md`, after the table of lines 51 to 58, which reads "Firmware bench, the benchmark that evaluates this tool, lies in [firmware-bench/](firmware-bench/README.md) until it moves to a repository of its own."; and the tool's test files that forbid the import or hold this list.
  - So removing the benchmark from this repository is deleting the folder and those places.

## Interface and seams

### The package

| From | To |
|---|---|
| `src/generative_driver/benchmark.py` | `firmware-bench/src/firmware_bench/benchmark.py` |
| `src/generative_driver/benchmark_support/` | `firmware-bench/src/firmware_bench/benchmark_support/` |
| `src/generative_driver/resources/bench/` | `firmware-bench/src/firmware_bench/resources/bench/` |
| `src/generative_driver/resources/registrations/` | `firmware-bench/src/firmware_bench/resources/registrations/` |

`firmware-bench/src/firmware_bench/__init__.py` is new and holds one docstring: `"""Firmware bench: runs Generative Driver on example firmware and evaluates the driver it emits."""`

Files are moved with `git mv`, so that history follows. The benchmark's own resources are still found from `__file__`: with the places above, `parents[1]` of a module of `benchmark_support` is the folder of `firmware_bench`, as it was the folder of `generative_driver`.

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

### The benchmark's tests

Every test file of `firmware-bench/tests/` follows 11-B1 in its imports, its `patch` targets and its child scripts. Beyond that:

| File | What happens |
|---|---|
| `test_assistive_wording.py` | Deleted. It pins the wording of documents of both sides and of the deleted record. |
| `test_benchmark_client_interface.py` | Deleted. It scans two imports that 11-B2 rewrites. |
| `test_benchmark_legacy_cases_dropped.py` | Deleted. Its constant `GRADING_CODE` moves unchanged into `test_benchmark_supplied_stages.py`, which uses it. |
| `test_benchmark_asset_build.py` | The class `LegacyBuildTests` (316 to 343) goes. One new test holds the refusal of 11-B4. |
| `test_benchmark_run.py` | The test of lines 24 to 28 holds the new refusal text. |
| `test_boundary.py` | The two tests on the deletion list (178 to 197) go. The tests that no benchmark test imports the tool's test folder are skipped where that folder is not beside `firmware-bench/`. The test of `package_env()` holds both folders. |
| `test_benchmark_surface.py` | The tests that read `docs/client-interface.md` (08-B15) are skipped where that file is not two folders above the tests. The test of the three hashed folders (234 to 243) holds 11-B2. The pinned digest of the `report.json` of form `1` (`BEFORE['1']`, lines 68 to 72, asserted at 224 to 232 and 263 to 268) is computed anew, since `toolchain_revision` joins three hashes, and its comment says so. The tests of 08-B12 follow 11-B2: where they pin an import they pin the absolute form, the assertion on `physical` goes, the sources they plant use the absolute form, and `is_tool` takes the benchmark to be `firmware_bench`. |
| `test_benchmark_distribution.py` | Rewritten for 11-B5. Its tests of the tool's source archive go. |
| `test_benchmark_checkout.py` | Follows 11-B5. |
| `test_reference_tools.py` | Reads `firmware-bench/README.md`. |
| `test_replay_executor.py` | The row of line 298 names `resources_root() / 'smoke'`, a folder of the tool's own resources, and expects the same refusal. One new test holds the harness's refusal of 11-B2. |
| `test_grader_identity.py`, `test_generated_trials.py`, `test_suite_reporting.py` | Hold 11-B2: the copies they make and the names they pin follow the two packages. Where two of them patch `snapshots.__file__` to put other folders in place (`test_grader_identity.py` 47 to 49, `test_generated_trials.py` 877 to 892), they pass those folders as `tool` and `runtime`. |
| `test_device_provider.py`, `test_trial_sealing.py`, `test_benchmark_helper_copies.py`, `test_family_seam.py`, `test_generated_sets.py`, `test_unlocked_keys.py`, `test_set_qualification.py`, `test_benchmark_suite_book.py`, `suite_fixtures.py`, `tool_fixtures.py` | A path into the checkout or into the tool's package becomes a path through the imported package it means. `suite_fixtures.py` reads missions and the smoke case through `case_root()`. |
| `benchmark_family_fixtures.py` | The unused `ToyTransport` goes. |
| `test_benchmark_suite_book.py` | `test_07_B9_a_book_starts_finds_and_seals_a_child_of_the_real_service_through_its_public_calls` waits until its child has ended with nothing of it left stopping before it settles the child. Today it waits only for the child's `status`, and it failed for that on one hosted job of Feature 10. No assertion changes. |

Every other test of the folder keeps its name and its assertions.

New: `firmware-bench/tests/test_standalone.py` holds 11-B1 (the package's layout, its build file, the smoke case through `python -m firmware_bench.benchmark`), 11-B2 (the revisions and their refusal of a missing folder, that `snapshots.py` imports nothing of the tool, the harness child's module and search path, the placement checks) and 11-B7 (links, command lines, the names that no document holds).

### The tool's tests

| File | What happens |
|---|---|
| `tests/test_tool_commands.py` | Holds the job `native` as `The hosted workflow` gives it: its last step word for word and the SHA-256 of the file's text before that step. Its three tests of the workflow read the lines of the job `native` alone: the step is compared to the end of that job (413 to 415), no line of that job says `bench` (447 to 451), and `test_09_B12_its_one_job_is_four_…` (507 to 527) holds that job's matrix; its name may change. No test holds the text of the second job; the hosted run is its evidence. Its tests of a passage between marker lines go. Its test that reads `resources/registrations/cases.json` goes. The three install blocks of `README.md` stay pinned and unchanged. |
| `tests/test_client_interface.py`, `tests/test_supplied_runs.py` | The assertions that name the marker text go. |
| `tests/test_doctor.py` | The doctor's programs are `codex`, `java` and `podman`. |
| `tests/test_tool_releases.py` | Writes its made-up pins into a workflow file, where it wrote a `bench/README.md`. |
| `tests/test_tool_boundary.py` | The one file it leaves out of its scan of strings, beside itself, is `tests/test_tool_alone.py`, where it left out the two files of the deletion list's check. |
| `tests/test_supplied_runs.py`, `tests/test_client_calls.py`, `tests/test_supplied_stages.py`, `tests/test_client_surface.py`, `tests/test_tool_commands.py` | The module docstring of each names "the benchmark's tests" where it named a file under `firmware-bench/tests/`. |
| `tests/test_checkout.py`, `tests/test_distribution.py` | Unchanged. |

New: `tests/test_tool_alone.py` holds 11-B3 (the build file, the doctor, and with `GD_TEST_WHEEL` that the wheel holds no benchmark path), 11-B6 (no marker line anywhere outside this Run's folder), the release check's part of 11-B13 and 11-B14. It is the one tool test file that may hold the benchmark's names as text. `tests/test_tool_documents.py` holds 11-B8 (the word scan, with a test on planted text that shows it sees each word and the places it leaves out; the links) and 11-B10.

Behaviours without a test of their own. The Reviewer checks these from the diff and the report: of 11-B4 everything but its two refusals; 11-B9, sentence by sentence; 11-B11; 11-B12; and of 11-B13 the lines of `MANIFEST.in`, `.gitignore`, `.gitattributes` and `NOTICE` and the second job.

Every other tool test keeps its name and its assertions. The scans in other tool test files that no tool module imports the benchmark stay as they are.

### The benchmark's documents

| From | To |
|---|---|
| `bench/README.md` | `firmware-bench/README.md` |
| `bench/groundtruth/README.md` | `firmware-bench/docs/evaluator.md` |
| `docs/benchmark-case-authoring.md` | `firmware-bench/docs/case-authoring.md` |
| `docs/benchmark-corpus.md` | `firmware-bench/docs/corpus.md` |
| `docs/generated-device-interface.md` | `firmware-bench/docs/generated-device-interface.md` |
| `docs/codex-acceptance.md` | `firmware-bench/docs/codex-acceptance.md` |
| `docs/adr/0001-benchmark-registration-and-selection.md` | `firmware-bench/docs/adr/`, same name |
| `docs/adr/0003-generated-devices-register-by-set.md` | the same |
| `docs/adr/0005-keep-the-device-generator-private.md` | the same |
| `docs/adr/0006-seal-graded-scoring-trials.md` | the same |
| `docs/adr/0007-one-validator-defines-a-generated-device.md` | the same |

Rules for every moved document:

1. Names follow 11-B1. A path under `src/generative_driver/` to a benchmark file becomes the path under `src/firmware_bench/`. A path is written from `firmware-bench/`, so `firmware-bench/tests/x.py` is `tests/x.py`. A moved document is named by its new place, also where code, a resource or a test file names it: `families.py`, `generated_device.py`, `generated_sets.py`, `resources/bench/corpora/seven-area-pilot.json` line 67 and `resources/bench/cases/zephyr-modbus-v1/NOTICE.md` line 17 among them.
2. Links follow 11-B7. A link to a deleted file goes, with the words that only lead to it. Where a sentence then says nothing, it goes.
3. A command name of before Feature 09 is the command of today: `benchmark <x>` is `firmware-bench <x>`.
4. A sentence that speaks only of a dropped case, of the earlier gated protocol, of a result measured in the past, or of a record format that the owner moved past on 2026-11-15 goes. The passages below are those the facts found. The implementer cuts each, and reports any further one it cuts under this rule as a Deviation.

Passages that go or change, by document and line:

- `bench/README.md`: line 3 (the legacy-copy notice); line 9, the sentence on a new series; the table rows of lines 16 and 21; line 23, the link to measured results; line 25, "with published native qualification evidence"; lines 27 to 36, which the new section `Install and check` replaces; line 52, last sentence; line 90, the sentence on recorded calibrations; lines 104 and 106, the sentences on what success no longer requires and on regrading earlier evidence; line 124, sentences 3 and 4; in lines 166, 175 and 190, the sentences on `/2` exports, earlier studies, physical and gated results, pre-harness suites and the joint verdict of earlier studies; line 196, the link to the tool's glossary becomes a link to `CONTEXT.md`; line 206; lines 210 to 226 (the section on the optional physical trial); line 232, both sentences on earlier gated results and on the deleted record; lines 235 to 237 (the section on verification evidence).
- `bench/groundtruth/README.md`: line 5, last two sentences; in lines 14 and 25, `--case tq9` becomes `--case tq9-v2` and `tq9.password` becomes `tq9-v2.password`; line 15, the rebuild without an authoring folder, goes, and with it the sentence of line 18 on what that rebuild uses and verifies; lines 77 and 79, the sentences on sidecars with six acceptance gates; line 85, the link to the deleted record. Lines 87 to 89, the third-party notice, stay.
- `docs/benchmark-case-authoring.md`: line 26, the words in brackets; line 72, the link label names `calibration.inventory`; line 108, "preserve old evidence/checkouts so historical reports retain their meaning"; line 224, the paragraph `Retire`; line 231, the link to the deleted record; line 18 says that paths are relative to the benchmark's folder.
- `docs/benchmark-corpus.md`: lines 3 and 9, "with native qualification evidence" and "admitted"; line 19, last sentence; line 26, the link to the deleted record; lines 46 and 103, the sentences on legacy `/1` plans and `/2` exports.
- `docs/generated-device-interface.md`: line 3 reads "Status: specified on 2026-11-02."; line 10 links `../CONTEXT.md`; line 186, the two sentences on version 1 and on the earlier schema name.
- `docs/codex-acceptance.md`: line 9, the link to the deleted record and the three sentences on `test_acquisition_pipeline.py`; lines 13 and 15, the sentences on prior gated measurements and on "this integration"; line 40, last sentence.
- Decision records: line 3 of 0003, 0005 and 0006 reads "Status: accepted."; line 3 of 0007 reads "Status: accepted with the [generated-device interface](../generated-device-interface.md)."; 0001 loses lines 27 and 29, and its line 34 stays, since its two links to issues resolve from the new place; 0003 line 7, the words in brackets read "(set name → manifest SHA-256 and whether the set is a scoring set)"; 0006 line 5 links the tool's `AGENTS.md` absolutely; 0007 line 9 says "three entry points".

The front page, `firmware-bench/README.md`, opens with this text, in place of lines 1 to 10 of `bench/README.md`. The heading of line 11 and the table of cases under it follow:

    # Firmware bench

    Firmware bench measures how well [Generative Driver](https://github.com/cam-berger/generative-driver) recovers a working driver from a device's firmware. It supplies a device, a firmware image and a mission to the tool through the tool's client interface, and grades the frozen driver once, after the run. The tool holds nothing of the benchmark.

    | To | Read |
    |---|---|
    | Hold the answer keys and qualify the graders | [Evaluator's guide](docs/evaluator.md) |
    | Add a case | [Case authoring](docs/case-authoring.md) · [Upstream corpus](docs/corpus.md) · [Generated-device interface](docs/generated-device-interface.md) |
    | Accept a model lane | [Codex acceptance](docs/codex-acceptance.md) |
    | Look up a term | [Glossary](CONTEXT.md) |
    | Follow a decision | [Decision records](docs/adr/) |

If a sentence of lines 5 to 10 says something of the benchmark as it is that this text does not, the implementer keeps it below the table and reports it.

The table of cases keeps its columns. It gains a row for `tq9-v3`, "Registered with its public mission. Its firmware and answer key are not in this repository yet, so it lists as `pending`", and a row for `generated`, "A device of a generated set. No set is registered, so it lists as `pending`". Below the table stands:

    Only `setup-smoke` runs with nothing but the two packages. It checks an installation: it validates a model, emits a package and replays a recorded reply. It starts no service, worker or grader. Every other case needs its evaluator password, the emulator, a qualification under the current grader, and a model or a saved candidate.

The section `Install and check` reads:

    ## Install and check

        python -m pip install "generative-driver @ git+https://github.com/cam-berger/generative-driver"
        python -m pip install -e .
        firmware-bench cases
        firmware-bench run --case setup-smoke --output "smoke output"

    While this folder lies in the tool's repository, install the tool from the checkout instead: `python -m pip install -e ..`. Set up the tool's service and Codex as its [setup](https://github.com/cam-berger/generative-driver/blob/main/docs/setup.md) and [Codex](https://github.com/cam-berger/generative-driver/blob/main/docs/codex.md) guides say. Run the tests with `python -m unittest discover -s tests`. A graded run also needs Renode, at the release this page pins, and for building firmware an Arm compiler.

`firmware-bench/CONTEXT.md` has this frame. The rows of its first table are those of `CONTEXT.md` lines 26 to 29, 31 to 36 and 38 to 58, unchanged but for line 33, whose second sentence reads "The bench harness builds it and freezes it with the trial's snapshot."

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

`firmware-bench/AGENTS.md`:

    # Firmware bench

    Before implementation, read [CONTEXT.md](CONTEXT.md) and the [front page](README.md).

    - Develop through one public seam, the command `firmware-bench`: run, score, compare. For each code change, observe one behavior test fail, implement it, then observe it pass.
    - Use the tool only as an installed package, through its client interface. The benchmark's code imports no other name of it.
    - The bench harness holds evaluator passwords; the configurator and workers receive neither passwords nor their paths or plaintext answers. Describe password separation accurately; hashes are not a sandbox.
    - Keep worker claims, accepted handoffs and evaluator verdicts separate. Score observable outputs/effects, not candidate source-code equality.
    - Distinguish model runs, scripted tests, replay, emulation and physical evidence. Launch model baselines or physical operations only when explicitly assigned by the primary agent.
    - Keep docs concise, concrete and executable. Preserve notices.
    - Track open issues as local Markdown under `.scratch/<feature>/`.

`firmware-bench/STANDARDS.md` is the root file as 11-B12 gives it, with these differences: line 3 links `AGENTS.md` of its own folder; after the item on Windows stand the two items "Grading code imports nothing from the system under test, the bench harness or the reporting code, so a grader identity covers grading code only and changing anything else keeps a qualification. `tests/test_grader_identity.py` checks the boundary." and "Evaluator passwords stay in the bench harness. The configurator and workers never receive a password, its path or a plaintext answer."; the test command is `ls tests/test_*.py | xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\n" "$out"; exit $s'` and the test files are `tests/test_*.py`; the sentence on `PYTHONPATH` goes; the item on a Python child names `tests/tool_fixtures.py`; and the last item of `Testing` reads "Tests use temporary homes. They never touch a real password, vault, device or model run."

`firmware-bench/NOTICE`:

    Firmware bench
    Copyright 2026 Generative Driver contributors

    Third-party materials retain their original notices and licenses. See
    docs/evaluator.md and the notices beside each case for the provenance of
    emulator models. Renode, Zephyr and compilers are separately distributed
    under their respective licenses.

`firmware-bench/LICENSE` is a copy of the root `LICENSE`. `firmware-bench/.gitattributes` is a copy of the root file as 11-B13 leaves it. `firmware-bench/.gitignore` holds the lines 1 to 11 of the root file and the two lines `runs/` and `groundtruth/plaintext/`.

### The client skill

Both copies:

    ---
    name: generative-driver
    description: Generate a device interface from firmware, or reconnect to an existing driver workflow.
    ---

    # Generate a device interface

    1. Run `driver_doctor` to check dependencies. An authenticated Codex runtime is configured with `generative-driver configure`. Credentials stay with Codex.
    2. Establish objective, inputs, binding and effects. Reads are default; writes/actuation require task authorization. Grant `reset` only for devices that restart on port open. Smoke uses no inference.
    3. Start once with `driver_start`, stable request ID; save `run_id`. Configurator owns workers/access/handoffs. When authorized, use `scoped_tool_approval: "bound-device"` for the selected binding/effects. Preserve prior authorization.
    4. Follow status/events and stage notes, including failed/not-run checks and open questions. Worker reports never stop runs; only safety, integrity, operator and host faults do. Use `driver_respond` for requested observations, naming the observer. Cancel when asked; resolve causes before resuming.
    5. Retrieve `driver_result`: report stages/notes, `equivalence` with basis, evidence and limits separately. Generic verdicts are `self-evidence`, not independent grades. Label replay, emulation and physical results.

    Six stages: acquire → interpret → probe → ground → emit → reuse. Pass notes forward except fresh reuse, which gets only package/objective and permitted operating information. Ground tests meanings against the device. Device differences drive bounded repair; after budget exhaustion continue with the best revision. Record one verdict, then `completed` regardless of equivalence. A package's structure does not prove its behavior.

    Workers see instrument readings as device evidence and are not read-isolated. Interrupted writes stay uncertain until checked; never silently retry them.

The first step changes because `driver_doctor` configures nothing, and "profile" was an argument of a tool that Feature 09 removed.

### The tool's documents, place by place

A new sentence is given word for word.

| Place | What happens |
|---|---|
| `README.md:3` | The second link goes, with the dot before it. |
| `README.md:56`, `:57` | Both rows go. |
| `README.md:60` | Sentences 3 to 5 become: "Under `assistive/1`, a run ends with one final equivalence verdict: the configurator's own, labelled self-evidence, or one posted through the client interface for a run on a supplied device. Workers are not read-isolated: a worker running as the same user can read files outside its workspace." |
| `README.md`, after the table of lines 51 to 58 | One new paragraph, the one that 11-B14 gives. |
| `CONTRIBUTING.md:3` | The last sentence becomes: "Work through the public CLI/MCP, configurator/workers or runtime/package interface, or the client interface." |
| `CONTRIBUTING.md:12` | It stays: it shows the tool's test run. |
| `CONTRIBUTING.md:20` | "or `bench/README.md`" goes. The sentence on benchmark evidence becomes: "An upgrade changes what the real Ghidra lane runs, so bump every pin and the SHA-256 beside it together." |
| `CONTRIBUTING.md:28` | "and evaluator-owned truth" goes. |
| `CONTRIBUTING.md:30` | Sentences 3 and 4 go. |
| `CONTRIBUTING.md:34` | The paragraph goes. |
| `docs/agents/domain.md:3` | "and benchmark" goes: "one domain vocabulary for the configurator, runtime and package." |
| `docs/codex.md:67` | "a benchmark or supported device" becomes "a supported device". |
| `docs/codex.md:69` | Sentence 2 goes. |
| `docs/codex.md:71` | The paragraph goes. One sentence takes its place: "Workers are not read-isolated: a worker running as the same user can read files outside its workspace." |
| `docs/codex.md:85` | The last sentence becomes: "A run on a supplied device gets no kit analysis and no task mode." |
| `docs/codex.md:97` | The last sentence becomes: "A run on a supplied device gets neither documents nor device-first interpretation." |
| `docs/codex.md:103` | The last sentence becomes: "A run on a supplied device is the exception: its tool calls still have no limit." |
| `docs/dashboard.md:7` | The last sentence goes. |
| `docs/dashboard.md:33` | The last sentence becomes: "Full command arguments and outputs stay in their original run artifacts." |
| `docs/dashboard.md:37` | The paragraph goes. |
| `docs/setup.md:5` | "can invalidate benchmark hashes" becomes "change bytes that the tool hashes". |
| `docs/setup.md:50` | "Ghidra, Java and Renode" becomes "Ghidra and Java". |
| `docs/setup.md:70-72` | The passage between the marker lines goes, with them. |
| `docs/support.md:3` | Sentences 3 to 5 become: "Windows 11 UI setup and native Windows Ghidra remain unmeasured." |
| `docs/support.md:8` | The row goes. |
| `docs/support.md:18` | "generic self-evidence or post-run benchmark grade" becomes "the configurator's self-evidence, or one posted after a run on a supplied device". |
| `docs/support.md:20` | Sentence 3 becomes: "The owner of a supplied device runs it; the configurator uses its control endpoint and holds no password." |
| `docs/support.md:24` | Sentence 1 becomes: "Local macOS scripted CLI/MCP tests cover reconnection, owner loss, Windows missing-owner policy and pagination." Sentence 3 goes. |
| `docs/support.md:26-28` | The passage between the marker lines goes, with them. |
| `docs/extending-drivers-and-mcp.md:101` | "and archived trial meanings" becomes "and the meaning of every format a kit has written". |
| `docs/extending-drivers-and-mcp.md:107` | "and the benchmark's grader" goes. |
| `docs/extending-drivers-and-mcp.md:113` | Sentences 2 and 3 go. |
| `docs/support.md:30` | Sentence 1 becomes: "Without a single firmware binary, the managed interpreter needs a device binding and interprets device first; see [Codex](codex.md)." |
| `docs/adr/0002-driver-contract-and-generated-mcp.md:7` | The last sentence goes. |
| `docs/adr/0002-…:15` | Sentence 2 becomes: "New format-2 fresh reuse requires generated-MCP dispatch evidence." Sentence 3 becomes: "Never put passwords or their paths into configurator/worker assignments." |
| `docs/adr/0002-…:19` | Sentence 1 becomes: "Historical format-1 packages retain their original rules." |
| `docs/adr/0002-…:21` | The paragraph becomes: "This change makes no MHS compliance, physical qualification or new Windows qualification claim." |
| `docs/adr/0008-run-the-installed-runtime-over-package-data.md:3` | "proposed" becomes "accepted". |
| `docs/adr/0008-…:15` | "and the benchmark's grader" goes, so the sentence ends "through its own command line." "the generic and benchmark emit gates" becomes "the emit gates". |
| `docs/adr/0008-…:25` | ", and the grader's runs stay those of the bundled copy" goes. |
| `docs/adr/0009-device-first-interpretation.md:5` | The last sentence becomes: "A run on a supplied device always has an image and never takes this path." |
| `NOTICE:7-10` | As 11-B13 gives it. |

### `docs/design.md`

These items go whole: the paragraph of line 5; the diagram lines 22 and 23; the paragraph of line 50; the table of lines 54 to 58; the paragraphs of lines 62, 64, 66 and 68; the table row of line 79; the list items of lines 104, 109, 110 and 111. The heading of line 52 stays, over what is left of line 60.

These sentences go: 3.3; 28.5; 36.5 and 36.6; 42.2; 48.4; 60.1; 106.1 to 106.4 and 106.6; 107.1 and 107.8 to 107.11; 112.1 to 112.5; 113.1, 113.3 to 113.6, 113.9 and 113.13 to 113.17.

These sentences change. A text in quotation marks is the whole sentence afterwards. Any other entry says which words change, and the rest of the sentence stays.

| Sentence | Afterwards |
|---|---|
| 9.1 | "Confirmed 2026-10-03." |
| 13.3 | "A fresh agent must use the emitted package successfully." |
| 34.1 | "Before final grading, an owned emulator" becomes "Before the final verdict, a supplied device". |
| 40.3 | "`docs/` and `tests/` hold guides and behavior tests." |
| 42.4 | "Follow [setup](setup.md) through runtime configuration and dependency checks before a device run." |
| 48.2 | "Fresh reuse uses that catalog through configurator-owned dispatch, with or without a supplied device." |
| 76.1 | "setup smoke" becomes "the smoke". |
| 86.2 | Its list of what the amendment supersedes ends after the diagnostic observations as repair input. The six acceptance gates, the terminal frozen-final failure and the configurator that unlocks truth go, and the list reads right without them. |
| 86.3 | "Earlier runs keep their recorded meaning; resuming one is refused." |
| 88.1 | "The configurator coordinates; for a run on a supplied device, a client of the interface plays the operator:" |
| 100.4 | "Benchmark emit" becomes "Emit on a supplied device". |
| 103.3 | "A benchmark verdict (basis `evaluator`)" becomes "A posted verdict (basis `evaluator`)". |
| 103.19 | "a benchmark run there awaiting its grade" becomes "a run on a supplied device there awaiting its verdict". |
| 105.1 | "the provider's verified replacement of the emulator" becomes "the verified replacement of a supplied device, through the request `replace`,". "the stopped emulator's" becomes "the stopped device's". |
| 105.2 | "and the driver is graded after the run" becomes "and the verdict comes after the run". |
| 106.9 | "The run releases the device when it ends, once no live call is inside its window; the client stops it." |
| 107.2 | "A client posts the verdict with `record_evaluation`." |
| 107.4 | The words in brackets go. |
| 107.7 | "A posted record has the status `graded`, `no-driver`, `unknown` or `unscored`; an `unscored` one is recorded as an event and leaves the run awaiting its verdict." |
| 108.1 | "until the harness attaches a verified device with its evaluation token" becomes "until a client attaches a verified device with the run's evaluation token". |
| 108.2 | "A verdict is taken only for a `completed` run." |
| 108.3 | "Readings leave the device's provider as `instrument-observation/1` values and units; the run records the device's session, binding and recovery receipts." |
| 113.7 | "The runs database stays readable." |
| 113.8 | It ends after its first clause, on what a driver prints and a worker's disk and memory use. |
| 113.12 | "The provided emulator (v2 families and legacy tq9)" becomes "A supplied device". |

Sentence 112.6 stays as it is.

The diagram of lines 90 to 98 keeps its frame and holds these five lines in place of lines 92 to 97:

        H["Client: owns the device, keeps the token"] --> P["Device provider"]
        H -->|"device descriptor, firmware, mission, token sha256"| C["Configurator: no password, no case"]
        C -->|"control endpoint: status, readings, call window, replacement, release"| P
        C -->|"assignments, gateway tools, prior notes"| W["Stage workers"]
        H -->|"after the run: equivalence/2 with record_evaluation"| C

The bold titles of three list items follow what is left under them: line 106 "**The configurator holds no password.**", line 107 "**One posted verdict after the run.**", line 112 "**No stage verdicts.**". The titles of lines 105, 108 and 113 stay.

If a sentence that stays still holds a word of 11-B8 after these changes, the implementer changes that word alone and reports it as a Deviation.

### Records and notes

| Item | What happens |
|---|---|
| `docs/verification.md`, `bench/evidence/` (18 files) | Deleted. The owner decided it. |
| `docs/adr/0004-compare-systems-through-a-scoring-set.md` | Deleted. It reasons from two protocols whose results the owner moved past, and its rule stands in the benchmark's glossary under `Comparison`. |
| `docs/research/2026-10-28-mhs-interface-review.md` | Deleted. It reviews a state before the generated MCP server existed. |
| `.scratch/benchmark-baseline/spec.md` | Deleted. Its built part is finished. Its open steps 6 to 10 (lines 103 to 107) are kept word for word as `firmware-bench/.scratch/baseline/issues/01-open-steps-of-the-baseline.md`, under a title and the line "Status: needs-triage". |
| `.scratch/benchmark-review/bench-harness-noticed.md`, `.scratch/benchmark-review/issues/12-one-case-gets-extra-help.md` | Deleted. Notes of a finished Run, and an issue whose premise no longer holds. |
| `.scratch/effect-labels/spec.md` | Deleted. It is resolved. |
| `.scratch/benchmark-review/spec.md` with issues 01 to 11 and 13 | Moved to `firmware-bench/.scratch/benchmark-review/`. The spec loses its section "The seam with the kit" (lines 29 to 38), its row for issue 12 and its links to deleted files. |
| `.scratch/benchmark-lifecycle/issues/02-…` and `03-…` | Moved to `firmware-bench/.scratch/benchmark-lifecycle/issues/`. |
| `.scratch/benchmark-baseline/issues/01-quiet-test-noise.md` | Split. The two items on the tool's files stay, as `.scratch/test-noise/issues/01-quiet-test-noise.md`. The item on `upstream_qualification.py` goes to `firmware-bench/.scratch/test-noise/issues/01-quiet-test-noise.md`. |
| `.scratch/effect-labels/issues/01-observe-read-state-changes.md` | Kept. It names the code's place of today, `src/generative_driver/supplied/instrument_evidence.py`, and loses its link to the deleted spec. |
| `.scratch/learning-loop/spec.md`, `docs/research/2026-10-30-firmware-learning-practice.md` | Kept. In the spec, the two links of lines 47 and 51 to benchmark files lose their targets and keep their words as plain text, and line 65 says the branch became pull request 24. |
| `docs/features/kit-fixes/`, `docs/features/untangle/` | Kept. |

In a moved issue or spec, rules 1 to 3 of `The benchmark's documents` hold. A sentence that gives a past result or a line number of an old commit stays: an issue is a note of its day.

### The owner's files

`AGENTS.md`. Lines 5, 8, 9, 10 and 11 read as below. Every other line stays.

    - Develop through three public seams: CLI/MCP, configurator/workers, runtime/package. For each code change, observe one behavior test fail, implement it, then observe it pass.

    - Fresh reuse receives only the package and objective. Under `assistive/1`, stages hand off with notes; device differences drive bounded repairs, then continue with the best revision. Record one final equivalence verdict: the configurator's own, labelled self-evidence, or the one posted through the client interface for a run on a supplied device. Never silently retry an uncertain write.

    - Keep worker claims, accepted handoffs and posted verdicts separate. Hashes establish provenance and integrity.

    - The configurator and workers receive neither passwords nor their paths or plaintext answers. Describe isolation accurately: workers are not read-isolated, and hashes are not a sandbox. Distinguish model runs, scripted tests, replay, emulation and physical evidence.

    - Keep docs concise, concrete and executable. Define terms on first use; remove repeated explanations. Preserve notices and private research artifacts. Launch model runs or physical operations only when explicitly assigned by the primary agent.

`STANDARDS.md`. Line 12 goes. Line 13 reads "- The configurator and workers never receive a password, its path or a plaintext answer." Line 29 and line 39 read as below. Every other line stays, line 31 among them.

    Test command: `ls tests/test_*.py firmware-bench/tests/test_*.py 2>/dev/null | PYTHONPATH="$PWD/src:$PWD/firmware-bench/src" xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\n" "$out"; exit $s' && node tests/dashboard-client.test.js`

    - Tests use temporary homes. They never touch a real password, device or model run.

`CONTEXT.md`. The thirty-one rows of lines 26 to 29, 31 to 36 and 38 to 58 go. Rows 16, 21, 30 and 37 and the paragraph of line 60 read as the first five texts below. The last sentence of line 62, on the pass rate, goes. One row is new, the sixth text below, after the row `Stand-in device`. Every other line stays.

    | Equivalence | One final verdict: the configurator's own, with basis `self-evidence`, or the one posted through the client interface for a run on a supplied device, with basis `evaluator`. Completion alone does not mean success. |

    | Effect grant | Permission for a driver call to write, actuate or reset; reads need none. The operator grants a run's effects; a call needing an ungranted effect is refused before any device I/O. |

    | Instrument observation | Device-state readings visible to workers, never grades. |

    | Public mission | Required task meanings, inputs, output units and the objective for fresh reuse. |

    Functional compatibility compares required outputs/effects, not source code. Hashes prove identity/integrity, not physical truth or access control. Keep passwords and answers out of worker inputs; the configurator holds neither.

    | Supplied device | A device that a client of the [client interface](docs/client-interface.md) owns, starts and stops, and describes to the service. A run on one ends `completed` and waits for the one verdict that client posts. |

### The hosted workflow

`.github/workflows/test.yml` keeps its name, its triggers and its permissions. The job `native` keeps its matrix and its steps. Its last step loses the line that sets `GD_TEST_REMOVAL` and the marked passage, so it runs the tool's tests and then the smoke. A second job follows it:

      firmware-bench:
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

### How the work goes

The owner's files. After this Spec's Approval the session that runs this Run prepares the three files as whole files outside the repository and gives the owner one command that copies them. The session checks them against `The owner's files` and commits them before the Build begins. The new test command passes on the tree as it is before the Build, but for the tests of the deletion list, which the Build deletes.

The Build. It is one Build. A sound order is: the package and its tests (11-B1, 11-B2, 11-B5); the tool's package and tests (11-B3, 11-B6); the dropped cases (11-B4); the documents and records (11-B7 to 11-B11); the build files and the workflow (11-B13, 11-B14). The implementer removes the untracked `__pycache__` folders that a moved folder leaves behind, since Python imports such a leftover as an empty package.

Existing tests. Each keeps its name and its assertions unless a rule here says otherwise. The report lists every existing test that changed, moved or went, with the rule that covers it, and names every assertion that went. A test reads every document as UTF-8, named so.

The installed state on this machine. The implementer builds both wheels from a copy of the tree outside the clone, installs them into a fresh environment there, and runs the last step of each job as `The hosted workflow` gives it. If that environment cannot be built here, the report says so, and the hosted run is the evidence.

Identities. Moving and editing grading code changes every grader identity and `toolchain_revision`. No admission is lost, since none stands.

The hosted tests. Windows does not run on this machine. So the session that runs this Run commits the state of the Build to the Run's branch before the Feature is Done and pushes it, and gives the implementer and the Reviewer the run's result. The Feature is Done when the workflow has passed on all eight of its jobs for the tree the Reviewer passed, or when the owner accepts a run as it stands. This is a rule and not a numbered Behaviour.

A hosted failure. The session tells the owner what failed, whether it reads as a fault in a test, a document or a build file or as a fault in product code, and what a fix costs. The owner chooses between accepting the run and a fix. A fix of a test, a document or a build file is made in this Feature and reported as a Deviation.

## Out of scope

- A redesign of the benchmark: how it runs a case, grades it and reports it. Its commands, flags and record formats stay, but for what 11-B4 names.
- Names of the benchmark's code that only tests reach, and its readers of earlier report formats. They stay.
- A qualification of any case, a model run, and any physical operation.
- The 46 names of the tool that the benchmark's tests import beyond the client interface, `Controller` among them. The tests keep them.
- The keywords `autostart` and `read_timeout` and the variable `GENERATIVE_DRIVER_HOME`, which the benchmark uses and the interface document does not name. The document is not edited here.
- A lock file and a release check of the benchmark's own, and the declaration of `pyelftools`, which one of its modules imports.
- The tool's leftovers that serve any client or nobody: the refused request keys `renode` and `set_root`, the branch for an actor `evaluator` in `device_access.py`, and the redundant import scans in the tool's tests.
- Deleting `firmware-bench/` from this repository, and this Run's own folder. Both are the owner's, after the Run.
- The generator volume and the private repository. No session opens either.

## Deviations
