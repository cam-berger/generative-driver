# Device Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A plugged-in serial device that resets on connect can be bound by USB identity, listened to, acquired (ESP32 flash) and probed through reset-aware sessions, with `reset` as an explicit operator grant, ending at a hashed `interpret-input` handoff.

**Architecture:** The shared runtime (`src/interface_runtime`) gains UART identity resolution, boot waits, a `Session` and the `reset` grant, so probe, ground and emitted packages connect identically. The toolchain gains `acquire_serial_listen` and reset-aware ESP acquisition. The configurator derives per-run binding state (`listened`, `resets_on_open`) from tool and operator events and gates every serial connection on it.

**Tech Stack:** Python 3.11+, pyserial (optional dependency, `serial` and `serial.tools.list_ports`), esptool, unittest, sqlite (configurator store).

**Spec:** `docs/superpowers/specs/2026-10-06-device-connection.md`

## Global Constraints

- Core Python must run on native macOS and Windows 11+; no Unix-only unconditional imports, no shell-dependent process launch, no hardcoded user paths.
- Hardware libraries are optional: import `serial` only inside the functions that open a port.
- Effect grants are exactly `read`, `write`, `actuate`, `reset`; operation effects stay `read`, `write`, `actuate`.
- `execute()` result shape (`interface-result/1`) and existing recorded replays must remain valid unchanged.
- Worker context never contains the port path; configurator-owned arguments (`port`, `binding`, `allow_effects`, `allow_reset`, `run_dir`, `bus_url`) are refused when a worker supplies them.
- Listen duration is at most 30 seconds; boot `timeout_ms` is 1..60000; unsolicited-output window is 500 ms.
- Tests run with `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest ...` from the worktree root. Scripted tests are contract checks, never physical or model evidence.
- One failing test, then its implementation, per slice; commit after each task with the `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` trailer.

## Review Focus

- Board unplugged or renamed between stages: identity binding must resolve the new path, and a vanished device must fail as an operator `binding` fault, not a model fault (Task 3).
- Device that never prints a boot line (wrong `line_prefix`, wrong baud): must fail with `boot_timeout` and the discarded byte count, and the port must close (Task 2).
- Worker smuggling a port or `allow_reset` through tool arguments: must be refused before dispatch (Task 7).
- Model omits `resets_on_open` although the run observed it: dispatch must refuse rather than open under `read` (Task 7).
- Listen on a quiet device (no output at all): must record `unsolicited_output: false`, set no reset property, and still be accepted acquisition evidence (Tasks 5 and 7).

---

### Task 0: Commit the trial fixes this plan builds on

The worktree already carries three tested fixes from the trial (uncommitted): Goose workers launch without `--no-profile`, `acquire_classify` receives the bound port instead of a worker port, and generic stage context carries operator observations. Task 7 replaces the port block from the second fix.

- [ ] **Step 1: Verify** `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_agents tests.test_configurator -v` passes.
- [ ] **Step 2: Commit**

```bash
git add src/generative_driver/agents.py src/generative_driver/configurator.py tests/test_agents.py tests/test_configurator.py
git commit -m "Give Goose workers their recipe tools and bound acquisition context"
```

---

### Task 1: UART channel declarations and the `reset` grant

**Files:**
- Modify: `src/interface_runtime/transports.py:70-90` (`validate_channel`)
- Modify: `src/interface_runtime/engine.py:732-734` (grant validation in `execute`)
- Test: `tests/test_package_engine.py`

**Interfaces:**
- Produces: UART channel keys `boot: {"line_prefix": str, "timeout_ms": int}` and `resets_on_open: bool`; `GRANTS = ("read","write","actuate","reset")` in `engine.py`; refusal code `effect` with fault `operator` when `resets_on_open` lacks `reset`, before any open.

- [ ] **Step 1: Write the failing tests** (append before `class EngineTests` in `tests/test_package_engine.py`)

```python
def test_uart_boot_and_reset_declarations_validate(model):
    model["channel"].update(boot={"line_prefix": "@ boot", "timeout_ms": 5000}, resets_on_open=True)
    assert validate_model(model)["ok"], validate_model(model)
    for bad in ({"line_prefix": "", "timeout_ms": 5000}, {"line_prefix": "x", "timeout_ms": 0},
                {"line_prefix": "x", "timeout_ms": 60001}, {"line_prefix": "x"}):
        broken = copy.deepcopy(model); broken["channel"]["boot"] = bad
        assert not validate_model(broken)["ok"], bad
    broken = copy.deepcopy(model); broken["channel"]["resets_on_open"] = "yes"
    assert not validate_model(broken)["ok"]


def test_resetting_channel_requires_reset_grant_before_open(model):
    model["channel"]["resets_on_open"] = True
    boundary = Boundary([])
    result = run(model, boundary)
    assert not result["ok"] and result["error"]["code"] == "effect"
    assert result["error"]["fault"] == "operator" and not boundary.opened
    assert result["transport_open_attempted"] is False
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (bytes.fromhex("540a"), b"T:21.5\n")])
    assert run(model, boundary, allow_effects=("reset",))["ok"]
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_package_engine -k reset -k boot -v`
Expected: FAIL (`unsupported` field `boot`, and `invalid effect grants` for `reset`).

- [ ] **Step 3: Implement**

In `validate_channel`, change the uart entry to `'uart': (['baudrate','bytesize','parity','stopbits','boot','resets_on_open'], ['baudrate'])` and extend the uart branch:

```python
        if 'resets_on_open' in channel and type(channel['resets_on_open']) is not bool:
            _fail('resets_on_open must be boolean')
        if 'boot' in channel:
            boot = channel['boot']
            _fields(boot, ['line_prefix','timeout_ms'], ['line_prefix','timeout_ms'], 'boot')
            _string(boot['line_prefix'], 'boot.line_prefix')
            _integer(boot['timeout_ms'], 1, 60000, 'boot.timeout_ms')
```

Confirm `_string` rejects the empty string; if it does not, add `if not boot['line_prefix']: _fail('boot.line_prefix must be nonempty')`.

In `engine.py` add near the top-level constants `GRANTS = ("read", "write", "actuate", "reset")`, replace the grant check with `all(v in GRANTS for v in allow_effects)`, and directly after it:

```python
        _require(not model["channel"].get("resets_on_open") or "reset" in allow_effects,
                 "effect", "channel resets the device on open; requires an explicit reset grant", Fault.OPERATOR)
```

