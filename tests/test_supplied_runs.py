"""A run on a supplied device: a client starts it with the device, the firmware and the mission as data, and the
service knows no case.

Feature 06 of the untangle Run (docs/features/untangle/06-benchmark-runs.md): the first two points of 06-B1, 06-B3
to 06-B5, 06-B6 without its last sentence, 06-B7 to 06-B14 and the tool's side of 06-B20 to 06-B22. This module
imports nothing from the benchmark package and names no registered case, so it also runs where the benchmark is
gone. The benchmark's side of the Feature is in the benchmark's tests.

Temporary homes only; no model, no hardware and no password. The device is the hand-written one of
`tests/supplied_device.py`.
"""
import copy
import hashlib
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver import client_interface, integrity
from input_path_fixtures import RunCase as ScriptedRunCase
from interpret_task_fixtures import INVENTORY, RunCase as TaskRunCase, cortex_m_image, seen, stopped
from supplied_device import SuppliedDevice
from test_supplied_stages import FIRMWARE_SHA256, MAPPING, MISSION, SAMPLED, hand_written_firmware

SHA = 'a' * 64
# A device descriptor without a fault. Its endpoint file need not exist for `device_errors`.
DESCRIPTOR = {'schema': 'device-descriptor/2', 'control': {'endpoint_file': '/nowhere/control.json'},
              'session_id': 'session-1', 'binding': {'host': '127.0.0.1', 'port': 4000}, 'firmware_sha256': SHA}
# A verdict record without a fault: an equivalent driver, graded.
VERDICT = {'schema': 'equivalence/2', 'status': 'graded', 'equivalent': True, 'basis': 'evaluator',
           'checks': [{'id': 'reads the level', 'passed': True}], 'diff': [], 'passed': 1, 'total': 1, 'mission': None,
           'frozen_artifact_sha256': SHA, 'evidence_sha256': 'b' * 64, 'protocol': 'assistive/1'}
TWELVE = ('schema', 'status', 'equivalent', 'basis', 'checks', 'diff', 'passed', 'total', 'mission',
          'frozen_artifact_sha256', 'evidence_sha256', 'protocol')


def without(value, *path):
    """A deep copy of `value` without the key at `path`."""
    changed = copy.deepcopy(value)
    holder = changed
    for key in path[:-1]:
        holder = holder[key]
    del holder[path[-1]]
    return changed


def having(value, *path_and_item):
    """A deep copy of `value` with the item `path_and_item[-1]` at the path before it."""
    *path, item = path_and_item
    changed = copy.deepcopy(value)
    holder = changed
    for key in path[:-1]:
        holder = holder[key]
    holder[path[-1]] = item
    return changed


class FirmwareRuleTests(unittest.TestCase):
    """The form of a supplied firmware (06-B5)."""

    def test_06_B5_a_firmware_with_exactly_an_absolute_path_and_a_sha256_has_no_fault(self):
        from pathlib import Path
        path = str(Path('nowhere/firmware.bin').resolve())
        self.assertEqual(client_interface.firmware_errors({'path': path, 'sha256': FIRMWARE_SHA256}), [])

    def test_06_B5_firmware_errors_gives_each_fault_as_a_sentence(self):
        from pathlib import Path
        path = str(Path('nowhere/firmware.bin').resolve())
        good = {'path': path, 'sha256': FIRMWARE_SHA256}
        exactly, absolute = 'a firmware has exactly path and sha256', 'path is an absolute path'
        hexadecimal = 'sha256 is 64 lowercase hex characters'
        for firmware, faults in ((None, [exactly]), ('firmware.bin', [exactly]), ({'path': path}, [exactly]),
                                 ({**good, 'size': 17}, [exactly]),
                                 ({**good, 'path': 'firmware.bin'}, [absolute]),
                                 ({**good, 'path': Path(path)}, [absolute]), ({**good, 'path': 7}, [absolute]),
                                 ({**good, 'sha256': FIRMWARE_SHA256.upper()}, [hexadecimal]),
                                 ({**good, 'sha256': FIRMWARE_SHA256[:63]}, [hexadecimal]),
                                 ({**good, 'sha256': None}, [hexadecimal]),
                                 ({'path': 'firmware.bin', 'sha256': 'no hash'}, [absolute, hexadecimal])):
            with self.subTest(firmware=firmware):
                self.assertEqual(client_interface.firmware_errors(firmware), faults)


class MissionRuleTests(unittest.TestCase):
    """The tool's own rules for a mission (06-B6)."""

    def faults(self, mission):
        return client_interface.mission_errors(mission)

    def test_06_B6_the_hand_written_mission_and_a_sampled_one_have_no_fault(self):
        self.assertEqual(self.faults(MISSION), [])
        self.assertEqual(self.faults(SAMPLED), [])
        self.assertEqual(self.faults(without(MISSION, 'operating_conditions')), [])

    def test_06_B6_a_mission_has_tasks_and_a_reuse_objective_and_may_have_operating_conditions(self):
        alone = ['a mission has tasks and reuse_objective, and may have operating_conditions']
        for mission in (None, [], 'mission', without(MISSION, 'tasks'), without(MISSION, 'reuse_objective'),
                        {**MISSION, 'case_id': 'named'}, {**MISSION, 'schema': 'mission/1'}):
            with self.subTest(mission=mission):
                self.assertEqual(self.faults(mission), alone)
        # A mission with another key gives this one fault, whatever else is wrong with it.
        self.assertEqual(self.faults({'tasks': {}, 'reuse_objective': '', 'hidden': 1}), alone)

    def test_06_B6_the_reuse_objective_is_text_that_is_not_blank(self):
        for objective in ('', '  \n', None, 7, ['Read the level.']):
            with self.subTest(objective=objective):
                self.assertEqual(self.faults({**MISSION, 'reuse_objective': objective}),
                                 ['reuse_objective is text that is not blank'])

    def test_06_B6_operating_conditions_are_one_to_sixteen_texts_of_at_most_a_thousand_characters(self):
        fault = ['operating_conditions are 1 to 16 texts that are not blank, of at most 1000 characters each']
        for conditions in ([], 'One at a time.', ['fine', ''], ['fine', ' '], ['fine', 7], ['x' * 1001],
                           ['condition'] * 17, None, {'first': 'fine'}):
            with self.subTest(conditions=conditions):
                self.assertEqual(self.faults({**MISSION, 'operating_conditions': conditions}), fault)
        for conditions in (['x' * 1000], ['condition'] * 16):
            with self.subTest(conditions=len(conditions)):
                self.assertEqual(self.faults({**MISSION, 'operating_conditions': conditions}), [])

    def test_06_B6_tasks_is_an_object_with_at_least_one_task(self):
        for tasks in ({}, [], None, 'read_level'):
            with self.subTest(tasks=tasks):
                self.assertEqual(self.faults({**MISSION, 'tasks': tasks}),
                                 ['tasks is an object with at least one task'])

    def test_06_B6_a_task_that_breaks_a_rule_gives_one_fault_with_its_name(self):
        task = MISSION['tasks']['read_level']
        sampled = SAMPLED['tasks']['measure']
        broken = {
            'a name that starts with a digit': ('9lives', task),
            'a name with a dash': ('read-level', task),
            'a name of 65 characters': ('a' * 65, task),
            'a task that is no object': ('read_level', 'Read the fill level.'),
            'no meaning': ('read_level', without(task, 'meaning')),
            'a blank meaning': ('read_level', {**task, 'meaning': ' '}),
            'no inputs': ('read_level', without(task, 'inputs')),
            'inputs that are a list': ('read_level', {**task, 'inputs': []}),
            'no outputs': ('read_level', without(task, 'outputs')),
            'another key': ('read_level', {**task, 'hint': 'L?'}),
            'another way to sample': ('read_level', {**task, 'sampling': 'poll'}),
            'an input that is no object': ('read_level', {**task, 'inputs': {'slot': 'integer'}}),
            'an input with another type': ('read_level', {**task, 'inputs': {'slot': {'type': 'float'}}}),
            'an input with no type': ('read_level', {**task, 'inputs': {'slot': {'minimum': 0}}}),
            'an output that is no object': ('read_level', {**task, 'outputs': {'level': 'percent'}}),
            'an output with no unit': ('read_level', {**task, 'outputs': {'level': {}}}),
            'an output with an empty unit': ('read_level', {**task, 'outputs': {'level': {'unit': ''}}}),
            'an instrument with no tolerance': ('measure', without(sampled, 'outputs', 'temperature',
                                                                    'absolute_tolerance')),
            'an empty instrument': ('measure', having(sampled, 'outputs', 'temperature', 'instrument', '')),
            'a tolerance below zero': ('measure', having(sampled, 'outputs', 'temperature', 'absolute_tolerance', -1)),
            'a tolerance that is not finite': ('measure', having(sampled, 'outputs', 'temperature',
                                                                  'absolute_tolerance', float('inf'))),
            'a tolerance that is true': ('measure', having(sampled, 'outputs', 'temperature',
                                                            'absolute_tolerance', True))}
        for what, (name, value) in broken.items():
            with self.subTest(task=what):
                self.assertEqual(self.faults({**MISSION, 'tasks': {'read_status': MISSION['tasks']['read_status'],
                                                                   name: value}}), ['invalid task: ' + name])
        both = self.faults({**MISSION, 'tasks': {'first': {}, 'second': {}}})
        self.assertEqual(both, ['invalid task: first', 'invalid task: second'])

    def test_06_B6_a_task_may_hold_inputs_of_the_four_types_a_null_unit_and_other_keys_in_inputs_and_outputs(self):
        task = {'meaning': 'Store a value.', 'sampling': 'retained',
                'inputs': {'slot': {'type': 'integer', 'minimum': 0, 'maximum': 7}, 'value': {'type': 'number'},
                           'label': {'type': 'string', 'enum': ['a', 'b']}, 'raw': {'type': 'bytes'}},
                'outputs': {'stored': {'unit': None, 'note': 'no unit'},
                            'level': {'unit': 'percent', 'instrument': 'gauge', 'absolute_tolerance': 0},
                            'drift': {'unit': 'percent', 'absolute_tolerance': 'unused without an instrument'}}}
        self.assertEqual(self.faults({'tasks': {'A' + 'b' * 63: task}, 'reuse_objective': 'Store one value.'}), [])


