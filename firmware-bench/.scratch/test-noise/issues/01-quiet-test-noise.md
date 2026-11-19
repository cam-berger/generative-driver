# Quiet the test suite's recurring noise

Status: needs-triage

One pre-existing output of a passing full test run is re-reported in reviews of unrelated work. It is no test failure. The tool's [note of the same name](https://github.com/cam-berger/generative-driver/blob/main/.scratch/test-noise/issues/01-quiet-test-noise.md) holds the two outputs that come from the tool's files.

- **`qualify`'s progress line on stderr.** `upstream_qualification.qualify` writes `{"reference_calibration": <run>, "phase": <phase>}` to stderr before each reference or mutant run. It printed in `test_upstream_authoring` until 3c4d37d wrapped both of that module's `qualify` calls in `redirect_stderr`, and the full run at 06da61e printed none; a new test that runs `qualify`'s references prints it again unless it redirects stderr too.

Decide whether the code or the tests change: either keep the progress line, which the operator sees during a long calibration, and capture it in tests, or route it somewhere tests do not print. Keep the suite's output free of it afterwards.
