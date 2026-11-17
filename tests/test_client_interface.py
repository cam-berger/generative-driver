"""The client interface: what a client that brings its own device and mission can ask of the tool.

Feature 03 of the untangle Run (docs/features/untangle/03-client-interface.md), 03-B1 to 03-B5. The interface module
is tested by what it offers and returns, the service through `generative_driver.client.call`, and the document by
its text. This module imports nothing from the benchmark package and no test file the deletion takes away, so it
also runs where the benchmark is gone and the document has lost its marked passage. The values it pins are those of
version 4 (docs/features/untangle/08-reports.md).

Temporary homes only; no model and no hardware.
"""
import hashlib
import inspect
import sys
import tempfile
import time
import unittest
from pathlib import Path

from generative_driver import client, client_interface

FIELDS = ('basis', 'checks', 'diff', 'equivalent', 'evidence_sha256', 'frozen_artifact_sha256', 'mission', 'passed',
          'protocol', 'schema', 'status', 'total')
FIELDS_MESSAGE = ('an equivalence/2 record has exactly the fields basis, checks, diff, equivalent, evidence_sha256, '
                  'frozen_artifact_sha256, mission, passed, protocol, schema, status, total')
# A well-formed verdict record: the one check passed, so the driver is graded equivalent.
GOOD = {'schema': 'equivalence/2', 'status': 'graded', 'equivalent': True, 'basis': 'evaluator',
        'checks': [{'id': 'c1', 'passed': True}], 'diff': [], 'passed': 1, 'total': 1, 'mission': None,
        'frozen_artifact_sha256': 'a' * 64, 'evidence_sha256': 'b' * 64, 'protocol': 'assistive/1'}


def offered(module):
    """The names `module` defines without a leading underscore, leaving out the modules it imported and the names it
    took from the standard library."""
    def standard(value):
        return (getattr(value, '__module__', None) or '').split('.')[0] in sys.stdlib_module_names
    return sorted(name for name, value in vars(module).items()
                  if not name.startswith('_') and not inspect.ismodule(value) and not standard(value))


class InterfaceModuleTests(unittest.TestCase):
    def test_03_B1_the_interface_module_offers_ten_names_and_nothing_else(self):
        # Version 2 added `firmware_errors` and `mission_errors` to the seven names of version 1 (06-B14), and
        # version 4 added `IMPORTS` (08-B9 of docs/features/untangle/08-reports.md).
        names = ['CALLS', 'IMPORTS', 'PROTOCOL', 'VERDICT_FIELDS', 'VERSION', 'device_errors', 'firmware_errors',
                 'frozen_driver', 'mission_errors', 'verdict_errors']
        self.assertEqual(sorted(client_interface.__all__), names)
        self.assertEqual(offered(client_interface), names)

    def test_03_B1_the_version_the_protocol_the_calls_and_the_verdict_fields_have_their_values(self):
        self.assertIs(type(client_interface.VERSION), int)
        # Version 3 added `cancel` and `seal` to the calls (07-B6 of docs/features/untangle/07-suites.md). Version 4
        # added no call (08-B9).
        self.assertEqual(client_interface.VERSION, 4)
        self.assertEqual(client_interface.PROTOCOL, 'assistive/1')
        self.assertEqual(client_interface.CALLS, ('ping', 'start', 'resume', 'status', 'events', 'result',
                                                  'attach_device', 'record_evaluation', 'cancel', 'seal'))
        self.assertIs(type(client_interface.VERDICT_FIELDS), frozenset)
        self.assertEqual(client_interface.VERDICT_FIELDS, frozenset(FIELDS))
        self.assertEqual(len(client_interface.VERDICT_FIELDS), 12)
        self.assertTrue(FIELDS_MESSAGE.endswith(' the fields ' + ', '.join(FIELDS)), 'FIELDS are those of the message')


class VerdictRecordTests(unittest.TestCase):
    def test_03_B2_a_well_formed_verdict_record_has_no_fault(self):
        self.assertEqual(client_interface.verdict_errors(GOOD), [])

    def test_03_B2_a_record_with_one_more_field_and_a_value_that_is_no_dict_give_the_fields_message_alone(self):
        for record in ({**GOOD, 'expected': 21.5}, 'equivalent', None, [GOOD]):
            with self.subTest(record=record):
                self.assertEqual(client_interface.verdict_errors(record), [FIELDS_MESSAGE])

    def test_03_B2_names_a_basis_that_is_not_the_evaluators(self):
        self.assertEqual(client_interface.verdict_errors({**GOOD, 'basis': 'self-evidence'}),
                         ['schema equivalence/2, basis evaluator and protocol assistive/1 are required'])

    def test_03_B2_names_an_equivalent_driver_whose_check_failed(self):
        failed = {**GOOD, 'checks': [{'id': 'c1', 'passed': False}], 'passed': 0}
        self.assertEqual(client_interface.verdict_errors(failed),
                         ['an equivalent driver passed every check, with no difference'])

    def test_03_B2_names_a_grade_without_its_evidence(self):
        self.assertEqual(client_interface.verdict_errors({**GOOD, 'evidence_sha256': None}),
                         ['a grade names the evidence it was sealed in'])

    def test_03_B2_an_unknown_grade_whose_difference_names_its_undecided_check_has_no_fault(self):
        unknown = {**GOOD, 'status': 'unknown', 'equivalent': None, 'checks': [{'id': 'c1', 'passed': None}],
                   'passed': 0, 'diff': [{'id': 'c1', 'reason': 'The device state could not be read'}]}
        self.assertEqual(client_interface.verdict_errors(unknown), [])

    def test_03_B2_an_unscored_record_with_no_check_no_evidence_and_a_named_reason_has_no_fault(self):
        unscored = {**GOOD, 'status': 'unscored', 'equivalent': None, 'checks': [], 'passed': 0, 'total': 0,
                    'evidence_sha256': None, 'diff': [{'id': 'evaluator', 'reason': 'The evaluator host failed'}]}
        self.assertEqual(client_interface.verdict_errors(unscored), [])


