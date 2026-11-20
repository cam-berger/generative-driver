# Driver contract and generated MCP

Status: Accepted, 2026-10-29.

Replaced in part, 2026-11-19: [ADR 0010](0010-a-package-is-data-in-one-format.md) replaced the packaging of this decision. A package no longer freezes a contract and a protocol as files of their own. It stores each driver once, as an interface model, and the driver contract and the declarative protocol are views of that model. The rule that generated tools run through sessions and assignments the configurator owns stays.

## Context

Agents, applications and scripts need the same device meanings and execution rules. Handwritten MCP wrappers would create another source for parameter semantics, wire behavior and preconditions.

## Decision

- Freeze a public `driver-contract/1`, declarative `driver-protocol/1`, checked executable model and mechanically rendered MCP catalog in `interface-package/2`.
- Keep operation/field meanings, units, caveats and preconditions in the contract. The shared runtime executes protocol data and checks fresh guard observations under one lease; there is no caller override.
- Route generated tools and supported `ManagedDriver` calls through configurator-owned sessions or assignments. Preserve binding/grant checks, immutable package pins, per-invocation status and uncertainty. Cancelling a queued request cannot later dispatch it; cancellation never repeats a command.
- Author higher-level tools through `generative_driver.sdk`, with declared child operations/effects, validated results, scoped warnings and complete partial evidence. Device-specific executable plugins remain outside the driver format.
- Under current `assistive/1`, retain ordered stage handoffs and notes. New format-2 fresh reuse requires generated-MCP dispatch evidence. Never put passwords or their paths into configurator/worker assignments.

## Compatibility and evidence

Historical format-1 packages retain their original rules. Conservative import into format 2 records missing meanings as warnings. Authored contract/protocol pairs travel with the model through probe, ground and emit; incomplete or inconsistent pairs cannot silently become an unguarded generated replacement.

This change makes no MHS compliance, physical qualification or new Windows qualification claim.
