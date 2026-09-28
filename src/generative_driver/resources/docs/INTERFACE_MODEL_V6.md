# Executable interface model, schema 6

One model describes one channel/device. Use only supplied evidence and record unresolved semantics in NOTES.md. The host supplies physical bindings outside the model. A passing validator proves structure, not firmware fidelity or physical safety.

Schema 6 (`interface-model/6`) is a superset of schema 5 (`interface-model/5`), which is a superset of schema 4 (`interface-model/4`), itself a superset of schema 3. The runtime accepts all four tags, and every valid model stays valid under its own tag. Schema 5 added what a binary framed protocol needs and schema 4 cannot say: a transmit part that computes an integrity check over earlier parts of the same request, so a request whose check depends on its own argument can be sent; a `frame` receive mode for stream channels that reads a length-prefixed frame, verifies its sync bytes, length, end bytes and check, and leaves later bytes buffered; and `reject_prefix_hex`, which names the device's error frames. A device that answers with binary frames carrying a length field on a stream channel must be modelled in schema 5 or 6 with `frame` mode. Everything below about schema 4 applies unchanged under schemas 5 and 6.

Schema 6 adds what a USB device with a binary command protocol needs and schema 5 cannot say: a bounded byte-string parameter, with transmit parts that insert its bytes verbatim or its byte count; `cstring` and `lpstring` decoders, which return text ended by a NUL byte or counted by a length field; `mask` and `shift` on the `integer` decoder, for bit fields; and, on a `usb` channel, a validator that requires the endpoint every exchange step uses. It also admits framed USB replies with a finite number of packet reads, composite body-length and padding fields, buffered surplus bytes, and matching reply header bytes against the request. The rules of the USB adapter described under "USB channels" (receive bounds of whole packets, the drain on open, timeouts reported as `timeout`) belong to the adapter and apply under every tag. Retagging a schema-5 model as schema 6 keeps it valid and behaving identically, with one exception: a usb model whose exchange steps lack an endpoint they use, which schema 5 accepted and the adapter then refused at run time, is refused by schema 6 at validation. A device that answers commands over USB bulk or interrupt endpoints is modelled in schema 6.


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

`control` is supported only by USB. Unsupported method/mode combinations fail before sending bytes. Channel configuration contains protocol settings; binding contains physical host locators, chosen by the host and never by the model. UART binding is `{"port": "..."}`, TCP is `{"host": "...", "port": 1234}`, FTDI is `{"url": "ftdi://..."}`, and USB is `{"vendor_id": 4660, "product_id": 22136, "serial_number": "..."}` with the identifiers as JSON integers. USB must resolve one matching device. Do not detach kernel drivers or change USB configuration implicitly; the explicit binding flags `allow_set_configuration` and `allow_detach_kernel_driver`, both false by default, permit those operations. "USB channels" below gives every USB channel and binding key.

Channel types/settings: `i2c` with `address_7bit` and `speed_hz`; `spi` with `cs`, `clock_hz`, `mode`, `bit_order` (initial implementation supports `msb_first`); `uart` with `baudrate`, optional `bytesize`, `parity`, `stopbits`; `tcp`; `usb` with optional `interface`, `in_endpoint`, `out_endpoint`, `transfer` (`bulk` or `interrupt`). USB endpoint types and directions must agree with descriptors. USB-CDC uses the UART transport and OS serial binding, rather than claiming all USB devices are serial. `capabilities()` reports the exchange modes per channel type: `uart` and `tcp` support `exact`, `until`, `up_to`, `lines` and `frame`; `i2c` and `spi` support `exact`; `usb` supports `exact`, `up_to` and schema-6 `frame`.

Receive shapes:

```json
{"mode":"exact", "length":2}
{"mode":"until", "delimiter_hex":"0a", "max_bytes":4096}
{"mode":"up_to", "max_bytes":64}
{"mode":"lines", "max_bytes":1024, "end":{"line_equals":"ok"}}
{"mode":"lines", "max_bytes":256, "end":{"line_token":"ok"}, "newline":"crlf", "max_lines":8}
{"mode":"frame", "sync_hex":"3cc3", "length":{"offset":2,"size":2,"byteorder":"little","add":7}, "max_bytes":64,
 "check":{"algorithm":"crc16_modbus","from":2,"byteorder":"little"}}
{"mode":"frame", "sync_hex":"1234", "length":{"offset":8,"size":4,"byteorder":"big","add":12,
 "body_mask":16777215,"padding":{"shift":24,"mask":255,"max_bytes":255}},
 "max_bytes":8192,"max_reads":32}
```

