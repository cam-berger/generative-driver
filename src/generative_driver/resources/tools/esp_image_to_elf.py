#!/usr/bin/env python3
"""Turn an ESP32 app image (.bin, as read from flash) into a minimal Xtensa ELF that Ghidra's
ELF loader maps with the right addresses, plus a segments.json record.

    python3 tools/esp_image_to_elf.py app.bin out.elf [--strip-app-desc]

Image layout (ESP-IDF app image format): 24-byte esp_image_header_t, then segment_count segments,
each an 8-byte header {load_addr u32, data_len u32} followed by data. The app descriptor
(esp_app_desc_t, 256 bytes) is the first thing in the first segment (offset 32 of the file).
--strip-app-desc zeroes it, which is arm B of the protocol (no version/project/IDF hints).
"""
import json, struct, sys

EM_XTENSA = 94

# chip_id from esp_image_header_t: 0 = ESP32, 2 = ESP32-S2, 5 = ESP32-C3, 9 = ESP32-S3, 13 = ESP32-C6, 16 = ESP32-H2
MAPS = {
    0: [  # classic ESP32 (Technical Reference Manual, ch. 1)
        (0x3F400000, 0x3F800000, "drom", "r--"), (0x3F800000, 0x3FC00000, "psram", "rw-"),
        (0x3FF80000, 0x3FFFC000, "dram", "rw-"), (0x40000000, 0x40070000, "rom", "r-x"),
        (0x40070000, 0x400C0000, "iram", "rwx"), (0x400C0000, 0x400C2000, "rtc_fast", "rwx"),
        (0x400D0000, 0x40400000, "irom", "r-x"), (0x50000000, 0x50002000, "rtc_slow", "rw-")],
    9: [  # ESP32-S3 (TRM ch. 4): flash maps at 0x3C00_0000 (data) and 0x4200_0000 (code); SRAM at 0x3FC8_8000 / 0x4037_0000
        (0x3C000000, 0x3E000000, "drom", "r--"), (0x3FC88000, 0x3FD00000, "dram", "rw-"),
        (0x40000000, 0x40060000, "rom", "r-x"), (0x40370000, 0x403E0000, "iram", "rwx"),
        (0x42000000, 0x44000000, "irom", "r-x"), (0x600FE000, 0x60100000, "rtc_fast", "rwx"),
        (0x50000000, 0x50002000, "rtc_slow", "rw-")],
}

def region(addr, chip_id=0):
    for lo, hi, name, perm in MAPS.get(chip_id, MAPS[0]):
        if lo <= addr < hi:
            return name, perm
    return "other", "rwx"

def parse(buf):
    magic, nseg, spi_mode, spi_sz_sp, entry = struct.unpack_from("<BBBBI", buf, 0)
    if magic != 0xE9:
        raise SystemExit(f"not an ESP image (magic 0x{magic:02x})")
    chip_id, = struct.unpack_from("<H", buf, 12)
    segs, off = [], 24
    for _ in range(nseg):
        load, ln = struct.unpack_from("<II", buf, off); off += 8
        name, perm = region(load, chip_id)
        segs.append({"load_addr": load, "file_off": off, "len": ln, "region": name, "perm": perm})
        off += ln
    return {"entry": entry, "segment_count": nseg, "chip_id": chip_id, "spi_mode": spi_mode,
            "flash_size_code": spi_sz_sp >> 4, "flash_speed_code": spi_sz_sp & 0xF, "segments": segs}

def write_elf(buf, info, out):
    """Segment-only ELF plus matching section headers so both Ghidra and objdump map it."""
    segs = info["segments"]
    ehsize, phsize, shsize = 52, 32, 40
    data_off = ehsize + phsize * len(segs)
    phdrs, blobs, cur, sec_meta = [], [], data_off, []
    counts = {}
    for s in segs:
        p = s["perm"]; flags = (4 if "r" in p else 0) | (2 if "w" in p else 0) | (1 if "x" in p else 0)
        phdrs.append(struct.pack("<IIIIIIII", 1, cur, s["load_addr"], s["load_addr"], s["len"], s["len"], flags, 4))
        blobs.append(buf[s["file_off"]: s["file_off"] + s["len"]])
        counts[s["region"]] = counts.get(s["region"], 0) + 1
        name = "." + s["region"] + (str(counts[s["region"]]) if counts[s["region"]] > 1 else "")
        sec_meta.append((name, cur, s["load_addr"], s["len"], "x" in p, "w" in p))
        cur += s["len"]
    # section header string table
    names = [""] + [m[0] for m in sec_meta] + [".shstrtab"]
    shstr, offs = b"", {}
    for n in names:
        offs[n] = len(shstr); shstr += n.encode() + b"\0"
    shstr_off = cur; cur += len(shstr)
    shoff = (cur + 3) & ~3
    shdrs = [bytes(shsize)]  # null section
    for name, off, addr, ln, x, w in sec_meta:
        sh_flags = 2 | (4 if x else 0) | (1 if w else 0)  # ALLOC | EXECINSTR | WRITE
        shdrs.append(struct.pack("<IIIIIIIIII", offs[name], 1, sh_flags, addr, off, ln, 0, 0, 4, 0))
    shdrs.append(struct.pack("<IIIIIIIIII", offs[".shstrtab"], 3, 0, 0, shstr_off, len(shstr), 0, 0, 1, 0))
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    ehdr = ident + struct.pack("<HHIIIIIHHHHHH", 2, EM_XTENSA, 1, info["entry"], ehsize, shoff, 0,
                               ehsize, phsize, len(segs), shsize, len(shdrs), len(shdrs) - 1)
    with open(out, "wb") as fh:
        fh.write(ehdr); fh.write(b"".join(phdrs)); fh.write(b"".join(blobs)); fh.write(shstr)
        fh.write(b"\0" * (shoff - (shstr_off + len(shstr)))); fh.write(b"".join(shdrs))

def main():
    a = sys.argv[1:]
    strip = "--strip-app-desc" in a; a = [x for x in a if not x.startswith("--")]
    src, out = a[0], a[1]
    buf = bytearray(open(src, "rb").read())
    if strip:
        buf[32:32 + 256] = b"\x00" * 256
    info = parse(buf)
    write_elf(buf, info, out)
    json.dump(info, open(out + ".segments.json", "w", encoding="utf-8"), indent=1)
    print(f"entry 0x{info['entry']:08x}  chip_id {info['chip_id']}  {len(info['segments'])} segments -> {out}")
    for s in info["segments"]:
        print(f"  {s['region']:9s} 0x{s['load_addr']:08x} +0x{s['len']:06x} {s['perm']}")

if __name__ == "__main__":
    main()
