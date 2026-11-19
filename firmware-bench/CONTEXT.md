# Domain of the benchmark

The terms of firmware bench. The terms of the tool it evaluates, such as configurator, stage, handoff, package and fresh reuse, are in the tool's [CONTEXT.md](https://github.com/cam-berger/generative-driver/blob/main/CONTEXT.md).

| Term | Meaning |
|---|---|
| Groundtruth | Password-encrypted, hash-pinned expected behavior held only by the evaluator. |
| Bench harness | Benchmark operator: holds password, owns emulator/provider, supplies device descriptor and grades after the run. A suite's harness reads each password once, holds its set in memory, starts trials only after the set's folder is out of reach, and seals each concluded scoring trial in place under the set's password. The operator moves the seals into the answer vault after the suite. |
| Evaluator password | One case's or set's password once read: held in the memory of the process that read it, used only to seal and unlock, and impossible to print or serialize. This catches mistakes in our own code; it does not stop anything that can read that process's memory. |
| Unlocked truth | A case's answer key decrypted in the bench harness's memory, once per trial and again for a trial resumed later, with the hash of the ciphertext it came from. The harness passes it to the grader, which never decrypts an answer key. |
| Lineage | Upstream firmware ancestry selected in a corpus. Repetitions of one lineage do not demonstrate another device family. |
| Case | Registered, versioned image and evaluator contract, with public mission data and a separately encrypted answer key. A generated device is a case registered through its set. |
| Device pin | The frozen identity of one generated device in a trial: its set, the set's manifest hash, its id and the hashes of its image, answer file and qualification record. The bench harness builds it and freezes it with the trial's snapshot. It never holds the level or framing, because a worker can read it. |
| Boot reading | A generated device's startup observation, read from a fresh emulator before anything else touches it. A device's graded runs must all give the same reading, or its set stays pending, because recovery relies on it to show a replacement emulator started clean. |
| Rerun | A scoring suite's one replacement trial for a device whose first trial ended in a fault, run after every first attempt. It stays one result for that device, and its result stands. |
| Execution setup | The case's method, actual executable artifact, substitutions, validated behavior and remaining hardware gaps. Native and MCU binaries have distinct identities. |
| Trial | One fresh six-stage run. A repetition starts another trial, not another lineage. |
| Pass / failure | The evaluator grades a trial's frozen driver equivalent / not equivalent, a departure from the original function; a trial that emits no driver fails. Fresh reuse is reported beside a pass, never inside it. |
| Unknown | A completed grade with no failed step and at least one step the evaluator could not decide; neither pass nor failure. |
| Pass rate | Passes / (passes + failures), printed with the unknown, fault and planned counts. A trial stopped by a host or operator fault it did not cause, or missing its evidence, is counted but not graded; running out of budget, or a stop the system caused, is a failure. A trial the transcript audit flags (a worker ran a command naming a forbidden path) is also counted but not graded. |
| Scorecard | Trial metrics computed afterwards from retained records: outcome, step accuracy, stage times, tool and device use, tokens, repairs and claim agreement (the fresh-reuse claim against the grade). The system under test records nothing for it. A trial still running counts in the pass line alone, and a suite not yet settled says it is provisional. |
| Scoring set / development set | Generated devices sealed for comparisons, from which only aggregate scorecards leave the bench harness / devices from the same generator, open for development. The code-owned list of sets marks which is which. A scoring suite runs every device of its set once. |
| Pinned system | The revision, runtime binary and version, model, reasoning effort and limits that every trial of a baseline shares. |
| Baseline | One pinned system's graded trials on the scoring set, stored so later systems are compared with it device by device. |
| Hidden sequence / step | An evaluator-owned task sequence that starts from a verified reset / one task call in it, which passes when both the driver's answer (a value, a refusal, no reply or an error) and the device state match the reference. A refusal counts whether the device or the driver made it. A generated device's hidden sequences together take every transition of the state machine its answer key declares. |
| Framing | How a generated device's commands and replies travel over UART: text lines, binary frames with length and checksum, or register access. A case's family is its registered evaluator, not its framing. |
| Answer vault / generator volume | The encrypted volume holding passwords, answer keys, every generated set, each set's secret seed and, once a suite ends, its sealed scoring trials / the one holding the generator's repository. The operator closes both by hand before workers run; the bench harness checks only that the set's folder is out of reach. |
| Device generator | The private tool, kept in its own encrypted volume outside this repository, that writes each generated device's firmware, mission, answer key, reference drivers and mutants from a sampled design. |
| Generated-device interface | The written rules every generated device meets: what its manifest entry, image, public mission and answer key hold, and how they agree. One validator in this repository checks them when the generator seals a device and when the evaluator opens it; rules that only an emulator run can show are checked when its set is qualified. |
| Family hook | One thing the grading core asks of a family: a family module declares it in `HOOKS`, and the core reads it only through `families.family_for`. |
| Difficulty level | One of five generator settings, easy to hard, that raise state, refusal rules, composite tasks, hidden triggers, value encoding and binary obfuscation together. |
| Comparison | Two systems' per-device results on one admitted scoring set under the same evaluator revision and limits, naming every changed dimension. Reports outside a scoring set stay separate series. |
| Anchor | Today's admitted hand-made and upstream cases, run with each pinned system and reported apart from the scoring set; never pooled into its pass rate or comparisons. |
| Leak check | A small fresh batch run at each comparison; a clearly better score on the scoring set than on fresh devices means the set leaked. |
| Selection | Frozen include/exclude decisions and reasons for every lineage in the full catalog. Excluded and blocked slots remain visible. |
| Availability / admission | Public inventory checks / authenticated reference qualification against the case's current grader identity: the hashes of the code the grader imports for that case and of the case's own registration entry, never of the system under test. Availability does not establish admission. A generated set is admitted when both references pass on every device and every mutant fails on a sample of two devices per level and framing. |
| Reference driver / mutant | A correct driver the grader must pass, two distinct ones per case / a deliberately wrong driver the grader must fail. |

What the benchmark adds to four of the tool's terms:

| Term | The benchmark's part |
|---|---|
| Equivalence | For a benchmark run the final verdict is the evaluator's grade. |
| Effect grant | The grader grants each hidden step's effects. |
| Instrument observation | Generated devices offer none for now: workers see only their UART. |
| Public mission | Hidden vectors remain evaluator-owned. A generated mission states only each task's effect; ranges, refusal conditions, framing and the mapping onto firmware commands stay hidden. |

The pass rate scores safety and integrity stops, like a spent budget, as failures, and other operator stops and host stops as faults.
