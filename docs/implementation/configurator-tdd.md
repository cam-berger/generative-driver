# Configurator verification

Current regression tests exercise the durable controller, worker execution, cancellation, reconnection, bounded repair and package-only fresh reuse through the approved public interfaces. Scripted workers and replay remain contract evidence; they do not measure model or physical performance.

The six-stage endpoint and final acceptance requirements are defined in [the design](../design.md). Current source, installed-package and native evaluator results are recorded in [verification](../verification.md) and its [index](benchmark-final-verification.json). Original development observations remain recoverable from Git at their original revisions.
