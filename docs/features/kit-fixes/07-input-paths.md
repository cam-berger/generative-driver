# 07. input-paths

## Intent

A generic run reaches a driver along one path today: exactly one firmware image, interpreted alone. Everything else acquired beside that image stops at the acquire stage:
- a datasheet's pages are handed on but never forwarded;
- a protocol specification is not even handed on;
- a file the operator passes with the run is copied into the acquire workspace and goes no further.

A run with no image, or with several, skips interpretation. The device stages that follow have nothing to test, and the run ends "no driver emitted", even when the device answers on its own port.

After this Feature, documents acquired beside the firmware, fetched or supplied by the operator, reach the interpreter as evidence of meanings, units and framing. The firmware stays the truth. Where a document disagrees with the firmware or the device, the firmware or the device wins, and the disagreement is noted.

A generic run with no single image but with a device binding interprets device first:
- The interpret worker writes the model from the device's replies to operations it declares read-only, together with the listen evidence and the documents.
- It hands the model on as an interpret handoff does today.
- Probe, ground, emit, reuse and repairs then work unchanged, so such a run can end with a driver and a self-evidence verdict.

Interpretation stays the one stage that writes models, and repairs keep coming back to it. That is why it, rather than probe, gets the device in this case (docs/adr/0009-device-first-interpretation.md).

## Behaviours

- **07-B1** — A generic acquire worker can import a document the operator passed with the run. A new acquire tool takes the name of a file in the acquire workspace's `inputs/` folder and refuses any other path.
  - A PDF becomes page text with an index of its pages, through pypdf loaded when the tool runs. Without pypdf, the tool answers unavailable with the reason.
  - A `.txt` or `.md` file in UTF-8 becomes one page.
  - Any other file is refused with the reason.

  The original file and its pages are hashed and recorded as acquire artifacts, so the acquire handoff carries them.
- **07-B2** — A protocol specification that `acquire_protocol_spec` fetched is recorded and handed on with the acquire handoff, as a datasheet's pages are: its source files and, when it has them, its sections.
- **07-B3** — In a generic run, every interpret worker gets the supplementary documents of the latest acquire handoff as sealed inputs, under `documents/`. They are the datasheet pages, the imported documents, and each protocol-specification source: its sections when it was split, otherwise the source itself when it decodes as UTF-8. This holds whether or not the run has an image.
  - `DOCUMENTS.json` lists each document: its kind (`datasheet`, `protocol-spec` or `supplied`), its source (URL or file name) and sha256, and each page or section with its path and size. A source that is neither split nor UTF-8 is listed as left out.
  - The documents' text is forwarded, never a PDF. Their total is at most 2 MiB. Over the bound, pages are taken round-robin by page number across the documents (every document's first page, then every second page, and so on) until the next page would pass the bound. Each document therefore loses pages only from its end, and none is pushed out by another. `DOCUMENTS.json` names each page left out.
  - The run's `TASKS.json` says three things: the documents are evidence of meanings, units and framing; the firmware or device evidence wins where they disagree, and the disagreement goes into `NOTES.md` and the contract's uncertainties; and provenance may cite a page by its path.
  - Documents do not become part of the worker's frozen output.
- **07-B4** — In task mode (Feature 06):
  - The inventory gets `DOCUMENTS.json` and the documents' text, at most 256 KiB, chosen round-robin as in 07-B3, and may cite document pages in each command's evidence.
  - A handler task gets `DOCUMENTS.json` and the pages its command's evidence cites, at most 256 KiB.
  - The cross-check gets `DOCUMENTS.json` and every page any command cites, at most 256 KiB.
  - What a bound leaves out, the task's inputs say. A citation that names no forwarded page is ignored, with a note.
