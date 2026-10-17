# Executable interface model, schema 6

One model describes one channel/device using supplied evidence. Record unknown semantics in `NOTES.md`; the host supplies physical bindings. Validation checks structure, not firmware fidelity or physical safety.

Schema 6 adds USB frames, byte payloads, string/bit decoders and request/reply matching to schemas 3–5. Use `interface-model/6` for these features. Earlier models remain valid under their own tags. This guide includes the shared contract needed to use this version.

## Transport and engine

`interface_runtime.transports`:

```python
class TransportError(OSError):
    code: str

def capabilities() -> dict: ...
def validate_channel(channel: dict) -> None: ...
def open_transport(channel: dict, binding: dict): ...

# Returned transport owns its handle and deadline-bounded I/O.
transport.exchange(tx: bytes, rx: dict, timeout_ms: int) -> bytes
transport.control(setup: dict, tx: bytes, rx: dict, timeout_ms: int) -> bytes
transport.close() -> None
```

`interface_runtime.engine`:

```python
def validate_model(model: dict) -> dict:  # ok, defects[{where,what}], checks_run
    ...
def execute(model: dict, operation: str, parameters=None, *, binding=None,
            allow_effects=(), transport_factory=None, delay_fn=None) -> dict:
    ...
```

`transport_factory` lazily defaults to `open_transport(channel, binding)`; replay uses the same engine. Each execution opens one transport, verifies identity, runs the requested operation and closes it. Remaining buffered bytes are discarded at close. Unsupported mode/method pairs fail before sending. Adapters enforce bounds independently of validation and close after uncertain transaction failure.

| Channel | Protocol settings | Host binding / modes |
|---|---|---|
| I2C | `address_7bit`, `speed_hz` | `{"url":"ftdi://..."}`; `exact` |
| SPI | `cs`, `clock_hz`, `mode`, `bit_order` (`msb_first` only) | FTDI URL; `exact` |
| UART | `baudrate`; optional `bytesize`, `parity`, `stopbits` | `{"port":"..."}`; `exact`, `until`, `up_to`, `lines`, `frame` |
| TCP | `type:"tcp"` | `{"host":"...","port":1234}`; `exact`, `until`, `up_to`, `lines`, `frame` |
| USB | `interface`, `in_endpoint`, `out_endpoint`, `transfer` (`bulk`/`interrupt`) | Integer vendor/product IDs, optional serial; `exact`, `up_to`, schema-6 `frame` |

USB endpoints must match descriptor types/directions; exactly one device must match the binding. No implicit configuration change/kernel-driver detach: explicit binding grants are required. USB-CDC uses UART and an OS serial binding. Only USB supports `control`.

Results use `interface-result/1`: `ok`, operation, outputs, units, `identity_verified` and ordered transcript (operation/step, TX/RX hex, USB setup). Failure adds `error:{code,message}` and empty outputs/units. `attempted` does not prove bytes reached the device; `completed` means response shape passed, not expectations/decoding. Replay cannot imply hardware verification. Model errors are `validation_error`, post-open expectation/decoder errors `execution_error`; transport errors retain their own codes.

`validate_model` returns `ok`, `defects:[{where,what}]`, and `checks_run` naming the model's accepted schema tag. Unknown fields are rejected, not ignored.

## Operations and bounds

Each operation contains `effect`, `parameters`, `steps`, `outputs` and has provenance. Effects are `read`, `write` or `actuate`; non-read effects require grants. Identity names a read operation with no parameters and a nonempty response expectation. Every output references a valid unique capture; captures cannot be overwritten.

| Limit | Value |
|---|---|
| Names | ASCII letter, then letters/digits/underscore; at most 64 characters |
| Model | At most 64 operations; JSON ≤1 MiB; no NaN/Infinity |
| Operation | At most 128 steps, 32 parameters; timeouts + delays ≤120000 ms |
| Timeout / delay | Integer 1–60000 ms / 0–1000 ms |
| Encoded TX | ≤65536 bytes; USB control ≤65535 |
| `exact` receive | `length` 0–65536; zero requests no response |
| `until` / `up_to` | `max_bytes` 1–65536 |
| `lines` | `max_bytes` 2–65536 |

