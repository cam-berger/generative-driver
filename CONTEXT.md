# Domain

| Term | Meaning |
|---|---|
| Configurator | Background owner of runs, workers and exclusive device access. Codex, Goose and CLI are clients. |
| Runtime / provider / model | Worker application, AI service and selected model; record separately. |
| Stage / assignment | Acquire, interpret, probe, ground, emit or reuse; one active worker with bounded inputs/tools/effects. |
| Handoff | Immutable artifacts and hashed notes: worker checks, truth-free configurator checks and open questions. Empty stages still hand off. |
| Worker report | A claim, never a reason by itself to stop a run. |
| Equivalence | One final verdict: evaluator grade for benchmarks, self-evidence for generic runs. Completion alone does not mean success. |
| Interface model / binding | Byte operations, decoding and effects / operator-selected host connection. |
| Driver contract | Authoritative public operations, meanings, value schemas, units, scoped caveats and preconditions. |
| Declarative protocol | Wire exchanges and decoders implementing the public contract through a versioned shared runtime. |
| Precondition | Driver-declared requirement checked from fresh read observations before the requested command. |
| Application MCP layer | Separately authored tools composing public managed-driver operations through the supported SDK. |
| Semantic uncertainty | Missing or weakly supported operation or field meaning; retained as a scoped warning. |
| Package | Contract, protocol, executable model projection, generated MCP catalog, runtime, reference, evidence and integrity manifest. |
| Ground / fresh reuse | Test meanings against the device / new agent uses only package and objective. Reuse is the final stage. |
| Groundtruth | Password-encrypted, hash-pinned expected behavior held only by the evaluator. |
| Bench harness | Benchmark operator: holds password, owns emulator/provider, supplies device descriptor and grades after the run. A suite's harness reads each password once, starts trials only after the answer vault and generator volume are unmounted, and seals each graded scoring trial in the answer vault. |
| Instrument observation | Device-state readings visible to workers, never grades. Generated devices offer none for now: workers see only their UART. |
| Lineage | Upstream firmware ancestry selected in a corpus. Repetitions of one lineage do not demonstrate another device family. |
| Case | Registered, versioned image and evaluator contract, with public mission data and a separately encrypted answer key. A generated device is a case registered through its set. |
| Execution setup | The case's method, actual executable artifact, substitutions, validated behavior and remaining hardware gaps. Native and MCU binaries have distinct identities. |
| Public mission | Required task meanings, inputs, output units and the objective for fresh reuse. Hidden vectors remain evaluator-owned. A generated mission states only each task's effect; ranges, refusal conditions, framing and the mapping onto firmware commands stay hidden. |
| Trial | One fresh six-stage run. A repetition starts another trial, not another lineage. |
| Pass / failure | The evaluator grades a trial's frozen driver equivalent / not equivalent, a departure from the original function; a trial that emits no driver fails. Fresh reuse is reported beside a pass, never inside it. |
| Unknown | A completed grade with no failed step and at least one step the evaluator could not decide; neither pass nor failure. |
| Pass rate | Passes / (passes + failures), printed with the unknown, fault and planned counts. A trial stopped by a host or operator fault it did not cause, or missing its evidence, is counted but not graded; running out of budget, or a stop the system caused, is a failure. A trial the transcript audit flags (a worker ran a command naming a forbidden path) is also counted but not graded. |
| Scorecard | Trial metrics computed afterwards from retained records: outcome, step accuracy, stage times, tool and device use, tokens, repairs and claim agreement (the fresh-reuse claim against the grade). The system under test records nothing for it. A trial still running counts in the pass line alone, and a suite not yet settled says it is provisional. |
| Scoring set / development set | Generated devices sealed for comparisons, from which only aggregate scorecards leave the bench harness / devices from the same generator, open for development. |
| Pinned system | The revision, runtime binary and version, model, reasoning effort and limits that every trial of a baseline shares. |
| Baseline | One pinned system's graded trials on the scoring set, stored so later systems are compared with it device by device. |
| Hidden sequence / step | An evaluator-owned task sequence that starts from a verified reset / one task call in it, which passes when both the driver's answer (a value, a refusal, no reply or an error) and the device state match the reference. A refusal counts whether the device or the driver made it. |
| Framing | How a generated device's commands and replies travel over UART: text lines, binary frames with length and checksum, or register access. A case's family is its registered evaluator, not its framing. |
| Answer vault / generator volume | The encrypted volume holding passwords, answer keys, each set's secret seed and sealed scoring trials / the one holding the generator's repository. Neither is mounted while workers run. |
| Device generator | The private tool, kept in its own encrypted volume outside this repository, that writes each generated device's firmware, mission, answer key, reference drivers and mutants from a sampled design. |
| Difficulty level | One of five generator settings, easy to hard, that raise state, refusal rules, composite tasks, hidden triggers, value encoding and binary obfuscation together. |
| Comparison | Two systems' per-device results on one admitted scoring set under the same evaluator revision and limits, naming every changed dimension. Reports outside a scoring set stay separate series. |
| Anchor | Today's admitted hand-made and upstream cases, run with each pinned system and reported apart from the scoring set; never pooled into its pass rate or comparisons. |
| Leak check | A small fresh batch run at each comparison; a clearly better score on the scoring set than on fresh devices means the set leaked. |
| Selection | Frozen include/exclude decisions and reasons for every lineage in the full catalog. Excluded and blocked slots remain visible. |
| Availability / admission | Public inventory checks / authenticated reference qualification against the current grader identity: the hashes of the code the grader imports, never of the system under test. Availability does not establish admission. A generated set is admitted when both references pass on every device and every mutant fails on a sample of two devices per level and framing. |
| Reference driver / mutant | A correct driver the grader must pass, two distinct ones per case / a deliberately wrong driver the grader must fail. |

Functional compatibility compares required outputs/effects, not source code. Hashes prove identity/integrity, not physical truth or access control. Keep passwords/answers out of candidate inputs; the configurator holds no truth.

Only safety, integrity, operator and host stops end runs. Model/device differences drive bounded interpretation repair. Operator stops need configuration; host stops need environment repair. After repair budget is spent, continue with the best revision. The pass rate scores safety and integrity stops, like a spent budget, as failures, and other operator stops and host stops as faults.