# A well-formed descriptor of a device a client supplies.
DEVICE = {'schema': 'device-descriptor/2', 'control': {'endpoint_file': 'control.json'},
          'session_id': 'session-1', 'binding': {'host': '127.0.0.1', 'port': 4000}, 'firmware_sha256': 'c' * 64}


class DeviceDescriptorTests(unittest.TestCase):
    def test_03_B3_a_well_formed_device_descriptor_has_no_fault(self):
        self.assertEqual(client_interface.device_errors(DEVICE), [])

    def test_03_B3_names_a_value_that_is_no_descriptor_of_a_supplied_device(self):
        for device in ('device', None, {**DEVICE, 'schema': 'device-descriptor/1'}):
            with self.subTest(device=device):
                self.assertEqual(client_interface.device_errors(device), ['not a device-descriptor/2'])
        # The descriptor names no kind of device any more: a part the tool does not know is a fault.
        self.assertEqual(client_interface.device_errors({**DEVICE, 'kind': 'bound-device'}),
                         ['a device-descriptor/2 has no part named kind'])

    def test_03_B3_names_each_part_a_descriptor_lacks(self):
        bare = {'schema': 'device-descriptor/2'}
        self.assertEqual(client_interface.device_errors(bare),
                         ['no control endpoint file', 'no session_id', 'no binding', 'no firmware_sha256'])

    def test_03_B3_the_service_applies_the_same_function(self):
        from generative_driver import device_client
        self.assertIs(client_interface.device_errors, device_client.descriptor_errors)


def handoff(stage, *artifacts):
    """An accepted handoff of `stage` with `artifacts`, each a pair of a path and the character its hash repeats."""
    return {'stage': stage, 'revision': 0, 'artifacts': [{'path': path, 'sha256': character * 64, 'kind': 'directory'}
                                                         for path, character in artifacts]}


class FrozenDriverTests(unittest.TestCase):
    def test_03_B4_no_handoff_and_an_emit_handoff_without_an_artifact_give_no_driver(self):
        self.assertIsNone(client_interface.frozen_driver([]))
        self.assertIsNone(client_interface.frozen_driver([handoff('emit')]))

    def test_03_B4_gives_the_first_artifact_of_the_later_of_two_emit_handoffs(self):
        first, later = handoff('emit', ('accepted/0/package', '1')), handoff('emit', ('accepted/1/package', '2'))
        self.assertEqual(client_interface.frozen_driver([first, later]),
                         {'path': 'accepted/1/package', 'sha256': '2' * 64})
        twice = handoff('emit', ('accepted/1/package', '2'), ('accepted/1/other', '3'))
        self.assertEqual(client_interface.frozen_driver([first, twice]),
                         {'path': 'accepted/1/package', 'sha256': '2' * 64})

    def test_03_B4_an_emit_handoff_without_an_artifact_leaves_the_earlier_driver(self):
        handoffs = [handoff('emit', ('accepted/0/package', '1')), handoff('emit')]
        self.assertEqual(client_interface.frozen_driver(handoffs), {'path': 'accepted/0/package', 'sha256': '1' * 64})

    def test_03_B4_a_probe_or_reuse_handoff_with_artifacts_changes_nothing(self):
        emit = handoff('emit', ('accepted/0/package', '1'))
        probe, reuse = handoff('probe', ('accepted/0/model', '4')), handoff('reuse', ('accepted/0/record', '5'))
        for handoffs in ([probe, emit], [emit, reuse], [probe, emit, reuse], [reuse, probe, emit, probe, reuse]):
            with self.subTest(stages=[each['stage'] for each in handoffs]):
                self.assertEqual(client_interface.frozen_driver(handoffs),
                                 {'path': 'accepted/0/package', 'sha256': '1' * 64})
        self.assertIsNone(client_interface.frozen_driver([probe, reuse]))


