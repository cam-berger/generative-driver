# Compare systems through a shared scoring set

Status: proposed in the [benchmark baseline spec](../../.scratch/benchmark-baseline/spec.md).

`benchmark compare` refuses reports from different protocols, and the client skill says `assistive/1` results are never compared with gated ones, because each protocol was graded its own way. A sealed scoring set graded step by step against each device's reference gives any two systems one grader. So two systems may be compared when both ran the same admitted scoring set under the same evaluator revision and limits, whatever their protocols. The comparison lists every changed dimension (protocol, revision, runtime, model, reasoning effort) and labels more than one a combined change. It reports each device as passed by both, neither, only the new system or only the baseline, tests the differing devices with McNemar's test and carries its leak check. Reports not run on a scoring set remain separate series.

## Consequences

The protocol refusal changes in `suite compare` for scoring-set suite reports, and so does the client skill's wording; single-run `benchmark compare` keeps refusing different protocols. A comparison never mixes scoring sets; a replaced scoring set starts a new series with a new baseline.
