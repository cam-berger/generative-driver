---
name: find-datasheet
description: Locates the vendor datasheet for a part marking or name and returns its URL for acquire_datasheet to fetch and hash. Use when acquire_datasheet returned needs_search because the part is not in the registry.
---

# Find the vendor datasheet

Use when `acquire_datasheet` returns `needs_search`. Read the part marking/name and origin in `part.json`. Search the vendor first; use a distributor only if the vendor hosts no copy, and disclose that fallback.

Write `datasheet.json`:

```json
{"part":"...","vendor":"...","url":"...","revision":"if visible","why":"one line linking this part to this vendor document"}
```

Return the datasheet URL for hashed acquisition. Inspect only title/revision; do not summarize it or substitute a driver, library, example or application note. One part per call. If several parts match, return all candidates and stop; `acquire_classify` handles ambiguity.
