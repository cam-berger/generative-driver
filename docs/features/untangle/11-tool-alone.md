# Spec 11: tool-alone

## Intent

For the owner, who wants Generative Driver running and up to date on its own first, and firmware bench adapted after it. The owner's words of 2026-11-18: "focus on one repo at a time, I want to have generative-driver running and updated, then we can adapt firmware-bench".

Generative Driver writes a driver and an MCP server for a new device. After Feature 10 no tool module imports the benchmark. But the benchmark's code and resources still lie inside the tool's package, the tool's documents describe both together, and this Run's deletion list and its check stand among the tool's tests.

This Feature makes the tool stand alone. Everything of the benchmark leaves the tool's package, documents, tests and build files. The benchmark's files are parked in `firmware-bench/`, at the places Feature 12 uses, moved and not changed. They do not run from there until Feature 12 adapts them.

What is different afterwards:

- `src/` holds the tool alone. Its wheel holds no benchmark file, it no longer requires `cryptography`, and `doctor` no longer looks for the two programs that only the benchmark runs.
- The tool's documents describe the tool: the front page, the guides, the design, three decision records, the glossary, the project rules and the client skill say nothing of the benchmark.
- This Run's deletion list, its marker lines and its check are gone, and so are the past results and the tool's own finished notes.
- The test command and the hosted tests run the tool alone.
- `firmware-bench/` holds the benchmark's code, resources, tests, documents and notes, parked.

Decisions taken in this Spec. Its Approval covers them.

- Parked means moved with `git mv` and not edited. Between this Feature and the next the benchmark does not run, and nothing tests it. The first line of its front page says so.
- Kept, though a reader may call them old: the Specs of the kit-fixes Run in `docs/features/kit-fixes/`, because 19 source lines and 17 test files cite them as the decision behind a module, as `STANDARDS.md` asks; and the parked learning-loop spec with its research note, because it is parked and not given up.
- The Reviewer's count of changed lines is not held to a limit. The moves alone are several thousand lines.

Conventions.

- Line numbers are those of commit `ffa9880`.
- A sentence of `docs/design.md` is named by its line and its place on it: 113.2 is the second sentence of line 113. The bold title that opens a list item is not counted as a sentence.
- The tool's documents are `README.md`, `CONTRIBUTING.md`, `NOTICE`, `AGENTS.md`, `CONTEXT.md`, `STANDARDS.md`, the `.md` files directly in `docs/`, in `docs/adr/` and in `docs/agents/`, as they are after the moves, and both copies of the client skill. The research note, the issues under `.scratch/` and the Specs under `docs/features/` are records and are not among them.
- "Goes" means the text is cut. Nothing is kept elsewhere.
- The marker lines are the two comment lines of Feature 01 that set off a benchmark passage. This Spec does not print them.

## Behaviours

- **11-B1** — Everything of the benchmark is parked in `firmware-bench/`.
  - The moves of the table `What is parked` are made with `git mv`. No moved file is edited, but for one line: `firmware-bench/README.md` begins with the paragraph "This folder holds firmware bench while it is adapted into a package of its own. Its code does not run from here yet.", and an empty line after it.
  - Afterwards `src/generative_driver/` holds no `benchmark.py`, no `benchmark_support/`, no `resources/bench/` and no `resources/registrations/`, tracked or untracked. The folder `bench/` is gone. `docs/` holds no benchmark document, and `.scratch/` no benchmark folder.
  - `src/generative_driver/benchmark_support/README.md` is deleted and not moved. It says only that the folder is a legacy copy.
  - `firmware-bench/AGENTS.md`, `firmware-bench/CONTEXT.md` and `firmware-bench/STANDARDS.md` are copies of the three root files as they are at commit `ffa9880`, byte for byte. They keep the benchmark's terms and rules at hand for Feature 12, since the root files lose them.
  - Nothing under `firmware-bench/` is imported, built or run by the test command or by the hosted tests.

- **11-B2** — The tool's package holds nothing of the benchmark, and the tool runs.
  - Root `pyproject.toml` has no script `firmware-bench`, no marker lines, and the dependencies `["mcp>=2.2,<3", "jsonschema>=4.20,<5"]`. `uv.lock` agrees with it.
  - A wheel built from the root holds no path with `benchmark`, `resources/bench` or `resources/registrations` in it.
  - `doctor` no longer reports `renode` or `arm-none-eabi-gcc`. It reports `codex`, `java` and `podman` as today.
  - With that wheel installed in a fresh environment and no checkout on the search path, `generative-driver doctor` runs, `generative-driver smoke` passes, and the MCP server offers its eight tools.
  - Nothing else of the tool's behaviour changes. The refusals of a client's request keys, the code identity and the client interface with its document stay as they are; `docs/client-interface.md` is not edited.

