# Benchmark deletion list

The benchmark is moving to a repository of its own. Once it has moved, the owner deletes the legacy copy from this repository. This list says what that deletion removes, and what the tool still needs until a later Feature of the [untangle Run](features/untangle/breakdown.md) frees it.

`tests/deletion_marking.py` checks the list against the repository and carries the deletion out on a copy. `tests/test_deletion_marking.py` runs the check, and runs the tool's tests in that copy when `GD_TEST_REMOVAL=1`. Nothing is deleted from the repository itself.

## How to read it

- A path is written from the repository root. A path that ends in `/` covers every file under it.
- A row under `Ready to delete` may name an item inside a folder that has a row under `Still used by the tool`. The narrower row decides for what it covers: the deletion removes that item and leaves the rest of the folder.
- `Freed by` is the number of the Feature that ends the tool's need. That Feature moves the item to `Ready to delete`, or takes its row out of the other tables.
- A benchmark passage in a file that stays sits between two marker lines. The marker text is `legacy-benchmark:begin` and `legacy-benchmark:end`. It is written as an HTML comment in Markdown and as a `#` comment elsewhere. The deletion removes each marked passage with its markers.
- Only whole blocks are marked. Every other line that says `bench` is counted under `Passages`.

## Ready to delete

Nothing that stays needs these. The deletion removes them.

| Item | Note |
|---|---|
| `docs/benchmark-deletion-list.md` | This list. The deletion removes it last. |
| `tests/deletion_marking.py` | The check and the removal behind this list. Removed last. |
| `tests/test_deletion_marking.py` | The tests of this list. Removed last. |
| `docs/generated-device-interface.md` | Benchmark document. Only benchmark items link to it. |
| `docs/adr/0001-benchmark-registration-and-selection.md` | Benchmark decision record. `docs/verification.md` links to it. |
| `docs/adr/0003-generated-devices-register-by-set.md` | Benchmark decision record. |
| `docs/adr/0004-compare-systems-through-a-scoring-set.md` | Benchmark decision record. |
| `docs/adr/0005-keep-the-device-generator-private.md` | Benchmark decision record. |
| `docs/adr/0006-seal-graded-scoring-trials.md` | Benchmark decision record. |
| `docs/adr/0007-one-validator-defines-a-generated-device.md` | Benchmark decision record. |
| `.scratch/benchmark-baseline/` | Benchmark spec and its issue, a record. |
| `.scratch/benchmark-review/` | Benchmark review notes, a record. |
| `src/generative_driver/benchmark.py` | The benchmark's command and the calls behind it. No file that stays imports it. |
| `src/generative_driver/benchmark_support/` | The benchmark's code. No file that stays imports it. `cases.py`, `physical.py`, `recorded_grading.py` and `tq9_legacy.py` belong to a dropped legacy case and are not carried to the benchmark's repository. |
| `src/generative_driver/resources/bench/` | The benchmark's cases, missions and answer keys. Only the benchmark's code reads it. `cases/tq9/`, `cases/bme280/`, `groundtruth/tq9.enc` and `groundtruth/bme280.enc` belong to a dropped legacy case and are not carried to the benchmark's repository. |
| `src/generative_driver/resources/registrations/` | The registered cases and sets. Only the benchmark's code reads it. |
| `firmware-bench/tests/` | The benchmark's tests and their helpers. No file that stays imports one. |

## Still used by the tool

Documents and build files that stay name these today. Deleting one now would break something that stays.

| Item | Used by | Freed by |
|---|---|---|
| `bench/README.md` | `README.md`, `docs/codex.md`, `docs/codex-acceptance.md` and `docs/design.md` link to it; `.github/scripts/tool_releases.py` reads its pins | 11 |
| `bench/groundtruth/README.md` | `docs/codex-acceptance.md` links to it; `NOTICE` names it | 11 |
| `docs/benchmark-case-authoring.md` | `docs/design.md` links to it | 11 |
| `docs/benchmark-corpus.md` | `README.md` links to it | 11 |
| `.scratch/benchmark-lifecycle/` | `MANIFEST.in` puts it in the source archive, because `docs/verification.md` links to it | 11 |

