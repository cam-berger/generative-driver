# A crashed worker leaves the pass rate's denominator and gets a rerun

Status: needs-triage

Checked: read.

The pass rate is passes / (passes + failures); unknown, fault and flagged trials are counted beside it (`pass_rate` in `benchmark_support/outcomes.py`). A worker runtime that exits with an error is stopped as a host fault (`_runtime_stop` in `configurator.py`: `cause == 'exit_error'`), and a stopped run whose category is host is a fault, not a failure (`classify`, rule 4). A scoring suite then reruns that device once (`child_disposition` in `suites.py`), and a device still faulted leaves the paired comparison.

A runtime can crash for reasons the device causes, such as a context that overflows on a large decompile. If crashes are more likely on hard devices, the rate rises because hard devices leave it, and the comparison loses the devices where two systems differ most.

## Direction

- Print a second rate beside the first with every fault counted as a failure, so a reader sees both bounds. This is reporting code, outside the grader identity.
- Decide which runtime exits are the system's own: a crash after the worker started producing output is the system's, a runtime that never started is the host's.
- Report faults per difficulty level, so a correlation with difficulty shows.
