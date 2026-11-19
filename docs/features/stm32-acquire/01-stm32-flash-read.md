# 01. stm32-flash-read

## Intent

An operator who starts a run on an STM32 device often has no firmware file. Today the acquire stage reads a chip only when it is an ESP32 (`acquire_flash`, through esptool). This Feature adds the same for an STM32: the operator names an ST-LINK probe with the run's device, and the acquire worker reads the chip's flash through STM32CubeProgrammer's command-line program, `STM32_Programmer_CLI`, and hands the image to interpret.

Three things matter more than convenience:

- The tool only reads. No call can write, erase, or remove read protection. Removing read protection erases the chip.
- The operator owns the connection. The program's path, the probe and the permission to reset come from the configurator, never from a worker.
- A run that declares no probe behaves exactly as before. So do supplied runs, and so does the benchmark.

STM32CubeProgrammer is the operator's own installation. It is not on the machine this Feature is built on, so every test uses a stand-in program, and the Feature gives no physical evidence. The command line is taken from ST's page for version 2.23.0 (`dev.st.com/stm32cube-docs/prog/2.23.0`, "MCU command lines"). That page shows the program's output only as figures, so the stand-in prints what the program is remembered to print, and the tool reads as little of that output as it can.

## Terms

- **The program**: `STM32_Programmer_CLI`, started as an argument list, never through a shell.
- **Declared probe**: the key `debug_probe` in a run's `binding`: `{"kind": "stlink"}`, with an optional `"serial_number"`.
- **Reset grant**: `reset` among the run's `effects`.
- **Launch**: one start of the program. The tool makes at most three in a call.

## Behaviours