All lengths are 0–65536 for exact, 1–65536 for until and up_to, and 2–65536 for lines. Timeouts are integer 1–60000 ms. Stream adapters support exact/until/up_to/lines, retain bytes after a delimiter or completion line, handle partial writes/reads and close on uncertain transaction failure. I2C/SPI support exact; USB supports exact/up_to/frame. Empty receive with exact length zero means no response requested. Short exact responses, oversized data, deadline expiry and disconnects are errors. Every adapter enforces bounds even when called without the model validator.

## Framed USB replies

Schema-6 USB `frame` receives use the same sync, length, optional check, end bytes, and `max_bytes` as stream frames. They additionally require `max_reads` (1–4096). The adapter reads one whole IN endpoint packet per call until it has the declared complete frame, the deadline expires, or a byte/read bound is reached. Surplus bytes from a USB read remain buffered for the next framed exchange on the same connection. A zero-length packet consumes one read from the budget and does not terminate a frame. A malformed length, trailer, check or sync fails the transaction; the adapter never searches past a bad frame for a convenient match.

The optional `length.body_mask` selects contiguous low bits of the length field. The optional `length.padding` declares `{shift,mask,max_bytes}` for a nonoverlapping padding count in that same field. The declared total is `(field & body_mask) + padding_count + add`; undeclared field bits, padding beyond its limit, and disagreement with the received frame length fail. Padding bytes remain in the transcript and in a `hex` output. Other decoders read the header and body prefix ending before padding, checksum and end bytes, so padding cannot satisfy a missing payload field. A protocol that specifies padding contents needs a corresponding expectation or integrity check. The bounds and bit layout must come from protocol evidence.

An exchange step may add `"match":[{"tx_offset":2,"rx_offset":2,"size":6}]` to compare bytes in its fixed response header with the actual encoded request. This can cover command and sequence fields without pinning a constant sequence in the model. Each range must fit the request and fixed response header; a mismatch fails before outputs are decoded. `expect` still pins the reply's protocol-specific success form. The runtime does not infer that firmware echoes a sequence from a client parser alone: the model needs evidence and a live probe for that claim.

Each exchange receives one framed message. A file transfer that requires a data-dependent number of complete messages and a separate completion predicate still needs an additional model form; bounded multi-packet assembly of one message does not claim that capability.

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

`transport_factory` defaults lazily to `open_transport` and has the same two arguments. It provides a real seam for replay and tests, not a second execution engine. Results include schema `interface-result/1`, `ok`, operation, typed outputs, units, identity_verified and an ordered transcript of operation/step, requested TX/returned RX hex and USB setup where relevant. Failures include `error:{code,message}` and empty outputs/units; a transport failure carries the transport's own code (`timeout`, `limit`, `framing`, `disconnect`, `io`, ...), an expectation or decoder failure after the transport opened carries `execution_error`, and a model defect carries `validation_error`. Transcript status `attempted` does not prove bytes reached the device; `completed` means the transport response passed its shape check, while expectations/decoding can still fail. Transcripts cannot imply fresh hardware verification when replayed. A result may also carry `transport_notes: {"drained_bytes": n}` when the adapter discarded stale bytes as it opened (see "USB channels"); the key is absent otherwise. Validation must reject unsupported/unknown fields rather than ignore a misspelled limit or effect.

`validate_model` returns `{"ok": bool, "defects": [{"where", "what"}], "checks_run": ["interface-model/6"]}`; `checks_run` names the schema tag the model was checked under, its own tag when that tag is accepted. The supplied `validate.py` prints that list on PASS.

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

