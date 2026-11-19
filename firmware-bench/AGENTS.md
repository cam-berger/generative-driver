# Firmware bench

Before implementation, read [CONTEXT.md](CONTEXT.md) and the [front page](README.md).

- Develop through one public seam, the command `firmware-bench`: run, score, compare. For each code change, observe one behavior test fail, implement it, then observe it pass.
- Use the tool only as an installed package, through its client interface. The benchmark's code imports no other name of it.
- The bench harness holds evaluator passwords; the configurator and workers receive neither passwords nor their paths or plaintext answers. Describe password separation accurately; hashes are not a sandbox.
- Keep worker claims, accepted handoffs and evaluator verdicts separate. Score observable outputs/effects, not candidate source-code equality.
- Distinguish model runs, scripted tests, replay, emulation and physical evidence. Launch model baselines or physical operations only when explicitly assigned by the primary agent.
- Keep docs concise, concrete and executable. Preserve notices.
- Track open issues as local Markdown under `.scratch/<feature>/`.
