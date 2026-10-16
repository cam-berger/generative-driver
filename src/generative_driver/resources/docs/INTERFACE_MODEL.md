# Register interface model, schema 1

Historical register contract: live I2C, register-file scoring and SPI capture. Live SPI is refused. Schemas 3–6 support executable command interfaces; schema 2 is separate static firmware evidence. Front-ends produce the same shape regardless of evidence source.

## Model

Invented example; no values describe a real part:

```json
{
  "schema": "interface-model/1",
  "device": {"name": "what the evidence supports, or null", "confidence": 0.0},
  "channel": {"type": "i2c", "address_7bit": "0x5A", "address_alternatives": ["0x5B"], "speed_hz": 100000,
              "wiring": "free text: which pins, which pull-ups, what the master is"},
  "operations": {
    "init":     [ {"op": "reg_write", "reg": "0x30", "bytes": ["0x01"]}, {"op": "delay_ms", "ms": 5} ],
    "identify": [ {"op": "reg_read", "reg": "0x0F", "len": 1, "as": "id"} ],
    "measure":  [ {"op": "reg_read", "reg": "0x40", "len": 6, "as": "raw"} ]
  },
  "identity": {"from": "id", "expect": ["0x3C"]},
  "state": [ {"name": "trim", "op": "reg_read", "reg": "0x20", "len": 8} ],
  "measure_period_ms": 250,
  "measure_wait_ms": 0,
  "conversion": {"module": "convert.py", "entry": "convert", "outputs": {"v": "volt", "i": "amp"}},
  "safety": {"safe_reads": ["0x0F", "0x20-0x27", "0x40-0x45"], "known_safe_writes": ["0x30=0x01"], "never": []},
  "provenance": [ {"item": "identify", "source": "document page and table, or function and offset, or bus capture", "how": "..."} ],
  "confidence": {"channel": 0.0, "init": 0.0, "identify": 0.0, "measure": 0.0, "state": 0.0, "conversion": 0.0}
}
```

Runner order: init → state reads retained for the session → identify checked against `identity.expect` → repeated measure every `measure_period_ms`, with optional initial `measure_wait_ms`. Other named operations may take `args`.

| Operation | Fields |
|---|---|
| `reg_write` | reg, bytes |
| `reg_read` | reg, len, as |
| `raw_write` | bytes |
| `raw_read` | len, as |
| `delay_ms` | ms |
| `probe` | Address acknowledgment only |

Channels: I2C and SPI. `uart_cmd`, `usb`, `tcp` are reserved here, not executable schema-1 transports. `tools/model_exec.py` implements operations by channel; downstream stages do not infer the evidence source.

## SPI capture framing

SPI selects a chip-select line, not an address. Register framing is device-specific:

```json
"channel": {
  "type": "spi", "mode": 0, "bit_order": "msb_first", "clock_hz": 1000000,
  "cs": "the chip-select line, named the way the bus master names it",
  "framing": {
    "command_bytes": 1,
    "address_mask": "0x3F", "address_shift": 0,
    "read_prefix": "0x40", "write_prefix": "0x80", "burst_prefix": "0xC0",
    "read_dummy_bytes": 0,
    "note": "free text: how the command byte is built, and anything the prefixes do not capture"
  },
  "wiring": "which pins, which chip select, what the master is"
}
```

Command byte: `prefix | ((R & address_mask) << address_shift)`. Use `read_prefix` for one-byte reads, `burst_prefix` for longer reads with a separate opcode, and `write_prefix` for writes. Equalize read/burst prefixes when the device does. `read_dummy_bytes` inserts required idle bytes. Record unrepresentable framing in `framing.note` and provenance; do not force it. State, identity, conversion, safety and register-file scoring retain their I2C meanings.

## Conversion

`convert.py` defines `convert(inputs: dict) -> dict`, standard library only. Inputs contain state/measurement `bytes` keyed by start register as lowercase hex, e.g. `"0x20"`. Return floats keyed by `conversion.outputs` in declared units. Preserve the firmware/datasheet arithmetic and rounding; do not substitute an approximation.
