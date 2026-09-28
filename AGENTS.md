# Generative Driver

Build the public distribution from the accepted design in `docs/design.md`. Keep the source research checkout unchanged. Work through public interfaces with one failing test and its implementation per slice; the user approved four seams: installed CLI/MCP, configurator/worker execution, runtime/emitted package, and benchmark run/score/compare.

Read `CONTEXT.md` before implementation. Preserve independent groundtruth and distinguish worker reports, accepted handoffs, and evaluator verdicts. Functional compatibility is the benchmark criterion: compare outputs and effects for specified inputs, never candidate source-code equality. Artifact hashes establish provenance and integrity.

Core Python must run on native macOS and Windows 11+. Hardware libraries and analysis tools are optional. No hardcoded user paths, Unix-only unconditional imports, shell-dependent process launch, or writing into installed resources.

Goose/Codex are clients of the background configurator. The configurator owns configured agent runtimes, stages, durable runs and device access. One stage runs at a time. A disconnected UI may reconnect to the same run. Never silently retry an uncertain device write.

Only the evaluator receives groundtruth passwords. Candidate workers receive neither password nor plaintext answer keys. Report this password-based separation accurately; do not call hashes a sandbox. Actual model runs, scripted contract tests, replay, emulation and physical observations must remain distinguishable.

Keep public documentation concise and executable. Preserve third-party notices. Do not publish or push private research artifacts. Do not launch actual model baselines or touch physical devices unless assigned explicitly by the primary agent.
