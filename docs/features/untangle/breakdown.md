# Breakdown: separate the driver tool from the benchmark

For the owner, who is moving the benchmark to a repository of its own and will evaluate the tool from outside. Today the tool and the benchmark import each other, so the benchmark cannot be lifted out. This Run pulls them apart in place, until they are two installable packages that only share this repository, and it marks every legacy benchmark item for the deletion that follows the move.

## Out

- Deleting the benchmark. That is a later step with its own go-ahead from the owner.
- Any change in behaviour, with six exceptions the owner has decided:
  - Benchmark runs and suites are started from the benchmark's own command.
  - The two legacy cases, the old tq9 and the physical BME280, can no longer be run, from Feature 04 on. The benchmark then has no case on real hardware until one is rebuilt.
  - The tool no longer judges the fresh worker's mission during a run, from Feature 05 on. It keeps recording the worker's calls and the readings after them, and the client judges the mission after the run and reports it in its verdict.
  - The records a client sends and reads, the verdict record and the device descriptor, are put in the tool's own terms in Feature 06.
  - Four names that say `benchmark` in the tool are put in the tool's terms in Feature 06: the worker tool `benchmark_package_execute`, the schemas `benchmark-capabilities/2` and `benchmark-ground-evidence/3`, and the subfolder `benchmark/` of a run, where the tool keeps its own records. The owner decided this on 2026-11-16.
  - The benchmark package is renamed `firmware_bench`, with the command `firmware-bench`.

  The change to the records edits grading code, so it changes every grader identity and voids every admission from Feature 06 on, and the rename does so again. No generated set is registered, and the hand-made cases are due to be requalified in any case.
- Keeping old records readable. On 2026-11-15 the owner decided to move past them: runs, sealed evidence and reports recorded before Feature 06 need not be readable after it, and new records come from later runs.
- Fixes to the benchmark, and the 13 notes in `.scratch/benchmark-review/`. Where a Feature must choose between what benchmark runs do today and a tool change the benchmark has not adopted, it keeps what they do today and says so.
- Windows and Python 3.11 stay supported for the tool. While the benchmark is still here, the hosted tests keep running it on both as well.
- The generator volume and the private repository. No session of this Run opens either. Test devices stay hand-written.

## Counted at the start

On `main` `cc9ac6d`, from import statements, including those inside functions:

- `src` holds 33,089 lines of Python in 121 files, without packaged resources. The benchmark is 16,312 of them: `benchmark.py` and the 66 files of `benchmark_support`. The service, `configurator.py`, is 3,991.
- 6 tool modules import benchmark code, at 51 import lines: the service 38, `reporting` 6, `mcp` 4, and `cli`, `replay` and `scripted_worker` 1 each. Together they reach 13 benchmark modules.
- 12 benchmark modules import tool code, at 60 import lines. They reach 8 tool modules and `interface_runtime`. Six of them import names from the service itself, private ones among them.
- Grading code, as `grader_identity` computes it, is 35 modules. The other 31 benchmark modules include the stage code that stays with the tool.
- `tests/` holds 100 test modules and 13 helper modules. By their imports, 43 test modules use both sides, 25 only the benchmark, 28 only the tool and 4 neither.
- No generated set is registered.

## Features

Each Feature ends with the public test command passing, and moves what it frees on the deletion list to ready. The grading-code boundary test keeps passing throughout.

On 2026-11-15 Feature 04, legacy-cases, was added, and every Feature after it moved up by one. The Specs of Features 01 to 03 were approved before that and name the later Features by their old numbers: what they call Features 04 to 10 are Features 05 to 11 here. Feature 04 brings the numbers in the deletion list, in its check and in the interface document up to date.

The tool keeps one small check of its own, through this Run and after it, that it can still make a driver. `generative-driver smoke` runs all six stages through the service on a stand-in device with a scripted worker, and passes only when the run ends with an equivalent self-evidence verdict on the driver it emitted. It uses no model, no hardware and nothing of the benchmark, and the hosted tests run it on every push. No Feature removes it or ties it to the benchmark.

