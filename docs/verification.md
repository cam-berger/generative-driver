# Verification record

## Initial snapshot — 2026-09-28

On macOS arm64 with Python 3.13.12, the initial wheel passed all 80 tests from outside the checkout. Coverage included distribution archives, real stdio MCP connections, background reconnect/cancellation, checked handoffs, replay, package relocation and scoring contracts. Scripted runtime fixtures make no model-performance claim.

The independent native TQ9 reference calibration passed probe, emission, fresh package reuse, seeded identity refusal and revised-package reuse. A deliberately incorrect decoder was rejected. Selected reference evidence is inside the password-encrypted groundtruth bundle.

## Baseline publication snapshot — 2026-09-28

The baseline publication package passed all **108 installed-wheel tests** on native macOS arm64 with Python 3.11.15 and 3.13.12, from outside the checkout. Plugin and skill validators passed; setup links and distribution archives passed their checks. The physical evaluator callback is implemented and tested with controlled fixtures; no physical sensor has been operated.

The [actual Codex development baseline](../bench/baselines/codex-tq9-2026-09-28.md) completed and passed all seven final gates, including independent emulated observations, fresh package reuse, seeded identity refusal, repair/requalification and second fresh reuse. Its selected report also passed offline regrading. One maintenance interpretation returned raw temperature `2450`; the evaluator rejected it, and one of two allowed model repairs restored correct decoding. All failed attempts remain visible.

The run spans six executing source snapshots. Two acquisition attempts were blocked by approval configuration; a user pause interrupted interpretation. A grounding handoff repair restored supporting evidence while disclosing absent legacy monitor detail. A package permission translation repair followed three calls that failed before protocol execution; a host audit reconciled the conservative uncertainty marker without claiming a successful disarm. The original start was preserved, and a recorded budget extension restored the stopped interval.

A later successful probe exposed seven comparison-tool failures caused by an argument alias. Independent behavioral scoring passed all five checks. The release fixes this separate tool contract and its evidence-path guard, with two failing-then-passing regressions. This last fix was tested after the executing snapshot was frozen; the baseline does not claim to have exercised it. The final executing snapshot passed 106 tests, while the release adds the two regressions for 108.

The selected publication includes source/wheel/skills hashes, per-attempt runtime identity, the revision sequence, independent observations, intervention history and missing-usage coverage. Raw candidate transcripts, private reference values and internal file inventories are excluded. One development run is not a performance study.

## Native portability repair — 2026-09-29

