# Supported paths and verification

The core targets native macOS and Windows 11 or newer. The development machine is macOS on Apple silicon. [Historical native CI for source `4c95b15`](https://github.com/cam-berger/generative-driver/actions/runs/36506077842) passed 122 installed-wheel tests and installation replay on macOS and Windows Server 2025 with Python 3.11 and 3.13. That run predates the suite changes. Windows 11 workstation UI setup and the native Windows Renode/Ghidra benchmark remain unmeasured.

| Component | Implemented behavior | Dependency / limit |
|---|---|---|
| Configurator | Detached owner, durable run IDs and checked stage handoffs | Python; native socket or Windows named pipe; Windows MCP requires an independently started owner |
| Benchmark suites | CLI/MCP reconnect to that owner; sequential children, checked export and offline saved comparison | Registered emulated full workflows with matching authenticated reference calibration |
| Codex integration | MCP, plugin, configured command-line workers | Separately installed and authenticated Codex |
| Goose integration | MCP, recipe, configured command-line workers | Goose live execution requires its installed version; contract test alone is not a live pass |
| Firmware input | Pinned local import and sealed neutral interpretation workspace | Firmware ownership and lawful acquisition are operator concerns |
| ESP acquisition | Explicit supported ESP backend | esptool; incomplete app carving fails closed |
| Datasheets | Registered vendor fetch, hash and extraction | Optional PDF dependency; vendor PDFs are not bundled |
| USB discovery | Read-only descriptors for explicit identity | PyUSB and native libusb |
| FTDI discovery | Explicit I2C scan and register sweep | PyFtdi and native libusb |
| Model schema 1 | Register model with conversion code | I2C live; SPI recorded capture |
| Model schema 2 | Experimental command evidence from firmware | Static collection/validation; not directly executable |
| Model schemas 3–6 | Executable operations, frames and typed decoding | Capability depends on selected schema and transport |
| UART / TCP | Serial transport / socket transport | pyserial / Python standard library |
| USB execution | Control, bulk and interrupt transfers | PyUSB/libusb; device-specific qualification still required |
| I2C / SPI execution | Bridge-backed transfers for executable models | PyFtdi/libusb |
| Ground / reuse | Case-specific independent gates; fresh reuse is the endpoint | Generic runs block when the required evidence adapter is unavailable |

BLE and SCPI discovery are not implemented. An adapter being present does not qualify arbitrary hardware. In particular, transport framing checks do not establish decoder correctness, physical meaning or safe protocol state sequencing.

The registered benchmark cases are documented in [bench/README.md](../bench/README.md). Reports distinguish installation replay, actual model execution, emulated observations and physical observations. Local test and baseline evidence is recorded in [verification](verification.md).

TQ9 v2 requires authenticated calibration for the installed evaluator implementation. Its native macOS reference gate uses GCC/Renode/Ghidra/Java; Controller fixtures use scripted external workers and are labelled accordingly. Neither establishes model success, physical-device behavior or native Windows emulator qualification. Windows CI configuration is not an observed Windows native result. Native process ownership is retained in the running configurator; ownership lost on restart fails closed for operator reconciliation.

Suite CLI and real stdio MCP boundaries have local macOS scripted contract coverage, including same-owner reconnection, missing-owner Windows policy, a disappearing owner, strict pagination, public exports and offline comparison. Simulating `platform.system()` in a macOS MCP process checks policy only. The historical CI run above predates this refactor. Current observations and remaining qualification are recorded in [verification](verification.md); local macOS observations do not qualify a new Windows branch, Windows 11 workstation or native Windows emulator run.
