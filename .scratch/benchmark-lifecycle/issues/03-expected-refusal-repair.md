# Recognize independently witnessed expected refusals before spending repair

Status: needs-triage

The completed architecture trial consumed a repair for the public invalid-address test even though trusted evidence showed refusal, unchanged state and successful same-driver recovery. The trigger's subsequent interpretation/probe attempts used 283.35 worker seconds; this is associated effort, not proven counterfactual savings.

Review the [finding and narrow proposal](../../../docs/research/2026-10-27-expected-refusal-repair.md) before implementation. Bind structured public intent prospectively to actual dispatch/mapping/model identity and a trusted no-effect/recovery receipt. Exempt only that dispatch's direct failure entry; preserve other defects and every stop condition. Begin with the explicit read-only case, with no retries or reset-based recovery.

The 16-scenario synthetic prototype remains outside the checkout. No exemption is implemented in this PR. Required validation includes gateway behavior, ambiguous binding and incorrect-driver controls, native qualification and a fresh end-to-end agent run.
