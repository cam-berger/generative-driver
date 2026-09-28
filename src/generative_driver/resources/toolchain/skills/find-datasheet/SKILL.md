---
name: find-datasheet
description: Locates the vendor datasheet for a part marking or name and returns its URL for acquire_datasheet to fetch and hash. Use when acquire_datasheet returned needs_search because the part is not in the registry.
---

# Find a datasheet

`acquire_datasheet` did not know this part. Find the vendor's own datasheet for it and hand back the URL. You do not read the datasheet; the interpret skill does that, from a hashed copy.

## Inputs

- `part.json`: the part marking or name, and where it came from (a person, a string in a flash dump, an identity register).

## Output

- `datasheet.json`: `{"part": "...", "vendor": "...", "url": "...", "revision": "if visible on the vendor page", "why": "one line: how you know this is the vendor's datasheet for this part"}`.

## Rules

- The vendor's site first. A distributor's copy only when the vendor does not host one, and say so.
- Return the datasheet, not an application note, a driver, a library or example code.
- Do not open the datasheet beyond its title page and revision. Do not summarize it.
- One part per call. If the marking matches several parts, return them all and stop; `acquire_classify` reports the ambiguity rather than guessing.
