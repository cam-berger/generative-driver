"""Stage 1. Get the best description of the device that this device allows, and say which rung that was.

The ladder is the objective's four classes in order of how much they tell you: a controller whose firmware
can be read, a documented part, a device that describes itself, and nothing at all. `acquire_classify`
walks it and stops at the first rung that yields a description. Every rung it tries is logged with the
reason it was taken or skipped, so the class is a finding rather than a setting.
"""
import array, hashlib, json, os, re, shutil, urllib.request, uuid

from . import core, runner


def _mkdir(run, *parts):
    p = os.path.join(run, *parts)
    os.makedirs(p, exist_ok=True)
    return p


@core.tool("acquire_firmware_artifact")
def acquire_firmware_artifact(run_dir, source_path, origin, expected_sha256,
                              source_url=None, reported_version=None, artifact_version=None, **_):
    """Copy a local firmware artifact and record declared provenance without accessing a device.

    Only the neutral image.bin belongs in the blind interpreter's input mapping. Source names, URLs,
    version reports and the origin declaration remain in the acquisition provenance record.
    """
    if origin not in ("public_release", "device_dump", "provided_binary"):
        return {"available": False, "reason": "Declare origin as public_release, device_dump or provided_binary",
                "_exit": 1}
    source = os.path.abspath(source_path if os.path.isabs(source_path) else os.path.join(core.BENCH, source_path))
    if not os.path.isfile(source):
        return {"available": False, "reason": "source_path must name an existing local regular file; URLs and device nodes are not read",
                "_exit": 1}
    with open(source, "rb") as file:
        data = file.read()
    if not data:
        return {"available": False, "reason": "A firmware artifact cannot be an empty file", "_exit": 1}
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(expected_sha256, str) or digest != expected_sha256.lower():
        return {"available": False, "reason": "SHA256 mismatch; no artifact was imported",
                "sha256": digest, "_exit": 1}
    run = core.resolve_run(run_dir)
    bundle = os.path.join(_mkdir(run, "acquire"), "firmware_artifact")
    try:
        os.mkdir(bundle)
    except FileExistsError:
        return {"available": False, "reason": "A firmware artifact bundle already exists; use a fresh run to preserve its evidence",
                "_exit": 1}
    image = os.path.join(bundle, "image.bin")
    provenance = os.path.join(bundle, "provenance.json")
    record = {"schema": "firmware-acquisition/1", "origin": origin, "origin_basis": "caller_declaration",
              "source_path": source, "source_url": source_url, "reported_version": reported_version,
              "artifact_version": artifact_version, "sha256": digest, "expected_sha256": expected_sha256.lower(),
              "size": len(data), "flash_performed": False, "bit_identical_to_device": "not_verified"}
    with open(image, "xb") as file:
        file.write(data)
    with open(provenance, "x", encoding="utf-8") as file:
        json.dump(record, file, indent=2)
        file.write("\n")
    artifact = os.path.abspath(image)
    provenance_path = os.path.abspath(provenance)
    return {"available": True, "rung": "firmware_artifact", "artifact": artifact,
            "provenance": provenance_path, "sha256": digest, "size": len(data),
            "front_end": "interpret-firmware-binary", "next_tool": "firmware_prepare",
            "inputs_for_interpret": {"image.bin": artifact},
            "_files": {artifact: digest, provenance_path: core.sha256(provenance)}}


# ---------------------------------------------------------------- rung 1: a controller we can read

@core.tool("acquire_flash")
def acquire_flash(run_dir, port, chunked=False, strip_app_desc=False, backend=None, **_):
    """Read an explicitly selected ESP32 controller; a serial port alone is not an ESP identity."""
    if backend != "esp32":
        return {"available": False, "reason": "Flash acquisition requires explicit backend='esp32'; "
                "other serial devices are unsupported. Use acquire_firmware_artifact for a local binary.",
                "_exit": 1}
    if not isinstance(port, str) or not port.strip():
        return {"available": False, "reason": "Select an explicit ESP32 serial port before flash acquisition", "_exit": 1}
    run = core.resolve_run(run_dir)
    out = _mkdir(run, "acquire")
    esptool = [core.VENV_PY, "-m", "esptool"]
    espefuse = [core.VENV_PY, "-m", "espefuse"]
    rc, o, e = core.sh([*esptool, "-p", port, "--before", "no-reset", "--after", "no-reset", "chip-id"])
    if rc != 0:
        return {"available": False, "reason": f"no controller answered on {port}", "_exit": 1}
    chip = next((l.split("Detecting chip type...")[-1].strip() for l in o.splitlines() if "Chip is" in l), None) \
        or next((l.strip() for l in o.splitlines() if "Chip is" in l), "unknown")
    mac = next((l.split()[-1] for l in o.splitlines() if l.startswith("MAC:")), None)
    rc2, fo, fe = core.sh([*espefuse, "-p", port, "--before", "no-reset", "summary"])
    if rc2 != 0:
        return {"available": False, "reason": "Fuse read failed; flash protection state is unknown, so no dump was attempted",
                "chip": chip, "mac": mac, "stderr": fe[-300:], "_exit": 1}
    locked = any(("SPI_BOOT_CRYPT_CNT" in l and "Disable" not in l and "disabled" not in l) or
                 ("SECURE_BOOT_EN" in l and "True" in l) for l in fo.splitlines())
    if locked:
        return {"available": False, "reason": "flash encryption or secure boot is set; class 1 unavailable",
                "chip": chip, "mac": mac, "_exit": 0}
    flash = os.path.join(out, "flash.bin")
    if chunked:
        rc3, do, de = core.sh([core.VENV_PY, os.path.join(core.BENCH, "tools", "read_flash_chunked.py"),
                               port, "8", flash], timeout=1800)
    else:
        rc3, do, de = core.sh([*esptool, "-p", port, "-b", "921600", "--before", "no-reset", "--after",
                               "no-reset", "read-flash", "0", "ALL", flash], timeout=1800)
    if rc3 != 0 or not os.path.exists(flash):
        return {"available": False, "reason": "dump failed", "stderr": de[-300:], "_exit": 1}
    app = os.path.join(out, "app.bin")
    elf = os.path.join(out, "app.elf")
    args = [core.VENV_PY, os.path.join(core.BENCH, "tools", "esp_image_to_elf.py"), app, elf]
    if strip_app_desc:
        args.append("--strip-app-desc")
    rc4, _, ce = core.sh(args)
    if rc4 != 0 or not os.path.isfile(elf):
        return {"available": False, "reason": "Flash dump was retained, but app extraction/conversion did not produce an ELF; "
                "automatic app carving is not implemented", "raw_flash": os.path.abspath(flash),
                "stderr": ce[-300:], "_exit": 1,
                "_files": {os.path.abspath(flash): core.sha256(flash)}}
    files = {core.label(p): core.sha256(p) for p in (flash, app, elf) if os.path.exists(p)}
    return {"available": True, "rung": "flash", "chip": chip, "mac": mac, "artifact": core.label(elf),
            "_files": files}