class DescriptorRuleTests(unittest.TestCase):
    """The device descriptor in the tool's terms (06-B9)."""

    def test_06_B9_a_device_descriptor_2_with_its_five_parts_has_no_fault(self):
        self.assertEqual(client_interface.device_errors(DESCRIPTOR), [])

    def test_06_B9_it_may_hold_an_instrument_and_development_conditions(self):
        catalog = {'schema': 'development-conditions/1', 'note': 'another key',
                   'conditions': [{'id': 'low', 'reference': {'value': -12.0}}, {'id': 'high'}]}
        for more in ({'instrument': {'units': {}}}, {'instrument': {'units': {'gauge': 'percent'}}},
                     {'development_conditions': catalog},
                     {'instrument': {'units': {'gauge': 'percent'}}, 'development_conditions': catalog}):
            with self.subTest(more=sorted(more)):
                self.assertEqual(client_interface.device_errors({**DESCRIPTOR, **more}), [])

    def test_06_B9_any_other_part_is_a_fault_that_names_it(self):
        for name in ('kind', 'image_sha256', 'provider_id', 'time_policy'):
            with self.subTest(part=name):
                self.assertEqual(client_interface.device_errors({**DESCRIPTOR, name: 'anything'}),
                                 ['a device-descriptor/2 has no part named ' + name])

    def test_06_B9_a_descriptor_of_another_schema_gives_the_one_fault(self):
        earlier = {**DESCRIPTOR, 'schema': 'device-descriptor/1', 'kind': 'provided-emulator', 'image_sha256': SHA}
        for device in (None, 'descriptor', {}, earlier, without(DESCRIPTOR, 'schema')):
            with self.subTest(device=device):
                self.assertEqual(client_interface.device_errors(device), ['not a device-descriptor/2'])

    def test_06_B9_each_missing_or_malformed_part_has_its_fault(self):
        conditions = 'development_conditions holds a list of conditions with distinct ids'
        instrument = 'instrument is an object with exactly units'
        for device, fault in (
                (without(DESCRIPTOR, 'control'), 'no control endpoint file'),
                ({**DESCRIPTOR, 'control': {'endpoint_file': ''}}, 'no control endpoint file'),
                ({**DESCRIPTOR, 'control': '/nowhere/control.json'}, 'no control endpoint file'),
                (without(DESCRIPTOR, 'session_id'), 'no session_id'),
                ({**DESCRIPTOR, 'session_id': ''}, 'no session_id'),
                (without(DESCRIPTOR, 'binding'), 'no binding'), ({**DESCRIPTOR, 'binding': {}}, 'no binding'),
                (without(DESCRIPTOR, 'firmware_sha256'), 'no firmware_sha256'),
                ({**DESCRIPTOR, 'firmware_sha256': SHA.upper()}, 'no firmware_sha256'),
                ({**DESCRIPTOR, 'firmware_sha256': 'short'}, 'no firmware_sha256'),
                ({**DESCRIPTOR, 'instrument': {'units': {}, 'label': 'reading'}}, instrument),
                ({**DESCRIPTOR, 'instrument': {'units': ['percent']}}, instrument),
                ({**DESCRIPTOR, 'instrument': None}, instrument),
                ({**DESCRIPTOR, 'development_conditions': {'conditions': []}}, conditions),
                ({**DESCRIPTOR, 'development_conditions': {'conditions': [{'id': 'low'}, {'id': 'low'}]}}, conditions),
                ({**DESCRIPTOR, 'development_conditions': {'conditions': [{'id': ''}]}}, conditions),
                ({**DESCRIPTOR, 'development_conditions': {'conditions': ['low']}}, conditions),
                ({**DESCRIPTOR, 'development_conditions': [{'id': 'low'}]}, conditions)):
            with self.subTest(fault=fault, device=device):
                self.assertEqual(client_interface.device_errors(device), [fault])
        several = {'schema': 'device-descriptor/2', 'kind': 'provided-emulator'}
        self.assertEqual(client_interface.device_errors(several),
                         ['a device-descriptor/2 has no part named kind', 'no control endpoint file', 'no session_id',
                          'no binding', 'no firmware_sha256'])

    def test_06_B9_a_device_offers_readings_with_no_instrument_or_with_units_that_name_some(self):
        from generative_driver.device_client import offers_readings
        self.assertTrue(offers_readings(DESCRIPTOR))
        self.assertTrue(offers_readings({**DESCRIPTOR, 'instrument': {'units': {'gauge': 'percent'}}}))
        self.assertFalse(offers_readings({**DESCRIPTOR, 'instrument': {'units': {}}}))


class VerdictRuleTests(unittest.TestCase):
    """The verdict record in the tool's terms (06-B10)."""

    def test_06_B10_the_verdict_record_has_the_twelve_fields(self):
        self.assertEqual(client_interface.VERDICT_FIELDS, frozenset(TWELVE))
        self.assertEqual(len(client_interface.VERDICT_FIELDS), 12)
        self.assertEqual(client_interface.verdict_errors(VERDICT), [])

    def test_06_B10_a_record_with_a_method_or_a_capability_hash_has_the_fault_of_its_fields(self):
        fields = 'an equivalence/2 record has exactly the fields ' + ', '.join(sorted(TWELVE))
        for more in ({'method': 'fresh-final'}, {'capabilities_sha256': SHA},
                     {'method': 'fresh-final', 'capabilities_sha256': None}):
            with self.subTest(more=sorted(more)):
                self.assertEqual(client_interface.verdict_errors({**VERDICT, **more}), [fields])
        self.assertEqual(client_interface.verdict_errors(without(VERDICT, 'mission')), [fields])

    def test_06_B10_the_schema_is_equivalence_2(self):
        required = 'schema equivalence/2, basis evaluator and protocol assistive/1 are required'
        for changed in ({'schema': 'equivalence/1'}, {'basis': 'self-evidence'}, {'protocol': 'gated/1'}):
            with self.subTest(changed=changed):
                self.assertEqual(client_interface.verdict_errors({**VERDICT, **changed}), [required])

    def test_06_B10_every_other_rule_of_the_record_is_as_before(self):
        cases = (({'checks': [{'id': 'reads the level', 'passed': None}]},
                  ['checks are [{id, passed}], with passed null only for an undecided check in an unknown grade or '
                   'beside a failed one', 'passed and total count the checks',
                   'an equivalent driver passed every check, with no difference']),
                 ({'diff': [{'id': 'reads the level'}]}, ['diff is [{id, reason}]']),
                 ({'passed': 2}, ['passed and total count the checks']),
                 ({'status': 'no-driver'},
                  ['status graded or no-driver with equivalent true or false, or unscored or unknown with equivalent '
                   'null']),
                 ({'equivalent': False}, ['a driver that is not judged equivalent names its differences']),
                 ({'mission': {'name': 'reuse', 'score': 1}}, ['mission is {name, passed, reason, fault}']),
                 ({'frozen_artifact_sha256': 'short'}, ['hashes are sha256 hex or null']),
                 ({'evidence_sha256': None}, ['a grade names the evidence it was sealed in']))
        for changed, faults in cases:
            with self.subTest(changed=changed):
                self.assertEqual(client_interface.verdict_errors({**VERDICT, **changed}), faults)
        unscored = {**VERDICT, 'status': 'unscored', 'equivalent': None, 'checks': [], 'passed': 0, 'total': 0,
                    'diff': [{'id': 'evaluation', 'reason': 'the evaluator could not run'}], 'evidence_sha256': None}
        self.assertEqual(client_interface.verdict_errors(unscored), [])