- [ ] **Step 4: Run to verify pass, then the whole engine suite**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_package_engine -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/interface_runtime/transports.py src/interface_runtime/engine.py tests/test_package_engine.py
git commit -m "Declare UART boot waits and require a reset grant for resetting channels"
```

---

### Task 2: Runtime `Session` with boot wait

**Files:**
- Modify: `src/interface_runtime/engine.py:709-818` (split `execute` into `Session`)
- Modify: `src/interface_runtime/evidence.py:88-116` (`RecordingFactory.Recorder.wait_boot`)
- Modify: `src/interface_runtime/__init__.py` (export `Session` if the module exports names)
- Test: `tests/test_package_engine.py`

**Interfaces:**
- Consumes: Task 1 channel keys and `GRANTS`.
- Produces: `Session(model, *, binding=None, allow_effects=(), transport_factory=None, delay_fn=None)` with `execute(operation, parameters=None) -> dict` (same `interface-result/1` shape as `execute()`), `close()`, context manager. Transport opens lazily on the first `execute` that passes validation. Transports may implement `wait_boot(line_prefix: str, timeout_ms: int) -> int` (discarded byte count, raises `TransportError('boot_timeout', ...)`); the session adds the count to `transport_notes.drained_bytes` of the first result. `execute()` becomes `with Session(...) as s: return s.execute(operation, parameters)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_package_engine.py`)

```python
def test_session_runs_operations_over_one_open_with_identity_each_time(model):
    from interface_runtime.engine import Session
    opens = []
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (bytes.fromhex("540a"), b"T:21.5\n"),
                         (b"ID?\n", b"DEMO-42\n"), (bytes.fromhex("540a"), b"T:22.0\n")])
    def factory(channel, binding):
        opens.append(1); return boundary.factory(channel, binding)
    with Session(model, binding={}, transport_factory=factory) as session:
        first, second = session.execute("measure"), session.execute("measure")
    assert first["outputs"] == {"temperature": 21.5} and second["outputs"] == {"temperature": 22.0}
    assert len(opens) == 1 and boundary.closed and not boundary.script


def test_session_waits_for_boot_line_and_reports_discarded_bytes(model):
    from interface_runtime.engine import Session
    model["channel"].update(boot={"line_prefix": "@ boot", "timeout_ms": 2000})
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (bytes.fromhex("540a"), b"T:21.5\n")])
    waits = []
    boundary.wait_boot = lambda prefix, timeout_ms: waits.append((prefix, timeout_ms)) or 37
    with Session(model, binding={}, transport_factory=boundary.factory) as session:
        result = session.execute("measure")
    assert waits == [("@ boot", 2000)] and result["transport_notes"] == {"drained_bytes": 37}


def test_boot_timeout_fails_and_closes(model):
    from interface_runtime.transports import TransportError
    model["channel"].update(boot={"line_prefix": "@ boot", "timeout_ms": 50})
    boundary = Boundary([])
    def never(prefix, timeout_ms):
        raise TransportError("boot_timeout", "no boot line after 50 ms; discarded 12 bytes")
    boundary.wait_boot = never
    result = run(model, boundary)
    assert not result["ok"] and result["error"]["code"] == "boot_timeout" and boundary.closed


def test_session_validation_failure_never_opens(model):
    from interface_runtime.engine import Session
    boundary = Boundary([])
    with Session(model, binding={}, transport_factory=boundary.factory) as session:
        result = session.execute("missing")
    assert not result["ok"] and result["transport_open_attempted"] is False and not boundary.opened
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_package_engine -k session -k boot_timeout -v`
Expected: FAIL with `ImportError: cannot import name 'Session'`.

- [ ] **Step 3: Implement**

Restructure `execute` in `engine.py`. Keep every existing line of validation, the inner `run(name, arguments)` closure, identity handling and the exception-to-result mapping; move them into `Session.execute`. Opening and closing move to the session:

```python
class Session:
    """One open transport for a sequence of operations; identity is verified before each operation.
    The transport opens lazily, after the first operation passes validation, and always closes."""

    def __init__(self, model, *, binding=None, allow_effects=(), transport_factory=None, delay_fn=None):
        self.model, self.binding, self.allow_effects = model, binding, allow_effects
        self.transport_factory, self.delay_fn = transport_factory, delay_fn
        self.transport = None
        self.pending_drained = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _open(self, model):
        factory = self.transport_factory
        if factory is None:
            from .transports import open_transport
            factory = open_transport
        self.transport = factory(model["channel"], {} if self.binding is None else dict(self.binding))
        boot = model["channel"].get("boot")
        if boot and hasattr(self.transport, "wait_boot"):
            self.pending_drained += self.transport.wait_boot(boot["line_prefix"], boot["timeout_ms"])

    def close(self):
        transport, self.transport = self.transport, None
        if transport is not None:
            transport.close()

    def execute(self, operation, parameters=None):
        ...  # body of the old execute(), with these replacements:
```

Inside `Session.execute`, use `self.model`, `self.binding`, `self.allow_effects`, `self.delay_fn`. Replace the open block with:

```python
        transport_open_attempted = self.transport is not None
        if self.transport is None:
            transport_open_attempted = True
            self._open(model)
        transport = self.transport
        drained = getattr(transport, "drained_bytes", 0)
        drained = (drained if type(drained) is int and drained > 0 else 0) + self.pending_drained
        self.pending_drained = 0
        if drained:
            result["transport_notes"] = {"drained_bytes": drained}
```

Remove the `finally: transport.close()` from `Session.execute` (the session closes). Keep the error mapping so a failure inside `_open` (including `boot_timeout`) becomes `ok: false` with that code; set `transport_open_attempted` to `True` once `_open` was entered. After an exception that occurred with an open transport, call `self.close()` so a failed operation never leaves the port open, matching today's single-shot behaviour. The module-level function becomes:

```python
def execute(model, operation, parameters=None, *, binding=None, allow_effects=(), transport_factory=None, delay_fn=None):
    """(keep the existing docstring)"""
    session = Session(model, binding=binding, allow_effects=allow_effects,
                      transport_factory=transport_factory, delay_fn=delay_fn)
    result = session.execute(operation, parameters)
    try:
        session.close()
    except Exception as exc:
        if result["ok"]:
            result.update(ok=False, outputs={}, units={}, transport_open_attempted=True,
                          error={"code": "close_error", "message": str(exc), "fault": Fault.HOST.value})
    return result
```

In `evidence.py` `RecordingFactory.Recorder`, add delegation so recording keeps boot waits:

```python
            def wait_boot(self, line_prefix, timeout_ms):
                wait = getattr(handle, 'wait_boot', None)
                return wait(line_prefix, timeout_ms) if wait else 0
