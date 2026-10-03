# Verification

## Scope

All current workflows end at fresh reuse: a new agent receives the emitted package and uses the requested device functions. Independent evaluator checks qualify its outputs, effects and final state. Source tests, installed-wheel checks, reference calibration, actual model execution and physical observations are separate evidence categories.

## Refactor qualification — 2026-10-03

The starting checkout passed 412 source tests in 78.247 seconds on native macOS, with one installed-wheel check skipped because `GD_TEST_WHEEL` was unset. These starting measurements precede the current refactor. Matching current-code reference calibration and installed-package results will be recorded in the [verification index](implementation/benchmark-final-verification.json).

The changed workflow is being verified through actual controller progression, package-only fresh reuse, bounded diagnostic repair, failed-final rejection, six-gate offline scoring, nine-trial suite expansion and installed CLI/MCP boundaries. Native reference checks require two independent implementations and meaningful incorrect models. Current cases reject stale evidence before admission.

## Reproduce software checks

```sh
python -m unittest discover -s tests -v
python -m build
```

To exercise the archive gate, select the built wheel with `GD_TEST_WHEEL` when running the test suite. Install that same wheel in a clean environment, run tests from outside the checkout and run `python -m generative_driver benchmark run --case setup-smoke --output "smoke output"` in a new output directory. These checks perform no paid inference or physical operations.

Native evaluator setup and retained encrypted evidence are documented in [groundtruth](../bench/groundtruth/README.md). Model trial setup, six-stage acceptance, reporting and comparisons are in [the benchmark guide](../bench/README.md).

## Qualification limits

Current local checks do not establish native Windows Renode/Ghidra operation, Windows 11 workstation UI setup, live Goose execution or physical-device performance. Configured CI jobs are separate from observed results. Password-encrypted truth establishes evaluator separation at rest; worker filesystem isolation is unverified. Historical evidence remains in Git at its original revision and is never relabelled as current qualification.
