# Firmware learning practice

Primary-source research, 2026-10-30. Read-only: no devices, emulators, benchmarks or model runs. Unmarked statements restate the linked source. **Synthesis** marks our conclusions, **Differs** a disagreement between sources, **Unverified** a claim we could not check against the source that owns it.

## Summary

- Rehosting ([SoK §6.1][sok]), active automata learning ([Angluin][angluin], [Vaandrager][vaa]) and legacy refactoring by equivalence checking ([Schuts et al. §1][shv]) run one loop: state a hypothesis model, experiment on the device or the original firmware, revise on each difference.
- Testing never proves equivalence; studies state it up to a bound: the alphabet, the sequence depth or extra-state bound, and the test budget ([Vaandrager][vaa], [TLS §3][tls], [SSH §5][ssh], [X.290 Introduction][x290]).
- Learning assumes a reset to one known state and deterministic replies, and in practice needs a mapper between abstract operations and wire bytes; practitioners repeat queries and cache replies to catch nondeterminism ([Vaandrager][vaa], [TCP §3.3][tcp], [SSH §4.3][ssh], [Lego §3.2][lego]).
- Bring-up starts read-only: identify, initialize, then read status back; writes wait until identity is certain ([hwmon §3][hwmon], [BMI270 §3][bmi]).
- Black-box tests missed a controller behaviour that the code-derived model exposed ([ESM §3.2][esm]); static help for equivalence queries is an open problem ([Vaandrager][vaa]). **Synthesis:** the firmware should shape experiments, not only hypotheses.
- Conformance testing defines pass, fail and inconclusive verdicts, and TTCN-3 adds error for test-system faults ([X.290 §6.5.1][x290], [TTCN-3 §24.1][ttcn3]). **Synthesis:** these match the redesign's rules.
- **Synthesis:** a ten-step process fits the six stages. The main gaps are an explicit reset/determinism step and a bounded counterexample search whose bound travels with the verdict.

## 1. Driver bring-up practice

*Bring-up* is the first work that makes software control new hardware.

| Step | Established practice | Done when |
|---|---|---|
| Start from the nearest known part | Check for an existing driver first; new temperature-sensor chips are often variants or relabels of older ones ([hwmon §3][hwmon]). A new Zephyr board starts as a copy of an existing board with the same SoC ([Zephyr porting][zport]). | No criterion stated. |
| Declare the device; probe last | Linux prefers explicit instantiation (devicetree, ACPI, board code); bus probing is a last resort with possible side effects ([I2C instantiation, method 3][i2c]). `i2cdetect` warns that scanning can confuse a bus and lose data, and names chips its probe modes corrupt or lock up ([i2cdetect(8)][i2cdetect]). | Identity comes from the declaration, or from a detect routine that is reliable ([hwmon §3][hwmon]). |
| Read before writing | Detect routines avoid register writes and write only after reads make the identity certain, since the chip may not be the expected one and a write can misconfigure it ([hwmon §3][hwmon]). | Same. |
| Identity, initialization, status | The BMI270 quick start reads CHIP_ID to check communication, runs the vendor init sequence (optionally reading the loaded configuration back for comparison), then checks INTERNAL_STATUS after at least 20 ms. The configuration blob comes from the vendor's reference API, and the datasheet warns that code bypassing that API must reproduce its sign extension ([BMI270 §3, §4.6][bmi]). | CHIP_ID reads 0x24; INTERNAL_STATUS bit 0 is set ([BMI270 §3][bmi]). |
| Smallest observable path first | Minimal UART console and GPIO configuration, then build, flash and fix; hello_world, then blinky ([Zephyr porting][zport]). | Each board target passes the minimum suite (samples/philosophers, tests/kernel) under Twister, Zephyr's test runner, before upstreaming ([Zephyr porting][zport]). |
| Undocumented hardware: trace the original driver | mmiotrace logs every memory-mapped I/O (MMIO) access of a kernel driver (width, address, value, program counter, time). It was built to reverse engineer memory-mapped devices, first for Nouveau; it is x86-only, unreliable with several CPUs online, perturbs timing and can drop events ([mmiotrace][mmio]). Asahi ran unmodified macOS under its m1n1 hypervisor, logged the official drivers' hardware accesses without reading their code, decoded the traces layer by layer and prototyped the driver in Python from them ([Asahi 2021-08][asahi]). | No criterion stated; the Asahi sequence ends in a working prototype, then generated kernel marshaling code ([Asahi 2021-08][asahi]). |
| Test against an emulated device, then hardware | i2c-stub fakes an SMBus chip preloaded with `i2cset` or a chip dump; a driver that polls for a change can hang on it ([i2c-stub][stub]). Zephyr bus emulators run the unchanged driver against an emulated peripheral; a backend API lets tests set or read emulator state out of band, and emulators can inject failures ([Zephyr emulators][zemul]). | hwmon asks for thorough testing and an explicit note when no run-time test was done ([hwmon §3][hwmon]). |

