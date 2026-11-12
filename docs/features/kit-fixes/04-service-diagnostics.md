# 04. service-diagnostics

## Intent

When a run goes wrong, the operator has the run's `status`, its events and the service's `daemon.log`, and none of them answers the first questions. `daemon.log` holds only what escapes the service process: every exception the service or a tool catches is cut to `Type: message`, and its traceback is lost. `status` does not say when the budget runs out, how many repairs are left, or whether the worker is still doing anything, so a slow stage looks like a stuck one.

A stuck call is also worse than it looks. A kit tool that the service runs, whether a worker called it through the gateway or the run's own checks did, runs inside the service with no deadline. While it runs it holds the run, so the budget cannot stop the run, a cancel leaves it `stopping`, and the service cannot shut down. Some of those tools run code the worker wrote: the conversion module of a register model (`interface-model/1`). A loop in it hangs the run. A crash or an exit in it can take down the service.

After this Feature, every exception that ends in a reply, a stop or a failure event leaves its traceback in `daemon.log`, tagged with the run and stage, and so does every exception a tool catches while the service runs it. `status` gives the deadline, the repairs used and allowed, and the running worker's last sign of life, as stored times. In a generic run, the service runs every kit tool in a child process with a deadline, for a worker's gateway calls and for its own checks alike, and stops that process when the deadline passes. A gateway call stopped that way, or one that ends without a result, is recorded as unfinished, so an effect it might have had stays uncertain and is never silently retried. Because the tool runs in a child, a worker's conversion module never runs in the service's process, and a bad one costs a failed check, not the service.

Benchmark runs and benchmark tools keep today's path for tool calls, because the benchmark and its tests are not edited in this Run.

## Behaviours

- **04-B1** — The service writes an entry to its home's `daemon.log` for every exception it catches that becomes:
  - a reply to a request;
  - a run's reason or stop;
  - a failure event: `cleanup.failed`, `device.release_failed`, `emulator.recovery_failed`, `device.unavailable`, `tool.refused` or `tool.abandoned`.

  It also writes one for every exception the toolkit's `call_tool` or the toolchain's error wrapper catches while running a tool for one of the service's runs. A catch that turns a missing or unreadable file into a check note or an empty digest writes nothing. Each entry gives:
  - its time;
  - the run id and stage when there is one;
  - what was being done: the request method, the step or the tool's name;
  - the exception's type and message;
  - its full traceback.

  A request refused with a `ValueError`, or a subclass of one, that leaves the run as it was gets a one-line entry with the method and the reason, without a traceback. A refusal that also records a stop gets the full entry. The toolkit and toolchain write nothing to any log when they run outside the service.
- **04-B2** — No log entry holds request parameters, run inputs, tool arguments, worker prompts or reports, or anything from `service.json`. An entry names the request method, the run id and the tool, and quotes the exception's own message.
- **04-B3** — A run's `status` gives `log`, the path of `daemon.log` in the service's home, whether or not the run has an entry in it.
- **04-B4** — When a service starts and its home's `daemon.log` is larger than 10 MB, the log is renamed to `daemon.log.1`, replacing any earlier one, and the new service writes a new `daemon.log`.
- **04-B5** — A run's `status`, and every reply built from it, adds:
  - `deadline`: when the run's budget runs out, in seconds since the epoch like `created`, after any extension;
  - `repairs`: `used`, the repairs the run has made, and `budget`, its repair budget; null when the run's saved progress records no repair count;
  - `worker`: null when no stage worker is running. Otherwise it gives:
    - `stage`;
    - `started`, when that worker's assignment began;
    - `last_activity`, the later of when the worker last printed a line on its output and when one of its gateway calls last started or ended; it equals `started` before either happens;
    - `last_activity_kind`: `output`, `tool` or `started`.

  Every time in `status` is a stored time, never an age or a remaining duration, so two reads of a stopped run return the same `status`. Reading these fields never makes `status` fail, including for a run saved by an earlier version. Suite replies stay as they are.
- **04-B6** — `generative-driver status` and the MCP `driver_status` return those fields, and the `driver_status` description names them.
- **04-B7** — In a generic run, the service runs a worker's gateway call to a kit tool in a child process. The call's time counts from when the service receives the request.
  - The service stops that process, together with every process it started, when the call's deadline passes: 570 seconds after the request arrived, or when the run's budget runs out, whichever comes first.
  - A cancel of the run, or the service shutting down, stops it at once.
  - A call whose deadline has already passed before it is dispatched, for example while it waited behind another call of the same run, is refused as `tool.refused` without being started, so it leaves no uncertain effect.

  The 570 seconds keep the service's answer ahead of Codex's own 600-second wait for a tool. Only the service process turns child processes on. A controller built inside another process, as the tests build one, runs tools inline as today, so a test's replacement for `call_tool` still takes effect.
