"""A generated device's frozen driver graded step by step. Each package is emitted by the reference runner against the
hand-written fixture device, never generator output, then graded by the evaluator's reference_execute on a fresh fixture
device through its Renode-like monitor."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generated_fixtures import (FIXTURE_ANSWER_KEY, MODE_ADDRESS, MUTANT_ERR_AS_VALUE, REFERENCE_A, REFERENCE_B, STEP,
                                FixtureDevice, fixture_start)
from generative_driver.benchmark_support import generated
from generative_driver.benchmark_support.behavior import validate_records
from generative_driver.benchmark_support.evaluator import reference_execute
from generative_driver.benchmark_support.generated import classify_answer
from generative_driver.benchmark_support.integrity import digest
from generative_driver.benchmark_support.reference_calibration import run_reference

PIN = {'case_id': 'generated', 'scenario_id': 'original', 'case_seed': 0, 'revision': 0}
# Appended to a package's bundled runtime: every call then prints a result that labels its fault the host's.
PRINTS_A_HOST_FAULT = (b'\nimport json, sys\nsys.stdout.write(json.dumps({"ok": False, "outputs": {}, "units": {}, '
                       b'"transcript": [], "error": {"fault": "host", "code": "io", "message": "printed"}}))\n'
                       b'sys.exit(0)\n')


def add_to_bundled_runtime(package, code):
    """Append `code` to the package's bundled interface_runtime/package.py, as another system's package could differ,
    and keep the package consistent: its manifest follows the edit."""
    import hashlib
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    relative = 'interface_runtime/package.py'
    (package/relative).write_bytes((package/relative).read_bytes() + code)
    manifest = json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    manifest['files'][relative] = sha(package/relative)
    (package/'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    (package/'manifest.sha256').write_text(sha(package/'manifest.json') + '\n', encoding='utf-8')


class StepGradingTests(unittest.TestCase):
    def grade(self, candidate, *, edit=None, healthy_device_failures=False, **device):
        """The evaluation of `candidate`'s frozen package: emitted by the reference runner against a fixture device,
        then graded by reference_execute on a fresh one; both devices are FixtureDevice(**device). `edit` changes the
        package between the two. The evaluator's raw evidence is kept in self.raw."""
        key, start = FIXTURE_ANSWER_KEY, fixture_start(**device)
        with tempfile.TemporaryDirectory() as temp, \
                patch('generative_driver.benchmark_support.native.NativeSession.start', side_effect=start):
            root = Path(temp)
            emitted = run_reference(PIN, key, candidate['model'], candidate['capabilities'], renode=Path('/toy'),
                                    image=Path('/toy'), output_dir=root/'emission', phase='final',
                                    private_dir=root/'emission-native')
            package = Path(emitted['package_dir'])
            if edit is not None:
                edit(package)
            try:
                return reference_execute(PIN, key, candidate['capabilities'], renode=Path('/toy'), image=Path('/toy'),
                                         output_dir=root/'final', package=package,
                                         expected_package_sha256=digest(package), private_dir=root/'native',
                                         healthy_device_failures=healthy_device_failures)
            finally:
                raw = root/'final'/'raw.json'
                self.raw = json.loads(raw.read_text(encoding='utf-8')) if raw.is_file() else None

    def test_a_correct_driver_passes_every_step_including_a_refusal_and_a_silence(self):
        self.assertEqual(self.grade(REFERENCE_A)['grade']['verdict'], 'passed')

    def test_a_driver_that_declines_an_out_of_range_mode_before_sending_matches_the_devices_refusal(self):
        # REFERENCE_B bounds mode to 0..3 in its capabilities, so bind_task refuses 9 without I/O and the mode stays 2.
        self.assertEqual(self.grade(REFERENCE_B)['grade']['verdict'], 'passed')

    def test_a_driver_that_reports_the_devices_refusal_as_a_value_fails_that_step(self):
        graded = self.grade(MUTANT_ERR_AS_VALUE)
        self.assertIn('s1/1/answer', [c['id'] for c in graded['grade']['checks'] if c['passed'] is False])

    def test_an_unreadable_cell_or_an_unverified_reset_is_undecided_never_failed(self):
        self.assertEqual(self.grade(REFERENCE_A, unreadable={0x20000000})['grade']['verdict'], 'unknown')
        self.assertEqual(self.grade(REFERENCE_A, reset_mode=1)['grade']['verdict'], 'unknown')

    def test_a_package_host_fault_on_a_healthy_device_is_the_drivers_error(self):
        self.assertEqual(classify_answer({'ok': False, 'transcript': [], 'error': {'fault': 'model', 'reported_fault': 'host',
                                          'classified': 'healthy-device'}}, STEP), 'error')

    def test_a_reference_passes_on_the_answers_the_evaluator_saw_with_a_record_for_every_check(self):
        # Catches a pass that never exercised the refusal, the silence or the state reads it claims to grade.
        evaluation = self.grade(REFERENCE_A)
        answers = {(e['sequence'], e['step']): e['answer'] for e in self.raw['events'] if 'step' in e}
        self.assertEqual(answers, {('s1', 1): 'refusal', ('s1', 2): 'value', ('s1', 3): 'value', ('s1', 4): 'value',
                                   ('s2', 1): 'no_reply', ('s2', 2): 'value', ('s2', 3): 'value'})
        self.assertEqual(len(evaluation['records']), len(evaluation['contract']['checks']))
        self.assertTrue(all('undecided' not in record for record in evaluation['records']))

    def test_an_unverified_reset_leaves_its_sequence_undecided_and_a_later_unreadable_cell_only_its_own_check(self):
        for device in ({'reset_mode': 1}, {'unreadable': {MODE_ADDRESS}}):
            with self.subTest(**device):
                checks = self.grade(REFERENCE_A, **device)['grade']['checks']
                self.assertEqual({(c['passed'], c.get('reason')) for c in checks}, {(None, 'unverified reset')})
                # No driver call is made on a device whose reset was not verified.
                self.assertEqual([e for e in self.raw['events'] if 'step' in e], [])
        # The mode cannot be read while the device is in mode 1: steps 3 and 4 of s1, after a verified reset.
        graded = self.grade(REFERENCE_A, unreadable_in_mode=1)['grade']
        undecided = {c['id']: c['reason'] for c in graded['checks'] if c['passed'] is None}
        self.assertEqual((graded['verdict'], undecided), ('unknown', {'s1/3/state:mode': 'unreadable state',
                                                                      's1/4/state:mode': 'unreadable state'}))

    def test_a_driver_that_changes_the_state_before_it_reports_the_devices_refusal_fails_that_steps_state(self):
        # Its set first sends MODE 0, so the device's ERR to MODE 9 is a refusal, but the mode is no longer 2.
        candidate = copy.deepcopy(REFERENCE_A)
        steps = candidate['model']['operations']['write_mode']['steps']
        steps.insert(0, {**steps[0], 'tx': [{'text': 'MODE 0\n'}], 'capture': 'first'})
        checks = {c['id']: c['passed'] for c in self.grade(candidate)['grade']['checks']}
        self.assertEqual((checks['s1/1/answer'], checks['s1/1/state:mode']), (True, False))

    def test_a_printed_host_fault_on_a_healthy_device_fails_the_driver_and_a_dead_device_leaves_no_grade(self):
        # Catches a package that escapes its grade by labelling its own failure the host's, and a dead evaluator device
        # read as the driver's failure or as an undecided step.
        printed = lambda package: add_to_bundled_runtime(package, PRINTS_A_HOST_FAULT)
        graded = self.grade(REFERENCE_A, edit=printed, healthy_device_failures=True)['grade']
        self.assertEqual(graded['verdict'], 'failed')
        failed = sorted(c['id'] for c in graded['checks'] if c['passed'] is False)
        self.assertEqual(failed, ['s1/1/answer', 's1/2/answer', 's1/2/out:mode', 's1/3/answer', 's1/3/state:mode',
                                  's1/4/answer', 's1/4/out:mode', 's1/4/state:mode', 's2/1/answer', 's2/2/answer',
                                  's2/2/state:mode', 's2/3/answer', 's2/3/out:mode', 's2/3/state:mode'])
        call = next(e for e in self.raw['events'] if 'step' in e)
        self.assertEqual((call['device_healthy'], call['result']['error']['reported_fault']), (True, 'host'))
        # Calibration never takes an outage for a driver's failure, and a device that died during a call is no grade.
        with self.assertRaisesRegex(RuntimeError, 'behavioral grading is unavailable'):
            self.grade(REFERENCE_A, edit=printed)
        with self.assertRaisesRegex(RuntimeError, 'behavioral grading is unavailable'):
            self.grade(REFERENCE_A, healthy_device_failures=True, dies_on='PING')
        self.assertIs(self.raw['incomplete'], True)

    def run_steps(self, device, invoke):
        """The hidden sequences of the fixture key run on `device` with the scripted package call `invoke`."""
        truth = {**FIXTURE_ANSWER_KEY, 'artifact_sha256': 'a' * 64}
        self.addCleanup(device.close)
        with tempfile.TemporaryDirectory() as temp:
            return generated.execute(device.session(truth['recipe']), invoke, generated.contract(PIN, truth, 'final'),
                                     truth, Path(temp)/'raw.json', healthy_device_failures=True)

    def test_a_package_process_that_could_not_start_is_the_hosts_even_on_a_healthy_device(self):
        spawn = {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [],
                 'error': {'fault': 'host', 'source': 'package-process', 'code': 'spawn', 'message': 'no interpreter'}}
        device = FixtureDevice()
        with self.assertRaisesRegex(RuntimeError, 'behavioral grading is unavailable'):
            self.run_steps(device, lambda *call: dict(spawn))
        # A reset, its observation and one read of the state, then the call's window; no health check follows.
        self.assertEqual(device.commands, ['pause', 'machine Reset', 'cpu PC', 'sysbus ReadDoubleWord 0x20000000',
                                           'start', 'pause'])

    def test_a_device_that_stops_before_its_state_is_read_leaves_no_grade(self):
        # Catches a dead evaluator device taken for an unreadable cell, which would leave an unknown grade that settles
        # its suite slot instead of an unscored one. The machine stops in the last sequence, so no later reset fails.
        device = FixtureDevice()
        def stops_the_machine(task, inputs, grants):
            device.dead = device.dead or task == 'ping'
            return {'ok': True, 'outputs': {}, 'units': {}, 'transcript': []}
        with self.assertRaisesRegex(RuntimeError, 'the device state is unavailable'):
            self.run_steps(device, stops_the_machine)

    def test_every_mutant_of_the_fixture_fails_exactly_its_expected_checks(self):
        for mutant in FIXTURE_ANSWER_KEY['mutants']:
            with self.subTest(mutant['id']):
                graded = self.grade(mutant)['grade']
                self.assertEqual(graded['verdict'], 'failed')
                self.assertEqual(sorted(c['id'] for c in graded['checks'] if c['passed'] is False),
                                 sorted(mutant['expected_failed_checks']))