- **11-B3** — The tool's tests need nothing of the benchmark, and the deletion list is gone.
  - Deleted: `docs/benchmark-deletion-list.md`, `tests/deletion_marking.py`, `tests/test_deletion_marking.py`, `tests/test_service_without_legacy_cases.py`.
  - No tracked file outside `docs/features/untangle/` and `firmware-bench/` holds a marker line. The passages that stood between marker lines go with them.
  - `tests/test_tool_boundary.py` stays the tool's boundary test: no tool module and no tool test reaches the benchmark's code.
  - The tool tests that held a marker line, the removal, the workflow's last step, the doctor's five programs or a benchmark resource hold what this Spec gives. The table `The tool's tests` names them.

- **11-B4** — The tool's documents hold no benchmark text.
  - Each place the table `The tool's documents, place by place` names is changed as it says.
  - No line of a tool's document holds one of these words, in any letter case: `bench`, `harness`, `suite`, `trial`, `scorecard`, `scoring set`, `answer key`, `answer vault`, `groundtruth`, `leak check`, `pass rate`, `mutant`, `renode`. Left out: the paragraph of `README.md` that points to the benchmark (11-B10), and the sentence of `STANDARDS.md` on "the serial suite" of tests.
  - Every relative link of a tool's document resolves, and none leads into `firmware-bench/` but the one of that paragraph.
  - Nothing else in a tool's document changes.

- **11-B5** — `docs/design.md` is the tool's design.
  - The items and sentences that the tables under `docs/design.md` name are cut or reworded as they say. Every other sentence stays word for word.

- **11-B6** — The client skill is the tool's alone.
  - Both copies hold the text given under `Interface and seams`, and are equal.
  - Every tool name of the form `driver_…` in it is a tool of the MCP server.

- **11-B7** — The tool's outdated records and notes are deleted.
  - The table `Records and notes` is carried out.

- **11-B8** — The owner's three files hold the texts given under `The owner's files`.
  - The owner changes them by hand before the Build, as `How the work goes` says.

- **11-B9** — The build files, the workflow and the release check follow.
  - `MANIFEST.in` loses lines 6, 10, 11, 12 and 15 to 17.
  - `.gitignore` loses lines 13 and 14, and line 18 reads `# Generated local device packages and reports stay outside Git.`
  - `.gitattributes` line 1 reads `# Hashes of source and resource bytes must be the same on every host.`
  - `NOTICE` lines 7 to 10 read: "Third-party materials retain their original notices and licenses. External Codex, Ghidra, compiler, serial and USB dependencies are separately distributed under their respective licenses.", wrapped as the file wraps.
  - `.github/workflows/test.yml` keeps its one job. Its last step loses the line that sets `GD_TEST_REMOVAL` and the passage between marker lines, so it runs the tool's tests and then the smoke.
  - `.github/scripts/tool_releases.py`: `WATCHED` holds the two workflow patterns and `firmware-bench/README.md`, where the parked front page keeps the emulator's pin. Its module doc names that file where it named `bench/README.md`. The text of an issue loses the sentence "Upgrading changes benchmark evidence." and the words "and re-run native calibration before comparing results across versions"; the sentence before them ends "and run the affected workflows."
  - `.github/workflows/tool-releases.yml` line 3 names `firmware-bench/README.md` where it named `bench/README.md`. `.github/dependabot.yml` stays as it is.
  - `src/generative_driver/setup.py`: the doctor's table of programs loses `renode` and `arm-none-eabi-gcc`.

- **11-B10** — Outside `firmware-bench/`, only named places speak of the benchmark's folder.
  - In tracked files outside `firmware-bench/`, `docs/features/` and `tests/`, the texts `firmware-bench` and `firmware_bench` stand only in: the release check's three places of 11-B9; and one paragraph of `README.md`, after the table of lines 51 to 58, which reads "Firmware bench, the benchmark that evaluates this tool, lies in [firmware-bench/](firmware-bench/README.md) until it moves to a repository of its own."
  - In `tests/`, those texts stand only where a test forbids the import of the benchmark or holds this list.

## Interface and seams

### What is parked