- **07-B5** — A generic run whose front end is the interface interpreter, whose latest acquire handoff has no single image (none, or two or more, by today's selection rule) and which names a device binding runs device-first interpretation (07-B6, 07-B7) instead of skipping.
  - Without a binding, interpretation is skipped as today.
  - With several images, the images are not given to the worker, and the notes say so.
  - A run started with `interpretation` set to `tasks` runs device first, as one worker, with a note.
  - The interpret handoff's `configurator.interpretation` notes give the mode `device`, the number of candidate images, and a kit analysis that was not made because no single image was acquired.
- **07-B6** — The device-first worker is a fresh interpret worker. Its sealed inputs are:
  - the listen evidence and `BINDING_CONTEXT.json` it would get today;
  - the documents (07-B3);
  - the run's `TASKS.json`, saying there is no firmware, the model comes from the device, and the worker runs only read operations while probe and ground will test the model under the run's grants, which it names;
  - the model document, the contract guide and tool, `validate.py` and `qualify.py`;
  - its instructions;
  - on a repair, the previous model and `REPAIR_CONTEXT.json`, as today.

  Its tools are `model_qualify`, `interface_describe`, `interface_execute` and `probe_run`. It writes `model.json`, `replies.json`, `NOTES.md` and `contract.json`. Its output is collected and checked as an interpret worker's output is today, and handed off as the interpret stage's one handoff.

  The model may declare `write` or `actuate` operations that the documents or the device's read replies support. The worker cannot run them, so each carries a contract uncertainty saying interpretation did not exercise it. Probe and ground test them under the run's grants.
- **07-B7** — Every operation the device-first worker runs is limited to `read`, whatever the run's grants.
  - An operation that declares `write` or `actuate`, or no effect, is refused before any device I/O, and the worker gets the refusal.
  - Opening the device follows today's rules and passes on the run's own `reset` grant: a serial port needs a prior successful listen or that grant, and a device that resets when opened needs it. A reset on open is not an operation the worker runs. Its effects are `read`, plus `reset` when the run grants it.
  - Each call is recorded in the run's events and dispatch journal as a probe stage's calls are. The handoff's notes list each call's dispatch id and record hash.
  - Records are not among the handoff's artifacts, so probe and ground receive only the model.
- **07-B8** — After device-first interpretation, the run goes on as today:
  - Probe and ground test the handed-on model as the assigned model, and their differences send the run back to interpretation within `max_revisions`.
  - A repair runs the device-first worker again with the previous model and the differences.
  - Emit takes the model from the interpret handoff, and reuse and the verdict follow.

  A run with a binding and no firmware can therefore end with an emitted package and a self-evidence verdict.
- **07-B9** — Benchmark runs acquire, prepare, run and check interpretation exactly as today:
  - no documents, no device-first interpretation, and no new acquire tool among their workers' tools;
  - existing tool descriptions unchanged.

  No benchmark acquire worker is given `acquire_protocol_spec`, so 07-B2 changes nothing there. `.scratch/benchmark-review/issues/13-kit-changes-not-adopted.md` records that benchmark runs get neither documents nor device-first interpretation, and that a benchmark acquire worker given `acquire_protocol_spec` would now hand its files on.

## Interface and seams

New behaviour goes in modules of its own. The configurator calls them from its generic branches of acquire tools, interpret preparation, stage launch and interpret check. Benchmark runs never reach those branches.

These stay as they are, because benchmark runs share them:
- the interface interpreter's brief (`skills/interpret-interface/SKILL.md`) and the interpret prompt that says the worker has no device access;
- the model documents, `validate.py` and `qualify.py`;
- the existing entries of `tools.json`;
- the existing tools' parameters and refusals (07-B2 adds recorded artifacts only).

The device-first instructions and prompt are files of this Feature's own, outside `resources/toolchain/skills/`. Benchmark code and benchmark tests are not edited.

The builder chooses:
- the new acquire tool's name and its `tools.json` entry;
- the device-first instructions;
- how documents are laid out under `documents/`;
- how the scripted worker learns a device-first mode.

The bounds (2 MiB, 256 KiB) are module constants that tests may shorten.

Tests attach in these places:
- 07-B1 and 07-B2: the acquire tools, with a small generated PDF (skipped when pypdf is absent), a Markdown file, and a stand-in protocol registry;
- 07-B3 to 07-B8: the configurator, with scripted workers;
- device-first tests: a localhost line device such as `tests/line_device.py`. One of them runs a run with a binding and no firmware through to an accepted emitted package.

No test needs a model, real hardware or the network. The smoke keeps its firmware path and passes as today.

`docs/codex.md` says how to pass documents with a run and what a run with no firmware does.

## Out of scope

- Interpreting several images.
- USB descriptors as interpret input, and importing source code, an existing driver or recorded traffic.
- Register-model front ends: datasheet to register model, and the I2C black-box skill.
- OCR, figures and tables beyond the text pypdf extracts, and searching the web for datasheets.
- Running write, actuate or reset operations during device-first interpretation (declaring them is 07-B6), and task mode for it.
- Size bounds on what acquire tools fetch, and a no-firmware smoke.
- Recording the device-first worker's prompt. The interpret seal keeps the prompt that `interpret_run` writes into it, which is benchmark provenance and is being retired with the benchmark.

## Deviations
- The new acquire tool is `acquire_document`. It takes a bare `name` from `inputs/`, plus an `inputs_dir` that the configurator sets, hides from the worker's schema and refuses from the worker; that path check applies to this tool only — the Spec leaves the name and entry to the builder, the tool refuses any path outside `inputs/`, and existing tools ignore a stray `inputs_dir` as before.
- Imports go to `acquire/documents/<name>/`, holding the original, `pages/NNN.txt` and `index.json`. The tool also refuses an already imported document, a symlink and text that is not UTF-8. The acquire context gets a `documents` hint whenever the run has inputs — the layout is the builder's, and the hint serves 07-B1's Intent.
- `acquire_protocol_spec` also returns `artifacts`, its source files and sections folder; its description is unchanged — the Spec allows exactly this: "07-B2 adds recorded artifacts only".
- Pages are forwarded under `documents/<n>-<kind>/` and listed in `DOCUMENTS.json` (`supplementary-documents/1`). The `acquire_classify` datasheet rung counts as a datasheet. Documents map to their sources through the configurator's own `tool.finished` records, matched by sha256. Round-robin stops at the first page that would pass the bound. TASKS.json gains a `documents` entry — the layout is the builder's, it uses configurator records rather than worker claims, and the bound rule is as specified.
- In task mode, a handler or the cross-check gets the `documents/` paths cited in the inventory command's `evidence`, within 256 KiB. TASK.json gets a `documents` entry, an ignored citation is noted once, and INVENTORY, HANDLER and CROSS_CHECK.md gain a paragraph each — the Spec says pages are cited in each command's evidence, and these are Feature 06 resources not shared with benchmark runs.
- Device-first notes are `{requested, mode: device, candidate_images, kit_analysis: {made: false, cause: no_single_image}, notes}` — this is what 07-B5 asks for.
- The device-first worker gets its own `resources/device_first/DEVICE_FIRST.md` and `device_first.prompt()`. BRIEF.md stays in the workspace, scoped by DEVICE_FIRST.md. Its TASKS.json replaces the generic one, and its `model_dir` is its workspace — this keeps `interpret_run` and the interpret skill unchanged, as the Spec requires.
- The interpret seal keeps `interpret_run`'s prompt, and the device-first worker's prompt is recorded nowhere — the amended Spec's last Out-of-scope bullet covers this; the human decided it after an Escalation.
- The device-first refusal runs before the grant check. The assignment's effects are `read`, plus `reset` when granted. Records go to `<run>/device-first/<attempt>/`, outside the handoff, and the notes' `device_calls` lists `{dispatch_id, name, operation, ok, record_sha256}` — the Spec leaves "record hash" and where records go open; the interpret handoff carries only the model directory.
- 07-B8 needed no production code, so its RED was captured by in-memory mutation — this is about evidence, not scope; one mutation is the base commit's behaviour.
- The kit's scripted worker gained no device-first mode — the Spec makes this the builder's choice, its interpret branch already takes a prompt with no JSON, and a no-firmware smoke is out of scope.
- 07-B9's RED was captured by mutation; the bme280 test patches `_datasheet_sha256`, and the tool-description digest is pinned from base — this is a non-regression Behaviour, no benchmark source is edited, and the human accepts benchmark-only concerns.
- `tests/input_path_fixtures.fake_serial` puts a stand-in pyserial in `sys.modules` — it is used only for serial listen and reset-on-open, which is the hardware boundary.
- The scripted program of the `acquisition` fixture in `tests/test_acquisition_pipeline.py` now accepts a prompt with no JSON task — it is a kit test, and 07-B5 changed the run path it assumed.
- One 07-B7 test was renamed after RED to fit 120 columns — cosmetic.
