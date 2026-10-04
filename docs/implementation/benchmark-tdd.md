# Fresh reuse refactor verification

The implementation uses the existing four seams: installed CLI/MCP, configurator/worker execution, emitted package/runtime, and benchmark run/score/compare. Each changed behavior is observed failing before implementation and passing afterward. Task reports retain exact commands, failed output, successful output and dependent failures; the final record distinguishes current native reference checks from scripted fixtures.

The required contracts are six-stage completion after fresh package use, bounded pre-final repair, terminal final failure, six-gate offline grades, stable original case inputs, stale-calibration rejection and nine distinct default suite slots. Installed packages must expose the same contracts outside their source checkout.

Measured outcomes are recorded in the [verification index](benchmark-final-verification.json) and [verification guide](../verification.md). Earlier TDD and qualification records remain recoverable in Git at their original revisions.
