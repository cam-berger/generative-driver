# Inventory the firmware's commands

You are the first task of an interpretation that runs as tasks, each a fresh worker with small inputs. List the
commands the firmware accepts on its interface and the functions that handle each. Later, one worker per command
writes that command's operation from your entry, and a last worker checks the whole model. Do not write the model.

## Inputs

Every file here is a sealed input: read it and leave it unchanged. Changing one stops the run.

- `TASK.json`: this task, what it writes and how much of the strings table was left out.
- `ANALYSIS_INDEX.json`: the configurator's analysis of the firmware. It lists every function with its address, name,
  the size of its decompiled code, its callers and callees, the strings it references and its switch tables, and it
  ranks the likely command dispatchers with the evidence for each rank.
- `DISPATCHERS.c`: the decompiled code of the top-ranked dispatcher candidates. Its first comment says which functions
  it holds and which it cut or left out.
- `strings.tsv`: the firmware's strings, with the functions that reference each.
- `TASKS.json`: the run's objective and effect grants. The objective says what is wanted, not how the device speaks.
- `serial-listen.json` and `BINDING_CONTEXT.json`, when supplied: what the configurator observed of the device's
  channel during acquisition.

## Write `inventory.json`

```json
{
  "schema": "interface-model/4",
  "device": {"name": "DEMO-42"},
  "channel": {"type": "uart", "baudrate": 115200},
  "framing": "ASCII commands ending in a newline; each reply is one line ending in a newline",
  "identity": "identify",
  "commands": [
    {"name": "identify", "handlers": ["08000300"], "evidence": ["dispatch case 0x49 at 08000220 calls 08000300"]}
  ]
}
```

- `schema`: the interface-model tag the commands need: `interface-model/4` for text lines, `interface-model/5` for
  binary frames, `interface-model/6` for USB or byte parameters.
- `device.name`: the name the firmware gives itself, or null.
- `channel`: the model's channel object. Use `BINDING_CONTEXT.json`'s `runtime_channel` when it is supplied; a UART
  needs its `baudrate`.
- `framing`: how a command and its reply are delimited, in a sentence.
- `identity`: the name of the command that identifies the device: a read without parameters whose reply names the
  firmware. It must be one of the commands.
- `commands`: at most 64, each with a `name` (letters, digits and underscores, starting with a letter), the
  addresses of the functions that handle it as `ANALYSIS_INDEX.json` writes them, and the evidence for the command
  and each handler: a switch case, a string compare, a call.

List only commands the evidence shows. If you find no command, write an empty list: the run then interprets with a
single worker. Write what you were unsure of in `NOTES.md`.
