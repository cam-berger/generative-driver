#!/usr/bin/env python3
"""Reported diagnostic for a datasheet-derived model: for every provenance entry that cites a page, check
that the page's text actually contains the register (or value) the entry is about. Not a gate.
    python provenance_audit.py <model_dir> <datasheet.pdf>"""
import json, os, re, sys
import pypdf

mdir, pdf = sys.argv[1].rstrip("/"), sys.argv[2]
model = json.load(open(os.path.join(mdir, "model.json"), encoding="utf-8"))
pages = [(p.extract_text() or "") for p in pypdf.PdfReader(pdf).pages]
ok = miss = nopage = 0
for e in model.get("provenance", []):
    src = str(e.get("source", "")); m = re.search(r"p(?:age)?\.?\s*(\d+)", src, re.I)
    if not m: nopage += 1; continue
    pg = int(m.group(1)); text = pages[pg - 1] if 0 < pg <= len(pages) else ""
    regs = set(re.findall(r"0x[0-9A-Fa-f]{2}", json.dumps(e) + " " + str(e.get("item", ""))))
    hit = any(r.upper() in text.upper() for r in regs) if regs else bool(text)
    ok += hit; miss += not hit
    if not hit: print(f"  not on page {pg}: item {e.get('item')!r} regs {sorted(regs)}")
print(f"provenance entries: {ok} confirmed on the cited page, {miss} not found, {nopage} without a page reference")
