"""The outcome reading through its one entry, `read`: rows are results as the tools, the runtime and the configurator
write them."""
import unittest

from generative_driver.dispatch_outcome import read

# A tool the list of trusted tools does not name: its results are read, never trusted.
OTHER = 'firmware_index'


def transcript(*steps):
    """Transcript entries as the runtime's engine writes them, one per (operation, op) step."""
    return [{'operation': operation, 'step': index, 'op': op, 'tx_hex': '', 'rx_hex': '', 'status': 'completed'}
            for index, (operation, op) in enumerate(steps)]


def interface_result(ok=True, operation='set_level', steps=(('identify', 'exchange'), ('set_level', 'exchange')),
                     opened=True, fault=None, code=None, identity=True, **extra):
    """One interface-result/1 the engine writes. A failed one carries the engine's transport_open_attempted."""
    record = {'schema': 'interface-result/1', 'operation': operation, 'ok': ok, 'outputs': {}, 'units': {},
              'identity_verified': identity, 'transcript': transcript(*steps), **extra}
    if not ok:
        record['transport_open_attempted'] = opened
        record['error'] = {'code': code or 'execution_error', 'message': 'failed', 'fault': fault or 'model'}
    return record


def driver_result(ok=True, dispatched=True, opened=True, fault=None, code=None, **extra):
    """One driver-result/1 the package runtime writes for a generated tool or a device session."""
    record = {'schema': 'driver-result/1', 'operation': 'set_level', 'ok': ok, 'outputs': {}, 'units': {},
              'identity_verified': True, 'transcript': [], 'observations': [], 'warnings': [],
              'requested_command_dispatched': dispatched, **extra}
    if not ok:
        record['transport_open_attempted'] = opened
        record['error'] = {'code': code or 'execution_error', 'message': 'failed', 'fault': fault or 'model'}
    return record


def before_contact(error='ValueError: model_dir holds no model.json'):
    """A toolkit refusal before the tool contacted the device (`_toolchain.interface.before_contact`)."""
    return {'ok': False, 'error': error, 'transport_open_attempted': False, '_exit': 1}


def host_before_contact(error='PermissionError: copy locked'):
    """A host failure before the tool contacted the device (`_toolchain.interface.host_before_contact`)."""
    return {'ok': False, 'fault': 'host', 'error': error, 'transport_open_attempted': False, '_exit': 4}


def probe(*records, fault=None, code=None, evidence_kind='live'):
    """A probe_run result over its records."""
    ok = all(record['ok'] for record in records)
    return {'ok': ok, 'probe': '/runs/r/probe/interface-1.json', 'evidence_kind': evidence_kind,
            'results': list(records), 'error_code': code, 'error_message': None if ok else 'failed',
            'fault': fault, '_exit': 0 if ok else 1 if fault == 'model' else 4}


def executed(record, **extra):
    """An interface_execute result: the engine's record with the toolkit's fields."""
    return {**record, 'evidence_kind': 'live', 'timing_policy': 'real', 'model_sha256': 'a' * 64,
            'transactions': [], '_exit': 0 if record['ok'] else 1, **extra}