Parameters are bounded finite `integer`/`number`, or string enums without control characters. Arguments must match exactly; booleans are not numbers. No free command passthrough. TX parts are `{"hex":"..."}`, UTF-8 `{"text":"..."}`, or `{"arg":"name","format":"..."}`. Formats: `ascii`, `u8`, `u16le`, `u16be`, `u32le`, `u32be`, `i16le`, `i16be`, `i32le`, `i32be`, `f32le`, `f32be`. Bounds must fit the encoding; literal text may contain protocol delimiters.

Steps are `exchange`, `usb_control`, or `{"op":"delay","ms":N}`. Control adds `setup:{request_type,request,value,index}` to TX/RX/capture/expect. First two fields are unsigned 8-bit, last two unsigned 16-bit; each is an integer or `{"arg":"integer_parameter"}`. Direction, data and receive lengths must agree. Control supports neither `until` nor `lines`.

```json
{"mode":"exact","length":2}
{"mode":"until","delimiter_hex":"0a","max_bytes":4096}
{"mode":"up_to","max_bytes":64}
```

Streams handle partial reads/writes and retain bytes beyond delimiters. Short exact replies, excess data, expired deadlines and disconnects fail. `up_to` may return the first available fragment; it is not a multi-line completion rule.

## Expectations and decoding

All supplied positive forms must hold. Byte forms: `equals_hex` (whole reply), `prefix_hex`, `contains_text` (substring anywhere, including errors). Line forms, only with `lines`/`until`: `contains_line`, `line_prefix_present`, `first_line_prefix`, `final_line_equals`, `final_line_token` (first whitespace token of last line). Literals are nonempty, within receive bounds; line literals are printable ASCII and token forms a single token. JSON keys cannot repeat; choose different forms to pin different identity fields.

`reject_line_prefix` names 1–16 distinct nonempty printable ASCII prefixes. Any matching error line fails even when positives hold. Every `lines` step needs this rejection list and at least one positive guard. Schema 3 retains its exactly-one-byte-guard rule under its own tag.

| Decoder | Fields / result |
|---|---|
| `hex` | Capture bytes as hex |
| `utf8` | Text; optional `strip` |
| `ascii_number` | Number following literal `prefix` |
| `integer` | `offset`, `size` 1/2/4/8, `byteorder` little/big, `signed`; optional finite `scale`, `offset_value` |
| `float32` | `offset`, `byteorder`; optional finite `scale`, `offset_value` |

Decoders use `from` for the capture and optional `unit`. Validate source and bounds; numeric outputs must be finite. No model-provided regex, eval, imports, code or filesystem references.

Text-field decoders read UTF-8 with invalid bytes replaced by U+FFFD:

| Decoder | Rule |
|---|---|
| `kv_number` | First whitespace token starting with `key`; parse the following run of `+-0123456789.eE` containing a digit, then scale (default 1) and offset (default 0). `T:33.58/0.00` gives 33.58. Missing key/nonfinite value fails. |
| `kv_text` | Same token search; nonempty remainder after `key` |
| `line_text` | First line starting with `line_prefix`; return remainder, stripped unless `strip:false` |

Keys are nonempty printable ASCII without whitespace; line prefixes are nonempty printable ASCII.

## Complete line replies

UART/TCP only, schema 4+. Read through the first completion line, retaining later bytes for the next exchange:

```json
{"mode":"lines","max_bytes":1024,"end":{"line_equals":"ok"}}
{"mode":"lines","max_bytes":256,"end":{"line_token":"ok"},"newline":"crlf","max_lines":8}
```

`end` has exactly one nonempty printable ASCII form: `line_equals`, `line_token` (single first word), or `line_prefix`. `newline` is `auto` (default), `lf` or `crlf`; `max_lines` is 1–4096, default 4096. Split on LF and remove one trailing CR. Auto accepts LF/CRLF per line; mismatched explicit policy fails `framing`.

The complete reply, including final newline, must fit byte/line bounds. Missing completion at a bound fails `limit`; expired deadline `timeout`; early TCP close `disconnect`. The engine repeats shape checks for replay/injected transports: final LF, bounds, newline policy, and last line being the first completion match.