Parameters support bounded integer/number values, string enums (no free command passthrough), and in schema 6 bounded byte strings (see "Bytes parameters and raw and length parts"). Numeric limits are mandatory and finite. Supplied arguments must match exactly; booleans are not numbers. A TX list contains literal `{hex}`, literal `{text}` (UTF-8), or `{arg,format}` parts, and in schema 5 and 6 check parts. Formats: `ascii`, `u8`, `u16le`, `u16be`, `u32le`, `u32be`, `i16le`, `i16be`, `i32le`, `i32be`, `f32le`, `f32be`, and for a bytes parameter only `raw`, `length_u8`, `length_u16le` and `length_u16be`. Bounds must be compatible with the selected encoding; total encoded bytes must not exceed 65536. Constant text may include protocol delimiters; enum arguments may not contain control characters.

Steps are `exchange`, `usb_control`, or `delay`. Delay is `{op:"delay",ms:integer}` with 0–1000 ms. A USB control step uses the same tx/rx/capture/expect fields plus `setup:{request_type,request,value,index}`. The first two setup values are unsigned 8-bit and the latter two unsigned 16-bit; values can be integers or `{arg:"integer_parameter"}`. Request direction and payload/receive lengths must agree. `until` and `lines` are invalid for control transfers. Control data is bounded at 65535 bytes because USB wLength is 16-bit; other exchanges allow 65536.

## Expectations

Schema 3 keeps its rule: exactly one form, chosen from `equals_hex`, `prefix_hex` or `contains_text`. Schema 4 allows one expectation to combine several positive forms, and every form present must hold. The byte forms are `equals_hex` (the whole reply), `prefix_hex` and `contains_text` (a byte substring anywhere in the reply, across line boundaries and inside an error line). The line forms are `contains_line` (some line equals the value exactly), `line_prefix_present` (some line starts with it), `first_line_prefix` (the first line starts with it), `final_line_equals` (the last line equals it) and `final_line_token` (the first whitespace-delimited word of the last line equals it; the value must be a single token). Line forms are permitted only on steps whose receive mode is `lines` or `until`, lines being split exactly as the transport splits them. Each literal is nonempty (printable ASCII for the line forms) and no longer than the step's receive bound; a JSON object holds each key once, so a form that must pin two different literals is the wrong form.

`reject_line_prefix` is a list of 1–16 distinct nonempty printable ASCII prefixes; after the positive forms hold, a reply containing any line that starts with one of them fails the step. It exists only in schema 4. A step that receives in `lines` mode must carry an `expect`, and that `expect` must include `reject_line_prefix` naming the device's error lines: the runtime refuses a line-protocol step with no error guard. At least one positive form is required as well. Captures cannot be overwritten.

## Decoders

Output decoders are `hex`, `utf8` (optional strip), `ascii_number` (literal prefix), `integer` (offset, size 1/2/4/8, byteorder little/big, signed boolean, optional finite scale/offset_value), and `float32` (offset, byteorder, optional scale/offset_value). Schema 4 adds three decoders over captured text, all reading the capture as UTF-8 with invalid bytes replaced by U+FFFD, and any decoder may carry `unit`. Schema 6 adds `cstring` and `lpstring` and the `integer` keys `mask` and `shift` (see "String decoders" and "Bit fields").

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

Each operation lists one array of reply lines per exchange step, in step order, delay steps excluded, the completion line included, predicted from the analysis rather than copied from any observation the candidate does not have. `parameters` supplies the arguments for an operation that takes them. Running `python3 qualify.py . replies.json` from the model directory drives the real engine and stream adapter over those replies, first complete and then in 1, 2, 7, 16, 32 and 64-byte fragments under LF and CRLF framing, then under synthetic faults: an unrelated reply, each pinned identity literal mutated, truncation before an anchor the model relies on, a missing completion line, an error line using the model's own reject prefix, a malformed line, a timeout, a disconnect, and a trailing-bytes diagnostic. It writes `qualification/result.json` and exits 0 only when every operation with replies qualifies. Do not submit a model until it does. A binary frame model is qualified as "Binary replies for qualification" describes, and a usb model as "Qualifying a USB model" describes.