class ServiceCase(unittest.TestCase):
    """The service on a temporary home, reached as a client reaches it."""

    def service(self):
        """The `call` of a service on a new home. The service is shut down and its home removed when the test ends."""
        temporary = tempfile.TemporaryDirectory(prefix='client interface ', ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        home = Path(temporary.name).resolve() / 'home'
        self.addCleanup(self.shut_down, home)
        return lambda method, params=None: client.call(method, params or {}, home=home)

    def shut_down(self, home, seconds=60):
        """Shut the service at `home` down, asking again while a worker of it is still stopping."""
        until = time.monotonic() + seconds
        while not client.call('shutdown', {}, home=home).get('stopped'):
            self.assertLess(time.monotonic(), until, 'The service did not shut down')
            time.sleep(.1)

    def stopped(self, call, run, seconds=30):
        """The status of `run` once it has stopped and nothing of it is left stopping."""
        until = time.monotonic() + seconds
        while True:
            status = call('status', {'run_id': run})
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return status
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)

    def stopped_run(self, call, token):
        """The id of a new run without a supplied device, whose runtime cannot start, once it has stopped. `token` is the token
        that later proves who may post its verdict."""
        started = call('start', {'goal': 'Ask the service what a client may ask', 'budget_seconds': 120,
                                 'executor_config': {'command': ['no-such-agent-runtime']},
                                 'evaluation_token_sha256': hashlib.sha256(token.encode()).hexdigest()})
        self.assertTrue(started.get('ok'), started)
        self.stopped(call, started['run_id'])
        return started['run_id']


class ServiceCallTests(ServiceCase):
    def test_03_B1_the_service_knows_every_call_and_no_other_name(self):
        call = self.service()
        run = self.stopped_run(call, 'a token only this test holds')
        for name in client_interface.CALLS:
            with self.subTest(call=name):
                reply = call(name, {'run_id': run})
                self.assertIsInstance(reply, dict)
                self.assertFalse(str(reply.get('reason')).startswith('Unknown method'), reply)
        # `resume` started the run again; nothing more is asked of it until it has stopped.
        self.stopped(call, run)
        self.assertEqual(call('nonsense', {'run_id': run}),
                         {'ok': False, 'error': 'ValueError', 'reason': 'Unknown method: nonsense'})

    def test_03_B2_the_service_refuses_a_posted_verdict_by_the_same_rules(self):
        call = self.service()
        token = 'a token only this test holds'
        run = self.stopped_run(call, token)
        record = {**GOOD, 'expected': 21.5}
        self.assertEqual(client_interface.verdict_errors(record), [FIELDS_MESSAGE])
        self.assertEqual(call('record_evaluation', {'run_id': run, 'token': token, 'equivalence': record}),
                         {'ok': False, 'error': 'ValueError', 'reason': 'Invalid evaluation record: ' + FIELDS_MESSAGE})


DOCUMENT = Path(__file__).resolve().parents[1] / 'docs' / 'client-interface.md'
# The two lines that set a passage about the legacy benchmark off; the deletion takes the passage away with them.
BEGIN, END = '<!-- legacy-benchmark:begin -->', '<!-- legacy-benchmark:end -->'
# Version 3 added `Stop or seal a run` to the six sections of version 2 (07-B6), and version 4 `Records on disk`
# (08-B11).
HEADINGS = ['Start a run on a supplied device', 'The control endpoint', 'Replace a device', 'Post one final verdict',
            'Read the records and the frozen driver', 'Stop or seal a run', 'Records on disk', 'What a client imports']
DESCRIPTOR_PARTS = ('schema', 'control', 'endpoint_file', 'session_id', 'binding', 'firmware_sha256', 'instrument',
                    'development_conditions')
RECORD_NAMES = ('device-descriptor/2', 'equivalence/2', 'assistive/1')


class DocumentTests(unittest.TestCase):
    """The document by its text outside any marked passage, which is all of it where the benchmark is gone."""

    def lines(self):
        """The lines of the document outside any marked passage."""
        self.assertTrue(DOCUMENT.is_file(), 'docs/client-interface.md is missing')
        kept, marked = [], False
        for line in DOCUMENT.read_text(encoding='utf-8').splitlines():
            if line.strip() in (BEGIN, END):
                marked = line.strip() == BEGIN
            elif not marked:
                kept.append(line)
        return kept

    def test_03_B5_the_first_line_names_the_interface_and_its_version(self):
        first = self.lines()[0]
        self.assertEqual(first, '# Client interface, version 4')
        self.assertEqual(first, f'# Client interface, version {client_interface.VERSION}')

    def test_03_B5_the_second_level_headings_are_the_eight_in_their_order(self):
        self.assertEqual([line[3:] for line in self.lines() if line.startswith('## ')], HEADINGS)

    def test_03_B5_names_every_call_verdict_field_descriptor_part_and_record_name_in_backticks(self):
        text = '\n'.join(self.lines())
        names = (*client_interface.CALLS, *sorted(client_interface.VERDICT_FIELDS), *DESCRIPTOR_PARTS, *RECORD_NAMES)
        self.assertEqual([name for name in names if f'`{name}`' not in text], [])

    def test_03_B5_no_line_outside_a_marked_passage_says_bench(self):
        saying = [f'{number}: {line}' for number, line in enumerate(self.lines(), 1) if 'bench' in line.lower()]
        self.assertEqual(saying, [])


if __name__ == '__main__':
    unittest.main()
