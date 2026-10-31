# Quiet the test suite's recurring noise

Status: needs-triage

Two pre-existing outputs of a passing full test run are re-reported in every review of unrelated work. Neither is a test failure.

- **`ResourceWarning: unclosed file` from [probe.py:88](../../../src/generative_driver/_toolchain/probe.py).** `probe_diff` reads a register model's probe record with `json.load(open(pp, encoding="utf-8"))` and leaves the file to the garbage collector. The full run at be2df85 printed it three times, all during `test_configurator.DeviceRepairTests.test_ground_compares_a_handed_on_record_only_when_the_assigned_model_wrote_it`, whose ground stages compare a handed-on probe record.
- **`qualify`'s progress line on stderr.** `upstream_qualification.qualify` writes `{"reference_calibration": <run>, "phase": <phase>}` to stderr before each reference or mutant run. It printed in `test_upstream_authoring` until 3c4d37d wrapped both of that module's `qualify` calls in `redirect_stderr`, and the full run at be2df85 printed none; a new test that runs `qualify`'s references prints it again unless it redirects stderr too.

Decide for each whether the code or the tests change: close the probe file where it is read; and either keep the progress line, which the operator sees during a long calibration, and capture it in tests, or route it somewhere tests do not print. Keep the suite's output free of both afterwards.