- **01-B1** — A new command, the sixteenth of `generative-driver`: `generative-driver [--home DIR] configure-program --program stm32-programmer --command PATH [--argument ARG]...` saves the program's command in `config.json` as `programs["stm32-programmer"] = {"command": [PATH, ARG...]}`. It leaves `executors` as it was, and `configure` leaves `programs` as it was. It refuses a `PATH` that is not absolute, a `PATH` that ends in `.bat` or `.cmd` in any letter case, and an empty `ARG`. It starts nothing and does not ask whether the file exists.
- **01-B2** — `doctor` reports `dependencies.stm32_programmer` with `configured`, `path` (the configured `PATH`, else null), `available` (configured, and the file exists), `source: "configuration"` and `required_for: "reading STM32 firmware through an ST-LINK probe"`. It never starts the program.
- **01-B3** — A start whose binding declares a probe is accepted when the declaration is well formed, the program is configured and its file exists. Otherwise the start is refused before a run exists, with one of these texts: `binding.debug_probe must be an object with kind "stlink" and an optional serial_number` (not an object, a key other than `kind` and `serial_number`, or another `kind`); `binding.debug_probe.serial_number must be 1 to 64 letters and digits`; `binding.debug_probe needs STM32CubeProgrammer: run generative-driver configure-program --program stm32-programmer --command PATH` (not configured, or the file is missing). A start that holds both a supplied device and `debug_probe` is refused before those three checks, with `binding.debug_probe is for a run without a supplied device`.
- **01-B4** — The run keeps the program's command it started with: a `configure-program` after the start does not change what that run launches.
- **01-B5** — At start the configurator takes `debug_probe` out of the binding and keeps it beside the binding in the run's record. Everything after sees the binding without it: the binding's lease is the one it has without a probe; `acquire_serial_listen` and a model's device call on a serial binding work as they do without one; and a run started with `scoped_tool_approval: "bound-device"` passes its stages as it does without one. The runtime does not change. A device session does not change either: it refuses `debug_probe` as it refuses any binding key it does not know.
- **01-B6** — Runs that declare a probe share one lease. While one such run is queued or running, the start of a second is refused with `The debug probe is already owned by another run`. When the first has ended, the second starts. A run without a declared probe neither takes this lease nor is refused by it.
- **01-B7** — The acquire stage of a run with a declared probe is assigned `acquire_stm32_flash` after the tools it had, and its context gains one entry, `debug_probe`, with the text given below. For a run without a declared probe the assigned tools and the context are what they were before this Feature. A supplied run's acquire stage is unchanged.
- **01-B8** — Through the gateway the worker's schema of the tool shows one argument, `size_kb`. The configurator supplies `program`, `probe_serial_number` and `allow_reset`. A call in which the worker sends `run_dir` or `allow_reset` is refused with the existing text `Connection, output directory and effect grants are configurator-owned`; one that sends `program` or `probe_serial_number` is refused with `The debug probe and its program are configurator-owned`.
- **01-B9** — Without the reset grant the tool makes two launches, each beginning `-c port=SWD mode=HOTPLUG`, followed by `sn=<serial>` when the probe names one. The first ends `-ob displ`. The second ends `--upload 0x08000000 <bytes> <file>`, with the byte count in decimal. Neither holds `-rst`. On success the result has `available: true`, the image as `acquire/interpret-input.bin`, `connection: "hotplug"`, and no key `returned_to_application`.
- **01-B10** — With the reset grant both launches begin `-c port=SWD mode=NORMAL reset=SWrst` (then `sn=<serial>` as above), and the read launch ends with `-rst`. On success the result has `connection: "normal"` and `returned_to_application: true`. When a call that the tool itself ends, ends without a successful read after at least one launch, the tool makes one more launch, `-c port=SWD mode=HOTPLUG [sn=<serial>] -rst`, and `returned_to_application` is whether that launch exited 0. A call the configurator stops (a cancel, a shutdown, a spent budget, the limit of a tool call) makes no such launch, as `acquire_flash` makes none today; under the reset grant the chip can then stay halted until it is reset.
- **01-B11** — Over every scenario of this Feature's tests, the arguments of each launch after the program's own command are drawn from this list only: `-c`, `port=SWD`, `mode=HOTPLUG`, `mode=NORMAL`, `reset=SWrst`, `sn=<serial>`, `-ob`, `displ`, `--upload`, `0x08000000`, a decimal byte count, the output file's path, `-rst`. Nothing a worker sends reaches a launch but as that byte count.
- **01-B12** — The tool returns `available: false` with a `reason`, and creates no image, in each of these cases; a file that was already there is left untouched. Nothing is launched in the first three.
  - `acquire/` already holds a file named `interpret-input` with any extension: `An acquired image already exists; use a fresh run`.
  - `size_kb` is given and is not a whole number from 1 to 16384: `size_kb must be a whole number from 1 to 16384`.
  - `program` is not a list of nonempty strings whose first names an existing file, or `probe_serial_number` is not 1 to 64 letters and digits: `The debug probe or its program is not configured`.
  - The first launch exits nonzero: `no controller answered through the debug probe`.
  - The option bytes show read protection other than level 0: `readout protection is set; class 1 unavailable`, with exit code 0 as `acquire_flash` gives for a locked ESP32. No read is launched.
  - The output names no flash size and `size_kb` was not given: `The program did not report the flash size; pass size_kb`.
  - The read launch exits nonzero, or its file is missing, or the file's size is not the size asked for: `dump failed`, with which of the three.
  - Every byte of the image is equal: `the dump is blank`.
  - A launch runs past its limit, 60 s for the first, 300 s for the read and 30 s for the return: the launch is stopped and counts as one that exited nonzero. The limits are three module constants of `_toolchain/acquire.py`, `STM32_IDENTIFY_SECONDS`, `STM32_READ_SECONDS` and `STM32_RETURN_SECONDS`, which the tests of this case patch.
- **01-B13** — The size read is `size_kb` times 1024 when the worker gives it, else the size on the program's `Flash size` line. Escape sequences in the output are removed before any line is read. When the output has no `RDP` line the tool records `readout_protection: "unknown"` and reads.
- **01-B14** — On success the tool writes, under the run's tool folder, the image, the two launches' texts and a provenance record. The tool's event in `events.jsonl` of that folder lists each of the four under `files` with its SHA-256. The files and the record's fields are those listed under "The tool" below.
- **01-B15** — In a whole run with scripted workers, the real tool and the stand-in program, the acquire handoff carries the image with the hash the gateway observed, and interpret receives exactly that image as its one firmware input.
- **01-B16** — The documents say how to use it. `docs/setup.md`: the program is installed separately, `configure-program`, a binding with `debug_probe`, what the reset grant changes, that a read the configurator stops under the reset grant can leave the chip halted until it is reset, that one such run runs at a time, that a read-protected chip is not read, and that on many STM32 families attaching a probe to a read-protected chip stops its application until the next power cycle, which this Feature has not checked on a chip. `docs/support.md`: the firmware row names the STM32 read and says it has scripted evidence only. The skill's step on binding gains one sentence. Both copies of the skill stay identical.
- **01-B17** — What existed keeps its shape: the fifteen commands `generative-driver` had, each with its arguments, and `configure-program` stands directly after `configure`; the client's tools with their arguments and descriptions; the hash of `docs/client-interface.md`; the description of every tool that existed; and the extras `doctor` lists.

