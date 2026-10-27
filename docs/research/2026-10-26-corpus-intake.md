# Seven-area software benchmark intake

This is the first milestone of the [growth plan](2026-10-03-benchmark-growth-plan.md), not a replacement for its 12/100-lineage targets. Seven selected upstream lineages are intake coverage. Only an admitted image with native reference qualification can become a model measurement. The three invented families remain development regressions and never substitute for these seven.

The [versioned catalog](../../src/generative_driver/resources/bench/corpora/seven-area-pilot.json) records immutable upstream commits, ancestry groups, missions, behavior strata, acceptance requirements, and admission blockers. Every requirement names observable behavior, compatibility, or safety/integrity and the independent evidence needed to judge it. All seven are currently in the development split; none is a held-out generalization claim.

| Area | Selected upstream lineage | Software evidence and remaining gap |
|---|---|---|
| Robotic control | P2IM's STM32 port of grbl | Built and booted in Renode; accepted unlock and motion commands but no position/step change was observed. Timer/peripheral behavior and an independent step observer remain unqualified. |
| IoT | Tasmota | Pinned application source and build configuration. ESP32 board/image selection, network/GPIO emulation and session representation remain open. |
| Sensors | Aceinna OpenIMU330BI | Pinned inertial-sensor example, packet and scaling definitions. Dependency rights, actual sensor stimuli and a freshness/quantity observer remain open. |
| USB peripherals | sigrok fx2lafw | Pinned 8051 application and board descriptors. Needs SDCC build qualification, a USB-capable FX2 backend and independent digital stimuli. UART is not a substitute for USB evidence. |
| Instruments | EEZ PSU firmware | Pinned Arduino Due application and Linux simulator build. The simulator is a host application, not an MCU image. Needs a qualified MCU/peripheral backend and plant observer. |
| Controllers | ODrive | Pinned application and ASCII interface. Start with non-motion configuration and idle state. Motor/encoder peripherals and an independent state observer remain unqualified; CAN remains a separate gap. |
| Industrial controllers | Zephyr Modbus RTU server | Upstream sample compiled for STM32F4, with unchanged application and Modbus code. Native register/GPIO probes and two identical rebuilds succeeded. Reference/mutant and agent results are recorded separately below. |

Each source and blocker has a pinned citation in the catalog. A source/license declaration is intake evidence, not a completed dependency review. Only the admitted Zephyr binary is redistributed here with component notices; blocked upstream binaries are not bundled.

## Native investigation

The grbl probe used P2IM commit `d4c7456574ce2c2ed038e6f14fea8e3142b3c1f7`, GCC 14.2.Rel1 and Renode 1.16.1. The sole source change disabled the fuzzing hypercall (`noHyperCall = 1`); compilation used `-fcommon`. This is a disclosed port transformation, not an admitted image. Command acknowledgement and a `Run` status did not demonstrate motion. The selected port also has no-op spindle/coolant functions in `CNC/board/gpio.h`; those cannot support actuator coverage. No source changes were made to invent the missing effects.

Zephyr uses release v4.2.0 at `413b789deb391d3a37d06b463288a5fe765ee57e` and pinned CMSIS/HAL modules. The board overlay attaches the Modbus sample to USART2; configuration disables console/log traffic on that UART. Application handlers, CRC processing and exception behavior are unchanged. Two clean builds produced SHA-256 `6137dfd5044bf38e791b77e25db0408ba081fc60ccdd2953629e7fb62090ca82`.

Native probes showed register writes in independently read SRAM, coil effects in the GPIO output register, invalid-address exception replies, and recovery on the following valid request. The firmware accepts more than one coil-on encoding. Qualification therefore includes two distinct valid driver models and checks effects rather than mandating one encoding. Six mutants target constant reads, byte order, register selection, coil selection, missing writes and request CRC. A mutant must fail its named functional requirement; additional related failures do not invalidate the test.

The first reference attempt exposed an emulator reset error: `machine Reset` cleared the vector-table offset and halted the CPU before requests. The recipe now restores that offset after reset. That attempt was a host failure and earned no mutant credit. Native qualification and model execution remain different evidence categories.

The first agent run exposed a second setup error: the public case granted writes, while the agent correctly classified coil switching as actuation. It reported the missing grant. The case and reference drivers now grant and label emulator actuation explicitly. The earlier attempt remains separate setup evidence; it must not be presented as a valid model-performance failure. This is why reference qualification alone cannot replace an agent run through the public assignment path.

That run also had one read timeout. A reference subsequently reproduced a timeout and stale-frame cascade, so this was not attributed to the generated driver without further investigation. Explicit inter-frame delay and smaller emulator scheduling quanta did not reliably fix it. The pinned Renode socket provider defaults to one host read per input byte. Increasing its buffer completed two 250-read sessions, but subsequent full-mission qualification still failed a coil-off request. Read-only stress was insufficient evidence. With that buffer, 10 ms delays failed one of two full missions; 50 ms delays passed both. These small samples did not establish a reliable fix.

The next adapter delivers each received socket block at one virtual timestamp, avoiding separate host scheduling for every UART byte. It changes neither byte values nor firmware, adds no protocol parser, and never retries a write. Three full missions passed without added driver delays; another attempt stopped on a monitor timeout and remains a host failure. The [adapter source](../../src/generative_driver/resources/bench/platforms/BufferedSocketTerminal.cs) and MIT notice are distributed for review. This models a software byte transport, not physical UART timing or TCP packet boundaries as RTU frames. Full native qualification and an agent run are separately required. The [experiment record](../implementation/2026-10-26-modbus-transport-experiment.json) retains every control and limitation.