TOKEN = 'a token only this test holds'
TOKEN_SHA256 = hashlib.sha256(TOKEN.encode()).hexdigest()


class ServiceCase(unittest.TestCase):
    """A service in a temporary home, with the hand-written device, firmware and mission."""

    def setUp(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.controller = Controller(self.root / 'home')
        self.addCleanup(self.controller.close)
        self.firmware = hand_written_firmware(self.root)

    def device(self, name='device', **options):
        """A hand-written device under the test's folder, closed when the test ends."""
        device = SuppliedDevice(self.root / name, **options)
        self.addCleanup(device.close)
        return device

    def supplied(self, device=None, **more):
        """The params of a `start` on `device`, or on a new hand-written device, with `more` laid over them."""
        return {'goal': 'Read the gauge', 'device': (device or self.device()).descriptor,
                'firmware': dict(self.firmware), 'mission': copy.deepcopy(MISSION),
                'evaluation_token_sha256': TOKEN_SHA256,
                'executor_config': {'command': ['never-launched']}, **more}

    def held(self, params):
        """The reply to a `start` of `params` whose run is held back: it stays queued and no stage begins."""
        with patch.object(self.controller, '_spawn'):
            return self.controller.call('start', params)

    def refusal(self, params, kind=ValueError):
        """The text of the refusal of a `start` of `params`, which must be of `kind` exactly."""
        with self.assertRaises(ValueError) as refused:
            self.controller.call('start', params)
        self.assertIs(type(refused.exception), kind, refused.exception)
        return str(refused.exception)

    def run_dir(self, run):
        return self.controller.home / 'runs' / run['run_id']


class StartTests(ServiceCase):
    """`start` takes a run on a supplied device with the firmware and the mission as data (06-B3)."""

    def test_06_B3_start_answers_with_a_run_on_the_supplied_device(self):
        device = self.device()
        started = self.held(self.supplied(device))
        self.assertEqual(started, {'ok': True, 'run_id': started['run_id'], 'duplicate': False})
        progress = self.controller.call('result', {'run_id': started['run_id']})['progress']
        self.assertEqual(progress['device'],
                         {'session_id': device.session_id, 'binding': device.binding, 'receipts': []})

    def test_06_B3_start_may_carry_the_other_keys_of_a_run(self):
        more = {'effects': ['read', 'write'], 'budget_seconds': 600, 'executor': 'codex', 'request_id': 'gauge-1',
                'client_record': {'order': 17}, 'ghidra_home': str(self.root / 'ghidra'),
                'java_home': str(self.root / 'java'), 'scoped_tool_approval': 'emulator'}
        started = self.held(self.supplied(**more))
        self.assertEqual((started['ok'], started['duplicate']), (True, False))

    def test_06_B3_the_service_leases_the_devices_binding_to_the_run(self):
        device = self.device()
        self.held(self.supplied(device))
        other = {**device.descriptor, 'session_id': 'another session'}
        refused = self.refusal({**self.supplied(device), 'device': other}, kind=_service('SafetyError'))
        self.assertEqual(refused, 'Device binding is already owned by another run or session')

    def test_06_B3_the_same_start_again_finds_the_same_run(self):
        params = self.supplied(request_id='gauge-2')
        first = self.held(params)
        again = self.held(params)
        self.assertEqual(again, {'ok': True, 'run_id': first['run_id'], 'duplicate': True})

    def test_06_B3_the_service_writes_nothing_into_the_runs_folder_at_start(self):
        started = self.held(self.supplied())
        self.assertFalse(self.run_dir(started).exists())


def _service(name):
    """The class `name` of the service."""
    from generative_driver import configurator
    return getattr(configurator, name)


class StartRefusalTests(ServiceCase):
    """What a supplied run may not be (06-B4)."""

    def test_06_B4_a_device_a_firmware_and_a_mission_come_together(self):
        whole = self.supplied()
        lacks = 'A supplied run needs a device, a firmware and a mission together; this start lacks '
        for missing, named in ((('device',), 'device'), (('firmware',), 'firmware'), (('mission',), 'mission'),
                               (('device', 'firmware'), 'device, firmware'), (('device', 'mission'), 'device, mission'),
                               (('firmware', 'mission'), 'firmware, mission')):
            with self.subTest(missing=missing):
                params = {key: value for key, value in whole.items() if key not in missing}
                self.assertEqual(self.refusal(params), lacks + named)

    def test_06_B4_a_supplied_run_requires_the_hash_of_the_clients_token(self):
        text = "A run on a supplied device requires evaluation_token_sha256, the sha256 of the client's token"
        whole = self.supplied()
        self.assertEqual(self.refusal(without(whole, 'evaluation_token_sha256')), text)
        for token in (TOKEN, TOKEN_SHA256.upper(), TOKEN_SHA256[:63], None, 7):
            with self.subTest(token=token):
                self.assertEqual(self.refusal({**whole, 'evaluation_token_sha256': token}), text)

    def test_06_B4_a_descriptor_a_firmware_or_a_mission_with_a_fault_is_refused_with_its_faults(self):
        whole = self.supplied()
        earlier = {**whole['device'], 'schema': 'device-descriptor/1', 'kind': 'provided-emulator'}
        self.assertEqual(self.refusal({**whole, 'device': earlier}),
                         'Invalid device descriptor: not a device-descriptor/2')
        faulty = {**without(whole['device'], 'session_id'), 'kind': 'provided-emulator'}
        self.assertEqual(self.refusal({**whole, 'device': faulty}),
                         'Invalid device descriptor: a device-descriptor/2 has no part named kind; no session_id')
        self.assertEqual(self.refusal({**whole, 'firmware': {'path': 'firmware.bin', 'sha256': 'none'}}),
                         'Invalid firmware: path is an absolute path; sha256 is 64 lowercase hex characters')
        self.assertEqual(self.refusal({**whole, 'firmware': {**self.firmware, 'path': str(self.root / 'absent.bin')}}),
                         'Invalid firmware: no file at path')
        mission = {**MISSION, 'reuse_objective': '', 'tasks': {'9lives': {}}}
        self.assertEqual(self.refusal({**whole, 'mission': mission}),
                         'Invalid mission: reuse_objective is text that is not blank; invalid task: 9lives')

    def test_06_B4_a_firmware_whose_bytes_do_not_hash_to_its_sha256_is_an_integrity_refusal(self):
        whole = self.supplied()
        other = self.root / 'other.bin'
        other.write_bytes(b'another firmware')
        self.assertEqual(self.refusal({**whole, 'firmware': {**self.firmware, 'path': str(other)}},
                                      kind=integrity.IntegrityError),
                         "The supplied firmware's bytes do not hash to its sha256")

    def test_06_B4_a_device_that_runs_another_firmware_is_an_integrity_refusal(self):
        device = self.device(firmware_sha256=SHA)
        self.assertEqual(self.refusal(self.supplied(device), kind=integrity.IntegrityError),
                         'The supplied device runs another firmware than the one supplied')

    def test_06_B4_start_takes_no_key_of_a_case(self):
        whole = self.supplied()
        values = {'case': 'a-case', 'case_options': {}, 'case_pin': {}, 'admission': {}, 'public_inputs': '/nowhere'}
        for key, value in values.items():
            with self.subTest(key=key):
                self.assertEqual(self.refusal({**whole, key: value}), 'start takes no ' + key
                                 + '; a client supplies the device, the firmware and the mission')
        # The first such key in that order is the one named, and a run without a supplied device is refused too.
        self.assertEqual(self.refusal({**whole, 'public_inputs': '/nowhere', 'case_pin': {}, 'case_options': {}}),
                         'start takes no case_options; a client supplies the device, the firmware and the mission')
        self.assertEqual(self.refusal({'goal': 'Read the gauge', 'case': 'a-case'}),
                         'start takes no case; a client supplies the device, the firmware and the mission')

    def test_06_B4_start_takes_no_password_emulator_executable_or_set_root(self):
        text = ('The configurator takes no password, password file, emulator executable or set root; the client '
                'holds them')
        for key in ('password', 'evaluator_password_file', 'Evaluator_Password_Files', 'renode', 'set_root', 'Renode'):
            with self.subTest(key=key):
                self.assertEqual(self.refusal({**self.supplied(), key: 'a value'}), text)
                self.assertEqual(self.refusal({'goal': 'Read the gauge', key: 'a value'}), text)


def _exchange(text, capture, **extra):
    """One step of a line model: send `text`, read a line, keep it as `capture`."""
    return {'op': 'exchange', 'tx': [{'text': text}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128},
            'timeout_ms': 2000, 'capture': capture, **extra}


# What the gauge, the line device of the full run, answers.
GAUGE = {b'ID?': b'GAUGE-1\n', b'L?': b'L:42\n', b'S?': b'S:7\n', b'Z': b'ERR\n'}
# A catalog of development conditions, as a descriptor carries one.
CATALOG = {'schema': 'development-conditions/1', 'conditions': [{'id': 'low'}, {'id': 'high'}]}


def gauge_model():
    """A line model of the gauge: its identity, and one operation for each task of the hand-written mission."""
    number = lambda prefix, unit: {'from': 'reply', 'kind': 'ascii_number', 'prefix': prefix, 'unit': unit}
    return {'schema': 'interface-model/3', 'device': {'name': 'GAUGE-1'}, 'channel': {'type': 'tcp'},
            'identity': {'operation': 'identify'},
            'operations': {
                'identify': {'effect': 'read', 'parameters': {}, 'outputs': {},
                             'steps': [_exchange('ID?\n', 'id', expect={'equals_hex': b'GAUGE-1\n'.hex()})]},
                'level': {'effect': 'read', 'parameters': {}, 'steps': [_exchange('L?\n', 'reply')],
                          'outputs': {'level': number('L:', 'percent')}},
                'status': {'effect': 'read', 'parameters': {}, 'steps': [_exchange('S?\n', 'reply')],
                           'outputs': {'status': number('S:', 'code')}}},
            'provenance': [{'item': 'operations.' + name, 'source': 'the supplied firmware'}
                           for name in ('identify', 'level', 'status')]}


def acquire(worker):
    """An acquire worker that imports the firmware it was handed."""
    worker.imported = worker.tool('acquire_firmware_artifact', **worker.task['context'])


def interpret(worker):
    """An interpret worker that reads the tasks it was given and writes the gauge's model."""
    worker.tasks = json.loads((worker.workspace / 'TASKS.json').read_text(encoding='utf-8'))
    worker.write('model.json', gauge_model())


def probe(worker):
    """A probe worker that reads its guide, maps the mission's tasks and runs each mapped operation once on the
    device."""
    context = worker.task['context']
    worker.guide = (worker.workspace / 'CAPABILITY_FORMAT.md').read_text(encoding='utf-8')
    Path(context['capabilities_output']).write_text(json.dumps(MAPPING), encoding='utf-8')
    worker.probed = [worker.tool('probe_run', model_dir=context['model_dir'], operation=operation, n=1)
                     for operation in ('level', 'status')]


def emit(worker):
    """An emit worker that packages its model with its probe record and reports the package."""
    context = worker.task['context']
    built = worker.tool('emit_package', model_dir=context['model_dir'], probe_json=context['probe'])
    return {'status': 'completed', 'summary': 'scripted emit', 'checks': [], 'unresolved': [],
            'artifacts': [{'path': built['package_dir'], 'sha256': integrity.digest(built['package_dir']),
                           'kind': 'package'}]}


def ground(worker):
    """A ground worker that reads the evidence index and the guide the stage package wrote for it and for probe."""
    index = worker.workspace / 'ground-evidence' / 'device-observations.json'
    worker.evidence = json.loads(index.read_text(encoding='utf-8'))


def reuse(worker):
    """A fresh reuse worker that calls the generated tool of each operation but the identity."""
    tools = worker.controller.call('mcp_tools', worker.identity)['tools']
    worker.reused = [worker.controller.call('mcp_tool', {**worker.identity, 'name': tool['name'], 'arguments': {},
                                                         'request_id': tool['name']})
                     for tool in tools if not tool['name'].endswith('identify')]


WORKERS = {'acquire': acquire, 'interpret': interpret, 'probe': probe, 'ground': ground, 'emit': emit,
           'reuse': reuse}


class ScriptedCase(ScriptedRunCase):
    """Supplied runs in a temporary folder whose workers are scripted, on hand-written devices."""

    def device(self, name='device', **options):
        device = SuppliedDevice(self.root / name, **{'replies': GAUGE, **options})
        self.addCleanup(device.close)
        return device

    def supplied(self, device=None, **more):
        """The params of a `start` on `device`, or on a new hand-written device, with `more` laid over them."""
        return {'device': (device or self.device()).descriptor, 'firmware': hand_written_firmware(self.root),
                'mission': copy.deepcopy(MISSION), 'evaluation_token_sha256': TOKEN_SHA256, **more}

    def workers(self, stage):
        """What the scripted workers of `stage` saw and did, in order."""
        return [worker for worker in self.seen if worker.stage == stage]

    def run_with(self, script, **params):
        """As the parent's, and keeps each launched worker in `self.seen`."""
        self.seen = []

        def keeping(work):
            def kept(worker):
                self.seen.append(worker)
                if worker.stage == 'interpret':
                    # The stage package seals the interpret workspace outside the run; remove it after the test.
                    self.addCleanup(shutil.rmtree, worker.workspace.parent, True)
                return work(worker) if work else None
            return kept
        stages = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
        return super().run_with({stage: keeping(script.get(stage)) for stage in stages}, **params)

    def run_dir(self):
        return self.controller.home / 'runs' / self.run['run_id']


class FullRunCase(ScriptedCase):
    """One supplied run through all six stages on the gauge, shared by the tests of a class. Its workers are scripted
    and its stages are the stage package's own, called through spies."""

    @classmethod
    def setUpClass(cls):
        from generative_driver.supplied import stages
        fixture = cls('shared_run')
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        cls.gauge = fixture.device(units={'gauge': 'percent'}, values={'gauge': 42}, conditions=CATALOG)
        cls.prepared, cls.observed = [], []
        real_prepare, real_observe = stages.prepare_stage, stages.observe_stage

        def prepare_stage(stage, run_dir, workspace, accepted=None, options=None, *, firmware, mission):
            call = {'stage': stage, 'options': copy.deepcopy(options), 'firmware': firmware, 'mission': mission}
            cls.prepared.append(call)
            call['prepared'] = real_prepare(stage, run_dir, workspace, accepted, options, firmware=firmware,
                                            mission=mission)
            return call['prepared']

        def observe_stage(stage, run_dir, workspace, report, accepted=None, options=None, *, firmware, mission):
            cls.observed.append({'stage': stage, 'options': copy.deepcopy(options), 'firmware': firmware,
                                 'mission': mission})
            return real_observe(stage, run_dir, workspace, report, accepted, options, firmware=firmware,
                                mission=mission)
        cls.start = fixture.supplied(cls.gauge, effects=['read', 'write'], client_record={'order': 17, 'case': 'kept'},
                                     ghidra_home=str(fixture.root / 'ghidra'), java_home=str(fixture.root / 'java'))
        with patch.object(stages, 'prepare_stage', prepare_stage), patch.object(stages, 'observe_stage', observe_stage):
            cls.result = fixture.run_with(WORKERS, **cls.start)
        cls.fixture = fixture

    def shared_run(self):
        """Not a test: the name under which the class builds the fixture that holds the shared run."""


class FirmwareHoldTests(ScriptedCase):
    """The service holds a supplied run to the firmware it was started with (06-B5)."""

    def changed_after_acquire(self, change):
        """The result of a supplied run whose acquire worker does `change(path)` to the firmware's file."""
        start = self.supplied()

        def tamper(worker):
            acquire(worker)
            change(Path(start['firmware']['path']))
        return self.run_with({**WORKERS, 'acquire': tamper}, **start)

    def test_06_B5_a_firmware_that_changed_stops_the_run_for_integrity_before_the_next_stage(self):
        for what, change in (('other bytes', lambda path: path.write_bytes(b'another firmware')),
                             ('gone', lambda path: path.unlink())):
            with self.subTest(firmware=what):
                result = self.changed_after_acquire(change)
                self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'integrity'), result)
                self.assertIn('The supplied firmware changed after the run started', result['reason'])
                # Acquire ran on the firmware as supplied; no worker was assigned after it changed.
                self.assertEqual([h['stage'] for h in result['accepted_handoffs']], ['acquire'])
                self.assertEqual(self.assigned('interpret'), [])
                self.assertEqual([worker.stage for worker in self.seen], ['acquire'])

    def test_06_B5_resume_of_a_run_whose_firmware_changed_is_refused(self):
        self.changed_after_acquire(lambda path: path.write_bytes(b'another firmware'))
        with self.assertRaises(integrity.IntegrityError) as refused:
            self.controller.call('resume', self.run)
        self.assertIn('The supplied firmware changed after the run started', str(refused.exception))
        status = self.controller.call('status', self.run)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'integrity'))


