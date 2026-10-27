# Native Modbus pilot

Question: can the existing Zephyr Modbus mission run on authentic host-compiled
firmware through the normal benchmark lifecycle?

The source is the same Zephyr v4.2.0 commit used by the ARM case,
`413b789deb391d3a37d06b463288a5fe765ee57e`. The application, RTU parser, CRC and
state handlers stay unchanged. The native overlay uses Zephyr's interrupt-capable
native TTY driver connected to a host-created PTY, and GPIO emulation. The native
PTY UART driver lacks the interrupt interface this RTU implementation needs.

The supplied artifact is the final native executable `zephyr.exe`, retained as
`firmware.elf`; the intermediate Zephyr `zephyr.elf` is not the host executable.
Debug-path normalization makes both clean executable builds identical. Independent
register observations use the executable's symbols; the GPIO output offset comes
from its DWARF type layout. Neither observation trusts a protocol readback.
The agent receives those same ELF bytes, including symbols and DWARF. Recovery
difficulty differs from the ARM raw image; this is not a controlled comparison of
agent performance with only the execution backend changed.

Case snapshots copy bytes without preserving execute permissions. The native
adapter stages an executable private copy and verifies both copies against the
pinned hash before launch and reset; it leaves the supplied artifact unchanged.

The optional backend runs on Linux, including a Linux VM on macOS/Windows. It
pauses its owned process between calls. With `--rt`, the host clock continues and
native simulated time catches up on resume. Reports name this policy
`host-clock-with-process-pauses`; thinking time is not excluded from clock pacing.
No UART/RS-485 electrical or timing equivalence is claimed.

## Reproduce

Run these commands in a writable evaluator directory with an installed package,
the clean pinned Zephyr source, host GCC, west and Zephyr build dependencies.
`pyelftools` is needed only when authoring the native observation recipe. Password
files belong to the evaluator; workers receive neither their paths nor contents.

```sh
python -m generative_driver benchmark corpus build --case zephyr-modbus-v1 \
  --backend native-sim --source /path/to/zephyr --compiler /path/to/gcc \
  --west /path/to/west --output native-build

python -m generative_driver benchmark corpus prepare-execution --build native-build \
  --password-file /path/to/evaluator.password --output native-intake

GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-intake" \
  python -m generative_driver benchmark corpus exercise-execution --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --output native-observations

python -m generative_driver benchmark corpus exercise-execution --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --renode /path/to/renode --output renode-observations

python -m generative_driver benchmark corpus compare-execution \
  native-observations/observations.json renode-observations/observations.json --output comparison.json

python -m generative_driver benchmark corpus prepare-execution --build native-build \
  --password-file /path/to/evaluator.password --native-record native-observations/observations.json \
  --reference-record renode-observations/observations.json --output native-qualified-inputs

GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-qualified-inputs" \
  python -m generative_driver benchmark truth calibrate --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --ghidra-home /path/to/ghidra \
  --java-home /path/to/jdk --output native-calibration
```

Stop if comparison or calibration fails. Native calibration requires the measured
comparison records, checks the backend and executed image, runs two distinct
reference drivers through diagnostic/final episodes, and rejects the declared
mutants. Pending metadata cannot admit an agent.

After successful calibration, copy its `case.json` to
`native-qualified-inputs/cases/zephyr-modbus-v1/case.json` and its
`zephyr-modbus-v1.enc` to
`native-qualified-inputs/groundtruth/zephyr-modbus-v1.enc`. These are external
resources; installed resources and previous results stay unchanged.

Use the existing runtime configuration and trial command with this resource root:

Install the complete matching Codex distribution, including its tool-host helper
and Bubblewrap resource. The Linux environment must support the worker sandbox's
namespaces and mounts. Verify these before spending model inference on a trial.

```sh
GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-qualified-inputs" \
  python -m generative_driver benchmark run --case zephyr-modbus-v1 --executor codex \
  --password-file /path/to/evaluator.password --home "$PWD/native-state" \
  --output "$PWD/native-trial" --ghidra-home /path/to/ghidra --java-home /path/to/jdk \
  --approve-emulator-tools --wait
```

The case keeps the same lineage and mission. `execution_setup.method=native-sim`
and the native ELF hash distinguish its execution from the ARM variant. The
generic software-emulation execution category alone does not identify a backend.
All six handoffs, a fresh package-only mission and the final evaluator verdict
remain separately reviewable. One trial establishes feasibility for that trial;
it does not estimate a success rate across firmware families.

Primary implementation references: [native simulator](https://docs.zephyrproject.org/4.2.0/boards/native/native_sim/doc/index.html),
[pinned native TTY driver](https://github.com/zephyrproject-rtos/zephyr/blob/413b789deb391d3a37d06b463288a5fe765ee57e/drivers/serial/uart_native_tty.c),
[pinned RTU application](https://github.com/zephyrproject-rtos/zephyr/blob/413b789deb391d3a37d06b463288a5fe765ee57e/samples/subsys/modbus/rtu_server/src/main.c).

## Measured pilot

The host build works. Two clean AArch64 builds produced the identical executable
`d7b0d96dfbd0c2b42b50339a9abf75752899d775762b5ef37e6c82dd425e927c`.
The 20 native/Renode episodes passed all 568 comparison checks, including exact
replies and independently observed complete register/coil state after every step.
Fresh native qualification passed all four 315-check reference runs (two distinct
drivers, diagnostic and final) and rejected all six named mutants. These are
authentic firmware executions with scripted requests, not agent performance.

The completed `gpt-6.1-sol` trial ran all six stages, including a fresh package-only
mission. It used one interpretation repair after an invalid-register probe timed
out: eight worker attempts, 489.95 worker seconds and 1,600,910 reported tokens
(1,295,616 cached input tokens, a subset). The frozen driver passed **315/315**
independent final checks and the fresh worker's mission. An offline regrade of
the sealed observations confirmed the verdict.

This was an engineering feasibility run with setup repairs. Two earlier attempts
emitted no driver because Docker denied Bubblewrap namespaces and then because
the replacement runtime lacked its matching tool-host executable. Both retain
their failures and usage. The completed trial initially remained unscored because
its copied ELF lacked execute permission. Restoring that bit left its bytes and
hash unchanged; a grading-only resume passed without repeating any agent stage
or changing its original evaluator. Its reported 837.42 seconds includes that
repair interval. The separate permanent adapter fix passed a failing-then-passing
Linux process regression, fresh 568-check comparison, reference/mutant calibration
and registry admission with a read-only, non-executable source ELF. Historical
trial and permanent-fix package identities are recorded separately. The final
installed-wheel suite passed 928 tests with three optional skips; the Linux
backend suite passed 11 tests with one non-Linux guard skipped.

The unprivileged container had no host mounts or Linux capabilities. Its local
sandbox smoke check allowed workspace writes and denied an outside write; this
does not establish worker read isolation. Device authentication used normal
sign-in and the dedicated session was logged out after the trial.

The [verification index](../implementation/native-modbus-verification.json) pins
the package and measurements. Raw private evidence is retained separately under
`bench/runs/native-modbus-20261027`; previous build attempts, setup failures and
the pre-repair unscored seal are preserved. No physical UART/RS-485, electrical
fault, timing-equivalence or cross-family result follows from this pilot.
