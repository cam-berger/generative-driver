# Generative Driver

Turn a supported device’s firmware or documentation into a portable driver, with evidence that it works. Use Codex, Goose or the command line.

The workflow reads the input, infers commands, tests the device, checks independent observations, builds a package, and asks a fresh agent to use it. This is an experimental tool; see [supported paths](docs/support.md).

## Install and try it

Requires Python 3.11+. Clone the repository:

```sh
git clone https://github.com/cam-berger/generative-driver.git
cd generative-driver
```

macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m generative_driver doctor
.venv/bin/python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
```

Windows 11+, in a standalone PowerShell window:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m generative_driver doctor
.\.venv\Scripts\python.exe -m generative_driver benchmark run --case setup-smoke --output "smoke output"
.\.venv\Scripts\python.exe -m generative_driver service start
```

Use a new output directory each time. Smoke tests installation and recorded replay; it needs no AI account or hardware and measures no model performance.

## Next

| Task | Guide |
|---|---|
| Connect a client | [Codex](docs/codex.md) · [Goose](docs/goose.md) |
| Configure AI or hardware | [Setup and recovery](docs/setup.md) |
| Evaluate a run | [Benchmarks](bench/README.md) · [Recorded results](docs/verification.md) |
| Broaden firmware coverage | [Upstream corpus](docs/benchmark-corpus.md) · [Seven-area intake and gaps](docs/research/2026-10-26-corpus-intake.md) |
| Contribute | [Development](CONTRIBUTING.md) · [Design](docs/design.md) |

The background configurator keeps runs, stage notes and evidence after the client closes. Save your run ID. Under `assistive/1`, the final equivalence verdict is an evaluator grade for benchmarks and self-evidence otherwise. Results form a new series. Workers are not read-isolated; answer-key separation is password-based, not a sandbox.

## Watch a run

```sh
python -m generative_driver dashboard RUN_ID
```

Use the run’s Python environment and `--home`. The page refreshes each second; closing it leaves the run active. See [dashboard options](docs/dashboard.md). Browser checks need Node.js 22+, no npm packages: `node tests/dashboard-client.test.js`.

Original code: [Apache-2.0](LICENSE). Included material: [third-party notices](NOTICE).