class AnswerTests(unittest.TestCase):
    def result(self, **changes):
        """A runtime result of reference A's write_mode, after its identity: the device replied ERR."""
        entry = {'operation': 'write_mode', 'step': 0, 'op': 'exchange', 'tx_hex': b'MODE 9\n'.hex(),
                 'rx_hex': b'ERR\n'.hex(), 'status': 'completed', 'device_rejected': True}
        return {'ok': False, 'operation': 'write_mode', 'identity_verified': True, 'transport_open_attempted': True,
                'transcript': [{**entry, 'operation': 'read_mode', 'rx_hex': b'MODE:2\n'.hex()}, entry],
                'error': {'code': 'execution_error', 'message': 'response expectation mismatch', 'fault': 'model'},
                **changes}

    def test_each_answer_is_classified_from_what_the_runtime_recorded(self):
        silent = {'operation': 'ping', 'step': 0, 'op': 'exchange', 'tx_hex': b'PING\n'.hex(), 'rx_hex': '',
                  'status': 'attempted'}
        timeout = {'code': 'timeout', 'message': 'timed out', 'fault': 'model'}
        rows = [
            ('value', {'ok': True, 'outputs': {'mode': 2}}),
            ('refusal', self.result()),  # the device's own refusal bytes
            ('refusal', {'ok': False, 'transcript': [], 'transport_open_attempted': False,  # bind_task's, before I/O
                         'error': {'fault': 'model', 'code': 'capability_mapping'}}),
            ('refusal', self.result(transcript=[], error={'fault': 'operator', 'code': 'precondition_failed'})),
            ('no_reply', self.result(operation='ping', transcript=[self.result()['transcript'][0], silent],
                                     error=timeout)),
            ('error', self.result(error={'fault': 'host', 'code': 'io'})),  # a fault the package labels the host's
            ('error', self.result(transport_open_attempted=False, error={'fault': 'host', 'code': 'binding'})),
            # An identity that timed out on a device the evaluator found healthy: the driver never sent the request,
            # but the fault was the host's as the package reported it, so this is no refusal.
            ('error', self.result(identity_verified=False, transcript=[{**silent, 'operation': 'read_mode'}],
                                  error={**timeout, 'fault': 'model', 'reported_fault': 'host',
                                         'classified': 'healthy-device'})),
            ('error', self.result(transcript=[self.result()['transcript'][0], {**self.result()['transcript'][1],
                                                                               'rx_hex': b'BUSY\n'.hex()}])),
            ('error', self.result(transcript=[], operation=None)),  # no requested operation to have declined
            ('error', self.result(identity_verified=False, operation='read_mode', error=timeout,
                                  transcript=[{**silent, 'operation': 'read_mode'}])),  # its identity is unverified
            ('error', self.result(operation='ping', transcript=[silent], error={**timeout, 'code': 'limit'})),
            ('error', self.result(operation='ping', error=timeout,
                                  transcript=[{**silent, 'rx_hex': '45', 'status': 'completed'}])),
            ('error', {'ok': False}), ('error', {'ok': 'yes', 'error': 'unreadable', 'transcript': 'none'})]
        for expected, result in rows:
            with self.subTest(result=result):
                self.assertEqual(classify_answer(result, STEP), expected)
        # The device's ERR is a refusal only where the step names it as the device's refusal.
        self.assertEqual(classify_answer(self.result(), FIXTURE_ANSWER_KEY['sequences'][0]['steps'][2]), 'error')


