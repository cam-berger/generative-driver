---
name: interpret-firmware-binary
description: Recover an evidence-backed experimental interface model from a raw firmware binary in a prepared neutral workspace, without hardware or external source access.
---

# Interpret a supplied binary

Read `OUTPUT_SCHEMA.json` and `TOOLING.md` first. Device evidence is `image.bin`, including its strings. Generic helpers/docs are not image evidence. Infer architecture, container/load map, interfaces, framing, replies, state/effects, guards and limits from bytes; product/protocol recognition does not prove version-specific behavior.

Verify every `INPUT_HASHES.json` entry before analysis; preserve inputs. Work only here except invoking installed generic tools and reading their own local documentation. Do not inspect parents, repositories, source trees, acquisition metadata, conversations or other workspaces; no network, device discovery, descriptors or live requests. This is instruction-level isolation, not a sandbox. Keep projects/settings/caches/logs/temp/exports here. Retain failed steps and competing interpretations. Rehash `image.bin` at completion and report both digests; hashes cannot prove absence of outside access.

Write:

- `model.json`: `interface-model/2-experimental`, with the operation list in `OUTPUT_SCHEMA.json`. Each unique operation needs request, response interpretation, effect and concrete address/file-offset evidence pointing to a retained relative export. State unknowns; no conversion module.
- `NOTES.md`: import/tools/settings, supported claims, failures, uncertainty and prior knowledge. Distinguish acknowledgment from action, no-op from rejection, queued from completed work, error precedence and parameter-sensitive effects where supported.
- `PROBES.json`: ordered minimal read-only proposals, or `[]`. Each names `operation_id`, repeats its exact request, rationale and expected observation. No state-changing, physical, destructive or unknown-effect proposal; proposals authorize nothing.

Effects: `read_only`, `state_change`, `physical_action`, `destructive`, `unknown`. Never classify uncertain/parameter-dependent behavior as read-only by familiarity. Describe recovered destructive behavior without executing it. Preserve uncertainty and stop after static handoff; this is not a verified driver.