class StageTests(FullRunCase):
    """A supplied run's stages run on the stage package, from what the client supplied (06-B7)."""

    def test_06_B7_the_service_calls_the_stage_package_with_the_firmware_and_the_mission_of_the_start(self):
        stages = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse']
        self.assertEqual([call['stage'] for call in self.prepared], stages)
        self.assertEqual([call['stage'] for call in self.observed], stages)
        for call in (*self.prepared, *self.observed):
            with self.subTest(stage=call['stage']):
                self.assertEqual(call['firmware'], self.start['firmware'])
                self.assertEqual(call['mission'], self.start['mission'])

    def test_06_B7_acquire_hands_on_the_supplied_firmwares_bytes_and_interpret_the_missions_tasks(self):
        [acquired] = self.fixture.workers('acquire')
        self.assertEqual(Path(acquired.task['inputs'][0]).read_bytes(), b'supplied firmware')
        self.assertEqual(acquired.imported['sha256'], FIRMWARE_SHA256)
        [interpreted] = self.fixture.workers('interpret')
        self.assertEqual(interpreted.tasks['tasks'], MISSION['tasks'])

    def test_06_B7_the_stage_options_hold_the_tools_the_effects_and_the_device_and_nothing_of_a_case(self):
        device = {'session_id': self.gauge.session_id, 'binding': self.gauge.binding,
                  'instrument': {'units': {'gauge': 'percent'}}, 'development_conditions': CATALOG}
        for call in (*self.prepared, *self.observed):
            options = call['options']
            with self.subTest(stage=call['stage']):
                self.assertEqual((options['ghidra_home'], options['java_home']),
                                 (self.start['ghidra_home'], self.start['java_home']))
                self.assertEqual(options['configured_effects'], ['read', 'write'])
                self.assertEqual(options['device'], device)
                for key in ('scenario_id', 'case_seed', '_execution_run_dir'):
                    self.assertNotIn(key, options)