# ---------------------------------------------------------------- rung 2: a documented part

@core.tool("acquire_datasheet")
def acquire_datasheet(run_dir, part_hint=None, url=None, expected_sha256=None, **_):
    """Resolve a part marking to its vendor datasheet, verify it by hash, and extract its pages as text.

    A cached copy is used only when its hash matches the registry. A part that is not in the registry and
    has no url returns needs_search, which is what the find-datasheet skill is for.
    """
    run = core.resolve_run(run_dir)
    reg = json.load(open(os.path.join(core.BENCH, "toolchain", "datasheets.json"), encoding="utf-8"))
    key = (part_hint or "").strip().lower()
    entry = reg["parts"].get(key)
    if not entry and not url:
        return {"available": False, "needs_search": True, "part": part_hint,
                "reason": "part not in the registry and no url given", "_exit": 0}
    entry = entry or {"url": url, "sha256": expected_sha256, "vendor": None, "revision": None, "title": None}
    out = _mkdir(run, "acquire")
    pdf = os.path.join(out, "datasheet.pdf")
    src, how = None, None
    cache = entry.get("cache") and os.path.join(core.BENCH, entry["cache"])
    if cache and os.path.exists(cache) and (not entry.get("sha256") or core.sha256(cache) == entry["sha256"]):
        shutil.copyfile(cache, pdf); src, how = entry["cache"], "cache, hash verified"
    else:
        with urllib.request.urlopen(entry["url"], timeout=120) as r, open(pdf, "wb") as fh:
            fh.write(r.read())
        src, how = entry["url"], "fetched"
    got = core.sha256(pdf)
    if entry.get("sha256") and got != entry["sha256"]:
        return {"available": False, "reason": f"sha256 mismatch: {got}", "_exit": 1}
    import pypdf
    pages = _mkdir(out, "pages")
    reader = pypdf.PdfReader(pdf)
    index = []
    for i, pg in enumerate(reader.pages, 1):
        txt = pg.extract_text() or ""
        open(os.path.join(pages, f"{i:03d}.txt"), "w", encoding="utf-8").write(txt)
        first = next((l.strip() for l in txt.splitlines() if l.strip()), "")
        index.append({"page": i, "chars": len(txt), "first_line": first[:90]})
    json.dump(index, open(os.path.join(pages, "index.json"), "w", encoding="utf-8"), indent=1)
    return {"available": True, "rung": "datasheet", "artifact": core.label(pdf),
            "pages_dir": core.label(pages), "page_count": len(index),
            "vendor": entry.get("vendor"), "revision": entry.get("revision"), "source": src, "how": how,
            "_files": {core.label(pdf): got}}


# ---------------------------------------------------------------- rung 3: the device describes itself

PROTOCOL_REGISTRY = os.path.join(core.BENCH, "toolchain", "protocols.json")
MS_OS_20_UUID = "D8DD60DF-4589-4CC7-9CD2-659D9E648A9F"
_TRANSFER = ("control", "isochronous", "bulk", "interrupt")
_DEVICE = (("bLength", 1), ("bDescriptorType", 1), ("bcdUSB", 2), ("bDeviceClass", 1), ("bDeviceSubClass", 1),
           ("bDeviceProtocol", 1), ("bMaxPacketSize0", 1), ("idVendor", 2), ("idProduct", 2), ("bcdDevice", 2),
           ("iManufacturer", 1), ("iProduct", 1), ("iSerialNumber", 1), ("bNumConfigurations", 1))
