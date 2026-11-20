# Generative Driver

Before implementation, read [CONTEXT.md](CONTEXT.md) and the [accepted design](docs/design.md). Keep the source research checkout unchanged.

- Develop through three public seams: CLI/MCP, configurator/workers, runtime/package. For each code change, observe one behavior test fail, implement it, then observe it pass.
- Support native macOS and Windows 11+. Load optional hardware/analysis dependencies at use time. Use portable imports and argument-array process launches; keep outputs outside installed resources and avoid hardcoded user paths.
- The configurator owns workers, durable runs and exclusive device access. Clients reconnect to the same run. Run six stages in order: acquire, interpret, probe, ground, emit, reuse. One stage is active at a time.
- Fresh reuse receives only the package and objective. Under `assistive/1`, stages hand off with notes; device differences drive bounded repairs, then continue with the best revision. Record one final equivalence verdict: the configurator's own, labelled self-evidence, or the one posted through the client interface for a run on a supplied device. Never silently retry an uncertain write.
- Keep worker claims, accepted handoffs and posted verdicts separate. Hashes establish provenance and integrity.
- The configurator and workers receive neither passwords nor their paths or plaintext answers. Describe isolation accurately: workers are not read-isolated, and hashes are not a sandbox. Distinguish model runs, scripted tests, replay, emulation and physical evidence.
- Keep docs concise, concrete and executable. Define terms on first use; remove repeated explanations. Preserve notices and private research artifacts. Launch model runs or physical operations only when explicitly assigned by the primary agent.

## Agent skills

### Issue tracker

Track specs and tickets as local Markdown under `.scratch/<feature>/`. See [issue tracker conventions](docs/agents/issue-tracker.md).

### Triage labels

Use `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human` and `wontfix`. See [triage labels](docs/agents/triage-labels.md).

### Domain docs

Use one repository-wide domain context, retaining the vocabulary in `CONTEXT.md`. See [domain documentation conventions](docs/agents/domain.md).