class StageOptionTests(ScriptedCase):
    def test_06_B7_a_descriptor_with_no_instrument_and_no_conditions_gives_the_device_its_session_and_binding(self):
        from generative_driver.supplied import stages
        device, seen = self.device(), []
        real = stages.prepare_stage

        def prepare_stage(stage, run_dir, workspace, accepted=None, options=None, *, firmware, mission):
            seen.append(copy.deepcopy(options))
            return real(stage, run_dir, workspace, accepted, options, firmware=firmware, mission=mission)
        with patch.object(stages, 'prepare_stage', prepare_stage):
            self.run_with({'acquire': acquire}, **self.supplied(device))
        self.assertEqual(seen[0]['device'], {'session_id': device.session_id, 'binding': device.binding})
        self.assertEqual(seen[0]['configured_effects'], ['read'])
        self.assertEqual([key for key in ('ghidra_home', 'java_home') if key in seen[0]], [])

    def test_06_B7_a_supplied_runs_kit_tools_run_in_the_services_own_process(self):
        from generative_driver.configurator import Controller
        from input_path_fixtures import Worker
        controller = Controller(self.root / 'service-home', for_service=True)
        self.addCleanup(controller.close)
        imported = {}

        def execute(request, config, cancel):
            if request.stage == 'acquire':
                worker = Worker(controller, request)
                imported.update(worker.tool('acquire_firmware_artifact', **worker.task['context']))
            return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0, 'usage': None,
                    'report': {'status': 'completed', 'summary': 'scripted', 'artifacts': [], 'checks': [],
                               'unresolved': []}}
        with patch('generative_driver.configurator.execute', execute), \
                patch('generative_driver.tool_process.run', side_effect=AssertionError('a child process')) as child:
            run = controller.call('start', {'goal': 'Read the gauge', 'executor_config': {'command': ['scripted']},
                                            **self.supplied()})
            self.stopped(controller, run)
        self.assertIs(imported.get('available'), True, imported)
        child.assert_not_called()


class ReplaceTests(ScriptedCase):
    def test_06_B9_the_service_replaces_a_failed_device_for_a_model_fault_on_a_supplied_run(self):
        # The device answers `ERR` where the model expects `OK`: a write that reached it and failed for the model.
        written = gauge_model()
        written['operations']['zero'] = {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [
            _exchange('Z\n', 'ack', expect={'equals_hex': b'OK\n'.hex()})]}
        written['provenance'].append({'item': 'operations.zero', 'source': 'the supplied firmware'})
        device = self.device()
        first = {'session_id': device.session_id, 'binding': device.binding}

        def failing(worker):
            context = worker.task['context']
            Path(context['capabilities_output']).write_text(json.dumps(MAPPING), encoding='utf-8')
            worker.failed = worker.tool('interface_execute', model_dir=context['model_dir'], operation='zero')
        result = self.run_with({'acquire': acquire, 'interpret': lambda worker: worker.write('model.json', written),
                                'probe': failing}, **self.supplied(device, effects=['read', 'write']), max_revisions=0)
        [probed] = self.workers('probe')
        self.assertEqual(probed.failed['error']['fault'], 'model', probed.failed)
        [request] = device.sent('replace')
        self.assertEqual(request['expected_session_id'], first['session_id'])
        held = result['progress']['device']
        self.assertEqual((held['session_id'], held['binding']), (device.session_id, device.binding))
        self.assertNotEqual(held['session_id'], first['session_id'])
        [receipt] = held['receipts']
        self.assertEqual((receipt['schema'], receipt['old_session_id'], receipt['new_session_id']),
                         ('emulator-recovery/2', first['session_id'], device.session_id))
        self.assertEqual(result['emulator_recovery_count'], 1)
        self.assertFalse(result['uncertain_effect'])
        # The descriptor names no kind: any supplied device is replaced.
        self.assertNotIn('kind', device.descriptor)


