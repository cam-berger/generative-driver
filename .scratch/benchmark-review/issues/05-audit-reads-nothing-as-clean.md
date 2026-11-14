# The transcript audit treats a missing transcript as a clean one

Status: needs-triage

Checked: read (`benchmark_support/audit.py:35-71`).

`audit_commands` reads every `_agent_events.jsonl` under the run folder and returns a finding for each completed shell command that names a forbidden path. A run with no transcript, a deleted one, or lines the audit cannot parse returns no findings, which the harness reads as clean. The transcript sits in the worker's own working directory, so the worker can remove it.

Only completed `command_execution` items are read. A file read through another tool, an MCP call, and a workspace outside the run's recorded paths are not seen. The harness passes only the set's root as forbidden, and only on scoring suites (reported).

The audit against a real Codex transcript layout is still unverified ([bench harness notes](../../../docs/features/bench-harness/noticed.md), Feature 06).

## Direction

- Return a third result, unaudited, when a stage that ran a worker has no readable transcript, and count such a trial like a flagged one.
- Keep the transcript outside the worker's reach: have the configurator copy each event as it arrives, as it already reads them to follow the worker.
- Check the audit once against a transcript from a real trial.
