# Write one command's operation

You are a handler task of an interpretation that runs as tasks, each a fresh worker with small inputs. An inventory
task listed the firmware's commands; you write the operation of one of them, `COMMAND.json`, as a fragment. The
configurator assembles the fragments of every command into one model, and a last worker checks the whole.

## Inputs

Every file here is a sealed input: read it and leave it unchanged. Changing one stops the run.

- `TASK.json`: this task and what it writes.
- `COMMAND.json`: your command, as the inventory lists it: its name, its handler functions' addresses and the evidence
  for each.
- `MODEL_FRAME.json`: the model's format tag, which the configurator writes, and the device name, channel and
  framing as the inventory found them.
- `IDENTITY.json`, unless your command identifies the device: the identity operation its task wrote. Your operation
  runs after it on the same connection.
- `CODE.c`: the decompiled code of your command's handler functions and of the functions they call, nearest first.
  Its first comment says which functions it holds and which it cut or left out.
- `STRINGS.json`: the strings those functions reference.
- `TASKS.json`: the run's objective and effect grants. The objective says what is wanted, not how the device speaks.
- `DOCUMENTS.json` and the `documents/` pages your command's evidence cites, when supplied: supplementary documents,
  evidence of meanings, units and framing as `TASKS.json` describes them; the firmware wins where they disagree.
  `TASK.json` names any cited page its size bound left out.
- `INTERFACE_MODEL.md`: the model document. It says how an operation is written: its wire steps, and beside them
  what it means. `validate.py` and `qualify.py`, with the `_toolkit/` they use: the model's validator and offline
  qualifier.
- `PREVIOUS_FRAGMENT.json`, `PREVIOUS_OPERATION.json` and `DIFFERENCES.json`, in a repair: the fragment this command's
  task wrote before, the operation as the previous final model has it, and what the device did that contradicts it.
  Start from the previous operation and change what the differences contradict.

## Write `fragment.json`

```json
{
  "schema": "interpret-fragment/1",
  "operation": "measure",
  "model": {
    "effect": "read", "description": "Read the temperature of the probe.", "parameters": {},
    "steps": [{"op": "exchange", "tx": [{"text": "T\n"}], "timeout_ms": 1000, "capture": "reply",
               "rx": {"mode": "until", "delimiter_hex": "0a", "max_bytes": 64}}],
    "outputs": {"temperature": {"from": "reply", "kind": "ascii_number", "prefix": "T:", "unit": "Cel",
                                "description": "Temperature of the probe."}}},
  "provenance": [{"item": "operations.measure",
                  "source": "handle_measure at 08000340 formats tenths of a degree Celsius as T:%d.%d"}],
  "replies": {"steps": [["T:21.5"]]}
}
```

- `operation`: your command's name, unless the evidence names the operation otherwise.
- `model`: the operation as it stands in `model.json`'s `operations`, as `INTERFACE_MODEL.md` describes it. Recover
  its bytes, replies and decoders from the code and strings. Say what it means there too: a `description` of the
  operation and of each parameter and output, the `unit` of each number, and an uncertainty where the evidence leaves
  a meaning open. Narrow an output's `value` only where the evidence fixes it.
- `provenance`: the model's provenance entries for this operation, at least one for `operations.<name>`.
- `replies`: the operation's entry in `replies.json`: the reply you predict from the code for each exchange step,
  independently of the model.

Write no precondition: a precondition spans operations, and you see one. The assembly takes your operation without
`preconditions`. If the code shows that the device refuses your command unless another command reports a given value,
name it in `NOTES.md`; the last worker, which sees the whole model, writes it.

Check it before you finish. In a folder of your own, such as `check/`, write `model.json` with the schema, device and
channel of `MODEL_FRAME.json`, an `identity` naming the identity operation, that operation (yours if your command
identifies the device, else the one in `IDENTITY.json`) and yours, with their provenance; `replies.json`
(`interface-replies/1` for lines, `/2` for binary frames) with your replies; and run `python3 validate.py check` and
`python3 qualify.py check check/replies.json`. Fix what they report, and answer the validator's `advice`, which lists
what is still undocumented. A fragment that does not validate in that model is left out of the assembly.

Write what you were unsure of in `NOTES.md`; the last worker reads it.