| From | To |
|---|---|
| `src/generative_driver/benchmark.py` | `firmware-bench/src/firmware_bench/benchmark.py` |
| `src/generative_driver/benchmark_support/`, without its `README.md` | `firmware-bench/src/firmware_bench/benchmark_support/` |
| `src/generative_driver/resources/bench/` | `firmware-bench/src/firmware_bench/resources/bench/` |
| `src/generative_driver/resources/registrations/` | `firmware-bench/src/firmware_bench/resources/registrations/` |
| `bench/README.md` | `firmware-bench/README.md` |
| `bench/groundtruth/README.md` | `firmware-bench/docs/evaluator.md` |
| `docs/benchmark-case-authoring.md` | `firmware-bench/docs/case-authoring.md` |
| `docs/benchmark-corpus.md` | `firmware-bench/docs/corpus.md` |
| `docs/generated-device-interface.md` | `firmware-bench/docs/generated-device-interface.md` |
| `docs/codex-acceptance.md` | `firmware-bench/docs/codex-acceptance.md` |
| `docs/adr/0001-…`, `0003-…`, `0004-…`, `0005-…`, `0006-…`, `0007-…` | `firmware-bench/docs/adr/`, same names |
| `.scratch/benchmark-baseline/`, `.scratch/benchmark-lifecycle/`, `.scratch/benchmark-review/` | `firmware-bench/.scratch/`, same names |

`firmware-bench/tests/` stays where it is. The moved code keeps its imports as they are, so it cannot be imported from its new place. That is meant: Feature 12 adapts it. The implementer removes the untracked `__pycache__` folders that a moved folder leaves behind, since Python imports such a leftover as an empty package.

### The tool's tests

| File | What happens |
|---|---|
| `tests/test_tool_commands.py` | Holds the workflow as 11-B9 gives it: its last step word for word and the SHA-256 of the file's text before that step. Its tests of a passage between marker lines go, and its test of the workflow's lines holds that no line says `bench`. Its test that reads `resources/registrations/cases.json` goes. The three install blocks of `README.md` stay pinned and unchanged. |
| `tests/test_client_interface.py`, `tests/test_supplied_runs.py` | The assertions that name the marker text go. |
| `tests/test_doctor.py` | The doctor's programs are `codex`, `java` and `podman`. |
| `tests/test_tool_releases.py` | Writes its made-up pins into a workflow file, where it wrote a `bench/README.md`. |
| `tests/test_tool_boundary.py` | The one file it leaves out of its scan of strings, beside itself, is `tests/test_tool_alone.py`, where it left out the two files of the deletion list's check. |
| `tests/test_supplied_runs.py`, `tests/test_client_calls.py`, `tests/test_supplied_stages.py`, `tests/test_client_surface.py`, `tests/test_tool_commands.py` | The module docstring of each names "the benchmark's tests" where it named a file under `firmware-bench/tests/`. |
| `tests/test_checkout.py`, `tests/test_distribution.py` | Unchanged. |

New: `tests/test_tool_alone.py` holds 11-B1 (the folders that are gone from the tool), 11-B2 (the build file, the doctor, and with `GD_TEST_WHEEL` that the wheel holds no benchmark path), 11-B3 (no marker line), the release check's part of 11-B9 and 11-B10. It is the one tool test file that may hold the benchmark's names as text. `tests/test_tool_documents.py` holds 11-B4 (the word scan, with a test on planted text that shows it sees each word and the places it leaves out; the links) and 11-B6.

Every other tool test keeps its name and its assertions. The scans in other tool test files that no tool module imports the benchmark stay as they are. Each existing test keeps its name unless it states a value or a place that changed. The report lists every existing test that changed or went, with the rule that covers it, and names every assertion that went. A test reads every document as UTF-8, named so.

Behaviours without a test of their own. The Reviewer checks these from the diff and the report: of 11-B1 that no moved file was edited; 11-B5, sentence by sentence; 11-B7; 11-B8; and of 11-B9 the lines of `MANIFEST.in`, `.gitignore`, `.gitattributes` and `NOTICE`.

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
| `README.md`, after the table of lines 51 to 58 | One new paragraph, the one that 11-B10 gives. |
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
| `NOTICE:7-10` | As 11-B9 gives it. |

A link of a tool's document that led to a moved or deleted file goes with the words that only lead to it, where no row above settles it. The implementer reports each such place.

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

If a sentence that stays still holds a word of 11-B4 after these changes, the implementer changes that word alone and reports it as a Deviation.

### Records and notes

| Item | What happens |
|---|---|
| `docs/verification.md`, `bench/evidence/` (18 files) | Deleted. The owner decided it. |
| `docs/research/2026-10-28-mhs-interface-review.md` | Deleted. It reviews a state before the generated MCP server existed. |
| `.scratch/effect-labels/spec.md` | Deleted. It is resolved. |
| `.scratch/effect-labels/issues/01-observe-read-state-changes.md` | Kept. It names the code's place of today, `src/generative_driver/supplied/instrument_evidence.py`, and loses its link to the deleted spec. |
| `.scratch/learning-loop/spec.md`, `docs/research/2026-10-30-firmware-learning-practice.md` | Kept. In the spec, the two links of lines 47 and 51 to benchmark files lose their targets and keep their words as plain text, and line 65 says the branch became pull request 24. |
| `docs/features/kit-fixes/`, `docs/features/untangle/` | Kept. |

