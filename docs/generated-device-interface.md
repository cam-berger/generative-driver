# Generated-device interface

Status: specified on 2026-11-02 from the interface review in the [benchmark baseline spec](../.scratch/benchmark-baseline/spec.md), and enforced by `generated_device` ([ADR 0007](adr/0007-one-validator-defines-a-generated-device.md)), whose validator `seal_device` and `open_device` run. The bench harness's part, under [What a trial and a verdict reveal](#what-a-trial-and-a-verdict-reveal), comes with the harness.

This is what the private device generator must hand over for a generated device to be sealed, graded and admitted. Each rule is checked in one of three places:
- by the validator in `generative_driver.benchmark_support`, which `seal_device` runs before writing anything and `open_device` runs before returning a device;
- by qualification's emulator runs;
- where neither can check it, by the generator's own tests, as a [generator duty](#generator-duties).

The validator holds no device's design, so the generator may import it ([ADR 0005](adr/0005-keep-the-device-generator-private.md)). Terms follow [CONTEXT.md](../CONTEXT.md).

## Set bundle

A set is a directory in the answer vault ([ADR 0003](adr/0003-generated-devices-register-by-set.md)), written only through `seal_device` and `write_manifest`:

| Path | Content |
|---|---|
| `manifest.json` | The plaintext manifest, `generated-set-manifest/1` |
| `devices/<id>.inputs.enc` | Sealed `{"image_base64", "mission"}`: what a trial's worker receives |
| `devices/<id>.answer.enc` | The sealed answer key, `generated-answer-key/2`, read only by grading and qualification |
| `qualification.enc` | The sealed qualification record, written by `benchmark set qualify`, never by the generator |

Each sealed file is a `generative-driver-groundtruth/1` envelope: AES-256-GCM under a key derived from the set's one password. Pass `seal_device` the password exactly as the operator's password file holds it, without surrounding whitespace, since `benchmark set qualify` strips the file's. The set's secret seed, if the generator keeps one, stays in the answer vault outside the bundle. Registering and qualifying a set are the operator's steps, in the [authoring guide](benchmark-case-authoring.md#generated-sets).

## Manifest

`{"schema": "generated-set-manifest/1", "set": <registration name>, "devices": [<entry>, …]}`, with at least one device and distinct ids. It stays plaintext and keeps each device's level and framing for the qualification sample and the scorecard; the answer vault, unmounted while workers run, protects it. An entry:

| Field | Rule |
|---|---|
| `id` | Eight random lowercase hexadecimal digits |
| `level` | Difficulty level, 1 to 5 |
| `framing` | `text-lines`, `binary-frames` or `register-access` |
| `image_sha256` | SHA-256 of the image bytes |
| `mission_sha256`, `answer_key_sha256` | SHA-256 of the mission's and the answer key's UTF-8 JSON with sorted keys, `,` and `:` separators and no NaN |
| `inputs`, `answer` | `{"path", "sha256"}`: the sealed file's relative POSIX path inside the set and the SHA-256 of its bytes |

## Framing and image

A device's framing is how its commands and replies travel over its UART:

| Framing | Commands and replies |
|---|---|
| `text-lines` | Text lines, each ending with a line feed (`0a`) |
| `binary-frames` | Binary frames carrying their length and a checksum |
| `register-access` | Numbered register reads and writes in binary messages |

Every reply is self-delimiting, refusals included: a text reply is whole lines, and a binary reply's length follows from its own header or from the request.

The image is the raw firmware binary, not an ELF file, that the recipe's setup loads from `$bin`; a trial's worker receives the same bytes. It is built with the Arm GNU Toolchain 14.2 for Cortex-M, as [asset_build.py](../src/generative_driver/benchmark_support/asset_build.py) builds the hand-made cases. Command names appear as plain strings at easy levels and are encoded at hard ones.

## Public mission

A generated device's mission is a `benchmark-public-mission/1` with `case_id` `"generated"`. It states effects only; whatever a worker must recover from the firmware stays out.

