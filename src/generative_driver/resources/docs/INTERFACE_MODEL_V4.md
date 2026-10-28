# Executable interface model, schema 4

One model describes one channel/device using supplied evidence. Record unknown semantics in `NOTES.md`; the host supplies physical bindings. Validation checks structure, not firmware fidelity or physical safety.

Schema 4 adds complete line replies, combined guards and text fields to schema 3. Use `interface-model/4` for these features. Earlier models remain valid under their own tags. This guide includes the shared contract needed to use this version.

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
| UART | `baudrate`; optional `bytesize`, `parity`, `stopbits` | `{"port":"..."}`; `exact`, `until`, `up_to`, `lines` |
| TCP | `type:"tcp"` | `{"host":"...","port":1234}`; `exact`, `until`, `up_to`, `lines` |
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

Choose `max_lines` and `max_bytes` for complete success **and error** replies, including the completion line. A rejection prefix that cannot share the completion line needs room for at least two lines; candidate validation and the managed gateway refuse an impossible one-line contract before I/O. If the rejection line itself satisfies the completion rule, a one-line bound remains valid.

Use completion-aware `lines`, never `up_to` or newline-only `until`, for multi-line replies: either can leave a tail that contaminates the next exchange. Pin acknowledgments with `final_line_equals`/`final_line_token` plus error prefixes; substring `ok` also matches `not ok` and `token`. Pin identity family/version/type separately, including token boundaries (trailing space or whole-line `contains_line`). Status must pin its own line shape.

## Worked example

Invented protocol; these values describe no real product. Replace every illustrative provenance source with supplied evidence.

```json
{
  "schema":"interface-model/4",
  "device":{"name":"GLOWCORE lamp controller (name from identity reply)"},
  "channel":{"type":"uart","baudrate":115200},
  "identity":{"operation":"identify"},
  "operations":{
    "identify":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange","tx":[{"text":"HELLO\n"}],
        "rx":{"mode":"lines","max_bytes":1024,"end":{"line_equals":"ok"}},
        "timeout_ms":2000,"capture":"id",
        "expect":{"first_line_prefix":"FW:GLOWCORE ",
                  "contains_text":"TYPE:LUMEN3 ",
                  "line_prefix_present":"VER:2.7.1 ",
                  "final_line_equals":"ok",
                  "reject_line_prefix":["ERR:","FAULT:"]}}],
      "outputs":{"firmware":{"from":"id","kind":"kv_text","key":"FW:"},
                 "version":{"from":"id","kind":"kv_text","key":"VER:"},
                 "machine":{"from":"id","kind":"kv_text","key":"TYPE:"},
                 "serial":{"from":"id","kind":"kv_text","key":"SN:"},
                 "dimmable":{"from":"id","kind":"line_text","line_prefix":"CAP:DIM"}}
    },
    "read_status":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange","tx":[{"text":"STATUS\n"}],
        "rx":{"mode":"lines","max_bytes":256,"end":{"line_token":"ok"},"max_lines":4},
        "timeout_ms":2000,"capture":"status",
        "expect":{"first_line_prefix":"ok LVL:",
                  "reject_line_prefix":["ERR:","FAULT:"]}}],
      "outputs":{"level":{"from":"status","kind":"kv_number","key":"LVL:","unit":"percent"},
                 "board_temperature":{"from":"status","kind":"kv_number","key":"TEMP:","unit":"degC"},
                 "supply_voltage":{"from":"status","kind":"kv_number","key":"VIN:","unit":"V"},
                 "error_count":{"from":"status","kind":"kv_number","key":"ERRS:"}}
    },
    "set_brightness":{
      "effect":"write",
      "parameters":{"level":{"type":"integer","minimum":0,"maximum":100}},
      "steps":[{"op":"exchange","tx":[{"text":"SET BRIGHT "},{"arg":"level","format":"ascii"},{"hex":"0a"}],
        "rx":{"mode":"lines","max_bytes":128,"end":{"line_equals":"ok"}},
        "timeout_ms":2000,"capture":"ack",
        "expect":{"final_line_equals":"ok","reject_line_prefix":["ERR:","FAULT:"]}}],
      "outputs":{}
    }
  },
  "provenance":[
    {"item":"operations.identify","source":"exports/function_0800a1c0.c: HELLO handler; identity literals at file offsets 0x1a2b0, 0x1a340"},
    {"item":"operations.read_status","source":"exports/function_0800b3f4.c: STATUS formatter, keys LVL TEMP VIN ERRS"},
    {"item":"operations.set_brightness","source":"exports/function_0800b6a8.c: SET BRIGHT parser, clamp 0..100 at 0x0800b6d2"}
  ]
}
```

The lamp identity pins family, type and version. Status uses a completion token with fields; brightness writes require a bare completion line and error guard. The current qualifier flags the brightness example’s unrelated/malformed reply cases: this illustrates the schema but is not a fully qualified submission.

## Qualification

Run configured Python 3.11+ (full interpreter path on Windows): `python3 validate.py .`, then `python3 qualify.py . replies.json`. Fix defects; submit only when every operation with replies qualifies. The result is `qualification/result.json`, exit 0 on success. List uncovered operations in notes.

`interface-replies/1` supplies one array of reply lines per exchange, including completion, excluding delays; include `parameters` when required. Derive replies from supplied evidence, not from model guards.

The real engine/stream adapter receives complete replies and 1, 2, 7, 16, 32, 64-byte fragments under LF/CRLF. Faults cover unrelated replies, changed identity anchors, truncation, missing completion, declared errors, malformed lines, timeout and disconnect. For completion-aware line error cases, qualification requires a completed reply with the declared rejection recognized; a framing or receive-limit failure does not count as handling the device rejection. Trailing bytes are a diagnostic. Qualification tests predicted replies and guards, not whether the device behaves that way; probe/ground establish behavior later.

Predicted replies for the example:

```json
{"schema":"interface-replies/1",
 "operations":{
   "identify":{"steps":[["FW:GLOWCORE TYPE:LUMEN3 SN:4F2A11","VER:2.7.1 BUILD:20260302","CAP:DIM 1","ok"]]},
   "read_status":{"steps":[["ok LVL:40 TEMP:31.5 VIN:12.08 ERRS:0"]]},
   "set_brightness":{"steps":[["ok"]],"parameters":{"level":40}}
 }}
```

## Unsupported

CAN, GPIO, 1-Wire, USB isochronous, automatic reconnect/retry, custom conversion code, SPI full-duplex and custom dummy fill are unsupported. Record unsupported behavior; do not approximate it.
