# 01. doctor-identity

## Intent

`doctor` is the first call the client skill asks for, and what a person runs when a start fails. Today it answers a different question from the one a start asks. A start uses only the Codex command the operator configured and never looks on `PATH`. `doctor` reports any file named `codex` on `PATH` as available without running it, and on the development machine that file was another program. It also says nothing about the optional Python packages, whose absence shows only as a failed tool call during a run.

After this Feature, what `doctor` says about the worker runtime is what a start would find, backed by the runtime's own answer, and the report lists which optional packages are installed. `doctor` stays a diagnosis: it changes no configuration, needs no sign-in, runs nothing the operator did not configure, and never fails because something optional is missing.

## Behaviours

- **01-B1** — With no Codex command configured, `dependencies.codex` has `configured: false`, `available: false`, `version: null`, `path: null`, `source: null` and a `reason` that gives the `generative-driver configure --executor codex --command <path>` command, whatever is on `PATH`.
- **01-B2** — With no Codex command configured, `dependencies.codex.on_path` is the path of an executable named `codex` on `PATH`, or `null` when there is none, and that executable is not run.
- **01-B3** — With a configured command that exits 0 for `--version` and whose first line of standard output is `codex-cli <version>`, `dependencies.codex` has `configured: true`, `available: true`, `version: "<version>"`, `source: "configuration"`, `path` the executable `shutil.which` resolves the command's first item to, `reason: null`, and the configured `model` and `provider`.
- **01-B4** — With a configured command whose first item resolves to no executable, `available` is `false`, `path` and `version` are `null`, and `reason` says the executable was not found and names it.
- **01-B5** — With a configured command that runs but does not answer as in 01-B3 (another first line, a nonzero exit, or no output), `available` is `false`, `version` is `null`, and `reason` quotes the first line it printed, from standard output or else standard error, cut to 200 characters.
- **01-B6** — A configured command that has not exited after 10 seconds is stopped together with its child processes, `available` is `false`, and `reason` says it gave no answer in time.
- **01-B7** — The check runs the configured command with `--version` appended, in a new empty directory that is removed afterwards, with no input, with its output captured, and with only the environment variables a worker launch passes on. It writes nothing to the caller's working directory or to the home.
- **01-B8** — `extras` has an entry for each of `pyserial`, `pyusb`, `pyftdi`, `esptool` and `pypdf` with `installed`, `version` (`null` when not installed or not recorded), `required_for`, and `extra`, the install extra that provides it (`hardware` or `docs`). None of them is imported to find out.
- **01-B9** — A `config.json` that is not valid JSON, or whose Codex entry has no `command` a launch would accept (a non-empty string, or a non-empty list of non-empty strings), gives `configured: false` and `available: false` with a `reason` that names the file, and the rest of the report is still returned.
- **01-B10** — `generative-driver doctor` exits 0 whenever `core.ok` is true, whatever the Codex and extras entries say.
- **01-B11** — `driver_doctor`, called over MCP stdio with a configured command that prints to both output streams, returns the same report as valid JSON, and the session answers a second call.
- **01-B12** — The check launches the stored command itself, never the path it resolved. When that launch fails with an operating-system error, `available` is `false`, `version` is `null`, `path` stays what `shutil.which` returned, and `reason` quotes the error. When the command's first item is a relative path with a directory part and the check obtained no version, `reason` also says that a start runs from another directory, so the path must be absolute. A command that answers as in 01-B3 is reported as 01-B3 says, whatever its path. On Windows that leaves a gap the human accepted on 2026-11-11: a relative command can resolve from the directory `doctor` was run in and be reported available, although a start, which launches through its wrapper from the stage workspace, would not find it. The check does not use that wrapper, and the test of the relative case is skipped on Windows.

## Interface and seams

`generative_driver.setup.doctor(home=None)` returns the report. `dependencies.codex` keeps `available`, `path`, `required_for` and `source`, and gains `configured`, `version`, `reason` and `on_path`. `available` now means a command is configured and answered as the Codex CLI. `version` is what the command printed, no longer a value read from `config.json`. `on_path` is always present and is `null` once a command is configured. `extras` is a new top-level key. The entries for Renode, Java, the Arm toolchain and Podman, and every other top-level key, are unchanged.

