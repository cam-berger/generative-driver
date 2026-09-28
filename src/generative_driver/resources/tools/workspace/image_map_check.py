#!/usr/bin/env python3
"""Mapping-consistency check for a raw little-endian ARM Cortex-M image.

    python3 image_map_check.py image.bin [--base 0x08000000] [--header 0x100] [--resolve 0x0800abcd] [--json out.json]

A plausible vector table does not fix where a raw image loads. The table's file offset is not the image
start when the file carries an outer prefix or a mapped header, and a reader who takes it as the start is
wrong by exactly that header size for every address it cites. This checker enumerates a small, explicit
family of (header offset, load base) hypotheses and scores each by evidence a correct mapping has to
explain: the reset handler decodes as Thumb, the first sixteen vectors land inside the image, and the
absolute literal pointers found in the image resolve to the starts of ASCII strings. Only the last is a
strong discriminator, because a wrong shift lands pointers on unrelated bytes. The verdict is heuristic; a
decisive best mapping is a ranked hypothesis to confirm by resolving one known literal pointer by hand.

A mapping is the single number delta = load base - header offset, since load = file offset + delta. Two
(header, base) pairs with the same delta resolve every pointer identically, so they are the same mapping;
the checker collapses them and lists the alternatives under `equivalent`.

Standard library only, no repository imports, Python 3.12 or later, so the file can be copied beside an
image into an offline worker. The Thumb self-branch encodings it recognises are 0xE7FE (16-bit `b .`) and
0xF7FF 0xBFFE (32-bit `b.w .`).
"""
import argparse
import hashlib
import json
import re
import struct
import sys

SCHEMA = "image-map-check/1"
NOTE = "heuristic; confirm by resolving a known literal pointer"
DEFAULT_HEADERS = (0x0, 0x20, 0x40, 0x80, 0x100, 0x200, 0x400, 0x800, 0x1000)
TABLE_OFFSETS = (0x0, 0x200, 0x400)                      # 'the vector table sits at base + K'
BASE_ALIGNMENTS = tuple(1 << k for k in range(12, 21))   # 4 KiB .. 1 MiB
SCAN_LIMIT = 64 * 1024
MAX_WORDS = 200000
SRAM = (0x20000000, 0x30000000)
FLASH_WINDOWS = ((0x00000000, 0x20000000), (0x08000000, 0x10000000))
PRINTABLE = re.compile(rb"[\t\n\r\x20-\x7e]+")
MIN_TERMINATED = 6      # printable bytes then NUL
MIN_UNTERMINATED = 12   # printable bytes, any terminator
DECISIVE_RATIO = 1.5
DECISIVE_FLOOR = 0.02
SAMPLE_LIMIT = 5


def hx(value, width=0):
    return "0x%0*x" % (width, value)


def in_flash(address):
    return any(lo <= address < hi for lo, hi in FLASH_WINDOWS)


def in_sram(address):
    return SRAM[0] <= address < SRAM[1]


def words_at(data, offset, count):
    return struct.unpack_from("<%dI" % count, data, offset)


def find_vector_tables(data, scan_limit=SCAN_LIMIT):
    """4-byte aligned offsets in the first scan_limit bytes that look like a Cortex-M vector table: an
    8-byte aligned SRAM stack pointer, an odd reset vector in a flash window, and six more handlers that are
    each zero or an odd address in flash or SRAM, at least one of them nonzero."""
    found = []
    last = min(len(data), scan_limit) - 32
    for offset in range(0, last + 1, 4):
        w = words_at(data, offset, 8)
        sp, reset = w[0], w[1]
        if not (in_sram(sp) and sp % 8 == 0):
            continue
        if not (reset & 1 and in_flash(reset)):
            continue
        handlers = w[2:8]
        if not all(v == 0 or (v & 1 and (in_flash(v) or in_sram(v))) for v in handlers):
            continue
        if not any(handlers):
            continue
        found.append({"file_offset": offset, "initial_sp": sp, "reset_vector": reset})
    return found


