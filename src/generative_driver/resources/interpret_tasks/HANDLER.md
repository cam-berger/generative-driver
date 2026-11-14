# Write one command's operation

You are a handler task of an interpretation that runs as tasks, each a fresh worker with small inputs. An inventory
task listed the firmware's commands; you write the operation of one of them, `COMMAND.json`, as a fragment. The
configurator assembles the fragments of every command into one model, and a last worker checks the whole.

## Inputs

Every file here is a sealed input: read it and leave it unchanged. Changing one stops the run.

- `TASK.json`: this task and what it writes.
- `COMMAND.json`: your command, as the inventory lists it: its name, its handler functions' addresses and the evidence
  for each.
- `MODEL_FRAME.json`: the model's schema tag, device name, channel and framing, as the inventory found them.
- `IDENTITY.json`, unless your command identifies the device: the identity operation its task wrote. Your operation
  runs after it on the same connection.
- `CODE.c`: the decompiled code of your command's handler functions and of the functions they call, nearest first.
  Its first comment says which functions it holds and which it cut or left out.
- `STRINGS.json`: the strings those functions reference.
- `TASKS.json`: the run's objective and effect grants. The objective says what is wanted, not how the device speaks.
- `INTERFACE_MODEL.md`: the model document. `CONTRACT.md` and `contract.py`: how to describe an operation for the
  driver contract. `validate.py` and `qualify.py`, with the `_toolkit/` they use: the model's validator and offline
  qualifier.
- `PREVIOUS_FRAGMENT.json`, `PREVIOUS_OPERATION.json` and `DIFFERENCES.json`, in a repair: the fragment this command's
  task wrote before, the operation as the previous final model has it, and what the device did that contradicts it.
  Start from the previous operation and change what the differences contradict.

## Write `fragment.json`

```json
{
  "schema": "interpret-fragment/1",
  "operation": "measure",
  "model": {"effect": "read", "parameters": {}, "steps": [], "outputs": {}},
  "provenance": [{"item": "operations.measure", "source": "handle_measure at 08000340 formats T:%d.%d"}],
  "replies": {"steps": [["T:21.5"]]},
  "contract": {"effect": "read", "description": "", "uncertainties": [], "parameters": {}, "outputs": {}}
}
```

- `operation`: your command's name, unless the evidence names the operation otherwise.
- `model`: the operation's entry in `model.json`'s `operations`, as `INTERFACE_MODEL.md` describes it, for the schema
  in `MODEL_FRAME.json`. Recover its bytes, replies and decoders from the code and strings.
- `provenance`: the model's provenance entries for this operation, at least one for `operations.<name>`.
- `replies`: the operation's entry in `replies.json`: the reply you predict from the code for each exchange step,
  independently of the model.
- `contract`: the operation's entry in `contract.json`'s `operations`, as `CONTRACT.md` describes it.

Check it before you finish. In a folder of your own, such as `check/`, write `model.json` with the schema, device and
channel of `MODEL_FRAME.json`, an `identity` naming the identity operation, that operation (yours if your command
identifies the device, else the one in `IDENTITY.json`) and yours, with their provenance; `replies.json`
(`interface-replies/1` for lines, `/2` for binary frames) with your replies; and run `python3 validate.py check`,
`python3 qualify.py check check/replies.json`, `python3 contract.py draft check` and then, with your descriptions in
place, `python3 contract.py check check`. Fix what they report. A fragment that does not validate in that model is
left out of the assembly.

Write what you were unsure of in `NOTES.md`; the last worker reads it.
