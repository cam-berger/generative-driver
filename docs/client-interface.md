# Client interface, version 4

A client that brings its own device, firmware and mission asks five things of the tool: start a run on a device it supplies, replace that device, post one final verdict, read the run's records and its frozen driver, and stop or seal a run. This page says what the client sends, what it gets back and when the tool refuses. The module `generative_driver.client_interface` holds the same in code, and its `VERSION` is the number in the title.

The service is the configurator, the background owner of runs. A client reaches it on the same machine with `call(method, params, home)` of `generative_driver.client`. Every reply is a JSON object. A refusal is `{"ok": false, "error": ..., "reason": ...}`, and `reason` is a sentence. `call` itself answers `{"ok": false, "reason": ...}`, with no `error`, when the service runs other code than the client, when a reply is lost, and for a `ping` whose `ok` is false. The calls of this interface are `ping`, `start`, `resume`, `status`, `events`, `result`, `attach_device`, `record_evaluation`, `cancel` and `seal`. Runs follow the protocol `assistive/1`.

## Start a run on a supplied device

A supplied device is one the client owns: the client starts it before the run and stops it afterwards, and describes it to the service with a device descriptor. The client also supplies the firmware the device runs and the mission the driver is for, as data.

`start` takes:

| Key | Value |
|---|---|
| `goal` | What the driver is for. Text, required. |
| `device` | The device descriptor, below. |
| `firmware` | The firmware the device runs: an object with exactly `path` and `sha256`, below. |
| `mission` | What the driver must do, below. |
| `evaluation_token_sha256` | The SHA-256, as 64 lowercase hex characters, of the UTF-8 bytes of a token the client keeps to itself. The token later proves who may replace the device and post the verdict. |
| `effects` | The effects the client grants the run: any of `read`, `write`, `actuate` and `reset`. Without it, `read` alone. |
| `budget_seconds` | The run's time budget: positive, at most seven days. Without it, three hours. |
| `executor` | The worker runtime, `codex` or `replay`. Without it, `codex`. |
| `executor_config` | Settings of that runtime for this run. On a supplied run it is the whole configuration, and without it the run has what the operator configured. |
| `request_id` | Text of the client's choice. A `start` repeated with the same `request_id` and the same inputs starts nothing and finds the same run, and `status` finds the run by it. |
| `client_record` | A JSON object of the client's own, of at most 262144 bytes as JSON. The service stores it, never reads it, and gives it back with `result`. |
| `ghidra_home`, `java_home` | Absolute paths of the analysis tools the interpret worker may use. |
| `scoped_tool_approval` | `emulator` approves the run's assigned tools on the supplied device without asking again. |

`device`, `firmware` and `mission` come together, and they need `evaluation_token_sha256`: a `start` with one or two of the three is refused.

The device descriptor has these parts and no other:

| Part | Value |
|---|---|
| `schema` | `device-descriptor/2` |
| `control` | An object whose `endpoint_file` is the path of the file through which the service reaches the device's control endpoint. Text, not empty. |
| `session_id` | The name of the device's running session. Text, not empty. |
| `binding` | The connection the run's device operations go over: a host and port, a serial port, an FTDI URL, or a USB vendor and product id. The service leases it to the run. |
| `firmware_sha256` | The SHA-256 of the firmware the device runs, as 64 lowercase hex characters. It must be the `sha256` of `firmware`. |
| `instrument` | Optional. An object with exactly `units`, an object that gives the unit of each reading the device offers. With no `instrument`, or with `units` that name some readings, a worker sees the device's readings beside each live result. With empty `units` it sees none. |
| `development_conditions` | Optional. An object whose `conditions` is a list of at least one object, each with an `id` that is text, not empty and unlike the others. A probe or ground worker may select one before a live call. The catalog and its entries may hold other keys. |

`device_errors(device)` gives a descriptor's faults before it is sent.

The firmware is a file the client names with its hash. `path` is an absolute path as text, and `sha256` is the SHA-256 of the file's bytes, as 64 lowercase hex characters. `firmware_errors(firmware)` gives the faults of its form. The service also refuses a `start` when no file lies at `path` or its bytes hash otherwise. It hashes the file again before each stage and when the run is resumed. A file that is gone or has changed stops the run with the outcome category `integrity`.

The mission is an object with `tasks` and `reuse_objective`, and it may have `operating_conditions`. It has no other key. `mission_errors(mission)` gives its faults, and the service refuses a `start` by the same rules:

- `reuse_objective` is text that is not blank: what a fresh worker is asked to do with the frozen driver.
- `operating_conditions`, when present, are 1 to 16 texts that are not blank, of at most 1000 characters each. Workers read them as facts.
- `tasks` is an object with at least one task. A task's name matches `[A-Za-z][A-Za-z0-9_]{0,63}`.
- A task is an object with `meaning`, text that is not blank, and with `inputs` and `outputs`, both objects. It may have `sampling`, which is `acquire` or `retained`. It has no other key.
- Each input is an object whose `type` is `integer`, `number`, `string` or `bytes`. Each output is an object with a `unit` that is null or text that is not empty. An input and an output may hold other keys.
- An output may hold `instrument`, text that is not empty. It then holds `absolute_tolerance` too, a finite number that is not below zero.

The tool uses `sampling`, `instrument` and `absolute_tolerance` during the probe and ground stages. For a task with `sampling`, it compares each successful live call of the task with the device's readings around the call. A task that says `acquire` must take a new sample, and one that says `retained` must not. An output that names an `instrument` is held to the device's reading of that name: the tool compares the two, and a difference beyond `absolute_tolerance` goes back to interpretation as a device difference. Nothing here is a grade.

The reply is `{"ok": true, "run_id": ..., "duplicate": false}`, or `duplicate` true for a repeated `request_id`. When its stages are done the run ends with the status `completed` and waits for the client's verdict. Whenever the run stops, the service sends `release`. It never stops the device: the client does.

`start` is refused without a `goal`, for a budget, an effect or an executor outside the values above, for a `request_id` already used with other inputs, for a descriptor, a firmware or a mission with a fault, for a device that runs another firmware than the one supplied, for a token hash that is missing or of another form, for a `client_record` that is no JSON object or is too large, and for a `binding` that another run or session still holds. The service holds no password, and refuses a request with a key that names one. It knows no case: a `start` with `case`, `case_options`, `case_pin`, `admission` or `public_inputs` is refused.

`resume` with `run_id` continues a run that stopped before it was complete (`blocked`, `failed` or `cancelled`) and answers with its status. It is refused while a worker of the run is still stopping, while an effect is uncertain, and once the budget is spent, unless `budget_seconds` with a `budget_reason` raises it. A stage resumes only on a device whose `status` answers healthy, with the session and binding the run recorded. Otherwise the resume is refused, and the run stays stopped until that device answers so again or `attach_device` records a new one.

`ping` takes nothing. Its `ok` is true when the service runs the same code as the client. `ping` answers with `pid`, the process id of the service, by which a client sees that another service process answers.

## The control endpoint

The endpoint file holds a JSON object with `family`, which is `AF_UNIX` or `AF_PIPE`, `address`, and `authkey`, the key as hex. The tool speaks to the endpoint over a connection of Python's `multiprocessing.connection` with that key. It makes one request per connection. The request is the JSON bytes of an object with `method` and `params`. The reply is the JSON bytes of `{"ok": true, "result": ...}` or `{"ok": false, "error": ..., "reason": ...}`, of at most 4 MiB. A refusal, a reply of another form and no reply in time all count as a device that cannot be reached.

The tool sends six requests and no other:

| Request | Params the tool sends | Parts of the answer the tool reads | When |
|---|---|---|---|
| `status` | None. | `session_id`, `binding` and `healthy`. | Before a stage that uses the device, when a stopped run is resumed, and for `attach_device`. |
| `observe` | None. | `session_id`, `values` and `units`, and `condition` when the answer has one. | Before and after each live call on the device. |
| `select_condition` | `condition_id` and `expected_session_id`. | None. | Before a live probe or ground call for which the worker chose a development condition. |
| `set_running` | `running`, true or false. | None. | True before each live call and false after it: the device runs only during a call. |
| `replace` | `expected_session_id`, `evidence_ids`, `dispatch_ids` and `deadline_seconds`. | A record with `schema` `emulator-recovery/2`, `old_session_id`, `new_session_id`, `binding`, and `old_owner_stopped` and `initial_state_verified`, both true. | After a stage in which a write reached the device and failed for the model. The run goes on with the new session and binding. |
| `release` | `session_id`. | None. | Whenever the run stops. |

## Replace a device

`attach_device` takes `run_id`, `token` and `device`, the descriptor of a new device. It is for a stopped run whose device was stopped or replaced outside the run. The service checks that:

- the run was started on a supplied device, and `token` hashes to its `evaluation_token_sha256`;
- the descriptor has no fault, and its `firmware_sha256` is the `sha256` of the run's firmware;
- the device's `status` answers healthy, with the `session_id` and the `binding` the descriptor names;
- the run is stopped, no worker of it is still stopping, and no effect is uncertain.

Where a check fails, the call is refused. Otherwise the service records the new session and binding and answers with the run's status. It does not resume the run: send `resume` next.

## Post one final verdict

`record_evaluation` takes `run_id`, `token` and `equivalence`, the verdict record. The record has exactly these fields:

| Field | Value |
|---|---|
| `schema` | `equivalence/2` |
| `protocol` | `assistive/1` |
| `basis` | `evaluator` |
| `status`, `equivalent` | `graded` or `no-driver` with true or false, `no-driver` only with false. `unknown` or `unscored` with null. |
| `checks` | A list of `{id, passed}`. `passed` is true or false. It is null for a check the evaluator could not decide, only in an `unknown` record or beside a failed check in a `graded` one. |
| `diff` | A list of `{id, reason}`: each difference with its reason. |
| `passed`, `total` | The number of checks that passed, and of all checks. |
| `mission` | Null, or how fresh reuse fared: an object with any of `name`, `passed`, `reason` and `fault`. Its `passed` is true, false or null, and the rest is text of at most 300 characters. |
| `frozen_artifact_sha256` | The hash of the driver the verdict is about. Null when the run froze none. |
| `evidence_sha256` | The hash of the evidence the client sealed its grade in. Null only in an `unscored` record. |

`verdict_errors(record)` gives a record's faults as sentences, and the service refuses by the same rules. An equivalent driver has at least one check, every check passed and an empty `diff`. Every other record has a `diff` that is not empty. An `unknown` record has no failed check and at least one undecided one. The `diff` names every undecided check. An `id` and a `reason` are text of 1 to 300 characters, and a hash is 64 lowercase hex characters.

The verdict is accepted once, for a `completed` run with nothing uncertain that waits for it, and only about the driver `frozen_driver` gives. A `graded`, `no-driver` or `unknown` record becomes the run's one final verdict, and `status` gives it as `equivalence`. The same record sent again changes nothing. An `unscored` record is kept as an event and leaves the run waiting. In each of these cases the call answers with the run's status.

`record_evaluation` is refused for a run started without an `evaluation_token_sha256`, for a `token` that does not hash to it, for a record with a fault, for a run that is not `completed` or has an uncertain effect, for a run that waits for no verdict, for a `frozen_artifact_sha256` that is not the frozen driver's, and for a record that differs from the verdict already recorded.

## Read the records and the frozen driver

`events` and `result` each take `run_id`. `status` takes a `run_id` or a `request_id`: the `request_id` of a `start` finds the run that `start` made, and asking starts nothing. With both, the `run_id` is used. A `run_id` the service does not know is refused, and a `request_id` that started no run is refused with `Unknown request_id`.

- `status` gives the run's `status`, `stage`, `reason` and `outcome_category`, whether an effect is uncertain (`uncertain_effect`), whether a worker is still stopping (`stopping`), the final verdict as `equivalence`, null until one is recorded, and the latest notes of each stage (`stage_notes`).
- `events` gives up to 500 events after the cursor `after`, each with `id`, `kind`, `data` and `time`, and the next `cursor`.
- `result` gives what `status` gives, the run's records, which are `worker_reports`, `accepted_handoffs`, `evaluator_verdicts` and `progress`, the `client_record` of the `start`, as it was sent, and `request`, the params of the `start` as the service received them. A run that an earlier version stored without its request has no `request`.

A run waits for a posted verdict when its `status` is `completed` and `progress` of `result` holds `"equivalence": "pending"`. The verdict must then name the run's frozen driver, the first artifact of the latest `emit` handoff that has one:

```python
from generative_driver.client import call
from generative_driver.client_interface import frozen_driver

result = call('result', {'run_id': run_id})
driver = frozen_driver(result['accepted_handoffs'])  # {'path': ..., 'sha256': ...}, or None
```

`path` is where the service keeps the driver. The client reads the files there, on the same machine. `sha256` is `generative_driver.integrity.digest` of that path.

A run that an earlier version of the tool started on a case is neither resumed nor graded: `resume`, `attach_device` and `record_evaluation` refuse it, and the client starts a new run.

## Stop or seal a run

`cancel` takes `run_id` and answers with the run's status. The run becomes `cancelled`, and a worker of it that still runs is told to stop: `stopping` is true until it has. A `completed` run that has its final verdict, or that waits for one, keeps its status.

