---
name: interpret-selfdescribing
description: Derive and qualify an executable interface-model/6 from USB descriptors and any supplied protocol specification or vendor client source. Use when the workspace holds descriptor.json from the self-description rung.
---

# Interpret USB self-description

Produce `model.json` (`interface-model/6`), `NOTES.md` and `replies.json` using `INTERFACE_MODEL.md`; no conversion module.

## Evidence

- `descriptor.json`: raw/parsed USB descriptors and read-only request log; `ENDPOINT.md`: selected channel/binding.
- Optional `spec/` and split `spec/sections/NNN.txt`: named protocol specification.
- Optional `client/`: vendor source or offset excerpts of a hash-pinned original. Trace selection, framing, parsing and callers; names alone prove no semantics. Never execute it or assume firmware equivalence.
- Retry: revise from `defects.json` and supplied `previous/` files.

One model covers one channel. Select functional endpoints/interface exactly from descriptors. CDC is host UART; note it as unmodelled unless evidence defines its protocol.

Identity uses descriptor bytes: `usb_control` GET_DESCRIPTOR with request_type 128, request 6, value 256, index 0, exact length 18. Copy `device.raw_hex` into `prefix_hex` through vendor/product IDs.

## Operations

Descriptors alone do not define commands. Cite every `operations.<name>` and `operations.<name>.outputs.<output>` using a descriptor JSON pointer, quoted spec section or client source range. Use literal `recalled` only for prior knowledge of a protocol named by the descriptor when supplied evidence is absent; mark it as a probe hypothesis. Label source-assisted analysis and separate predictions from later observations.

Effects: `read` changes nothing; `write` changes state/configuration; `actuate` drives pins/outputs/targets. Choose the stronger effect when uncertain. Omit unsupported fields or use null where allowed; never guess bounds/replies.

USB `up_to` uses descriptor packet size, or a larger multiple only when protocol short-packet termination is supported. Use `exact` only for fixed unpadded replies. Length-framed replies need justified `max_bytes`/`max_reads`; composite length/padding needs source-backed bit layout. Use `match` only for proven echoed fields; a client parser alone does not prove echo. Pin reply headers and known error prefixes. Record data-dependent frame counts, unproved sequence rules and other runtime gaps instead of assuming short packets or leaving replies unguarded.

## Finish

Use configured Python 3.11+ (full path on Windows). Run `python3 validate.py .`, fixing each reported defect. Write `interface-replies/2` with one `{"hex": ...}` per exchange/control, empty hex for no response. Derive bytes from descriptors/spec/source, never from the model. Run `python3 qualify.py . replies.json` until all covered operations qualify; list uncovered operations in notes.

Notes list each operation/decoder's source, predicted replies for probing, omitted interfaces, null/unsupported values and reasons. Work only here; no fetching/searching or USB/serial/network discovery/access. Preserve inputs, `validate.py`, `qualify.py` and `_toolkit/`. Stop when both checks pass or after two hours.
