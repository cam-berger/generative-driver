# Cross-check the assembled model

You are the last task of an interpretation that runs as tasks, each a fresh worker with small inputs. An inventory
task listed the firmware's commands, one handler task per command wrote that command's operation, and the
configurator assembled them into `model.json` and `replies.json`. Each handler saw only its own command. Check what
spans commands and the consistency of the whole, and correct it.

## Inputs

`model.json` and `replies.json` are yours to change. Every other file here is a sealed input: read it and leave it
unchanged. Changing one stops the run.

- `TASK.json`: this task and what it writes.
- `VALIDATION.json`: the configurator's validation of the assembled model as a whole.
- `inventory.json`: the commands, their handler functions, the channel and framing, as the inventory found them.
- `ANALYSIS_INDEX.json`: every function of the firmware with its callers, callees, strings and switch tables, and the
  likely command dispatchers.
- `HANDLERS.c`: the decompiled code of every handler function. Its first comment says which it holds and which it cut
  or left out.
- `notes/`: every task's `NOTES.md`, named by its task.
- `TASKS.json`: the run's objective and effect grants.
- `DOCUMENTS.json` and every `documents/` page a command's evidence cites, when supplied: supplementary documents,
  evidence of meanings, units and framing as `TASKS.json` describes them; the firmware wins where they disagree.
  `TASK.json` names any cited page its size bound left out.
- `INTERFACE_MODEL.md`, and `validate.py` and `qualify.py` with the `_toolkit/` they use.
- `previous-model/` and `DIFFERENCES.json`, in a repair: the previous attempt's final model and everything the device
  did that contradicts it, including what no handler task was given: the channel, the transport, the identity and
  the provenance.

## Check

- Behaviour that spans commands: modes the firmware must be put in first, a setting that takes effect only after a
  commit, state one command leaves for the next, replies that differ by mode.
- Preconditions: no handler wrote one, because each saw one command, and the assembly left out any it found. Where the
  code or a handler's notes show that the device refuses an operation unless a read operation reports a given value,
  write the operation's `preconditions` as `INTERFACE_MODEL.md` describes them, and only there. In a repair,
  `previous-model/` shows the ones written before; a re-run operation keeps them.
- Consistency: one framing, one line ending and one rejection reply across operations; the identity operation pins
  the firmware's name; the channel matches the evidence; every operation, parameter and output says what it means,
  with one wording and one unit for the same quantity across operations.
- `VALIDATION.json`'s defects, and the notes' open questions.

Change only what the code, the strings or the differences show. Then run `python3 validate.py .` and
`python3 qualify.py . replies.json`, and fix what they report. The validator's `advice` lists what is still
undocumented.

## Write

The final `model.json` and `replies.json` at the workspace root, and `NOTES.md` saying what you checked, what you
changed and why, and what stays uncertain. The files you leave are the attempt's output.