`seal` takes `run_id` and answers `{"ok": true, "run_id": ..., "sealed": true}`. The same call again gives the same answer. It marks the run sealed and changes nothing else of it: not its status, its events, its verdict or its files. From then on `status` and `result` hold `"sealed": true`, and `resume` and `attach_device` are refused with `A sealed run does not resume or take a device: <run_id>`. A client seals a run whose records it has put away, so that nothing continues the run afterwards.

## Records on disk

The service keeps its records in its home. The home is the folder `default_home()` names when a client gives none. A client on the same machine may read what this section names, without asking the service.

`config.json` in the home holds the operator's settings. Its `executors` object holds, by the name of an executor, the settings a run has when its `start` gives no `executor_config`. The file may be absent.

`runs.sqlite3` in the home is the SQLite database of the service's records, and a client opens it read-only. A client reads these tables and columns:

| Table | Columns |
|---|---|
| `runs` | `id`, `request_id` |
| `events` | `seq`, `run_id`, `kind`, `payload`, `created` |
| `reports` | `run_id`, `stage`, `payload`, `created` |
| `handoffs` | `run_id`, `payload`, `created` |
| `verdicts` | `id`, `payload` |
| `progress` | `run_id`, `payload` |

A `payload` is JSON text. The rows are what `events` and `result` are made from. The rows of `handoffs` are read in the order they were written, which is SQLite's own `rowid`. The final verdict of a run is the row of `verdicts` whose `id` is the id of the run followed by `:final`.

`runs/<run id>/` in the home is the run's folder, and a client may read every file in it. `supplied/package-events.jsonl` there holds one JSON object on a line for each call a worker made on the driver package, with `actor`, `stage`, `operation`, `revision`, `attempt_id`, `assignment_id`, `package_sha256`, `parameters` and `result`. A line has `mcp` when the call came through a server of the package, and `observation` and `before_observation` when the device offers readings. Every file named `_agent_events.jsonl` under the run's folder is the transcript of one worker. An interpret worker works in a temporary workspace outside the run's folder, which its `stage.assigned` event names as `workspace`.

The service makes these folders directly in a run's folder: `supplied`, `work`, `accepted`, `checks`, `preparation`, `tool-calls`, `snapshots`, `replay-candidate`, `kit-analysis`, `interpret-tasks` and `device-first`. A client may add files and folders of its own directly in a run's folder under other names, and the service never changes or removes them.

A client may keep saved candidates for the replay executor in the folder `replay-candidates` in the home. The service never changes or removes that folder, and reads in it only a candidate a `start` names.

Once a run has stopped, with no worker of it still stopping, and is sealed, the client may remove the files of the run's folder and those temporary workspaces. `status`, `events` and `result` of the run answer as before without them.

## What a client imports

A client imports these modules of the tool and takes these names from them. `IMPORTS` of the interface module holds the same list.

- `generative_driver.client_interface`: `CALLS`, `IMPORTS`, `PROTOCOL`, `VERDICT_FIELDS`, `VERSION`, `device_errors`, `firmware_errors`, `frozen_driver`, `mission_errors` and `verdict_errors`.
- `generative_driver.client`: `call` and `default_home`.
- `generative_driver.integrity`: `digest`.
- `generative_driver.setup`: `SUPPORTED_EXECUTORS` and `resolve_executor`.
- `generative_driver.replay`: `candidate_config`.
- `generative_driver.toolkit`: `call_tool` and `resources_root`.
- `interface_runtime.engine`: `validate_model`.
- `interface_runtime`: no name.

Any listed module may be imported for where it is installed.

`default_home()` is the home `call` uses when it is given none.

`SUPPORTED_EXECUTORS` are the executors a `start` takes. `resolve_executor(executor)` gives the executor a `start` runs with, the default for none, and refuses one the tool does not have, as `start` does.

`candidate_config(config)` checks the saved candidate of a replay, given as `{"candidate": <directory>}`, and gives the `executor_config` of a `start` with the executor `replay`: the candidate's absolute path and the hash of its files, as `start` checks them.

`call_tool(name, arguments)` calls one of the tool's developer tools in this process, the ones `generative-driver tool` calls. `resources_root()` is the folder of the tool's installed assets.

`interface_runtime` is the runtime a driver package runs on. `validate_model` of `interface_runtime.engine` checks a device model without opening a transport.

Nothing else in the tool is part of this interface. The operator clears an uncertain effect through the command line or the MCP server, outside this interface.