Qualification proves that the model's guards and framing hold against the replies the candidate assumed, under every delivery pattern the adapter can see. It proves nothing about the device: a wrong prediction qualifies as readily as a right one. Device behaviour is established later, by probe and ground, against the real channel.

## Why up_to and substring ok fail on line protocols

`up_to` returns as soon as any bytes are available, at most `max_bytes` of them. On a serial adapter that delivers a multi-line reply in fragments, the first fragment is the whole response the engine ever sees: an identity check against it fails, or, with a fragment large enough to contain the pinned prefix, passes while most of the reply is still unread. Those unread bytes are then returned to the next exchange as if they were its reply, so the following status request is answered by the tail of the identity text. An offline trial saw exactly this: only its complete-delivery cases passed. `until` with a newline delimiter has the mirror defect for multi-line replies, stopping after the first line and leaving the rest buffered. `lines` with an `end` on the completion line is the only receive shape whose boundary is the device's own end-of-reply marker.

`contains_text` with the value `ok` is satisfied by `not ok`, by `Error: request rejected; not ok`, by `token` and by any reply that happens to carry those two bytes. An acknowledgement is a bare completion line, so it is pinned with `final_line_equals` (or `final_line_token` when the completion line carries trailing fields) together with `reject_line_prefix` naming the device's error lines. Every write in the trial accepted a synthetic error acknowledgement for want of this.



## Schema 5: binary frames and integrity checks

The worked example is an invented dosing pump on a serial line. Requests are `C3 3C`, a little-endian
16-bit payload length, a command byte, the payload and a CRC-16/MODBUS over everything after the sync
bytes, low byte first. Replies are `3C C3`, the same length field, a response byte (the command with its
top bit set, or `EE` for an error, whose payload is the command and an error code), the payload and the
same check. Nothing about it corresponds to a real product.

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

### Transmit checks

A tx part `{"check": ALGORITHM, "over_parts": [first, end], "byteorder": "big"|"little"}` inserts the
integrity value computed over the encoded bytes of parts `first` up to but not including `end`, all of
which come before the check part in the same list. The span is by part, so a check covers an argument
exactly when the argument's part is inside the span; above, `set_rate`'s check covers the length and
command part and the rate argument, and not the sync bytes. A list may hold more than one check; each
is computed left to right, so a later check may cover an earlier one.

`ALGORITHM` is either a catalogue name or an explicit CRC. The names are `crc8`, `crc8_maxim`,
`crc16_ccitt_false`, `crc16_xmodem`, `crc16_kermit`, `crc16_modbus`, `crc16_arc`, `crc32` and
`crc32_mpeg2`, each fixed by its catalogue parameters and pinned by its catalogue check value over the
ASCII bytes `123456789`, and the simple forms `sum8`, `sum16`, `xor8` and `twos_complement_sum8`. An
explicit CRC is `{"width":8|16|32, "poly", "init", "refin", "refout", "xorout"}` with integer values
inside the width and booleans for the two reflection flags: the conventional parameter model, so a
routine read out of firmware can be written down as it is, whether or not it has a catalogue name. Name
the algorithm the firmware's check routine computes: its polynomial, its initial value, whether it
shifts left or right, whether it complements at the end, and which bytes it is called over. Do not
assume one from a familiar protocol.

### Frame receive mode

`{"mode":"frame", "sync_hex", "length":{"offset","size","byteorder","add"}, "max_bytes", "check"?, "end_hex"?}`
reads one length-prefixed frame. `sync_hex` is 1–8 bytes the reply must begin with; there is no
hunting, so a reply that begins with anything else fails with code `framing` rather than being skipped
until something frame-like appears. The length field is `size` bytes (1, 2 or 4) at byte `offset`
(which must follow the sync bytes) in `byteorder`; the frame's total length in bytes is the field's value
plus `add`, which may be negative. A total above `max_bytes` fails with `limit`; a total smaller than
the frame's fixed parts fails with `framing`. `check`, when present, is
`{"algorithm", "from", "byteorder"}`: the frame carries, immediately before its end bytes, the
algorithm's value over the bytes from offset `from` up to the check itself, and a mismatch fails with
code `integrity`. `end_hex`, when present, is 1–8 bytes the frame must end with. A serial or TCP adapter reads only through the declared frame end. A USB adapter reads whole endpoint packets and buffers bytes after the frame for the next framed exchange. The engine repeats every one of these checks on whatever the
transport returned, including replayed and injected transports.