## Interface and seams

### Configuration (CLI seam)

`--program` takes the one value `stm32-programmer`. Tests attach at `generative_driver.cli.main` and `generative_driver.setup.doctor`, with a temporary home.

### The declared probe (configurator seam)

- The run's record holds the probe under its own key, beside `binding`, and the binding it holds has no `debug_probe`. So `binding_identity`, the runtime's check of binding fields and the comparison behind `Prepared stage changed the explicitly approved device binding` all see what they see today.
- The probe's lease is a second row of the table that holds binding leases, under one fixed identity, claimed where the binding's lease is claimed (start, resume and each stage assignment) and released with the run's other rows.
- The command is copied into the run's record at start, as `executor_config` is.
- The context entry, as given text:

> An ST-LINK debug probe is attached to this device. If the device is an STM32 and no firmware file is among the inputs, read its firmware with acquire_stm32_flash. The configurator supplies the program and the probe. Without the reset grant the tool neither resets nor halts the chip; with it the chip is reset to connect and again after the read.

### The tool (toolkit seam)

`acquire_stm32_flash(run_dir, program, probe_serial_number=None, allow_reset=False, size_kb=None)` in `src/generative_driver/_toolchain/acquire.py`, listed in `toolkit._MODULE_TOOLS` and described in `resources/toolchain/tools.json`. It is offered to the worker directly, not through `acquire_classify`, whose ladder and texts do not change.

Files, all under the run's tool folder:

- `acquire/interpret-input.bin`: the image, exactly the bytes read. The read goes to `acquire/stm32/flash.bin` first and is moved here only when every check has passed; on a failure that file is removed.
- `acquire/stm32/identify.txt` and `acquire/stm32/read.txt`: each launch's standard output, then its standard error, with escape sequences removed. They are kept on a failure, and the result names them.
- `acquire/stm32/provenance.json`, with exactly these fields: `schema` (`firmware-readout/1`), `origin` (`device_dump`), `origin_basis` (`debug_probe_read`), `program` (the file name of the command's first element), `probe` (`stlink`), `probe_serial_number` (or null), `connection`, `reset_granted`, `start_address` (`0x08000000`), `size`, `sha256`, `device_name` (or null), `readout_protection` (`level 0` or `unknown`), `flash_performed` (false), `bit_identical_to_device` (`not_verified`).

The result on success, as `toolkit.call_tool` returns it: `available`, `rung` (`flash`), `chip` (the device name, or null), `artifact` and `interpret_input` (the image), `artifacts` (the four files), `provenance`, `sha256`, `size`, `connection`, `readout_protection`, `front_end` (`interpret-firmware-binary`), `inputs_for_interpret` (`{"image.bin": <the image>}`), and under the reset grant `returned_to_application`.

Lines read from the output, after escape sequences are removed, without regard to letter case, the first match winning:

- `RDP`, then `:`, then `0x` and two hex digits. `AA` is level 0; any other value is protected.
- `Flash size`, then `:`, then a whole number, then `KBytes` or `MBytes`.
- `Device name`, then `:`, then the rest of the line.

Each launch is an argument list with no shell. Its working folder is `acquire/stm32/`. Its output is decoded as UTF-8 with replacement. The tool never repeats a launch.

### The stand-in program

`tests/stm32_programmer_stand_in.py`, configured as `[sys.executable, <that file>]`, so it runs the same on macOS and Windows. It appends each launch's arguments to a record file, prints a connection text with escape sequences and an option-bytes text, and writes the upload file. A scenario file beside the record steers it: the RDP value, whether the flash-size and RDP lines are printed, each launch's exit code, the bytes written, a short file, a blank file, a launch that outlasts its limit.

### Tests

This Run's Feature is numbered 01, and so was a Feature of the untangle Run, whose tests named `test_01_B…` remain. Every test of this Feature is therefore named `test_01_B<n>_stm32_…` and lives in a new file `tests/test_stm32_*.py`. Each Behaviour has at least one such test. The Reviewer counts those, not the older ones.

By seam: 01-B1 and 01-B2 at the CLI; 01-B3 to 01-B8 and 01-B15 at the configurator, with the scripted workers and stand-in serial device of `tests/input_path_fixtures.py`; 01-B9 to 01-B14 by `toolkit.call_tool` with the stand-in program; 01-B16 and 01-B17 in the style of `tests/test_tool_documents.py` and `tests/test_tool_commands.py`.

Existing tests that change, and only these:

- `tests/test_doctor.py:193` and `tests/test_tool_alone.py:118` (11-B2) pin the dependency names as `['codex', 'java', 'podman']`. Each becomes `['codex', 'java', 'podman', 'stm32_programmer']`.
- `tests/test_tool_commands.py:141` (09-B6) pins the commands as fifteen, with the usage line. `COMMANDS` gains `configure-program` directly after `configure`, the count becomes 16, and the fifteen entries stay as they are.
- `tests/test_input_paths.py`, 07-B9: the filter that leaves out `acquire_document` also leaves out `acquire_stm32_flash`. The pinned hash stays.
- `tests/test_tool_documents.py`: `SKILL_SHA256`, for the one new sentence.

A test outside this list that fails because it holds the whole tool catalogue or the acquire stage's tool list is changed only by adding the new name, and the report names it.

### What the Reviewer checks beyond the tests

- No file under `firmware-bench/` changed.
- No tracked file outside `firmware-bench/` and `docs/features/` gained the benchmark's name.
- The words the document scan of `tests/test_tool_documents.py` bans are absent from the new document text.

### How the work goes

One Behaviour at a time, each test seen to fail first. Then the whole test command of `STANDARDS.md`. The Build's state is then pushed to this Run's branch on GitHub, so that the hosted workflows run it on macOS and Windows; nothing else is pushed, and no pull request is opened.

## Out of scope

- The serial and USB bootloader connections of the program, JTAG, J-Link, and any chip whose flash is not at `0x08000000`.
- Connecting under reset (`mode=UR`), a second read to compare, and trimming the image.
- Listing probes, finding the program on the search path or in default folders, asking it for its version, and starting it from `doctor`.
- A way to remove a configured program.
- Any change to `acquire_classify`, `acquire_flash`, the supplied stages, the runtime, the fifteen existing commands, the client's tools or the benchmark.
- A read of a real chip. That is a physical operation, and the operator's to run.

## Deviations

- `configure-program` refuses as an argparse error, exit status 2, with the text `generative-driver configure-program: error: <reason>` — the Spec says "refuses" without a form, and `configure` lets a `ValueError` through as a traceback.
- The three refusal texts of 01-B1 are the implementer's: `The command of a program must be an absolute path: <path>`, `The command of a program cannot be a batch file, which only a shell starts: <path>`, `An argument of a program cannot be empty: argument <n>` — the Spec gives none.
- The tool also requires the program's first word to be an absolute path, with the same check the configuration uses — a relative name would resolve against the launch folder `acquire/stm32/`.
- A reported flash size of 0 counts as not reported — the Spec does not cover it, and a read of no bytes is no read.
- `dump failed` carries `cause`, one of `the read launch exited nonzero`, `the read launch left no file`, `the file is not the size asked for` — the Spec says "with which of the three", not how.
- On a failure the kept texts are named under `launch_texts` and hashed under the event's `files` — the Spec says the result names them, not the key.
- An RDP line needs exactly two hex digits after `0x`; anything else is `unknown` and the tool reads — the reading of "0x and two hex digits" that 01-B13 allows.
- A call that an exception ends after the tool's checks returns `available: false`, `reason: "the read could not be finished"`, `cause: "<type>: <message>"` and exit code 1, with `launch_texts` for the texts written — 01-B12 lists no such case; it is the shape every other failed read has, and it lets the result carry `returned_to_application`.
- Only a launch whose program started counts as a launch: a first launch the system cannot start is followed by no return launch and no `returned_to_application` — the Spec's Terms call a launch "one start of the program", and no chip was touched.
- A return launch that cannot be made gives `returned_to_application: false`, and its exception is not kept — 01-B10 defines the key as whether that launch exited 0.
- An exception inside the read is not written to the service log; its type and message are in the result, the tool's event and the run's `tool.finished` event — no rule asks for the log entry. The traceback is lost; see `noticed.md`.
- A failed call also removes `acquire/stm32/provenance.json`; a `flash.bin` or record that cannot be removed is left — 01-B14 writes the record on success, and a record without an image describes nothing.
- The hash of each kept text is taken from the bytes written, not by reading the file back — the same value, and nothing on the failure path reads a file.
- When an exception comes after a read launch that already ended with a reset, the return launch resets the chip once more — 01-B10 asks for the return launch on every tool-ended failure after a launch, and the operator granted the reset.
- A launch past its limit is ended with `process.kill()`, the program alone, not processes it may have started — the Spec does not say how.
- The stand-in finds its record folder through the environment variable `STM32_STAND_IN_STATE` — the Spec does not say how.
- The stand-in's scenario `in_the_way` leaves a folder where the image is to go, so the stand-in knows `acquire/interpret-input.bin` and `acquire/stm32/flash.bin` — both are paths the Spec names, and it is the portable way found to make the move fail.
- One 01-B10 test replaces `subprocess.Popen` with one that refuses chosen commands — a mock at the system boundary, the portable way found to make a launch unstartable.
- The record's keys are `debug_probe` and `debug_probe_program`; a request's top-level keys of those names are dropped when the binding declares no probe — only a binding declares a probe.
- The program's command is read from the configuration twice at start, once to refuse and once to copy — the smallest change; a configuration changed in that window is the one the run keeps.
- The probe's lease identity is the fixed text `debug-probe`, its refusal is a `SafetyError` as the binding's is, and `DeviceAccess.claim_binding` became a shared `_claim` plus `claim_probe` — the binding's own claim does what it did.
- The lease claim at stage assignment (`configurator.py:1668`) has no test of its own — start and resume are tested, and no test was found that fails without that one line.
- A binding that holds only a probe, started with `scoped_tool_approval: "bound-device"`, is refused with the existing text on an explicit operator binding — a consequence of 01-B5: such a run has no device binding.
- The device-session clause of 01-B5 is asserted through the runtime's `validate_binding`, not by opening a session — the refusal is made there.
- The context entry `debug_probe` stands after `serial`, and the 01-B7 test pins the literal texts of the plain acquire context — to hold "what they were before this Feature".
- The gateway's new refusal applies to this tool only, by name, as the USB one does; `allow_reset` is whether `reset` is among the assignment's effects — the Spec gives the refusal's text, not its reach.
- In `tools.json` the description and the schema (`size_kb`, minimum 1, maximum 16384) are the implementer's wording — the Spec gives the argument and its range.
- The tool repeats the expression `Path(...).is_absolute()` instead of sharing a function with the configuration — the tool module does not import the configuration module.
- 01-B15 needed no new code; its test was shown to fail by removing the image from the tool's result, then putting it back — the whole run already carried the image once 01-B7 to 01-B14 stood.
- The extra 01-B10 test that a call the configurator stops makes no return launch passed when first run and has no red — it describes what the tool's process already does.
- The resume test of 01-B6 was rewritten after its code, and shown to fail by removing the lease claim — its first form failed about once in eight runs, because the resumed run could end before the assertion.
- The test of a failed move got its red by removing the record's clean-up line, which had been written with the main fix — the excerpt fails for the test's own reason.
- The 01-B17 test was written before the code of 01-B1, and one 01-B17 test stayed red until 01-B8 listed the tool — it holds what stays, across the Feature.
- The check that the documented command and binding are ones the tool takes (01-B16) goes through `Controller.start` — the configurator seam, after the review asked for it.
- The two limit tests of 01-B12 measure a plain read on the machine and patch the limit to five times that, 3 s at least — a fixed 3 s could fail on a slow hosted runner.
- Two tests gain a case for a path without its drive, on Windows only — there `Path.is_absolute` refuses such a path and the case adds nothing elsewhere.
- The 09-B6 test keeps its name, which says "fifteen", with a comment and the count 16 — the Spec changes the count, and a rename would lose the test's history.
- `docs/setup.md` says two things beyond the list of 01-B16: a run keeps the command it started with, and a run on a supplied device takes no probe. The skill's sentence stands before "Smoke uses no inference." — both are Behaviours of this Spec that an operator meets.
- In round 0 two test-only edits followed the whole test command, and in fix round 1 that command was held while a graded measurement ran on the machine; the hosted workflows ran each state, and the command ran again before the Feature was marked done — one machine could not do both at once.