class AnswerKeyTests(unittest.TestCase):
    def test_the_contract_checks_each_steps_answer_its_outputs_and_every_state_cell(self):
        contract = generated.contract(PIN, {**FIXTURE_ANSWER_KEY, 'artifact_sha256': 'a' * 64}, 'final')
        rows = {check['id']: check for check in contract['checks']}
        self.assertEqual((contract['schema'], contract['artifact_sha256'], len(rows)),
                         ('benchmark-behavior/1', 'a' * 64, 17))
        self.assertEqual([i for i in rows if i.startswith('s1/1/')], ['s1/1/answer', 's1/1/state:mode'])
        self.assertEqual([i for i in rows if i.startswith('s1/2/')],
                         ['s1/2/answer', 's1/2/out:mode', 's1/2/state:mode'])
        pick = lambda identifier, *keys: tuple(rows[identifier][key] for key in keys)
        self.assertEqual(pick('s2/1/answer', 'kind', 'expected', 'channel'), ('text', 'no_reply', 'runtime-transcript'))
        self.assertEqual(pick('s1/2/out:mode', 'kind', 'expected', 'absolute_tolerance'), ('number', 2, 0))
        self.assertEqual(pick('s1/3/state:mode', 'kind', 'expected', 'absolute_tolerance', 'channel'),
                         ('number', 1, 0, 'device-state'))
        self.assertTrue(all(check['revision'] == 0 for check in contract['checks']))
        # The device-state channel is one the grader accepts; a channel it does not know is still refused.
        self.assertEqual(validate_records(contract, [])['verdict'], 'failed')
        with self.assertRaisesRegex(ValueError, 'channel'):
            validate_records({**contract, 'checks': [{**contract['checks'][0], 'channel': 'elsewhere'}]}, [])

    def test_an_answer_key_with_an_unknown_missing_or_malformed_field_is_refused(self):
        def change(edit):
            key = copy.deepcopy(FIXTURE_ANSWER_KEY)
            edit(key)
            return key
        step = lambda key, index=0: key['sequences'][0]['steps'][index]
        defects = {
            'unknown field': lambda key: key.update(extra=1),
            'missing nonce': lambda key: key.pop('nonce'),
            'short nonce': lambda key: key.update(nonce='ab' * 31),
            'uppercase nonce': lambda key: key.update(nonce='AB' * 32),
            'another schema': lambda key: key.update(schema='generated-answer-key/2'),
            'no reset commands': lambda key: key['recipe'].update(reset_commands=[]),
            'no observations': lambda key: key['recipe'].update(observations={}),
            'another backend': lambda key: key['recipe'].update(backend='native-sim'),
            'a 64-bit cell': lambda key: key['state']['mode'].update(width=64),
            'a negative address': lambda key: key['state']['mode'].update(address=-4),
            'a cell with an extra field': lambda key: key['state']['mode'].update(signed=False),
            'a reset that misses a cell': lambda key: key.update(reset={}),
            'a reset beyond its width': lambda key: key['reset'].update(mode=2 ** 32),
            'a step with an unknown field': lambda key: step(key).update(note='x'),
            'an answer that is no answer': lambda key: step(key).update(answer='error'),
            'outputs on a refusal': lambda key: step(key).update(outputs={'mode': {'value': 2, 'tolerance': 0}}),
            'a refusal witness on a value': lambda key: step(key, 1).update(refusal_rx_hex='00'),
            'a witness that is no hex': lambda key: step(key).update(refusal_rx_hex='ERR'),
            'a negative tolerance': lambda key: step(key, 1)['outputs']['mode'].update(tolerance=-1),
            'an unknown grant': lambda key: step(key).update(grants=['everything']),
            'an input that is no scalar': lambda key: step(key).update(inputs={'mode': [9]}),
            'a step state beyond its width': lambda key: step(key).update(state={'mode': -1}),
            'a duplicate sequence': lambda key: key['sequences'].append(copy.deepcopy(key['sequences'][0])),
            'a sequence without steps': lambda key: key['sequences'][1].update(steps=[]),
            'a sequence id with a slash': lambda key: key['sequences'][1].update(id='s/2'),
            'one reference': lambda key: key['references'].pop('b'),
            'a mutant failing an unknown check':
                lambda key: key['mutants'][0].update(expected_failed_checks=['s9/1/answer']),
            'a duplicate mutant': lambda key: key['mutants'].append(copy.deepcopy(key['mutants'][0])),
            'a mission state of no cell': lambda key: key.update(mission={'state': {}})}
        artifact = {'artifact_sha256': 'a' * 64}
        self.assertEqual(len(generated.contract(PIN, {**FIXTURE_ANSWER_KEY, **artifact}, 'final')['checks']), 17)
        for name, edit in defects.items():
            with self.subTest(name), self.assertRaises(ValueError):
                generated.contract(PIN, {**change(edit), **artifact}, 'final')
        with self.assertRaisesRegex(ValueError, 'final'):
            generated.contract(PIN, {**FIXTURE_ANSWER_KEY, **artifact}, 'diagnostic')