A `frame` step must carry an `expect` whose `equals_hex` or `prefix_hex` starts with the sync bytes and
pins at least one byte beyond them. The sync bytes are the same on every reply, success or error; a
guard that stops at them, or at the length field, accepts an error frame or an unrelated reply of the
same length as success. Pin the response code, and more when the firmware makes more of the prefix
fixed. `reject_prefix_hex` is a list of 1–16 distinct nonempty hex prefixes; after the positive forms
hold, a reply that starts with any of them fails the step. Name the error frames the firmware can send,
including the length bytes they carry. Line forms do not apply to frames. Decoders read the captured
frame by byte offset from its first sync byte, so `integer` and `float32` work as in schema 3.

### Binary replies for qualification

A model with frame steps is qualified with `replies.json` in schema `interface-replies/2`, which is
`interface-replies/1` with one addition: a step's reply may be `{"hex": "<the whole frame>"}` instead of
a list of lines. A frame step needs a hex reply. The frame must be the one the firmware would send,
check included, computed from the check routine and the handler that builds the reply, not from the
model's own declaration:

```json
{"schema":"interface-replies/2",
 "operations":{
   "identify":{"steps":[{"hex":"3cc30600814b454c500702e75c"}]},
   "set_rate":{"steps":[{"hex":"3cc3020090fa001f4d"}],"parameters":{"rate":250}}
 }}
```

The last two bytes of each frame are its CRC-16/MODBUS, low byte first; the qualifier refuses a reply
whose check is wrong. `python3 qualify.py . replies.json` delivers
binary replies complete and in 1, 2, 7, 16, 32 and 64-byte fragments under one `BINARY` framing, then
injects faults built from the model's own frame declaration: a well-formed unrelated reply (one content
byte changed and the check recomputed), a truncated frame, a corrupted check, a wrong sync byte, an
impossible length, a well-formed error frame starting with the model's first `reject_prefix_hex`, each
pinned prefix with its last byte changed and the check recomputed, a timeout, a disconnect, and a
trailing-bytes diagnostic. As before, qualification proves the model's framing and guards against the
replies the candidate assumed; it proves nothing about the device.

## Schema 6: USB channels and variable byte strings

The worked example is an invented thermal logger with one vendor-specific USB interface, number 0, holding a bulk OUT endpoint at address 0x01 and a bulk IN endpoint at address 0x82, both with a wMaxPacketSize of 64. A request is one OUT transfer: a command byte and its arguments. Every reply is one IN transfer of at most 64 bytes: the command echoed, a status byte and the payload. The status is `00` for success, `E1` for an argument the firmware rejects and `E2` for a command it does not know, so an error reply still starts with the echo. Command `4E` answers the model name as a NUL-terminated string. Command `53` answers the serial number as a string counted by a length byte, with no terminator. Command `54` answers a little-endian 16-bit status word (mode in bits 0–2, alarm in bit 7, battery percent in bits 8–15) followed by the temperature in hundredths of a degree as a signed little-endian 16-bit value. Command `4C` stores a label of 1–32 bytes sent after a count byte and followed by a CRC-8 over the count and the label. Vendor control request 17, host to device, clears the alarm. Nothing about it corresponds to a real product.

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

The identity reads the 18-byte device descriptor with a standard control request and pins its first 12 bytes, which end with the vendor and product identifiers (`3412` and `7856` are 0x1234 and 0x5678, little-endian), then asks the firmware for its name and pins the reply's echo, its status and the model text. `release_major` is the high byte of bcdDevice. The status reply `54 00 83 57 2e fb` decodes to mode 3, alarm 1, battery 87 percent and a temperature of -12.34 degC. `write_label` with the label `ALPHA-3`, hex `414c5048412d33`, sends `4c 07 41 4c 50 48 41 2d 33 9c`: the command, the count, the label and the CRC-8 over the count and the label. Each positive guard pins the echo and the success status, so an error reply fails it; `reject_prefix_hex` names the error replies the protocol documents, so the qualifier can build them.

