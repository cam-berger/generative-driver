# Configurator implementation record

The approved test seam is the public configurator/worker execution interface. These tests invoke `Controller.call`, the detached service through `client.call`, and real external subprocesses through `agents.execute`. Scripted subprocesses are contract fixtures, never model benchmark results. No provider inference or physical device was used for these tests.

## Observed test cycles

| Behavior | First observed failure | Implemented behavior |
|---|---|---|
| Codex external execution | Adapter module absent | Fresh external session, structured report, reported usage and retained output |
| Durable idempotent start | Controller module absent | SQLite run identity and event history survive reconnection |
| UI disconnect | Client module absent | Detached owner over authenticated Unix socket or Windows named pipe |
| Worker tool scope | Unknown tool method | Assignment-specific tool inventory; inactive and unassigned requests rejected |
| Explicit resume | Unknown resume method | Cancelled workers stop before a new assignment can start |
| Shared connection | Second owner accepted | Endpoint leases ignore tuning settings; overlapping selectors blocked |
| Malformed worker report | Incomplete JSON accepted | Required fields and nested report structure validated independently |
| Goose launch contract | External fixture rejected inherited-profile flags | Explicit recipe extensions, fresh session and no default extension profile |
| Evidence acceptance | Hashed arbitrary text advanced through all stages | Replaced this incorrect gate with observed acquisition/probe results and executable artifact checks; unsupported ground/maintain adapters block |
| Blind generic interpretation | Operator goal and acquisition provenance appeared in the prompt | Neutral prepared binary workspace, exact prompt and pinned collection seal |
| Public metadata | Agent metadata absent | Sanitized runtime/model/provider/version/settings and budget without evaluator options |

Further public regressions verify cancellation remains responsive during a deliberately delayed HTTP tool, an actual fixture acquisition is accepted, changed accepted bytes prevent resume, and recovery preserves effect uncertainty until an explicit operator reconciliation. These were added to the working implementation as integration checks; they are not claimed as separate failing-first cycles.

## Durable state

Assignments, raw worker reports, accepted handoffs, evaluator verdicts and events are stored separately. Artifact hashes are checked before accepting or reusing evidence. Accepting a handoff and advancing its next-stage pointer share one database transaction. A revision retains earlier reports and evidence. Model-fault interpretation repairs have a default limit of two; the deliberately seeded maintenance cycle has its own limit of one.

Start requests may contain a `request_id`; reuse with changed parameters is rejected. `status`, `events` and `result` reconnect to the existing owner. `cancel` prevents later managed dispatch; an operation already dispatched may finish. In-flight device ownership remains held. An interrupted effectful operation retains uncertainty. Recovery requires explicit `resume`, and uncertain effects require `respond` with an operator observation containing `effect_resolution: "confirmed_safe"` after reconciliation.

Worker-side MCP inventories are restricted to the active assignment. Connection bindings, output directories and effect grants are injected by the configurator, never accepted as worker overrides. Calls for one run are serialized. TCP and serial leases identify the endpoint rather than baud/timeouts. USB leases conservatively cover a vendor/product family and FTDI leases cover all FTDI selectors because selector aliases can overlap.

## Runtime contracts and limits

Codex uses its installed CLI `exec` interface, JSON events and a response schema, with user configuration/rules and automatic plugins disabled. Saved authentication remains owned by Codex; the configurator does not read authentication files. Only the built-in OpenAI provider is supported by this adapter. Model and reasoning settings are supplied explicitly. See the official [noninteractive interface](https://learn.chatgpt.com/docs/non-interactive-mode) and [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference).

Goose uses a generated recipe, `--no-profile`, `--no-session` and structured event output. Recipe extensions contain the developer capability and the assigned tool gateway. Credentials remain owned by the installed runtime. The adapter retains unknown cumulative usage as null; a last-context token count is not converted into total session consumption. See the official [CLI implementation](https://github.com/aaif-goose/goose/blob/main/crates/goose-cli/src/cli.rs), [recipe reference](https://goose-docs.ai/docs/guides/recipes/recipe-reference/) and [usage reporting issue](https://github.com/aaif-goose/goose/issues/8871).

These are fresh sessions with scoped supplied context and managed tools. Filesystem read isolation, runtime global hooks and prevention of direct device access through a runtime's shell are not verified. Password encryption and hashes do not establish an OS sandbox. Workers receive no evaluator password/options in prompts or environments. Run records report the actual boundary rather than claiming stronger isolation.

The generic workflow currently accepts a supplied binary, prepares `interpret-interface`, validates the collected model and observes owned probe calls. Independent grounding and maintenance require a device/case adapter; absent adapters are explicit blockers. The TQ9 case supplies its own independent checks and maintenance routing. A worker's assertion of success never supplies a missing ground observation.

## Repeatable verification

From an installed development environment:

```text
python -m unittest discover -s tests -p test_agents.py -v
python -m unittest discover -s tests -p test_configurator.py -v
python -m unittest discover -s tests -p test_service.py -v
```

The local results exercise macOS. Windows uses a named pipe and Windows process-group termination branches; successful Windows CI is required for a native Windows verification claim. Goose's external process contract is tested with a scripted process; a real Goose model run is a separate benchmark measurement.
