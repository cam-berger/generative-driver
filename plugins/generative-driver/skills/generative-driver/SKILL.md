---
name: generative-driver
description: Generate a device interface from firmware, or reconnect to an existing driver workflow.
---

# Generate a device interface

1. Run `driver_doctor` to check dependencies. An authenticated Codex runtime is configured with `generative-driver configure`. Credentials stay with Codex.
2. Establish objective, inputs, binding and effects. Reads are default; writes/actuation require task authorization. Grant `reset` only for devices that restart on port open. For an STM32 with an ST-LINK probe attached, add `"debug_probe": {"kind": "stlink"}` to the binding so that acquisition reads its firmware; that read resets the chip only under the `reset` grant. Smoke uses no inference.
3. Start once with `driver_start`, stable request ID; save `run_id`. Configurator owns workers/access/handoffs. When authorized, use `scoped_tool_approval: "bound-device"` for the selected binding/effects. Preserve prior authorization.
4. Follow status/events and stage notes, including failed/not-run checks and open questions. Worker reports never stop runs; only safety, integrity, operator and host faults do. Use `driver_respond` for requested observations, naming the observer. Cancel when asked; resolve causes before resuming.
5. Retrieve `driver_result`: report stages/notes, `equivalence` with basis, evidence and limits separately. Generic verdicts are `self-evidence`, not independent grades. Label replay, emulation and physical results.

Six stages: acquire → interpret → probe → ground → emit → reuse. Pass notes forward except fresh reuse, which gets only package/objective and permitted operating information. Ground tests meanings against the device. Device differences drive bounded repair; after budget exhaustion continue with the best revision. Record one verdict, then `completed` regardless of equivalence. A package's structure does not prove its behavior.

Workers see instrument readings as device evidence and are not read-isolated. Interrupted writes stay uncertain until checked; never silently retry them.
