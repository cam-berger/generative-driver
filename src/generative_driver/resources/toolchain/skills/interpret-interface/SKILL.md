---
name: interpret-interface
description: Derive a bounded executable interface-model/4 or /5 from supplied firmware or protocol evidence, using only provided tools and inputs, and qualify it offline against its own predicted replies before finishing.
---

# Interpret an executable interface

Read `INTERFACE_MODEL.md` and supplied evidence. Produce `model.json` in schema 4 (`interface-model/4`), or in schema 5 (`interface-model/5`) for a binary framed protocol, `NOTES.md` and `replies.json`.
No `convert.py` is required or executed. This is the shared model/probe/emission workflow; no target-specific
runtime can fill missing protocol facts. Supplied binary analysis may use the provided Ghidra installation
and exporter in the supplied workspace. Use only supplied evidence; filesystem and network isolation
depend on the configured runtime and are not established by the input seal. Do not use internet resources.

Run Python commands with the configured Python 3.11+ interpreter. The `python3` examples below stand
for that interpreter; on Windows use its full path rather than assuming a `python3` command exists.

Before choosing any load base for a raw image, run the mapping checker, `python3 image_map_check.py image.bin`,
a standard-library script supplied beside `validate.py`. A plausible vector table is not a load map: the same
table is consistent with several bases and prefixes, and an earlier run took a vector table's file offset for the image start and mapped an image a header's length
bytes low by taking the table's file offset as the image start. Record in `NOTES.md` the checker's best mapping,
the literal-pointer evidence behind it (which absolute pointers resolve to which strings under that mapping and
not under the alternatives) and any disagreement between the checker, the reset handler and the image header.
Every address in provenance is stated under that recorded mapping, with the original file offset beside it.

Inventory all interfaces found in the evidence, with function/offset or descriptor references and gaps.
Distinguish the controller's externally accessible interface from internal peripherals. One model describes
one channel/device; use separate directories for additional supported interfaces and identify the main
output in NOTES. A structurally valid incidental peripheral model does not establish the requested task.

Record the original artifact hash, analysis architecture/base assumptions, commands and output files in
NOTES. Confirm exporter artifacts actually exist; an exit code alone does not establish decompilation.
Use exact transaction boundaries and evidence-backed limits. The host supplies physical bindings later;
do not put serial device paths, network destinations or FTDI URLs in the model.

A device that answers a request with one or more lines and a completion line is a line protocol, and its
exchange steps receive in `lines` mode with `end` on the completion line the firmware emits, never `up_to`
and never `until` with a newline. Pin the identity: the firmware-family token, the version token and the
machine-type token the firmware's identity handler emits, each as its own positive form, together with
`final_line_equals` or `final_line_token` on the completion line and `reject_line_prefix` naming the error
lines the firmware can send. A status read pins the shape of its status line; a write is acknowledged by
the completion line alone, never by a substring. `INTERFACE_MODEL.md` explains why the weaker forms fail.

A device that exchanges binary frames (sync bytes, a length field, a payload and an integrity check) is a
framed protocol, and it is modelled in schema 5 (`interface-model/5`). Each reply is received in `frame`
mode with the sync bytes, length field and check that the firmware's reply builder emits; each request
carries `check` parts computed over exactly the bytes the firmware's receive path verifies, so a request
whose check depends on its own argument is sent correctly for every argument. Read the check algorithm
out of the firmware's check routine (width, polynomial, initial value, shift direction, final
complement, and which bytes it is called over) rather than assuming a familiar one. Pin each reply's
response code with `prefix_hex` and name the firmware's error frames in `reject_prefix_hex`. Binary
replies for qualification are whole frames, check included, in `interface-replies/2` as `{"hex": ...}`,
computed from the firmware's own routines rather than from the model. If the framing needs something
schema 5 does not have, record the operation as unsupported instead of approximating it.

Give every operation provenance, typed/bounded arguments and its effect. The identity operation must
verify a response before other operations execute. Keep status/read operations separate from writes and
actuation. An effect label does not prove arbitrary command bytes harmless. Do not force a command
interface into fake sensor measurements. If clocks, framing, setup, response grammar or decoder semantics
are unresolved, record the unsupported operation instead of inventing an executable value. Physical
questions may establish facts the supplied code cannot, through the host's provided observation channel.

Run `python3 validate.py .`, revise reported defects, then write `replies.json` in schema `interface-replies/1`
(`interface-replies/2` when any reply is a binary frame) from your own analysis: for every operation, one
list of reply lines, or one `{"hex": ...}` frame, per exchange step, completion line or check included,
with `parameters` for operations that take arguments, predicted from the handler code and its
literals rather than assumed from a familiar protocol. Run `python3 qualify.py . replies.json` and revise
the model until every operation with replies qualifies; do not finish before it does. Qualification proves
the model's guards and framing against the replies you assumed, complete, fragmented and faulted; it proves
nothing about the device. Return model, replies and notes plus exact unresolved gaps. Validation checks
structure; independent byte fixtures, probe evidence and physical verification are later gates. Do not
rewrite the validator, qualifier, checker, supplied toolchain or sealed inputs. The host records inference
usage; do not invent token counts or infer them from tool calls.
