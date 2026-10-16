# Device connection for plugged-in serial devices

## Intent

A developer plugs in a device, opens Goose or Codex, runs the generated recipe with a short instruction, and the generic workflow reaches accepted fresh reuse. The device alone is the input: no protocol document or firmware source. The reference device is an ESP32-S3-DevKitC-1 behind a CP2102N bridge, running `banner_node` firmware with a BME280 and an SH1106 display. Results from it are physical, operator-observed evidence, never benchmark scores. Candidate workers never receive the firmware source or the host driver written for it.

This is sub-project 1 of six, each with its own spec, plan and physical check: device connection, blind ESP32 interpretation, model expressiveness, generic operator-observation ground, objective-driven reuse, and Goose/Codex onboarding. This spec covers device connection only. The gaps it closes are recorded in [new-device-trial findings](../../implementation/new-device-trial-findings.md) items 6–9 and 14.

## Binding

A UART binding is either `{"port": PATH}` or `{"vendor_id": INT, "product_id": INT, "serial_number": STR}` with `serial_number` optional. The runtime resolves an identity binding to the current port path from the operating system's port list each time it connects. No match, or several matches without a serial number, fails with code `binding` as an operator fault. The configurator leases an identity binding product-wide by vendor and product, as it already does for USB bindings, so one board cannot be reached by two runs through different paths or through selectors with and without a serial number. A `port` binding keeps its current path lease.

## Reset effect

Effect grants become `read`, `write`, `actuate` and `reset`. A connection that restarts the device is a `reset` effect.

The first connection to a binding is a passive listen under `read`. If the device sends output within the listen's first 500 ms without any host transmission, the configurator records the binding property `resets_on_open` with the capture as evidence. This detection is a heuristic: a device that streams continuously is also treated as resetting until the operator overrides the property with a `respond` observation `{"binding_property": "resets_on_open", "value": false}`, recorded as an operator event. Every connecting tool other than the listen requires either a listen record for the binding in the same run or a `reset` grant, so an unlistened device is never opened under `read` alone. After `resets_on_open` is recorded, every connection to that binding requires an operator `reset` grant: further listens, auto-reset flash acquisition, probe, ground and package execution.

A model may declare `"resets_on_open": true` on its `uart` channel. The runtime then refuses to open the channel unless `reset` is granted, in probe and in the emitted package alike. A run whose binding carries `resets_on_open` rejects a model that omits the declaration at validation.

## Runtime connection

A `uart` channel may declare `"boot": {"line_prefix": STR, "timeout_ms": INT}` with `timeout_ms` from 1 to 60000. After opening, the runtime discards input through the first line starting with `line_prefix`, then runs the operation. Missing the line within the timeout fails with code `boot_timeout`, reporting the discarded byte count. Without `boot`, opening behaves as before.

Opening a UART holds DTR and RTS deasserted before the port opens and discards pending input. Some hosts still pulse reset on open; the reset effect covers that case.

A `Session` opens the transport once, applies the boot wait and grant checks, runs operations in order, and closes. The identity operation still runs before each operation. `execute()` runs a one-operation session and keeps its current results and transcript shape, so existing callers and recorded replays remain valid. For a model declaring `resets_on_open`, `probe_run` records all samples in one session and marks the evidence `session: single`; replay reproduces one open and one boot wait.

The emitted package exposes `session()` in its Python API and a `sequence` CLI command taking a JSON list of `{operation, parameters}` executed over one connection. Results keep the existing per-operation `interface-result/1` shape inside one sequence result.

## Acquisition

`acquire_serial_listen(baudrate, seconds)` opens the bound port with control lines deasserted, transmits nothing, and records for at most 30 seconds. It writes append-only `listen-N.raw` (raw bytes) and `listen-N.json` (byte count, first-byte delay, `unsolicited_output`, escaped text). It returns `available: true` with both artifacts hashed and sets `resets_on_open` as described above. The configurator injects the port; a worker-supplied port is refused.

`acquire_flash` and `acquire_classify` with `flash_backend: "esp32"` use the bridge's auto-reset into download mode and hard-reset on completion only when `reset` is granted; otherwise they keep `no-reset` and report that the chip did not answer. A successful acquisition returns `available: true`, lists `flash.bin`, `app.bin`, `app.elf` and the eFuse summary as artifacts, and names one `interpret_input`: `app.elf` when conversion succeeded, otherwise `app.bin`. Every exit, success or failure, leaves the chip at its default baud and, when `reset` is granted, back in its application.

Generic interpret preparation uses the accepted `interpret_input` when present and keeps the one-binary rule for imported firmware. Supporting Xtensa analysis of that input belongs to sub-project 2; this spec ends at a hashed, correctly selected handoff.

The acquire stage context names the listen tool, the grants and the reset rule. It never contains the port path.

## Public surface

CLI, MCP and recipe effect options accept `reset`. `doctor` lists serial devices with vendor, product and serial number so an operator can write an identity binding. Setup and support documents describe identity bindings, the reset effect and the listen-first acquisition order.

## Verification

Each behaviour enters through an approved seam with one failing test and its implementation. Runtime and emitted-package tests use an injected fake serial module and stubbed port listing so they run on macOS and Windows without hardware: boot discard and `boot_timeout`, refusal of `resets_on_open` without `reset` before any open, multi-operation sessions with per-operation identity, unchanged `execute()` and replay, package `sequence` and `session()`, identity resolution and its operator faults. Configurator and worker tests cover recording `resets_on_open` with evidence, refusal of the next connection without `reset`, identity leasing, listen captures as accepted acquisition, `interpret_input` selection and the absence of the port path from worker context. Installed CLI/MCP tests cover the `reset` grant and `doctor` serial listing. These are scripted contract checks, not model or physical evidence.

The physical check follows the source and installed suites. Back up the board's flash first. Start a fresh run from the generated Goose recipe with a short instruction and the identity binding. Acquire must record `resets_on_open`, dump flash under a `reset` grant, return the board to its application and hand `app.elf` to interpret. The run is expected to stop at interpret until sub-project 2. Repeat from Codex. Record the outcome as a physical observation with the actual runtime, model and worker reports, distinct from scripted tests.
