# Seal graded scoring trials in the answer vault

Status: proposed in the [benchmark baseline spec](../../.scratch/benchmark-baseline/spec.md).

[AGENTS.md](../../AGENTS.md) has the configurator own durable runs, and each finished trial's folder stays in its home with the working driver, Codex transcripts and device exchanges, while suite views show per-trial verdicts. Workers run as the operator's user and can read other folders, so a later system's workers could find the baseline's driver for the identical firmware, and developers would see per-trial details of a sealed scoring set. So once a scoring trial is graded, the bench harness moves its run folder into the encrypted answer vault and leaves a stub. Scoring suites show aggregates only, and the scorecard runs only with the vault mounted and no workers running.

## Consequences

The configurator still owns active runs and every run outside a scoring set. Reconnecting to a sealed scoring trial shows its stub. Rescoring a baseline needs the answer vault mounted.