_CONFIG = (("bLength", 1), ("bDescriptorType", 1), ("wTotalLength", 2), ("bNumInterfaces", 1),
           ("bConfigurationValue", 1), ("iConfiguration", 1), ("bmAttributes", 1), ("bMaxPower", 1))
_INTERFACE = (("bLength", 1), ("bDescriptorType", 1), ("bInterfaceNumber", 1), ("bAlternateSetting", 1),
              ("bNumEndpoints", 1), ("bInterfaceClass", 1), ("bInterfaceSubClass", 1), ("bInterfaceProtocol", 1),
              ("iInterface", 1))
_ENDPOINT = (("bLength", 1), ("bDescriptorType", 1), ("bEndpointAddress", 1), ("bmAttributes", 1),
             ("wMaxPacketSize", 2), ("bInterval", 1))
_IAD = (("bLength", 1), ("bDescriptorType", 1), ("bFirstInterface", 1), ("bInterfaceCount", 1),
        ("bFunctionClass", 1), ("bFunctionSubClass", 1), ("bFunctionProtocol", 1), ("iFunction", 1))


def _fields(raw, layout):
    out, i = {}, 0
    for name, size in layout:
        out[name] = int.from_bytes(raw[i:i + size], "little")
        i += size
    return out


def _pick(record, names):
    return {k: record[k] for k in names}


def _u16(b, i):
    return b[i] | b[i + 1] << 8


class _Unreadable(Exception):
    """A mandatory descriptor could not be read or parsed; the device, not the caller, is at fault."""


class _Reader:
    """The only path to one device. Every request is checked against the read-only allow-list before it is
    sent and logged whatever happens, so descriptor.json accounts for every byte the host asked for."""

    def __init__(self, dev, errors):
        self.dev, self.errors, self.log, self.vendor_code = dev, errors, [], None
        self.device, self.langids, self.langid_error, self.strings, self.string_errors = None, None, None, {}, {}

    def allowed(self, rt, req, value, index):
        return ((rt == 0x80 and req == 6) or (rt == 0x81 and req == 6 and value >> 8 == 0x22)
                or (rt == 0xC0 and req == self.vendor_code and value == 0 and index == 7))

    def get(self, rt, req, value, index, length):
        if not self.allowed(rt, req, value, index):
            raise PermissionError(f"request {rt:#04x}/{req} {value:#06x} is not a descriptor read")
        entry = {"bmRequestType": rt, "bRequest": req, "wValue": value, "wIndex": index, "wLength": length,
                 "ok": False, "received": 0, "error": None}
        self.log.append(entry)
        try:
            if rt & 0x1F == 1:
                # pyusb's Device.ctrl_transfer claims the recipient interface before a standard interface
                # request; the backend call underneath it sends the same setup packet without the claim
                ctx = self.dev._ctx
                ctx.managed_open()
                buf = array.array("B", bytes(length))
                data = bytes(buf[:ctx.backend.ctrl_transfer(ctx.handle, rt, req, value, index, buf, 1000)])
            else:
                data = bytes(self.dev.ctrl_transfer(rt, req, value, index, length, 1000))
        except self.errors as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            return None
        entry.update(ok=True, received=len(data))
        return data

    def device_descriptor(self):
        if self.device is None:
            raw = self.get(0x80, 6, 0x0100, 0, 18)
            if raw is None or len(raw) < 18 or raw[1] != 1:
                raise _Unreadable("device descriptor: " + (self.log[-1]["error"] or f"{(raw or b'').hex()!r} is not one"))
            self.device = {"raw_hex": raw[:18].hex(), **_fields(raw, _DEVICE)}
        return self.device

    def string(self, index):
        """Text of one string descriptor in the first LANGID, or None with the reason kept in string_errors."""
        key = str(index)
        if key in self.strings or key in self.string_errors:
            return self.strings.get(key, {}).get("text")
        if self.langids is None:
            raw = self.get(0x80, 6, 0x0300, 0, 255)
            ok = raw is not None and len(raw) >= 2 and raw[1] == 3
            self.langids = [_u16(raw, i) for i in range(2, min(raw[0], len(raw)) - 1, 2)] if ok else []
            if not self.langids:
                self.langid_error = ("string descriptor 0 read failed: " + self.log[-1]["error"] if raw is None
                                     else f"string descriptor 0 lists no LANGID: {raw.hex()}")
        if not self.langids:
            self.string_errors[key] = self.langid_error
            return None
        langid = self.langids[0]
        raw = self.get(0x80, 6, 0x0300 | index, langid, 255)
        if raw is None:
            self.string_errors[key] = self.log[-1]["error"]
        elif len(raw) < 2 or raw[1] != 3:
            self.string_errors[key] = f"not a string descriptor: {raw.hex()}"
        else:
            body = raw[2:min(raw[0], len(raw))]
            self.strings[key] = {"langid": langid, "raw_hex": raw.hex(),
                                 "text": body[:len(body) & ~1].decode("utf-16-le", "replace")}
        return self.strings.get(key, {}).get("text")