class OutcomeReadingTest(unittest.TestCase):

    def test_01_B1_the_success_test(self):
        rows = (
            ('ok true', {'ok': True}, True),
            ('ok false', {'ok': False}, False),
            ('ok truthy', {'ok': 1}, True),
            ('ok falsy', {'ok': 0}, False),
            ('exit 0 and ok', {'ok': True, '_exit': 0}, True),
            ('exit 1 and ok', {'ok': True, '_exit': 1}, False),
            ('exit 4', {'ok': False, '_exit': 4}, False),
            ('available without ok', {'available': True}, True),
            ('unavailable without ok', {'available': False}, False),
            ('ok wins over available', {'ok': True, 'available': False}, True),
            ('neither ok nor available', {}, True),
            ('neither, exit 1', {'_exit': 1}, False),
            ('no object', None, False),
            ('a list', [{'ok': True}], False),
            ('a text', 'ok', False),
        )
        for label, result, succeeded in rows:
            with self.subTest(label):
                self.assertIs(read('interface_execute', result).succeeded, succeeded)
                self.assertIs(read(OTHER, result).succeeded, succeeded)
                self.assertIs(read('package_tool', result, generated=True).succeeded, succeeded)

    def test_01_B2_what_each_execution_of_a_trusted_result_sent(self):
        delays = (('identify', 'exchange'), ('set_level', 'delay'), ('set_level', 'delay'))
        unavailable = {'schema': 'interface-result/1', 'ok': False, 'transport_open_attempted': False, 'outputs': {},
                       'units': {}, 'transcript': [], 'error': {'fault': 'operator', 'code': 'device_unavailable',
                       'message': 'The device provider is unavailable; nothing was dispatched: gone'}}
        no_transcript = interface_result(ok=False)
        del no_transcript['transcript']
        no_operation = interface_result(ok=False)
        del no_operation['operation']
        undeclared = driver_result(ok=False)
        del undeclared['requested_command_dispatched']
        rows = (
            # (label, name, result, keywords, trusted, executions as (ok, opened, sent))
            ('a write that succeeded', 'interface_execute', executed(interface_result()), {}, True,
             ((True, True, True),)),
            ('failed after the requested step', 'interface_execute', executed(interface_result(ok=False)), {}, True,
             ((False, True, True),)),
            ('identity failed', 'interface_execute',
             executed(interface_result(ok=False, steps=(('identify', 'exchange'),), identity=False)), {}, True,
             ((False, True, False),)),
            ('the open failed', 'interface_execute',
             executed(interface_result(ok=False, steps=(), identity=False, code='open', fault='host')), {}, True,
             ((False, True, False),)),
            ('the requested operation only waited', 'interface_execute',
             executed(interface_result(ok=False, steps=delays)), {}, True, ((False, True, False),)),
            ('preflight refused before the open', 'interface_execute',
             executed(interface_result(ok=False, steps=(), opened=False, identity=False, fault='operator',
                                       code='effect')), {}, True, ((False, False, False),)),
            ('no transcript list counts as sent', 'interface_execute', no_transcript, {}, True,
             ((False, True, True),)),
            ('no operation name counts as sent', 'interface_execute', no_operation, {}, True,
             ((False, True, True),)),
            ('every probe sample', 'probe_run',
             probe(interface_result(), interface_result(ok=False, steps=(('identify', 'exchange'),)), fault='model'),
             {}, True, ((True, True, True), (False, True, False))),
            ('a probe list with an item that is no record', 'probe_run',
             probe(interface_result(), {'ok': True}), {}, True, ()),
            ('an empty probe list', 'probe_run', {'ok': True, 'results': []}, {}, True, ()),
            ('a toolkit refusal before contact', 'probe_run', before_contact(), {}, True, ()),
            ('a summary of emit_test_live holds no record', 'emit_test_live',
             {'ok': False, 'passed': 0, 'failed': 1, 'not_run': [], 'verdict': 'fail', 'manifest_sha256': 'b' * 64,
              'results': [{'operation': 'set_level', 'ok': False, 'returncode': 1, 'error_code': 'execution_error',
                           'error_message': 'failed', 'fault': 'model', 'transport_open_attempted': True}],
              '_exit': 1}, {}, True, ()),
            ('no object', 'interface_execute', None, {}, True, ()),
            ('a driver-result the configurator did not run', 'interface_execute', driver_result(ok=False), {}, True,
             ()),
            ('a generated call that sent its command', 'package_tool', driver_result(ok=False), {'generated': True},
             True, ((False, True, True),)),
            ('a generated call stopped by its guard', 'package_tool',
             driver_result(ok=False, dispatched=False, code='precondition_failed', fault='operator'),
             {'generated': True}, True, ((False, True, False),)),
            ('a generated call that does not say', 'package_tool', undeclared, {'generated': True}, True,
             ((False, True, True),)),
            ('a generated call refused before the open', 'package_tool', driver_result(ok=False, dispatched=False,
             opened=False, fault='operator', code='validation_error'), {'generated': True}, True,
             ((False, False, False),)),
            ('a generated call that succeeded', 'package_tool', driver_result(), {'generated': True}, True,
             ((True, True, True),)),
            ('a generated call over an interface-result', 'package_tool', unavailable, {'generated': True}, True,
             ((False, False, False),)),
            ('another tool', OTHER, executed(interface_result(ok=False, opened=False, steps=())), {}, False, ()),
            ('another tool declaring a refusal', OTHER, before_contact(), {}, False, ()),
            ('another tool that succeeded', OTHER, executed(interface_result()), {}, False, ()),
            ('the provider failed before the call', OTHER, unavailable, {'undispatched': True}, True,
             ((False, False, False),)),
            ('the provider failed before a probe', 'probe_run',
             {'ok': False, 'results': [unavailable], 'error': unavailable['error']}, {'undispatched': True}, True,
             ((False, False, False),)),
        )
        for label, name, result, keywords, trusted, executions in rows:
            with self.subTest(label):
                reading = read(name, result, **keywords)
                self.assertIs(reading.trusted, trusted)
                self.assertEqual(tuple(tuple(execution) for execution in reading.executions), executions)
                for execution in reading.executions:
                    self.assertEqual((execution.ok, execution.opened, execution.sent), tuple(execution))

    def test_01_B2_no_field_of_a_result_makes_it_trusted(self):
        claims = executed(interface_result(ok=False, opened=False, steps=()), undispatched=True, generated=True,
                          trusted=True, mcp={'schema': 'driver-mcp-call/1'})
        reading = read(OTHER, claims)
        self.assertIs(reading.trusted, False)
        self.assertEqual(reading.executions, ())

    def test_01_B2_the_reading_is_immutable(self):
        reading = read('interface_execute', executed(interface_result()))
        with self.assertRaises(AttributeError):
            reading.trusted = False
        with self.assertRaises(AttributeError):
            reading.executions[0].sent = False

    def test_01_B3_how_the_call_ended_for_the_verdict(self):
        refused = interface_result(ok=False, steps=(), opened=False, identity=False, fault='operator', code='effect')
        summary = lambda **item: {'operation': 'set_level', 'returncode': 1, 'error_message': 'failed', **item}
        rows = (
            ('a call that succeeded', 'interface_execute', executed(interface_result()), ('succeeded', None)),
            ('the runtime refused before the open', 'interface_execute', executed(refused), ('refused', None)),
            ('the toolkit refused before contact', 'interface_execute', before_contact(), ('refused', None)),
            ('a host fault before contact', 'emit_test_live', host_before_contact(),
             ('inconclusive', 'host fault (PermissionError)')),
            ('the model failed on the device', 'interface_execute', executed(interface_result(ok=False)),
             ('failed', None)),
            ('a timeout after identity is the model', 'interface_execute',
             executed({**interface_result(ok=False), 'error': {'code': 'timeout', 'message': 'no reply'}}),
             ('failed', None)),
            ('a timeout before identity is the host', 'interface_execute',
             executed({**interface_result(ok=False, identity=False), 'error': {'code': 'timeout', 'message': 'x'}}),
             ('inconclusive', 'host fault (timeout)')),
            ('the open failed', 'interface_execute',
             executed(interface_result(ok=False, steps=(), identity=False, code='open', fault='host')),
             ('inconclusive', 'host fault (open)')),
            ('a probe of passes and refusals', 'probe_run', probe(interface_result(), refused), ('succeeded', None)),
            ('a probe whose refusal the tool calls a fault', 'probe_run', probe(interface_result(), refused,
             fault='operator', code='effect'), ('inconclusive', 'operator fault (effect)')),
            ('a probe refused throughout', 'probe_run', probe(refused, refused, fault='operator', code='effect'),
             ('refused', None)),
            ('a probe the model failed', 'probe_run', probe(interface_result(), interface_result(ok=False),
             fault='model', code='execution_error'), ('failed', None)),
            ('the provider failed before a probe', 'probe_run',
             {'ok': False, 'results': [{**refused, 'error': {'fault': 'operator', 'code': 'device_unavailable',
                                                             'message': 'gone'}}]}, ('refused', None)),
            ('a generated call the model failed', 'package_tool', driver_result(ok=False), ('failed', None)),
            ('a guard that refused a generated call', 'package_tool',
             driver_result(ok=False, dispatched=False, code='precondition_failed', fault='operator'),
             ('inconclusive', 'operator fault (precondition_failed)')),
            ('emit_test_live the model failed', 'emit_test_live',
             {'ok': False, 'verdict': 'fail', 'results': [summary(ok=False, error_code='execution_error',
              fault='model', transport_open_attempted=True)], '_exit': 1}, ('failed', None)),
            ('emit_test_live stopped for the operator', 'emit_test_live',
             {'ok': False, 'verdict': 'inconclusive', 'results': [summary(ok=False, error_code='open',
              fault='operator', returncode=4)], '_exit': 4}, ('inconclusive', 'operator fault (open)')),
            ('emit_test_live without its live record', 'emit_test_live',
             {'ok': False, 'verdict': 'inconclusive', 'fault': 'host', 'results': [summary(ok=True, error_code=None,
              fault=None, returncode=0)], 'error': 'live record not written: OSError: disk full', '_exit': 4},
             ('inconclusive', 'host fault')),
            ('an undeclared toolchain error', 'interface_execute',
             {'ok': False, 'error': 'AttributeError: list has no attribute get', '_exit': 1},
             ('inconclusive', 'undeclared toolchain error (AttributeError)')),
            ('a toolchain error naming a host failure', 'interface_execute',
             {'ok': False, 'error': 'OSError: [Errno 28] No space left on device', '_exit': 1},
             ('inconclusive', 'host fault (OSError)')),
            ('exit 4 with no fault', 'probe_run',
             {'ok': False, 'error': 'TimeoutExpired: Command timed out after 260 seconds', '_exit': 4},
             ('inconclusive', 'unclassified fault (TimeoutExpired)')),
            ('no object', 'probe_run', None, ('inconclusive', 'undeclared toolchain error (toolchain error)')),
            ('an item whose error is a text', 'probe_run',
             {'ok': False, 'results': [{'ok': False, 'error': 'boom'}]}, ('inconclusive', 'unclassified fault')),
        )
        for label, name, result, expected in rows:
            with self.subTest(label):
                for keywords in ({}, {'generated': True}, {'undispatched': True}):
                    for tool in (name, OTHER):
                        reading = read(tool, result, **keywords)
                        self.assertEqual((reading.outcome, reading.detail), expected, (tool, keywords))

    def test_01_B3_refused_before_open(self):
        refused = interface_result(ok=False, steps=(), opened=False, identity=False, fault='operator', code='effect')
        guarded = driver_result(ok=False, dispatched=False, code='precondition_failed', fault='operator')
        rows = (
            ('the runtime refused before the open', 'interface_execute', executed(refused), {}, True),
            ('every probe sample refused', 'probe_run', probe(refused, refused, fault='operator'), {}, True),
            ('one probe sample opened', 'probe_run', probe(interface_result(), refused), {}, False),
            ('the toolkit refused before contact', 'probe_run', before_contact(), {}, True),
            ('emit_test_live refused before its first call', 'emit_test_live', before_contact(), {}, True),
            ('a host fault before contact', 'emit_test_live', host_before_contact(), {}, False),
            ('a driver-result the configurator did not run declares a stop', 'interface_execute',
             driver_result(ok=False, opened=False, fault='operator'), {}, True),
            ('a call that opened', 'interface_execute', executed(interface_result(ok=False)), {}, False),
            ('a call that succeeded', 'interface_execute', executed(interface_result()), {}, False),
            ('another tool refused before the open', OTHER, executed(refused), {}, False),
            ('another tool declaring a refusal', OTHER, before_contact(), {}, False),
            ('the provider failed before the call', OTHER, refused, {'undispatched': True}, True),
            ('a generated call refused before the open', 'package_tool',
             driver_result(ok=False, dispatched=False, opened=False, fault='operator'), {'generated': True}, True),
            ('a generated call stopped by its guard', 'package_tool', guarded, {'generated': True}, False),
            ('no object', 'interface_execute', None, {}, False),
        )
        for label, name, result, keywords, expected in rows:
            with self.subTest(label):
                self.assertIs(read(name, result, **keywords).refused_before_open, expected)

    def test_01_B3_host_fault(self):
        rows = (('OSError: [Errno 28] No space left on device', True), ('PermissionError: denied', True),
                ('FileNotFoundError: no temporary directory', True), ('ModuleNotFoundError: no module', True),
                ('MemoryError: ', True), ('TimeoutExpired: Command timed out after 260 seconds', True),
                ('subprocess.TimeoutExpired: late', True), ('[Errno 20] Not a directory', True),
                ('ValueError: duplicate JSON key: name', False), ('KeyError: files', False),
                ('AttributeError: list has no attribute get', False),
                ('RecursionError: maximum recursion depth exceeded', False),
                ('ZeroDivisionError: division by zero', False), ('ModelError: identity', False))
        for error, host in rows:
            with self.subTest(error):
                for keywords in ({}, {'generated': True}, {'undispatched': True}):
                    self.assertIs(read(OTHER, {'ok': False, 'error': error, '_exit': 1}, **keywords).host_fault, host)
        for label, result, host in (('the tool says so', {'ok': False, 'fault': 'host', '_exit': 4}, True),
                                    ('a declared host fault before contact', host_before_contact(), True),
                                    ('defects are no error', {'ok': False, 'defects': [{'where': 'p'}], '_exit': 1},
                                     False),
                                    ('an error object is no toolchain error',
                                     executed(interface_result(ok=False, code='open', fault='host')), False),
                                    ('no object', None, False)):
            with self.subTest(label):
                self.assertIs(read('emit_test_live', result).host_fault, host)

    def test_01_B3_answered(self):
        refused = interface_result(ok=False, steps=(), opened=False, identity=False, fault='operator', code='effect')
        rows = (
            ('a live probe that succeeded', 'probe_run', probe(interface_result()), True),
            ('a live probe the model failed after the open', 'probe_run',
             probe(interface_result(), interface_result(ok=False), fault='model'), True),
            ('a model failure with a sample that never opened', 'probe_run',
             probe(interface_result(ok=False), refused, fault='model'), False),
            ('a probe that failed for the host', 'probe_run',
             probe(interface_result(ok=False, code='open', fault='host'), fault='host'), False),
            ('a replayed probe', 'probe_run', probe(interface_result(), evidence_kind='replay'), False),
            ('a probe refused before contact', 'probe_run', before_contact(), False),
            ('another tool', 'interface_execute', executed(interface_result()), False),
            ('no object', 'probe_run', None, False),
        )
        for label, name, result, expected in rows:
            with self.subTest(label):
                for keywords in ({}, {'generated': True}, {'undispatched': True}):
                    self.assertIs(read(name, result, **keywords).answered, expected)

    def test_01_B4_the_faults_of_a_pending_effect(self):
        model = interface_result(ok=False)
        call = executed(model)
        written = driver_result(ok=False, transcript=transcript(('identify', 'exchange'), ('set_level', 'exchange')))
        unsaid = dict(written)
        del unsaid['requested_command_dispatched']
        rows = (
            # (label, result, generated, pending_faults, model_failures)
            ('the model failed after identity and the open', call, False, ('model',), (call,)),
            ('the host failed', executed(interface_result(ok=False, fault='host', code='io')), False, ('host',), ()),
            ('the operator stopped it', executed(interface_result(ok=False, fault='operator', code='closed')), False,
             ('operator',), ()),
            ('a model fault before identity', executed(interface_result(ok=False, identity=False)), False,
             ('unknown',), ()),
            ('a model fault with no transcript', executed(interface_result(ok=False, steps=())), False,
             ('unknown',), ()),
            ('a model fault that never opened', executed(interface_result(ok=False, opened=False)), False,
             ('unknown',), ()),
            ('a record that succeeded is no failure', executed(interface_result()), False, ('unknown',), ()),
            ('a probe with one sample the model failed', probe(interface_result(), model, fault='model'), False,
             ('model',), (model,)),
            ('a probe of host and model failures', probe(interface_result(ok=False, fault='host', code='io'), model,
             fault='model'), False, ('host', 'model'), (model,)),
            ('a generated call the model failed', written, True, ('model',), (written,)),
            ('a driver-result the configurator did not run', written, False, ('unknown', 'unknown'), ()),
            ('a generated call that does not say it sent', unsaid, True, ('unknown', 'unknown'), ()),
            ('a record whose ok is no boolean', executed({**model, 'ok': None}), False, ('unknown', 'unknown'), ()),
            ('no object', None, False, ('unknown', 'unknown'), ()),
            ('an empty results list', {'ok': False, 'results': []}, False, ('unknown',), ()),
            ('results that are no list', {'ok': False, 'results': None}, False, ('unknown',), ()),
            ('a summary of emit_test_live', {'ok': False, 'results': [{'operation': 'set_level', 'ok': False,
             'fault': 'model'}], '_exit': 1}, False, ('unknown', 'unknown'), ()),
        )
        for label, result, generated, faults, failures in rows:
            with self.subTest(label):
                for name, keywords in (('interface_execute', {}), (OTHER, {}), (OTHER, {'undispatched': True})):
                    reading = read(name, result, generated=generated, **keywords)
                    self.assertEqual(reading.pending_faults, faults, name)
                    self.assertEqual(reading.model_failures, failures, name)

    def test_01_B4_the_faults_another_failed_call_adds(self):
        refused = interface_result(ok=False, steps=(), opened=False, identity=False, fault='operator', code='effect')
        written = driver_result(ok=False)
        rows = (
            # (label, result, generated, other_faults)
            ('no object', None, False, ('unknown',)),
            ('an empty results list', {'ok': False, 'results': []}, False, ('unknown',)),
            ('results that are no list', {'ok': False, 'results': None}, False, ('unknown',)),
            ('every record refused before the open', probe(refused, refused, fault='operator'), False, ()),
            ('a host fault before contact', host_before_contact(), False, ()),
            ('a probe the host failed', probe(interface_result(ok=False, fault='host', code='io'), fault='host'),
             False, ('host', 'host')),
            ('a probe the model failed', probe(interface_result(), interface_result(ok=False), refused, fault='model'),
             False, ()),
            ('a call the model failed', executed(interface_result(ok=False)), False, ()),
            ('an operator stop declared on the result', {'ok': False, 'error': {'fault': 'operator'}, 'results': [
             interface_result(ok=False, fault='operator', code='closed')]}, False, ('operator', 'operator')),
            ('a fault the reading does not know', probe(interface_result(ok=False), fault='strange'), False,
             ('unknown',)),
            ('a record with no fault', probe({**interface_result(ok=False), 'error': {'code': 'x'}}, fault='model'),
             False, ('unknown',)),
            ('an item that is no object', {'ok': False, 'fault': 'model', 'results': ['lost']}, False, ('unknown',)),
            ('a generated call the model failed', written, True, ()),
            ('a driver-result the configurator did not run', written, False, ('unknown',)),
            ('a summary of emit_test_live', {'ok': False, 'results': [{'operation': 'set_level', 'ok': False,
             'fault': 'model', 'transport_open_attempted': True}], '_exit': 1}, False, ('unknown',)),
        )
        for label, result, generated, faults in rows:
            with self.subTest(label):
                for name, keywords in (('probe_run', {}), (OTHER, {}), (OTHER, {'undispatched': True})):
                    self.assertEqual(read(name, result, generated=generated, **keywords).other_faults, faults, name)

    def test_01_B7_each_execution_record_is_read_on_its_own(self):
        refused = interface_result(ok=False, steps=(), opened=False, identity=False, fault='operator', code='effect')
        rejected = interface_result(ok=False, code='expect')
        rejected['transcript'][-1]['device_rejected'] = True
        guard = {'precondition_id': 'armed', 'result': {**interface_result(operation='read_state'), 'warnings': []}}
        unsaid = driver_result(ok=False)
        del unsaid['requested_command_dispatched']
        rows = (
            # (label, name, result, keywords, leaves_uncertain)
            ('a record with ok true clears', 'interface_execute', executed(interface_result()), {}, False),
            ('a failed record whose transport was not opened clears', 'interface_execute', executed(refused), {},
             False),
            ('the identity read failed: clears', 'interface_execute',
             executed(interface_result(ok=False, steps=(('identify', 'exchange'),), identity=False)), {}, False),
            ('the open failed: clears', 'interface_execute',
             executed(interface_result(ok=False, steps=(), identity=False, code='open', fault='host')), {}, False),
            ('a guard read failed: clears', 'package_tool',
             driver_result(ok=False, dispatched=False, code='precondition_unavailable', fault='host'),
             {'generated': True}, False),
            ('a guard refused the call: clears', 'package_tool',
             driver_result(ok=False, dispatched=False, code='precondition_failed', fault='operator',
                           observations=[guard]), {'generated': True}, False),
            ('failed for the model after the step went out: stays', 'interface_execute',
             executed(interface_result(ok=False)), {}, True),
            ('failed for the host after the step went out: stays', 'interface_execute',
             executed(interface_result(ok=False, fault='host', code='io')), {}, True),
            ('failed for the operator after the step went out: stays', 'interface_execute',
             executed(interface_result(ok=False, fault='operator', code='closed')), {}, True),
            ('the model reads the reply as a rejection: stays', 'interface_execute', executed(rejected), {}, True),
            ('a generated call that sent its command and failed: stays', 'package_tool', driver_result(ok=False),
             {'generated': True}, True),
            ('a driver-result that does not say whether it sent: stays', 'package_tool', unsaid,
             {'generated': True}, True),
            ('records all ok while the call did not succeed: stays', 'interface_execute',
             executed(interface_result(), _exit=1), {}, True),
            ('a probe whose records all passed while it failed: stays', 'probe_run',
             {**probe(interface_result(), interface_result()), 'ok': False, '_exit': 4}, {}, True),
            ('a probe with one record that stays', 'probe_run',
             probe(interface_result(), refused, interface_result(ok=False), fault='model'), {}, True),
            ('a probe whose failed records were never sent clears', 'probe_run',
             probe(interface_result(), refused, interface_result(ok=False, steps=(('identify', 'exchange'),)),
                   fault='model'), {}, False),
            ('the provider failed before the call: clears', OTHER,
             {**refused, 'error': {'fault': 'operator', 'code': 'device_unavailable', 'message': 'gone'}},
             {'undispatched': True}, False),
        )
        for label, name, result, keywords, expected in rows:
            with self.subTest(label):
                self.assertIs(read(name, result, **keywords).leaves_uncertain, expected)

    def test_01_B8_a_result_without_execution_records_is_read_as_a_whole(self):
        summary = {'operation': 'set_level', 'returncode': 0, 'error_code': None, 'error_message': None,
                   'fault': None, 'ok': True, 'transport_open_attempted': True}
        passed = {'ok': True, 'passed': 1, 'failed': 0, 'not_run': [], 'verdict': 'pass', 'results': [summary],
                  'manifest_sha256': 'b' * 64, 'live_record': '/runs/r/emit/live-1.json', '_exit': 0}
        failed = {**passed, 'ok': False, 'passed': 0, 'failed': 1, 'verdict': 'fail', '_exit': 1,
                  'results': [{**summary, 'ok': False, 'returncode': 1, 'error_code': 'execution_error',
                               'fault': 'model', 'transport_open_attempted': False}]}
        rows = (
            # (label, name, result, leaves_uncertain)
            ('a trusted call that succeeded clears', 'emit_test_live', passed, False),
            ('a trusted stop before the first device call clears', 'emit_test_live', before_contact(), False),
            ('a trusted stop before the first device call with a host fault clears', 'emit_test_live',
             host_before_contact(), False),
            ('a trusted summary of a failure stays', 'emit_test_live', failed, True),
            ('a trusted toolchain error stays', 'interface_execute',
             {'ok': False, 'error': 'OSError: disk full', '_exit': 1}, True),
            ('a trusted result that is no object stays', 'probe_run', None, True),
            ('a driver-result the configurator did not run stays', 'interface_execute',
             driver_result(ok=False, dispatched=False), True),
            ('another tool that succeeded clears', OTHER, executed(interface_result()), False),
            ('another tool declaring a stop before contact stays', OTHER, before_contact(), True),
            ('another tool with a record that never opened stays', OTHER,
             executed(interface_result(ok=False, steps=(), opened=False, fault='operator', code='effect')), True),
            ('another tool with a host fault before contact stays', OTHER, host_before_contact(), True),
        )
        for label, name, result, expected in rows:
            with self.subTest(label):
                self.assertIs(read(name, result).leaves_uncertain, expected)


class CallerTest(unittest.TestCase):

    def test_01_B1_replay_and_the_scripted_worker_ask_the_reading_and_hold_no_copy(self):
        import inspect
        from generative_driver import replay, scripted_worker
        for module in (replay, scripted_worker):
            with self.subTest(module.__name__):
                source = inspect.getsource(module)
                self.assertFalse("get('available'" in source or "get('_exit'" in source, 'holds a copy of the test')
                self.assertTrue('dispatch_outcome' in source and '.succeeded' in source, 'does not ask the reading')


if __name__ == '__main__':
    unittest.main()
