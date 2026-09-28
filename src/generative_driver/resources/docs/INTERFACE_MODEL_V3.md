# Executable interface model, schema 3

One model describes one channel/device. Use only supplied evidence and record unresolved semantics in NOTES.md. The host supplies physical bindings outside the model. A passing validator proves structure, not firmware fidelity or physical safety.


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

Channel types/settings: `i2c` with `address_7bit` and `speed_hz`; `spi` with `cs`, `clock_hz`, `mode`, `bit_order` (initial implementation supports `msb_first`); `uart` with `baudrate`, optional `bytesize`, `parity`, `stopbits`; `tcp`; `usb` with optional `interface`, `in_endpoint`, `out_endpoint`, `transfer` (`bulk` or `interrupt`). USB endpoint types and directions must agree with descriptors. USB-CDC uses the UART transport and OS serial binding, rather than claiming all USB devices are serial.

Receive shapes:

```json
{"mode":"exact", "length":2}
{"mode":"until", "delimiter_hex":"0a", "max_bytes":4096}
{"mode":"up_to", "max_bytes":64}
```

All lengths are 0–65536 for exact and 1–65536 for bounded modes. Timeouts are integer 1–60000 ms. Stream adapters support exact/until/up_to, retain bytes after delimiters, handle partial writes/reads and close on uncertain transaction failure. I2C/SPI support exact; USB supports exact/up_to. Empty receive with exact length zero means no response requested. Short exact responses, oversized data, deadline expiry and disconnects are errors. Every adapter enforces bounds even when called without the model validator.

Engine module `interface_runtime.engine` supplies:

```python
def validate_model(model: dict) -> dict:  # ok, defects[{where,what}], checks_run
    ...
def execute(model: dict, operation: str, parameters=None, *, binding=None,
            allow_effects=(), transport_factory=None, delay_fn=None) -> dict:
    ...
```

`transport_factory` defaults lazily to `open_transport` and has the same two arguments. It provides a real seam for replay and tests, not a second execution engine. Results include schema `interface-result/1`, `ok`, operation, typed outputs, units, identity_verified and an ordered transcript of operation/step, requested TX/returned RX hex and USB setup where relevant. Failures include `error:{code,message}` and empty outputs/units. Transcript status `attempted` does not prove bytes reached the device; `completed` means the transport response passed its shape check, while expectations/decoding can still fail. Transcripts cannot imply fresh hardware verification when replayed. Validation must reject unsupported/unknown fields rather than ignore a misspelled limit or effect.

## Schema 3

```json
{
  "schema":"interface-model/3",
  "device":{"name":"evidence-backed name or null"},
  "channel":{"type":"uart","baudrate":115200},
  "identity":{"operation":"identify"},
  "operations":{
    "identify":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange","tx":[{"text":"ID?\n"}],
        "rx":{"mode":"until","delimiter_hex":"0a","max_bytes":128},
        "timeout_ms":1000,"capture":"id",
        "expect":{"contains_text":"DEMO-42"}}],
      "outputs":{"identity":{"from":"id","kind":"utf8","strip":true}}
    },
    "measure":{
      "effect":"read", "parameters":{},
      "steps":[{"op":"exchange","tx":[{"hex":"540a"}],
        "rx":{"mode":"until","delimiter_hex":"0a","max_bytes":128},
        "timeout_ms":1000,"capture":"reply"}],
      "outputs":{"temperature":{"from":"reply","kind":"ascii_number","prefix":"T:","unit":"degC"}}
    },
    "set_level":{
      "effect":"write", "parameters":{"level":{"type":"integer","minimum":0,"maximum":100}},
      "steps":[{"op":"exchange","tx":[{"text":"SET "},{"arg":"level","format":"ascii"},{"hex":"0a"}],
        "rx":{"mode":"until","delimiter_hex":"0a","max_bytes":64},
        "timeout_ms":1000,"capture":"ack","expect":{"equals_hex":"4f4b0a"}}],
      "outputs":{}
    }
  },
  "provenance":[
    {"item":"operations.identify","source":"decomp.c: function+offset"},
    {"item":"operations.measure","source":"decomp.c: function+offset"},
    {"item":"operations.set_level","source":"decomp.c: function+offset"}
  ]
}
```

Operations contain effect, parameters, steps and outputs. Every operation needs provenance. Identity references a read operation without parameters and with a nonempty response expectation. Names are simple identifiers (ASCII letter followed by letters/digits/underscore, maximum 64 characters). Each model has at most 64 operations; each operation at most 128 steps and 32 parameters. Sum of step timeouts/delays is at most 120000 ms per operation. Models serialize to at most 1 MiB with no NaN/Infinity.

Parameters support bounded integer/number values, or string enums (no free command passthrough). Numeric limits are mandatory and finite. Supplied arguments must match exactly; booleans are not numbers. A TX list contains literal `{hex}`, literal `{text}` (UTF-8), or `{arg,format}` parts. Formats: `ascii`, `u8`, `u16le`, `u16be`, `u32le`, `u32be`, `i16le`, `i16be`, `i32le`, `i32be`, `f32le`, `f32be`. Bounds must be compatible with the selected encoding; total encoded bytes must not exceed 65536. Constant text may include protocol delimiters; enum arguments may not contain control characters.

Steps are `exchange`, `usb_control`, or `delay`. Delay is `{op:"delay",ms:integer}` with 0–1000 ms. A USB control step uses the same tx/rx/capture/expect fields plus `setup:{request_type,request,value,index}`. The first two setup values are unsigned 8-bit and the latter two unsigned 16-bit; values can be integers or `{arg:"integer_parameter"}`. Request direction and payload/receive lengths must agree. `until` is invalid for control transfers. Control data is bounded at 65535 bytes because USB wLength is 16-bit; other exchanges allow 65536.

Expectations support nonempty literal `equals_hex`, `prefix_hex` or `contains_text` only, with exactly one matching form. Captures cannot be overwritten. Output decoders are `hex`, `utf8` (optional strip), `ascii_number` (literal prefix), `integer` (offset, size 1/2/4/8, byteorder little/big, signed boolean, optional finite scale/offset_value), and `float32` (offset, byteorder, optional scale/offset_value). Numeric outputs must be finite. Parsing never uses model-provided regex, eval, code imports or filesystem references. Decoder source capture and bounds must be valid.


CAN, GPIO, 1-Wire, USB isochronous, automatic reconnect/retry, custom conversion code, SPI full-duplex and custom dummy fill are unsupported.