`generative-driver [--home H] doctor [--json]` prints that report, and the MCP tool `driver_doctor` returns it.

Tests attach at those three places: the function with a temporary home, the command as a child process (exit status, working directory left empty) and MCP stdio for 01-B11. A stand-in for the runtime is a script the test writes, configured as `[sys.executable, <script>]`, so no test needs a real Codex. The 10-second limit is one module constant, which the 01-B6 test shortens. `tests/test_cli.py` has one test that configures the Python interpreter as Codex and expects `available: true`; it asserts the old meaning and is rewritten against a stand-in that answers as the Codex CLI.

## Out of scope

- Whether Codex is signed in, and any model call.
- Recording the version a run's worker reported: a run's `runtime_version` is read from a `version` key that `configure` never writes, so it is always null. Report it under Noticed.
- Checking the command when `configure` stores it, or changing `config.json` in any way.
- Finding Renode, Java, the Arm toolchain, Ghidra or Podman anywhere but `PATH`.
- The client skill's text and the generated plugin, which are Feature 02.
- How a start refuses a runtime that is not configured.

## Deviations

- The check is `generative_driver.agents.codex_identity(config)`, with the 10-second limit in `agents.VERSION_ANSWER_SECONDS`, imported by `setup.doctor` at call time — the Spec names no module, and sharing the launch's environment filter and process-tree stop is how the check finds what a start would find.
- `_command(config)` and `_OWN_GROUP` were pulled out of the launch code, and `_command` raises `ValueError` where a missing or non-object config raised `KeyError` or `TypeError` — doctor accepts exactly the commands a launch accepts, the message text is unchanged, and `execute` refuses a missing command before reaching it.
- 01-B4: a command `shutil.which` cannot find is not launched — the Spec asks for no launch there, and nothing would start.
- 01-B12: the "must be absolute" sentence is added whenever no version was obtained, including the not-found case — this is the amended Spec's own wording.
- 01-B12 on Windows: the stored command is launched directly, not through the start's wrapper, and the relative-path test is skipped there — the human accepted this gap on 2026-11-11 and the amended 01-B12 states it.
- 01-B12's "a command that answers is reported as 01-B3 says, whatever its path" is tested only through 01-B3's absolute-path stand-ins — on macOS a relative command cannot answer from the check's empty directory, and the code adds nothing once a version is obtained.
- `model` and `provider` appear only when `configured` is true — the Spec lists them for 01-B3 only.
- 01-B9 also covers an unreadable file, a file that is not a JSON object, `executors` that is not an object and a Codex entry that is not an object; a `null` entry counts as none — each crashed doctor before, which the Intent and 01-B10 rule out.
- 01-B9 reasons also give the `configure` command, with "Fix or remove it" first for a file `configure` cannot rewrite — the next step for an operator whose start failed.
- 01-B9 reasons for a non-object file or `executors` name the JSON type found, never text from the file — the Standards rule on `ValueError` messages, without exposing the operator's file in `reason`.
- `version` is the rest of the first standard-output line after `codex-cli `, stripped — the Spec gives no version grammar.
- 01-B5: the quoted line is standard output's first line, else standard error's, "It printed nothing." when neither, and the exit status is added — 01-B5's rule with more detail.
- `extras[*].required_for` wording is the implementer's, and the package-to-extra table is a literal in `setup.py` held to `pyproject.toml` by its test — an installed package has no `pyproject.toml` to read.
- New tests are in `tests/test_doctor.py`; the `tests/test_cli.py` test is rewritten in place with `package_env()` — the Spec asks for the rewrite and STANDARDS for that environment.
- 01-B10's test runs its child with `python -S` and checks the extras keys, not their values — values depend on how the package is installed.
- 01-B11's test leaves `core.platform` out of the comparison — the value is cached per process from the `PATH` it first saw.
- 01-B12's first test patches `shutil.which` — the host's executable lookup is a system boundary, and the test still fails if the resolved path is launched.
- RED order: 01-B11 failed before the 01-B3 code, 01-B10 before 01-B9, and the kept 01-B6 failure is on behaviour — disclosed, and each kept failure is for its Behaviour's reason.