```

- [ ] **Step 4: Run engine, package and evidence-dependent suites**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_package_engine tests.test_package tests.test_package_benchmark -v`
Expected: all PASS (existing `execute` and replay tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/interface_runtime/engine.py src/interface_runtime/evidence.py src/interface_runtime/__init__.py tests/test_package_engine.py
git commit -m "Run interface operations in reset-aware sessions with boot waits"
```

---

### Task 3: UART identity bindings, released control lines and the stream boot wait

**Files:**
- Modify: `src/interface_runtime/transports.py:576-625` (`validate_binding`, `_binding`, `open_transport`) and `_Stream` (add `wait_boot`)
- Create: `tests/test_serial_transport.py`

**Interfaces:**
- Produces: `resolve_serial_port(binding: dict, ports=None) -> str` (uses `serial.tools.list_ports.comports()` when `ports` is None; each port has `.device`, `.vid`, `.pid`, `.serial_number`); raises `TransportError('binding', ..., Fault.OPERATOR)` on no match or ambiguity. UART bindings accept `{"port"}` or `{"vendor_id","product_id"[, "serial_number"]}`. `_Stream.wait_boot(line_prefix, timeout_ms) -> int`.

- [ ] **Step 1: Write the failing tests** (`tests/test_serial_transport.py`)

```python
"""UART identity resolution and opening, with a fake pyserial so no hardware or OS port is needed."""
import sys
import types
import unittest
from unittest.mock import patch

from interface_runtime.transports import TransportError, open_transport, resolve_serial_port

CHANNEL = {"type": "uart", "baudrate": 115200}


def port(device, vid=0x10C4, pid=0xEA60, serial_number="abc"):
    return types.SimpleNamespace(device=device, vid=vid, pid=pid, serial_number=serial_number)


class FakeSerial:
    instances = []

    def __init__(self, *args, **kwargs):
        assert not args and not kwargs, "open must be deferred until control lines are released"
        self.events, self.chunks, self.timeout, self.write_timeout = [], [], 0, 0
        FakeSerial.instances.append(self)

    def __setattr__(self, name, value):
        if name in ("dtr", "rts", "port", "baudrate", "bytesize", "parity", "stopbits") and hasattr(self, "events"):
            self.events.append((name, value))
        object.__setattr__(self, name, value)

    def open(self):
        self.events.append(("open", None))

    def reset_input_buffer(self):
        self.events.append(("flush", None))

    @property
    def in_waiting(self):
        return sum(len(c) for c in self.chunks)

    def read(self, size=1):
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        if len(chunk) > size:
            self.chunks.insert(0, chunk[size:]); chunk = chunk[:size]
        return chunk

    def close(self):
        self.events.append(("close", None))


def fake_serial_module():
    module = types.ModuleType("serial")
    module.Serial = FakeSerial
    return module


class SerialTransportTests(unittest.TestCase):
    def test_identity_resolves_current_path(self):
        ports = [port("/dev/cu.other", vid=1, pid=2), port("/dev/cu.usbserial-11140")]
        self.assertEqual(resolve_serial_port({"vendor_id": 0x10C4, "product_id": 0xEA60}, ports), "/dev/cu.usbserial-11140")
        self.assertEqual(resolve_serial_port({"vendor_id": 0x10C4, "product_id": 0xEA60, "serial_number": "abc"}, ports),
                         "/dev/cu.usbserial-11140")

    def test_missing_or_ambiguous_identity_is_operator_fault(self):
        for ports in ([], [port("/dev/a"), port("/dev/b", serial_number="def")]):
            with self.assertRaises(TransportError) as raised:
                resolve_serial_port({"vendor_id": 0x10C4, "product_id": 0xEA60}, ports)
            self.assertEqual((raised.exception.code, raised.exception.fault.value), ("binding", "operator"))

    def test_open_releases_control_lines_before_opening_and_flushes(self):
        FakeSerial.instances.clear()
        with patch.dict(sys.modules, {"serial": fake_serial_module()}):
            transport = open_transport(CHANNEL, {"port": "/dev/cu.test"})
        events = [name for name, _ in FakeSerial.instances[0].events]
        self.assertLess(events.index("dtr"), events.index("open"))
        self.assertLess(events.index("rts"), events.index("open"))
        self.assertLess(events.index("open"), events.index("flush"))
        transport.close()

    def test_stream_boot_wait_discards_through_boot_line(self):
        FakeSerial.instances.clear()
        with patch.dict(sys.modules, {"serial": fake_serial_module()}):
            transport = open_transport(CHANNEL, {"port": "/dev/cu.test"})
        FakeSerial.instances[0].chunks = [b"ESP-ROM\r\nnoise\n@ boot ok\nREST"]
        self.assertEqual(transport.wait_boot("@ boot", 500), len(b"ESP-ROM\r\nnoise\n@ boot ok\n"))
        self.assertEqual(bytes(transport.buffer), b"REST")
        FakeSerial.instances[0].chunks = [b"no boot line here\n"]
        with self.assertRaises(TransportError) as raised:
            transport.wait_boot("@ boot", 50)
        self.assertEqual(raised.exception.code, "boot_timeout")
        self.assertIn("discarded", str(raised.exception))
        transport.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_serial_transport -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_serial_port'`.

- [ ] **Step 3: Implement**

In `transports.py`, change `validate_binding`'s uart entry to accept both shapes. Replace `'uart': (['port'], ['port'])` with a dedicated check before the generic call:

```python
    if channel['type'] == 'uart' and 'port' not in binding:
        try:
            _fields(binding, ['vendor_id','product_id','serial_number'], ['vendor_id','product_id'], 'binding')
            for key in ('vendor_id','product_id'):
                _integer(binding[key], 0, 65535, key)
            if 'serial_number' in binding:
                _string(binding['serial_number'], 'serial_number')
        except TransportError as exc:
            raise TransportError('binding', str(exc), Fault.OPERATOR) from None
        return
```

Add:

```python
def resolve_serial_port(binding, ports=None):
    """Current OS path for a UART binding; identity bindings survive a replug that renames the port."""
    if 'port' in binding:
        return binding['port']
    if ports is None:
        list_ports = _dependency('serial.tools.list_ports')
        ports = list_ports.comports()
    matches = [p for p in ports if p.vid == binding['vendor_id'] and p.pid == binding['product_id']
               and ('serial_number' not in binding or p.serial_number == binding['serial_number'])]
    if len(matches) != 1:
        what = 'no serial port' if not matches else f'{len(matches)} serial ports'
        raise TransportError('binding', f'{what} match vendor {binding["vendor_id"]:#06x} product '
                             f'{binding["product_id"]:#06x}; supply serial_number or check the cable', Fault.OPERATOR)
    return matches[0].device
```

Check `_dependency` imports dotted modules (`importlib.import_module(name)`); if it uses `__import__` without `fromlist`, change it to `importlib.import_module`.

Replace the uart branch of `open_transport`:

```python
        if kind == 'uart':
            serial = _dependency('serial')
            handle = serial.Serial()
            handle.port = resolve_serial_port(binding)
            handle.baudrate = channel['baudrate']
            handle.bytesize = channel.get('bytesize', 8)
            handle.parity = channel.get('parity', 'N')
            handle.stopbits = channel.get('stopbits', 1)
            handle.timeout = 0
            handle.write_timeout = 0
            # Released lines keep common auto-reset bridges from pulsing EN; some hosts pulse anyway.
            handle.dtr = False
            handle.rts = False
            handle.open()
            handle.reset_input_buffer()
            return _Stream(handle, 'uart')
```

Keep the `TransportError` re-raise ahead of the generic `except Exception` so binding faults keep code `binding`. Add to `_Stream`:

```python
    def wait_boot(self, line_prefix, timeout_ms):
        """Discard input through the first line starting with line_prefix; return the discarded byte count."""
        deadline = time.monotonic() + timeout_ms / 1000
        prefix = line_prefix.encode('utf-8')
        discarded = 0
        while True:
            while b'\n' in self.buffer:
                end = self.buffer.index(b'\n') + 1
                line = bytes(self.buffer[:end]).rstrip(b'\r\n')
                del self.buffer[:end]
                discarded += end
                if line.startswith(prefix):
                    return discarded
            try:
                self.buffer += self._read(4096, deadline)
            except TransportError as exc:
                if exc.code != 'timeout':
                    raise
                raise TransportError('boot_timeout', f'no boot line after {timeout_ms} ms; '
                                     f'discarded {discarded + len(self.buffer)} bytes') from None
```

If `_remaining(deadline)` raises a different code at expiry, catch that code too so expiry always maps to `boot_timeout`. Confirm `exchange` consumes `self.buffer` first; if it does not, prepend the buffered bytes in `exchange` before reading.

- [ ] **Step 4: Run transport, engine and package suites**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_serial_transport tests.test_package_engine tests.test_package -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/interface_runtime/transports.py tests/test_serial_transport.py
git commit -m "Bind UART devices by USB identity and open with released control lines"
```

---

### Task 4: Single-session evidence and the package session interface

**Files:**
- Modify: `src/interface_runtime/evidence.py:156-180` (`run_samples`)
- Modify: `src/generative_driver/resources/toolchain/template/interface_driver/__init__.py` (add `session`, `sequence`)
- Modify: `src/generative_driver/resources/toolchain/template/interface_driver/__main__.py` (add `sequence`, `--allow-effect reset`)
- Test: `tests/test_package_engine.py`, `tests/test_package.py`

**Interfaces:**
- Consumes: `Session` (Task 2).
- Produces: `run_samples(...)` uses one `Session` for all samples when `model["channel"].get("resets_on_open")`, adding `"session": "single"` to its return dict (absent otherwise). Package API `session(*, binding=None, allow_effects=())` returning a context manager whose `execute(operation, parameters=None)` returns `interface-result/1`; `sequence(steps, *, binding=None, allow_effects=())` returning `{"ok": bool, "results": [...]}` where `steps` is a list of `{"operation": str, "parameters": dict}` and execution stops at the first failure. CLI: `python -m driver sequence --steps JSON --binding JSON [--allow-effect write|actuate|reset ...]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_package_engine.py`:

```python
def test_resetting_channel_samples_share_one_session(model):
    from interface_runtime.evidence import run_samples
    model["channel"]["resets_on_open"] = True
    opens = []
    boundary = Boundary([(b"ID?\n", b"DEMO-42\n"), (bytes.fromhex("540a"), b"T:21.5\n")] * 3)
    def factory(channel, binding):
        opens.append(1); return boundary.factory(channel, binding)
    with patch("interface_runtime.evidence.open_transport", factory):
        evidence = run_samples(model, "measure", n=3, binding={}, allow_effects=("reset",))
    assert evidence["ok"] and evidence["session"] == "single" and len(opens) == 1
```

Append to `PackageTests` in `tests/test_package.py`, reusing the emission steps from `test_emitted_package_relocates_and_replays_without_source_checkout` up to `emitted`:

```python
    def test_emitted_package_runs_a_sequence_over_one_connection(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            model = root / "model"; model.mkdir()
            (model / "model.json").write_text(json.dumps(example_model()))
            replay = root / "replay.json"; replay.write_text(json.dumps(example_replay()))
            def call(tool_name, **arguments):
                result = call_tool(tool_name, {"run_dir": str(root / "run"), **arguments})
                self.assertEqual(result.get("_exit", 0), 0, result)
                return result
            probe = call("probe_run", model_dir=str(model), operation="measure", n=1, replay=str(replay))
            emitted = call("emit_package", model_dir=str(model), probe_json=probe["probe"])
            sys.path.insert(0, emitted["package_dir"])
            try:
                import importlib
                driver = importlib.import_module("driver")
                opens = []
                class Fake:
                    def exchange(self, tx, rx, timeout_ms):
                        return self.replies.pop(0)
                    def close(self):
                        pass
                fake = Fake()
                fake.replies = [bytes.fromhex(t["rx_hex"]) for t in example_replay()["transactions"]] * 2
                def factory(channel, binding):
                    opens.append(1); return fake
                with patch("interface_runtime.evidence.open_transport", factory), \
                     patch("interface_runtime.engine.Session._open",
                           lambda self, model: setattr(self, "transport", factory(model["channel"], {}))):
                    result = driver.sequence([{"operation": "measure"}, {"operation": "measure"}], binding={})
                self.assertTrue(result["ok"], result)
                self.assertEqual(len(result["results"]), 2)
                self.assertEqual(len(opens), 1)
            finally:
                sys.path.remove(emitted["package_dir"])
                sys.modules.pop("driver", None)
```

Add `from unittest.mock import patch` to the imports of `tests/test_package.py` if absent. If `example_replay()` transactions include the identity exchange, the doubled reply list covers identity plus measure for each step; adjust the multiplier to the actual transaction count per operation after reading `example_replay()`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_package_engine -k one_session tests.test_package -k sequence -v`
Expected: FAIL (`KeyError: 'session'`; `AttributeError: module 'driver' has no attribute 'sequence'`).

- [ ] **Step 3: Implement**

In `run_samples`, after building `recorder`:

```python
    single = bool(model['channel'].get('resets_on_open')) and player is None
    results = []
    if single:
        from .engine import Session
        with Session(model, binding=binding, allow_effects=allow_effects, transport_factory=recorder) as session:
            for _ in range(n):
                result = session.execute(operation, parameters)
                results.append(result)
                if not result['ok']:
                    break
    else:
        for _ in range(n):
            ...  # existing loop unchanged
```

and add `**({'session': 'single'} if single else {})` to the returned dict.

In the template `__init__.py` add:

```python
def session(*, binding=None, allow_effects=()):
    """One connection for several operations; a device that resets on open is not restarted between them."""
    from interface_runtime.engine import Session
    model, _ = _load()
    return Session(model, binding=binding, allow_effects=allow_effects)


def sequence(steps, *, binding=None, allow_effects=()):
    """Run [{operation, parameters}] in order over one connection, stopping at the first failure."""
    if type(steps) is not list or not steps or any(type(s) is not dict or type(s.get('operation')) is not str for s in steps):
        raise ValueError('sequence requires a nonempty list of {operation, parameters}')
    results = []
    with session(binding=binding, allow_effects=allow_effects) as current:
        for step in steps:
            result = current.execute(step['operation'], step.get('parameters') or {})
            results.append(result)
            if not result['ok']:
                break
    return {'ok': all(r['ok'] for r in results) and len(results) == len(steps), 'results': results}
```

In `__main__.py`: change both `--allow-effect` choices to `['write','actuate','reset']`, import `sequence`, and add:

```python
    steps = commands.add_parser('sequence', help='several operations over one connection')
    steps.add_argument('--steps', type=json.loads, required=True)
    steps.add_argument('--binding', type=json.loads, default={})
    steps.add_argument('--allow-effect', action='append', choices=['write','actuate','reset'], default=[])
```

with dispatch `elif args.command == 'sequence': result = sequence(args.steps, binding=args.binding, allow_effects=args.allow_effect)`.

Update the effect sentence in `src/generative_driver/_toolchain/interface.py:169-176` so REFERENCE.md lists `reset` when the model channel declares `resets_on_open`, and change the grant check at `interface.py:365` to accept `reset`.

- [ ] **Step 4: Run package and engine suites**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_package_engine tests.test_package tests.test_package_benchmark -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/interface_runtime/evidence.py src/generative_driver/resources/toolchain/template/interface_driver src/generative_driver/_toolchain/interface.py tests/test_package_engine.py tests/test_package.py
git commit -m "Keep resetting devices in one session for probes and emitted packages"
```

---

### Task 5: `acquire_serial_listen`

**Files:**
- Modify: `src/generative_driver/_toolchain/acquire.py` (new tool)
- Modify: `src/generative_driver/resources/toolchain/tools.json` (catalog entry)
- Test: `tests/test_toolkit.py`

**Interfaces:**
- Produces: tool `acquire_serial_listen(run_dir, port, baudrate=115200, seconds=8, **_)` returning `{"available": True, "unsolicited_output": bool, "bytes": int, "first_byte_ms": int|None, "artifacts": [listen.bin, listen.json], "artifact": listen.json}` with absolute paths, `_files` hashes. `port` is configurator-injected (Task 7). Refuses `seconds` outside (0, 30] and baudrate outside 1..12000000 with `available: False`, `_exit: 1`.

- [ ] **Step 1: Write the failing test** (add to the toolkit test class in `tests/test_toolkit.py`)

```python
    def test_serial_listen_records_capture_without_transmitting(self):
        import sys, types
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        writes = []
        class Port:
            def __init__(self): self.chunks = [b"ESP-ROM:esp32s3\r\n", b"waiting for download\r\n"]
            def open(self): pass
            def reset_input_buffer(self): pass
            def read(self, size=1): return self.chunks.pop(0) if self.chunks else b""
            def write(self, data): writes.append(data)
            def close(self): pass
        module = types.ModuleType("serial"); module.Serial = Port
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"serial": module}):
            result = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.3})
            self.assertTrue(result["available"], result)
            self.assertTrue(result["unsolicited_output"])
            self.assertEqual(writes, [])
            capture = json.loads(Path(result["artifact"]).read_text())
            self.assertIn("waiting for download", capture["text"])
            self.assertEqual(Path(result["artifacts"][0]).read_bytes(), b"ESP-ROM:esp32s3\r\nwaiting for download\r\n")
            refused = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 31})
            self.assertFalse(refused["available"])

    def test_serial_listen_on_a_quiet_device_is_evidence_without_reset(self):
        import sys, types
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        class Quiet:
            def open(self): pass
            def read(self, size=1): return b""
            def close(self): pass
        module = types.ModuleType("serial"); module.Serial = Quiet
        with tempfile.TemporaryDirectory() as temp, patch.dict(sys.modules, {"serial": module}):
            result = call_tool("acquire_serial_listen", {"run_dir": temp, "port": "/dev/cu.fake", "seconds": 0.2})
        self.assertTrue(result["available"])
        self.assertFalse(result["unsolicited_output"])
        self.assertEqual((result["bytes"], result["first_byte_ms"]), (0, None))
