# Seal graded scoring trials in the answer vault

Status: proposed in the [benchmark baseline spec](../../.scratch/benchmark-baseline/spec.md).

[AGENTS.md](../../AGENTS.md) has the configurator own durable runs, and each finished trial's folder stays in its home with the working driver, Codex transcripts and device exchanges, while suite views show per-trial verdicts. Workers run as the operator's user and can read other folders, so a later system's workers could find the baseline's driver for the identical firmware, and developers would see per-trial details of a sealed scoring set. So once a scoring trial has concluded, the bench harness encrypts its folders in place under the set's password, which it already holds, and leaves a stub; the vault stays closed while workers run, so the operator moves the seals into it after the suite (`benchmark suite store`). A scoring suite's scorecard is aggregate-only and runs only with the set's password file and no workers running.

## Consequences

The configurator still owns active runs and every run outside a scoring set. A sealed run keeps its rows in the runs database, as a stated limit, and refuses `resume` and `attach_device`. A sealed trial's kept paths are its run's `benchmark/execution.json` and `benchmark/inputs/`, which are public to that device's worker. Rescoring a baseline needs the set's password file, and the answer vault mounted once its seals have moved there.