class RegistrationTests(unittest.TestCase):
    def test_the_generated_family_is_registered_unavailable_in_listings_and_shows_workers_no_instrument(self):
        from generative_driver.benchmark_support.instruments import family_for, public_observation
        from generative_driver.benchmark_support.registrations import CaseRegistration, family_module, registration_for
        from generative_driver.benchmark_support.registry import CURRENT_VERSIONS, case_descriptors
        self.assertEqual(registration_for('generated'),
                         CaseRegistration('generated', 'generated', '1', 'emulator-v2', 'benchmark-case/2',
                                          native_module='generated', truth_schema='generated-answer-key/1',
                                          calibration_kind='set'))
        self.assertIs(family_for('generated'), generated)
        self.assertIs(family_module('generated'), generated)
        self.assertEqual(CURRENT_VERSIONS['generated'], '1')
        rows = {row['id']: row for row in case_descriptors()}
        self.assertEqual({**rows['generated'], 'id': None}, {**rows['tq9-v3'], 'id': None})
        self.assertEqual(rows['generated']['status'], 'pending')
        # Workers see only a generated device's UART: its monitor reads project to nothing.
        self.assertEqual(public_observation('generated', {'values': {'pc': 0x08000100}}), {'values': {}, 'units': {}})
        self.assertEqual(generated.REQUIRED_MUTANTS, frozenset({'wrong-scale', 'constant-output', 'wrong-state'}))
        self.assertIsNone(generated.reuse_passed([], {}))

    def test_the_generated_identity_covers_the_core_and_the_generated_family(self):
        from generative_driver.benchmark_support.grader_identity import grader_closure, grader_identity
        core = {name for name in grader_closure(families=()) if not name.startswith('interface_runtime')}
        identity = grader_identity(case_id='generated')
        self.assertEqual(identity['scope'], 'generated')
        self.assertEqual(set(identity['modules']), core | {'benchmark_support.generated'})


if __name__ == '__main__':
    unittest.main()