class VerdictTests(FullRunCase):
    """A supplied run waits for its client's verdict (06-B10)."""

    def test_06_B10_a_supplied_run_whose_stages_are_done_waits_for_its_clients_verdict(self):
        result = self.result
        self.assertEqual((result['status'], result['reason']),
                         ('completed', 'Frozen driver awaits independent evaluation'), result)
        self.assertEqual(result['progress']['equivalence'], 'pending')
        self.assertIsNone(result['equivalence'])
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']],
                         ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])

    def test_06_B10_record_evaluation_records_the_one_final_verdict_and_asks_for_no_method(self):
        controller, run = self.fixture.controller, self.fixture.run
        driver = client_interface.frozen_driver(self.result['accepted_handoffs'])
        record = {**VERDICT, 'frozen_artifact_sha256': driver['sha256']}
        post = lambda equivalence, token=TOKEN: controller.call(
            'record_evaluation', {**run, 'token': token, 'equivalence': equivalence})
        # Its other refusals keep their texts.
        with self.assertRaisesRegex(ValueError, "^record_evaluation requires the run's evaluation token$"):
            post(record, token='another token')
        with self.assertRaisesRegex(ValueError, '^Invalid evaluation record: an equivalence/2 record has exactly'):
            post({**record, 'method': 'fresh-final'})
        with self.assertRaises(integrity.IntegrityError) as refused:
            post({**record, 'frozen_artifact_sha256': 'c' * 64})
        self.assertEqual(str(refused.exception), "The evaluated package is not the run's accepted emitted package")
        status = post(record)
        self.assertEqual(status['equivalence'], record)
        self.assertEqual((status['status'], status['reason']), ('completed', 'Final verdict: equivalent'))
        self.assertEqual(controller.call('result', run)['progress']['equivalence'], 'graded')
        # The same record again changes nothing; another one is refused.
        self.assertEqual(post(record)['equivalence'], record)
        other = {**record, 'equivalent': False, 'checks': [{'id': 'reads the level', 'passed': False}], 'passed': 0,
                 'diff': [{'id': 'reads the level', 'reason': 'the level differs'}]}
        with self.assertRaisesRegex(ValueError, '^A different evaluation is already recorded for this run$'):
            post(other)


class VerdictRefusalTests(ServiceCase):
    def test_06_B10_a_run_started_without_a_token_records_no_evaluation(self):
        started = self.held({'goal': 'Read the gauge', 'executor_config': {'command': ['never-launched']}})
        with self.assertRaises(ValueError) as refused:
            self.controller.call('record_evaluation', {**started, 'token': TOKEN, 'equivalence': VERDICT})
        self.assertEqual(str(refused.exception), 'Only a run started with an evaluation token can record an evaluation')

    def test_06_B10_a_supplied_run_that_is_not_completed_records_no_evaluation(self):
        started = self.held(self.supplied())
        with self.assertRaises(ValueError) as refused:
            self.controller.call('record_evaluation', {**started, 'token': TOKEN, 'equivalence': VERDICT})
        self.assertEqual(str(refused.exception), 'record_evaluation only for a completed run')


class SelfEvidenceTests(ScriptedCase):
    def test_06_B10_a_run_without_a_supplied_device_gets_its_self_evidence_verdict(self):
        result = self.run_with({})
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['equivalence']['basis'], 'self-evidence')
        self.assertNotIn('equivalence', result['progress'])
        self.assertTrue(result['reason'].startswith('Final verdict: '), result['reason'])


class DeviceTests(ServiceCase):
    """A supplied run's device is replaced, resumed on and released without a case (06-B11)."""

    def stopped_run(self, **params):
        """A run of `params` whose worker runtime is missing, once it has stopped at acquire for the operator."""
        run = self.controller.call('start', {**params, 'executor_config': {'command': ['missing-agent-runtime']}})
        until = time.monotonic() + 30
        while True:
            status = self.controller.call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                self.assertEqual((status['status'], status['stage']), ('blocked', 'acquire'), status)
                return run
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)

    def attach(self, run, device, token=TOKEN):
        return self.controller.call('attach_device', {**run, 'token': token, 'device': device})

    def refused(self, call, kind=ValueError):
        with self.assertRaises(ValueError) as refused:
            call()
        self.assertIs(type(refused.exception), kind, refused.exception)
        return str(refused.exception)

    def test_06_B11_a_supplied_run_that_stops_releases_its_device_with_the_recorded_session(self):
        device = self.device()
        self.stopped_run(**self.supplied(device))
        self.assertEqual(device.sent('release'), [{'session_id': device.session_id}])

    def test_06_B11_a_run_without_a_supplied_device_sends_nothing_to_a_control_endpoint(self):
        from generative_driver.device_client import DeviceClient
        with patch.object(DeviceClient, '_call', side_effect=AssertionError('a control request')) as sent:
            run = self.stopped_run(goal='Read the gauge')
        sent.assert_not_called()
        kinds = [event['kind'] for event in self.controller.call('events', run)['events']]
        self.assertNotIn('device.release_failed', kinds)

    def test_06_B11_resume_asks_the_devices_status_and_continues_only_on_the_recorded_healthy_device(self):
        device = self.device()
        run = self.stopped_run(**self.supplied(device))
        asked = len(device.sent('status'))
        device.healthy = False
        self.assertEqual(self.refused(lambda: self.controller.call('resume', run)),
                         'Device provider unavailable: the supplied device is not healthy; attach a verified device')
        self.assertEqual(len(device.sent('status')), asked + 1)
        device.healthy, recorded = True, device.session_id
        device.session_id = 'another session'
        self.assertEqual(self.refused(lambda: self.controller.call('resume', run)),
                         'Device session changed; attach a verified device')
        status = self.controller.call('status', run)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'operator'))
        device.session_id = recorded
        self.assertTrue(self.controller.call('resume', run)['ok'])

    def test_06_B11_attach_device_is_refused_for_what_is_no_device_of_the_run(self):
        device = self.device()
        run = self.stopped_run(**self.supplied(device))
        other = self.device('other')
        generic = self.stopped_run(goal='Read the gauge', evaluation_token_sha256=TOKEN_SHA256)
        self.assertEqual(self.refused(lambda: self.attach(generic, other.descriptor)),
                         'Only a run started on a supplied device can attach one')
        self.assertEqual(self.refused(lambda: self.attach(run, without(other.descriptor, 'session_id'))),
                         'Invalid device descriptor: no session_id')
        self.assertEqual(self.refused(lambda: self.attach(run, {**other.descriptor, 'firmware_sha256': SHA}),
                                      kind=integrity.IntegrityError),
                         'The supplied device runs another firmware than the one supplied')
        # Its other checks keep their texts.
        self.assertEqual(self.refused(lambda: self.attach(run, other.descriptor, token='another token')),
                         "attach_device requires the run's evaluation token")
        self.assertEqual(self.refused(lambda: self.attach(run, {**other.descriptor, 'session_id': 'not its session'})),
                         'The supplied device does not answer, healthy, with the session and binding its descriptor '
                         'names')

    def test_06_B11_attach_device_records_the_new_device_and_resume_continues_on_it(self):
        device = self.device()
        run = self.stopped_run(**self.supplied(device))
        other = self.device('other')
        status = self.attach(run, other.descriptor)
        self.assertEqual((status['ok'], status['status']), (True, 'blocked'))
        progress = self.controller.call('result', run)['progress']
        self.assertEqual(progress['device'], {'session_id': other.session_id, 'binding': other.binding, 'receipts': []})
        [attached] = [event['data'] for event in self.controller.call('events', run)['events']
                      if event['kind'] == 'device.attached']
        self.assertEqual(attached, {'session_id': other.session_id, 'binding': other.binding,
                                    'previous': {'session_id': device.session_id, 'binding': device.binding}})
        asked = len(other.sent('status'))
        self.assertTrue(self.controller.call('resume', run)['ok'])
        self.assertGreater(len(other.sent('status')), asked)

    def test_06_B11_a_run_an_earlier_version_started_on_a_case_is_neither_resumed_nor_graded(self):
        from generative_driver import configurator
        self.assertIs(configurator._earlier_case_run({'goal': 'x', 'case': 'a-case'}), True)
        self.assertIs(configurator._earlier_case_run(self.supplied()), False)
        device = self.device()
        spec = {'goal': 'x', 'case': 'a-case', 'executor': 'codex', 'executor_config': {}, 'budget_seconds': 600,
                'effects': ['read'], 'device': device.descriptor, 'evaluation_token_sha256': TOKEN_SHA256}
        with self.controller._db() as db:
            db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                       ('run-earlier', None, json.dumps(spec), 'blocked', 'probe', time.time(), time.time()))
            db.execute('INSERT INTO progress VALUES(?,?)', ('run-earlier', json.dumps(
                {'revision': 0, 'next_stage': 'probe', 'repairs': 0, 'feedback': None,
                 'protocol': configurator.PROTOCOL, 'device': {'session_id': 's', 'binding': device.binding}})))
        run = {'run_id': 'run-earlier'}
        text = 'This run was started on a benchmark case by an earlier version; start a new run'
        posted = {**run, 'token': TOKEN, 'equivalence': VERDICT}
        for call in (lambda: self.controller.call('resume', run), lambda: self.attach(run, device.descriptor),
                     lambda: self.controller.call('record_evaluation', posted)):
            self.assertEqual(self.refused(call), text)
        self.assertEqual(device.requests, [])


