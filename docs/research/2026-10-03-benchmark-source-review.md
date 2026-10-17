# Sources for benchmark expansion

Targeted primary-source inspection, 2026-10-03, for the [growth plan](2026-10-03-benchmark-growth-plan.md). No upstream builds, performance reproduction or admissions. Counts below are author-reported inventories, not eligible Generative Driver cases.

## Scope

Searched firmware rehosting, P2IM/Fuzzware, embedded-agent benchmarks, FirmReBugger and Zephyr testing; followed official papers/repos/docs and the September 30 shortlist. Included accessible concrete assets, excluded unrelated inference benchmarks and deduplicated paper/repository pairs. This was not a systematic review. Public MCU/emulation software dominates; proprietary devices, analog behavior and physical acquisition are underrepresented.

## Candidates and limits

| Source | Inspected evidence | Reuse decision |
|---|---|---|
| [P2IM](https://github.com/RiS3-Lab/p2im-real_firmware) / [Open Firmware Dataset Builder](https://github.com/VincentDary/open-firmware-dataset-builder) | Source/build/binaries, `aflCall` instrumentation, ancestry table | First admission pool. CNC→grbl, Gateway→StandardFirmata, Console→RIOT, PLC→Modbus overlap across collections. [P2IM notice](https://github.com/RiS3-Lab/p2im-real_firmware/blob/d4c7456574ce2c2ed038e6f14fea8e3142b3c1f7/LICENSE.md) requires asset-specific permission; builder GPL metadata does not license every image. |
| [Fuzzware](https://github.com/fuzzware-fuzzer/fuzzware-experiments) / [Hoedur](https://github.com/fuzzware-fuzzer/hoedur-experiments) | READMEs, targets, reproduction setup; Fuzzware comparison lists 10 P2IM and 11 uEmu targets | Reuse provenance/configuration after deduplication. Fuzzing/crash coverage is not a functional oracle. Hoedur's Docker/Linux setup stays optional. |
| [FirmReBugger paper](https://arxiv.org/abs/2601.15774) / [repository](https://github.com/FirmReBugger/FirmReBugger) | Abstract, oracle interface, target/modification tables; reached/triggered/detected distinction | Inspect ancestry/oracle design. PLC CRC and timeout changes require separate original/transformed identities. License unresolved; no copying authorized. |
| [Zephyr Twister](https://docs.zephyrproject.org/latest/develop/twister/index.html), [Modbus server](https://docs.zephyrproject.org/latest/samples/subsys/modbus/rtu_server/README.html), [emulators](https://docs.zephyrproject.org/latest/hardware/emulator/index.html) | Runner/sample docs and source listings; coils/registers/LED effects | Screen Modbus, then shell/management/settings. Added interfaces must be disclosed. [native_sim](https://docs.zephyrproject.org/latest/boards/native/native_sim/doc/index.html) is application testing, not MCU-binary recovery. Group shared parsers. |
| [EmbedBench paper](https://arxiv.org/html/2506.11003v1) / [EmbedAgent](https://github.com/icip-cas/EmbedAgent) | Construction/dataset/evaluation descriptions; 126 programming/circuit/migration tasks, Wokwi | Coverage inspiration only. Adaptations need permission, an external interface and new qualification; no repository license returned. |
| [EmbedEval](https://github.com/Ecro/embedeval) / [methodology](https://github.com/Ecro/embedeval/blob/main/docs/METHODOLOGY.md) | 267 cases including 48 private; static, compile, runtime, heuristic and mutation layers; platform skips | Inspect public runtime-backed subset. Static checks/counts do not establish eligibility; private cases are unavailable by assumption. |
| [Closed-loop embedded agents](https://github.com/jgcarrasco/closed_loop_evaluation_agents_embedded) | README: five control tasks, plant checks, four feedback regimes; firmware-editing task | Study independent observations/final grading. Recovery needs a frozen external interface and derived-task label. Shared framework is not five independent lineages; license unresolved. |

## First admission queue

Candidates only. Retain failures and move to the next candidate covering the missing behavior/platform; never screen by model success.

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

## Inspected snapshots

GitHub API commits from 2026-10-03 pin inspection, not successful builds. Metadata licenses do not replace per-asset/dependency review.

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

## Statistical sources

[NIST](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm) supplies the Wilson formula; [Stata](https://www.stata.com/manuals/pss-2powerpairedproportions.pdf) explains paired binary power's dependence on discordance. The plan's paired-mean formula is a planning approximation. Pilot variance and simulation are required; no measured power is claimed.
