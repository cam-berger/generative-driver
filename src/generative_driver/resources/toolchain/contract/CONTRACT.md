# Write the driver contract

`contract.json` is the driver contract: all a fresh agent reads about the driver. It gets this file, the generated
MCP tools rendered from it and nothing else of your evidence, so say there what each operation, parameter and output
means. `model.json` says how the bytes travel; the contract says what they mean.

## Steps

1. Finish `model.json` first. Then run `python3 contract.py draft`. It writes `contract.json` from `model.json` with a
   placeholder in every description, a `semantics_unknown` caveat on everything and the units the model states. It
   never overwrites an existing `contract.json`.
2. Replace each placeholder: every `description` of an operation, a parameter and an output.
3. Run `python3 contract.py check` before finishing, and fix what it reports.

## Descriptions and caveats

Describe each operation, parameter and output by its observable meaning: what the device does or reports, in the
words a user of the device would use, not the bytes it sends. Keep a description to what the evidence supports.

`uncertainties` holds the caveats a reader should know: each is `{"code", "message", "evidence_refs"}`. Remove the
draft's `semantics_unknown` caveat once the evidence settles a meaning, and keep or write one where it does not. Cite
the evidence in each caveat's `evidence_refs`: a file, a function or address, a string, a probe record. A caveat's
message says what is uncertain and why.

## Units

Write units as UCUM codes: `Cel` for degrees Celsius, `hPa`, `%`, `V`, and `1` for a count or ratio. An output's
`unit` must equal its decoder's `unit` in `model.json`, because the contract's unit is projected into the model:
write the same code in both files, or leave both out (`null` in the contract, no `unit` in the decoder).

When the evidence states no unit for a number, write `null` and add an uncertainty with code `unit_not_stated`. Its
message and `evidence_refs` say where you searched the evidence for a unit: the reply format, the handler, the strings,
any document. Never guess a unit, however likely one seems; the tools then tell the fresh agent that the device does
not state it.

## What stays as `model.json` says

Keep `schema`, `device`, `identity` and `provenance`, each operation's `effect`, and the names and schemas of its
parameters and outputs as the draft has them: they compile back to `model.json`, and `check` fails where they do not.
You may narrow an output's schema (`minimum`, `maximum`, `enum`) only where the evidence fixes it; the driver refuses a
reading outside it. Add an operation's `preconditions` only where the evidence shows the device refuses the operation
unless a read operation reports a given value.

## Check

`python3 contract.py check` exits nonzero when the contract does not compile back to `model.json`, and names each
operation that does not match with its first difference. It also lists, as advice, each number whose unit is `null`
without a `unit_not_stated` caveat and each description still as the draft wrote it. An operation that does not match
reaches the package with the draft's text, so a fresh agent loses what you wrote for it.
