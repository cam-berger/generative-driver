# New-device trial findings (2026-10-06)

Goal under test: plug in a device, open Goose or Codex, run the generated recipe with a short instruction, and reach an emitted driver that a fresh agent can use. Device: ESP32-S3-DevKitC-1 (N8R8) behind a CP2102N USB-UART bridge, firmware `bme_oled_node` (BME280 on I2C, SH1106 OLED on SPI). The trial did not reach that goal. Each gap below names the observed failure and the change it implies.

## Fixed during the trial (uncommitted in this worktree and the main checkout)

1. **Goose workers launched with no tools.** `agents.py` passed `goose run --no-profile`. In Goose 1.53.0 that flag drops recipe extensions, so stage workers had neither `developer` nor the `stage` gateway; only `final_output` was callable. Removed the flag; recipe extensions already replace the profile's extension selection (checked live: profile extensions were not inherited, `available_tools` filtering held).
2. **Acquire accepted a worker-chosen serial port.** The gateway injected the binding only for probe/execute tools. `acquire_classify` took any `port` argument, bypassing the leased binding. The gateway now injects the bound serial port and rejects a worker `port`.
3. **Operator observations never reached generic workers.** `respond` recorded them, but `_prepare` passed them only to benchmark cases. Generic stage context now carries `operator_observations` and a note that a binding exists (without the connection).

## Open gaps, in the order a new user hits them

4. **Goose thinking configuration breaks current Claude models.** `GOOSE_THINKING_EFFORT` makes Goose 1.53.0 send `thinking.type.enabled`, which `claude-sonnet-5-5` rejects (400). Setup/doctor should detect the provider/model and warn, or the generated recipe should pin a working setting. Workaround used: `GOOSE_THINKING_EFFORT: none`.
5. **`configure` accepted placeholder values.** The Goose executor was saved as `YOUR_MODEL` / `YOUR_PROVIDER` from the docs example. Reject obvious placeholders, or have `doctor` fail them.
6. **No passive serial observation in acquire.** With only a serial port, acquire refuses unless the operator names the ESP32 backend. A plug-in-and-go flow needs a read-only listen (bounded duration/baud, DTR/RTS held) that records boot banners. Here that single read identified `ESP32-S3`, the boot mode and the app name.
7. **ESP32 acquisition requires the chip already in download mode.** `acquire_flash` uses `--before no-reset`, so a board running firmware answers "no controller answered". The CP2102N auto-reset (`--before default-reset`) works on this board and is how the dump finally succeeded. Offer it as an explicit, recorded effect (`reset`) the operator can grant, and report the mode the chip was in.
8. **Stub left at 921600 after a dump.** After `read-flash ... --after no-reset` the stub keeps listening at the dump baud, and later default-baud connections fail with "No serial data received". The tool should leave the chip in a known state or record the baud.
9. **Port names change on replug.** `/dev/cu.usbserial-11120` became `-11140`. A path binding goes stale; bind by USB identity (VID 0x10C4, PID 0xEA60, serial `4c1afd94…`) and resolve the path at dispatch.
10. **Generic runs cannot pass interpret or ground.** Interpret requires exactly one acquired binary (an ESP32 dump yields flash/app/elf) and has no Xtensa path. Ground is hard-blocked for non-benchmark runs ("requires an independent observation/evaluator adapter"). Until an operator-observation ground adapter exists, no plugged-in device can reach emit or reuse.
11. **Agents cannot list runs.** The front-end had to ask for a run ID. A `driver_runs` listing would make reconnection work from a short instruction.
12. **Main checkout drift.** The editable install points at the main checkout, which was on an older branch without the dashboard. `doctor` should report the installed source revision.
13. **Worker report hash.** The acquire worker excluded `report.json` because it hashed it before the final write; artifact hashing should happen after the report is finalized.

14. **Opening the port reboots the board.** On macOS, opening the CP2102N pulses EN even with DTR/RTS released (`rst:0x1 (POWERON)`). Any probe, emitted driver or reuse agent must expect a boot on connect and wait for it; commands sent during boot are lost.
15. **A dead peripheral looks like a dead board.** The original firmware treats a missing BME280 as fatal and reboots every few hundred milliseconds. To the operator "nothing happens". Acquire/probe should capture boot output and report a crash loop, with its abort site, as a device-health finding before attempting interpretation.
16. **No path when the firmware has no command interface.** This firmware only logs readings; nothing on the wire can draw text. Reaching the requested task needed new firmware (build, flash after backup), which no stage offers. A plug-in flow needs either an explicit `flash` effect with mandatory backup and restore, or a clear report that the requested functions are not reachable through the existing firmware.

## Path actually taken to reach the task

Outside the configurator, from Claude Code, with operator authorization for read-only access and flashing after backup: passive listen → `esptool flash-id`/`espefuse summary` → full flash backup → boot-log capture (found the crash loop) → firmware source located in the research checkout → new firmware with a line protocol (`PING`, `READ`, `LINE`, `BANNER`, `AUTO`, `CLEAR`) that survives a missing sensor → host driver `banner_driver.py` → task script. None of this ran through stages, assignments or evaluator verdicts, so it is a scripted operator path, not a Generative Driver result. Files: `~/Research/esp32s3-banner-node/` (kept outside both repositories because it derives from a benchmark answer key).

## Device facts established (physical observation)

- Chip: ESP32-S3 (QFN56) rev v0.2, 8 MB flash, 8 MB PSRAM, MAC 80:45:6b:2d:2f:6c. Flash encryption and secure boot off.
- Original flash backed up outside the repository (SHA-256 `81594fc9…dae0c5`).
- Firmware boots, configures the OLED, then aborts at `main.c:253` because the BME280 does not acknowledge at 0x76 or 0x77, and reboots in a loop. The firmware treats a missing sensor as fatal.
- Pin map came from the firmware's own source in the research checkout, which is a benchmark answer key. Anything built from it is not blind evidence.
