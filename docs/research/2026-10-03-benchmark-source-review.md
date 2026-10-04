# Sources for benchmark expansion

Inspected 2026-10-03 for the [growth plan](2026-10-03-benchmark-growth-plan.md). This is a targeted source/asset review. Counts below describe upstream authors' inventories, not admitted Generative Driver cases. No upstream performance result was reproduced.

## Search and selection

Question: which existing embedded/firmware benchmarks supply executable applications, behavioral evidence or evaluation methods for firmware-to-interface recovery ending in fresh reuse?

Searches used combinations of “firmware rehosting benchmark dataset Fuzzware P2IM”, “embedded firmware LLM benchmark EmbedBench EmbedEval”, “FirmReBugger benchmark”, and “Zephyr Twister Modbus peripheral emulation”. Followed official paper links, project repositories and documentation, plus the September 30 source shortlist. Included accessible primary artifacts with concrete relevance. Excluded unrelated language-model inference benchmarks and did not use search snippets as proof of asset suitability. This was not a systematic review; search-engine hit counts are not a corpus inventory. Paper/repository pairs were deduplicated by project identity rather than counted as separate evidence.

The source set is concentrated in public microcontroller software and emulation/fuzzing artifacts. Proprietary devices, analog behavior and physical acquisition are underrepresented. Coverage here supports candidate selection, not a claim of exhaustive novelty or representativeness.

## Candidate sources and reuse decisions

### P2IM and the Open Firmware Dataset Builder

