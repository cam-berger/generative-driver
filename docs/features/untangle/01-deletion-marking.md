# 01. deletion-marking

## Intent

The owner will delete the legacy benchmark from this repository once it has been copied to a repository of its own. Until then they need two things at every commit of this Run: a list that says exactly what that deletion removes, and proof that the tool still passes its tests without it. Neither exists today. Benchmark code, tests, data and documents sit among the tool's, and passages about the benchmark run through documents that stay.

This Feature writes the marking down once, for the whole repository. A deletion list names every benchmark item and says whether it is ready to delete or still used by the tool, with the Feature of this Run that frees it. A marker sets off benchmark passages in files that stay. A test carries the deletion out on a copy of the tree and runs the tool's own tests on what is left. Later Features move entries to ready as they free them, and the Run ends when nothing is still used.

Nothing is deleted from the repository. Nothing a user or a benchmark run sees changes, and no grader identity changes.

## Behaviours

In these Behaviours, "the files" are the files git tracks plus the untracked files it does not ignore. "The check" is `problems` and "the removal" is `strip`, both named under Interface and seams. "The benchmark package" is `generative_driver.benchmark` and `generative_driver.benchmark_support`, and `firmware_bench` once it exists.

- **01-B1** — `docs/benchmark-deletion-list.md` is the deletion list. It holds six tables, under these headings and with these columns, and nothing else decides what the removal deletes:
  - `Ready to delete`: Item, Note.
  - `Still used by the tool`: Item, Used by, Freed by.
  - `Tool modules that import the benchmark`: Module, Freed by.
  - `Commands`: Command, Where, Freed by.
  - `Passages`: File, Unmarked lines, Why, Freed by.
  - `Kept`: Item, Why.

  An Item, Module or File is a path from the repository root, written with `/`. An Item that ends in `/` covers every file under it. `Freed by` is the number of a later Feature of this Run, `02` to `10`; under `Passages` it may also be `stays`, for lines that are not about the benchmark. Under `Passages` a file has one row for each `Freed by` its unmarked lines have. The check fails, naming the entry, when a path matches none of the files, when two entries of `Ready to delete`, `Still used by the tool` and `Kept` cover the same file, when two rows of `Passages` have the same File and `Freed by`, or when a `Freed by` is missing or is neither of those forms.
- **01-B2** — Every file with a benchmark name is covered by an entry under `Ready to delete`, `Still used by the tool` or `Kept`. A file has a benchmark name when a part of its path is `bench` or `registrations`, starts with `bench-` or `bench_`, or contains `benchmark`, `firmware_bench` or `firmware-bench`. The check fails, naming each file that no entry covers.
- **01-B3** — Every Python file that imports the benchmark package, in any import statement at any depth, is accounted for:
  - under `tests/`, it is a benchmark item, covered by `Ready to delete` or `Still used by the tool`; so is a file under `tests/` that imports such a file;
  - anywhere else, unless an entry of `Ready to delete` or `Still used by the tool` covers it, it is listed under `Tool modules that import the benchmark`.

  The check fails, naming each file that is not accounted for, and each listed module that no longer imports the benchmark package.
- **01-B4** — A marked passage is every line from a begin marker through its end marker. The marker text is `legacy-benchmark:begin` and `legacy-benchmark:end`. A marker stands alone on its line, inside the file's own comment form: `<!-- legacy-benchmark:begin -->` in Markdown, `# legacy-benchmark:begin` in YAML, TOML and other files whose comments start with `#`. The check fails, naming the file and line, when a begin has no end, an end has no begin, one passage starts inside another, or a marker stands in a file that `Ready to delete`, `Still used by the tool` or `Kept` covers.
- **01-B5** — In every text file that is not Python source and that no entry of `Ready to delete`, `Still used by the tool` or `Kept` covers, each line outside the marked passages that contains `bench`, in any letter case, is an unmarked line. The number of unmarked lines in a file equals the sum of its `Unmarked lines` figures under `Passages`, and is zero for a file with no row there. The check fails, naming the file and both numbers, when they differ.
- **01-B6** — The removal writes a copy of the files into a directory the caller gives, then deletes from the copy every item under `Ready to delete` and every marked passage, markers included. It never writes inside the tree it reads. In the copy:
  - no Markdown file that no entry covers has a relative link to a file the copy lacks;
  - `pyproject.toml` parses.

  The removal returns each failure as a sentence that names the file and the link, or the parse error, and returns none when the copy passes.