The [first CI run](https://github.com/cam-berger/generative-driver/actions/runs/36502950912) passed both macOS jobs and failed both Windows jobs. Native Windows exposed premature shutdown acknowledgments and file locks, a cancel/resume race, missing-executable classification through the Windows worker wrapper, and an MCP child-process lifetime constraint. A TCP test fixture also needed to stop its serving thread before closing its socket.

The repair confirms daemon exit, drains accepted requests, settles idle workers before resume, preserves startup failures as host blockers, and requires Windows MCP clients to connect to an independently started owner. Stage gateways cannot restart a missing owner. Generated Codex/Goose settings retain the selected state directory, and an MCP benchmark cannot select a different directory from its status/cancel tools. The public regression tests were observed failing before their fixes; the fixture cleanup repair follows its native CI failure.

The repaired wheel passes all **119 installed-wheel tests** on native macOS arm64 with Python 3.11.15 and 3.13.12, from outside the checkout. [Native CI for source `9744292`](https://github.com/cam-berger/generative-driver/actions/runs/36504830025) also passed every test and the separate installation benchmark in all four jobs:

| Native CI host | Python | Installed-wheel tests | Installation benchmark |
|---|---|---|---|
| macOS | 3.11.9 | 119 passed | passed |
| macOS | 3.13.15 | 119 passed | passed |
| Windows Server 2025 | 3.11.9 | 119 passed | passed |
| Windows Server 2025 | 3.13.15 | 119 passed | passed |

CI exercises scripted worker contracts and real local process/MCP connections; it makes no model-performance claim. The Windows jobs verify the native named-pipe, process ownership, cancellation and reconnection paths on Microsoft's Server 2025 runner. These release changes do not alter or rerun the frozen Codex baseline.

### Startup publication follow-up

The [documentation-only repeat](https://github.com/cam-berger/generative-driver/actions/runs/36505100608) exposed one intermittent startup timeout on Windows Python 3.13; its other three jobs passed. Its temporary daemon log was discarded, so the exact cause cannot be established. Review identified a concrete Windows hazard: ordinary readers can prevent replacing the connection metadata while the daemon starts. The fix publishes the endpoint and known child PID once, then releases the child through a private pipe; the daemon no longer replaces that file. Early child exit now reports promptly with the retained log location, and a parent disconnect before publication stops the child. Regression fixtures retain their own failure logs.

The updated wheel passes **122 installed-wheel tests** on native macOS Python 3.11.15 and 3.13.12. [Native CI for source `4c95b15`](https://github.com/cam-berger/generative-driver/actions/runs/36506077842) passed all 122 tests and installation replay in all four macOS/Windows Python 3.11/3.13 jobs, including the held-reader restart on both Windows versions. This closes the follow-up; the baseline execution and measurements remain unchanged.

### Python 3.13 test synchronization

The [CI run for `bfce07f`](https://github.com/cam-berger/generative-driver/actions/runs/36508764721) installed the package successfully but exposed two asynchronous test assumptions in its Python 3.13 jobs. Interpreter and dependency versions matched the earlier passing jobs. The cancellation test read an assignment after a fixed 0.1-second sleep; the repair test shared ten seconds across several healthy stage transitions. Tests now wait for the expected durable assignments, retain bounded deadlines and fail immediately on terminal errors. Both failures were reproduced with deliberately delayed scheduling/workers before checking the corrected waits. See the [test synchronization record](implementation/configurator-tdd.md#test-synchronization). Production package behavior and the frozen benchmark remain unchanged.

## Remaining qualification

Windows 11 workstation UI setup and the native Windows Renode/Ghidra benchmark have not been run. Goose has contract coverage but no live Goose trial. No physical BME280 baseline has been performed. Password encryption protects groundtruth at rest; worker filesystem isolation is unverified.

## Family calibration boundary

New firmware-family admission validates the authenticated exact private inventory, current evaluator/dependency and executed runtime identities, pinned images, two distinct references, diagnostic and frozen-package final coverage, semantic/control maintenance, and every required changed mutant with its exact required failure IDs. The selected native gate is documented in [evaluator evidence](../bench/groundtruth/README.md#authored-family-calibration). Public toy contract tests cannot qualify distributed assets.

Source and installed-wheel tests require no password, native emulator, analysis tool, inference call or physical device. Native reference calibration is a separate measured gate. Native Windows Renode/Ghidra qualification and model-performance pilot execution remain unobserved.

### Historical native family calibration — 2026-10-01

At source revision `aee512e`, native reference calibration passed on macOS 26.5.1 arm64 with Python 3.13.12, cryptography 50.0.1 and mcp 2.2.0. GCC 14.2.1 rebuilt every pinned binary; Renode 1.16.1 measured the episodes; Ghidra 12.1.3 with OpenJDK 21.0.12.1 imported and decompiled every pinned image. Two independently authored references passed eight episodes per case, including emitted-package final execution. Required behavioral mutants were rejected.

| Case | Correct reference episodes | Rejected mutant executions | Native runs |
|---|---:|---:|---:|
| tq9-v2 | 8 | 24 | 32 |
| sampled-sensor-v1 | 8 | 5 | 13 |
| parameter-store-v1 | 8 | 6 | 14 |

At that revision, the new-family gate also passed native malformed reset-vector refusal checks and authenticated admission passed for all seven registered scenarios. Public manifests retain those encrypted historical records and report their measured platform and counts. The subsequent sealed interpretation-kit correction changes evaluator identity: current calibration status is pending, and admission refuses these historical records. One native refresh is deferred to the final integration gate after suite implementation and review. Failed and interrupted development calibrations were retained; none supplied the published qualification. The original TQ9 and September 28 baseline identities remain unchanged. These are reference-calibration results, with no model-performance or physical-grounding claim. Native Windows qualification remains unobserved.