Use completion-aware `lines`, never `up_to` or newline-only `until`, for multi-line replies: either can leave a tail that contaminates the next exchange. Pin acknowledgments with `final_line_equals`/`final_line_token` plus error prefixes; substring `ok` also matches `not ok` and `token`. Pin identity family/version/type separately, including token boundaries (trailing space or whole-line `contains_line`). Status must pin its own line shape.

## Binary frames and integrity

Schema 5+ stream frames begin with known sync bytes and contain a byte-count length. Schema 6 also supports USB frames.

TX check part:

```json
{"check":"crc16_modbus","over_parts":[1,3],"byteorder":"little"}
```

Compute over encoded parts `[first,end)`, all before the check. Arguments count when inside that range. Compute left-to-right; later checks may cover earlier checks.

Algorithms: `crc8`, `crc8_maxim`, `crc16_ccitt_false`, `crc16_xmodem`, `crc16_kermit`, `crc16_modbus`, `crc16_arc`, `crc32`, `crc32_mpeg2`, `sum8`, `sum16`, `xor8`, `twos_complement_sum8`. Catalogue CRCs are pinned by their `123456789` check values. Explicit CRC: `{width,poly,init,refin,refout,xorout}`, width 8/16/32, integers within width and boolean reflection flags. Derive polynomial, initialization, shift direction, final complement and covered bytes from evidence, not protocol familiarity.

```json
{"mode":"frame","sync_hex":"3cc3",
 "length":{"offset":2,"size":2,"byteorder":"little","add":7},
 "max_bytes":64,"check":{"algorithm":"crc16_modbus","from":2,"byteorder":"little"}}
```

| Field | Rule |
|---|---|
| `sync_hex` | 1–8 bytes at reply start; no searching past noise |
| `length` | Field after sync; size 1/2/4, little/big endian; total = field + `add` (may be negative) |
| `max_bytes` | Total bound; excessive length fails `limit`, shorter than fixed parts fails `framing` |
| Optional `check` | Algorithm value immediately before end bytes; covers `from` through byte before check; mismatch fails `integrity` |
| Optional `end_hex` | Required final 1–8 bytes |

Read only through declared frame end once known; buffer later bytes. The engine repeats sync/length/end/integrity checks for every transport, including replay. `expect.equals_hex` or `prefix_hex` must start with sync and pin beyond it. Pin the response code, not merely sync/length. `reject_prefix_hex` lists 1–16 distinct nonempty error prefixes, including applicable length bytes; any match fails. Line forms cannot guard frames. Decoder offsets start at the first sync byte.

## Schema 6 payloads and decoders

Bytes parameters use `{"type":"bytes","min_length":m,"max_length":n}`: `0 ≤ m ≤ n`, `1 ≤ n ≤ 4096`. Arguments are even-length hex strings with no whitespace/`0x`, holding m–n bytes. Invalid arguments fail before transmission. Keep command bytes literal; this is a bounded payload, not whole-command passthrough.

TX `format:"raw"` inserts bytes; its bound counts `max_length`. `length_u8` inserts byte count and requires max length ≤255; `length_u16le`/`length_u16be` insert a two-byte count. Raw and length parts may share an argument and participate in integrity checks. Bytes cannot use ascii/numeric formats or USB setup fields.

| Decoder | Rule |
|---|---|
| `cstring` | `from`, `offset`, optional `max_length`, `encoding`, `unit`. Require NUL within capture and within max_length+1 bytes when bounded; omit NUL and ignore later padding. Offset must be inside receive bound. |
| `lpstring` | `from`, `length_offset`, `length_size` 1/2, `byteorder`, `offset`, `includes_terminator`, optional `encoding`/`unit`. Unsigned count must fit captured data. If terminator included, require/remove final NUL; zero count gives empty string. Length field must fit receive bound; text offset may equal it. |
| Integer bit fields | Optional `shift` 0..8×size−1, `mask` 1..2^64−1. Decode, shift right, AND mask, then scale/offset. Signed right shift preserves sign; narrower signed fields cannot be sign-extended. |