The published record of past results, `docs/verification.md` and `bench/evidence/`, is no longer kept. Feature 01 listed it as kept, and Feature 10 puts it on the deletion list with the benchmark's other documents.

This Run goes side by side with `kit-fixes`. Features 03 and 05 are written against whatever `kit-fixes` Feature 05 has landed by then, and Features 06 and 07 against its Feature 06. If one of those lands later, this Run's Feature is the one brought up to date, since it changes no behaviour and the other does.

On 2026-11-16 `main`, with `kit-fixes` Features 05 to 07, was merged into this Run at `d6eefa5`. Nine tests fail on that merge. The kit allows no source edit outside a Feature's Build, so the owner decided that Feature 06 brings what arrived in line with Features 01 to 05 as its first part. The owner also decided that Feature 06 stays one Feature, and that a run may carry a client record: a small JSON object the tool stores, never reads and gives back, where the bench harness keeps the case's name, its pin and the hash of its frozen inputs until the suite and report code no longer read them from the run.

01. deletion-marking — one deletion list names every legacy benchmark item as ready to delete or as still used by the tool, with the Feature that frees it; one fixed marker sets off the benchmark passages of files that stay; a test deletes what is marked from a copy of the tree and runs the tool's tests on the rest; and the benchmark's folder and README say that this is the legacy copy
02. shared-helpers — the helpers the tool needs for its own work, which hash an artifact, stop a run for integrity and word a verdict, get one home on the tool's side, and the tool stops importing those from the benchmark folder; the helpers that serve a benchmark case or the run report get no copy in the tool, and are settled with that code in later Features; grading code keeps its own copies, and one benchmark test holds them to the tool's answers
03. client-interface — one short document and one module, with a version, say what a client that brings its own device and mission can ask of the tool and what it gets back: start a run on a supplied device, replace a device, post one final verdict, and read the run's records and its frozen driver
04. legacy-cases — the old tq9 and the physical BME280 are dropped: they are no longer registered, the service loses the branches that exist only for them, and nothing that stays imports their code; their code and resources are on the deletion list as ready to delete, each marked as a dropped case that is not carried to the benchmark's repository; their tests are removed, and what a test covered with the old tq9 as a stand-in is covered on a current case
05. supplied-device-stages — the code that runs the six stages on a supplied device moves to the tool's side, behind the interface, and works from the device and mission the client supplied; the tool no longer judges the fresh worker's mission during a run
06. benchmark-runs — what arrived with `main` is brought in line with Features 01 to 05; starting a benchmark run, freezing its inputs and recording its verdict leave the service, and the bench harness does them through the interface, as a client that supplies the device, the firmware and the mission; the verdict record and the device descriptor are put in the tool's own terms, without the fields the benchmark shaped; and the four names that say `benchmark` in the tool are renamed
07. suites — the bookkeeping of a suite leaves the service, to the home this Feature's Spec settles
08. reports — the tool's report code and the benchmark's reports stop importing each other, after which the service imports no benchmark module and the benchmark calls the tool only through the interface
09. commands — the benchmark's command line and MCP tools become its own entry point; the tool's command line and MCP server stop importing them and keep what a user with a device needs, plus the way to accept one verdict from an outside grader; and the benchmark's smoke case comes out of the tool's hosted tests, where the tool's own smoke stays
10. two-folders — tests and documents split into a tool folder and a benchmark folder, with approved documents moved byte for byte; the published record of past results joins the benchmark's documents and the deletion list; the tool's tests pass with the benchmark folder absent; and a boundary test fails if a tool module imports the benchmark, or if the benchmark imports a tool name the interface does not list
11. two-packages — the tool and the benchmark become two installable packages, each with its own build file; the benchmark takes the name `firmware_bench` and the command `firmware-bench`; the hosted tests build both, test the tool alone, then both together; and one `uv` lock file serves both

## Closing

When Feature 11 is done, nothing on the deletion list is still used by the tool, and the test that removes every listed item passes. The Run's closing record names the Run's last commit, because the benchmark is copied out from it.
