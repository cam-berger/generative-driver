# Contributing

Read [CONTEXT.md](CONTEXT.md) and the [accepted design](docs/design.md). Keep interfaces small: the CLI/MCP delegates to the configurator, the configurator owns external workers, and the benchmark owns independent scoring. A worker report never becomes groundtruth by being well formatted.

## Make a change

1. Pick an approved public seam: installed CLI/MCP, configurator/worker execution, runtime/package, or benchmark run/score/compare.
2. Add one behavior test with an independent expected result and observe it fail.
3. Implement the smallest working change and observe that test pass. Repeat for the next behavior.
4. Build the wheel and run relevant tests against that installed wheel from outside the checkout. Run the native CI matrix before claiming a Windows pass.

The runtime tests use synthetic device replies and scripted workers. Their results establish contract behavior, not model capability or hardware qualification. Actual inference runs are selected explicitly and retain failures, usage, runtime/model configuration, limits and evidence hashes.

## Extend device support

Add a generic operation to the toolkit facade and its packaged manifest, or extend an existing runtime transport. Keep target-specific facts in input evidence and benchmark truth. Optional native dependencies load at use time. Use argument arrays for processes, explicit operator bindings for hardware, and absolute caller-owned output directories. Installed resources are read-only inputs.

An acquisition adapter must state what was actually acquired. An interpreter emits the applicable model schema and evidence. Probe and ground have distinct obligations: receiving a well-framed reply does not establish the decoder's meaning. A new benchmark case must define independent accepted and rejected observations, versioned inputs, and every applicable stage gate. Keep its password outside candidate inputs and commit its evidence hashes.

## Compare performance

Change one recorded dimension when practical. Keep case, evaluator, seed, runtime, model, settings, toolchain, skills and budget in reports. Use three or more fresh runs for a performance claim; one run is a development baseline. Report success counts and time/usage distributions alongside stage failures. Unknown usage is unknown, not zero.

## Publication

Include original or redistributable assets and preserve third-party notices. Keep device recordings, credentials, local paths and personal run history outside published source archives. Inspect the actual wheel and source distribution, not only the Git diff.