**Synthesis:** bring-up orders experiments by risk and observability — read-only identity, vendor-prescribed writes, status read-back, then function — and checks each rung with something the device reports about itself.

## 2. Firmware-analysis methodology

### OWASP Firmware Security Testing Methodology (FSTM)

Nine stages: information gathering, obtaining firmware, analyzing firmware, extracting the filesystem, analyzing filesystem contents, emulating firmware, dynamic analysis, runtime analysis, binary exploitation ([FSTM][fstm]). For interfaces: stage 1 collects schematics, datasheets, source location, FCC IDs and data-flow diagrams; stage 2 extracts images over UART, JTAG or the bootloader, with flash or MCU removal as the last resort; stage 3 checks format, compression and encryption with `file`, `strings` and `binwalk`; stage 6 separates user-mode emulation of single binaries from full-system emulation, and moves binaries onto the device when emulation fails ([FSTM][fstm]).

Done: FSTM states no completion criteria. It names outputs (a light threat model, tool reports to review, a proof of concept) and reads as a linear sequence ([FSTM][fstm]).

### Rehosting

*Rehosting* builds a *rehosted embedded system* (RES): the firmware plus a virtual environment that reproduces only the hardware the firmware needs for an analysis ([SoK §2][sok]). The process: obtain the firmware and the device, determine the instruction set, enumerate peripherals, gather documentation; build an initial specification (CPU, firmware, peripheral models, configuration); run it; compare with ground-truth traces from the device or with expected behaviour; update; repeat ([SoK §6, Fig. 3][sok]).

Done: fidelity evaluation decides when rehosting is finished ([SoK §6.1][sok]). Fidelity has no formal definition and measuring it is undecidable in general, so studies report observable differences ([SoK §3.4][sok]). For a RES meant to reproduce behaviour, the SoK proposes input–output equivalence, with confidence set by the scale and diversity of inputs and the precision of the output comparison ([SoK §7.4][sok]).

| Approach | How the interface is learned | How correctness is judged |
|---|---|---|
| Hardware in the loop | Avatar forwards the emulated firmware's I/O to the real device. Full forwarding is very slow (about five memory accesses per second over a 38400-baud debug link) and breaks time-critical code ([Avatar][avatar]). Avatar² records an execution with MMIO forwarded to a board, then replays it without the board ([Avatar² §IV.C][avatar2]). | The device answers every access. |
| Learned from recordings | Pretender records firmware–hardware interactions, clusters MMIO into peripherals, infers interrupts and trains models; hardware is needed only to record ([Pretender §3][pretender], [SoK §5.2.2][sok]). | The firmware runs fully emulated and reaches code not seen while recording ([Pretender][pretender]). |
| Hardware abstraction layer (HAL) replacement | HALucinator finds vendor HAL functions in the binary by matching against the vendor SDK (LibMatch) and replaces them with Python handlers; uninteresting peripherals may get handlers that only report success ([HALucinator §2.3, §3][halu]). | External behaviour matches the physical board (same data sent, same pages served); equal code paths could not be checked on hardware ([HALucinator §5.3][halu]). |
| Access-pattern models | P2IM classifies registers as control, status, data or control-status from access patterns and picks status values by parallel trial runs from a snapshot (explorative execution). Its replies need only pass the firmware's own checks, which the authors contrast with testing functional correctness ([P2IM §2.3, §3.1, §4][p2im]). | No crash, stall or skipped operation; 55 of 70 unit-test images passed ([P2IM §2.4, §5.1][p2im]). |
| Specification-guided | SEmu turns chip-manual text into condition–action rules with NLP; symbolic execution localizes wrong rules for manual repair ([SEmu][semu]). | Edit distance between execution traces ([SEmu][semu]). |

**Differs:** P2IM and HALucinator derive peripheral behaviour from the firmware; SEmu argues firmware can lack clues or mislead (for example, buggy firmware) and uses the manuals ([SEmu][semu]). FSTM is linear; the SoK models rehosting as iterative refinement.

**Synthesis:** these studies judge emulators of the *hardware*. A driver judged against the *firmware* takes the firmware's external behaviour as the reference; manuals supply hypotheses. Fuzzing-grade models (P2IM replies, success-only HAL handlers) are not oracles for functional equivalence.

## 3. Protocol reverse engineering

### Message-format inference

