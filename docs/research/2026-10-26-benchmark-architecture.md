# Benchmark architecture: lessons from agentic evaluations

Primary-source review completed 2026-10-26 (America/New_York). These are design comparisons, not benchmark results. The inspected revisions are SWE-bench [`02e7a74`](https://github.com/SWE-bench/SWE-bench/tree/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e), Harbor [`e77a18b`](https://github.com/harbor-framework/harbor/tree/e77a18bfeed53f907f335b9132d87006e01b3dfd), Terminal-Bench 2 [`2fd12b8`](https://github.com/harbor-framework/terminal-bench-2/tree/2fd12b88aafdd04a52c298e3940bcb189f9766d6), original τ-bench [`59a200c`](https://github.com/sierra-research/tau-bench/tree/59a200c6d575d595120f1cb70fea53cef0632f6b), and its successor [`0c69df4`](https://github.com/sierra-research/tau2-bench/tree/0c69df456a96aa76a1162591574dad9c427a34d7).

Original τ-bench [warns that its tasks are outdated][tau-old]. The successor currently calls itself τ-bench 2.0 and [documents incompatible task, prompt, simulator and judge changes][tau-versions]. These revisions support lifecycle comparisons; their scores must not be mixed with historical releases.

## Task contracts and grading

SWE-bench's current [task repository format][swe-package] packages metadata, objective, tests, gold/test patches, evaluation script, Dockerfile and assets. Harbor's [task format][harbor-package] separates instruction, configuration, environment, oracle solution and verifier tests. The [Terminal-Bench 2 README][terminal] identifies Harbor as its harness; the canonical repositories now live under `harbor-framework`, following redirects from `laude-institute`.

Borrow the versioned task contract and separation of candidate generation from grading. Public mission data must not select executable Python or establish evaluator authority. A trusted registration should bind case identity, family, executable adapter and qualification expectations; catalog metadata describes the selected population. Lazy family loaders can keep optional firmware dependencies out of ordinary imports while concentrating dispatch knowledge in one module.

Grade required outputs and effects. SWE-bench [requires fail-to-pass and pass-to-pass tests][swe-grade], rather than equality with the reference patch. Original τ-bench [compares final database state][tau-grade] with state produced by reference actions, plus required communicated outputs. Its successor [explicitly distinguishes reference trajectories from correctness][tau-outcomes]: different correct tool sequences can receive full reward. Harbor [accepts verifier-produced rewards][harbor-grade]; reward quality still depends on each task's verifier.

Preserve Generative Driver's independent equivalence verdict and fresh package-only reuse mission. Worker claims and accepted handoffs remain separate evidence. Distinct valid references, diagnostic/final episodes and targeted mutants qualify the firmware verifier; none of these benchmarks supplies that qualification automatically.

## Selection, trials and denominators

Selection must remain inspectable. SWE-bench Lite [documents exclusions][swe-lite] involving external information, description length and patch complexity. These explain its subset; they are not firmware admission rules. Harbor [supports inclusion/exclusion patterns and task limits][harbor-select]. Current τ-bench [freezes subset IDs, order, strata, seed and provenance][tau-subsets], and requires a new name for changed content.

Retain the full catalog and record an explicit immutable selection with exclusion reasons. Bind that selection and its content digest into the existing suite comparison identity. Report total catalog coverage, selected planned slots, started trials and graded trials separately. Blocked and excluded lineages remain visible; repetitions do not become new devices. Preserve related-lineage split constraints.

Metric names need exact definitions. SWE-bench's harness [maps predictions by instance ID][swe-trials], representing one selected patch per task/run. Harbor [expands attempts × tasks × agents][harbor-trials] and computes [pass@k][harbor-pass]: at least one success among k attempts. τ-bench computes [pass^k][tau-pass], `C(successes,k)/C(trials,k)`: all k sampled attempts succeed. These answer different questions. Keep measured success over graded trials beside demonstrated coverage over planned slots; add reliability estimates only when comparable repetition counts support them.

## Evidence, admission and reproducibility

Public availability is not authenticated admission. Use an operator-only corpus check to authenticate case qualification before the suite starts, then reuse the existing suite owner. Keep one documented CLI lifecycle from catalog and check through frozen plan, run, status and report. Preserve the configurator's six stages, exclusive device ownership, evaluator-only credentials and one final verdict.

Save task/trial identity, effective configuration, candidate/package hashes, stage handoffs, instrument observations, verifier evidence, termination reason and retry history. Harbor [records separate agent/verifier evidence and resolved locks][harbor-artifacts]; its [task locks][harbor-lock] contain content digests and source commits. Current τ-bench [rejects changed manifest pins without a new revision][tau-pins]. Pin build/toolchain, backend, transport/time policy, evaluator inventory, protocol, runtime/provider/model and budgets as well as task content.

Failure categories must accompany rates. SWE-bench [adds likely infrastructure/ambiguous labels without changing its denominator][swe-errors]. Original τ-bench [records exceptions as zero reward][tau-errors]; its current successor [excludes infrastructure simulations that never ran and reports their count][tau-current-errors]. Harbor [distinguishes failure causes and reward validity][harbor-errors], but its [aggregate code][harbor-aggregate] consumes rewards or missing values directly; do not assume every metric filters invalid evidence. Keep host/operator stops, missing grades and model failures distinct. LLM fault labels are diagnostics, not authoritative attribution.

## Apply and defer

Apply trusted case registration first: it gives identity checks and family adapters one seam, with behavior tests through the same interface callers use. Then deepen corpus planning around immutable selection and explicit denominators, followed by authenticated admission through the existing suite owner. This adds locality and test leverage without another runner.

Defer adopting another harness, software-specific difficulty filters, trajectory matching and conversational LLM simulators as firmware standards. Container, database or emulator success establishes only the tested software scope. Physical transport, timing, electrical effects and plant behavior require separate evidence. Hashes establish provenance and integrity; they do not provide confidentiality, filesystem isolation or independent report authentication.

[swe-package]: https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/task/repo.py#L1-L20
[harbor-package]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/models/task/paths.py#L14-L31
[terminal]: https://github.com/harbor-framework/terminal-bench-2/blob/2fd12b88aafdd04a52c298e3940bcb189f9766d6/README.md#L29-L51
[swe-grade]: https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/harness/grading.py#L311-L326
[tau-grade]: https://github.com/sierra-research/tau-bench/blob/59a200c6d575d595120f1cb70fea53cef0632f6b/tau_bench/envs/base.py#L124-L164
[tau-outcomes]: https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/docs/evaluation.md#L86-L109
[harbor-grade]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/verifier/verifier.py#L198-L227
[swe-lite]: https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/collect/make_lite/README.md#L1-L11
[harbor-select]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/models/job/config.py#L120-L154
[tau-subsets]: https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/docs/task-subsets.md#L3-L32
[swe-trials]: https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/harness/run_evaluation.py#L733-L738
[harbor-trials]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/job_plan.py#L140-L168
[harbor-pass]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/utils/pass_at_k.py#L32-L94
[tau-pass]: https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/metrics/agent_metrics.py#L113-L190
[harbor-artifacts]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/models/trial/paths.py#L85-L107
[harbor-lock]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/models/job/lock.py#L605-L630
[tau-pins]: https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/docs/running-the-benchmark.md#L401-L416
[swe-errors]: https://github.com/SWE-bench/SWE-bench/blob/02e7a74ffd0b707aab73d203fe87bdc7c76afc8e/swebench/harness/reporting.py#L88-L108
[tau-errors]: https://github.com/sierra-research/tau-bench/blob/59a200c6d575d595120f1cb70fea53cef0632f6b/tau_bench/run.py#L76-L109
[tau-current-errors]: https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/src/tau2/metrics/agent_metrics.py#L129-L249
[harbor-errors]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/models/trial/result.py#L75-L128
[harbor-aggregate]: https://github.com/harbor-framework/harbor/blob/e77a18bfeed53f907f335b9132d87006e01b3dfd/src/harbor/job.py#L1165-L1204
[tau-old]: https://github.com/sierra-research/tau-bench/blob/59a200c6d575d595120f1cb70fea53cef0632f6b/README.md#L3-L5
[tau-versions]: https://github.com/sierra-research/tau2-bench/blob/0c69df456a96aa76a1162591574dad9c427a34d7/README.md#L89-L119