| Field | Rule |
|---|---|
| `tasks` | `{<name>: <task>}`, each name matching `[A-Za-z][A-Za-z0-9_]{0,63}` |
| task `meaning` | One sentence saying what the application wants the task to do |
| task `inputs` | `{<name>: {"type"}}`, the type `integer` or `number`, with no other key |
| task `outputs` | `{<name>: {"unit"}}`, the unit of a numeric output, `1` when it has none, with no other key |
| `reuse_objective` | One sentence: the fresh-reuse worker's goal |

A generated mission has no `operating_conditions` and no task `sampling`. Inputs are numeric because capability binding takes no bytes, and takes a string only from a set the driver declares. The validator checks these shapes; that the wording names no valid range, refusal condition, refusal reason, framing, reply format or firmware command is a generator duty.

## Answer key

`generated-answer-key/2` has every field below and no other, beside the `artifact_sha256` the evaluator adds:

| Field | Content |
|---|---|
| `schema` | `"generated-answer-key/2"` |
| `nonce` | 64 random lowercase hexadecimal digits, so the key's digest in the manifest cannot be confirmed by guessing the key |
| `recipe` | The Renode recipe, below |
| `state` | The state cells, `{"c1": {"address", "width"}, …}`: named `c1` to `cM`, each a 32-bit address and a width of 8, 16 or 32 bits |
| `reset` | Each cell's value after a verified reset |
| `machine` | The device's state machine, below |
| `sequences` | The hidden sequences, `[{"id", "steps"}, …]`, the *i*-th named `s<i>` |
| `mission` | `{"state"}`: the values that the mission's `reuse_objective` leaves in the cells it determines, whatever state the device started in. Fresh reuse is judged against them |
| `references` | `{"a", "b"}`, each `{"model", "capabilities"}`: an `interface-model/6` driver model and its `benchmark-capabilities/2` mapping of the mission's tasks |
| `mutants` | `[{"id", "model", "capabilities", "expected_failed_checks"}, …]` |

A cell's value is an unsigned integer of the cell's width.

**Recipe.** The recipe has these fields:
- `source_files` holds Renode files by name, each name matching `[A-Za-z0-9_-]+\.(resc|repl|cs)`.
- `setup` names the `.resc` that runs last, and `includes` lists the source files to include before it, in order. The evaluator writes the source files into a private directory and runs `$bin=<image>; $uart=<port>; include <each include>; include <setup>`. The setup loads `$bin`, serves the UART as a TCP server on `$uart` and starts the machine. The evaluator waits for Renode's log to say `Machine started`.
- `reset_commands` are monitor commands that leave the firmware booted to idle, the last of them `emulation RunFor` over a boot interval. Renode keeps RAM across `machine Reset`, and the evaluator reads the reset state as soon as the reset commands return, with the emulator paused. Without the boot interval, that read shows the previous run's RAM.
- `observations` names at least one monitor read for the evaluator's health check: `{<name>: {"command", "width", "count"}}`, with a width of 8, 16, 32 or 64 bits and a count of 1 to 4096, or `{<name>: {"kind": "scalar", "command", "unit"}}`. It must read the same value on every boot, since recovery compares a replacement's reading with the first boot's, and each grade reads it once on its fresh session, before any reset or call. Pick a read that is stable at boot, such as a word of the image's vector table, not the program counter or anything else that running firmware or a timer moves: the machine has been started when this read comes, set qualification compares a device's readings across its runs, and a difference leaves the set pending.
- `backend`, if present, is `renode`. A generated recipe has no `stimuli`.

Every monitor command is a single command, without a line break, `;`, NUL or braces, and finishes within the evaluator's 5-second monitor timeout.

**State machine.** `machine` is `{"initial", "states", "transitions"}`:
- `states` maps each state's name to the cells' values in that state, `{<name>: {"c1": <value>, …}}`, and distinct states hold distinct values.
- `initial`'s values are `reset`.
- A transition is `{"id", "from", "task", "answer", "to"}`, with a distinct id, `from` and `to` among the states, a mission task and an answer. Two transitions may share a start state, task and answer; the inputs choose between them.

These names stay inside the key.