Qualification now stops at the first failed positive reference. Running mutants after a valid driver has failed wastes work and cannot establish a trustworthy evaluator. A behavioral regression test demonstrated the previous ten native calls and now requires immediate termination after the first failed reference.

## Acceptance and informative evidence

Public mission files supply task meanings, input domains, output units and the fresh-reuse objective. Interpretation, probing and grounding read the same contract. Private vectors, reference drivers and final expected values remain encrypted and evaluator-owned. Fresh reuse receives only its emitted package, objective and permitted operating information.

For Modbus, acceptance means correct read values, requested register/coil effects, preservation of unrelated state, and refusal of an invalid address followed by recovery. The verifier does not require an internal exception-code name. A transport outage cannot satisfy refusal. Tests separately check positive operations, state preservation and subsequent valid reads so an always-failing driver cannot pass.

A fresh-reuse counterexample also exposed an overstrict check: an exploratory argument refusal with unchanged device state caused a later completed mission to fail. The mission now accepts that sequence. It still requires the readback, on/off effects and preservation of unrelated state; host failures and failed calls that changed state remain unacceptable. This test is scripted verifier evidence, not another model success.

The existing fixed public conditions and independently verified quantity repair changes remain in this branch. This corpus refactor does not add repair attempts based on hidden grades. Missing tasks, unit/value discrepancies and connection problems must retain their distinct evidence and repairability; one final evaluator verdict follows the completed run.

The first corrected agent trial exposed an informative-evidence issue without causing a false grade. Its emit worker compared the notes' `model_sha256` (`2aaf5bfc…`) with the SHA-256 of `model.json` (`f034be8a…`) and reported `needs_revision`. Recomputing both identities showed that the former hashes the **model directory**, including adaptation metadata. Every inspected model-file copy had the latter hash. Managed package integrity checks passed; fresh reuse and all 315 final behavioral checks also passed. The existing separation between worker claims and trusted checks correctly prevented this warning from vetoing a working driver.

Recommended follow-up: add explicit hash scope to public tool results and notes, such as `model-directory` and `model-file`, and explain which identity each consumer should compare. Preserve both integrity checks. This recommendation is supported by the recorded run and a direct recomputation; the running benchmark's interfaces were left frozen. A repeated hash string with an ambiguous name is less useful development evidence than a typed identity.

The second corrected trial exposed a separate timing-evidence gap. A monitor timeout stopped it **before dispatch**, with no uncertain effect; its emulator stop was confirmed. A separate 2,000-command read-only monitor experiment passed without reproducing the fault. The suite resumed through the existing verified replacement path, with the stop and intervention retained. Its cause remains unresolved.

After resumption, a coil command took effect but its eight-byte acknowledgment arrived during the next call, after the candidate's one-second deadline. Subsequent identity checks consumed stale replies. The worker identified the mismatch and the bounded repair loop ran. Comparing its accepted models showed that the only operational change was increasing the coil acknowledgment timeout from 1,000 to 10,000 ms; request bytes, response length and decoding were unchanged. The next probe passed. That is evidence of a useful repair, but it does not establish whether the delay originated in the driver timing, transport or emulator scheduling.

Recommended follow-up: record request/response timestamps and emulator virtual-time advancement around each call before attributing a timeout to generated logic. A live process or open socket does not prove the firmware made progress. Keep functional acceptance separate from latency requirements unless the public mission specifies a response budget and the execution clock is qualified. Public timing conditions and progress evidence can inform development without exposing hidden vectors. Also measure control-channel overhead separately: the read-only experiment took about 337 seconds for 2,000 monitor commands; the current observer opens a connection for each individual read. Batching complete state observations is worth testing, while preserving all state checks and never retrying an uncertain write.

## Reading the results

The final suite, `suite-a3ee25bff5614bb2`, ran three fresh Codex trials on the admitted Zephyr image. Each completed all six stages, including package-only reuse. The saved encrypted evidence was authenticated and regraded; the public corpus CLI reproduced the resulting report.

| Trial | Independent behavior | Fresh reuse | Recorded intervention |
|---|---|---|---|
| `run-e45d76bdc5a34463` | 315/315 | Passed | None; the hash-scope warning above did not veto acceptance. |
| `run-432248611aab4078` | 315/315 | Passed | One host recovery and one bounded timeout repair. |
| `run-495c45b196ed415f` | 315/315 | Passed | None. |

Measured success is **3/3 trials on one lineage**. Demonstrated coverage is **3/21 planned slots**, or one of seven lineages; the other 18 slots remain blocked. The terminal pass counts do not erase the recorded host stop or repair. These results establish the pilot's software path, not breadth across seven working devices. [Validation evidence](../implementation/2026-10-26-corpus-validation.json) includes the frozen plan, per-trial evidence hashes, usage, stage attempts and qualification results.

A frozen plan retains `origin group → lineage → mission → repetition` and all six required stages. The runner executes admitted cases through the existing harness. The report accepts only a matching frozen suite manifest and case pins from actual agent emulation. Blocked, unrun, running, stopped, unscored and failed trials remain separate.

Measured success requires equivalence **and** successful fresh reuse for the same trial, with all six stages accepted. Missing mission evidence is unscored. Demonstrated coverage retains every planned slot, including unavailable devices. Origin-group means weight measured lineages equally and leave unavailable groups without a measured rate. Neither a seven-area source catalog nor a passing single lineage establishes broad performance.

The current execution substrate remains one ARM STM32 emulator. It does not establish RS-485 electrical behavior, USB enumeration on physical hardware, radio/network behavior, motor safety, sensor accuracy, real-time deadlines or power-loss persistence. Those gaps stay in the catalog until independently qualified evidence closes them.
