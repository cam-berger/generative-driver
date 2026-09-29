# Supported paths and verification

The core targets native macOS and Windows 11 or newer. The development machine is macOS on Apple silicon. [Native CI](https://github.com/cam-berger/generative-driver/actions/runs/36504830025) passes 119 installed-wheel tests and installation replay on macOS and Windows Server 2025 with Python 3.11 and 3.13. Windows 11 workstation UI setup and the native Windows Renode/Ghidra benchmark remain unmeasured.

| Component | Implemented behavior | Dependency / limit |
|---|---|---|
| Configurator | Detached owner, durable run IDs and checked stage handoffs | Python; native socket or Windows named pipe; Windows MCP requires an independently started owner |
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
| Ground / reuse / maintain | Case-specific independent gates | Generic runs block when the required evidence adapter is unavailable |

BLE and SCPI discovery are not implemented. An adapter being present does not qualify arbitrary hardware. In particular, transport framing checks do not establish decoder correctness, physical meaning or safe protocol state sequencing.

The three benchmark profiles are documented in [bench/README.md](../bench/README.md). Reports distinguish installation replay, actual model execution, emulated observations and physical observations. Local test and baseline evidence is recorded in [verification](verification.md).
