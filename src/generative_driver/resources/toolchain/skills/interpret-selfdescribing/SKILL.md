---
name: interpret-selfdescribing
description: Derive and qualify an executable interface-model/6 from USB descriptors and any supplied protocol specification or vendor client source. Use when the workspace holds descriptor.json from the self-description rung.
---

# Interpret a self-description

The device described itself, and that description, with any specification or vendor client source supplied beside it, is all you get. Write `model.json` in schema 6 (`interface-model/6`, see `INTERFACE_MODEL.md`), `NOTES.md` and `replies.json`. No `convert.py` is written or run.

## Inputs, in the workspace

- `descriptor.json` (`usb-selfdescription/1`): the device's descriptors as raw hex and as parsed fields (the device descriptor, each configuration with its interfaces and endpoints, the string descriptors, any BOS or HID data) and `requests`, the log of the read-only requests that produced them.
- `ENDPOINT.md`: the channel the host will bind and what it binds by.
- `spec/`, when present: a published specification of a protocol the descriptor names, whole and split into `spec/sections/NNN.txt`.
- `client/`, when present: captured vendor client source, or exact excerpts with offsets into a hash-pinned original. This is implementation evidence, not a published specification or proof that the installed firmware behaves identically. Trace device selection, request framing, response parsing and command callers; a function name alone does not establish semantics. Do not execute supplied client code.
- `defects.json` on a retry, with the previous attempt's files under `previous/` when supplied: revise them, do not start over.

## The model

One model describes one channel. Choose the interface whose endpoints carry the device's own function and set `channel` to `usb` with that `interface` and the `in_endpoint`, `out_endpoint` and `transfer` exactly as `descriptor.json` gives them. A communications or CDC data interface is a serial port the host reaches through its `uart` transport: list it in NOTES and leave it unmodelled unless the evidence defines a protocol on it.

Identity pins the device's own bytes, taken only from `descriptor.json`: a `usb_control` standard GET_DESCRIPTOR read of the device descriptor (`request_type` 128, `request` 6, `value` 256, `index` 0, receive `exact` 18) whose `prefix_hex` copies `device.raw_hex` at least through the vendor and product ids.

Operations come from a named protocol's specification or supplied vendor client implementation; descriptors alone never establish a command set. Every operation has a provenance entry (`item` `operations.<name>`), and so does every output decoder you rely on (`operations.<name>.outputs.<output>`). Its `source` names a JSON pointer into `descriptor.json` (`descriptor.json#/device/raw_hex`), a specification section with a short quote (`spec/sections/007.txt: "..."`), a source range in `client/` that constructs or parses the relevant bytes, or the literal `recalled` when the item comes from your own knowledge of a protocol the descriptor names and no supplied source establishes it. These forms replace the illustrative sources in `INTERFACE_MODEL.md`. Prefer supplied primary evidence. A recalled item is a claim the probe stage must test, not evidence. Label a source-assisted interpretation explicitly in NOTES and keep code-derived predictions separate from later live observations.

Declare effects honestly: `read` for a query that changes nothing, `write` for a command that changes the device's own configuration or state, `actuate` for anything that drives pins, lines, outputs or an attached target. When unsure, declare the stronger effect.

Leave out, or set to null, whatever the evidence does not give; never guess a value, a bound or a reply. Receive bounds follow the USB rules in `INTERFACE_MODEL.md`: an `up_to` bound is the IN endpoint's `wMaxPacketSize` from `descriptor.json` (a larger multiple of it only for a reply the protocol ends with a short packet), and `exact` is only for a reply of fixed length that the device does not pad. For a length-prefixed USB reply, use schema-6 `frame` with a justified `max_bytes` and `max_reads`; describe composite body length and padding only when the client or specification establishes their bit layout. For fields the evidence establishes as echoed, use `match` to compare fixed header ranges with the transmitted command or sequence. Pin each reply's leading bytes with `prefix_hex` and name the protocol's error replies in `reject_prefix_hex`.

## Checking

Run `python3 validate.py .` and fix every defect; it reports one at a time. Write `replies.json` in `interface-replies/2` with one `{"hex": ...}` per exchange or control step (`{"hex": ""}` for a step that receives nothing), copied from the raw bytes in `descriptor.json` or built from the reply formats the specification gives, never from the model. Run `python3 qualify.py . replies.json` and revise until every operation with replies qualifies; an operation it reports uncovered goes into NOTES.

`NOTES.md` lists every operation and decoder with its source (descriptor, spec, vendor client or recall), what you expect the device to answer to each that the probe stage should check, the interfaces you did not model, and each value you left null and why. Record remaining runtime gaps precisely, including streams that need a data-dependent number of frames or an unproved reply sequence rule. A parsed sequence field in vendor code does not itself prove firmware echoes the request sequence. Do not substitute a short-packet assumption or an unguarded response.

## Rules

- Work only in this workspace. Do not fetch, download or search for anything, and do not enumerate, open or query any USB, serial or network device.
- Do not change `validate.py`, `qualify.py`, `_toolkit/` or any supplied input.
- Stop when both checks pass, or after two hours.
