# 03. service-smoke

## Intent

A first run is the first time the background service, its worker launch and the tool gateway work together on the operator's machine. Nothing checks that path short of a model run on a device. The release check's smoke, the benchmark's `setup-smoke` case, calls the tools in-process and starts neither the service nor a worker. The tests that start the service stop at probe or skip the stages that do real work. So a broken socket, worker launch, gateway, interpret workspace or generated reuse catalog on a fresh Mac or Windows machine shows only after the operator has signed in to Codex, spent tokens and bound a device.

After this Feature, `generative-driver smoke` runs one whole run through that path with no model, no Codex and no hardware. It starts its own service with a new temporary folder as its home, configures a scripted worker in place of Codex, and binds a stand-in device it serves on this machine. Each of the six stages launches the scripted worker the way it would launch Codex, and the worker does that stage's work through the stage's own gateway and tools. The smoke passes only when every stage did its work cleanly and the run's self-evidence verdict is equivalent. Otherwise it names the first stage that broke and where its logs are. It leaves nothing running. The test suite runs the same command, so the later Features of this Run have a regression check of the whole service path.

The smoke checks the kit's machinery, not a model, a firmware image or a device, and its report says so. It never touches the operator's home, runs, configuration, Codex or devices.

**Scripted worker**: a program the kit ships that the configurator launches in place of the Codex CLI and that does a stage's work by a fixed script, with no model.

**Stand-in device**: a fictional line-protocol device the smoke serves on a localhost TCP port for the length of one smoke.

## Behaviours

- **03-B1** — `generative-driver smoke` exits 0 and prints a JSON report with `ok: true` when all of these hold:
  - its run completed the six stages in order (acquire, interpret, probe, ground, emit, reuse), one attempt each, none skipped;
  - each attempt launched the scripted worker, which ended with a report whose status is `completed`;
  - the configurator accepted each handoff, and every configurator check recorded in its notes passed;
  - the run recorded the self-evidence verdict `equivalent: true`;
  - the service stopped (03-B7).

  The report gives:
  - `run_id`;
  - `stages`, one entry per stage attempt in the order they ran, each with `stage`, `accepted`, `skipped` (the configurator's skip reason, or null), `worker` (the scripted worker's report status, or the cause the launch recorded when there was no valid report), `failed_checks` (the ids of the configurator checks in its notes that did not pass) and `seconds`;
  - `verdict`: the recorded final verdict's `equivalent` and `basis`, and the ids of any of its checks that did not pass;
  - `service` (03-B2);
  - `seconds` for the whole smoke;
  - `evidence`, a sentence saying the run used the scripted worker and the stand-in device, so it shows that the kit's service path works on this machine and nothing about a model, a firmware image or a real device.
- **03-B2** — The run goes through a background service that the smoke starts as its own process, the same service `generative-driver service start` starts, with a new folder under the system's temporary directory as its home. The report's `service` gives that process's `pid` and `stopped`, whether the smoke saw the process end. The service launches every stage's worker through the same executor launch it uses for Codex, with the arguments it passes Codex. The scripted worker uses only what that launch gives a Codex worker: the prompt on its input, its workspace, and the stage's tool gateway. It starts that gateway with the command and arguments the launch names, speaks MCP to it over standard input and output as Codex does, and calls only the tools the launch enables.
- **03-B3** — The scripted worker does each stage's work with the tools that stage is given:
  - acquire imports the firmware stand-in, a small file the smoke writes, with `acquire_firmware_artifact`;
  - interpret writes the stand-in model as `model.json` in its workspace;
  - probe and ground each run the model's operations on the stand-in device with `probe_run`;
  - emit builds the package with `emit_package`;
  - reuse, given only the package and the objective, calls an operation other than the identity operation through the package's generated MCP catalog, served by the gateway its launch names.

  It prints a report at every stage, interpret included. When a step of its script fails, it writes the error to standard error and exits nonzero, so the run stops in that stage.
- **03-B4** — The smoke does not read or write the operator's home, whether named by `--home`, by `GENERATIVE_DRIVER_HOME` or by default, including its `config.json` and runs. It does not launch the configured Codex or any program named `codex`, open a serial, USB or FTDI device, or connect anywhere but this machine. The stand-in device listens on 127.0.0.1 only, on a port the system picks for each smoke.
- **03-B5** — When any condition of 03-B1 fails, the smoke exits 1 with `ok: false` and:
  - `failed_stage`: the stage the run stopped in; otherwise the first stage attempt that failed a condition of 03-B1 (it ran more than once, was skipped, gave no `completed` report, was not accepted or failed a check); null when the service or the run could not be started, or when every attempt passed and only the verdict or the service's stop failed. For a repair, the failed attempt is the probe or ground attempt whose device difference caused it.
  - `reason`: the run's reason and outcome category when it stopped; otherwise the failed stage's failed check ids, skip reason, device difference or worker summary; otherwise the verdict's failed check ids, the start error, or that the service did not stop.
  - `logs`: the path of the service's log, and of the standard error and event stream of the failed stage's worker when there is one.
  - `home`: the temporary folder, which it keeps so the logs can be read.
