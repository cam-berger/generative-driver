# Executable interface model, schema 4

One model describes one channel/device. Use only supplied evidence and record unresolved semantics in NOTES.md. The host supplies physical bindings outside the model. A passing validator proves structure, not firmware fidelity or physical safety.

Schema 4 (`interface-model/4`) is a superset of schema 3 (`interface-model/3`). The runtime accepts both tags, and every valid `interface-model/3` model stays valid under its own tag. Schema 4 adds a completion-aware `lines` receive mode for stream channels, expectations that combine several positive forms with explicit rejection of error lines, and three text decoders for key:value and line-oriented replies. A device that talks a line protocol (one request, one or more reply lines, a completion line) must be modelled in schema 4 with `lines` mode; the reasons the schema-3 forms fail on such devices are given at the end of this document.


Transport module `interface_runtime.transports` supplies:

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

`control` is supported only by USB. Unsupported method/mode combinations fail before sending bytes. Channel configuration contains protocol settings; binding contains physical host locators. UART binding is `{"port": "..."}`, TCP is `{"host": "...", "port": 1234}`, FTDI is `{"url": "ftdi://..."}`, and USB is `{"vendor_id": 0x1234, "product_id": 0x5678, "serial_number": "..."}`. USB must resolve one matching device. Do not detach kernel drivers or change USB configuration implicitly; optional explicit binding flags may permit those operations and must be documented.

Channel types/settings: `i2c` with `address_7bit` and `speed_hz`; `spi` with `cs`, `clock_hz`, `mode`, `bit_order` (initial implementation supports `msb_first`); `uart` with `baudrate`, optional `bytesize`, `parity`, `stopbits`; `tcp`; `usb` with optional `interface`, `in_endpoint`, `out_endpoint`, `transfer` (`bulk` or `interrupt`). USB endpoint types and directions must agree with descriptors. USB-CDC uses the UART transport and OS serial binding, rather than claiming all USB devices are serial. `capabilities()` reports the exchange modes per channel type: `uart` and `tcp` support `exact`, `until`, `up_to` and `lines`; `i2c` and `spi` support `exact`; `usb` supports `exact` and `up_to`.

Receive shapes:

```json
{"mode":"exact", "length":2}
{"mode":"until", "delimiter_hex":"0a", "max_bytes":4096}
{"mode":"up_to", "max_bytes":64}
{"mode":"lines", "max_bytes":1024, "end":{"line_equals":"ok"}}
{"mode":"lines", "max_bytes":256, "end":{"line_token":"ok"}, "newline":"crlf", "max_lines":8}
```

All lengths are 0–65536 for exact, 1–65536 for until and up_to, and 2–65536 for lines. Timeouts are integer 1–60000 ms. Stream adapters support exact/until/up_to/lines, retain bytes after a delimiter or completion line, handle partial writes/reads and close on uncertain transaction failure. I2C/SPI support exact; USB supports exact/up_to. Empty receive with exact length zero means no response requested. Short exact responses, oversized data, deadline expiry and disconnects are errors. Every adapter enforces bounds even when called without the model validator.

## Lines receive mode

`{"mode":"lines","max_bytes":N,"end":{...}}` reads whole lines until the first line that satisfies `end`, consumes exactly through that line's terminator, and leaves every later byte buffered in the transport for the next exchange. It exists for uart and tcp exchange steps only, requires the `interface-model/4` tag, and is not available for USB or control steps.

`end` holds exactly one of three forms and the value is nonempty printable ASCII. `line_equals` matches when the line, without its terminator, equals the value. `line_token` matches when the line's first whitespace-delimited word equals the value; the value itself must be a single token. `line_prefix` matches when the line starts with the value. Optional `newline` is `auto` (default), `lf` or `crlf`. Optional `max_lines` is 1–4096 and defaults to 4096.

Lines are split on LF. One trailing CR before the LF is removed before matching, so under `auto` every line may end in either LF or CRLF, individually. Under `lf` a line ending in CR fails with code `framing`; under `crlf` a line without CR fails the same way. The adapter reads in bounded chunks (never past `max_bytes`) and re-examines its buffer after each read, so a reply that arrives one byte at a time and a reply that arrives whole produce the same returned bytes. A reply whose completion line has not appeared by the time the buffer holds `max_bytes` fails with code `limit`, as does a completion line whose terminator lies beyond `max_bytes`, and a reply with more than `max_lines` lines before completion. A deadline that expires without the completion line fails with `timeout`; a TCP peer that closes first fails with `disconnect`. The engine repeats the shape check on whatever the transport returned: the bytes must end in LF, fit `max_bytes` and `max_lines`, obey the declared newline policy, and the last line must be the first line that satisfies `end`. That check also runs against replayed and injected transports.

A transport lives for one `execute` call (identity operation, then the requested operation) and is always closed at the end. Bytes still buffered at close are dropped, so a reply that continued past the modelled completion line is invisible to the result; the qualification harness reports such trailing bytes as a diagnostic.

Engine module `interface_runtime.engine` supplies:

```python
def validate_model(model: dict) -> dict:  # ok, defects[{where,what}], checks_run
    ...
def execute(model: dict, operation: str, parameters=None, *, binding=None,
            allow_effects=(), transport_factory=None, delay_fn=None) -> dict:
    ...
```

`transport_factory` defaults lazily to `open_transport` and has the same two arguments. It provides a real seam for replay and tests, not a second execution engine. Results include schema `interface-result/1`, `ok`, operation, typed outputs, units, identity_verified and an ordered transcript of operation/step, requested TX/returned RX hex and USB setup where relevant. Failures include `error:{code,message}` and empty outputs/units; a transport failure carries the transport's own code (`timeout`, `limit`, `framing`, `disconnect`, `io`, ...), an expectation or decoder failure after the transport opened carries `execution_error`, and a model defect carries `validation_error`. Transcript status `attempted` does not prove bytes reached the device; `completed` means the transport response passed its shape check, while expectations/decoding can still fail. Transcripts cannot imply fresh hardware verification when replayed. Validation must reject unsupported/unknown fields rather than ignore a misspelled limit or effect.

`validate_model` returns `{"ok": bool, "defects": [{"where", "what"}], "checks_run": ["interface-model/4"]}`; `checks_run` names the schema tag the model was checked under, its own tag when that tag is accepted. The supplied `validate.py` prints that list on PASS.

## Schema 4

The worked example is an invented lamp controller on a serial line. It answers `HELLO` with a multi-line identity ending in a bare `ok`, answers `STATUS` with one line that starts with the completion token and carries key:value pairs, and acknowledges `SET BRIGHT n` with a bare `ok`. Error replies start with `ERR:` or `!!`. Nothing about it corresponds to a real product.

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

The identity reply this model predicts is four lines: `FW:GLOWCORE TYPE:LUMEN3 SN:4F2A11`, `VER:2.7.1 BUILD:20260302`, `CAP:DIM 1`, `ok`. The three pinned literals are the firmware family on the first line, the machine type anywhere in the reply and the version at the start of a line; each carries its trailing space so that `VER:2.7.1 ` cannot match `VER:2.7.12`. When a pinned token is the last thing on its line there is no trailing space to include, so pin it with `contains_line` on the whole line instead. `final_line_equals` requires the bare completion line and `reject_line_prefix` fails the step on any error line even when every positive form holds. The decoders take `GLOWCORE`, `2.7.1`, `LUMEN3`, `4F2A11` and `1` from the captured bytes. The status reply is one line, `ok LVL:40 TEMP:31.5 VIN:12.08 ERRS:0`; its first token is the completion token, so `end` is `line_token` and the exchange returns after that single line, and `first_line_prefix` insists the line is a status line and not an unrelated `ok`. The write reply is the bare line `ok`.

Operations contain effect, parameters, steps and outputs. Every operation needs provenance. Identity references a read operation without parameters and with a nonempty response expectation. Names are simple identifiers (ASCII letter followed by letters/digits/underscore, maximum 64 characters). Each model has at most 64 operations; each operation at most 128 steps and 32 parameters. Sum of step timeouts/delays is at most 120000 ms per operation. Models serialize to at most 1 MiB with no NaN/Infinity.

Parameters support bounded integer/number values, or string enums (no free command passthrough). Numeric limits are mandatory and finite. Supplied arguments must match exactly; booleans are not numbers. A TX list contains literal `{hex}`, literal `{text}` (UTF-8), or `{arg,format}` parts. Formats: `ascii`, `u8`, `u16le`, `u16be`, `u32le`, `u32be`, `i16le`, `i16be`, `i32le`, `i32be`, `f32le`, `f32be`. Bounds must be compatible with the selected encoding; total encoded bytes must not exceed 65536. Constant text may include protocol delimiters; enum arguments may not contain control characters.

Steps are `exchange`, `usb_control`, or `delay`. Delay is `{op:"delay",ms:integer}` with 0–1000 ms. A USB control step uses the same tx/rx/capture/expect fields plus `setup:{request_type,request,value,index}`. The first two setup values are unsigned 8-bit and the latter two unsigned 16-bit; values can be integers or `{arg:"integer_parameter"}`. Request direction and payload/receive lengths must agree. `until` and `lines` are invalid for control transfers. Control data is bounded at 65535 bytes because USB wLength is 16-bit; other exchanges allow 65536.

## Expectations

Schema 3 keeps its rule: exactly one form, chosen from `equals_hex`, `prefix_hex` or `contains_text`. Schema 4 allows one expectation to combine several positive forms, and every form present must hold. The byte forms are `equals_hex` (the whole reply), `prefix_hex` and `contains_text` (a byte substring anywhere in the reply, across line boundaries and inside an error line). The line forms are `contains_line` (some line equals the value exactly), `line_prefix_present` (some line starts with it), `first_line_prefix` (the first line starts with it), `final_line_equals` (the last line equals it) and `final_line_token` (the first whitespace-delimited word of the last line equals it; the value must be a single token). Line forms are permitted only on steps whose receive mode is `lines` or `until`, lines being split exactly as the transport splits them. Each literal is nonempty (printable ASCII for the line forms) and no longer than the step's receive bound; a JSON object holds each key once, so a form that must pin two different literals is the wrong form.

