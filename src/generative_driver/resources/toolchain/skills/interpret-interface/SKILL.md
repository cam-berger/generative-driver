---
name: interpret-interface
description: Derive a bounded executable interface-model/4 or /5 from supplied firmware or protocol evidence, using only provided tools and inputs, and qualify it offline against its own predicted replies before finishing.
---

# Interpret an executable interface

Read `INTERFACE_MODEL.md`. Deliver `model.json` (`interface-model/4` for lines, `/5` for binary frames), `replies.json` and `NOTES.md`; no conversion module or target-specific runtime. Use only supplied evidence/tools, no internet. Installed analysis executables/libraries may run outside the workspace; outputs, projects, caches and logs stay here. Input seals establish integrity, not filesystem/network isolation.

## Inputs and analysis

1. Read supplied `TASKS.json`: required behavior, outputs, units and grants, not command bytes. Implement required transactions, including multiple exchanges when needed.
2. On repair, read `REPAIR_CONTEXT.json` and its named feedback (`DEFECTS.json`, `repair/DEFECTS.json` or `defects.json`); read standalone `defects.json` when supplied. It contains device differences and configurator validation defects (`model_contract`), never a grade. Prior candidates, mappings, replies, probe records and live journals are authorized same-run evidence; instrument readings are device evidence. Start from the latest candidate, preserve sealed inputs, write root outputs and justify corrections from captures/measurements, not worker claims.
3. Use `BINDING_CONTEXT.json.runtime_channel` when supplied. Keep unresolved physical settings in notes; TCP does not verify UART baud, framing or pins. Bindings come from the host; never embed serial paths, destinations or FTDI URLs.
4. Before choosing a raw-image load base, run `python3 image_map_check.py image.bin`. Record its best mapping, absolute pointers/string targets, alternatives and any reset/header disagreement. A vector table alone is ambiguous. Carry the justified mapping into import and cite both mapped addresses and file offsets.
5. Inventory external interfaces separately from internal peripherals, with evidence/gaps. One model per channel/device; use separate directories for extras and identify the main output. An incidental peripheral cannot satisfy the requested mission.

Use the configured Python 3.11+ interpreter (full path on Windows). Record input hash, architecture/base, commands and output files. Verify exports exist and are nonempty; exit status alone is insufficient.

Navigation exports `references.tsv`, `strings.tsv`, `switches.tsv` help trace callers, strings and cases. Each defaults to 16 MiB (`--navigation-max-bytes` overrides); check `navigation.json` and `exports.truncated`. These are static facts, not proof code runs.

## Model rules

Every operation needs provenance, bounded typed arguments, effect and response guards. Identity must verify a response before other operations. Separate reads, writes and actuation; an effect label does not prove arbitrary bytes harmless. Keep unsupported clocks/framing/setup/decoders/operations explicit rather than inventing values or fake sensor measurements. Use host observations for unresolved physical facts.

Line replies: use `lines` ending at the firmware's completion line, never `up_to` or newline-only `until`. Identity separately pins firmware, version and machine tokens, plus `final_line_equals`/`final_line_token` and `reject_line_prefix`. Status pins its line shape; writes pin completion, not substring `ok`.

Binary frames: use schema 5 `frame` with evidence-backed sync, length and integrity. TX `check` covers exactly the bytes verified by firmware, including arguments. Derive width/polynomial/init/shift/final complement and covered range from code. Pin response codes with `prefix_hex` and errors with `reject_prefix_hex`. Record unsupported framing rather than approximating it.

## Finish

Run `python3 validate.py .` and fix defects. Predict replies from handlers/literals, independently of the model: `interface-replies/1` has one line array per exchange; `/2` has whole-frame `{"hex": ...}` replies including checks. Include completion lines and parameter values; exclude delay steps.

Run `python3 qualify.py . replies.json`; do not finish until every operation with replies qualifies. It tests complete/fragmented/faulted predictions, not device truth. Return model, replies, notes and exact gaps; later probes/grounding establish behavior. Do not modify validators, qualifier, checker, toolkit or sealed inputs. The host records usage; never invent token counts.