- **03-B6** — The smoke gives its run a budget of 300 seconds and the default repair budget. A run that has not ended by then is stopped by the configurator like any run whose budget is spent, and the smoke reports it as 03-B5 says. If the run has still not ended 60 seconds after its budget, the smoke cancels it and reports `failed_stage` the stage that was running and a `reason` saying the run did not end in time.
- **03-B7** — On every exit (success, failure, a spent budget, an error in the smoke itself, or Ctrl-C), the smoke stops the service it started and the stand-in device. It asks the service to shut down, asks again while the service answers that its shutdown is pending, and after 30 seconds terminates the service process. When the service stopped, no worker or gateway the service launched for the smoke is left running. The smoke removes its temporary folder after a success and keeps it otherwise. After Ctrl-C it exits nonzero and prints the report with `ok: false` and a `reason` saying it was interrupted.
- **03-B8** — The smoke works from an installed wheel as it does from a source checkout. The scripted worker and the stand-in model ship in the package, and the worker finds the package although a worker launch passes no `PYTHONPATH`. The CI job that runs the installed wheel, and the software checks in `docs/verification.md`, also run `python -m generative_driver smoke`.

## Interface and seams

`generative-driver smoke` takes no options and prints the report as the other commands print theirs. The global `--home` does not apply to it (03-B4). The smoke lives in a module of its own, as the Breakdown asks of new behaviour. Where the scripted worker and the stand-in device live inside the package is the builder's choice.

Tests run the command as a child process with `package_env()` and attach to its exit status, its report, the kept folder (its run's events and what the scripted worker records of its launch) and the processes left running. A test that needs a stage to fail, a shorter budget or wait, a service that does not end the run or does not stop, or the folder kept after a success uses a seam the builder adds that callers outside tests never use. The Ctrl-C test may be skipped on Windows, where a test cannot send Ctrl-C to one child process alone; every other test runs on both platforms.

`docs/codex.md` tells an installer to run `generative-driver smoke` after `doctor` and before a first run, and says what a failure points to.

## Out of scope

- An MCP tool or a client-skill step for the smoke.
- Running the configured Codex, a model or a real device, or any option to choose the home, the worker or the device.
- The benchmark's `setup-smoke` case and the other software checks, which stay as they are.
- Changes to the configurator, the worker launch, the gateways or the stage checks, beyond fixing a defect the smoke exposes; each such fix is reported as a Deviation.
- Stopping workers or gateways left behind after the service had to be terminated (03-B7); the report's `service.stopped: false` says so.
- Files the service and the configurator place outside the smoke's temporary folder: the interpret stage's sealed workspace, which the configurator creates in the system's temporary directory and never removes, and the service's socket folder.
- The directory listing the worker launch already does of the operator's skill folders (`~/.agents/skills`, `$CODEX_HOME/skills`) to switch those skills off for a worker.
- A service log beyond the existing `daemon.log`, and deadlines on tool calls, which are Features 04 and 05.

## Deviations

- The test seams are one JSON environment variable, `_GENERATIVE_DRIVER_SMOKE_TEST`, with keys `keep`, `replies`, `worker` (`--idle STAGE`, `--hang STAGE`), `budget_seconds`, `run_budget_seconds`, `grace_seconds`, `error_in`, `kill_in`, `ignore_shutdown` and `stop_seconds` — the Spec allows seams only tests use and leaves their form open; each key serves a case the Spec needs tested and acts only when set.
- The scripted worker writes `_scripted_worker.json` in its workspace: its launch arguments, the gateway command and arguments, the tools it listed and called, the `generative_driver` file it imported and its `PYTHONPATH` — this is the Spec's "what the scripted worker records of its launch".
- Probe and ground call `probe_run` once per model operation — still "runs the model's operations with `probe_run`"; the gateway refuses a call that names no operation.
- The stand-in model is interface-model/3 over TCP with identify (`ID?` → `DEMO-42`) and measure (`T` → `T:21.5`, degC); the firmware stand-in is a short text file; the run asks for `['read']` effects and a fixed goal — details the Spec leaves open.
- After the 60-second grace the smoke cancels the run and waits up to 30 seconds before reading it — a bounded wait so 03-B6 can name the running stage.
- `failed_checks` lists the configurator checks' `name`s — stage checks carry only `name` (configurator.py:3427), while the verdict's checks carry the `id` that `verdict.failed_checks` uses.
- On Ctrl-C or an error in the smoke itself, `failed_stage` is the stage the run was in, with "The smoke was interrupted (Ctrl-C) while the run was in …" or "The smoke itself failed: …" and that stage's worker logs — consistent with 03-B5's "the stage the run stopped in" and 03-B7's "interrupted".
- A further Ctrl-C is ignored while the smoke stops the service — serves 03-B7's "on every exit … stops the service".
- After 30 seconds the service process alone is killed (SIGKILL, or `taskkill /F` on Windows), only while it still holds its home's `service.lock`, else by pid; the reason says workers or gateways may still run — matches 03-B7's "terminates the service process" and the Out-of-scope line on leftover workers.
- `service.stopped` is true only once the smoke sees the service release its lock — this is the Spec's definition, "whether the smoke saw the process end".
- `docs/codex.md` writes the command as `python -m generative_driver smoke` — the same command, written as the rest of that page writes commands.
- A service that stops answering is treated as a stop at once (an `ok: false` reply while its home's lock is free): the smoke follows the run's events while it waits and reports the stage it was in, the attempts already done, and that workers or gateways may still run — applies 03-B5's "the stage the run stopped in" and the Intent's "names the first stage that broke".
- A service that stopped answering on its own gets `service.stopped: false` although its process ended — keeps 03-B7's promise that a stopped service leaves no worker running, and matches the Out-of-scope line where `stopped: false` signals possible leftovers.
- A failing report's `evidence` says the run "can show at most whether" the service path works; a passing report keeps the Spec's sentence — keeps the Intent's "its report says so" true for failures.