def string_starts(data):
    """File offsets where a maximal printable run begins and is long enough to be a string."""
    starts = set()
    size = len(data)
    for m in PRINTABLE.finditer(data):
        s, e = m.start(), m.end()
        n = e - s
        if n >= MIN_UNTERMINATED or (n >= MIN_TERMINATED and e < size and data[e] == 0):
            starts.add(s)
    return starts


def string_at(data, offset, limit=64):
    """The printable run starting at offset, decoded, or None when the byte there is not printable."""
    m = PRINTABLE.match(data, offset)
    if not m or m.end() - m.start() < 2 or not data[m.start():m.end()].strip():
        return None
    text = data[m.start():min(m.end(), m.start() + limit)].decode("ascii")
    return text.encode("unicode_escape").decode("ascii")


def sample_words(data, max_words=MAX_WORDS):
    """Every 4-byte aligned word as (file offset, value), or a uniform stride of them for a large image."""
    n = len(data) // 4
    stride = max(1, -(-n // max_words))
    values = struct.unpack_from("<%dI" % n, data)
    return [(i * 4, values[i]) for i in range(0, n, stride)], stride


def thumb_plausible(data, offset):
    """The first four halfwords are neither 0x0000 nor 0xFFFF and the entry is not a branch to itself."""
    if offset < 0 or offset & 1 or offset + 8 > len(data):
        return False
    hw = struct.unpack_from("<4H", data, offset)
    if any(h in (0x0000, 0xFFFF) for h in hw):
        return False
    if hw[0] == 0xE7FE or (hw[0] == 0xF7FF and hw[1] == 0xBFFE):
        return False
    return True


def header_hypotheses(vector_offset, size, extra=()):
    """Default list, caller additions, and 'the table sits K past the image start' for K in TABLE_OFFSETS,
    kept to offsets at or before the table and inside the file."""
    hs = set(DEFAULT_HEADERS) | set(extra)
    hs |= {vector_offset - k for k in TABLE_OFFSETS}
    return sorted(h for h in hs if 0 <= h <= vector_offset and h < size)


def base_hypotheses(reset_vector, override=()):
    """The reset handler's address rounded down to each power-of-two alignment from 4 KiB to 1 MiB, unless
    the caller fixed the base. A reset handler placed further from the image start than the image's own
    alignment is not found this way; pass --base then."""
    if override:
        return sorted(set(override))
    entry = reset_vector & ~1
    return sorted({entry & ~(a - 1) for a in BASE_ALIGNMENTS})


def score_mapping(data, table, header, base, pointers, starts):
    size = len(data)
    delta = base - header
    extent = size - header
    reset = table["reset_vector"]
    entry = reset & ~1
    vector_offset = table["file_offset"]

    def in_range(address):
        return header <= address - delta < size

    reset_file = entry - delta
    thumb = in_range(entry) and thumb_plausible(data, reset_file)
    vectors = words_at(data, vector_offset, 16)
    vectors_in_range = sum(1 for v in vectors[1:] if v == 0 or (v & 1 and in_range(v)))
    lo, hi = base, base + extent
    count = hits = 0
    samples = []
    for at, value in pointers:
        if lo <= value < hi:
            count += 1
            f = value - delta
            if f in starts:
                hits += 1
                if len(samples) < SAMPLE_LIMIT:
                    samples.append({"literal_at": hx(at), "value": hx(value, 8), "file_offset": hx(f),
                                    "text": string_at(data, f)})
    return {
        "header_offset": hx(header), "load_base": hx(base, 8), "load_delta": hx(delta, 8),
        "vector_file_offset": hx(vector_offset), "vector_load_address": hx(vector_offset + delta, 8),
        "reset_vector": hx(reset, 8), "reset_file_offset": hx(reset_file) if in_range(entry) else None,
        "coherence": round(hits / count, 4) if count else 0.0,
        "string_pointers": hits, "pointers": count, "pointer_samples": samples,
        "thumb_plausible": thumb, "vectors_ok": vectors_in_range == 15, "vectors_in_range": vectors_in_range,
        "table_aligned_128": (vector_offset + delta) % 128 == 0,
        "equivalent": [], "_delta": delta,
    }


def check_image(data, bases=(), headers=(), resolve=(), max_words=MAX_WORDS, scan_limit=SCAN_LIMIT):
    """Score every mapping hypothesis for the image bytes and return the report dict."""
    tables = find_vector_tables(data, scan_limit)
    starts = string_starts(data)
    sampled, stride = sample_words(data, max_words)
    pointers = [(at, v) for at, v in sampled if in_flash(v)]
    by_delta = {}
    for table in tables:
        hs = header_hypotheses(table["file_offset"], len(data), headers)
        bs = base_hypotheses(table["reset_vector"], bases)
        for base in bs:
            for header in hs:
                delta = base - header
                if delta in by_delta:
                    by_delta[delta]["equivalent"].append({"header_offset": hx(header), "load_base": hx(base, 8),
                                                          "vector_file_offset": hx(table["file_offset"])})
                    continue
                by_delta[delta] = score_mapping(data, table, header, base, pointers, starts)
    ranked = sorted(by_delta.values(), key=lambda c: (-c["coherence"], -c["string_pointers"],
                                                      -c["vectors_in_range"], not c["thumb_plausible"],
                                                      int(c["header_offset"], 16)))
    best = ranked[0] if ranked else None
    runner = ranked[1] if len(ranked) > 1 else None
    decisive = bool(best and best["coherence"] >= DECISIVE_FLOOR and
                    (runner is None or best["coherence"] >= DECISIVE_RATIO * runner["coherence"]))
    report = {
        "schema": SCHEMA, "image_sha256": hashlib.sha256(data).hexdigest(), "image_size": len(data),
        "parameters": {"scan_limit": scan_limit, "max_words": max_words, "sample_stride": stride,
                       "sampled_words": len(sampled), "flash_pointers": len(pointers),
                       "string_starts": len(starts), "base_override": [hx(b, 8) for b in sorted(set(bases))],
                       "extra_headers": [hx(h) for h in sorted(set(headers))]},
        "vector_candidates": [{"file_offset": hx(t["file_offset"]), "initial_sp": hx(t["initial_sp"], 8),
                               "reset_vector": hx(t["reset_vector"], 8)} for t in tables],
        "candidates": ranked, "best": best, "runner_up": runner, "decisive": decisive, "note": NOTE,
    }
    if resolve:
        report["resolutions"] = {hx(a, 8): resolve_address(data, ranked, a) for a in resolve}
    for c in ranked:
        c.pop("_delta", None)
    return report


def resolve_address(data, ranked, address):
    """What a load address resolves to under each mapping, best first."""
    out = []
    for c in ranked:
        delta = c.get("_delta", int(c["load_delta"], 16))
        f = address - delta
        header = int(c["header_offset"], 16)
        entry = {"header_offset": c["header_offset"], "load_base": c["load_base"],
                 "in_range": header <= f < len(data), "file_offset": None, "bytes_hex": None, "text": None}
        if entry["in_range"]:
            entry["file_offset"] = hx(f)
            entry["bytes_hex"] = data[f:f + 16].hex()
            entry["text"] = string_at(data, f)
        out.append(entry)
    return out


def render(report, path):
    lines = [f"image {path}  {report['image_size']} bytes  sha256 {report['image_sha256']}"]
    p = report["parameters"]
    lines.append(f"words sampled {p['sampled_words']} (stride {p['sample_stride']}), flash-window pointers "
                 f"{p['flash_pointers']}, string starts {p['string_starts']}")
    lines.append(f"vector table candidates: {len(report['vector_candidates'])}")
    for t in report["vector_candidates"]:
        lines.append(f"  file {t['file_offset']}: sp {t['initial_sp']} reset {t['reset_vector']}")
    if report["candidates"]:
        lines.append("")
        lines.append(f"{'rank':>4}  {'header':>8}  {'base':>10}  {'table->load':>11}  {'reset->file':>11}  "
                     f"thumb  vec15  align  {'ptrs':>6}  {'strptr':>6}  coherence")
        for i, c in enumerate(report["candidates"], 1):
            yes = lambda b: "yes" if b else "no"
            lines.append(f"{i:>4}  {c['header_offset']:>8}  {c['load_base']:>10}  {c['vector_load_address']:>11}  "
                         f"{str(c['reset_file_offset']):>11}  {yes(c['thumb_plausible']):>5}  "
                         f"{yes(c['vectors_ok']):>5}  {yes(c['table_aligned_128']):>5}  {c['pointers']:>6}  "
                         f"{c['string_pointers']:>6}  {c['coherence']:.4f}")
    lines.append("")
    best, runner = report["best"], report["runner_up"]
    if best is None:
        lines.append("best: none (no vector table candidate in the scanned prefix)")
    else:
        lines.append(f"best: header {best['header_offset']} base {best['load_base']}  "
                     f"(load = {best['load_base']} + (file - {best['header_offset']}))  coherence {best['coherence']:.4f}"
                     + (f"; runner-up {runner['coherence']:.4f} at header {runner['header_offset']} base "
                        f"{runner['load_base']}" if runner else ""))
        for s in best["pointer_samples"]:
            lines.append(f"  e.g. literal at {s['literal_at']} = {s['value']} -> file {s['file_offset']}: {s['text']!r}")
    lines.append(f"decisive: {'yes' if report['decisive'] else 'no'}  (best >= {DECISIVE_RATIO}x runner-up and "
                 f">= {DECISIVE_FLOOR} absolute)")
    lines.append(f"note: {report['note']}")
    for address, rows in (report.get("resolutions") or {}).items():
        lines.append("")
        lines.append(f"resolve {address}:")
        for r in rows:
            where = f"file {r['file_offset']}  {r['bytes_hex']}" if r["in_range"] else "out of range"
            text = f"  {r['text']!r}" if r["text"] else ""
            lines.append(f"  header {r['header_offset']:>7} base {r['load_base']}: {where}{text}")
    return "\n".join(lines)


def parse_int(text):
    return int(text, 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="raw little-endian Cortex-M image file")
    ap.add_argument("--base", action="append", type=parse_int, default=[],
                    help="fix the load base (repeatable); replaces the reset-vector-derived family")
    ap.add_argument("--header", action="append", type=parse_int, default=[],
                    help="extra header-offset hypothesis (repeatable)")
    ap.add_argument("--resolve", action="append", type=parse_int, default=[],
                    help="a load address to resolve under every mapping (repeatable)")
    ap.add_argument("--json", metavar="PATH", help="write the report as JSON to PATH, or '-' for stdout")
    ap.add_argument("--max-words", type=int, default=MAX_WORDS, help="sample at most this many words")
    ap.add_argument("--scan-limit", type=parse_int, default=SCAN_LIMIT, help="bytes of prefix to scan for vector tables")
    args = ap.parse_args(argv)
    try:
        with open(args.image, "rb") as fh:
            data = fh.read()
    except OSError as e:
        print(f"cannot read image: {e}", file=sys.stderr)
        return 2
    if len(data) < 64:
        print("image too small to hold a vector table", file=sys.stderr)
        return 2
    report = check_image(data, bases=args.base, headers=args.header, resolve=args.resolve,
                         max_words=args.max_words, scan_limit=args.scan_limit)
    print(render(report, args.image))
    if args.json == "-":
        print(json.dumps(report, indent=1))
    elif args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=1)
            fh.write("\n")
    return 0 if report["decisive"] else 1


if __name__ == "__main__":
    sys.exit(main())
