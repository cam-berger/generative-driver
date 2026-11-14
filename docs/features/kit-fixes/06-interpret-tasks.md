# 06. interpret-tasks

## Intent

Interpretation costs more than any other stage, and the cost is in reading. On 27 October it took 47% of all tokens, and 99% of those were input, although the firmware images were 0.8 to 21 KB. One worker runs Ghidra inside its own session, reads exports that have no size limit, and writes the whole model in one long, growing context. A repair starts a new worker that runs Ghidra again, because nothing keeps the analysis.

After this Feature, the configurator makes the firmware analysis itself, once per run, with an index of functions and likely command dispatchers (the kit analysis). Every interpret worker of that run reads it and does not redo it. Interpretation can also run as tasks, each a fresh worker with small inputs, run one at a time:
- an inventory lists the commands, their handlers, the framing and the identity command;
- one handler task per command writes that operation;
- the configurator assembles the pieces into one model;
- a cross-check reviews the assembled model for behaviour that spans commands, such as modes or set-then-commit, and may correct it.

A repair in task mode redoes only the handler tasks whose operations the device contradicted, then assembles and cross-checks again.

The task mode is opt-in. Its effect on cost and driver quality is a hypothesis until the human measures it in model runs, and the benchmark, which is not edited in this Run, cannot measure it. So the single worker stays the default, now handed the kit analysis, and each task records its token use for that comparison. Whatever the kit cannot do for itself falls back to today's path, so no run fails or stops because of it.

## Behaviours

- **06-B1** — In a generic run that names `ghidra_home` and `java_home`, the configurator makes the kit analysis of the acquired image before the first interpret worker starts, when it can decide how to load the image:
  - an ELF whose machine type the kit maps to a Ghidra language (at least 32-bit ARM, AArch64 and Xtensa), with Ghidra's ELF loader;
  - or a raw image for which the Cortex-M map check gives a decisive mapping with the vector table at the image's first byte, with that load address and the Cortex-M prescript. A decisive mapping with a header or with the table further in counts as loading that cannot be decided.

  It runs the existing Ghidra helper and exporter in a child process.
  - The process stops at the helper's own limit or when the run's budget runs out.
  - A cancel or a service shutdown stops it and every process it started at once, as Feature 04 does for a tool call. The run then ends as cancelled, or stopped by the shutdown, without falling back.
  - Events record when the analysis starts and ends.

  The helper's working folders may be a fresh folder under the system temporary folder, because the helper refuses a path with whitespace and the default macOS home has one. The export, the helper's log and the decision are kept in the run's folder, outside every worker's workspace. The decision records the processor, loader, load address and why.

  Every later interpret worker of the run reuses that analysis: repairs, resumes and every task. A run makes it at most once per image, Ghidra and Java installation, analysis scripts and load decision. A failure the helper reported, or a timeout, is recorded once, and later attempts fall back (06-B2) with that cause. An analysis stopped by a cancel or a shutdown is made again on resume.
- **06-B2** — When the kit cannot make the analysis, interpretation goes on as today, with the analysis kit supplied for the worker to run. The causes are:
  - Ghidra or Java is not named;
  - the image's loading cannot be decided;
  - the helper fails or runs out of time.

  The interpret stage's notes say which cause applied and what the helper reported. It never stops the run.
- **06-B3** — From the export, the configurator writes the index as `ANALYSIS_INDEX.json`:
  - for each function: its address, name, the size of its decompiled code, its callers and callees, the strings it references, and its switch tables;
  - a ranked list of dispatcher candidates, each with the evidence that ranked it (switch tables with their case values and targets, command-like strings and their references).

  The index is at most 2 MiB; what it leaves out to stay under the bound, it says. The same export always gives the same index bytes.
- **06-B4** — In the default mode, the run's single interpret worker gets the kit analysis as sealed inputs: the index and the export. Its `TASKS.json` instructions say that the analysis is made and how the image was loaded, and to rerun the supplied analysis kit only if the evidence contradicts that loading. The analysis kit stays supplied for that case. The export does not become part of the worker's frozen output.
- **06-B5** — A generic run started with `interpretation` set to `tasks`, whose kit analysis was made, runs each interpret attempt as tasks (06-B6 to 06-B9).
  - `interpretation` is a start parameter of the configurator's `start` request and of the MCP `driver_start`, with the values `single` (the default) and `tasks`. Any other value is refused at start.
  - A run started with `tasks` whose kit analysis could not be made runs the single worker of 06-B4, and the stage's notes say so.
- **06-B6** — The inventory is a fresh worker. Its sealed inputs are:
  - the index;
  - the decompiled code of the top-ranked dispatcher candidates, at most 256 KiB;
  - the export's strings table, at most 256 KiB;
  - the run's `TASKS.json` and the acquisition evidence it would get today (`serial-listen.json`, `BINDING_CONTEXT.json`);
  - its instructions.

  It writes `inventory.json`: the model's schema tag, the device name, the channel and framing it found, which command identifies the device, and each command with a name, its handler functions' addresses and the evidence for each. The identity command is one of the commands, and there are at most 64, the model's limit. An inventory that lists no command or cannot be read sends that attempt to the single worker of 06-B4, and the notes say why.
- **06-B7** — For each command in the inventory, a handler task runs as a fresh worker, one at a time, the identity command's first. Its sealed inputs are:
  - the inventory's schema tag, device name, channel and framing;
  - its own command's entry;
  - for every task after the identity command's, the identity operation that task wrote;
  - the decompiled code of its handler functions and of the functions they call, nearest first, at most 256 KiB, with a note of what was cut;
  - the strings those functions reference;
  - the run's `TASKS.json`;
  - the model document, the contract guide and tool (Feature 05), `validate.py` and `qualify.py`;
  - its instructions.

  It writes a fragment: the operation's model entry, its provenance, its replies and its contract entry. A task whose worker completed but wrote no usable fragment leaves its command out of the assembly, with a note. If the identity command's fragment is unusable, or no fragment is usable, the attempt goes to the single worker of 06-B4, and the notes say why.