String encoding is strict `ascii` (default) or `utf-8`; invalid bytes fail `execution_error`. No UTF-16 decoder: return USB string descriptors as hex or pin bytes.

## USB channels

Take settings from descriptors. `interface` is 0–255 (default 0), alternate setting 0 only. Endpoint addresses are JSON integers (IN bit 7 set; e.g. 0x82 → 130). `transfer` defaults to `bulk`, or `interrupt`; both endpoints must match descriptors and have nonzero packet size. Schema 6 requires OUT for any possibly nonempty exchange TX and IN for any exchange receive. Control-only models need neither. Retagging schema 5 may newly reject these missing endpoints at validation.

Binding: integer `vendor_id`/`product_id` 0–65535, optional `serial_number`, boolean `allow_set_configuration` and `allow_detach_kernel_driver` (both false). Exactly one device must match. Configuration permission selects the first configuration only if none is active; detach permission applies to the selected interface, with reattach on close. Channel/descriptor defects are `channel`; malformed/ambiguous/missing-device bindings or ungranted permissions are `binding`.

Exchange sends all TX in one OUT transfer (empty TX skips it); short write fails `length`. `exact`/`up_to` use one IN transfer. `until`/`lines` are invalid. Control uses the default endpoint. An unframed exchange with buffered frame bytes fails before TX.

For IN, `up_to.max_bytes` must be a positive endpoint-packet multiple or fails `invalid` before sending. Default to one packet unless evidence establishes multi-packet replies ending with a short packet/ZLP. A larger buffer can otherwise wait until timeout after one full packet. `exact` allocates rounded-up whole packets (≤65536), but returned bytes must equal the requested length. Use `up_to` for full-packet padded replies; offline qualification cannot verify actual packet size.

Opening drains up to eight IN packets with 10 ms timeout each, stopping at first timeout. This cannot be disabled; continuously unsolicited streams may lose eight packets per open. Actual discards add `transport_notes:{"drained_bytes":n}`; otherwise the key is absent. Execution close releases the interface and reattaches any detached driver.

Errors: `timeout` for expiry; `io` for stall/overflow/disconnect; `length` for wrong/excess response length; `dependency` for missing PyUSB/libusb.

### USB control

Direction bit 7 set means device read with empty TX; clear means host write with `exact` zero receive and optional TX data. Type is bits 5–6 (standard/class/vendor); recipient bits 0–4 (device/interface/endpoint), with interface/endpoint index in `index`.

Identity can GET_DESCRIPTOR: request_type 128, request 6, value 256, index 0, exact 18. Pin `1201` and descriptor fields through VID/PID at bytes 8–11 (little-endian). Configuration uses value 512, `up_to` wTotalLength; string uses value 768+string index, language in index (1033 for US English), `up_to` 255. String content after its two-byte header is UTF-16LE, unsupported by text decoders.

A `read` operation may never allow request_type bit 7 clear, including parameter ranges. Host-to-device controls need write/actuate effects. Standard requests SET_ADDRESS (5), SET_CONFIGURATION (9), SET_INTERFACE (11) are forbidden under every effect; validator and adapter enforce this because the host owns configuration.

### Framed USB replies

Use schema-6 `frame` with `max_reads` 1–4096, `max_bytes` and timeout. Read whole IN packets until one complete frame; zero-length packets consume a read and do not finish it. Retain surplus for the next framed exchange. Bad sync/length/check/trailer fails without searching for another frame.

```json
{"mode":"frame","sync_hex":"1234",
 "length":{"offset":8,"size":4,"byteorder":"big","add":12,
   "body_mask":16777215,"padding":{"shift":24,"mask":255,"max_bytes":255}},
 "max_bytes":8192,"max_reads":32}
```

Optional `body_mask` selects contiguous low length bits. Optional padding `{shift,mask,max_bytes}` selects nonoverlapping bits. Total is `(field & body_mask) + padding_count + add`. Undeclared bits, excessive padding or length mismatch fail. Padding stays in transcript/hex; other decoders see the header/body prefix before padding/check/end bytes. Required padding contents need expectations/integrity. Every bound/bit layout needs evidence.

