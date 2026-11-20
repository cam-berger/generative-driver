# Quiet the test suite's recurring noise

Status: needs-triage

Two pre-existing outputs of a passing full test run are re-reported in every review of unrelated work. Neither is a test failure.

- **`ResourceWarning: unclosed file` from [probe.py:88](../../../src/generative_driver/_toolchain/probe.py).** `probe_diff` reads a register model's probe record with `json.load(open(pp, encoding="utf-8"))` and leaves the file to the garbage collector. The full run at 06da61e printed it three times, all during `test_configurator.DeviceRepairTests.test_ground_compares_a_handed_on_record_only_when_the_assigned_model_wrote_it`, whose ground stages compare a handed-on probe record.
- **`ConnectionResetError` tracebacks from [test_device_sessions.py:24](../../../tests/test_device_sessions.py).** The demonstration device's handler reads with `iter(self.rfile.readline, b'')`, and `socketserver` prints the traceback when a client resets its connection. Pre-existing noise that needs no code change.

Decide for each whether the code or the tests change: close the probe file where it is read. Keep the suite's output free of both afterwards.