class ReplayAndApprovalTests(ServiceCase):
    """The replay executor and the emulator approval ask for a supplied device (06-B12)."""

    def candidate(self):
        """A saved replay candidate with no step."""
        folder = self.root / 'candidate'
        folder.mkdir()
        (folder / 'replay.json').write_text(json.dumps({'schema': 'replay-candidate/1', 'stages': {}}),
                                            encoding='utf-8')
        return folder

    def test_06_B12_the_replay_executor_runs_on_a_supplied_device_only(self):
        replay = {'executor': 'replay', 'executor_config': {'candidate': str(self.candidate())}}
        self.assertEqual(self.refusal({'goal': 'Read the gauge', **replay}),
                         'The replay executor runs on a supplied device only; this start names none')

    def test_06_B12_with_a_supplied_device_the_candidate_is_checked_and_copied_into_the_run(self):
        candidate = self.candidate()
        replay = {'executor': 'replay', 'executor_config': {'candidate': str(candidate)}}
        started = self.held({**self.supplied(), **replay})
        copied = self.run_dir(started) / 'replay-candidate'
        self.assertEqual(integrity.digest(copied), integrity.digest(candidate))
        (candidate / 'replay.json').write_text('not a candidate', encoding='utf-8')
        self.assertIn('replay.json', self.refusal({**self.supplied(), **replay}))

    def test_06_B12_the_runs_copy_of_the_candidate_is_checked_before_every_stage(self):
        replay = {'executor': 'replay', 'executor_config': {'candidate': str(self.candidate())}}

        def execute(request, config, cancel):
            raise AssertionError('a worker ran on a changed candidate')
        with patch('generative_driver.configurator._copy_candidate',
                   lambda config, run_dir: {**config, 'candidate': str(self.root / 'withdrawn')}), \
                patch('generative_driver.configurator.execute', execute):
            run = self.controller.call('start', {**self.supplied(), **replay})
            until = time.monotonic() + 30
            while self.controller.call('status', run)['status'] in ('queued', 'running'):
                self.assertLess(time.monotonic(), until)
                time.sleep(.02)
        status = self.controller.call('status', run)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'integrity'), status)
        self.assertIn('The replay candidate changed since the run recorded its hash', status['reason'])

    def test_06_B12_emulator_approval_requires_a_supplied_device_and_no_operator_binding(self):
        approval = {'scoped_tool_approval': 'emulator'}
        self.assertEqual(self.refusal({'goal': 'Read the gauge', **approval}),
                         'Emulator approval requires a supplied device')
        self.assertEqual(self.refusal({**self.supplied(), **approval, 'binding': {'host': '127.0.0.1', 'port': 9}}),
                         'Emulator tool approval cannot authorize an operator-supplied device binding')
        self.assertTrue(self.held({**self.supplied(), **approval})['ok'])


class ClientRecordTests(ServiceCase):
    """A run carries its client's record, and the service reads none of it (06-B13)."""

    def result(self, started):
        return self.controller.call('result', {'run_id': started['run_id']})

    def test_06_B13_result_gives_the_client_record_back_as_it_was_sent(self):
        record = {'case': 'kept as sent', 'case_pin': {'nested': [1, 2, {'deep': None}]}, 'snapshot_sha256': SHA,
                  'text': 'größe', 'number': 1.5}
        self.assertEqual(self.result(self.held(self.supplied(client_record=record)))['client_record'], record)
        self.assertEqual(self.result(self.held(self.supplied(client_record={})))['client_record'], {})
        self.assertNotIn('client_record', self.result(self.held(self.supplied())))
        generic = self.held({'goal': 'Read the gauge', 'executor_config': {'command': ['never-launched']},
                             'client_record': {'note': 'a run without a supplied device'}})
        self.assertEqual(self.result(generic)['client_record'], {'note': 'a run without a supplied device'})

    def test_06_B13_anything_but_a_json_object_of_at_most_262144_bytes_is_refused(self):
        text = 'client_record must be a JSON object of at most 262144 bytes'
        for record in (None, 'a record', ['a', 'list'], 7):
            with self.subTest(record=record):
                self.assertEqual(self.refusal(self.supplied(client_record=record)), text)
        # `{"k": "aaa"}` is ten bytes around the text as the service encodes it.
        largest = {'k': 'a' * (262144 - 9)}
        self.assertEqual(len(json.dumps(largest, sort_keys=True, ensure_ascii=False).encode('utf-8')), 262144)
        self.assertTrue(self.held(self.supplied(client_record=largest))['ok'])
        self.assertEqual(self.refusal(self.supplied(client_record={'k': 'a' * (262144 - 8)})), text)
        # The bytes count, not the characters.
        self.assertEqual(self.refusal(self.supplied(client_record={'k': 'ä' * 140000})), text)

    def test_06_B13_the_same_request_id_with_another_client_record_is_a_request_id_used_with_different_inputs(self):
        params = self.supplied(request_id='gauge-3', client_record={'order': 1})
        self.held(params)
        self.assertEqual(self.refusal({**params, 'client_record': {'order': 2}}),
                         'request_id was already used with different inputs')

    def test_06_B13_result_has_no_case_pin_or_snapshot_hash_and_status_and_events_are_as_they_were(self):
        started = self.held(self.supplied(client_record={'case': 'a-case', 'case_pin': {}, 'snapshot_sha256': SHA}))
        run = {'run_id': started['run_id']}
        generic = self.held({'goal': 'x', 'executor_config': {'command': ['c']}})
        for reply in (self.result(started), self.result(generic)):
            for key in ('case', 'case_pin', 'snapshot_sha256'):
                self.assertNotIn(key, reply)
        # The keys `status` gave before this Feature, for a run on a supplied device as for any other.
        self.assertEqual(sorted(self.controller.call('status', run)),
                         ['created', 'deadline', 'equivalence', 'log', 'ok', 'outcome_category', 'reason', 'repairs',
                          'run_id', 'stage', 'stage_notes', 'status', 'stopping', 'uncertain_effect', 'updated',
                          'worker'])
        events = self.controller.call('events', run)
        self.assertEqual(sorted(events), ['cursor', 'events', 'ok', 'run_id'])
        self.assertEqual([(event['kind'], sorted(event['data'])) for event in events['events']],
                         [('run.started', ['budget_seconds', 'executor', 'goal'])])

    def test_06_B13_the_dashboards_run_summary_has_no_case(self):
        from generative_driver.dashboard import project_run
        started = self.held(self.supplied(client_record={'case': 'a-case'}))
        summary = project_run(self.result(started))
        self.assertNotIn('case', summary)
        self.assertEqual(summary['run_id'], started['run_id'])
        script = Path(project_run.__code__.co_filename).parent / 'resources' / 'dashboard' / 'app.js'
        self.assertNotIn('data.case', script.read_text(encoding='utf-8'))


# The fresh worker's package tool as the service offers it: the description and the schema it had under its old name.
PACKAGE_TOOL = {
    'name': 'package_execute',
    'description': 'Execute a packaged driver operation over the configurator-owned binding. When the device has an '
                   'instrument, as an emulated device does, the result also carries its reading after the call: '
                   "device evidence, as a real device's reading would be, never a grade.",
    'inputSchema': {'type': 'object', 'required': ['package_dir', 'operation'], 'additionalProperties': False,
                    'properties': {'package_dir': {'type': 'string'}, 'operation': {'type': 'string'},
                                   'parameters': {'type': 'object'}}}}
# The objective the stage package gives fresh reuse for the hand-written mission on a device with an instrument.
REUSE_OBJECTIVE = ('Use only the emitted package and this objective: Read the level twice and report both readings.'
                   '\nProvided operating conditions (public facts):\n- The device answers one request at a time.\n'
                   ' Use package_execute for each operation. Find names and arguments in the package reference. '
                   'Each result also carries an instrument reading of the emulated device after the call. '
                   'Report actual observations and limitations.')


