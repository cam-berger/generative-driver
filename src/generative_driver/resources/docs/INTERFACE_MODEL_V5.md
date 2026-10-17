# Executable interface model, schema 5

One model describes one channel/device using supplied evidence. Record unknown semantics in `NOTES.md`; the host supplies physical bindings. Validation checks structure, not firmware fidelity or physical safety.

Schema 5 adds request integrity checks and binary frames to schemas 3–4. Use `interface-model/5` for these features. Earlier models remain valid under their own tags. This guide includes the shared contract needed to use this version.

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
| USB | `interface`, `in_endpoint`, `out_endpoint`, `transfer` (`bulk`/`interrupt`) | Integer vendor/product IDs, optional serial; `exact`, `up_to` |

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

## Worked example

Invented protocol; these values describe no real product. Replace every illustrative provenance source with supplied evidence.

```json
{
  "schema":"interface-model/5",
  "device":{"name":"KELP-7 dosing pump (name from identity reply)"},
  "channel":{"type":"uart","baudrate":57600},
  "identity":{"operation":"identify"},
  "operations":{
    "identify":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange",
        "tx":[{"hex":"c33c"},{"hex":"000001"},{"check":"crc16_modbus","over_parts":[1,2],"byteorder":"little"}],
        "rx":{"mode":"frame","sync_hex":"3cc3","length":{"offset":2,"size":2,"byteorder":"little","add":7},
              "max_bytes":64,"check":{"algorithm":"crc16_modbus","from":2,"byteorder":"little"}},
        "timeout_ms":1000,"capture":"id",
        "expect":{"prefix_hex":"3cc30600814b454c50","reject_prefix_hex":["3cc30200ee"]}}],
      "outputs":{"version_major":{"from":"id","kind":"integer","offset":9,"size":1,"byteorder":"little","signed":false}}
    },
    "set_rate":{
      "effect":"actuate",
      "parameters":{"rate":{"type":"integer","minimum":0,"maximum":500}},
      "steps":[{"op":"exchange",
        "tx":[{"hex":"c33c"},{"hex":"020010"},{"arg":"rate","format":"u16le"},
              {"check":"crc16_modbus","over_parts":[1,3],"byteorder":"little"}],
        "rx":{"mode":"frame","sync_hex":"3cc3","length":{"offset":2,"size":2,"byteorder":"little","add":7},
              "max_bytes":64,"check":{"algorithm":"crc16_modbus","from":2,"byteorder":"little"}},
        "timeout_ms":1000,"capture":"ack",
        "expect":{"prefix_hex":"3cc3020090","reject_prefix_hex":["3cc30200ee"]}}],
      "outputs":{"applied":{"from":"ack","kind":"integer","offset":5,"size":2,"byteorder":"little","signed":false}}
    }
  },
  "provenance":[
    {"item":"operations.identify","source":"exports/function_08000a10.c: command 0x01 handler; reply builder at 0x08000b3c"},
    {"item":"operations.set_rate","source":"exports/function_08000c20.c: command 0x10, bound check 500 at 0x08000c3e; check routine 0x08000900"}
  ]
}
```

The pump uses request sync C3 3C, reply sync 3C C3, little-endian payload length, command/response, payload and CRC-16/MODBUS over bytes after sync. Success sets the command high bit; EE denotes an error carrying command/error code.

## Qualification

Run configured Python 3.11+ (full interpreter path on Windows): `python3 validate.py .`, then `python3 qualify.py . replies.json`. Fix defects; submit only when every operation with replies qualifies. The result is `qualification/result.json`, exit 0 on success. List uncovered operations in notes.

`interface-replies/1` supplies one array of reply lines per exchange, including completion, excluding delays; include `parameters` when required. Derive replies from supplied evidence, not from model guards.

The real engine/stream adapter receives complete replies and 1, 2, 7, 16, 32, 64-byte fragments under LF/CRLF. Faults cover unrelated replies, changed identity anchors, truncation, missing completion, declared errors, malformed lines, timeout and disconnect. Trailing bytes are a diagnostic. Qualification tests predicted replies and guards, not whether the device behaves that way; probe/ground establish behavior later.

Frame models use `interface-replies/2`; each frame step needs `{"hex":"whole frame including check"}`. Line replies remain allowed on suitable non-USB steps. Compute checks from independent firmware analysis; an invalid predicted check is refused. Binary qualification uses complete/fragmented delivery under `BINARY`, then unrelated payloads with recomputed checks, truncation, bad checks/sync/length, declared errors, changed guarded prefixes with recomputed checks, timeout/disconnect and trailing-byte diagnostics.

Predicted replies for the example:

```json
{"schema":"interface-replies/2",
 "operations":{
   "identify":{"steps":[{"hex":"3cc30600814b454c500702e75c"}]},
   "set_rate":{"steps":[{"hex":"3cc3020090fa001f4d"}],"parameters":{"rate":250}}
 }}
```

## Unsupported

CAN, GPIO, 1-Wire, USB isochronous, automatic reconnect/retry, custom conversion code, SPI full-duplex and custom dummy fill are unsupported. Also unsupported: byte stuffing/escaping (SLIP, COBS, HDLC), non-byte-count lengths and searching noise for sync. Record unsupported behavior; do not approximate it.
