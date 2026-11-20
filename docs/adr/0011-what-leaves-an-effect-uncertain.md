# 11. What leaves an effect uncertain

Status: Accepted, 2026-11-19.

The configurator must never repeat a write or an actuation whose outcome it does not know. The rule that decides when an effect is uncertain was written twice: in the worker gateway, and in the device access that serves generated tools and managed sessions. The two copies disagreed. Between them they stopped the operator for effects that could not have happened: a read over a channel that resets on open, a write that failed before its command was sent, a run granted `write` after any restart, and a cleanup that failed. The owner decided:

1. Only a write or an actuation can leave an uncertain effect. A reset needs its effect grant and nothing more.
2. An effect stays uncertain only when the requested command may have been sent and its execution did not succeed.
3. A restart marks a run uncertain only for a recorded pending effect: the stored mark of one write or actuation whose outcome is not known.
4. The operator resolves an uncertain effect of a run with the same statement as for a managed session: `effect_resolution` `confirmed_safe`, and an observation with a `source`, a `message` and `prior_execution_stopped` true.
5. A cancellation keeps no successful write uncertain. A call that ended without a result stays uncertain.

The rule is written once: one function reads a tool's result, and one module records every dispatch and owns the uncertain effects. A failed cleanup is recorded as an event and marks nothing.

Why: the operator is stopped only when a write or an actuation may really have gone out. Nothing is cleared silently: a call that ended without a result, a result that does not say whether the command was sent, and a flag an earlier version set all stay until the operator's statement. Only the verified replacement of a supplied device resolves a pending effect without one.

The cost: a write the configurator could not start, because the checked copy of the package was refused or could not be read back, still stays uncertain, although nothing reached the device.