class PackageToolTests(ScriptedCase):
    """The fresh worker's package tool is `package_execute` (06-B20)."""

    def package_run(self):
        """The result of a supplied run whose fresh worker gets the stage package's own tool, as for a package
        that has no generated tools, lists its tools and calls that one once."""
        from generative_driver.configurator import Controller

        def reuse(worker):
            worker.offered = worker.controller.call('tools', worker.identity)['tools']
            package = worker.task['context']['package_dir']
            worker.called = worker.tool('package_execute', package_dir=package, operation='level')
        device = self.device(units={'gauge': 'percent'}, values={'gauge': 42})
        with patch.object(Controller, '_generated_reuse', staticmethod(lambda prepared: prepared)):
            return self.run_with({**WORKERS, 'reuse': reuse}, **self.supplied(device))

    def test_06_B20_the_reuse_stage_assigns_package_execute_and_its_objective_names_it(self):
        result = self.package_run()
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']][-1], 'reuse', result)
        [assignment] = self.assigned('reuse')
        self.assertEqual(assignment['allowed_tools'], ['package_execute'])
        [worker] = self.workers('reuse')
        # Only the tool's name changed in the objective.
        self.assertEqual(worker.task['objective'], REUSE_OBJECTIVE)

    def test_06_B20_the_service_offers_dispatches_and_records_the_tool_under_that_name(self):
        self.package_run()
        [worker] = self.workers('reuse')
        self.assertEqual(worker.offered, [PACKAGE_TOOL])
        self.assertIs(worker.called['ok'], True, worker.called)
        self.assertEqual(worker.called['outputs'], {'level': 42})
        self.assertEqual(worker.called['observation']['source'], 'instrument')
        calls = [event['data'] for event in self.events('tool.started', 'tool.finished')
                 if event['data']['stage'] == 'reuse']
        self.assertEqual([call['name'] for call in calls], ['package_execute', 'package_execute'])
        self.assertIs(calls[-1]['connected'], True)


class GeneratedReuseTests(FullRunCase):
    def test_06_B20_a_generated_packages_fresh_worker_is_told_to_use_its_generated_tools(self):
        # The service rewrites the stage package's sentence about its tool, which follows the tool's new name.
        reuse = next(call for call in self.prepared if call['stage'] == 'reuse')['prepared']
        self.assertEqual(reuse['allowed_tools'], ['package_execute'])
        self.assertIn(' Use package_execute for each operation. ', reuse['objective'])
        [worker] = self.fixture.workers('reuse')
        self.assertEqual(worker.task['objective'],
                         reuse['objective'].replace('Use package_execute for each operation.',
                                                    'Use the generated MCP tools for each operation.')
                         + ' Use only the assigned generated MCP tools to operate this device.')
        self.assertNotIn('package_execute', worker.task['objective'])


class SchemaNameTests(FullRunCase):
    """The capability mapping's schema is `capabilities/2` and the ground evidence's is `ground-evidence/3`
    (06-B21)."""

    def test_06_B21_the_capability_rules_accept_capabilities_2_and_refuse_the_old_name(self):
        from generative_driver.supplied import capabilities
        model, mapping = gauge_model(), {**MAPPING, 'schema': 'capabilities/2'}
        tasks = tuple(MISSION['tasks'])
        self.assertEqual(capabilities.validate_capabilities(mapping, model, tasks), {'ok': True, 'errors': []})
        self.assertEqual(capabilities.bind_task(mapping, 'read_level', {}, model)['operation'], 'level')
        old = {**mapping, 'schema': 'benchmark-capabilities/2'}
        refused = capabilities.validate_capabilities(old, model, tasks)
        self.assertIs(refused['ok'], False)
        self.assertEqual([(error['path'], error['message']) for error in refused['errors']],
                         [('tasks', 'Declare schema capabilities/2 and a tasks object')])
        with self.assertRaisesRegex(ValueError, 'Invalid capability schema or input'):
            capabilities.bind_task(old, 'read_level', {}, model)

    def test_06_B21_the_worker_texts_of_probe_name_capabilities_2(self):
        prepared = next(call for call in self.prepared if call['stage'] == 'probe')['prepared']
        self.assertIn('Write capabilities.json with schema capabilities/2 and tasks read_level, read_status. ',
                      prepared['objective'])
        [worker] = self.fixture.workers('probe')
        self.assertIn('Declare schema `capabilities/2` and a `tasks` object.', worker.guide)
        self.assertIn('{"schema":"capabilities/2","tasks":{', worker.guide)
        for text in (prepared['objective'], worker.guide):
            self.assertNotIn('benchmark-capabilities', text)

    def test_06_B21_the_mapping_a_probe_worker_writes_under_the_new_name_is_handed_on(self):
        self.assertEqual(MAPPING['schema'], 'capabilities/2')
        handed = next(h for h in self.result['accepted_handoffs'] if h['stage'] == 'probe')
        mapping = next(Path(a['path']) for a in handed['artifacts'] if a['path'].endswith('capabilities.json'))
        self.assertEqual(json.loads(mapping.read_text(encoding='utf-8')), MAPPING)
        [check] = [c for c in handed['checks'] if c['name'] == 'capability_mapping']
        self.assertIs(check['passed'], True, check)

    def test_06_B21_the_ground_stage_writes_ground_evidence_3(self):
        [worker] = self.fixture.workers('ground')
        self.assertEqual(worker.evidence['schema'], 'ground-evidence/3')


# What a full supplied run leaves under its `supplied/` folder: the six records of 06-B22.
RECORDS = ['candidate-history', 'live-observations-0.jsonl', 'package-checks', 'package-events.jsonl', 'state.json',
           'tools']


class RecordFolderTests(FullRunCase):
    """The tool keeps a supplied run's own records under the run's `supplied/` folder (06-B22)."""

    def test_06_B22_the_runs_own_records_lie_under_supplied(self):
        run = self.fixture.run_dir()
        self.assertEqual(sorted(path.name for path in (run / 'supplied').iterdir()), RECORDS)
        self.assertFalse((run / 'benchmark').exists(), 'the tool wrote under the run\'s benchmark/ folder')

    def test_06_B22_the_calls_of_fresh_reuse_are_recorded_in_supplied_package_events(self):
        recorded = self.fixture.run_dir() / 'supplied' / 'package-events.jsonl'
        rows = [json.loads(line) for line in recorded.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(row['stage'], row['operation'], row['result']['ok']) for row in rows],
                         [('reuse', 'level', True), ('reuse', 'status', True)])

    def test_06_B22_the_stages_read_their_state_and_the_probes_journal_there(self):
        run = self.fixture.run_dir()
        state = json.loads((run / 'supplied' / 'state.json').read_text(encoding='utf-8'))
        self.assertEqual(state['revision'], 0)
        self.assertTrue(Path(state['package_copies']['reuse']).is_dir())
        journal = (run / 'supplied' / 'live-observations-0.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual([json.loads(line)['operation'] for line in journal], ['level', 'status'])
        # The ground stage handed the worker that journal's rows.
        [worker] = self.fixture.workers('ground')
        self.assertEqual(len(worker.evidence['live_observations']), 2)


class NoDeviceTextTests(unittest.TestCase):
    def test_06_B7_a_device_stage_without_a_device_waits_with_the_text_that_names_the_supplied_device(self):
        from generative_driver.supplied import stages
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = root / 'emitted' / 'package'
            package.mkdir(parents=True)
            (package / 'manifest.json').write_text('{}', encoding='utf-8')
            state = root / 'run' / 'supplied' / 'state.json'
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({'package_sha256': integrity.digest(package)}), encoding='utf-8')
            emitted = [{'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': integrity.digest(package)}]}]
            prepared = stages.prepare_stage('reuse', root / 'run', root / 'reuse', emitted, {},
                                            firmware=hand_written_firmware(root), mission=MISSION)
        self.assertEqual(prepared, {'blocked': 'This stage needs the supplied device, and this run has none: start '
                                               'the run with a device, a firmware and a mission'})


class CatchUpTests(TaskRunCase):
    """What arrived with `main` uses the tool's own integrity helpers (06-B1)."""

    def test_06_B1_an_inventory_task_that_changes_a_sealed_input_blocks_the_run_for_integrity(self):
        controller, run, _, folder = self.start(cortex_m_image(), interpretation='tasks',
                                                script={'inventory': INVENTORY, 'tamper': 'inventory'})
        result = stopped(controller, run)
        self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'integrity'), result)
        self.assertIn('TASKS.json', result['reason'])
        self.assertEqual([record['task'] for record in seen(folder)], ['acquire', 'inventory'])


if __name__ == '__main__':
    unittest.main()
