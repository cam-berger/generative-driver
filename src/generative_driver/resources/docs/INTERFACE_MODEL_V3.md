# Executable interface model, schema 3

Model one channel/device from supplied evidence. Put unknowns in `NOTES.md`; bindings are host-owned. Validation proves structure, not behavior or safety.

Use `interface-model/3` for bounded operations and typed outputs.

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

`transport_factory` lazily defaults to `open_transport(channel,binding)`; replay uses the same engine. Execute opens, identifies, operates, then closes/discards buffers. Unsupported modes fail before sending; adapters enforce bounds and close after uncertain failure.

| Channel | Protocol settings | Host binding / modes |
|---|---|---|
| I2C | `address_7bit`, `speed_hz` | `{"url":"ftdi://..."}`; `exact` |
| SPI | `cs`, `clock_hz`, `mode`, `bit_order` (`msb_first` only) | FTDI URL; `exact` |
| UART | `baudrate`; optional `bytesize`, `parity`, `stopbits` | `{"port":"..."}`; `exact`, `until`, `up_to` |
| TCP | `type:"tcp"` | `{"host":"...","port":1234}`; `exact`, `until`, `up_to` |
| USB | `interface`, `in_endpoint`, `out_endpoint`, `transfer` (`bulk`/`interrupt`) | Integer vendor/product IDs, optional serial; `exact`, `up_to` |

USB needs matching endpoint types/directions and one device. Configuration/detach requires explicit binding grants. USB-CDC uses UART/OS serial; only USB supports `control`.

`interface-result/1` returns `ok`, operation, outputs, units, `identity_verified` and ordered operation/step/TX/RX/USB-setup transcript. Failures add `error:{code,message}`, empty outputs/units. Attempted ≠ delivered; completed means shape passed, not guards/decoding. Replay proves no hardware behavior. Errors: `validation_error`, post-open `execution_error`, or transport code.

Validation returns `ok`, `defects:[{where,what}]`, `checks_run:[schema tag]`; unknown fields fail.

## Operations and bounds

Operations require `effect`, `parameters`, `steps`, `outputs` and provenance. Effects: `read`, `write`, `actuate`; non-read needs grants. Identity: read, no parameters, nonempty expectation. Outputs reference valid captures; captures cannot be overwritten.

| Limit | Value |
|---|---|
| Names | ASCII letter, then letters/digits/underscore; at most 64 characters |
| Model | At most 64 operations; JSON ≤1 MiB; no NaN/Infinity |
| Operation | At most 128 steps, 32 parameters; timeouts + delays ≤120000 ms |
| Timeout / delay | Integer 1–60000 ms / 0–1000 ms |
| Encoded TX | ≤65536 bytes; USB control ≤65535 |
| `exact` receive | `length` 0–65536; zero requests no response |
| `until` / `up_to` | `max_bytes` 1–65536 |

Parameters are bounded finite `integer`/`number`, or string enums without control characters. Arguments must match exactly; booleans are not numbers. No free command passthrough. TX parts are `{"hex":"..."}`, UTF-8 `{"text":"..."}`, or `{"arg":"name","format":"..."}`. Formats: `ascii`, `u8`, `u16le`, `u16be`, `u32le`, `u32be`, `i16le`, `i16be`, `i32le`, `i32be`, `f32le`, `f32be`. Bounds must fit the encoding; literal text may contain protocol delimiters.

Steps are `exchange`, `usb_control`, or `{"op":"delay","ms":N}`. Control adds `setup:{request_type,request,value,index}` to TX/RX/capture/expect. First two fields are unsigned 8-bit, last two unsigned 16-bit; each is an integer or `{"arg":"integer_parameter"}`. Direction, data and receive lengths must agree. Control supports neither `until` nor `lines`.

```json
{"mode":"exact","length":2}
{"mode":"until","delimiter_hex":"0a","max_bytes":4096}
{"mode":"up_to","max_bytes":64}
```

Streams handle partial I/O and buffer past delimiters. Short exact replies, excess data, expiry/disconnect fail. `up_to` may return one fragment, not complete multi-line replies.

## Expectations and decoding

Exactly one nonempty literal guard: `equals_hex` (whole reply), `prefix_hex`, or `contains_text` (byte substring).

| Decoder | Fields / result |
|---|---|
| `hex` | Capture bytes as hex |
| `utf8` | Text; optional `strip` |
| `ascii_number` | Number following literal `prefix` |
| `integer` | `offset`, `size` 1/2/4/8, `byteorder` little/big, `signed`; optional finite `scale`, `offset_value` |
| `float32` | `offset`, `byteorder`; optional finite `scale`, `offset_value` |

Decoders use `from` for the capture and optional `unit`. Validate source and bounds; numeric outputs must be finite. No model-provided regex, eval, imports, code or filesystem references.

## Worked example

Invented device; replace values/provenance with supplied evidence.

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

## Unsupported

CAN, GPIO, 1-Wire, USB isochronous, automatic reconnect/retry, custom conversion code, SPI full-duplex and custom dummy fill are unsupported. Record unsupported behavior; do not approximate it.
