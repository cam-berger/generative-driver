# Noticed during the stm32-acquire Run

- 01: `src/generative_driver/_toolchain/acquire.py:343` catches an exception inside the read and returns it as a failed read. When the tool runs for the service, such an exception no longer leaves a traceback in `daemon.log`; its type and message are in the result and the run's events. A call to `service_log.caught` in that catch would keep the traceback. The Reviewer raised it as a note, not a finding.
- 01: `src/generative_driver/_toolchain/core.py:128-144`: the tool folder's event log records every argument of a call. For the new tool these are the program's path and the probe's serial number, in a place a worker can read.
- 01: `plugins/generative-driver/skills/generative-driver/SKILL.md:9` says "Grant `reset` only for devices that restart on port open". The new sentence beside it gives the grant a second use, for a read through a debug probe. The older sentence is the owner's to word.
- 01: `src/generative_driver/_toolchain/acquire.py:31`: `acquire_firmware_artifact` tests `os.path.isabs`, which on Windows before Python 3.13 takes a path without its drive as absolute. The new tool uses `Path.is_absolute`, which does not.
- 01: `src/generative_driver/setup.py:42`: `configure` reads `config.json` with `read_text()` and no named encoding.
- 01: `src/generative_driver/setup.py:51`, `:102` and `:260` are three copies of the same atomic JSON write.
- 01: `src/generative_driver/setup.py:458`: `doctor` starts `uname -p` through `platform.platform()`.
- 01: `src/generative_driver/configurator.py:1601`: a run whose budget runs out during a stage has that stage accepted first and is blocked before the next, so a resume does not repeat the stage.
- 01: `tests/test_stm32_flash_tool.py:479-480`: fix round 1 removed one of the two blank lines before `def sha256`.
- 01: This Run's Feature 01 repeats the number of the untangle Run's Feature 01, whose tests named `test_01_B…` remain. The kit's check cannot tell the two apart, so this Feature's tests are named `test_01_B<n>_stm32_…` and the Reviewer counted them: 52 tests, every Behaviour 01-B1 to 01-B17 covered.
- 01: STM32CubeProgrammer is not installed on the machine that built this, and no probe or chip was used. The program's output texts the tool reads (`Flash size`, `RDP`, the escape sequences) come from ST's command-line page for version 2.23.0 and from the stand-in. A first read of a real chip is the operator's to run and may show a text the tool does not expect.
- 01: hosted runs of the first build, `44fb004`: "Installed package" passed on macOS and Windows with Python 3.11 and 3.13. In "Firmware bench" GitHub cancelled the macOS 3.11 job after 15 minutes, before any step started.
