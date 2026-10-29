# Driver contract and generated MCP

Status: Accepted, 2026-10-29. Implements the [agreed contract design](../superpowers/specs/2026-10-28-driver-contract-mcp-design.md).

## Context

Agents, applications and scripts need the same device meanings and execution rules. Handwritten MCP wrappers would create another source for parameter semantics, wire behavior and preconditions. Current main separately owns device communication and post-run benchmark grading.

## Decision

- Freeze a public `driver-contract/1`, declarative `driver-protocol/1`, checked executable model and mechanically rendered MCP catalog in `interface-package/2`.
- Keep operation/field meanings, units, caveats and preconditions in the contract. The shared runtime executes protocol data and checks fresh guard observations under one lease; there is no caller override.
- Route generated tools and supported `ManagedDriver` calls through configurator-owned sessions or assignments. Preserve binding/grant checks, immutable package pins, per-invocation status and uncertainty. Cancelling a queued request cannot later dispatch it; cancellation never repeats a command.
- Author higher-level tools through `generative_driver.sdk`, with declared child operations/effects, validated results, scoped warnings and complete partial evidence. Device-specific executable plugins remain outside the driver format.
- Under current `assistive/1`, retain ordered stage handoffs and notes. New format-2 fresh reuse requires generated-MCP dispatch evidence; the bench harness seals and grades the complete owner sequence after the run. Never put evaluator passwords or their paths into configurator/worker assignments.

## Compatibility and evidence

Historical format-1 packages and unprofiled reports retain their original rules. Conservative import into format 2 records missing meanings as warnings. Authored contract/protocol pairs travel with the model through probe, ground and emit; incomplete or inconsistent pairs cannot silently become an unguarded generated replacement.

The [nine-trial record](../../bench/evidence/2026-10-29-driver-contract-mcp-verification.json) belongs to its preserved r2 runtime. Current integrated-head unit/process/installation checks are distinct evidence; a future model study needs current native admission. This change makes no MHS compliance, physical qualification or new Windows qualification claim.