### Bytes parameters and raw and length parts

A parameter `{"type":"bytes","min_length":m,"max_length":n}`, with 0 ≤ m ≤ n and 1 ≤ n ≤ 4096, takes an argument that is a string of an even number of hexadecimal digits, with no whitespace and no `0x` prefix, holding between m and n bytes: `"414c5048412d33"` is the seven bytes of `ALPHA-3`. It exists for payloads whose content and length the caller chooses, such as a label, a block of data or a variable-length request. It is not a way to pass a whole command: the command bytes stay literal `hex` parts beside it. An argument that is not such a string, or whose length is out of bounds, is refused before anything is sent.

A bytes parameter is encoded only by these tx parts. `{"arg": name, "format": "raw"}` inserts the argument's bytes verbatim, and the tx bound counts its `max_length`. `{"arg": name, "format": "length_u8"}` inserts the argument's byte count as one byte and requires `max_length` of at most 255; `length_u16le` and `length_u16be` insert the count as two bytes, little-endian or big-endian. Both kinds of part may name the same parameter, so a count prefix is derived from the payload rather than supplied as a second argument that could disagree with it. A check part (schema 5) covers raw and length parts like any other: in `write_label` the check spans parts 1 up to 3, the count and the label. A bytes parameter cannot be encoded as `ascii` or a numeric format and cannot fill a USB setup field. All of this needs schema 6.

### String decoders

`cstring` is `{"from","kind":"cstring","offset","max_length"?,"encoding"?,"unit"?}`: the text from byte `offset` up to the first NUL byte, without the NUL. The NUL must appear within the capture and, when `max_length` is given, within the first `max_length + 1` bytes from `offset` (at most `max_length` bytes of text); otherwise the decode fails. `offset` must lie inside the step's receive bound. Bytes after the NUL are ignored, so a reply padded to a whole packet decodes like an unpadded one.

`lpstring` is `{"from","kind":"lpstring","length_offset","length_size","byteorder","offset","includes_terminator","encoding"?,"unit"?}`: an unsigned count of `length_size` bytes (1 or 2) at `length_offset` in `byteorder`, and the text is that many bytes starting at `offset`. When `includes_terminator` is true the count includes one trailing NUL, which must be present and is removed; a count of zero gives the empty string. When it is false the counted bytes are all text. A count that runs past the end of the reply fails the decode. The length field must lie inside the receive bound, and `offset` may be at most the bound.

`encoding` is `ascii` (the default) or `utf-8`, decoded strictly: a byte that is not valid in the encoding fails the decode instead of being replaced. A failed decode is an `execution_error` of the operation, as a reply too short for an `integer` decoder is. Neither decoder reads UTF-16, so a USB string descriptor is pinned with `prefix_hex` or returned with `hex`, not decoded as text. Both decoders need schema 6.

### Bit fields

The `integer` decoder takes an optional `mask`, an integer from 1 to 2^64 − 1, and an optional `shift`, an integer from 0 to 8 × `size` − 1. The value is decoded as before, signed or not, shifted right by `shift`, ANDed with `mask`, and only then multiplied by `scale` and offset by `offset_value`. In `read_status`, `battery` is `(word >> 8) & 255`. A right shift keeps the sign of a signed value; a field that is itself signed but narrower than its container cannot be sign-extended. Both keys need schema 6.

### USB channels

A `usb` channel is `{"type":"usb","interface":N,"in_endpoint":A,"out_endpoint":B,"transfer":"bulk"}`. `interface` is the interface's bInterfaceNumber, 0–255 and 0 by default; the adapter uses its alternate setting 0 and never selects another. `in_endpoint` and `out_endpoint` are bEndpointAddress values written as JSON integers: an IN address has bit 7 set, so 0x82 is written 130, and an OUT address has it clear. `transfer` is `bulk` (the default) or `interrupt` and must match both endpoint descriptors. When the adapter opens the device it refuses, with code `channel`, an interface or endpoint that the active configuration does not have, a transfer type that disagrees with a descriptor, and an endpoint whose wMaxPacketSize is zero: these are faults of the model's channel. Code `binding` is kept for the operator's side: a malformed binding, a binding that matches no device or more than one, and a permission the binding does not grant. In schema 6 the validator also requires `out_endpoint` when any exchange step can transmit (its tx can be nonempty) and `in_endpoint` when any exchange step receives; a model whose steps are all `usb_control` needs neither. Take every one of these numbers from the device's own descriptors, never from a device that looks familiar.

