# Support and limits

The core targets native macOS and Windows 11+. Development uses Apple silicon macOS. [Historical CI at `4c95b15`](https://github.com/cam-berger/generative-driver/actions/runs/36506077842) passed 122 installed-wheel tests and installation replay on macOS and Windows Server 2025 with Python 3.11/3.13; it predates suite changes. Windows 11 UI setup and native Windows Renode/Ghidra remain unmeasured. See [current evidence](verification.md).

| Component | Support / requirement |
|---|---|
| Configurator | Durable background owner; native socket or Windows named pipe. Windows MCP needs an independently started owner. |
| Suites | Sequential emulated workflows, reconnect, exports and offline comparison; matching authenticated calibration required |
| Codex / Goose | Installed, authenticated command-line runtime; Goose contract tests do not establish live execution |
| Firmware | Pinned local import; operator owns acquisition rights. ESP acquisition needs esptool and rejects incomplete app carving. |
| Datasheets | Registered vendor fetch, hash and extraction; optional PDF dependency; PDFs not bundled |
| USB | Explicit read-only discovery; control/bulk/interrupt execution via PyUSB/libusb |
| FTDI | Explicit I2C scan/register sweep and I2C/SPI execution via PyFtdi/libusb |
| UART / TCP | pyserial with identity binding and `reset` grant for reset-on-open / Python standard library |
| Model 1 | Register/conversion model; live I2C, recorded SPI |
| Model 2 | Experimental firmware command evidence; static validation only |
| Models 3–6 | Executable operations and typed decoding; framing/features depend on version and transport |
| Ground / reuse | Test meanings against the device, then fresh package-only use. One final verdict: generic self-evidence or post-run benchmark grade. |

BLE and SCPI discovery are absent. An available adapter does not qualify arbitrary hardware; frame checks do not prove decoding, physical meaning or state sequencing. The bench harness owns the emulator/password; the configurator uses its control endpoint. A stopped run needs a healthy device matching its recorded session before resuming.

Effect grants bound the effects a model declares. Only a register model's write steps and a USB control transfer's direction are checked against the request. Elsewhere, on UART, TCP and USB bulk/interrupt, a `read` label is the model author's claim, and probe and ground workers may run models they wrote. A read-only grant does not show that the device was left unchanged, and an interrupted read is never held uncertain.

Local macOS scripted CLI/MCP tests cover suite reconnection, owner loss, Windows missing-owner policy, pagination, exports and offline comparison. Simulated Windows policy checks are not native Windows results. TQ9 v2 native qualification uses GCC/Renode/Ghidra/Java and authenticates the installed evaluator; it measures reference behavior, not model or physical performance.

[Benchmark reports](../bench/README.md) distinguish replay, scripted workers, model execution, emulation and physical observations.

The generic managed interpreter currently requires one firmware binary. Acquired USB descriptors, datasheet pages and FTDI scans remain evidence; automatic selection of their non-firmware front-ends is absent. Acquisition alone does not establish a generated driver.
