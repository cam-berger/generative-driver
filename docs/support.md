# Support and limits

The core targets native macOS and Windows 11+. Development uses Apple silicon macOS. Windows 11 UI setup and native Windows Ghidra remain unmeasured.

| Component | Support / requirement |
|---|---|
| Configurator | Durable background owner; native socket or Windows named pipe. Windows MCP needs an independently started owner. |
| Codex | Installed and authenticated CLI for model runs |
| Firmware | Pinned local import; operator owns acquisition rights. ESP acquisition needs esptool and rejects incomplete app carving. STM32 flash is read through an ST-LINK probe with STM32CubeProgrammer; that read has scripted evidence only. |
| Datasheets | Registered vendor fetch, hash and extraction; optional PDF dependency; PDFs not bundled |
| USB | Explicit read-only discovery; control/bulk/interrupt execution via PyUSB/libusb |
| FTDI | Explicit I2C scan/register sweep and I2C/SPI execution via PyFtdi/libusb |
| UART / TCP | pyserial with identity binding and `reset` grant for reset-on-open / Python standard library |
| Model 1 | Register/conversion model; live I2C, recorded SPI |
| Model 2 | Experimental firmware command evidence; static validation only |
| Models 3–6 | Executable operations and typed decoding; framing/features depend on version and transport |
| Ground / reuse | Test meanings against the device, then fresh package-only use. One final verdict: the configurator's self-evidence, or one posted after a run on a supplied device. |

BLE and SCPI discovery are absent. An available adapter does not qualify arbitrary hardware; frame checks do not prove decoding, physical meaning or state sequencing. The owner of a supplied device runs it; the configurator uses its control endpoint and holds no password. A stopped run needs a healthy device matching its recorded session before resuming.

Effect grants bound the effects a model declares. Only a register model's write steps and a USB control transfer's direction are checked against the request. Elsewhere, on UART, TCP and USB bulk/interrupt, a `read` label is the model author's claim, and probe and ground workers may run models they wrote. A read-only grant does not show that the device was left unchanged, and an interrupted read is never held uncertain.

Local macOS scripted CLI/MCP tests cover reconnection, owner loss, Windows missing-owner policy and pagination. Simulated Windows policy checks are not native Windows results.

Without a single firmware binary, the managed interpreter needs a device binding and interprets device first; see [Codex](codex.md). Acquired USB descriptors, datasheet pages and FTDI scans remain evidence; automatic selection of their non-firmware front-ends is absent. Acquisition alone does not establish a generated driver.