## Tool modules that import the benchmark

No tool module imports the benchmark package.

| Module | Freed by |
|---|---|

## Commands

The tool offers no benchmark command. The benchmark's own command is `firmware-bench`.

| Command | Where | Freed by |
|---|---|---|

## Passages

Lines that say `bench` outside the marked passages, in files that stay. `stays` is for a line that is not about the benchmark.

| File | Unmarked lines | Why | Freed by |
|---|---|---|---|
| `.github/workflows/tool-releases.yml` | 1 | Names the pins in `bench/README.md` | 11 |
| `.gitignore` | 3 | Keeps benchmark runs and plaintext answers out of Git while the legacy copy is here | 11 |
| `AGENTS.md` | 3 | Project rules whose lines cover the tool and the benchmark together | 11 |
| `CONTEXT.md` | 6 | Terms of the benchmark in the shared glossary. Only the owner edits this file | 11 |
| `CONTRIBUTING.md` | 3 | Sentences that cover the tool and the benchmark together | 11 |
| `MANIFEST.in` | 3 | Puts a benchmark record in the source archive and keeps benchmark runs and plaintext answers out of it | 11 |
| `MANIFEST.in` | 1 | Puts `bench/evidence/`, which is kept, in the source archive | stays |
| `NOTICE` | 1 | Names `bench/groundtruth/README.md` and the benchmark evidence | 11 |
| `README.md` | 3 | Two rows of a table that also holds tool rows, and one mixed paragraph | 11 |
| `README.md` | 1 | Links to the published record under `bench/evidence/`, which is kept | stays |
| `STANDARDS.md` | 4 | Rules about the bench harness, and the test command and the test files, which name the benchmark's test folder. Only the owner edits this file | 11 |
| `docs/agents/domain.md` | 1 | One sentence names the benchmark beside the tool's parts | 11 |
| `docs/codex-acceptance.md` | 4 | Describes acceptance through the benchmark's suite, and gives a file of the benchmark's test folder as the acceptance command. Feature 11 settles what moves | 11 |
| `docs/codex.md` | 6 | Mixed paragraphs. `firmware-bench/tests/test_assistive_wording.py` requires the wording of one | 11 |
| `docs/dashboard.md` | 1 | One sentence of a tool paragraph | 11 |
| `docs/design.md` | 16 | The accepted design describes the tool and the benchmark together | 11 |
| `docs/design.md` | 1 | Links to the published record under `bench/evidence/`, which is kept | stays |
| `docs/extending-drivers-and-mcp.md` | 2 | One sentence in each of two tool paragraphs | 11 |
| `docs/setup.md` | 1 | A paragraph about line endings whose reason is benchmark hashes | 11 |
| `docs/support.md` | 2 | A table row and a paragraph that cover the tool and the benchmark together | 11 |
| `plugins/generative-driver/skills/generative-driver/SKILL.md` | 5 | The client skill covers benchmark runs in steps it shares with tool runs. Not marked: an agent reads the file as text, so a marker would change what it sees | 11 |
| `src/generative_driver/resources/client/skills/generative-driver/SKILL.md` | 5 | The packaged copy of the client skill. A test keeps the two copies the same | 11 |

## Kept

These stay. The deletion does not touch them, and no passage in them is marked.

| Item | Why |
|---|---|
| `docs/verification.md` | The published record of past results. |
| `bench/evidence/` | The evidence files of the published record. |
| `docs/features/untangle/` | This Run's own folder. |
| `docs/features/kit-fixes/` | Record of the kit-fixes Run. |
| `docs/adr/0002-driver-contract-and-generated-mcp.md` | Decision record of the tool. |
| `docs/adr/0008-run-the-installed-runtime-over-package-data.md` | Decision record of the tool. |
| `docs/adr/0009-device-first-interpretation.md` | Decision record of the tool. |
| `docs/research/` | Research notes, a record. |
| `.scratch/effect-labels/` | Spec and issues of the tool, a record. |
| `.scratch/learning-loop/` | Parked spec of the tool, a record. |