Exchange `match:[{tx_offset,rx_offset,size}]` compares actual encoded request bytes with fixed reply-header ranges before decoding. Ranges must fit; `expect` still pins success. A client parsing a sequence is not proof firmware echoes it; evidence and a probe must establish that claim. One exchange receives one frame, not a data-dependent sequence of complete messages.

### USB qualification

Use `interface-replies/2`, one hex reply per exchange/control in order, excluding delays; `{"hex":""}` for no receive. USB rejects line arrays. Run the real engine/USB adapter over a scripted device: complete unframed transfers or bounded frame fragments, including declared match/padding mutations. Actual endpoint packet sizes still require live descriptors.

Fault the first identity transfer and first requested-operation transfer: changed first byte, truncation before required guard/decoder bytes (exact loses last byte), over-bound reply, declared error prefix padded with zeros, changed positive literal and timeout. Inapplicable faults are marked so. Every applicable fault must fail at that step before later TX.

The padding diagnostic zero-pads `up_to` replies and compares outputs; differing outputs expose length-sensitive decoding but do not affect verdict. Qualification checks predicted bytes/guards, never device truth.

## Worked example

Invented protocol; these values describe no real product. Replace every illustrative provenance source with supplied evidence.

```json
{
  "schema":"interface-model/6",
  "device":{"name":"FROSTLINE TL-9 thermal logger (name from the firmware's name reply)"},
  "channel":{"type":"usb","interface":0,"in_endpoint":130,"out_endpoint":1,"transfer":"bulk"},
  "identity":{"operation":"identify"},
  "operations":{
    "identify":{
      "effect":"read", "parameters":{},
      "steps":[
        {"op":"usb_control","setup":{"request_type":128,"request":6,"value":256,"index":0},
         "tx":[],"rx":{"mode":"exact","length":18},"timeout_ms":1000,"capture":"device",
         "expect":{"prefix_hex":"120100020000004034127856"}},
        {"op":"exchange","tx":[{"hex":"4e"}],"rx":{"mode":"up_to","max_bytes":64},"timeout_ms":1000,"capture":"name",
         "expect":{"prefix_hex":"4e00","contains_text":"FROSTLINE TL-9","reject_prefix_hex":["4ee2"]}}],
      "outputs":{"model_name":{"from":"name","kind":"cstring","offset":2,"max_length":32},
                 "release_major":{"from":"device","kind":"integer","offset":13,"size":1,"byteorder":"little","signed":false}}
    },
    "read_serial":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange","tx":[{"hex":"53"}],"rx":{"mode":"up_to","max_bytes":64},"timeout_ms":1000,"capture":"sn",
        "expect":{"prefix_hex":"5300","reject_prefix_hex":["53e2"]}}],
      "outputs":{"serial":{"from":"sn","kind":"lpstring","length_offset":2,"length_size":1,"byteorder":"little",
                           "offset":3,"includes_terminator":false}}
    },
    "read_status":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange","tx":[{"hex":"54"}],"rx":{"mode":"up_to","max_bytes":64},"timeout_ms":1000,"capture":"st",
        "expect":{"prefix_hex":"5400","reject_prefix_hex":["54e2"]}}],
      "outputs":{"mode":{"from":"st","kind":"integer","offset":2,"size":2,"byteorder":"little","signed":false,"mask":7},
                 "alarm":{"from":"st","kind":"integer","offset":2,"size":2,"byteorder":"little","signed":false,"shift":7,"mask":1},
                 "battery":{"from":"st","kind":"integer","offset":2,"size":2,"byteorder":"little","signed":false,"shift":8,"mask":255,"unit":"percent"},
                 "temperature":{"from":"st","kind":"integer","offset":4,"size":2,"byteorder":"little","signed":true,"scale":0.01,"unit":"degC"}}
    },
    "write_label":{
      "effect":"write",
      "parameters":{"label":{"type":"bytes","min_length":1,"max_length":32}},
      "steps":[{"op":"exchange",
        "tx":[{"hex":"4c"},{"arg":"label","format":"length_u8"},{"arg":"label","format":"raw"},
              {"check":"crc8","over_parts":[1,3],"byteorder":"little"}],
        "rx":{"mode":"up_to","max_bytes":64},"timeout_ms":1000,"capture":"ack",
        "expect":{"prefix_hex":"4c00","reject_prefix_hex":["4ce1","4ce2"]}}],
      "outputs":{}
    },
    "clear_alarm":{
      "effect":"write", "parameters":{},
      "steps":[{"op":"usb_control","setup":{"request_type":64,"request":17,"value":0,"index":0},
        "tx":[],"rx":{"mode":"exact","length":0},"timeout_ms":1000}],
      "outputs":{}
    }
  },
  "provenance":[
    {"item":"operations.identify","source":"descriptor.json: device descriptor bytes 0-11; PROTOCOL.md section 3.1, command 4E"},
    {"item":"operations.read_serial","source":"PROTOCOL.md section 3.2, command 53, counted string without terminator"},
    {"item":"operations.read_status","source":"PROTOCOL.md section 3.4, command 54, status word bit table"},
    {"item":"operations.write_label","source":"PROTOCOL.md section 3.6, command 4C, CRC-8 over count and label"},
    {"item":"operations.clear_alarm","source":"PROTOCOL.md section 4, vendor request 0x11"}
  ]
}
```