The binding is supplied by the host operator, never by the model, and a model cannot name or change it: `{"vendor_id": 4660, "product_id": 22136}` with an optional `"serial_number": "..."` and two optional booleans, `"allow_set_configuration"` and `"allow_detach_kernel_driver"`, both false by default. `vendor_id` and `product_id` are JSON integers from 0 to 65535 (4660 is 0x1234; a string such as `"0x1234"` is refused). Exactly one attached device may match them, and the serial number when it is given. `allow_set_configuration` lets the adapter select the first configuration of a device that has none active; `allow_detach_kernel_driver` lets it detach an operating-system driver from the selected interface and reattach it on close. Without them the adapter refuses rather than changing the device's state.

An `exchange` step on a usb channel sends the whole tx in one OUT transfer to `out_endpoint`; a short write fails with `length`. An empty tx skips the OUT transfer. `exact` and `up_to` receive one IN transfer. Schema-6 `frame` receives read one IN endpoint packet at a time until one complete frame is buffered, with `max_reads`, `max_bytes`, and `timeout_ms` bounding the work. Surplus bytes remain buffered for the next framed exchange on that connection; an unframed exchange with buffered bytes fails before transmitting. Frame length, padding, sync, end bytes, and integrity are checked before the captured frame reaches a decoder. `usb_control` uses the default control endpoint and needs no channel endpoint. USB receive modes are `exact`, `up_to`, and, in schema 6, `frame`; `until` and `lines` are refused. Each exchange returns one frame, while a frame may span multiple IN packets.

A bulk or interrupt IN transfer ends when the device sends a packet shorter than the endpoint's wMaxPacketSize (a short packet, possibly of zero length) or when the host's buffer is full. The adapter reads the packet size from the endpoint descriptor when it opens the device, and refuses with code `invalid`, before sending anything, an `up_to` bound that is not a positive multiple of it. An `exact` length is read into a buffer of whole packets (the length rounded up to a multiple of the packet size, at most 65536 bytes) and the transfer must then be exactly `length` bytes, or the step fails with code `length`. The reason is that a buffer that is not a whole number of packets overflows: when the device sends a full packet into the tail of such a buffer, the host has nowhere to put the excess, and the transfer fails with an I/O error instead of returning the reply. A bound of more than one packet is allowed but is safe only for replies that the device ends with a short or zero-length packet: a reply that is exactly one full packet, with nothing after it, leaves a two-packet transfer open until the deadline, and the step fails with `timeout`. So set `max_bytes` to the IN endpoint's wMaxPacketSize, unless a reply can span packets and the firmware ends it with a short packet. Firmware that pads every reply to a full packet fails an `exact` receive shorter than the packet; model such replies with `up_to` and with decoders at fixed offsets, `cstring` or `lpstring`, which ignore the padding. The offline qualifier cannot check this rule, because the packet size comes from the device.

When the adapter opens the device and claims the interface, it drains the IN endpoint before the first step: up to 8 reads of one packet each, with a 10 ms timeout, stopping at the first read that times out. Anything read there answers a request made before this transport opened, typically one whose exchange timed out and whose reply arrived late, and it would otherwise be taken as the reply to the next request. It is discarded, and the result reports its size as `transport_notes: {"drained_bytes": n}`; the key is absent when nothing was discarded. The model cannot turn the drain off. A device that sends unsolicited data continuously loses at most 8 packets of it each time the transport opens.

A transfer that times out fails with code `timeout`. A stall, an overflow or a device that went away fails with `io`. A reply longer than its bound, or of the wrong `exact` length, fails with `length`. A host without pyusb or libusb fails with `dependency`. The transport is opened for each `execute` call and closed at its end, which releases the interface and reattaches any detached driver.

