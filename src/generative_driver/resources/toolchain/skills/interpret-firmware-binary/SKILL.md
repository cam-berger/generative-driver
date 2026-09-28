---
name: interpret-firmware-binary
description: Recover an evidence-backed experimental interface model from a raw firmware binary in a prepared neutral workspace, without hardware or external source access.
---

# Interpret the supplied binary

Read `OUTPUT_SCHEMA.json` for the output contract and `TOOLING.md` before launching analysis tools. The only device evidence supplied is `image.bin`; identifying strings inside it are admissible evidence. Generic export helpers and tool documentation are conveniences, not evidence about this image.

Recover the image/container format, plausible architecture and memory layout, externally exposed transports, framing, requests, response interpretation, state changes, physical effects, protective checks and limits as far as the bytes support them. Infer architecture and load parameters from the image; no processor, vendor, command language or application is assumed. Recognition of a product or conventional protocol is not evidence for its version-specific behavior.

Work only inside this workspace, apart from invoking generic installed local tools and reading their own local documentation. Do not inspect parent directories, repositories, source trees, acquisition metadata, task conversations or other workspaces. Do not use network access, discover or open devices, query USB descriptors, or issue live requests. This is instruction-level isolation, not an OS sandbox. Input hashes show whether supplied bytes changed; they cannot establish that no external information was accessed.

Before analysis, verify every hash in `INPUT_HASHES.json`; preserve all supplied files. Keep derived files, projects, settings, caches, logs and temporary files here. Retain failed steps and explain their consequences. Hash `image.bin` again when finished and report both digests in provenance. If decoding is uncertain, record competing interpretations rather than silently forcing an import.

Deliver these three files:

- `model.json`: `schema` is `interface-model/2-experimental`; use the operation **list** contract in `OUTPUT_SCHEMA.json`. Every operation needs a unique ID, request, response interpretation, effect class, and concrete binary evidence containing an address or file offset plus a relative export file. Keep each cited export. State unknown fields and uncertainties explicitly. This contract requires no conversion module.
- `NOTES.md`: record import decisions, tools and settings, supported claims, unsuccessful analysis, remaining ambiguity, and any use of general prior knowledge. Explain acknowledged no-ops, rejected requests, queued versus completed work, error precedence, and parameter-sensitive effects when the binary supports them. Do not infer a successful action from an acknowledgement alone.
- `PROBES.json`: an ordered list of minimal proposed read-only checks for a separate operator, or `[]` when none is justified. Each entry names `operation_id`, repeats that operation's exact `request`, and explains its rationale and expected observation. Proposals do not authorize execution. Do not include state changes, physical actions, destructive operations or operations with unknown effects.

The model is an experimental evidence handoff, not a verified driver or proof of live-device equivalence. Use effect classes `read_only`, `state_change`, `physical_action`, `destructive`, or `unknown`. Describe destructive behavior if recovered; do not execute it. An uncertain or parameter-dependent effect must not become read-only merely because the request resembles a familiar query. Preserve uncertainties in the final output and stop after handing off the static findings.