def _parse_configuration(raw, problems):
    """Walk one configuration bundle. Class-specific descriptors are kept as hex on the interface or endpoint
    they follow, as libusb attaches them; interface associations are listed on their own."""
    head = _fields(raw, _CONFIG)
    config = {"raw_hex": raw.hex(), **_pick(head, ("bConfigurationValue", "iConfiguration", "bmAttributes",
                                                   "bMaxPower", "wTotalLength", "bNumInterfaces")),
              "interfaces": [], "interface_associations": []}
    if len(raw) < head["wTotalLength"]:
        problems.append(f"configuration {head['bConfigurationValue']}: {len(raw)} of {head['wTotalLength']} bytes received")
    i, owner = max(raw[0], 9), None
    while i < len(raw):
        n = raw[i]
        if n < 2 or i + n > len(raw):
            problems.append(f"configuration {head['bConfigurationValue']}: bLength {n} at offset {i}; walk stopped")
            break
        d, kind = raw[i:i + n], raw[i + 1]
        if kind == 4 and n >= 9:
            owner = {**_pick(_fields(d, _INTERFACE), ("bInterfaceNumber", "bAlternateSetting", "bInterfaceClass",
                                                      "bInterfaceSubClass", "bInterfaceProtocol", "iInterface")),
                     "interface_string": None, "extra_hex": "", "endpoints": []}
            config["interfaces"].append(owner)
        elif kind == 5 and n >= 7 and config["interfaces"]:
            ep = _fields(d, _ENDPOINT)
            owner = {"bEndpointAddress": ep["bEndpointAddress"],
                     "direction": "in" if ep["bEndpointAddress"] & 0x80 else "out",
                     "transfer": _TRANSFER[ep["bmAttributes"] & 3], "wMaxPacketSize": ep["wMaxPacketSize"],
                     "bInterval": ep["bInterval"], "extra_hex": ""}
            config["interfaces"][-1]["endpoints"].append(owner)
        elif kind == 0x0B and n >= 8:
            config["interface_associations"].append(_pick(_fields(d, _IAD), (
                "bFirstInterface", "bInterfaceCount", "bFunctionClass", "bFunctionSubClass", "bFunctionProtocol",
                "iFunction")))
        elif owner is not None:
            owner["extra_hex"] += d.hex()
        i += n
    return config


def _parse_bos(raw):
    caps, i = [], raw[0] if raw else 0
    while i + 3 <= len(raw):
        n = raw[i]
        if n < 3 or i + n > len(raw):
            break
        d = raw[i:i + n]
        caps.append({"bDevCapabilityType": d[2], "raw_hex": d.hex(),
                     "platform_uuid": str(uuid.UUID(bytes_le=bytes(d[4:20]))).upper() if d[2] == 5 and n >= 20 else None})
        i += n
    return {"raw_hex": raw.hex(), "capabilities": caps}


def _parse_ms_os_20(raw, problems):
    """Compatible IDs and registry properties, each tagged with the interface of the function subset holding
    it (None outside one). String-typed property values decode from UTF-16LE, a multi-string one joined by
    newlines; other types stay hex."""
    compat, props, i, function, end = [], [], 0, None, 0
    while i + 4 <= len(raw):
        n, kind = _u16(raw, i), _u16(raw, i + 2)
        if n < 4 or i + n > len(raw):
            problems.append(f"MS OS 2.0 set: wLength {n} at offset {i}; walk stopped")
            break
        if i >= end:
            function = None
        d = raw[i:i + n]
        if kind == 2 and n >= 8:
            function, end = d[4], i + _u16(d, 6)
        elif kind == 3 and n >= 20:
            compat.append({"interface": function, "compatible_id": d[4:12].decode("ascii", "replace").rstrip("\0"),
                           "sub_compatible_id": d[12:20].decode("ascii", "replace").rstrip("\0")})
        elif kind == 4 and n >= 10:
            dtype, name_len = _u16(d, 4), _u16(d, 6)
            if 10 + name_len > n or 10 + name_len + _u16(d, 8 + name_len) > n:
                problems.append(f"MS OS 2.0 set: registry property at offset {i} overruns its wLength")
            else:
                value = d[10 + name_len:10 + name_len + _u16(d, 8 + name_len)]
                text = value.decode("utf-16-le", "replace") if dtype in (1, 2, 6, 7) else None
                props.append({"interface": function,
                              "name": d[8:8 + name_len].decode("utf-16-le", "replace").rstrip("\0"),
                              "value": value.hex() if text is None else "\n".join(p for p in text.split("\0") if p)})
        i += n
    return compat, props


def _hid_report_length(extra_hex):
    """wDescriptorLength of the first report descriptor a HID descriptor in the interface's extras names."""
    extra, i = bytes.fromhex(extra_hex), 0
    while i + 2 <= len(extra) and extra[i] >= 2:
        d = extra[i:i + extra[i]]
        if d[1] == 0x21:
            for k in range(6, len(d) - 2, 3):
                if d[k] == 0x22:
                    return _u16(d, k + 1)
        i += extra[i]
    return None


def _protocol_registry():
    with open(PROTOCOL_REGISTRY, encoding="utf-8") as fh:
        return json.load(fh)


