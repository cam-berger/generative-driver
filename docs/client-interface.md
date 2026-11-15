# Client interface, version 1

A client that brings its own device and mission asks four things of the tool: start a run on a device it supplies, replace that device, post one final verdict, and read the run's records and its frozen driver. This page says what the client sends, what it gets back and when the tool refuses. The module `generative_driver.client_interface` holds the same in code, and its `VERSION` is the number in the title.

The service is the configurator, the background owner of runs. A client reaches it on the same machine with `call(method, params, home)` of `generative_driver.client`. Every reply is a JSON object. A refusal is `{"ok": false, "error": ..., "reason": ...}`, and `reason` is a sentence. `call` itself answers `{"ok": false, "reason": ...}`, with no `error`, when the service runs other code than the client, when a reply is lost, and for a `ping` whose `ok` is false. The calls of this interface are `ping`, `start`, `resume`, `status`, `events`, `result`, `attach_device` and `record_evaluation`. Runs follow the protocol `assistive/1`.

## Start a run on a supplied device

A supplied device is one the client owns: the client starts it before the run and stops it afterwards, and describes it to the service with a device descriptor.

`start` takes:

| Key | Value |
|---|---|
| `goal` | What the driver is for. Text, required. |
| `device` | The device descriptor, below. |
| `effects` | The effects the client grants the run: any of `read`, `write`, `actuate` and `reset`. Without it, `read` alone. |
| `budget_seconds` | The run's time budget: positive, at most seven days. Without it, three hours. |
| `executor` | The worker runtime, `codex` or `replay`. Without it, `codex`. |
| `executor_config` | Settings of that runtime for this run, laid over the ones the operator configured. |
| `request_id` | Text of the client's choice. A `start` repeated with the same `request_id` and the same inputs starts nothing and finds the same run. |
| `evaluation_token_sha256` | The SHA-256, as 64 lowercase hex characters, of the UTF-8 bytes of a token the client keeps to itself. The token later proves who may replace the device and post the verdict. |

The device descriptor has these parts:

| Part | Value |
|---|---|
| `schema` | `device-descriptor/1` |
| `kind` | `provided-emulator`, the one kind of supplied device today. |
| `control` | An object whose `endpoint_file` is the path of the file through which the service reaches the device's control endpoint. |
| `session_id` | The name of the device's running session. Text, not empty. |
| `binding` | The connection the run's device operations go over: a host and port, a serial port, an FTDI URL, or a USB vendor and product id. The service leases it to the run. |
| `image_sha256` | The hash of the image the run is started on. |

`device_errors(device)` gives a descriptor's faults before it is sent. The tool asks the control endpoint for six things only: `status`, `observe`, `select_condition`, `set_running`, `replace` and `release`. This version does not yet say what the endpoint file holds or what each answer must be.

The reply is `{"ok": true, "run_id": ..., "duplicate": false}`, or `duplicate` true for a repeated `request_id`. When its stages are done the run ends with the status `completed` and waits for the client's verdict. Whenever the run stops, the service sends `release`. It never stops the device: the client does.

`start` is refused without a `goal`, for a budget, an effect or an executor outside the values above, for a `request_id` already used with other inputs, for a descriptor with a fault, for a token hash that is missing or of another form, and for a `binding` that another run or session still holds. The service holds no password, and refuses a request with a key that names one.

`resume` with `run_id` continues a run that stopped before it was complete (`blocked`, `failed` or `cancelled`) and answers with its status. It is refused while a worker of the run is still stopping, while an effect is uncertain, and once the budget is spent, unless `budget_seconds` with a `budget_reason` raises it. A stage resumes only on a device whose `status` answers healthy, with the session and binding the run recorded. Otherwise the resume is refused, and the run stays stopped until that device answers so again or `attach_device` records a new one.

`ping` takes nothing. Its `ok` is true when the service runs the same code as the client.

## Replace a device

`attach_device` takes `run_id`, `token` and `device`, the descriptor of a new device. It is for a stopped run whose device was stopped or replaced outside the run. The service checks that:

- the run was started on a supplied device, and `token` hashes to its `evaluation_token_sha256`;
- the descriptor has no fault, and its `image_sha256` is the one the run was started on;
- the device's `status` answers healthy, with the `session_id` and the `binding` the descriptor names;
- the run is stopped, no worker of it is still stopping, and no effect is uncertain.

Where a check fails, the call is refused. Otherwise the service records the new session and binding and answers with the run's status. It does not resume the run: send `resume` next.

## Post one final verdict

`record_evaluation` takes `run_id`, `token` and `equivalence`, the verdict record. The record has exactly these fields:

| Field | Value |
|---|---|
| `schema` | `equivalence/1` |
| `protocol` | `assistive/1` |
| `basis` | `evaluator` |
| `method` | How the evaluator graded. `fresh-final`: it ran the frozen driver on a fresh device of its own. `recorded-reuse`: it judged the fresh reuse the run recorded. |
| `status`, `equivalent` | `graded` or `no-driver` with true or false, `no-driver` only with false. `unknown` or `unscored` with null. |
| `checks` | A list of `{id, passed}`. `passed` is true or false. It is null for a check the evaluator could not decide, only in an `unknown` record or beside a failed check in a `graded` one. |
| `diff` | A list of `{id, reason}`: each difference with its reason. |
| `passed`, `total` | The number of checks that passed, and of all checks. |
| `mission` | Null, or how fresh reuse fared: an object with any of `name`, `passed`, `reason` and `fault`. Its `passed` is true, false or null, and the rest is text of at most 300 characters. |
| `frozen_artifact_sha256` | The hash of the driver the verdict is about. Null when the run froze none. |
| `capabilities_sha256` | A SHA-256 or null. The service checks only its form. |
| `evidence_sha256` | The hash of the evidence the client sealed its grade in. Null only in an `unscored` record. |

`verdict_errors(record)` gives a record's faults as sentences, and the service refuses by the same rules. An equivalent driver has at least one check, every check passed and an empty `diff`. Every other record has a `diff` that is not empty. An `unknown` record has no failed check and at least one undecided one. The `diff` names every undecided check. An `id` and a `reason` are text of 1 to 300 characters, and a hash is 64 lowercase hex characters.

The verdict is accepted once, for a `completed` run with nothing uncertain that waits for it, and only about the driver `frozen_driver` gives. A `graded`, `no-driver` or `unknown` record becomes the run's one final verdict, and `status` gives it as `equivalence`. The same record sent again changes nothing. An `unscored` record is kept as an event and leaves the run waiting. In each of these cases the call answers with the run's status.

`record_evaluation` is refused for a `token` that does not hash to the run's `evaluation_token_sha256`, for a record with a fault, for a run that is not `completed` or has an uncertain effect, for a run that waits for no verdict, for a `frozen_artifact_sha256` that is not the frozen driver's, and for a record that differs from the verdict already recorded.

## Read the records and the frozen driver

`status`, `events` and `result` each take `run_id`. A `run_id` the service does not know is refused.

- `status` gives the run's `status`, `stage`, `reason` and `outcome_category`, whether an effect is uncertain (`uncertain_effect`), whether a worker is still stopping (`stopping`), the final verdict as `equivalence`, null until one is recorded, and the latest notes of each stage (`stage_notes`).
- `events` gives up to 500 events after the cursor `after`, each with `id`, `kind`, `data` and `time`, and the next `cursor`.
- `result` gives what `status` gives, and the run's records: `worker_reports`, `accepted_handoffs`, `evaluator_verdicts` and `progress`.

A run waits for a posted verdict when its `status` is `completed` and `progress` of `result` holds `"equivalence": "pending"`. The verdict must then name the run's frozen driver, the first artifact of the latest `emit` handoff that has one:

```python
from generative_driver.client import call
from generative_driver.client_interface import frozen_driver

result = call('result', {'run_id': run_id})
driver = frozen_driver(result['accepted_handoffs'])  # {'path': ..., 'sha256': ...}, or None
```

`path` is where the service keeps the driver. The client reads the files there, on the same machine. `sha256` is `generative_driver.integrity.digest` of that path.

## What a client imports

- `generative_driver.client_interface`: `VERSION`, `PROTOCOL`, `CALLS`, `VERDICT_FIELDS`, `verdict_errors`, `device_errors` and `frozen_driver`.
- `call` and `default_home` of `generative_driver.client`. `default_home()` is the home `call` uses when it is given none.
- `digest` of `generative_driver.integrity`.

Nothing else in the tool is part of this interface. The operator clears an uncertain effect and cancels a run through the command line or the MCP server, outside this interface.

## Limits today

The service accepts a supplied device only for a run that names a registered case. The verdict record's `method` and `capabilities_sha256` and the descriptor's `kind` are due to change in a later version of this interface.

<!-- legacy-benchmark:begin -->
Today the service takes these from a registered benchmark case: the `start` keys `case`, `case_options` and `admission`, the run's inputs and its mission, the pinned image that `image_sha256` must equal, the effects the case requires, which `effects` must grant, and the grading method that fits the case, which `method` must name. Features 04 and 05 of the [untangle Run](features/untangle/breakdown.md) replace the case with inputs and a mission the client supplies.
<!-- legacy-benchmark:end -->