**Step.** A step is `{"task", "inputs", "grants", "answer", "state", "transition"}`, with `outputs` on a value step of a task that declares outputs and `refusal_rx_hex` on a refusal step:

| Field | Content |
|---|---|
| `task`, `inputs` | The call: a mission task and a number for each of its declared inputs, an integer for an `integer` input |
| `grants` | The effect grants the grader gives the call: distinct among `read`, `write`, `actuate` and `reset`. Reads need none, and `reset` matters only for a channel that resets the device when it is opened, which a generated device's UART does not. A task that changes the device's state, resetting it included, needs `write` or `actuate` |
| `answer` | `value`, `refusal` or `no_reply` |
| `state` | The cells' values after the call: those of its transition's end state |
| `transition` | The id of the transition the call takes |
| `outputs` | `{<name>: {"value", "tolerance", "unit"}}` for every output the task declares: a finite number in the mission's unit, with a tolerance of at least 0 |
| `refusal_rx_hex` | The device's complete refusal reply, in lowercase hexadecimal |

**Answers.** The grader calls each step through the frozen driver package, with the step's grants. The runtime first runs the driver's own identity operation, then reads the result as one of:
- `value`: the call succeeded.
- `refusal`: the driver reported a model or operator fault, and either declined the call without sending it to the device or ended it with an exchange that completed with exactly `refusal_rx_hex`.
- `no_reply`: the call's last exchange timed out with nothing received.
- `error`: anything else, which never matches.

A step passes when its answer, its outputs and every cell after it match. Receive modes that wait for a complete reply (`exact`, `until`, `lines` and `frame`) read a partial reply followed by silence as no reply, since a timed-out exchange records none of the bytes it received; `up_to` completes on whatever arrives first. A command the device never answers is a `value`, sent by an exchange that expects no bytes. `no_reply` is for a command the device answers in some states but not in others.

**References.** Each reference is a correct driver that grading must pass:
- It reports a refused request as a failure, through an expectation that rejects the refusal reply, for example `reject_line_prefix`. The refusal reply is its call's last exchange.
- It forwards every request a refusal step makes, so qualification checks the device's refusal bytes, not the driver's own bounds.
- Its identity operation is a read that sends a command the other reference's does not.

On a `text-lines` device, one reference reads each refusing task's reply with `until` on the line feed and the other with `lines`, ending on the first line. A `lines` read ends on a line that matches one non-empty word or prefix, so every reply to a command begins with one word, such as the command's: the test fixture replies `MODE OK` and `MODE ERR`. Devices with the other two framings have no such rule, since a correct driver must read a whole binary reply to check it.

## Rules the validator checks

Beyond each field's own shape, the validator checks that the parts agree:

1. **Mission.** The steps call exactly the mission's tasks. Each step's inputs are its task's declared inputs with their types, and each value step's outputs are its task's declared outputs, in the mission's units. Both references' and every mutant's capability mappings cover exactly the mission's tasks.
2. **Grants.** All steps of one task carry the same grants. A task with a refusal step or a `no_reply` step also has a value step, so the reference runs prove its grants and its replies. Without this, a refusal step whose grants left out its task's effect would pass any driver: the runtime refuses an ungranted effect before sending, which grades as a matching refusal.
3. **Machine.** Each step's `state` is its transition's end state. Each sequence's first step takes a transition from `initial`, each later step's transition starts where the previous step's ended, and each step's task and answer are its transition's. Every transition is taken at least once.
4. **Reads.** Every transition of a read task, one whose steps grant nothing beyond `read`, starts and ends in the same state.
5. **Refusals.** The device has at least one refusal step. Every refusal transition starts and ends in the same state, so a refusal leaves the cells unchanged whether the device or the driver makes it. A `text-lines` device's refusal replies end with a line feed.
6. **References.** Every refusal step's inputs lie within both references' parameter bounds, so both forward it. The two identity operations send different commands. On a `text-lines` device, the operations the two references map each refusing task to end with an `until` exchange and a `lines` exchange respectively.
7. **Cells.** Every cell holds a value other than its reset value after some step.
8. **Outputs.** At least one value step has an output, so `wrong-scale` and `constant-output` have something to break.
9. **Mutants.** Mutant ids are distinct and include `wrong-scale`, `constant-output` and `wrong-state`. Each mutant expects a nonempty list of distinct check ids of its own key to fail.
10. **Recipe.** `setup` and its sources are present, and the last reset command is `emulation RunFor`.
11. **Bundle.** The entry's hashes are those of the image, mission and key it seals, and each sealed file's hash is that of its bytes.

