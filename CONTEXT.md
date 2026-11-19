# Domain

| Term | Meaning |
|---|---|
| Configurator | Background owner of runs, workers and exclusive device access. Codex and the CLI are clients. |
| Runtime / provider / model | Worker application, AI service and selected model; record separately. |
| Stage / assignment | Acquire, interpret, probe, ground, emit or reuse; one active worker with bounded inputs/tools/effects. |
| Handoff | Immutable artifacts and hashed notes: worker checks, truth-free configurator checks and open questions. Empty stages still hand off. |
| Worker report | A claim, never a reason by itself to stop a run. |
| Scripted worker | A program the kit ships that the configurator launches in place of the Codex CLI, doing a stage's work by a fixed script with no model. Its runs show the kit's machinery works, never a model's ability. |
| Stand-in device | A fictional line-protocol device served on a localhost TCP port for one smoke. Its exchanges are not physical evidence. |
| Supplied device | A device that a client of the [client interface](docs/client-interface.md) owns, starts and stops, and describes to the service. A run on one ends `completed` and waits for the one verdict that client posts. |
| Kit analysis | The firmware analysis the configurator makes once per run, before interpretation, with its index of functions and likely command dispatchers. Workers read it; they do not rerun it unless the evidence contradicts how it loaded the image. |
| Interpret task | One fresh worker inside an interpret stage attempt run in task mode: the inventory, one handler task per command, or the cross-check. Tasks are recorded under the stage's one assignment, never as stage attempts. |
| Supplementary document | A datasheet, protocol specification or operator-supplied file acquired beside the firmware. Interpreters read it as evidence of meanings, units and framing; where it disagrees with the firmware or the device, the firmware or the device wins. |
| Device-first interpretation | Interpretation in a generic run with no single firmware image: the interpret worker writes the model from the device's replies to operations it declares read-only, and hands it on as usual. |
| Equivalence | One final verdict: the configurator's own, with basis `self-evidence`, or the one posted through the client interface for a run on a supplied device, with basis `evaluator`. Completion alone does not mean success. |
| Interface model / binding | Byte operations, decoding and effects / operator-selected host connection. |
| Driver contract | Authoritative public operations, meanings, value schemas, units, scoped caveats and preconditions. |
| Declarative protocol | Wire exchanges and decoders implementing the public contract through a versioned shared runtime. |
| Precondition | Driver-declared requirement checked from fresh read observations before the requested command. |
| Effect grant | Permission for a driver call to write, actuate or reset; reads need none. The operator grants a run's effects; a call needing an ungranted effect is refused before any device I/O. |
| Uncertain effect | A write or actuation that may have been sent to a device and whose outcome the configurator does not know. A call that succeeded, or whose result shows the requested command was never sent, leaves none. It is never repeated silently; the operator, or the verified replacement of a supplied device, resolves it. Until then the device stays with its run or session, and nothing else that would change it is sent. A reset is not one: it needs its effect grant and nothing more. |
| Application MCP layer | Separately authored tools composing public managed-driver operations through the supported SDK. |
| Semantic uncertainty | Missing or weakly supported operation or field meaning; retained as a scoped warning. |
| Package | Contract, protocol, executable model projection, generated MCP catalog, runtime, reference, evidence and integrity manifest. |
| Ground / fresh reuse | Test meanings against the device / new agent uses only package and objective. Reuse is the final stage. |
| Instrument observation | Device-state readings visible to workers, never grades. |
| Public mission | Required task meanings, inputs, output units and the objective for fresh reuse. |

Functional compatibility compares required outputs/effects, not source code. Hashes prove identity/integrity, not physical truth or access control. Keep passwords and answers out of worker inputs; the configurator holds neither.

Only safety, integrity, operator and host stops end runs. Model/device differences drive bounded interpretation repair. Operator stops need configuration; host stops need environment repair. After repair budget is spent, continue with the best revision.