- **04-B8** — A dispatched gateway call that ends without a result is recorded as a `tool.abandoned` event, never as `tool.finished`. That happens when the service stopped it (04-B7), when it raised inside the service, or when its child process ended without returning a result (it crashed, exited, or raised outside the tool's own handling). The event gives its stage, its assignment, the tool's name, the dispatch id of its `tool.started` event, and `reason`:
  - `timeout`, `budget`, `cancelled` or `shutdown` when the service stopped it;
  - `raised` otherwise, with a type and message that name the exception or the child's exit status.

  The worker's reply is `{"ok": false, "abandoned": <reason>, "reason": <a sentence naming what happened>}`. It adds `"uncertain_effect": true` when the call could have had an effect.
  - Every reader that looks for `tool.finished` still finds the call unfinished, because its outcome is unknown.
  - An effect the call might have had stays uncertain until the operator confirms it, as after a lost reply today. Nothing the stopped process did can complete the call later.
  - Once the process has ended, the call no longer holds the run: `stopping` clears, the run's thread carries on, and the service can shut down.

  A fresh-reuse call through a package's generated catalog that raises inside the service after it began is recorded the same way.
- **04-B9** — In a generic run, the service runs its own checks' kit tools (`interpret_collect`, `model_validate` and `emit_check`) in a child process too, with the limits of 04-B7 counted from when the check starts. A check that the service stopped, or whose process ended without a result, is recorded in the stage's notes as a failed check that names the tool and the cause, and the run carries on as it does after any failed check; it is not a host stop. So a worker's conversion module, which `model_validate`, `probe_run` and `emit_check` execute, never runs in the service's process in a generic run. A check that answers in time gives the result it gives today.

## Interface and seams

The log stays `daemon.log` in the service's home, the file client errors, the smoke and `docs/codex.md` already point to. Its line format is the builder's choice within 04-B1 and 04-B2. Rotation happens where the service's log is opened when it starts. How a tool running for the service learns its run, stage and log is the builder's choice; nothing outside the service sets it.

A run's `status` keeps every key it has and adds `deadline`, `repairs`, `worker` and `log`. A tool result keeps its keys. `tool.abandoned` is a new event kind. The 570-second limit is a module constant that tests may shorten. How the service turns child processes on when it builds its controller is the builder's choice; the default for a controller built any other way is inline.

Tests attach at the configurator (an in-process controller) for status fields and for a call that raises, and at the service, or at a controller built with child processes on, for the log and for 04-B7 to 04-B9. A test that needs to inject a failure into the service may run it in a thread of the test process, as `tests/test_replay_executor.py` does. A test of a stopped call shows the process and its children gone, the call recorded as abandoned, an effectful call's uncertainty kept, and the run able to end. A test of 04-B9 uses a register model whose conversion module loops, and one whose module exits the process. No existing test changes because of 04-B7 or 04-B9. The smoke of Feature 03 runs through the service, so it exercises both on every run. The 04-B7 and 04-B9 tests run on both platforms.

`docs/codex.md` says three things: the traceback behind a failed run is in `daemon.log`, which `status` names as `log`; older entries may be in `daemon.log.1`; and a tool call has 570 seconds.

## Out of scope

- Runs with a benchmark case, and tools the benchmark provides, keep today's inline path for gateway calls and checks, so a conversion module still runs inside the service there; this is recorded in the benchmark notes.
- Fresh-reuse calls through a package's generated catalog, which run package operations in a package process that already has a 300-second limit; 04-B7 does not apply to them.
- Changes to how the toolchain loads or runs a conversion module, bounds on worker-chosen sizes and waits (`n`, `seeds`, `random_files`, `delay_ms`), and a conversion module named outside its model's folder. The builder lists the last two under Noticed.
- Memory limits on any process.
- Codex's 600-second tool wait and the client's 620-second reply wait, which stay as they are.
- Choosing fault categories by type instead of by wording, which the Breakdown leaves out.
- Checking a worker's tool arguments against the tools' input schemas.
- The dashboard, suite replies, a field telling a client that the service could not be reached, and recording a run's runtime version.

## Deviations