```

Make sure `json`, `tempfile` and `Path` are imported at the top of `tests/test_toolkit.py`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_toolkit -k serial_listen -v`
Expected: FAIL (unknown tool `acquire_serial_listen`).

- [ ] **Step 3: Implement** (in `acquire.py`, after `acquire_flash`)

```python
@core.tool("acquire_serial_listen")
def acquire_serial_listen(run_dir, port, baudrate=115200, seconds=8, **_):
    """Record what a serial device sends with control lines released and nothing transmitted."""
    if not isinstance(port, str) or not port.strip():
        return {"available": False, "reason": "No bound serial port", "_exit": 1}
    if type(seconds) not in (int, float) or not 0 < seconds <= 30:
        return {"available": False, "reason": "seconds must be in (0, 30]", "_exit": 1}
    if type(baudrate) is not int or not 1 <= baudrate <= 12000000:
        return {"available": False, "reason": "baudrate must be an integer 1..12000000", "_exit": 1}
    import serial  # optional hardware dependency, imported only when listening
    run = core.resolve_run(run_dir)
    out = _mkdir(run, "acquire")
    handle = serial.Serial()
    handle.port, handle.baudrate, handle.timeout = port, baudrate, 0.05
    handle.dtr = False
    handle.rts = False
    started = time.monotonic()
    handle.open()
    data, first = b"", None
    try:
        while time.monotonic() - started < seconds:
            chunk = handle.read(4096)
            if chunk:
                first = first if first is not None else int((time.monotonic() - started) * 1000)
                data += chunk
    finally:
        handle.close()
    unsolicited = first is not None and first <= 500
    raw = os.path.join(out, "listen.bin")
    meta = os.path.join(out, "listen.json")
    with open(raw, "wb") as f:
        f.write(data)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump({"schema": "serial-listen/1", "baudrate": baudrate, "seconds": seconds, "bytes": len(data),
                   "first_byte_ms": first, "unsolicited_output": unsolicited,
                   "text": data.decode("utf-8", "backslashreplace")}, f, indent=1)
    raw, meta = os.path.abspath(raw), os.path.abspath(meta)
    return {"available": True, "unsolicited_output": unsolicited, "bytes": len(data), "first_byte_ms": first,
            "artifact": meta, "artifacts": [raw, meta], "_files": {raw: core.sha256(raw), meta: core.sha256(meta)}}
```

