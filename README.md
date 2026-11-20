# Generative Driver

[Driver contracts and application MCP authoring](docs/extending-drivers-and-mcp.md)

Turn a supported device’s firmware or documentation into a driver, with evidence that it works. The driver is a package of data that the installed tool runs and serves to an agent. Use Codex or the command line.

The workflow reads the input, infers commands, tests the device, checks independent observations, builds a package, and asks a fresh agent to use it. This is an experimental tool; see [supported paths](docs/support.md).

## Install and try it

Requires Python 3.11+. Clone the repository:

```sh
git clone https://github.com/cam-berger/generative-driver.git
cd generative-driver
```

With [uv](https://docs.astral.sh/uv/), on macOS or Windows 11+, one `uv sync` makes `.venv` and installs the pinned versions in `uv.lock` into it:

```sh
uv sync --extra dev
uv run generative-driver doctor
uv run generative-driver smoke
```

On Windows, also run `uv run generative-driver service start` in a standalone PowerShell window.

Without uv, on macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m generative_driver doctor
.venv/bin/python -m generative_driver smoke
```

Without uv, on Windows 11+, in a standalone PowerShell window:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m generative_driver doctor
.\.venv\Scripts\python.exe -m generative_driver smoke
.\.venv\Scripts\python.exe -m generative_driver service start
```

The smoke runs one whole run through a service of its own, with a scripted worker and a stand-in device. It needs no AI account or hardware and measures no model performance.

## Use a driver

A run emits a package: five files of data that hold the driver, its MCP tools, its documentation and its evidence, and no code. One command serves a package's tools to an agent over MCP:

```sh
uv run generative-driver serve PACKAGE --binding '{"host": "127.0.0.1", "port": 12345}'
```

The binding above is an example; the package's `README.md` gives the form for its device. The server and its tools are named after the device. See [the package](docs/extending-drivers-and-mcp.md#the-package) and [the command](docs/extending-drivers-and-mcp.md#serve-a-package).

## Next

| Task | Guide |
|---|---|
| Connect a client | [Codex](docs/codex.md) |
| Configure AI or hardware | [Setup and recovery](docs/setup.md) |
| Supply your own device and mission | [Client interface](docs/client-interface.md) |
| Contribute | [Development](CONTRIBUTING.md) · [Design](docs/design.md) |

The background configurator keeps runs, stage notes and evidence after the client closes. Save your run ID. Under `assistive/1`, a run ends with one final equivalence verdict: the configurator's own, labelled self-evidence, or one posted through the client interface for a run on a supplied device. Workers are not read-isolated: a worker running as the same user can read files outside its workspace.

## Watch a run

```sh
python -m generative_driver dashboard RUN_ID
```

Use the run’s Python environment and `--home`. The page refreshes each second; closing it leaves the run active. See [dashboard options](docs/dashboard.md). Browser checks need Node.js 22+, no npm packages: `node tests/dashboard-client.test.js`.

Original code: [Apache-2.0](LICENSE). Included material: [third-party notices](NOTICE).
