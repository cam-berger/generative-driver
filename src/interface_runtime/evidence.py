"""Bounded wire recording and strict offline replay through the real engine."""
import copy
import hashlib
import json

from .engine import execute, validate_model, _tx_bound
from .transports import TransportError, open_transport

MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


def read_json(path, limit=MAX_EVIDENCE_BYTES):
    return read_json_hashed(path, limit)[0]


def read_json_hashed(path, limit=MAX_EVIDENCE_BYTES):
    """Parse and hash the same bounded bytes; never hash a second file read."""
    with open(path, 'rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('JSON artifact exceeds size bound')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise ValueError('duplicate JSON key: ' + key)
            obj[key] = value
        return obj
    def constant(value):
        raise ValueError('non-finite JSON constant: ' + value)
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant), hashlib.sha256(raw).hexdigest()


def _error(result, code, message):
    result.update(ok=False, outputs={}, units={}, error={'code':code,'message':message})
    return result


class ReplayFactory:
    """One ordered trace shared by successive short-lived transport handles."""
    def __init__(self, fixture):
        if type(fixture) is not dict or set(fixture) != {'schema','transactions'} or fixture['schema'] != 'interface-replay/1':
            raise ValueError('expected interface-replay/1 with transactions')
        txs = fixture['transactions']
        if type(txs) is not list or len(txs) > 12800:
            raise ValueError('replay transaction count exceeds bound')
        if len(json.dumps(fixture, allow_nan=False).encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError('replay exceeds byte bound')
        for entry in txs:
            if (type(entry) is not dict or not {'tx_hex','rx_hex','rx','timeout_ms'} <= entry.keys()
                    or set(entry) - {'tx_hex','rx_hex','rx','timeout_ms','setup'}):
                raise ValueError('invalid replay transaction fields')
            for key in ('tx_hex','rx_hex'):
                value = entry[key]
                if (type(value) is not str or len(value) > 131072 or len(value) % 2
                        or any(c not in '0123456789abcdef' for c in value)):
                    raise ValueError('replay requires bounded canonical lowercase hex')
        self.entries = copy.deepcopy(txs)
        self.position = 0

    def __call__(self, channel, binding):
        parent = self
        class Handle:
            closed = False
            def exchange(self, tx, rx, timeout_ms):
                return self._call(tx, rx, timeout_ms)
            def control(self, setup, tx, rx, timeout_ms):
                return self._call(tx, rx, timeout_ms, setup)
            def _call(self, tx, rx, timeout_ms, setup=None):
                if self.closed or parent.position >= len(parent.entries):
                    raise TransportError('replay_mismatch','missing transaction or closed replay')
                expected = parent.entries[parent.position]
                request = {'tx_hex':tx.hex(),'rx':rx,'timeout_ms':timeout_ms}
                if setup is not None:
                    request['setup'] = setup
                if request != {k:v for k,v in expected.items() if k != 'rx_hex'}:
                    raise TransportError('replay_mismatch',f'request differs at transaction {parent.position}')
                parent.position += 1
                return bytes.fromhex(expected['rx_hex'])
            def close(self):
                self.closed = True
        return Handle()

    def exhausted(self):
        return self.position == len(self.entries)


class RecordingFactory:
    def __init__(self, factory):
        self.factory = factory
        self.transactions = []
    def __call__(self, channel, binding):
        handle = self.factory(channel, binding)
        parent = self
        class Recorder:
            def exchange(self, tx, rx, timeout_ms):
                return self._call(tx, rx, timeout_ms)
            def control(self, setup, tx, rx, timeout_ms):
                return self._call(tx, rx, timeout_ms, setup)
            def _call(self, tx, rx, timeout_ms, setup=None):
                entry = {'tx_hex':tx.hex(),'rx':copy.deepcopy(rx),'timeout_ms':timeout_ms}
                if setup is not None:
                    entry['setup'] = copy.deepcopy(setup)
                # Only completed responses are replayable. Engine transcript retains failed attempts.
                data = (handle.exchange(tx, rx, timeout_ms) if setup is None else
                        handle.control(setup, tx, rx, timeout_ms))
                if type(data) is not bytes:
                    raise TransportError('invalid_response','transport must return bytes')
                entry['rx_hex'] = data.hex()
                parent.transactions.append(entry)
                return data
            def close(self):
                handle.close()
            # Bytes the adapter drained on open reach the engine, which reports them as transport_notes.
            drained_bytes = property(lambda self: getattr(handle, 'drained_bytes', 0))
        return Recorder()


def _evidence_bound(model, operation, parameters, allow_effects, n):
    """Conservative serialized-byte bound for a validated model, without allocating decoded outputs.

    Include both wire copies (transcript and replay), repeated captures decoded into many outputs,
    JSON escaping, units, policy fields, indentation and the outer probe envelope. Identity outputs
    count even when discarded after verification, bounding that intermediate allocation as well.
    """
    operations = model['operations']
    if type(operation) is not str or operation not in operations:
        raise ValueError('unknown operation')
    names = [model['identity']['operation']]
    if operation not in names:
        names.append(operation)
    per_sample = 1024
    for name in names:
        op = operations[name]
        captures = {}
        for step in op['steps']:
            if step['op'] == 'delay':
                per_sample += 512
                continue
            rx = step['rx']; received = rx['length'] if rx['mode'] == 'exact' else rx.get('max_bytes',0)
            transmitted = _tx_bound(step['tx'],op['parameters'],'evidence.tx',model['schema'] == 'interface-model/5')
            per_sample += 4 * (transmitted + received) + len(json.dumps(rx,indent=2)) + 1024
            if 'capture' in step:
                captures[step['capture']] = received
        for output, decoder in op['outputs'].items():
            # Six ASCII JSON characters cover a control byte. Multi-byte UTF-8 has a smaller
            # escape expansion per input byte. Any finite numeric JSON value fits in 330 bytes.
            expansion = {'hex':2,'utf8':6,'kv_text':6,'line_text':6,'cstring':6,'lpstring':6}.get(decoder['kind'])
            per_sample += (expansion * captures[decoder['from']] if expansion else 330) + 256
            if 'unit' in decoder:
                per_sample += len(json.dumps(decoder['unit'])) + 256
    outer = len(json.dumps({'parameters':parameters,'allow_effects':list(allow_effects)},allow_nan=False,indent=2))
    return outer + 2048 + n * per_sample


def run_samples(model, operation, parameters=None, *, n=1, binding=None, allow_effects=(), replay=None):
    if type(n) is not int or not 1 <= n <= 100:
        raise ValueError('sample count must be integer 1..100')
    validation = validate_model(model)
    if not validation['ok']:
        raise ValueError('invalid model: ' + str(validation['defects']))
    if _evidence_bound(model,operation,parameters,allow_effects,n) > MAX_EVIDENCE_BYTES:
        raise ValueError('requested probe exceeds evidence bound; reduce samples, steps or output expansion')
    player = ReplayFactory(replay) if replay is not None else None
    recorder = RecordingFactory(player if player is not None else open_transport)
    results = []
    for _ in range(n):
        result = execute(model, operation, parameters, binding=binding,
                         allow_effects=allow_effects, transport_factory=recorder,
                         delay_fn=(lambda seconds: None) if player is not None else None)
        results.append(result)
        if not result['ok']:
            break
    if player is not None and all(r['ok'] for r in results) and not player.exhausted():
        _error(results[-1], 'replay_mismatch','unused extra replay transactions')
    return {'ok':all(r['ok'] for r in results), 'evidence_kind':'replay' if player else 'live',
            'timing_policy':'skip_delays' if player else 'real',
            'results':results,'transactions':recorder.transactions}


def _without_notes(results):
    """Results as replay can reproduce them: transport_notes describe the recorded transport (bytes it drained on
    open), which a replay has no counterpart for, so they are checked for shape and set aside."""
    out = []
    for result in results:
        if type(result) is dict and 'transport_notes' in result:
            notes = result['transport_notes']
            if (type(notes) is not dict or set(notes) != {'drained_bytes'} or type(notes['drained_bytes']) is not int
                    or notes['drained_bytes'] <= 0):
                raise ValueError('malformed transport_notes')
            result = {k: v for k, v in result.items() if k != 'transport_notes'}
        out.append(result)
    return out


def check_probe(model, probe, model_sha256):
    """Replay is a consistency check, never evidence of a new physical observation."""
    defects = []
    replayed = None
    try:
        required = {'schema','model_sha256','evidence_kind','timing_policy','operation','parameters','allow_effects',
                    'results','transactions','ok'}
        if type(probe) is not dict or set(probe) != required or probe['schema'] != 'interface-probe/1':
            raise ValueError('invalid probe envelope')
        if probe['model_sha256'] != model_sha256:
            raise ValueError('probe belongs to different model bytes')
        if probe['evidence_kind'] not in ('live','replay') or probe['ok'] is not True:
            raise ValueError('probe has no successful execution evidence')
        if probe['timing_policy'] != ('real' if probe['evidence_kind'] == 'live' else 'skip_delays'):
            raise ValueError('probe timing policy contradicts evidence kind')
        if type(probe['results']) is not list or not probe['results']:
            raise ValueError('probe has no results')
        replayed = run_samples(model, probe['operation'], probe['parameters'], n=len(probe['results']),
            allow_effects=probe['allow_effects'], replay={'schema':'interface-replay/1','transactions':probe['transactions']})
        if not replayed['ok'] or replayed['results'] != _without_notes(probe['results']):
            raise ValueError('replayed outputs/transcripts differ from recorded probe')
    except (ValueError, TypeError, KeyError) as exc:
        defects.append({'where':'probe','what':str(exc)})
    return {'ok':not defects, 'defects':defects, 'evidence_kind':'replay','timing_policy':'skip_delays',
            'source_evidence_kind':probe.get('evidence_kind') if type(probe) is dict else None,
            'results':replayed['results'] if replayed else []}


COVERAGE_SCOPE = ('Coverage refers only to the samples in the bundled probe, with the listed parameters. '
                  'Untested means no bundled execution evidence, not absence of testing elsewhere. '
                  'Live means recorded device execution; replay means supplied transactions, not a new device observation. '
                  'Each call opens and closes a separate connection. Sequence safety and physical semantics are not established.')


def probe_coverage(model, probe):
    """Per-operation coverage of one probe; identity checks count only when attempted in a sample.

    This is an evidence inventory, not a model correctness or safety verdict. Failed samples remain
    failed observations, including host/operator failures after a transaction started.
    """
    identity, selected = model['identity']['operation'], probe['operation']
    coverage = {}
    for name, operation in model['operations'].items():
        passed, failed = [], []
        for index, result in enumerate(probe['results'], 1):
            attempted = any(step.get('operation') == name for step in result.get('transcript', []))
            if not attempted:
                continue
            success = result.get('identity_verified') is True if name == identity else (
                name == selected and result.get('ok') is True)
            (passed if success else failed).append(index)
        attempted = len(passed) + len(failed)
        coverage[name] = {'effect':operation['effect'],
                          'status':'failed' if failed else 'tested' if passed else 'untested',
                          'evidence_kind':probe['evidence_kind'] if attempted else None,
                          'parameters':({} if name == identity else probe['parameters']) if attempted else None,
                          'samples':{'attempted':attempted,'passed':len(passed),'failed':len(failed)},
                          'sample_indices':{'passed':passed,'failed':failed}}
    return coverage