Add `import time` and `import json` at the top of `acquire.py` if absent. Add a catalog entry to `tools.json` modelled on `acquire_classify`: name `acquire_serial_listen`, description "Stage 1. Record what the bound serial device sends for up to 30 seconds with DTR/RTS released and nothing transmitted. Output within 500 ms of opening is recorded as unsolicited and marks the binding as resetting on open; every later connection then requires the operator's reset grant. The configurator supplies the port.", input properties `run_dir` (string), `baudrate` (integer, default 115200), `seconds` (number, default 8). Do not list `port` in the schema.

- [ ] **Step 4: Run toolkit suite (catalog tests included)**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_toolkit -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/generative_driver/_toolchain/acquire.py src/generative_driver/resources/toolchain/tools.json tests/test_toolkit.py
git commit -m "Add a passive serial listen acquisition tool"
```

---

### Task 6: Reset-aware ESP acquisition with an `interpret-input` handoff

**Files:**
- Modify: `src/generative_driver/_toolchain/acquire.py:70-121` (`acquire_flash`) and `acquire_classify` (pass-through of `allow_reset`, propagate `available` and `artifacts`)
- Test: `tests/test_toolkit.py`

**Interfaces:**
- Produces: `acquire_flash(..., allow_reset=False)`: with `allow_reset` uses `--before default-reset` for every esptool/espefuse call and `--after hard-reset` on the final call, dumping at 921600; without it uses `--before no-reset --after no-reset` and dumps at 115200 so the chip is left at its default baud. Success returns `available: True`, `artifacts` (absolute paths of `flash.bin`, `app.bin`, `app.elf`, `efuse.txt`, `interpret-input.elf`), `interpret_input` (absolute path of `interpret-input.elf`, or `interpret-input.bin` when ELF conversion failed but `app.bin` exists). `acquire_classify(..., allow_reset=False)` forwards it and, when the flash rung succeeds, includes `available: True`, `artifacts` and `interpret_input` from the flash result.

- [ ] **Step 1: Write the failing test**

```python
    def test_esp_flash_uses_auto_reset_only_with_grant_and_names_interpret_input(self):
        from unittest.mock import patch
        from generative_driver.toolkit import call_tool
        from generative_driver._toolchain import core
        commands = []
        def fake_sh(cmd, cwd=None, env=None, timeout=900):
            commands.append(cmd)
            if "chip-id" in cmd:
                return 0, "Chip is ESP32-S3 (QFN56)\nMAC: 80:45:6b:2d:2f:6c\n", ""
            if "summary" in cmd:
                return 0, "SPI_BOOT_CRYPT_CNT = Disable\nSECURE_BOOT_EN = False\n", ""
            if "read-flash" in cmd:
                Path(cmd[-1]).write_bytes(b"\xe9" + b"\xff" * 64)
                return 0, "", ""
            if any(str(c).endswith("esp_image_to_elf.py") for c in cmd):
                Path(cmd[-2]).write_bytes(b"app"); Path(cmd[-1]).write_bytes(b"\x7fELF")
                return 0, "", ""
            return 0, "", ""
        with tempfile.TemporaryDirectory() as temp, patch.object(core, "sh", fake_sh):
            granted = call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32", "allow_reset": True})
            self.assertTrue(granted["available"], granted)
            self.assertTrue(granted["interpret_input"].endswith("interpret-input.elf"))
            self.assertTrue(Path(granted["interpret_input"]).is_file())
            esp = [c for c in commands if "-p" in c]
            self.assertTrue(all("default-reset" in c for c in esp))
            self.assertIn("hard-reset", esp[-1])
            commands.clear()
            call_tool("acquire_flash", {"run_dir": temp, "port": "/dev/cu.fake", "backend": "esp32"})
            esp = [c for c in commands if "-p" in c]
            self.assertTrue(all("no-reset" in c and "default-reset" not in c for c in esp))
            dump = next(c for c in esp if "read-flash" in c)
            self.assertEqual(dump[dump.index("-b") + 1], "115200")
