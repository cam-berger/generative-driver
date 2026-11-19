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