@core.tool("acquire_selfdescription")
def acquire_selfdescription(run_dir, transport=None, address=None, serial_number=None, **_):
    """Read one explicitly selected USB device's standard descriptors into descriptor.json, raw and parsed.

    Only standard GET_DESCRIPTOR reads, HID report descriptor reads, and the MS OS 2.0 descriptor-set read
    that the device's own BOS advertises are ever sent; nothing is claimed or configured and no class,
    bulk or interrupt transfer is made. address is vid:pid in hex. ENDPOINT.md beside it tells the blind
    interpreter what the host binds by without handing it the device.
    """
    def fail(reason, code, **k):
        return {"available": False, "rung": "selfdescription", "transport": transport, "reason": reason,
                "_exit": code, **k}
    if transport != "usb":
        return fail(f"transport {transport!r} has no self-description backend; only 'usb' is built", 1)
    m = re.fullmatch(r"([0-9A-Fa-f]{4}):([0-9A-Fa-f]{4})", address) if isinstance(address, str) else None
    if not m:
        return fail("address must be the operator's vid:pid, four hex digits each, e.g. 1209:7a01", 1)
    if serial_number is not None and (not isinstance(serial_number, str) or not serial_number):
        return fail("serial_number, when given, must be the non-empty serial string", 1)
    vid, pid = int(m.group(1), 16), int(m.group(2), 16)
    out = os.path.join(core.resolve_run(run_dir), "acquire", "selfdescription")
    descriptor, endpoint = os.path.join(out, "descriptor.json"), os.path.join(out, "ENDPOINT.md")
    if os.path.exists(descriptor) or os.path.exists(endpoint):
        return fail("a self-description already exists in this run; use a fresh run to preserve its evidence", 1)
    registry = _protocol_registry()["protocols"]
    import usb.core, usb.util
    try:
        devices = list(usb.core.find(find_all=True, idVendor=vid, idProduct=pid))
    except (usb.core.NoBackendError, usb.core.USBError) as e:
        return fail(f"USB enumeration failed: {type(e).__name__}: {e}", 4)
    notes, problems = [], []
    try:
        if serial_number is None and len(devices) != 1:
            return fail(f"{address} matched {len(devices)} devices; exactly one is required"
                        + (", so name it by serial_number" if devices else ""), 1)
        readers = [_Reader(d, usb.core.USBError) for d in devices]
        if serial_number is not None:
            unreadable, chosen = 0, []
            for n, r in enumerate(readers):
                try:
                    dev = r.device_descriptor()
                    got = r.string(dev["iSerialNumber"]) if dev["iSerialNumber"] else None
                except _Unreadable as e:
                    unreadable, got = unreadable + 1, f"<unreadable: {e}>"
                if got == serial_number:
                    chosen.append(r)
                else:
                    notes.append(f"candidate {n} rejected, serial {got!r}; requests {json.dumps(r.log)}")
            if len(chosen) != 1:
                return fail(f"{address} with serial {serial_number!r} matched {len(chosen)} of {len(devices)} devices; "
                            f"exactly one is required ({unreadable} unreadable)", 4 if unreadable and not chosen else 1,
                            _notes=notes)
            readers = chosen
        r = readers[0]
        try:
            dev = r.device_descriptor()
            if (dev["idVendor"], dev["idProduct"]) != (vid, pid):
                return fail(f"device descriptor reports {dev['idVendor']:04x}:{dev['idProduct']:04x}, not {address}", 2,
                            requests=r.log, _notes=notes)
            configurations = []
            for index in range(dev["bNumConfigurations"]):
                head = r.get(0x80, 6, 0x0200 | index, 0, 9)
                if head is None or len(head) < 9 or head[1] != 2:
                    raise _Unreadable(f"configuration {index} header: " + (r.log[-1]["error"] or (head or b"").hex()))
                raw = r.get(0x80, 6, 0x0200 | index, 0, _u16(head, 2))
                if raw is None or len(raw) < 9 or raw[1] != 2:
                    raise _Unreadable(f"configuration {index}: " + (r.log[-1]["error"] or (raw or b"").hex()))
                configurations.append(_parse_configuration(raw, problems))
        except _Unreadable as e:
            return fail(f"mandatory descriptor unreadable: {e}", 4, requests=r.log, _notes=notes + problems)
        indices = [dev["iManufacturer"], dev["iProduct"], dev["iSerialNumber"]]
        for c in configurations:
            indices += [c["iConfiguration"]] + [i["iInterface"] for i in c["interfaces"]] \
                + [a["iFunction"] for a in c["interface_associations"]]
        for index in sorted({i for i in indices if i}):
            r.string(index)
        for c in configurations:
            for i in c["interfaces"]:
                i["interface_string"] = r.strings.get(str(i["iInterface"]), {}).get("text") if i["iInterface"] else None
        bos = ms_os_20 = None
        if dev["bcdUSB"] >= 0x0201:
            head = r.get(0x80, 6, 0x0F00, 0, 5)
            if head is not None and len(head) >= 5 and head[1] == 0x0F:
                raw = r.get(0x80, 6, 0x0F00, 0, _u16(head, 2))
                bos = _parse_bos(raw) if raw is not None else None
        for cap in (bos or {}).get("capabilities", []):
            d = bytes.fromhex(cap["raw_hex"])
            if cap["platform_uuid"] == MS_OS_20_UUID and len(d) >= 28:
                r.vendor_code = d[26]
                raw = r.get(0xC0, d[26], 0, 7, _u16(d, 24))
                if raw is not None:
                    compat, props = _parse_ms_os_20(raw, problems)
                    ms_os_20 = {"vendor_code": d[26], "raw_hex": raw.hex(), "compatible_ids": compat,
                                "registry_properties": props}
                break
        hid_reports = {}
        for c in configurations:
            for i in c["interfaces"]:
                key = str(i["bInterfaceNumber"])
                if i["bInterfaceClass"] != 3 or key in hid_reports:
                    continue
                length = _hid_report_length(i["extra_hex"])
                if length is None:
                    problems.append(f"HID interface {key} carries no HID descriptor naming a report length; not read")
                    continue
                raw = r.get(0x81, 6, 0x2200, i["bInterfaceNumber"], length)
                if raw is not None:
                    hid_reports[key] = raw.hex()
        texts = [s["text"] for s in r.strings.values()]
        named = sorted(k for k, p in registry.items() if any(s in t for s in p.get("match", []) for t in texts))
        serial_text = r.strings.get(str(dev["iSerialNumber"]), {}).get("text") if dev["iSerialNumber"] else None
        record = {"schema": "usb-selfdescription/1", "transport": "usb",
                  "selection": {"vendor_id": vid, "product_id": pid, "serial_number": serial_number},
                  "device": dev, "configurations": configurations,
                  "strings": {"langids": r.langids or [], "by_index": r.strings, "errors": r.string_errors},
                  "bos": bos, "ms_os_20": ms_os_20, "hid_reports": hid_reports, "requests": r.log,
                  "named_protocols": named}
    finally:
        for d in devices:
            usb.util.dispose_resources(d)
    os.makedirs(out, exist_ok=True)
    with open(descriptor, "x", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
        fh.write("\n")
    binds = "vendor id, product id and serial number" if serial_number is not None else "vendor id and product id"
    with open(endpoint, "x", encoding="utf-8") as fh:
        fh.write(f"# Endpoint\n\nThe channel type is usb.\n\nThe host binds the device by {binds}, which the "
                 f"operator supplies.\n\nThe interpreter has no access to the device.\n")
    descriptor, endpoint = os.path.abspath(descriptor), os.path.abspath(endpoint)
    digest = core.sha256(descriptor)
    failed = [q for q in r.log if not q["ok"]]
    return {"available": True, "rung": "selfdescription", "transport": "usb",
            "identity": {"vendor_id": dev["idVendor"], "product_id": dev["idProduct"], "bcdDevice": dev["bcdDevice"],
                         "serial_number": serial_text},
            "artifact": descriptor, "sha256": digest, "named_protocols": named,
            "front_end": "interpret-selfdescribing", "next_tool": "interpret_run",
            "inputs_for_interpret": {"descriptor.json": descriptor, "ENDPOINT.md": endpoint},
            "requests": len(r.log), "failed_requests": len(failed),
            "_files": {descriptor: digest, endpoint: core.sha256(endpoint)},
            "_notes": notes + problems + [f"request failed: {json.dumps(q)}" for q in failed]}


@core.tool("acquire_protocol_spec")
def acquire_protocol_spec(run_dir, key, **_):
    """Fetch the public specification of a protocol a device names in its own strings, verify every source
    against the registry's sha256, and split long sources into sections for the interpreter.

    `key` is a protocols.json key, as acquire_selfdescription returns in named_protocols. Nothing is
    written unless every source matches; a source is taken from its `cache` only when that copy matches too.
    """
    def fail(reason, **k):
        return {"available": False, "rung": "protocol_spec", "key": key, "reason": reason, "_exit": 1, **k}
    registry = _protocol_registry()["protocols"]
    entry = registry.get(key) if isinstance(key, str) else None
    if not entry:
        return fail("key is not in the protocol registry", known=sorted(registry))
    sources = entry.get("sources") or []
    names = [s.get("name") for s in sources]
    if not sources or len(set(names)) != len(names) or any(
            not isinstance(n, str) or n in ("", ".", "..", "sections") or os.path.basename(n) != n for n in names):
        return fail("the registry entry needs sources with distinct plain file names")
    run = core.resolve_run(run_dir)
    spec = os.path.join(run, "acquire", "spec")
    if os.path.exists(spec):
        return fail("a protocol spec already exists in this run; use a fresh run to preserve its evidence")
    fetched = []
    for s in sources:
        data, how = None, None
        cache = s.get("cache") and os.path.join(core.BENCH, s["cache"])
        if cache and os.path.isfile(cache):
            with open(cache, "rb") as fh:
                data, how = fh.read(), "cache, hash verified"
            if hashlib.sha256(data).hexdigest() != s["sha256"]:
                data = None
        if data is None:
            try:
                with urllib.request.urlopen(s["url"], timeout=120) as reply:
                    data, how = reply.read(), "fetched"
            except OSError as e:
                return fail(f"{s['name']}: fetch failed: {type(e).__name__}: {e}; nothing was written")
        got = hashlib.sha256(data).hexdigest()
        if got != s["sha256"]:
            return fail(f"{s['name']}: sha256 mismatch, got {got}; nothing was written", sha256=got)
        fetched.append((s, data, how))
    sections = []
    for s, data, _ in fetched:
        if not s.get("split"):
            continue
        parts = data.decode("utf-8-sig").split(s["split"])
        line = 1
        for text in [parts[0]] + [s["split"] + p for p in parts[1:]]:
            first = next((l.strip() for l in text.splitlines() if l.strip()), "")
            sections.append({"section": len(sections), "source": s["name"], "start_line": line,
                             "first_line": first[:90], "chars": len(text), "_text": text})
            line += text.count("\n")
    _mkdir(run, "acquire")
    os.mkdir(spec)
    files, inputs = {}, {}
    for s, data, _ in fetched:
        path = os.path.abspath(os.path.join(spec, s["name"]))
        with open(path, "xb") as fh:
            fh.write(data)
        files[path], inputs[f"spec/{s['name']}"] = s["sha256"], path
    sections_dir = None
    if sections:
        sections_dir = os.path.abspath(os.path.join(spec, "sections"))
        os.mkdir(sections_dir)
        for sec in sections:
            path = os.path.join(sections_dir, f"{sec['section']:03d}.txt")
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(sec.pop("_text"))
            files[path] = core.sha256(path)
        index = os.path.join(sections_dir, "index.json")
        with open(index, "x", encoding="utf-8") as fh:
            json.dump(sections, fh, indent=1)
        files[index] = core.sha256(index)
        inputs["spec/sections"] = sections_dir
    return {"available": True, "rung": "protocol_spec", "key": key, "title": entry.get("title"),
            "publisher": entry.get("publisher"), "rendered": entry.get("rendered"),
            "sources": [{"name": s["name"], "url": s["url"], "sha256": s["sha256"], "how": how} for s, _, how in fetched],
            "sections_dir": sections_dir, "section_count": len(sections),
            "inputs_for_interpret": inputs, "_files": files}


# ---------------------------------------------------------------- rung 4: nothing but the bus

@core.tool("acquire_bus_scan")
def acquire_bus_scan(run_dir, bus_url=None, speed_hz=100000, **_):
    """Poll every 7-bit address in the write direction and report what acknowledged."""
    if not isinstance(bus_url, str) or not bus_url.startswith("ftdi://"):
        return {"ok": False, "fault": "operator", "reason": "Select an explicit ftdi:// bus_url", "_exit": 4}
    run = core.resolve_run(run_dir)
    be = runner.FtdiI2c(bus_url, speed_hz)
    try:
        acks = [f"0x{a:02x}" for a in range(0x08, 0x78) if be.probe(a)]
    finally:
        be.close()
    p = os.path.join(_mkdir(run, "acquire"), "scan.json")
    with open(p, "w", encoding="utf-8") as file:
        json.dump({"acks": acks, "bus_url": bus_url}, file, indent=1)
    return {"acks": acks, "scan": core.label(p), "_files": {core.label(p): core.sha256(p)}}


@core.tool("acquire_register_sweep")
def acquire_register_sweep(run_dir, address, bus_url=None, speed_hz=100000,
                           snapshots=20, interval_ms=500, labels=None, **_):
    """Read every register of one address repeatedly and report what is constant and what moves.

    Read-only, and opt-in: on some parts a read has a side effect. This is the only description a class-4
    device gives, so it is the black-box front-end's input.
    """
    import time
    if not isinstance(bus_url, str) or not bus_url.startswith("ftdi://"):
        return {"ok": False, "fault": "operator", "reason": "Select an explicit ftdi:// bus_url", "_exit": 4}
    run = core.resolve_run(run_dir)
    addr = runner.hx(address)
    be = runner.FtdiI2c(bus_url, speed_hz)
    snaps = []
    try:
        for i in range(int(snapshots)):
            row = bytearray()
            for base in range(0, 256, 16):
                row += be.reg_read(addr, base, 16)
            snaps.append({"i": i, "t_wall": time.time(), "label": (labels or [None] * snapshots)[i]
                          if labels and i < len(labels) else None, "regs": bytes(row).hex()})
            if i < snapshots - 1:
                time.sleep(interval_ms / 1000.0)
    finally:
        be.close()
    cols = [bytes.fromhex(s["regs"]) for s in snaps]
    constant = [r for r in range(256) if len({c[r] for c in cols}) == 1]
    changing = [r for r in range(256) if len({c[r] for c in cols}) > 1]
    ident = [{"reg": f"0x{r:02x}", "value": f"0x{cols[0][r]:02x}"} for r in constant
             if cols[0][r] not in (0x00, 0xFF)][:8]
    p = os.path.join(_mkdir(run, "acquire"), "sweep.json")
    json.dump({"address": f"0x{addr:02x}", "snapshots": snaps,
               "constant": [f"0x{r:02x}" for r in constant], "changing": [f"0x{r:02x}" for r in changing],
               "identity_candidates": ident}, open(p, "w", encoding="utf-8"), indent=1)
    return {"sweep": core.label(p), "constant_count": len(constant),
            "changing_count": len(changing), "identity_candidates": ident,
            "_files": {core.label(p): core.sha256(p)}}


# ---------------------------------------------------------------- the ladder

@core.tool("acquire_classify")
def acquire_classify(run_dir, bus_url=None, port=None, part_hint=None,
                     transports=None, allow_register_sweep=False, flash_backend=None, address=None,
                     serial_number=None, **_):
    """Walk the ladder and stop at the first rung that yields a description of the device.

    Returns the class (1 readable controller, 2 documented part, 3 self-describing, 4 black box), the
    description artifact, and the log of every rung tried with why it was taken or skipped. Naming
    `transports` (with the operator's `address` and optional `serial_number`) selects the self-describing
    device: rungs 1 and 2 run only for an explicit port or part hint, and a rung-3 miss ends the walk
    instead of scanning the I2C bus.
    """
    rungs = []
    if bus_url is None and port is None and part_hint is None and transports is None:
        return {"device_class": None, "rungs": [], "fault": "operator",
                "reason": "Select an explicit bus_url, controller port, part hint or self-describing transport", "_exit": 4}
    if isinstance(transports, str):
        transports = [transports]
    if transports is not None and (not isinstance(transports, (list, tuple)) or not transports):
        return {"device_class": None, "rungs": rungs, "_exit": 1,
                "reason": "transports, when given, must name at least one transport, e.g. ['usb']"}
    if port is not None and flash_backend != "esp32":
        reason = ("A serial endpoint does not select a flash backend. Pass flash_backend='esp32' only "
                  "for an explicitly selected ESP32 controller, or use acquire_firmware_artifact for "
                  "a local binary. No hardware acquisition was attempted.")
        return {"device_class": None, "unsupported": True, "reason": reason,
                "rungs": [{"rung": 1, "name": "flash", "tried": False, "result": reason}], "_exit": 1}
    if port is not None:
        r = acquire_flash(run_dir=run_dir, port=port, backend=flash_backend)
        rungs.append({"rung": 1, "name": "flash", "tried": True, "result": r.get("reason") or "dumped",
                      "event": r.get("_event")})
        if r.get("available"):
            return {"device_class": 1, "front_end": "interpret-firmware", "artifact": r["artifact"],
                    "rungs": rungs, "inputs_for_interpret": {"elf": r["artifact"]}}
        if not part_hint and transports is None:
            return {"device_class": None, "rungs": rungs,
                    "reason": "Firmware acquisition on the selected serial endpoint failed; no other bus was scanned. "
                              + r.get("reason", "See the acquisition event for details."),
                    "next_tool": "acquire_firmware_artifact", "_exit": 1}
    else:
        rungs.append({"rung": 1, "name": "flash", "tried": False,
                      "result": "no controller port given: the device under compilation is not a controller of ours"})

    if transports is not None and not part_hint:
        rungs.append({"rung": 2, "name": "datasheet", "tried": False,
                      "result": "no part hint given: a named transport selects a device that describes itself"})
    else:
        r = acquire_datasheet(run_dir=run_dir, part_hint=part_hint)
        rungs.append({"rung": 2, "name": "datasheet", "tried": True,
                      "result": ("obtained rev %s" % r.get("revision")) if r.get("available") else r.get("reason"),
                      "event": r.get("_event")})
        if r.get("available"):
            return {"device_class": 2, "front_end": "interpret-datasheet", "artifact": r["artifact"],
                    "rungs": rungs,
                    "inputs_for_interpret": {"datasheet.pdf": r["artifact"], "pages": r["pages_dir"]}}
        if port is not None and transports is None:
            return {"device_class": None, "rungs": rungs,
                    "reason": "Neither the selected serial firmware acquisition nor the requested datasheet was available; "
                              "no other bus was scanned. Supply a local firmware artifact or an explicit datasheet source.",
                    "next_tool": "acquire_firmware_artifact", "_exit": 1}

    if transports is not None:
        code = 1
        for transport in transports:
            r = acquire_selfdescription(run_dir=run_dir, transport=transport, address=address,
                                        serial_number=serial_number)
            rungs.append({"rung": 3, "name": "selfdescription", "transport": transport, "tried": True,
                          "result": "described" if r.get("available") else r.get("reason") or r.get("error"),
                          "event": r.get("_event")})
            if r.get("available"):
                return {"device_class": 3, "front_end": r["front_end"], "artifact": r["artifact"], "rungs": rungs,
                        "identity": r["identity"], "named_protocols": r["named_protocols"],
                        "next_tool": r["next_tool"], "inputs_for_interpret": r["inputs_for_interpret"]}
            code = max(code, 4 if r.get("_exit") == 4 else 1)
        rungs.append({"rung": 4, "name": "blackbox", "tried": False,
                      "result": "a transport was named, so no other bus was scanned"})
        return {"device_class": None, "rungs": rungs, "_exit": code,
                "reason": "The named transport gave no self-description, and no other bus was scanned: "
                          + "; ".join(f"{g['transport']}: {g['result']}" for g in rungs if g["rung"] == 3)}

    r = acquire_selfdescription(run_dir=run_dir, transport=(transports or [None])[0])
    rungs.append({"rung": 3, "name": "selfdescription", "tried": True, "result": r.get("reason"),
                  "event": r.get("_event")})
    if r.get("available"):
        return {"device_class": 3, "front_end": "interpret-selfdescribing", "artifact": r["artifact"], "rungs": rungs}

    scan = acquire_bus_scan(run_dir=run_dir, bus_url=bus_url)
    rungs.append({"rung": 4, "name": "blackbox", "tried": True,
                  "result": f"bus answered at {scan.get('acks')}", "event": scan.get("_event")})
    if not scan.get("acks"):
        return {"device_class": None, "rungs": rungs, "reason": "nothing on the bus", "_exit": 1}
    inputs = {"scan": scan["scan"]}
    if allow_register_sweep:
        sw = acquire_register_sweep(run_dir=run_dir, address=scan["acks"][0], bus_url=bus_url)
        inputs["sweep"] = sw.get("sweep")
    return {"device_class": 4, "front_end": "interpret-blackbox", "artifact": scan["scan"],
            "rungs": rungs, "inputs_for_interpret": inputs}
