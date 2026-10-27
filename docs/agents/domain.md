# Domain documentation

This is a single-context repository: one domain vocabulary for the configurator, runtime, package and benchmark.

## Read before exploring

- Root [CONTEXT.md](../../CONTEXT.md) defines the existing vocabulary. Retain it as the authoritative glossary for now.
- [Accepted design](../design.md), including its assistive protocol amendment, defines the current behavior and constraints.
- If a root `GLOSSARY.md` exists, read it too. Any migration from `CONTEXT.md` must update the existing consumers and leave one authoritative vocabulary; do not maintain conflicting copies.
- Read relevant architectural decision records under `docs/adr/` when present. No per-package glossary layout is needed.

The skills' conventional glossary location is root `GLOSSARY.md`; their decision-record location is `docs/adr/`. Do not create empty placeholders or rename the existing glossary during setup. `/domain-modeling`, reached through `/grill-with-docs` or `/improve-codebase-architecture`, can create or migrate these documents when actual terms or decisions are resolved.

If optional glossary or decision-record files are absent, proceed silently. Use the existing vocabulary in explanations, issue titles, proposals and tests. Flag conflicts with the accepted design or a decision record explicitly before proposing a change.
