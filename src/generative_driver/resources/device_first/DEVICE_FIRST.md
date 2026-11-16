# Interpret the device

There is no firmware image for this device. Write its interface model from the device itself: its replies to the
read operations you run, the listen evidence (`serial-listen.json`, `BINDING_CONTEXT.json`) and the documents
(`DOCUMENTS.json`) when they are supplied. The device's replies are the truth.

`BRIEF.md` is the interpret brief. Its inputs, model rules and finish apply here. Its firmware analysis does not:
there is no image, `image_map_check.py` or decompiler export.

## Steps

1. Read `TASKS.json`, `INTERFACE_MODEL.md` and `CONTRACT.md`. On a repair, read `REPAIR_CONTEXT.json` first and start
   from `previous-model/`.
2. Write a first `model.json`: the channel, identity and the read operations the evidence suggests. Run
   `python3 validate.py .` and fix what it reports.
3. Run read operations on the device with `interface_execute` (`model_dir` this workspace, `operation`, `parameters`),
   or `probe_run` for repeated samples. `interface_describe` lists the model's operations and transport. The
   configurator owns the connection, the grants and the output folder: pass none of them. Each result's `transcript`
   holds the bytes sent (`tx_hex`) and the device's reply (`rx_hex`). Revise the model from the replies and repeat.
4. Only operations declared `read` run. The configurator refuses an operation declared `write` or `actuate`, or with
   no effect, before it reaches the device, whatever the run's grants. Declare a write or actuate operation only
   where the documents or the device's read replies support it, and give it a contract uncertainty with code
   `not_exercised` saying interpretation did not exercise it, its `evidence_refs` citing that support. Probe and
   ground test it under the run's grants.
5. Write `replies.json` from the replies the device gave you, in the shape `BRIEF.md` describes. For an operation you
   could not run, predict its replies from the documents and say so in `NOTES.md`. Run
   `python3 qualify.py . replies.json` until every operation with replies qualifies.
6. Write `contract.json` as `CONTRACT.md` describes and run `python3 contract.py check`.

## Output

Write `model.json`, `replies.json`, `NOTES.md` and `contract.json` at the workspace root. `NOTES.md` lists each call
you ran and what it showed, the evidence behind each operation and what stays unresolved. The configurator keeps its
own record of your calls; probe and ground receive only the model.