- **01-B7** — When `GD_TEST_REMOVAL=1`, the test then runs the tool's test modules in the copy: every `tests/test_*.py` left there that is not a benchmark item under 01-B3. Each module runs in its own process, up to eight at a time. The child's `PYTHONPATH` holds only the copy's `src`, so it cannot import from the tree the copy was made from; the rest of its environment is inherited. The test fails unless every module passes, and names each failing module with the end of its output. So an item moved to `Ready to delete` while a tool test still needs it fails this test. Without the variable the test is skipped and says why. The last step of the Test command in `STANDARDS.md` sets the variable, after the other tests have finished, and so does the test step of `.github/workflows/test.yml`. A test asserts that the workflow's step sets it. The step in `STANDARDS.md` is the owner's, and no test asserts it.
- **01-B8** — `Commands` names every benchmark command the tool offers: `generative-driver benchmark`, and each tool of the tool's MCP server whose name contains `benchmark`. The check fails when the MCP server offers such a tool that the list does not name, when the list names one the server does not offer, and when the list and the command line disagree on whether `generative-driver benchmark --help` succeeds.
- **01-B9** — At the end of this Feature the list covers the whole repository: on the repository itself the check reports no problem, the removal passes 01-B6, and 01-B7 passes. The entries follow these rules:
  - An item is ready when deleting it, with every other ready item and every marked passage, leaves 01-B6 and 01-B7 passing, and no tool module, tool test, tool document, workflow or still-used item needs it to work as it does today. Otherwise it is still used: `Used by` names what needs it, and `Freed by` the Feature of the Breakdown that ends that need. An item that only a still-used item needs takes that item's `Freed by`. So every still-used test module can still be imported in the copy.
  - A test module that uses both the tool and the benchmark, by its imports and those of the helpers it imports, is still used, freed by `09`, unless an earlier Feature of the Breakdown moves its tool coverage into a tool test.
  - The marking's own files are ready: the list, `tests/test_deletion_marking.py` and `tests/deletion_marking.py`. The deletion removes them last.
  - Records are never edited or marked: the documents of other Runs under `docs/features/`, `docs/adr/`, `docs/superpowers/`, `docs/research/` and `.scratch/`. A record whose subject is the benchmark is a benchmark item, listed whole. Any other record that says `bench` is listed whole under `Kept`.
  - `Kept` also holds the published record, `docs/verification.md` and `bench/evidence/`, and this Run's own folder, `docs/features/untangle/`.
  - A passage is marked only when it is already a whole block (a paragraph, a list item, a section, a fenced block, or a table that holds only benchmark rows) and removing it leaves the document correct for a reader of the tool alone. Every other line that says `bench` is counted under `Passages`: `08` frees command lines and hosted-test lines, `09` frees the rest, and `stays` is for lines that are not about the benchmark, such as the hardware bench.

  The starting classification, which Build checks by reading each file; a difference is a Deviation. The table is complete for whole documents: a document it does not name is a tool document for now. `docs/codex-acceptance.md` is one. It describes acceptance through the benchmark's suite, so its lines that say `bench` are counted under `Passages`, and Feature 09 settles what moves.

  | Files | Side |
  |---|---|
  | `docs/benchmark-case-authoring.md`, `docs/benchmark-corpus.md`, `docs/generated-device-interface.md` | benchmark |
  | `docs/features/bench-harness/`, `docs/adr/0001`, `0003`, `0004`, `0005`, `0006`, `0007` | benchmark |
  | `docs/superpowers/plans/` `tq9-replacement`, `grader-identity`, `scorecard`, `generated-grader` | benchmark |
  | `.scratch/benchmark-baseline/`, `.scratch/benchmark-lifecycle/`, `.scratch/benchmark-review/` | benchmark |
  | `docs/features/kit-fixes/`, `docs/adr/0002`, the two `driver-contract-mcp` documents under `docs/superpowers/`, `docs/research/`, `.scratch/effect-labels/`, `.scratch/learning-loop/` | kept |
- **01-B10** — The benchmark's folders say that this is the legacy copy. `bench/README.md` carries the notice below directly under its title. `src/generative_driver/benchmark_support/README.md` is a new file that holds a title, `# Benchmark support`, and the same notice, with the link's path adjusted. Both files are on the list.

  > **This is the legacy copy of the benchmark.** The benchmark is moving to a repository of its own. This copy will be deleted once the move is complete. The [deletion list](../docs/benchmark-deletion-list.md) says what goes.

## Interface and seams

- The list, `docs/benchmark-deletion-list.md`, in the form 01-B1 gives. It is written for the owner to read and for the check to parse.
- The marker text of 01-B4.
- `tests/deletion_marking.py`, a test helper with three functions. Each takes the root of a git working tree and reads its files from git:
  - `problems(root)` returns every failure of 01-B1 to 01-B5 and 01-B8 as a sentence that names the path, and the line where there is one;
  - `strip(root, destination)` writes the stripped copy of 01-B6 into `destination` and returns that Behaviour's failures;
  - `tool_test_modules(root)` returns the test modules 01-B7 runs.
- `tests/test_deletion_marking.py` tests each rule through those functions, on small git repositories it builds in temporary directories, and tests the repository itself for 01-B9 and 01-B10. Each test name carries its Behaviour ID.
- `GD_TEST_REMOVAL=1` turns on 01-B7. The owner adds the last step to the Test command in `STANDARDS.md` before Build starts, since only the owner edits that file: `&& GD_TEST_REMOVAL=1 PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -v -s tests -p test_deletion_marking.py`.
- Outside a git working tree, the tests that read the repository are skipped and say why. Child processes are started from argument lists, with the interpreter that runs the test.

## Out of scope

- Deleting anything from the repository. No command or script that deletes in place is added: `strip` writes only into its destination.
- Any change to a `.py` file under `src/`, to `src/generative_driver/resources/bench/` or `resources/registrations/`, or anything else that changes a grader identity.
- Rewording or moving text in any document. Splitting lines that mix tool and benchmark content is Feature 09's work.
- Marking Python code. Imports and commands are listed, not marked.
- Editing a record, an approved document, `STANDARDS.md`, `docs/verification.md` or `bench/evidence/`.
- Acting on the notes in `.scratch/benchmark-review/`, or fixing a benchmark problem found while listing. Such a problem is reported as noticed.
- Running the dashboard's JavaScript test in the copy.

## Deviations