### The owner's files

`AGENTS.md`. Lines 5, 8, 9, 10 and 11 read as below. Every other line stays.

    - Develop through three public seams: CLI/MCP, configurator/workers, runtime/package. For each code change, observe one behavior test fail, implement it, then observe it pass.

    - Fresh reuse receives only the package and objective. Under `assistive/1`, stages hand off with notes; device differences drive bounded repairs, then continue with the best revision. Record one final equivalence verdict: the configurator's own, labelled self-evidence, or the one posted through the client interface for a run on a supplied device. Never silently retry an uncertain write.

    - Keep worker claims, accepted handoffs and posted verdicts separate. Hashes establish provenance and integrity.

    - The configurator and workers receive neither passwords nor their paths or plaintext answers. Describe isolation accurately: workers are not read-isolated, and hashes are not a sandbox. Distinguish model runs, scripted tests, replay, emulation and physical evidence.

    - Keep docs concise, concrete and executable. Define terms on first use; remove repeated explanations. Preserve notices and private research artifacts. Launch model runs or physical operations only when explicitly assigned by the primary agent.

`STANDARDS.md`. Line 12 goes. Line 13 reads "- The configurator and workers never receive a password, its path or a plaintext answer." Lines 29, 31 and 39 read as below. Every other line stays.

    Test command: `ls tests/test_*.py | PYTHONPATH="$PWD/src" xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\n" "$out"; exit $s' && node tests/dashboard-client.test.js`

    Test files: `tests/test_*.py`

    - Tests use temporary homes. They never touch a real password, device or model run.

`CONTEXT.md`. The thirty-one rows of lines 26 to 29, 31 to 36 and 38 to 58 go. Rows 16, 21, 30 and 37 and the paragraph of line 60 read as the first five texts below. The last sentence of line 62, on the pass rate, goes. One row is new, the sixth text below, after the row `Stand-in device`. Every other line stays.

    | Equivalence | One final verdict: the configurator's own, with basis `self-evidence`, or the one posted through the client interface for a run on a supplied device, with basis `evaluator`. Completion alone does not mean success. |

    | Effect grant | Permission for a driver call to write, actuate or reset; reads need none. The operator grants a run's effects; a call needing an ungranted effect is refused before any device I/O. |

    | Instrument observation | Device-state readings visible to workers, never grades. |

    | Public mission | Required task meanings, inputs, output units and the objective for fresh reuse. |

    Functional compatibility compares required outputs/effects, not source code. Hashes prove identity/integrity, not physical truth or access control. Keep passwords and answers out of worker inputs; the configurator holds neither.

    | Supplied device | A device that a client of the [client interface](docs/client-interface.md) owns, starts and stops, and describes to the service. A run on one ends `completed` and waits for the one verdict that client posts. |

### How the work goes

The owner's files. After this Spec's Approval the session that runs this Run prepares the three files as whole files outside the repository and gives the owner one command that copies them. The session checks them against `The owner's files` and commits them before the Build begins. The new test command passes on the tree as it is before the Build, but for the tests of the deletion list, which the Build deletes.

The installed state on this machine. The implementer builds the wheel from a copy of the tree outside the clone, installs it into a fresh environment there, and runs the workflow's last step as 11-B9 leaves it. If that environment cannot be built here, the report says so, and the hosted run is the evidence.

The hosted tests. Windows does not run on this machine. So the session that runs this Run commits the state of the Build to the Run's branch before the Feature is Done and pushes it, and gives the implementer and the Reviewer the run's result. The Feature is Done when the workflow has passed on all four of its jobs for the tree the Reviewer passed, or when the owner accepts a run as it stands. This is a rule and not a numbered Behaviour.

A hosted failure. The session tells the owner what failed, whether it reads as a fault in a test, a document or a build file or as a fault in product code, and what a fix costs. The owner chooses between accepting the run and a fix. A fix of a test, a document or a build file is made in this Feature and reported as a Deviation.

## Out of scope

- Everything under `firmware-bench/` beyond the move: making it a working package, its build file, its tests, its documents, its rules and glossary, the dropped cases, and the hosted job that tests it. Feature 12 does that.
- The rule on grading code. This Feature moves the grading code and does not edit it. `STANDARDS.md` loses the rule's line, and Feature 12 writes it into the benchmark's own rules.
- The tool's leftovers that serve any client or nobody: the refused request keys `renode` and `set_root`, the branch for an actor `evaluator` in `device_access.py`, and the redundant import scans in the tool's tests.
- A qualification of any case, a model run, and any physical operation.
- Deleting `firmware-bench/` from this repository, and this Run's own folder. Both are the owner's, after the Run.
- The generator volume and the private repository. No session opens either.

## Deviations