```

If `acquire_flash` writes `app.bin` through a step other than `esp_image_to_elf.py` (read the lines between the fuse check and the ELF conversion), make the fake create `app.bin` at that step instead.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_toolkit -k esp_flash -v`
Expected: FAIL (`interpret_input` missing; `no-reset` used with the grant).

- [ ] **Step 3: Implement**

In `acquire_flash` add parameter `allow_reset=False` and build the flags once:

```python
    before = ["--before", "default-reset" if allow_reset else "no-reset"]
    stay = ["--after", "no-reset"]
    dump_baud = "921600" if allow_reset else "115200"   # no-reset leaves the stub at the dump baud
```

Use `[*esptool, "-p", port, *before, *stay, "chip-id"]`, `[*espefuse, "-p", port, *before, "summary"]` (write its stdout to `efuse.txt` in `out`), and for the dump `[*esptool, "-p", port, "-b", dump_baud, *before, "--after", "hard-reset" if allow_reset else "no-reset", "read-flash", "0", "ALL", flash]`. The chunked path keeps its current behaviour. After ELF conversion:

```python
    chosen = elf if os.path.isfile(elf) else (app if os.path.isfile(app) else None)
    if chosen is None:
        ...  # keep the existing failure return
    handoff = os.path.join(out, "interpret-input" + os.path.splitext(chosen)[1])
    shutil.copyfile(chosen, handoff)
    paths = [os.path.abspath(p) for p in (flash, app, elf, efuse, handoff) if os.path.exists(p)]
    return {"available": True, "rung": "flash", "chip": chip, "mac": mac, "artifact": os.path.abspath(handoff),
            "artifacts": paths, "interpret_input": os.path.abspath(handoff),
            "_files": {p: core.sha256(p) for p in paths}}
```

Add `import shutil` if absent. When ELF conversion fails but `app.bin` exists, return the success shape with `interpret-input.bin` instead of the current failure; keep the failure for a missing `app.bin`. In `acquire_classify`, accept `allow_reset=False`, call `acquire_flash(run_dir=run_dir, port=port, backend=flash_backend, allow_reset=allow_reset)`, and in the success return add `"available": True, "artifacts": r["artifacts"], "interpret_input": r["interpret_input"]`.

