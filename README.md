# Generative Driver

Attach a supported device, state an objective, and generate a portable driver with evidence of what it can do.

Generative Driver distills a research toolchain into an installable Python package. A local configurator owns six stages: **acquire → interpret → probe → ground → emit → reuse**. The final stage gives a new agent the emitted driver and an objective, then checks that it can use the desired device functions. Codex, Goose and the command line connect to the same background process. Closing the interface leaves the run and its evidence available for reconnection.

This is an experimental developer tool. Supported paths and their verification status are recorded in [support](docs/support.md). A successful model validation is not proof that a driver describes the device correctly; independent observations provide that evidence.

## Start here

Use Python 3.11 or newer. Clone the repository, then choose your platform's installation commands:

```sh
git clone https://github.com/cam-berger/generative-driver.git
cd generative-driver
```

### macOS

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m generative_driver doctor
.venv/bin/python -m generative_driver benchmark run --case setup-smoke --output /tmp/generative-driver-smoke
```

### Windows 11 or newer

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m generative_driver doctor
.\.venv\Scripts\python.exe -m generative_driver benchmark run --case setup-smoke --output "$env:TEMP\generative-driver-smoke"
.\.venv\Scripts\python.exe -m generative_driver service start
```

Run the Windows commands in a standalone PowerShell window opened separately from Goose or Codex. Windows MCP clients connect to that independently started configurator so closing the interface leaves its run alive. See [service management](docs/setup.md#start-the-configurator).

Choose a new output directory on each run. The smoke check validates a model, replays recorded responses, emits a package, moves it, and exercises its standalone interface. It needs no agent account, hardware or container. Its report is explicitly labelled `scripted-replay` and does not count as model performance.

## Connect your interface

- [Codex app, CLI or code extension](docs/codex.md)
- [Goose Desktop or CLI](docs/goose.md)
- [Runtime configuration and hardware](docs/setup.md)
- [Benchmark setup, run, score and compare](bench/README.md)

Agent execution uses your separately configured Codex or Goose runtime and its supported authentication. The configurator does not implement another provider API loop. Original code is [Apache-2.0](LICENSE); included third-party material retains its [notices](NOTICE).

## What a run records

Each run has a durable ID, immutable accepted artifacts, worker reports, stage checks, tool events and evaluator verdicts. These records distinguish what an agent claimed, what the configurator accepted, and what independent evidence established. Device binding and allowed effects are explicit. Fresh reuse starts a new agent context. Interrupted writes remain uncertain until checked.

Benchmarks measure functional compatibility: expected outputs and effects for specified inputs. Generated source code does not have to resemble reference code. Password-encrypted groundtruth and hashes keep evaluator evidence separate from candidate input and record provenance. Password separation is not an operating-system sandbox.

## Watch a run

Open the local dashboard using the ID returned when you start a run:

```sh
python -m generative_driver dashboard RUN_ID
```

The page refreshes each second with accepted stages, status, repairs and recent activity. Use the same Python environment and `--home` as the run. Closing the page or dashboard terminal leaves the configurator running. See [dashboard usage](docs/dashboard.md) for connection and browser options.

## Development

```sh
python -m unittest discover -s tests -v
python -m build
```

Dashboard browser behavior checks use Node.js 22 or newer, with no npm packages:

```sh
node tests/dashboard-client.test.js
```

Run these with the environment used for installation. CI builds a wheel and tests its installed behavior on macOS and Windows. The approved test seams and design decisions are in [the design](docs/design.md); the red-to-green implementation records are in [docs/implementation](docs/implementation).
