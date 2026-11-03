# Standards

How code in this project is built. Build follows these rules, the Reviewer enforces them, and only the human changes them. The project rules in [AGENTS.md](AGENTS.md) hold as well.

## Architecture

- Deep modules: a lot of behaviour behind a small interface, so callers learn little to use one.
- Small interfaces: expose only what callers need. Every public name is something to keep working.
- One responsibility per file, so a reader finds each thing where its name says.
- Reshape only code the Feature touches. Problems elsewhere are reported, not fixed, so each diff stays reviewable.
- Code runs natively on macOS and Windows 11+: portable imports, process launches as argument lists, no hardcoded user paths, and output only to directories the caller owns, never to installed resources. Optional hardware and analysis dependencies load when used. This machine runs only macOS, so Windows behaviour is claimed only once CI passes on it.
- Grading code imports nothing from the system under test, the bench harness or the reporting code, so a grader identity covers grading code only and changing anything else keeps a qualification. `tests/test_grader_identity.py` checks the boundary.
- Evaluator passwords stay in the bench harness. The configurator and workers never receive a password, its path or a plaintext answer.
- Never silently retry an uncertain write: a lost reply is reported, not repeated.

## Documentation

- Each module starts with a short doc that says why it exists, citing the ADR or spec decision behind it.
- Function docs are prose, result first, with names in backticks and no Args or Returns sections.
- No comments that restate the code. Comment only a why the code cannot show.
- Code matches its neighbours: single quotes, lines up to 120 columns, and `ValueError` messages in sentence case with no final period and the offending value after a colon.
- READMEs stay short: what it is, how to run it, where to read more.
- Use the terms in [CONTEXT.md](CONTEXT.md), defining any new term on first use, and say "interface", not "contract": the driver contract is already a term.

## Testing

Each test module runs in its own process, eight at a time, because the serial suite takes about 13 minutes and the Done check stops at 9. `PYTHONPATH` is absolute because some tests start a configurator from another directory. A worktree links `.venv` to the main checkout's environment.

Test command: `ls tests | grep -x 'test_.*\.py' | PYTHONPATH="$PWD/src" xargs -P 8 -n 1 sh -c 'out=$(.venv/bin/python -m unittest discover -v -s tests -p "$0" 2>&1); s=$?; printf "%s\n" "$out"; exit $s' && node tests/dashboard-client.test.js`
Test files: `tests/test_*.py`

- Test through public interfaces at the seams the Spec agrees, so tests survive refactoring.
- Each test name carries its Behaviour ID, such as `test_01_B2_rejects_empty_name`.
- See each Behaviour's test fail before writing its code, then pass.
- No tautological tests: never assert on a mock, or compute the expected value the way the code does.
- Mock only at system boundaries, such as the network, the clock or a third-party service.
- A test that starts a Python child gives it `tests/package_env.py`'s environment. MCP stdio children drop `PYTHONPATH`, so without it the child silently runs another checkout's code.
- Tests use temporary homes. They never touch a real password, vault, device or model run.

## Baseline

Tests that already failed when this file was written. The test command excludes them.

- none