- [ ] **Step 4: Run toolkit suite**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_toolkit -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/generative_driver/_toolchain/acquire.py tests/test_toolkit.py
git commit -m "Acquire ESP flash with an explicit reset grant and name the interpret input"
```

---

### Task 7: Configurator reset gating, identity injection and acquisition artifacts

**Files:**
- Modify: `src/generative_driver/configurator.py` — `STAGE_TOOLS` (:22-29), effects validation (:672-673), `binding_identity` (:98-127), `_tool` (:1218-1300), `respond` handling (:344-362)
- Test: `tests/test_configurator.py`

**Interfaces:**
- Consumes: `resolve_serial_port` (Task 3), `acquire_serial_listen` (Task 5), `allow_reset` on ESP tools (Task 6).
- Produces: effects accept `reset`. `_binding_state(run_id) -> {"listened": bool, "resets_on_open": bool}` derived from this run's `tool.finished` events for `acquire_serial_listen` (`ok`, `result.unsolicited_output`) and `operator.response` events whose observation is `{"binding_property": "resets_on_open", "value": false}`. Serial tools: `acquire_serial_listen`, `acquire_classify`, `acquire_flash` always; `probe_run`, `interface_execute`, `emit_test_live` when the model's channel type is `uart`. `observed_artifacts` also hashes every absolute path in a result's `artifacts` list.

- [ ] **Step 1: Write the failing tests** (add to `ConfiguratorTests`; the helper starts a generic run with a sleeping executor and returns the active acquire assignment, following `test_acquire_receives_the_operator_serial_binding_not_a_worker_port`)

```python
    def _serial_run(self, controller, effects=('read',), binding=None):
        run = controller.call('start', {'goal':'Scripted serial contract', 'effects':list(effects),
            'binding': binding or {'port': '/dev/cu.contract'},
            'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}})
        until = time.monotonic() + 3
        while time.monotonic() < until:
            assignment = next((e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'), None)
            if assignment and controller.call('status', run)['status'] == 'running':
                return run, assignment
            time.sleep(.02)
        self.fail('no running acquire assignment')

    def test_listen_records_reset_and_later_connections_need_the_grant(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            if name == 'acquire_serial_listen':
                return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
            return {'device_class': None, 'reason': 'scripted', '_exit': 1}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller)
                request = {**run, 'assignment_id': assignment['id']}
                with self.assertRaisesRegex(ValueError, 'listen'):
                    controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'flash_backend': 'esp32'}})
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {'seconds': 1}})
                self.assertEqual(captured['acquire_serial_listen']['port'], '/dev/cu.contract')
                with self.assertRaisesRegex(ValueError, 'reset grant'):
                    controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'flash_backend': 'esp32'}})
                with self.assertRaisesRegex(ValueError, 'configurator-owned'):
                    controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {'allow_reset': True}})
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_reset_grant_lets_esp_acquisition_auto_reset(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller, effects=('read', 'reset'))
                request = {**run, 'assignment_id': assignment['id']}
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'flash_backend': 'esp32'}})
                self.assertIs(captured['acquire_classify']['allow_reset'], True)
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_model_must_declare_an_observed_reset(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory) / 'home')
            try:
                model_dir = Path(directory) / 'model'; model_dir.mkdir()
                (model_dir / 'model.json').write_text(json.dumps({'channel': {'type': 'uart', 'baudrate': 115200},
                    'operations': {'read': {'effect': 'read'}}}))
                run, assignment = self._serial_run(controller, effects=('read', 'reset'))
                request = {**run, 'assignment_id': assignment['id']}
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                # The probe tool is not assigned to acquire; exercise the gate directly through the same check.
                with patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': ['acquire_serial_listen', 'probe_run']}):
                    with self.assertRaisesRegex(ValueError, 'omits resets_on_open'):
                        controller.call('tool', {**request, 'name': 'probe_run',
                                                 'arguments': {'model_dir': str(model_dir), 'operation': 'read'}})
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_identity_binding_is_resolved_for_serial_tools(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            return {'available': True, 'unsolicited_output': False, '_exit': 0, 'artifacts': []}
        identity = {'vendor_id': 0x10C4, 'product_id': 0xEA60, 'serial_number': 'abc'}
        with tempfile.TemporaryDirectory() as directory, \
             patch('generative_driver.toolkit.call_tool', fake_call_tool), \
             patch('interface_runtime.transports.resolve_serial_port', lambda binding, ports=None: '/dev/cu.usbserial-11140'):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller, binding=identity)
                controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_serial_listen', 'arguments': {}})
                self.assertEqual(captured['acquire_serial_listen']['port'], '/dev/cu.usbserial-11140')
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_operator_can_clear_a_detected_reset(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run = controller.call('start', {'goal': 'Scripted override', 'binding': {'port': '/dev/cu.contract'},
                    'executor_config': {'command': ['missing-contract-runtime']}})
                until = time.monotonic() + 5
                while controller.call('status', run)['status'] in ('queued', 'running') and time.monotonic() < until:
                    time.sleep(.02)
                controller.call('respond', {**run, 'observation': {'binding_property': 'resets_on_open', 'value': False}})
                self.assertEqual(controller._binding_state(run['run_id'])['resets_on_open'], False)
            finally:
                controller.close()
```

Assignments save their tool list when created, so in `test_model_must_declare_an_observed_reset` move the `patch.dict(... STAGE_TOOLS ...)` context to wrap `self._serial_run(...)` as well; the gate under test is the configurator's, not the tool list. If `controller.call('start', ...)` returns a dict whose run id key is not `run_id`, use the key the existing tests use with `controller.call('result', run)`.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_configurator -k listen -k reset -k identity_binding -v`
Expected: FAIL (`effects must contain only read, write, actuate`; tool not assigned; no `_binding_state`).

- [ ] **Step 3: Implement**

1. `STAGE_TOOLS['acquire']` becomes `['acquire_serial_listen','acquire_firmware_artifact','acquire_datasheet','acquire_protocol_spec','acquire_classify']`.
2. Effects validation: `set(effects) <= {'read','write','actuate','reset'}` and message `'effects must contain only read, write, actuate, reset'`. The uncertain-flag computation at :214 and `effectful` at :1261 stay limited to `write`/`actuate`.
3. `binding_identity`: before the USB branch, keep USB identity bindings product-wide (`['usb', vid, pid]`), which now also covers UART identity bindings; no change needed beyond confirming `serial_number` is ignored.
4. Add the binding-state helper:

```python
    def _binding_state(self, run_id):
        """Per-run serial facts: whether the device was listened to and whether opening it resets it."""
        listened = resets = False
        with self._db() as db:
            rows = db.execute("SELECT kind,payload FROM events WHERE run_id=? AND kind IN ('tool.finished','operator.response') ORDER BY seq",(run_id,)).fetchall()
        for row in rows:
            data = json.loads(row['payload'])
            if row['kind'] == 'tool.finished' and data.get('name') == 'acquire_serial_listen' and data.get('ok'):
                listened = True
                resets = resets or bool(data.get('result',{}).get('unsolicited_output'))
            elif row['kind'] == 'operator.response':
                observed = data.get('observation') or {}
                if observed.get('binding_property') == 'resets_on_open' and observed.get('value') is False:
                    resets = False
        return {'listened': listened, 'resets_on_open': resets}
```

5. In `_tool`, replace the `acquire_classify` port block added earlier in this branch with:

```python
        if {'allow_reset','port'} & arguments.keys():
            raise ValueError('Connection, output directory and effect grants are configurator-owned')
        serial_tool = name in ('acquire_serial_listen','acquire_classify','acquire_flash')
        model_channel = None
        if name in ('probe_run','interface_execute','emit_test_live') and not arguments.get('replay'):
            model_channel = _model_channel(name, arguments)
        if serial_tool or (model_channel or {}).get('type') == 'uart':
            binding = assignment.get('binding') or {}
            if not binding:
                raise ValueError('No operator-owned device binding for this stage')
            granted = 'reset' in assignment['effects']
            state = self._binding_state(run['id'])
            if name != 'acquire_serial_listen' and not state['listened'] and not granted:
                raise ValueError('Listen to the bound serial device first, or supply a reset grant')
            if state['resets_on_open'] and not granted:
                raise ValueError('The bound device resets when opened; this connection requires a reset grant')
            if model_channel is not None and state['resets_on_open'] and not model_channel.get('resets_on_open'):
                raise ValueError('The model omits resets_on_open although this run observed the device reset on open')
            if serial_tool:
                from interface_runtime.transports import resolve_serial_port
                arguments['port'] = resolve_serial_port(binding)
                if name != 'acquire_serial_listen':
                    arguments['allow_reset'] = granted
```

with a module-level helper reusing the model location logic of `operation_effects`:

```python
def _model_channel(name, arguments):
    model_dir = arguments.get('model_dir')
    path = Path(model_dir)/'model.json' if model_dir else Path(arguments['package_dir'])/'driver/model.json'
    return json.loads(path.read_text(encoding='utf-8')).get('channel', {})
```

Keep the existing check that refuses `run_dir`, `binding`, `allow_effects`, `bus_url`. Remove `port` from any tool `inputSchema` exposed to workers by the gateway if the gateway forwards catalog schemas (check where `contracts` are built in `_tool`/`tools`; filter `port` and `allow_reset` the same way `binding` is filtered).

6. In the `observed_artifacts` loop, also collect list entries:

```python
        for raw in result.get('artifacts', []) if isinstance(result.get('artifacts'), list) else []:
            if isinstance(raw, str) and Path(raw).is_absolute():
                path = Path(raw).resolve()
                if path.is_relative_to(workspace) and path.exists():
                    observed_artifacts[str(path)] = digest(path)
```

- [ ] **Step 4: Run configurator, service and pipeline suites**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_configurator tests.test_service tests.test_pipeline_repair tests.test_benchmark_physical -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/generative_driver/configurator.py tests/test_configurator.py
git commit -m "Gate serial connections on listen evidence and the reset grant"
```

---

### Task 8: Interpret handoff and acquire stage context

**Files:**
- Modify: `src/generative_driver/configurator.py:976-1015` (`_prepare`)
- Test: `tests/test_configurator.py`

**Interfaces:**
- Consumes: `interpret-input.*` artifact name (Task 6), binding presence.
- Produces: generic interpret uses the single accepted acquire artifact named `interpret-input.elf` or `interpret-input.bin` when present, otherwise the existing one-binary rule. Acquire context gains `"serial": "Listen first with acquire_serial_listen. If it reports unsolicited output the device resets on open and every later connection needs the operator's reset grant. Granted effects: ..."` when the spec has a binding.

- [ ] **Step 1: Write the failing test**

```python
    def test_interpret_prefers_the_named_acquisition_input(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                acquired = root / 'acquire'; acquired.mkdir()
                for name in ('flash.bin', 'app.bin', 'interpret-input.elf'):
                    (acquired / name).write_bytes(b'\x7fELF' if name.endswith('.elf') else b'\xe9')
                accepted = [{'stage': 'acquire', 'artifacts': [{'path': str(acquired / n), 'sha256': '', 'kind': 'file'}
                                                               for n in ('flash.bin', 'app.bin', 'interpret-input.elf')]}]
                seen = {}
                def fake_call_tool(name, arguments):
                    seen.update(arguments['inputs'])
                    return {'ok': False, 'reason': 'scripted stop'}
                from unittest.mock import patch
                with patch('generative_driver.toolkit.call_tool', fake_call_tool):
                    prepared = controller._prepare({'goal': 'x', 'effects': ['read']}, 'interpret', root / 'run', root / 'work', accepted)
                self.assertEqual(seen['image.bin'], str(acquired / 'interpret-input.elf'))
                self.assertIn('blocked', prepared)
            finally:
                controller.close()

    def test_acquire_context_explains_listen_and_reset_without_the_port(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                (root / 'work').mkdir()
                prepared = controller._prepare({'goal': 'x', 'effects': ['read', 'reset'], 'binding': {'port': '/dev/cu.secret'}},
                                               'acquire', root / 'run', root / 'work', [])
                text = json.dumps(prepared['context'])
                self.assertIn('acquire_serial_listen', text)
                self.assertIn('reset', text)
                self.assertNotIn('/dev/cu.secret', text)
            finally:
                controller.close()
```

Add `import json` to `tests/test_configurator.py` if absent. If `_verify_snapshot` rejects the minimal spec, it returns early for specs without `_snapshot_sha256`, so no change is expected.

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_configurator -k interpret_prefers -k acquire_context -v`
Expected: FAIL (three binaries hit the one-binary rule; no `serial` context).

- [ ] **Step 3: Implement**

In the interpret branch:

```python
            artifacts = [Path(a['path']) for a in acquisition['artifacts']] if acquisition else []
            named = [p for p in artifacts if p.name in ('interpret-input.elf', 'interpret-input.bin')]
            images = named if len(named) == 1 else [p for p in artifacts if p.suffix.lower() in ('.bin','.elf')]
```

In the generic return, after the `device` note:

```python
        if spec.get('binding') and stage == 'acquire':
            context['serial'] = ('Listen first with acquire_serial_listen. If it reports unsolicited output the device '
                                 'resets on open, and every later connection needs the operator reset grant. '
                                 'Granted effects: ' + ', '.join(spec['effects']) + '.')
```

- [ ] **Step 4: Run configurator suite**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_configurator -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/generative_driver/configurator.py tests/test_configurator.py
git commit -m "Hand the named acquisition image to interpret and explain serial grants to acquire"
```

---

### Task 9: CLI, MCP and doctor surface; documentation

**Files:**
- Modify: `src/generative_driver/setup.py:97-120` (`doctor`)
- Modify: `src/generative_driver/mcp.py:26-35` (docstring for effects)
- Modify: `docs/setup.md`, `docs/support.md`, `src/generative_driver/resources/toolchain/skills/*` only where effects or acquisition order are listed (grep for `actuate`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `doctor()["serial_ports"]` as a list of `{"device", "vendor_id", "product_id", "serial_number", "description"}` (empty list when pyserial is unavailable, with `"serial_ports_available": false`).

- [ ] **Step 1: Write the failing test** (add to the CLI test class)

```python
    def test_doctor_lists_serial_ports_by_usb_identity(self):
        import sys, types
        from unittest.mock import patch
        from generative_driver.setup import doctor
        listing = types.ModuleType("serial.tools.list_ports")
        listing.comports = lambda: [types.SimpleNamespace(device="/dev/cu.usbserial-11140", vid=0x10C4, pid=0xEA60,
                                                          serial_number="abc", description="CP2102N")]
        tools = types.ModuleType("serial.tools"); tools.list_ports = listing
        serial = types.ModuleType("serial"); serial.tools = tools
        with tempfile.TemporaryDirectory() as home, \
             patch.dict(sys.modules, {"serial": serial, "serial.tools": tools, "serial.tools.list_ports": listing}):
            report = doctor(home)
        self.assertEqual(report["serial_ports"], [{"device": "/dev/cu.usbserial-11140", "vendor_id": 0x10C4,
            "product_id": 0xEA60, "serial_number": "abc", "description": "CP2102N"}])
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_cli -k serial_ports -v`
Expected: FAIL with `KeyError: 'serial_ports'`.

- [ ] **Step 3: Implement**

In `doctor`, before the return:

```python
    try:
        from serial.tools import list_ports
        serial_ports = [{"device": p.device, "vendor_id": p.vid, "product_id": p.pid,
                         "serial_number": p.serial_number, "description": p.description}
                        for p in list_ports.comports() if p.vid is not None]
        ports_available = True
    except ImportError:
        serial_ports, ports_available = [], False
```

and add `"serial_ports": serial_ports, "serial_ports_available": ports_available` to the returned dict. In `mcp.py` `driver_start` docstring, state that effects may include `reset` for devices that restart when their port opens. In `docs/setup.md` "Add optional hardware tools", add: identity binding example `{"vendor_id": 4292, "product_id": 60000, "serial_number": "..."}` taken from `doctor`'s `serial_ports`; the listen-first order; the `reset` effect. In `docs/support.md` UART row, add "identity binding; reset-on-open devices need the `reset` grant". Update any skill text that enumerates effects.

- [ ] **Step 4: Run CLI, MCP, distribution suites**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest tests.test_cli tests.test_mcp tests.test_distribution -v`
Expected: all PASS.

- [ ] **Step 5: Full suite**

Run: `PYTHONPATH=src /Users/cberg/Research/generative-driver/.venv/bin/python -m unittest discover -s tests`
Expected: PASS except `test_dashboard`'s CLI test while the main checkout's editable install lacks the dashboard; record any other failure and fix before committing.

- [ ] **Step 6: Commit**

```bash
git add src/generative_driver/setup.py src/generative_driver/mcp.py docs/setup.md docs/support.md src/generative_driver/resources/toolchain/skills
git commit -m "Expose serial identities in doctor and document the reset grant"
```

---

### Task 10: Physical verification on the reference board (operator present)

**Files:**
- Modify: `docs/implementation/new-device-trial-findings.md` (record outcome)
- Create: `docs/implementation/device-connection-physical-verification.json`

This task touches the physical device and launches actual model workers; run it only with the operator present and explicit approval in chat.

- [ ] **Step 1: Back up the current flash** to `~/Research/esp32s3-backup/flash-80456b2d2f6c-banner_node.bin` with `esptool -b 921600 --before default-reset --after hard-reset read-flash 0 ALL`, record its SHA-256.
- [ ] **Step 2: Bring the main checkout to this branch's code** (the service runs the main checkout's editable install), restart the service, regenerate the Goose recipe with `setup --host goose`.
- [ ] **Step 3: Goose run.** Start from the recipe with: "Plug-and-run trial. Device binding `{"vendor_id": 4292, "product_id": 60000, "serial_number": "4c1afd94e7bef011912abb7948e9de0f"}`. Effects: read, reset. Acquire the device." Expect: listen records `unsolicited_output: true`; ESP acquisition succeeds under `reset`; the board returns to `banner_node` (display resumes); the run blocks at interpret preparation with `interpret-input.elf` as input.
- [ ] **Step 4: Codex run.** Repeat Step 3 from Codex with the same instruction.
- [ ] **Step 5: Record** runtime, model, run IDs, worker report statuses, accepted handoff hashes and the operator's display observation in the verification JSON, labelled `physical` and `actual-model`, and update the findings note (items 6–9 and 14 resolved or not).
- [ ] **Step 6: Commit**

```bash
git add docs/implementation/new-device-trial-findings.md docs/implementation/device-connection-physical-verification.json
git commit -m "Record physical device-connection verification"
```