- **06-B8** — The configurator assembles the fragments into `model.json`, `replies.json` and `contract.json`.
  - The schema, device, channel and identity come from the inventory.
  - Each operation, its provenance, its replies and its contract entry come from its fragment.
  - A fragment validates on its own when a model made of the inventory's schema, device and channel, the identity operation and that fragment's operation validates. A fragment that does not, or whose operation name is already taken, is left out with a note.
  - The assembled model is then validated as a whole, and the result goes to the cross-check.
- **06-B9** — The cross-check is a fresh worker. It starts from the assembled files, which it may change, together with:
  - the whole-model validation result;
  - the inventory;
  - the index;
  - the decompiled code of every handler function, at most 256 KiB;
  - every task's notes;
  - the run's `TASKS.json`;
  - the model document, the contract guide and tool, `validate.py` and `qualify.py`.

  It checks behaviour that spans commands and the consistency of the whole, and writes the final `model.json`, `replies.json`, `contract.json` and `NOTES.md`. Whatever files it leaves are the attempt's output, collected and checked as an interpret worker's output is today, and handed off as the interpret stage's one handoff. If the cross-check ends without a usable result, the assembled files are handed off as they are, with a note.
- **06-B10** — Each task is recorded under the stage attempt's one assignment, never as a stage attempt of its own:
  - an event when it starts and when it ends, with its kind (`inventory`, `handler` with its command, or `cross-check`), its workspace, its status and its token usage;
  - a `reports` row of its own, under an id of its own, whose payload names the assignment and the task kind, so a stage's token total sums every task;
  - its transcript in its workspace.

  `stage.assigned` stays one per stage attempt. The run's `status.worker` adds `task`, naming the running task.
  - A cancel stops the running task and starts no other. The run's budget bounds each task as it bounds a stage worker today.
  - A task whose worker cannot be launched, exits with an error or runs out of budget stops the run exactly as a stage worker's does today. Only a task whose worker completed but left unusable output is left out or falls back.
  - A resume runs an interrupted first attempt's tasks again from the inventory, and repeats an interrupted repair (06-B11). Both reuse the kit analysis.
- **06-B11** — When probe or ground sends a task-mode run back to interpretation, the repair starts from the previous attempt's final, cross-checked model:
  - A difference names an operation by where the runtime says it failed: the `operations.<name>` prefix of a defect's location, or else the operation of the last step in its transcript. A failure inside the identity operation's steps therefore names the identity operation.
  - The repair runs again only the handler task of each operation a difference names and a handler wrote. Each gets its previous fragment, its operation as the previous final model has it, and those differences.
  - Every other operation, and the channel, come from the previous final model, and the re-run operations replace theirs. The configurator assembles that.
  - The cross-check runs again with the previous final model and every difference. That includes the differences that name no operation (the channel, transport, identity field or provenance) and those about operations no handler wrote.

  The inventory and fragments of each attempt are kept in the run's folder, with their hashes in the interpret handoff's notes and not among its artifacts, so probe and ground never receive them. The kit analysis is not made again. A repair after an attempt that fell back to the single worker repairs the single worker's way, as today.
- **06-B12** — Benchmark runs prepare, run and check interpretation exactly as today: no kit analysis, no index, no tasks, and no change to what their workers receive.

## Interface and seams

New behaviour goes in modules of its own, which the configurator calls from the generic branches of its interpret preparation, its stage launch and its interpret check. Benchmark runs never reach those branches.

These stay as they are, because benchmark runs share them:
- the shared interpret brief, the model documents, the interpret prompt, `validate.py` and `qualify.py`;
- `firmware_analysis_inputs`;
- the Ghidra helper's command line and result, and the exporter's outputs.

The task instructions are files of this Feature's own, outside `resources/toolchain/skills/`. Benchmark code and benchmark tests are not edited.

The builder chooses:
- how the configurator runs the helper (Feature 04's tool child process is one way);
- the index's ranking rule;
- the instruction texts;
- the folder layout under the run;
- the event kinds' names.

The size bounds (2 MiB, 256 KiB) are module constants that tests may shorten.

Tests attach in these places:
- 06-B1 to 06-B4: the configurator, with a stand-in analyzer, which is a `ghidra_home` whose headless launcher writes a canned export, and with canned ELF and raw images; one 06-B1 test uses a home whose path contains a space;
- 06-B5 to 06-B11: the configurator, with scripted task workers that recognise their task from their workspace's instructions; one test runs a task-mode run through the service, for `status.worker`, cancel and resume.

No test needs a real Ghidra, a model or a device. The smoke, which names no Ghidra, keeps its single-worker path and passes as today.

`docs/codex.md` says how to turn on the task mode and that it is unmeasured. `.scratch/benchmark-review/issues/13-kit-changes-not-adopted.md` records that benchmark runs get neither the kit analysis nor the task mode.

## Out of scope

- Making the task mode the default, which waits for the human's measurements, and any model run.
- Running tasks in parallel.
- Reusing the tasks an interrupted attempt had finished when it is resumed, and keeping the kit analysis across runs.
- Architectures beyond those 06-B1 names, and analyzers other than Ghidra.
- A `doctor` check for Ghidra, and removing interpret workspaces from the system temporary folder.
- A time limit per task other than the run's budget.
- The register-model front ends, and runs without exactly one acquired image (Feature 07).

## Deviations