## Rules only an emulator run can check

`benchmark set qualify` runs both references on every device, and every mutant on the first two devices by id of each level and framing ([qualification](benchmark-case-authoring.md#generated-sets)). Together with the validator's rules, these runs enforce:

| Obligation | Checked by |
|---|---|
| The firmware gives every step's answer, outputs and state | Both references pass every device |
| Reset leaves the firmware booted and idle | Rule 10's boot interval. A reset whose state differs from `reset` also leaves its sequence undecided, and an undecided reference fails qualification |
| Reading changes nothing | Rule 4, and rule 6's two identity commands, which run before every reference call |
| Every correct driver captures a refusal as the same bytes | Self-delimiting replies and rule 6. Both references must forward each refused request and, on text lines, capture `refusal_rx_hex` in two receive modes |
| Cells are real | Rule 7: Renode reads an unmapped address as 0, so a misplaced cell fails the references |
| The hidden sequences catch wrong drivers | Every sampled mutant fails, on at least its expected checks |
| A boot reads the same every time | Each grade records its startup observation, and a device's graded runs must all show the same one |

A set is registered in `resources/registrations/sets.json`, `generated-set-registrations/2`: `{"schema", "sets": {<name>: {"manifest_sha256", "scoring"}}}`, where `scoring` is true for a scoring set and false for a development set.

Admission reads the whole record, not only the device it is asked about. Beyond the [record's identity and status](benchmark-case-authoring.md#generated-sets), it refuses a set whose record holds a failure; whose devices or sample are not the manifest's; where any device's two references did not both pass; where a mutant row was not rejected, repeats another or lies outside the sample, or a sampled device lacks a required mutant; whose runs are not exactly each device's two references and each mutant row; where any run's `native_process_observed` is not `true`; or where a device's runs show boot readings (a boot reading is the startup observation's values, read at the start of a grade) that differ, are missing or are empty. A record does not list a key's mutants, so a mutant dropped beyond the required ones is not detected. These checks read the record correctly; only a real qualification shows that Renode ran.

## Generator duties

Nothing in this repository can check these, so the generator's own tests must:
- No read command changes the state, including commands no reference uses.
- Each step's inputs belong to its transition.
- The firmware applies every state change before it replies, since the emulator pauses when a call returns and the evaluator then reads the cells.
- The firmware sends nothing unprompted: no boot banner, and no late or repeated reply.
- No reference answer depends on telling a partial reply from silence.
- The mission's wording names no valid range, refusal condition, refusal reason, framing, reply format or firmware command.
- The cells in `mission.state` hold those values after the objective, whatever state the device started in.
- Device ids are drawn at random.

## What a trial and a verdict reveal

A trial's worker receives the image and the mission, and a pin that names the set, its manifest hash, the device id, and the hashes of the image, the answer file and the qualification record, never the level or framing. The bench harness holds a suite's manifest, qualification record and sealed files in memory, and waits until the set's folder is out of reach before it starts the suite. The operator closes the vault by hand; the harness only watches the path.

A verdict publishes each check's id, whether it passed, and a reason for each check that failed or was undecided. The ids are `<sequence>/<step>/answer`, `<sequence>/<step>/out:<output>` and `<sequence>/<step>/state:<cell>`, with steps numbered from 1. They show:
- how many sequences, steps and cells a device has;
- which steps returned values, and their output names, so often which task such a step called.

They show no expected value, input or state name.

## Versions and the worked example

From this version on, a change to any rule here changes the version of the schema it touches. Version 1 of the answer key is refused. The manifest keeps version 1 with the eight-digit id, since no set was written before it. The public test fixture, [tests/generated_fixtures.py](../tests/generated_fixtures.py), is the worked example, and the validator accepts it.