`reject_line_prefix` is a list of 1–16 distinct nonempty printable ASCII prefixes; after the positive forms hold, a reply containing any line that starts with one of them fails the step. It exists only in schema 4. A step that receives in `lines` mode must carry an `expect`, and that `expect` must include `reject_line_prefix` naming the device's error lines: the runtime refuses a line-protocol step with no error guard. At least one positive form is required as well. Captures cannot be overwritten.

## Decoders

Output decoders are `hex`, `utf8` (optional strip), `ascii_number` (literal prefix), `integer` (offset, size 1/2/4/8, byteorder little/big, signed boolean, optional finite scale/offset_value), and `float32` (offset, byteorder, optional scale/offset_value). Schema 4 adds three decoders over captured text, all reading the capture as UTF-8 with invalid bytes replaced by U+FFFD, and any decoder may carry `unit`.

`kv_number` is `{"from","kind":"kv_number","key","unit"?,"scale"?,"offset_value"?}`. The whole capture, every line of it, is split on whitespace; the first token that starts with `key` is taken and the text after `key` must begin with a numeric run of the characters `+-0123456789.eE` containing at least one digit. That run is parsed as a float, multiplied by `scale` (default 1) and offset by `offset_value` (default 0), and must be finite. `TEMP:31.5` yields 31.5; `T:33.58/0.00` yields 33.58 because the run stops at `/`. A key that is absent fails the decode with `key not present in reply`. `key` is nonempty printable ASCII without whitespace.

`kv_text` is `{"from","kind":"kv_text","key","unit"?}`: the same token search, returning the remainder of the token after `key` as a string, which must be nonempty. `line_text` is `{"from","kind":"line_text","line_prefix","strip"?,"unit"?}`: the first line that starts with `line_prefix`, returning the text after the prefix, stripped of surrounding whitespace unless `strip` is false. `line_prefix` is nonempty printable ASCII. Numeric outputs must be finite. Parsing never uses model-provided regex, eval, code imports or filesystem references. Decoder source capture and bounds must be valid.

## Qualification before submission

A schema-4 model is submitted together with `replies.json`, the candidate's own prediction of what the device answers, in schema `interface-replies/1`:

```json
{"schema":"interface-replies/1",
 "operations":{
   "identify":{"steps":[["FW:GLOWCORE TYPE:LUMEN3 SN:4F2A11","VER:2.7.1 BUILD:20260302","CAP:DIM 1","ok"]]},
   "read_status":{"steps":[["ok LVL:40 TEMP:31.5 VIN:12.08 ERRS:0"]]},
   "set_brightness":{"steps":[["ok"]],"parameters":{"level":40}}
 }}
```

Each operation lists one array of reply lines per exchange step, in step order, delay steps excluded, the completion line included, predicted from the analysis rather than copied from any observation the candidate does not have. `parameters` supplies the arguments for an operation that takes them. Running `python3 qualify.py . replies.json` from the model directory drives the real engine and stream adapter over those replies, first complete and then in 1, 2, 7, 16, 32 and 64-byte fragments under LF and CRLF framing, then under synthetic faults: an unrelated reply, each pinned identity literal mutated, truncation before an anchor the model relies on, a missing completion line, an error line using the model's own reject prefix, a malformed line, a timeout, a disconnect, and a trailing-bytes diagnostic. It writes `qualification/result.json` and exits 0 only when every operation with replies qualifies. Do not submit a model until it does.

Qualification proves that the model's guards and framing hold against the replies the candidate assumed, under every delivery pattern the adapter can see. It proves nothing about the device: a wrong prediction qualifies as readily as a right one. Device behaviour is established later, by probe and ground, against the real channel.

## Why up_to and substring ok fail on line protocols

`up_to` returns as soon as any bytes are available, at most `max_bytes` of them. On a serial adapter that delivers a multi-line reply in fragments, the first fragment is the whole response the engine ever sees: an identity check against it fails, or, with a fragment large enough to contain the pinned prefix, passes while most of the reply is still unread. Those unread bytes are then returned to the next exchange as if they were its reply, so the following status request is answered by the tail of the identity text. An offline trial saw exactly this: only its complete-delivery cases passed. `until` with a newline delimiter has the mirror defect for multi-line replies, stopping after the first line and leaving the rest buffered. `lines` with an `end` on the completion line is the only receive shape whose boundary is the device's own end-of-reply marker.

`contains_text` with the value `ok` is satisfied by `not ok`, by `Error: request rejected; not ok`, by `token` and by any reply that happens to carry those two bytes. An acknowledgement is a bare completion line, so it is pinned with `final_line_equals` (or `final_line_token` when the completion line carries trailing fields) together with `reject_line_prefix` naming the device's error lines. Every write in the trial accepted a synthetic error acknowledgement for want of this.


CAN, GPIO, 1-Wire, USB isochronous, automatic reconnect/retry, custom conversion code, SPI full-duplex and custom dummy fill are unsupported.