- **Discoverer** works from network traces alone: tokenize messages, cluster by token pattern, infer field properties and semantics (length, offset, cookie), split clusters recursively on format-distinguishing tokens, then merge by type-based alignment. Judged against Ethereal parsers by correctness (one true format per cluster), conciseness and coverage: over 90% of clusters correct, about five inferred formats per true format ([Discoverer §4.2][disc]). It cannot infer formats absent from the trace, supports only three semantics and leaves state machines to future work ([Discoverer][disc]).
- **Polyglot** watches the binary parse input (dynamic taint analysis), on the premise that how an implementation processes a message reveals its format. It finds length and pointer fields, separators and keywords. Compared with Wireshark's hand-written formats, the differences were small and mostly reflected implementations handling fields differently ([Polyglot §1][poly]).
- **Netzob** works in three steps: cluster and partition messages (Needleman–Wunsch alignment, UPGMA clustering); characterize fields (regular-expression types, size fields, values that depend on the host environment); infer the transition graph by stimulating a live implementation with an adapted L\*. That needs known input symbols, an obvious failure on invalid sequences, a reset between experiments and temporarily deterministic behaviour; the authors confine the implementation to limit nondeterminism ([Netzob 2012, pp. 6–9][netzob], [README][netzob-repo]). **Unverified:** the peer-reviewed Netzob paper ([ASIA CCS 2014][netzob14]) could not be read.

**Differs:** Polyglot argues that trace clustering lacks semantic information and analyzes the binary instead ([Polyglot][poly]); Discoverer stays with traces and infers only length, offset and cookie semantics ([Discoverer][disc]).

Done: both papers score inference against reference parsers (Ethereal, Wireshark) ([Discoverer §4][disc], [Polyglot][poly]). **Synthesis:** an unknown device has no such parser, so a format is accepted only through device experiments.

### Active automata learning

A *Mealy machine* is a finite-state machine that emits one output per input. In Angluin's *minimally adequate teacher* (MAT) setting, a learner asks *membership queries* and *equivalence queries* (is this hypothesis correct?); the teacher answers an equivalence query with yes or a *counterexample* on which hypothesis and target differ. L\* fills an observation table with membership queries until it is closed and consistent, conjectures a machine and refines it on each counterexample, in time polynomial in the target's states and the longest counterexample ([Angluin][angluin]).

For Mealy machines, a membership query returns the outputs for an input sequence. For a real *system under learning* (SUL), it resets the SUL and runs the inputs; an equivalence query is approximated by a finite conformance test suite, so a learned model is never certain, though a complete suite exists for a known bound on states. Membership queries grow linearly with inputs and quadratically with states, while conformance testing becomes the bottleneck; Vaandrager puts the practical limit below about 100 inputs. A *mapper* translates abstract symbols to concrete messages and back, and abstraction is the key to scaling ([Vaandrager][vaa]); Aarts et al. ground mappers in predicate abstraction, supplied from outside because the system is a black box ([Aarts et al.][aarts]). Learning on input subsets and merging inputs into higher-level actions also help. Open problems: systems emitting zero or several outputs per input, timing, nondeterminism, model quality, and static analysis that helps answer equivalence queries ([Vaandrager][vaa]). LearnLib approximates equivalence with the W- and Wp-methods (conformance suites that can find missing states), random walks and exhaustive tests up to a depth; it filters queries through a cache, parallelizes them and reuses states to cut resets ([LearnLib][learnlib], [repository][learnlib-repo]). Without any reset, learning still works with homing sequences, inputs whose outputs reveal the state reached ([Rivest & Schapire][rs93]).

### Applied studies

| Study | Set-up | Lesson the authors report |
|---|---|---|
| TLS state fuzzing | A stateful harness translates abstract inputs to TLS packets. Outputs are TLS messages and alerts plus timeout, decryption-failed and connection-closed symbols. Equivalence is tested with a W-method variant whose answer holds only if the implementation has at most the found states plus a chosen depth ([TLS §3, §3.1][tls]). | New flaws in GnuTLS, JSSE and OpenSSL; each implementation learned to a unique model ([TLS][tls]). |
| SSH | The mapper is a stateful transducer; it merges several outputs into one symbol. Timeouts were tuned until behaviour was deterministic; an SQLite cache checks new observations against old ones and lets runs resume ([SSH §2.3, §4.2–4.3][ssh]). | Tests join an access sequence (reaches a state), a middle part of length k and a distinguishing sequence (identifies the state reached). Exhaustive suites give correctness unless the system has k more states; random suites found counterexamples better but give no formal confidence ([SSH §5][ssh]). |
| TCP | Mapper theory; the abstraction is tested by sending different concrete values that must give the same abstract behaviour ([TCP §2][tcp]). Missed sniffed packets looked like timeouts, so each observation was repeated three times and consistent results cached ([TCP §3.3][tcp]). | Several departures from the RFCs ([TCP §5][tcp]). |
| Printer controller (ESM) | 77 inputs, 3,410 states, about 60 million queries; reset before every query; parameters split into separate inputs and grouped where behaviour was identical ([ESM §3.1, §3.3][esm]). | Random, W and Wp testing failed to find some counterexamples in acceptable time; one needed an input repeated eight times and was first built by hand from the code model. Learning stopped after eight more hours without a counterexample, and the result was checked equivalent to a model generated from the code ([ESM §3.2–4][esm]). |
| Smartcard reader | A Lego robot presses buttons; cutting USB power resets the reader after each query ([Lego §3][lego]). | Physical nondeterminism needed majority voting: run twice, a third time on disagreement, raise an error if all differ. A W-method run to eight states took about 88 hours ([Lego §3.2, §4.2][lego]). |
| Legacy refactoring (Philips) | Learn the legacy and the new implementation, compare the learned models with an equivalence checker, grow the input set step by step ([Schuts et al. §1][shv]). | On a counterexample, first check each model against its own implementation (model wrong: relearn), otherwise the implementations differ and one is fixed. Faults were found in both; tiny models took up to nine hours at about ten seconds per query ([Schuts et al. §1, §5][shv]). |

