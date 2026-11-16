"""Fixtures for the input paths (docs/features/kit-fixes/07-input-paths.md).

- In-process generic runs whose stage workers are scripted functions of the test: each stage's real preparation and
  check run, and a worker calls the real tools through the configurator's gateway. No model and no Codex run.
- A small PDF written here, a stand-in protocol registry and a datasheet fetched from a stand-in network answer.

Temporary homes only; no real hardware and no network."""
import contextlib
import hashlib
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TIMEOUT = 120


def pdf_bytes(pages):
    """A PDF with one page per text of `pages`, its lines in Helvetica, which pypdf extracts as the text."""
    objects = ['<< /Type /Catalog /Pages 2 0 R >>', None, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    kids = []
    for text in pages:
        ops = ['BT', '/F1 12 Tf', '14 TL', '72 720 Td']
        for line in text.split('\n'):
            ops.append('(' + line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') + ') Tj T*')
        stream = '\n'.join(ops + ['ET'])
        objects.append('<< /Length %d >>\nstream\n%s\nendstream' % (len(stream.encode('latin-1')), stream))
        objects.append('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> '
                       '/Contents %d 0 R >>' % len(objects))
        kids.append(len(objects))
    objects[1] = '<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join('%d 0 R' % k for k in kids), len(kids))
    out = io.BytesIO()
    out.write(b'%PDF-1.4\n')
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(('%d 0 obj\n%s\nendobj\n' % (number, body)).encode('latin-1'))
    table = out.tell()
    out.write(('xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)).encode())
    for offset in offsets:
        out.write(('%010d 00000 n \n' % offset).encode())
    out.write(('trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, table)).encode())
    return out.getvalue()


def have_pypdf():
    try:
        import pypdf  # noqa: F401
    except ImportError:
        return False
    return True


def sha256(data):
    return hashlib.sha256(data).hexdigest()


@contextlib.contextmanager
def network(answers):
    """The network as a stand-in: `urllib.request.urlopen(url)` answers `answers[url]` (bytes) and refuses any other."""
    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(url, timeout=None):
        if url not in answers:
            raise OSError('no stand-in answer for ' + str(url))
        return Reply(answers[url])
    with patch('urllib.request.urlopen', urlopen):
        yield


@contextlib.contextmanager
def protocol_registry(folder, protocols):
    """A stand-in protocol registry: `protocols` maps a key to its sources, each {name, data (bytes), split}. Each
    source is served from a cache file under `folder`, so nothing is fetched."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    entries = {}
    for key, sources in protocols.items():
        listed = []
        for source in sources:
            cache = folder / (key + '-' + source['name'])
            cache.write_bytes(source['data'])
            listed.append({'name': source['name'], 'url': 'https://specs.example/' + key + '/' + source['name'],
                           'sha256': sha256(source['data']), 'split': source.get('split'), 'cache': str(cache)})
        entries[key] = {'title': 'Stand-in protocol ' + key, 'publisher': 'Example', 'match': [key], 'sources': listed}
    registry = folder / 'protocols.json'
    registry.write_text(json.dumps({'schema': 'protocol-registry/1', 'protocols': entries}), encoding='utf-8')
    with patch('generative_driver._toolchain.acquire.PROTOCOL_REGISTRY', str(registry)):
        yield entries


def fake_serial(boot=b'', replies=None):
    """A stand-in for pyserial (the hardware boundary): every port it opens first sends `boot`, as a device that resets
    when opened does, then answers each request line with `replies[line]`. Patch it in as sys.modules['serial']."""
    import types

    class Serial:
        def __init__(self):
            self.port, self.baudrate, self.timeout, self.write_timeout = None, 9600, None, None
            self.dtr = self.rts = None
            self.pending, self.received = bytearray(), bytearray()

        def open(self):
            self.pending += boot

        def close(self):
            pass

        def reset_input_buffer(self):
            pass

        @property
        def in_waiting(self):
            return len(self.pending)

        def read(self, size=1):
            data = bytes(self.pending[:size])
            del self.pending[:size]
            if not data and self.timeout:
                time.sleep(min(self.timeout, 0.01))
            return data

        def write(self, data):
            self.received += data
            while b'\n' in self.received:
                line, _, rest = bytes(self.received).partition(b'\n')
                self.received = bytearray(rest)
                self.pending += (replies or {}).get(line, b'')
            return len(data)
    module = types.ModuleType('serial')
    module.Serial = Serial
    return module


def task_of(prompt):
    """The JSON task after the default prompt's first line, or None for a prompt without one."""
    try:
        return json.loads(prompt.split('\n', 1)[1])
    except (IndexError, ValueError):
        return None


def gateway_identity(request):
    args = request.gateway['args']
    return {'run_id': args[args.index('--run') + 1], 'assignment_id': args[args.index('--assignment') + 1]}


class Worker:
    """What a scripted stage worker gets: its launch request, its task (None for interpretation), its workspace and
    `tool(name, **arguments)`, which calls a tool through the configurator's gateway and returns the result, or the
    gateway's refusal as a ValueError."""

    def __init__(self, controller, request):
        self.request, self.stage = request, request.stage
        self.workspace = Path(request.workspace)
        self.task = task_of(request.prompt)
        self.identity = gateway_identity(request) if request.gateway else None
        self.controller = controller

    def tool(self, tool, /, **arguments):
        if self.identity is None:
            raise AssertionError('This worker has no gateway')
        try:
            return self.controller.call('tool', {**self.identity, 'name': tool, 'arguments': arguments})
        except ValueError as refusal:
            return refusal

    def tools(self):
        return [tool['name'] for tool in self.controller.call('tools', self.identity)['tools']]

    def write(self, name, value):
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, indent=1), encoding='utf-8')


class RunCase(unittest.TestCase):
    """In-process generic runs in a temporary folder. `script` maps a stage to `work(worker)`, which may return the
    worker's report; a stage without one does nothing and reports completed. Each launch is kept in `self.launched`
    as (request, the files of its workspace when it started)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.launched, self.worker_errors, self.homes = [], [], []

    def run_with(self, script, *, timeout=TIMEOUT, **params):
        """The result of a generic run of `params` (its goal given here) with the scripted workers, once it stopped.
        Each run has a home of its own."""
        from generative_driver.configurator import Controller
        controller = Controller(self.root / ('home-' + str(len(self.homes))))
        self.homes.append(controller.home)
        self.addCleanup(controller.close)
        self.controller = controller

        def execute(request, config, cancel):
            worker = Worker(controller, request)
            files = sorted(p.relative_to(worker.workspace).as_posix() for p in worker.workspace.rglob('*')
                           if p.is_file()) if worker.workspace.is_dir() else []
            self.launched.append((request, files))
            work = script.get(request.stage)
            try:
                report = work(worker) if work else None
            except Exception as error:  # The test fails on it below, not on the run it stopped.
                self.worker_errors.append((request.stage, error))
                raise
            return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0, 'usage': None,
                    'report': report or {'status': 'completed', 'summary': 'scripted ' + request.stage,
                                         'artifacts': [], 'checks': [], 'unresolved': []}}
        with patch('generative_driver.configurator.execute', execute):
            run = controller.call('start', {'goal': 'Recover the interface from the acquired evidence',
                                            'executor_config': {'command': ['never-launched']}, **params})
            self.run = run
            result = self.stopped(controller, run, timeout)
        if self.worker_errors:
            stage, error = self.worker_errors[0]
            raise AssertionError('The scripted ' + stage + ' worker raised ' + repr(error)) from error
        return result

    @staticmethod
    def stopped(controller, run, timeout=TIMEOUT):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            status = controller.call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return controller.call('result', run)
            time.sleep(.05)
        raise AssertionError('The run did not stop in time: ' + json.dumps(controller.call('status', run)))

    def events(self, *kinds):
        found, cursor = [], 0
        while True:
            page = self.controller.call('events', {**self.run, 'after': cursor})
            found += page['events']
            if not page['events']:
                break
            cursor = page['cursor']
        return [event for event in found if not kinds or event['kind'] in kinds]

    def assigned(self, stage):
        """The assignments of `stage`, in order."""
        return [e['data'] for e in self.events('stage.assigned') if e['data']['stage'] == stage]

    def launches(self, stage):
        return [(request, files) for request, files in self.launched if request.stage == stage]

    @staticmethod
    def handoffs(result, stage):
        return [h for h in result['accepted_handoffs'] if h['stage'] == stage]