`usb_control` steps carry standard, class and vendor requests on the default control endpoint. Bit 7 of `request_type` is the direction: set, the request reads from the device, with an empty tx; clear, it writes to the device, with a receive of `exact` 0 and the data stage, if any, in tx. Bits 5–6 are the type (0 standard, 1 class, 2 vendor) and bits 0–4 the recipient (0 device, 1 interface, 2 endpoint; for the last two, `index` holds the interface number or the endpoint address). The identity of a USB device is best pinned with a standard GET_DESCRIPTOR read of the device descriptor, which needs no knowledge of the vendor protocol: `request_type` 128, `request` 6, `value` 256 (descriptor type 1 in the high byte, index 0 in the low byte), `index` 0 and a receive of `exact` 18. Its `prefix_hex` can pin bLength and bDescriptorType (`1201`), bcdUSB, the class, subclass and protocol, bMaxPacketSize0 and, at bytes 8–11, the vendor and product identifiers, little-endian. The configuration descriptor is `value` 512, read `up_to` its wTotalLength. A string descriptor is `value` 768 plus the string's index, with `index` holding the language identifier (1033 for US English), read `up_to` 255; its text is UTF-16LE after a two-byte header. A standard read is a `read` effect. A `read` operation runs without any grant, and the identity runs before every operation, so the validator refuses a `read` operation with a control request whose `request_type`, or any value its parameter can take, has bit 7 clear: a host-to-device request is `write` or `actuate`, like any other step that changes the device's state. The validator also refuses the standard (type 0) requests SET_ADDRESS (5), SET_CONFIGURATION (9) and SET_INTERFACE (11) under every effect, and the adapter refuses them again before sending anything: the host owns the device's address and configuration.

### Qualifying a USB model

A usb model is qualified with `interface-replies/2` in which every step that transfers, each `exchange` and each `usb_control` step in step order with delays excluded, takes one reply `{"hex": "..."}`: the complete framed message, unframed IN transfer, or data stage of a control read. A step that receives nothing, such as a write-only exchange or a control request without a data stage, takes `{"hex": ""}`. Line replies are refused on a usb channel.

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

`python3 qualify.py . replies.json` runs the real engine and USB adapter over a scripted device. For a framed receive it splits the predicted message into bounded USB reads; for an unframed receive it delivers one transfer. For framed replies it also mutates request/reply matching fields and padding counts when those are declared. Synthetic read sizes are chosen within `max_reads`; endpoint packet sizes still require the real descriptor. The qualifier then injects faults built from each step's own declaration, on the first transferring step of the identity and on the first of the operation: an unrelated reply (its first byte changed); a truncated reply (cut before the last byte that a guard or a fixed-offset decoder relies on; an `exact` reply loses its last byte); a reply one byte longer than the receive bound; a reply made of the model's first `reject_prefix_hex` padded with zeros to the reply's length; each literal that the step's positive guard pins, with its last byte changed; and a timeout. A fault that means nothing for a step, such as a changed byte on a step that receives nothing or an error reply for a step without `reject_prefix_hex`, is reported as not applicable. Every applicable fault must fail the operation at the step where it landed, before anything further is sent. A padding diagnostic then reruns the operation with every `up_to` reply zero-padded to its bound, as firmware that answers in whole packets would send it, and reports whether the outputs stay identical; outputs that differ mean that a decoder depends on the reply's length. The diagnostic is not part of the verdict, and the packet-size rule is checked only when a real device is opened. As for every channel, qualification proves the model's guards against the replies the candidate assumed and nothing about the device.

## Unsupported

CAN, GPIO, 1-Wire, USB isochronous transfers, USB alternate settings other than 0, data-dependent aggregation of several complete frames in one step, a host-appended zero-length packet, text decoding of UTF-16, sign extension of bit fields, automatic reconnect/retry, custom conversion code, SPI full-duplex,
custom dummy fill, byte stuffing or escaping inside frames (SLIP, COBS, HDLC transparency), frames whose
length field counts something other than bytes, and hunting for a sync pattern inside noise are
unsupported. A protocol that needs one of them is recorded as unsupported, not approximated.