**Differs:** SSH reports random tests finding counterexamples better than exhaustive suites ([SSH §5][ssh]); in ESM, random, W and Wp all failed and code knowledge was needed ([ESM §3.2][esm]).

Done: L\* conjectures when its table is closed and consistent ([Angluin][angluin]). Studies stop when a bounded or budgeted search finds no counterexample ([TLS §3][tls], [ESM §3.3][esm]) or when the equivalence checker accepts the current input set ([Schuts et al.][shv]).

## 4. Model-based conformance testing

- **ioco.** Implementations are modelled as transition systems that always accept inputs. *Quiescence* (no output is coming) counts as an observable output. An implementation conforms (*ioco*) when, after every suspension trace (a trace that may include observed quiescence) the specification allows, each output it can produce, quiescence included, is one the specification allows; behaviour after traces the specification does not define is not judged ([TorX][torx], [Tretmans TACAS '96][tre96]). Generated suites are sound (a failure proves non-conformance) and exhaustive (every non-conforming implementation fails some test in the suite); TorX generates, executes and judges tests on the fly ([TorX][torx]).
- **ISO/IEC 9646 / ITU-T X.290.** The texts are technically aligned; X.290 is also published as ISO/IEC 9646-1:1994 ([X.290][x290]). Assessment runs a static review of the implementation's claimed capabilities, test selection and parameterization, a test campaign, analysis of results and a report (§6.3, Fig. 1). Test types rise in depth from basic interconnection (prima facie) through capability and behaviour tests to conformance-resolution tests that give a definite answer with diagnostics (§6.1). Verdicts are pass, fail and inconclusive (§6.5.1.3); an outcome the test case did not foresee is recorded as a test-case error or abnormal termination, not as a verdict (§6.5.1.4–6). Results must be repeatable (§6.5.3). Only external behaviour is the standard, and testing gives confidence on representative cases, never a guarantee (Introduction).
- **TTCN-3.** A verdict starts at none and only rises: none < pass < inconclusive < fail. The test system alone sets error, for its own runtime faults, and nothing overrides it ([ES 201 873-1 §24.1, Table 30][ttcn3]).

Done: every executed test case ends with a verdict or a recorded error or abnormal termination, and the protocol test report gives every case's result with a reference to its conformance log ([X.290 §6.5.1–6.5.2][x290]).

**Synthesis:** the redesign fixes these rules: success is functional equivalence; an unknown is recorded apart from failure; a device refusal is an answer; only a missing or malformed reply is a communication failure; a reference model grades every step. They map onto this vocabulary. Departure from the reference is fail; behaviour that could not be compared is inconclusive (unknown); a communication failure is closest to TTCN-3 error or X.290 abnormal termination. A refusal the reference model predicts is a pass. The sources treat silence as an output ([TorX][torx]; the TLS harness's timeout symbol, [TLS §3.1][tls]); a missing reply is therefore a communication failure only where the reference model predicts a reply, so the model must predict silence explicitly.

## Generalized process (synthesis)

Firmware and any documents go in; a driver whose outputs and effects match the original firmware comes out. Terms: the *alphabet* is the finite set of abstract operations a learner may send; a *query* is one input sequence run from reset; *exit evidence* is what a step hands on. Exit evidence informs later steps and the final grade; it does not stop the run. H, E and R mark hypothesis, experiment and revision.

1. **Pin inputs and objective.** Hash the firmware images, documents, vendor reference code, device binding, effect grants and objective. Name the nearest known relative: part family, vendor SDK, existing driver. *From:* [FSTM stages 1–2][fstm], [SoK Fig. 3][sok], [hwmon §3][hwmon], [HALucinator §3][halu]. *Exit:* hashed inventory and missing inputs.
2. **Triage the image.** H: format, instruction set, load address, compression or encryption. E: disassemble and check that vectors, pointers and string references resolve under the chosen mapping. R: change the mapping until they do and keep rejected alternatives. *From:* [FSTM stage 3][fstm], [SoK §6.1][sok]. *Exit:* a justified load map.
3. **Inventory interfaces and handlers from code.** Separate external channels from internal peripherals. Locate vendor library calls, the receive path, command dispatch, framing checks (delimiters, length fields, checksums), keywords, error replies, state variables, counters and timeouts. Treat document and vendor-code claims as hypotheses, since implementations depart from specifications. *From:* [SoK Fig. 3][sok], [HALucinator LibMatch][halu], [P2IM §3.1][p2im], [Polyglot][poly], [Netzob][netzob], [TCP][tcp]. *Exit:* candidate alphabet and formats with code addresses, plus **test targets**: code facts that predict hard-to-reach behaviour (counters, thresholds, error branches, timeouts).
4. **Write the hypothesis as an executable model and a mapper.** The model holds states, the alphabet, outputs including refusals and silence, and each step's effect on device state. The mapper holds encoding, framing, integrity checks, multi-message replies and timeouts. Predict replies from handler code independently of the model. *From:* [Vaandrager][vaa], [TLS §3.1][tls], [SSH §4.2][ssh], [TCP §2][tcp], [TorX][torx]. *Exit:* the model agrees with its own predicted replies — consistency, not device evidence.
5. **Make first contact; establish reset and determinism.** Listen before sending. Identify with read-only queries; write only after identity is certain and only within grants. Find a reset that returns the device to one known initial state (soft reset, power cycle, emulator restore) and confirm it by identifying again. Repeat identical queries to measure determinism, and tune timeouts so slow replies are not read as silence. Without a reliable reset, plan reset-free learning. *From:* [I2C instantiation][i2c], [hwmon §3][hwmon], [i2cdetect(8)][i2cdetect], [BMI270 §3][bmi], [X.290 §6.1][x290], [Netzob][netzob], [Lego §3.2][lego], [TCP §3.3][tcp], [SSH §4.3][ssh], [Rivest & Schapire][rs93]. *Exit:* identity, reset procedure and determinism evidence.
6. **Query the device.** From reset, run input sequences and record the output and, where observable, the device state after every step. Label each observation as physical, emulated or replayed; cache it and compare repeats with the cache. Start with read-only operations, then add state-changing ones within grants. Where the original firmware can be exercised by its own host software, record that traffic too. *From:* [Angluin][angluin], [LearnLib][learnlib], [SSH §4.3][ssh], [Schuts et al. §1][shv], [Vaandrager][vaa], [Avatar² §IV.C][avatar2], [Pretender][pretender], [Asahi][asahi]. *Exit:* a hypothesis consistent with every cached observation for the current alphabet.
7. **Search for counterexamples.** Test the hypothesis with bounded suites (access sequence, middle part of length k, distinguishing sequence), random walks and sequences built from the step-3 test targets. Rerun any difference; if it repeats, decide whether the model or the observation is wrong, then revise (step 4) and requery (step 6). Stop when no counterexample appears within the declared bound or the budget is spent. *From:* [Vaandrager][vaa], [LearnLib][learnlib], [TLS §3][tls], [SSH §5][ssh], [ESM §3.2–3.3][esm], [Schuts et al. §1][shv]. *Exit:* the alphabet, k, test count and length, and budget used — the bound any later verdict inherits.
8. **Ground meanings.** H: each field's unit, scale and sign, and each write's effect. E: vary a stimulus, or compare decoded values with an independent reference (instrument or operator reading, vendor reference arithmetic). Send several concrete values for each abstract operation; they must give the same abstract behaviour, and nondeterminism means the abstraction is too coarse. R: fix the decoder, split the abstraction, or keep the meaning as a scoped uncertainty. *From:* [TCP §2][tcp], [Vaandrager][vaa], [Netzob][netzob], [Discoverer][disc], [BMI270 §4.6][bmi], [Zephyr emulators][zemul], [HALucinator §5.3][halu]. *Exit:* every meaning tied to evidence; unsupported meanings listed.
9. **Emit the driver from the accepted model.** Freeze model, mapper and runtime as one package; ship the cached traces and the step-7 suite as replay and regression tests. *From:* [ESM §1][esm], [Avatar² §IV.C][avatar2], [i2c-stub][stub], [Zephyr emulators][zemul]. *Exit:* the package replays its recorded exchanges — self-evidence only.
10. **Grade the frozen driver step by step against the reference model.** A fresh agent receives only the package and objective. For each step of each graded sequence, the reference model predicts the answer and the device state, and the grader compares both on the fly. A step passes, fails, is inconclusive (unknown) or records a communication error; a sequence takes the worst of fail, inconclusive and pass, with errors kept apart. The verdict carries the step-7 bound. *From:* [TorX][torx], [X.290 §6.5.1][x290], [ES 201 873-1 §24.1][ttcn3], [SoK §7.4][sok], [Schuts et al.][shv]. *Exit:* per-step verdicts, unknowns, errors and the bound.

Cross-cutting:

- The redesign allows retrying a single command where the next state comparison would expose a repeated effect. The sources repeat whole queries from reset — three times in [TCP §3.3][tcp], by majority vote in [Lego §3.2][lego] — so a repeat cannot accumulate effects there.
- Compare internal state directly where it is observable (emulator backends, instrument readings). Otherwise infer it with distinguishing sequences, as conformance suites do ([SSH §5][ssh]).
- Budgets bound queries and repairs. A spent budget ends the loop with the best revision, and that revision's bound is stated.
- In a generic run the only independent reference is the device itself. A learned model used as the reference is the pipeline's own evidence, matching the design's *self-evidence* label ([design][design]).

## Mapping onto the current stages (interpretation)

Our reading of [CONTEXT.md][ctx] and the [accepted design][design], with stage tools from [`STAGE_TOOLS`][tools] and the [interpretation skills][skill-if]. Fit: kept, merged, split or missing.

| # | Generalized step | Current stage | Fit | Note |
|---|---|---|---|---|
| 1 | Pin inputs and objective | acquire | kept | Firmware, datasheet, protocol-spec and classification tools; hashed handoffs. |
| 2 | Triage the image | interpret | merged | `image_map_check` and `ghidra_run` inside interpretation. |
| 3 | Inventory interfaces and handlers | interpret | merged | The skill asks for an external-interface inventory. Read-only probe proposals exist; no code-derived list of hard-to-reach behaviour reaches probe or ground. |
| 4 | Hypothesis model and mapper | interpret; emit freezes contract and protocol | kept | `model.json` plus independently predicted `replies.json`, qualified offline; the contract and declarative protocol separate meaning from wire exchanges. |
| 5 | First contact, reset, determinism | acquire (serial listen), probe (identity first) | split; partly missing | No step establishes a reset to a known initial state before each query or measures determinism; `probe_run` takes a sample count. |
| 6 | Query the device | probe | kept | Probe records are bound to the model that wrote them; interpretation may propose read-only probes ([skill][skill-bin]). No cross-query consistency cache is described. |
| 7 | Counterexample search and revision | probe and ground, back to interpret | split; bound missing | Device differences drive repair within a repair budget (two by default). The sources iterate until a bounded search finds nothing; no test bound is recorded with the result. |
| 8 | Ground meanings | ground | kept | Units, scaling and effects against probe records and operator observations. Varying concrete values within an abstract operation is not described. |
| 9 | Emit driver with replay and regression | emit | kept | `emit_package` (regression suite, evidence) and `emit_check` replay. |
| 10 | Fresh reuse and step-by-step grading | reuse, then the bench-harness evaluator | split | Reuse sees only package and objective; benchmarks are graded after the run; generic runs carry self-evidence. Run-level `unscored` exists; per-step pass, fail, inconclusive and error verdicts are not design terms. |

## Sources

Access notes. **Unverified:** the ASIA CCS 2014 Netzob paper (only its metadata could be fetched; the publisher page and HAL block automated readers), so Netzob claims rest on the authors' 2012 whitepaper. ISO/IEC 9646 itself was not read; X.290 is the aligned ITU-T text. Tretmans' 1996 journal paper and 2008 tutorial were not reachable; ioco is cited from the TACAS '96 paper and the TorX paper. Aarts et al. 2015 was read only as its author-page abstract. SEmu's conference venue was not confirmed; its arXiv extended version was read. W-method claims come from the TLS, SSH and LearnLib papers, not from Chow's original. No general vendor bring-up application note was found; the BMI270 datasheet is the vendor example.

Bring-up

- [How to instantiate I2C devices][i2c], Linux kernel documentation.
- [How to Get Your Patch Accepted Into the Hwmon Subsystem][hwmon], Linux kernel documentation.
- [i2c-stub][stub], Linux kernel documentation.
- [In-kernel memory-mapped I/O tracing][mmio], Linux kernel documentation.
- [i2cdetect(8)][i2cdetect], i2c-tools manual page (Debian copy of i2c-tools 4.4).
- [External Bus and Bus Connected Peripherals Emulators][zemul], Zephyr Project documentation.
- [Board Porting Guide][zport], Zephyr Project documentation.
- [BMI270 datasheet][bmi], Bosch Sensortec, BST-BMI270-DS000-08, revision 1.6, March 2026.
- [Progress Report: August 2021][asahi], Asahi Linux.

Firmware analysis

- [OWASP Firmware Security Testing Methodology][fstm], A. Guzman et al., GitHub repository.
- [SoK: Enabling Security Analyses of Embedded Systems via Rehosting][sok], A. Fasano et al., ASIA CCS 2021, [doi:10.1145/3433210.3453093](https://doi.org/10.1145/3433210.3453093) (accepted manuscript linked).
- [HALucinator: Firmware Re-hosting Through Abstraction Layer Emulation][halu], A. Clements et al., USENIX Security 2020.
- [P2IM: Scalable and Hardware-independent Firmware Testing via Automatic Peripheral Interface Modeling][p2im], B. Feng, A. Mera, L. Lu, USENIX Security 2020.
- [Avatar: A Framework to Support Dynamic Security Analysis of Embedded Systems' Firmwares][avatar], J. Zaddach et al., NDSS 2014, [doi:10.14722/ndss.2014.23229](https://doi.org/10.14722/ndss.2014.23229).
- [Avatar²: A Multi-target Orchestration Platform][avatar2], M. Muench et al., BAR 2018.
- [Toward the Analysis of Embedded Firmware through Automated Re-hosting][pretender] (Pretender), E. Gustafson et al., RAID 2019.
- [What Your Firmware Tells You Is Not How You Should Emulate It][semu] (SEmu), W. Zhou et al., arXiv:2208.07833, extended version.

Protocol reverse engineering

- [Discoverer: Automatic Protocol Reverse Engineering from Network Traces][disc], W. Cui, J. Kannan, H. J. Wang, USENIX Security 2007.
- [Polyglot: Automatic Extraction of Protocol Message Format using Dynamic Binary Analysis][poly], J. Caballero, H. Yin, Z. Liang, D. Song, CCS 2007, [doi:10.1145/1315245.1315286](https://doi.org/10.1145/1315245.1315286).
- [Reverse and Simulate your Enemy Botnet C&C: Mapping a P2P Botnet with Netzob][netzob], G. Bossert, F. Guihéry, Black Hat Abu Dhabi 2012 whitepaper; [Netzob repository][netzob-repo].
- [Towards automated protocol reverse engineering using semantic information][netzob14], G. Bossert, F. Guihéry, G. Hiet, ASIA CCS 2014 (metadata only).
- [Learning regular sets from queries and counterexamples][angluin], D. Angluin, Information and Computation 75(2), 1987.
- [Model learning][vaa], F. Vaandrager, Communications of the ACM 60(2), 2017, [doi:10.1145/2967606](https://doi.org/10.1145/2967606).
- [The Open-Source LearnLib: A Framework for Active Automata Learning][learnlib], M. Isberner, F. Howar, B. Steffen, CAV 2015; [LearnLib repository][learnlib-repo].
- [Inference of finite automata using homing sequences][rs93], R. Rivest, R. Schapire, Information and Computation 103(2), 1993.
- [Protocol State Fuzzing of TLS Implementations][tls], J. de Ruiter, E. Poll, USENIX Security 2015.
- [Model Learning and Model Checking of SSH Implementations][ssh], P. Fiterău-Broştean et al., SPIN 2017, [doi:10.1145/3092282.3092289](https://doi.org/10.1145/3092282.3092289).
- [Combining Model Learning and Model Checking to Analyze TCP Implementations][tcp], P. Fiterău-Broştean, R. Janssen, F. Vaandrager, CAV 2016, [doi:10.1007/978-3-319-41540-6_25](https://doi.org/10.1007/978-3-319-41540-6_25).
- [Applying Automata Learning to Embedded Control Software][esm], W. Smeenk, J. Moerman, F. Vaandrager, D. Jansen, ICFEM 2015, [doi:10.1007/978-3-319-25423-4_5](https://doi.org/10.1007/978-3-319-25423-4_5).
- [Automated Reverse Engineering using Lego][lego], G. Chalupar, S. Peherstorfer, E. Poll, J. de Ruiter, WOOT 2014.
- [Refactoring of Legacy Software using Model Learning and Equivalence Checking][shv], M. Schuts, J. Hooman, F. Vaandrager, iFM 2016, [doi:10.1007/978-3-319-33693-0_20](https://doi.org/10.1007/978-3-319-33693-0_20).
- [Generating models of infinite-state communication protocols using regular inference with abstraction][aarts], F. Aarts, B. Jonsson, J. Uijen, F. Vaandrager, Formal Methods in System Design 46, 2015, [doi:10.1007/s10703-014-0216-x](https://doi.org/10.1007/s10703-014-0216-x) (abstract only).

Conformance testing

- [Test Generation with Inputs, Outputs, and Quiescence][tre96], J. Tretmans, TACAS 1996, [doi:10.1007/3-540-61042-1_42](https://doi.org/10.1007/3-540-61042-1_42).
- [TorX: Automated Model Based Testing][torx], J. Tretmans, E. Brinksma, First European Conference on Model-Driven Software Engineering, 2003.
- [ITU-T X.290 (04/95): OSI conformance testing methodology and framework — General concepts][x290], aligned with ISO/IEC 9646-1.
- [ETSI ES 201 873-1 V4.16.1 (2024-10): TTCN-3 Part 1, Core Language][ttcn3].

Repository

- [CONTEXT.md][ctx], [accepted design][design], [configurator stage tools][tools], [interface interpretation skill][skill-if], [firmware-binary interpretation skill][skill-bin].

[i2c]: https://docs.kernel.org/i2c/instantiating-devices.html
[hwmon]: https://docs.kernel.org/hwmon/submitting-patches.html
[stub]: https://docs.kernel.org/i2c/i2c-stub.html
[mmio]: https://docs.kernel.org/trace/mmiotrace.html
[i2cdetect]: https://manpages.debian.org/testing/i2c-tools/i2cdetect.8.en.html
[zemul]: https://docs.zephyrproject.org/latest/hardware/emulator/bus_emulators.html
[zport]: https://docs.zephyrproject.org/latest/hardware/porting/board_porting.html
[bmi]: https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmi270-ds000.pdf
[asahi]: https://asahilinux.org/2021/08/progress-report-august-2021/
[fstm]: https://github.com/scriptingxss/owasp-fstm
[sok]: https://par.nsf.gov/biblio/10217229
[halu]: https://www.usenix.org/system/files/sec20-clements.pdf
[p2im]: https://www.usenix.org/system/files/sec20-feng.pdf
[avatar]: http://www.s3.eurecom.fr/docs/ndss14_zaddach.pdf
[avatar2]: http://www.s3.eurecom.fr/docs/bar18_muench.pdf
[pretender]: https://www.usenix.org/system/files/raid2019-gustafson.pdf
[semu]: https://arxiv.org/abs/2208.07833
[disc]: https://static.usenix.org/event/sec07/tech/full_papers/cui/cui_html/index.html
[poly]: https://www.cs.ucr.edu/~heng/pubs/polyglot-ccs07.pdf
[netzob]: https://media.blackhat.com/ad-12/Guihery/bh-ad-12-reverse-and-simulate-guihery-wp.pdf
[netzob-repo]: https://github.com/netzob/netzob
[netzob14]: https://doi.org/10.1145/2590296.2590346
[angluin]: https://doi.org/10.1016/0890-5401%2887%2990052-6
[vaa]: https://cacm.acm.org/research/model-learning/
[learnlib]: https://doi.org/10.1007/978-3-319-21690-4_32
[learnlib-repo]: https://github.com/LearnLib/learnlib
[rs93]: https://doi.org/10.1006/inco.1993.1021
[tls]: https://www.usenix.org/system/files/conference/usenixsecurity15/sec15-paper-de-ruiter.pdf
[ssh]: http://www.sws.cs.ru.nl/publications/papers/fvaan/SSH/main.pdf
[tcp]: http://www.sws.cs.ru.nl/publications/papers/fvaan/FJV16/main.pdf
[esm]: http://www.sws.cs.ru.nl/publications/papers/fvaan/ESM/main.pdf
[lego]: https://www.usenix.org/system/files/conference/woot14/woot14-chalupar.pdf
[shv]: http://www.sws.cs.ru.nl/publications/papers/fvaan/SHV16/SchutsHoomanVaandrager2016.pdf
[aarts]: http://www.sws.cs.ru.nl/publications/papers/fvaan/AJUV
[tre96]: https://ris.utwente.nl/ws/files/6144406/276_Tre96.pdf
[torx]: https://ftp.cs.ru.nl/CSI/SoftwEng.FunctLang/papers/2003/trej2003-torx.pdf
[x290]: https://www.itu.int/rec/T-REC-X.290-199504-I/en
[ttcn3]: https://www.etsi.org/deliver/etsi_es/201800_201899/20187301/04.16.01_60/es_20187301v041601p.pdf
[ctx]: ../../CONTEXT.md
[design]: ../design.md
[tools]: ../../src/generative_driver/configurator.py
[skill-if]: ../../src/generative_driver/resources/toolchain/skills/interpret-interface/SKILL.md
[skill-bin]: ../../src/generative_driver/resources/toolchain/skills/interpret-firmware-binary/SKILL.md