The logger has interface 0, OUT 0x01, IN 0x82, 64-byte bulk packets. Success status is 00; E1/E2 are argument/unknown-command errors. Status `540083572efb` yields mode 3, alarm 1, battery 87%, temperature −12.34 degC. Label ALPHA-3 sends `4c07414c5048412d339c`, including count and CRC-8. Identity pins descriptor VID/PID then the firmware name.

## Qualification

Run configured Python 3.11+ (full interpreter path on Windows): `python3 validate.py .`, then `python3 qualify.py . replies.json`. Fix defects; submit only when every operation with replies qualifies. The result is `qualification/result.json`, exit 0 on success. List uncovered operations in notes.

`interface-replies/1` supplies one array of reply lines per exchange, including completion, excluding delays; include `parameters` when required. Derive replies from supplied evidence, not from model guards.

The real engine/stream adapter receives complete replies and 1, 2, 7, 16, 32, 64-byte fragments under LF/CRLF. Faults cover unrelated replies, changed identity anchors, truncation, missing completion, declared errors, malformed lines, timeout and disconnect. Trailing bytes are a diagnostic. Qualification tests predicted replies and guards, not whether the device behaves that way; probe/ground establish behavior later.

Frame models use `interface-replies/2`; each frame step needs `{"hex":"whole frame including check"}`. Line replies remain allowed on suitable non-USB steps. Compute checks from independent firmware analysis; an invalid predicted check is refused. Binary qualification uses complete/fragmented delivery under `BINARY`, then unrelated payloads with recomputed checks, truncation, bad checks/sync/length, declared errors, changed guarded prefixes with recomputed checks, timeout/disconnect and trailing-byte diagnostics.

Predicted replies for the example:

```json
{"schema":"interface-replies/2",
 "operations":{
   "identify":{"steps":[{"hex":"120100020000004034127856000101020301"},{"hex":"4e0046524f53544c494e4520544c2d3900"}]},
   "read_serial":{"steps":[{"hex":"530009544c392d3030343137"}]},
   "read_status":{"steps":[{"hex":"540083572efb"}]},
   "write_label":{"steps":[{"hex":"4c00"}],"parameters":{"label":"414c5048412d33"}},
   "clear_alarm":{"steps":[{"hex":""}]}
 }}
```

## Unsupported

CAN, GPIO, 1-Wire, USB isochronous, automatic reconnect/retry, custom conversion code, SPI full-duplex and custom dummy fill are unsupported. Also unsupported: byte stuffing/escaping (SLIP, COBS, HDLC), non-byte-count lengths and searching noise for sync. Schema 6 also excludes nonzero USB alternate settings, data-dependent multi-frame aggregation, host-appended ZLP, UTF-16 text and sign extension of narrow bit fields. Record unsupported behavior; do not approximate it.
