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

- `problems` checks the list's form first (missing list, missing table, wrong columns, a row with the wrong number of cells) and returns only those faults when there are any — 01-B1 says the list holds six tables with those columns, and a list that breaks the form cannot be held to the tree.
- A benchmark item under `tests/` is found through any chain of imports between files under `tests/`, not one level — it applies 01-B3's "a file under `tests/` that imports such a file" repeatedly, so no test that reaches the benchmark runs in the copy.
- A Python file that does not parse is a problem — 01-B3 accounts for every Python file, and an unreadable one cannot be accounted for.
- The marker form is chosen by file name: `.md` takes the HTML comment, every other file the `#` comment. A marker in the other form is plain text and is counted as an unmarked line — 01-B4 says "the file's own comment form" and gives exactly these two forms.
- A stray marker (no partner, or nested) is reported once under 01-B4 and not also counted under 01-B5 — one fault gives one sentence and no failure is hidden.
- An `Unmarked lines` figure must be a whole number, and a row for a file with nothing countable is held to zero — 01-B5's sum needs numbers and already says zero for a file with no row.
- `strip` raises `ValueError` when the destination is the tree, lies inside it, or exists and is not empty — 01-B6 says it never writes inside the tree it reads, and a used destination could pass a link check on stale files.
- `strip` also returns the list's form faults as failures — otherwise a missing list gives a full copy that passes as stripped, against the Intent's proof.
- The link check covers inline links, images and reference definitions. It skips fenced blocks, code spans, links with a scheme, `#` anchors and paths that start with `/`. `pyproject.toml` is parsed when the copy has one, and file modes are copied — a reading of 01-B6's "relative link to a file the copy lacks" that adds no scope.
- 01-B8 takes `src/generative_driver` of the tree as the tool. It asks the command line with `python -m generative_driver benchmark --help` and the MCP server over stdio. A tree without those files offers no command. The `Where` column is not checked — 01-B8 names no check of `Where`, and the Spec tests each rule on small trees.
- Children get a `PYTHONPATH` of the `src` of the tree under check, not the environment of `tests/package_env.py`, for 01-B8 as for 01-B7 — 01-B7 states this environment in its own words, and the 01-B8 children must answer for a small tree.
- The runner of 01-B7 lives in `tests/test_deletion_marking.py`. A module has 300 s, a timeout is a failure, and the last 40 lines of output are kept — the Spec gives the helper three functions and says the test runs the modules.
- An extra 01-B9 test checks that every test file left in the copy imports only test files left there — it checks a rule 01-B9 states and adds no behaviour.
- The notice is written as a block quote — the text is the Spec's, word for word, in both READMEs.
- "Uses both the tool and the benchmark" counts tool use reached through any imported file under `tests/`, other test modules included. That gives 57 test modules that use both and 15 that use only the benchmark — the 8 modules this adds to "both" are kept rather than deleted, and Feature 09 settles them.
- Of the 15 benchmark-only test modules, 3 are still used, `09`, because still-used modules import them: `test_benchmark_native`, `test_emulator_recovery` and `test_corpus_preflight`. The other 12 are ready — this is 01-B9's own rule for an item that only a still-used item needs.
- `tests/test_benchmark_family_runtime.py` has a benchmark name but imports only `interface_runtime`. It is still used, `09`, and runs in the copy — 01-B2 requires an entry for its name and 01-B7 runs it.
- A link from a covered document does not make its target still used. So ADR 0001 is ready though `docs/verification.md` links to it — 01-B6 checks only uncovered Markdown, and 01-B10 has still-used `bench/README.md` link to the ready list. No tool document, test, workflow or build file names a ready item.
- Every still-used item is `09`. The tool modules are `02` (`replay`, `scripted_worker`), `07` (`configurator`, `reporting`) and `08` (`cli`, `mcp`). Nothing is `10` — the import lines match the Breakdown, and its Feature 09 has the tool's tests pass without the benchmark.
- Under `Passages`, `08` is only for lines that are a command invocation. Prose that names a benchmark command is `09` — 01-B9 says 08 frees command lines and hosted-test lines.
- `stays` is used for three lines whose only `bench` is the path of the kept `bench/evidence/` — the Breakdown keeps that path unchanged, so no Feature can free them.
- Only two passages are marked, in `docs/support.md` and `docs/setup.md`. Other whole blocks are counted instead: one a still-used test requires, the benchmark items of `docs/design.md`, the ignore rules for plaintext answers in `.gitignore` and `MANIFEST.in`, and both copies of the client skill — 01-B9 says "marked only when", and both marked passages are whole paragraphs that no test requires.
- The Spec's starting classification was checked by reading each file it names, and no difference was found. The base commit tracks 439 files — every row of the Spec's table matches the list's entries.
- One sentence of the list's prose was reworded after the whole Test command ran — the Done check passed afterwards.
