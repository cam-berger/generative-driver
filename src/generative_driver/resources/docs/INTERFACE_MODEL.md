# Interface model, schema 1

This is the historical register-model contract. New executable multi-transport models use
[schema 3](INTERFACE_MODEL_V3.md) and, from 2026-09-18, its superset [schema 4](INTERFACE_MODEL_V4.md) with completion-aware line replies, and `interpret-interface`. Schema 1 retains register-file scoring,
live I2C and SPI capture observation. Its live runner does not implement SPI and now refuses that path
explicitly. Schema-2 experimental firmware evidence remains a separate evidence format.

The original shared data shape for register front-ends and back-half stages. The example below is a made-up device; nothing in it describes any real part. A front-end (firmware reader,
datasheet reader, self-description reader, probe-only) owns the operation types it needs. The runner
(`tools/model_exec.py`) implements each operation type per channel type. Nothing downstream of the front-end
knows where the model came from.

## model.json

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

Order of execution by the runner: `operations.init`, then every `state` read (kept for the life of the
session), then `operations.identify` checked against `identity.expect`, then `operations.measure` repeated
every `measure_period_ms` after an optional `measure_wait_ms` for the first sample. Any other operation
under `operations` is exposed by name and may take `args`.

Operation types in schema 1: `reg_write` (reg, bytes), `reg_read` (reg, len, as), `raw_write` (bytes),
`raw_read` (len, as), `delay_ms` (ms), `probe` (address acknowledge only). Channel types in schema 1:
`i2c` and `spi`. Reserved names for later front-ends: `uart_cmd` (request and response strings), `usb`, `tcp`.

## The spi channel (added 2026-09-14 for the first non-I2C device)

SPI has no addresses. A device is selected by its chip-select line, and a register access is a frame of
bytes whose first byte the device decodes as a command. Because that encoding differs from part to part,
the channel carries it and the operations stay the same: `reg_read` and `reg_write` mean what they mean on
any register device, and `framing` says how the runner turns one into bytes on the wire.

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

The command byte for an access to register R is `prefix | ((R & address_mask) << address_shift)`, with
`read_prefix` for a `reg_read` of one byte, `burst_prefix` for a `reg_read` of more than one where the
device has a separate burst opcode, and `write_prefix` for a `reg_write`. A device that uses the same
prefix for single and burst reads sets them equal. `read_dummy_bytes` is for parts that need idle bytes
between the command and the data. A part whose framing this cannot express should say so in `framing.note`
and describe the access in the provenance rather than forcing it.

Nothing else changes. `state`, `identity`, `conversion`, `safety` and `provenance` mean exactly what they
mean for an I2C device, and a model of an SPI part executes against a register file for scoring in the
same way, because a register read is a register read whatever carried it.

## convert.py

`def convert(inputs: dict) -> dict`. `inputs` maps each byte string the model reads, keyed by its start
register as a lower-case hex string (for example `"0x20"`), to a `bytes` object; every `state` read and
every `measure` read appears. The result maps the keys of `conversion.outputs` to floats in the stated units.
Standard library only. The arithmetic must be the device's own (the firmware's or the datasheet's), not a
textbook approximation.