[P2IM real firmware](https://github.com/RiS3-Lab/p2im-real_firmware) supplies application source/build instructions and binaries. Its README documents inserted `aflCall` instrumentation; its [license notice](https://github.com/RiS3-Lab/p2im-real_firmware/blob/d4c7456574ce2c2ed038e6f14fea8e3142b3c1f7/LICENSE.md) delegates permissions to original sources. Inspect each asset individually.

The [Open Firmware Dataset Builder](https://github.com/VincentDary/open-firmware-dataset-builder) maps P2IM names to original projects: CNC to grbl-stm32f4, Gateway to StandardFirmata, Console to a RIOT shell, and PLC to a Modbus slave. Use it for source ancestry and build investigation. These are overlapping assets, not another independent corpus. Builder-level GPL metadata does not establish the license of every built firmware.

Decision: first source pool for external admission; prioritize observable command interfaces. Reading scope: repository descriptions, build/provenance table and P2IM license notice. Functional compatibility remains untested.

### Fuzzware and Hoedur

[Fuzzware's experiment repository](https://github.com/fuzzware-fuzzer/fuzzware-experiments) includes P2IM/uEmu targets and experiments on other firmware. Its reproduction instructions describe 10 P2IM and 11 uEmu targets for the comparison. [Hoedur's repository](https://github.com/fuzzware-fuzzer/hoedur-experiments) provides rebuild/configuration tools and explicitly reuses the established P2IM/uEmu/Fuzzware collection, alongside additional targets.

Decision: reuse provenance, eligible firmware and setup knowledge after deduplication. Fuzzing input models and coverage/crash outcomes do not establish the peripheral semantics required by our missions. Hoedur documents a Docker/Linux-oriented reproduction environment; keep optional authoring environments outside the native Generative Driver core. Reading scope: repository READMEs, experiment descriptions and target-directory inventory; no builds.

### FirmReBugger / FirmBench

The [paper record](https://arxiv.org/abs/2601.15774) describes explicit bug oracles and distinguishes reached/triggered/detected outcomes. The [repository](https://github.com/FirmReBugger/FirmReBugger) supplies target groups and a modification table; that table identifies changes to a PLC CRC check and timeout behavior.

Decision: inspect its target ancestry and oracle pattern, but require a separate functional contract. Keep original and transformed binaries distinct. No repository-wide license was returned by GitHub metadata at inspection; asset permission is unresolved, so this plan authorizes no copying. Reading scope: abstract/identity, README oracle interface, target inventory and modification table. No bug-count claim is used for our sample-size target.

### Zephyr samples, emulators and Twister

[Twister](https://docs.zephyrproject.org/latest/develop/twister/index.html) describes harness-driven interactions with test images. The [Modbus RTU server sample](https://docs.zephyrproject.org/latest/samples/subsys/modbus/rtu_server/README.html) exposes coil/register operations and LED effects. [Peripheral emulators](https://docs.zephyrproject.org/latest/hardware/emulator/index.html) provide additional testing mechanisms. The repository also contains shell-module tests, settings and management-server samples.

Decision: screen Modbus first, then shell/management/storage applications. Determine whether an existing interface exposes the desired behavior; an added interface is a disclosed transformation. [native_sim](https://docs.zephyrproject.org/latest/boards/native/native_sim/doc/index.html) is useful for application tests but does not model a particular MCU, so those results cannot be labelled MCU-binary recovery. Multiple sample applications sharing a parser require common origin-group treatment. Reading scope: official runner/sample/emulator documentation and source-directory listings; execution untested.

### EmbedBench / EmbedAgent

The [paper](https://arxiv.org/html/2506.11003v1) and [repository](https://github.com/icip-cas/EmbedAgent) describe 126 tasks involving embedded programming, circuit design and platform migration; dataset fields include problems, diagrams, sketches and tests. The evaluation uses Wokwi.

Decision: use its component/task coverage to identify omissions and consider permission-cleared task adaptations. Its task count cannot be transferred to our driver-recovery denominator. A compiled reference needs a usable external interface and our own behavioral qualification. GitHub returned no repository-wide license metadata; resolve before importing. Reading scope: paper benchmark-construction sections and repository dataset/evaluation description. No native integration demonstrated.

### EmbedEval

[EmbedEval](https://github.com/Ecro/embedeval) reports 267 embedded code-generation cases, including 48 private cases, and an Apache-2.0 repository license. Its [methodology](https://github.com/Ecro/embedeval/blob/main/docs/METHODOLOGY.md) separates static, compilation, runtime, heuristic and mutation layers; it documents runtime skips for some platforms.

Decision: borrow coverage and evaluator-testing ideas. Inspect the runtime-backed public subset before proposing adaptations. Our acceptance remains observable behavior; static conventions and author-reported totals do not establish eligibility. Private upstream cases are not assumed available. Reading scope: README and methodology, particularly evaluation layers and platform limitations. No upstream score or test was reproduced.

### Closed-loop evaluation of embedded agents

The [repository](https://github.com/jgcarrasco/closed_loop_evaluation_agents_embedded) provides five control tasks, deterministic plant/runtime checks and four feedback regimes. Agents modify allowed firmware files; its task is software development.

Decision: study independent plant observations and final-grader separation. A recovery adaptation would need a frozen firmware interface and an explicit derived-task label. Its five tasks share a framework and cannot automatically count as five independent firmware lineages. No repository-wide license metadata was returned. Reading scope: README task/mode/evaluation descriptions. Asset permissions and compatibility remain unresolved.

## First admission queue

These are feasibility candidates, not accepted entries or promised independent groups.

| Priority | Candidate | What admission must demonstrate |
|---|---|---|
| 1 | P2IM Gateway / StandardFirmata ancestry | Actual firmware protocol, bounded I/O effects and independent observer |
| 2 | P2IM CNC / grbl ancestry | Stateful command mission and independently observable effect in emulation |
| 3 | Zephyr Modbus RTU server | Framing/CRC, register/coil operations and LED/GPIO observation |
| 4 | P2IM Console / RIOT shell | A device mission beyond reproducing help text or parsing a known shell |
| 5 | P2IM PLC / Modbus ancestry | Source identity, retained CRC/timing semantics and distinction from other Modbus implementations |
| 6 | Zephyr shell-module application | Existing commands with independent state/effect checks; parser ancestry recorded |
| 7 | Zephyr management server | Bounded management operations, framing and runtime representability |
| 8 | Zephyr settings sample | Whether an existing external interface suffices; persistence independently checked |
| 9 | Further Hoedur/FirmBench targets | Net-new lineage, executable behavior and available asset rights |

If any first-three candidate fails admission, retain the reason and select the next candidate covering the missing behavior/platform. Screening does not select targets by model success.

## Repository snapshots inspected

Commits were obtained from the GitHub API on 2026-10-03. They pin source inspection, not successful builds. License entries are repository metadata only; review each selected asset and dependencies before redistribution.

| Repository | Inspected commit | Repository license metadata |
|---|---|---|
| RiS3-Lab/p2im-real_firmware | `d4c7456574ce2c2ed038e6f14fea8e3142b3c1f7` | NOASSERTION; upstream-specific notice |
| fuzzware-fuzzer/fuzzware-experiments | `1b03b728ea660b777571bf2a6c1ecfa9072f0bf5` | Apache-2.0 |
| fuzzware-fuzzer/hoedur-experiments | `2babc78b72b1331b3122c11f170e9367132b8aa2` | AGPL-3.0 |
| FirmReBugger/FirmReBugger | `e5803848d48cfe74208dc33afc874b87ac806877` | Not returned |
| VincentDary/open-firmware-dataset-builder | `f3a36e86e57bdab99dea2d8f52a613d4925444db` | GPL-3.0 |
| zephyrproject-rtos/zephyr | `d65f1e67ae74336fe9317d29d8064a2a9d824593` | Apache-2.0 |
| icip-cas/EmbedAgent | `877bae655797d01180b562a673a4dfdaaddbadd6` | Not returned |
| Ecro/embedeval | `fdbcb9b93476149cce2d395532bf5164811fe053` | Apache-2.0 |
| jgcarrasco/closed_loop_evaluation_agents_embedded | `4cb20a3860387fb628de8f1be79d6ef94ace21ec` | Not returned |

## Statistical source notes

[NIST's confidence-interval reference](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) supplies the Wilson formula used for the plan's illustrative independent-binomial precision table. [Stata's paired-proportion power manual](https://www.stata.com/manuals/pss-2powerpairedproportions.pdf) documents why paired binary power depends on discordance. The plan's paired-mean approximation is an explicitly labelled planning calculation; actual group-level variance, sample size and operating characteristics require the pilot and simulation. No measured statistical power is claimed.
