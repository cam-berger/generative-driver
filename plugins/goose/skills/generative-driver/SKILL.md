---
name: generative-driver
description: Generate a device interface from firmware, reconnect to an existing driver workflow, or run a Generative Driver benchmark through the configurator.
---

# Generate an evidence-backed interface

1. Call `driver_doctor`. Resolve missing dependencies required for the selected profile. Use the installed CLI's `configure` command to select an authenticated Codex or Goose runtime; credentials stay in that runtime.
2. Establish the objective, supplied inputs, explicit device binding and allowed effects. Reading is the default. Request a write or physical action only when the developer's task authorizes it. For benchmarks use `driver_benchmark_run` with the selected profile and its documented options. The `setup-smoke` profile needs no inference; real profiles use the configured executor. Supply an evaluator-side password file path and keep answer keys out of candidate context.
3. Start the selected path once: `driver_start` for a device workflow, or `driver_benchmark_run` for a benchmark. Use a stable request ID and save the returned run ID. The configurator owns stage launches, connection access and accepted handoffs. When the developer explicitly authorizes the assigned runtime tools, use `scoped_tool_approval: "emulator"` for TQ9, or `"bound-device"` for the selected physical binding and effects. Benchmark options carry that setting; generic starts accept it directly. Preserve an authorization already given in this conversation.
4. Use `driver_status` and `driver_events` to inspect progress. A disconnected interface reconnects with the same run ID. Supply a requested observation using `driver_respond`, recording who observed what. Use `driver_cancel` when the developer stops a run; use `driver_resume` only after resolving the reported cause.
5. Retrieve `driver_result`. Report accepted stages, independent verdict, evidence paths and unresolved limits separately. A worker's completion claim is provisional until the configurator accepts its evidence. Replay, emulated observations and physical observations retain their labels.

The seven stages are acquire, interpret, probe, ground, emit, reuse and maintain. The configurator prepares a fresh context for each. Ground requires evidence independent of generated code; reuse receives the emitted package and its new objective. A structurally valid package may still be semantically wrong. Device writes interrupted in progress remain uncertain until checked.
