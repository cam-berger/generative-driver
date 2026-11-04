"""A generated device's frozen driver graded step by step. Each package is emitted by the reference runner against the
hand-written fixture device, never generator output, then graded by the evaluator's reference_execute on a fresh fixture
device through its Renode-like monitor."""
import copy
import hashlib
import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generated_fixtures import (BOUNDED, FIXTURE_ANSWER_KEY, MODE_ADDRESS, MODE_UNIT, MUTANT_ERR_AS_VALUE, REFERENCE_A,
                                REFERENCE_B, STEP, FixtureDevice, fixture_mission, fixture_start)
from generative_driver.benchmark_support import generated, generated_device
from generative_driver.benchmark_support.behavior import validate_records
from generative_driver.benchmark_support.evaluator import reference_execute
from generative_driver.benchmark_support.generated import classify_answer
from generative_driver.benchmark_support.integrity import digest
from generative_driver.benchmark_support.reference_calibration import run_reference

PIN = {'case_id': 'generated', 'scenario_id': 'original', 'case_seed': 0, 'revision': 0}


def prints(result):
    """Code that, appended to a package's bundled runtime, makes every call of the package print `result` and exit."""
    return ('\nimport sys\nsys.stdout.write(%r)\nsys.exit(0)\n' % json.dumps(result)).encode()


# A package result that labels its own failure the host's, and one that claims the label only the grader writes for a
# process that could not start, the one host fault the grader takes as the host's on any device.
PRINTED_HOST_FAULT = {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [],
                      'error': {'fault': 'host', 'code': 'io', 'message': 'printed'}}
FORGED_SOURCE = {**PRINTED_HOST_FAULT,
                 'error': {**PRINTED_HOST_FAULT['error'], 'source': 'package-process', 'code': 'spawn'}}
PRINTS_A_HOST_FAULT = prints(PRINTED_HOST_FAULT)


def call_package(candidate, task, inputs, grants, *, preconditions=None, before=None):
    """One call of `candidate`'s package, as the evaluator's invoker returns it. The package is emitted by the toolkit
    against a fresh fixture device as `run_reference` emits one, with `preconditions` ({operation: [condition]}) added
    to its authored driver contract, and called on that device, running, once `before(device)` has run."""
    from generative_driver.benchmark_support.calibration import reference_model_bytes
    from generative_driver.benchmark_support.emulated_actions import canonical_package_invoker
    from generative_driver.toolkit import call_tool
    from interface_runtime.contracts import from_model
    device = FixtureDevice()
    device.running = True
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            model = {**candidate['model'], 'channel': {'type': 'tcp'}}
            (root/'model').mkdir()
            (root/'model/model.json').write_bytes(reference_model_bytes(model))
            if preconditions:
                contract, protocol = from_model(model)
                for operation, conditions in preconditions.items():
                    contract['operations'][operation]['preconditions'] = conditions
                for name, document in (('contract.json', contract), ('protocol.json', protocol)):
                    (root/'model'/name).write_text(json.dumps(document), encoding='utf-8')
            tools = {'run_dir': str(root/'tools'), 'model_dir': str(root/'model')}
            probed = call_tool('probe_run', {**tools, 'operation': model['identity']['operation'], 'parameters': {},
                                             'binding': device.binding, 'n': 1})
            emitted = call_tool('emit_package', {**tools, 'probe': probed['probe']})
            if before is not None:
                before(device)
            invoke = canonical_package_invoker(Path(emitted['package_dir']), candidate['capabilities'], device.binding)
            return invoke(task, inputs, grants)
    finally:
        device.close()


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
        # BOUNDED bounds mode to 0..3 in its model, so bind_task refuses 9 without I/O and the mode stays 2.
        self.assertEqual(self.grade(BOUNDED)['grade']['verdict'], 'passed')

    def test_a_driver_that_reads_the_devices_refusal_as_a_line_matches_it_too(self):
        # REFERENCE_B forwards 9 and reads the reply as lines: it captures the same MODE ERR as A's read until a line feed.
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
                                   ('s2', 1): 'no_reply', ('s2', 2): 'value', ('s2', 3): 'value', ('s2', 4): 'value'})
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
        self.assertEqual((graded['verdict'], undecided), ('unknown', {'s1/3/state:c1': 'unreadable state',
                                                                      's1/4/state:c1': 'unreadable state'}))

    def test_a_driver_that_changes_the_state_before_it_reports_the_devices_refusal_fails_that_steps_state(self):
        # Its set first sends MODE 0, so the device's ERR to MODE 9 is a refusal, but the mode is no longer 2.
        candidate = copy.deepcopy(REFERENCE_A)
        steps = candidate['model']['operations']['write_mode']['steps']
        steps.insert(0, {**steps[0], 'tx': [{'text': 'MODE 0\n'}], 'capture': 'first'})
        checks = {c['id']: c['passed'] for c in self.grade(candidate)['grade']['checks']}
        self.assertEqual((checks['s1/1/answer'], checks['s1/1/state:c1']), (True, False))

    def test_a_printed_host_fault_on_a_healthy_device_fails_the_driver_and_a_dead_device_leaves_no_grade(self):
        # Catches a package that escapes its grade by labelling its own failure the host's, and a dead evaluator device
        # read as the driver's failure or as an undecided step.
        printed = lambda package: add_to_bundled_runtime(package, PRINTS_A_HOST_FAULT)
        graded = self.grade(REFERENCE_A, edit=printed, healthy_device_failures=True)['grade']
        self.assertEqual(graded['verdict'], 'failed')
        failed = sorted(c['id'] for c in graded['checks'] if c['passed'] is False)
        self.assertEqual(failed, ['s1/1/answer', 's1/2/answer', 's1/2/out:mode', 's1/3/answer', 's1/3/state:c1',
                                  's1/4/answer', 's1/4/out:mode', 's1/4/state:c1', 's2/1/answer', 's2/2/answer',
                                  's2/2/state:c1', 's2/3/answer', 's2/3/out:mode', 's2/3/state:c1', 's2/4/answer',
                                  's2/4/state:c1'])
        call = next(e for e in self.raw['events'] if 'step' in e)
        self.assertEqual((call['device_healthy'], call['result']['error']['reported_fault']), (True, 'host'))
        # Calibration never takes an outage for a driver's failure, and a device that died during a call is no grade.
        with self.assertRaisesRegex(RuntimeError, 'behavioral grading is unavailable'):
            self.grade(REFERENCE_A, edit=printed)
        with self.assertRaisesRegex(RuntimeError, 'behavioral grading is unavailable'):
            self.grade(REFERENCE_A, healthy_device_failures=True, dies_on='PING')
        self.assertIs(self.raw['incomplete'], True)

    def test_a_driver_that_reports_another_unit_fails_its_output_checks(self):
        # Catches an output graded by its number alone: the mission states the unit, and the driver must report it.
        candidate = copy.deepcopy(REFERENCE_A)
        candidate['model']['operations']['read_mode']['outputs']['mode']['unit'] = 'count'
        candidate['capabilities']['tasks']['get_mode']['outputs']['mode']['unit'] = 'count'
        failed = {c['id']: c['reason'] for c in self.grade(candidate)['grade']['checks'] if c['passed'] is False}
        self.assertEqual(failed, dict.fromkeys(['s1/2/out:mode', 's1/4/out:mode', 's2/3/out:mode'], 'unit mismatch'))

    def test_a_forged_process_label_or_a_malformed_result_is_the_drivers_error(self):
        # Catches a package that escapes its grade by printing the label only the grader writes for a process that could
        # not start, and one whose malformed result made the grader raise, or left its evidence unwritable, instead of
        # grading it. The evidence keeps the first 4096 characters of the print, its length and its SHA-256, however
        # long the print.
        from generative_driver.benchmark_support.emulated_actions import MAX_RESULT_DEPTH
        # One level beyond the cap: the result is the first level, its outputs the second, and arrays fill the rest.
        too_deep = []
        for _ in range(MAX_RESULT_DEPTH - 2):
            too_deep = [too_deep]
        for name, printed in (('forged source', FORGED_SOURCE),
                              ('outputs', {'ok': True, 'outputs': 'two', 'units': {}, 'transcript': []}),
                              ('a long print', {'ok': True, 'outputs': 'two' * 2000, 'units': {}, 'transcript': []}),
                              ('units', {'ok': True, 'outputs': {'mode': 2}, 'units': ['1'], 'transcript': []}),
                              ('error', {'ok': False, 'error': 'refused', 'transcript': []}),
                              ('transcript', {'ok': False, 'error': {'fault': 'model'}, 'transcript': 'none'}),
                              ('transcript entry', {'ok': False, 'error': {'fault': 'model'}, 'transcript': ['ERR']}),
                              ('not finite', {'ok': True, 'outputs': {'mode': float('nan')}, 'units': {'mode': '1'},
                                              'transcript': []}),
                              ('nested beyond the cap', {'ok': True, 'outputs': {'mode': too_deep},
                                                         'units': {'mode': '1'}, 'transcript': []})):
            with self.subTest(name):
                graded = self.grade(REFERENCE_A, edit=lambda package: add_to_bundled_runtime(package, prints(printed)),
                                    healthy_device_failures=True)['grade']
                self.assertEqual(graded['verdict'], 'failed')
                calls = [event for event in self.raw['events'] if 'step' in event]
                self.assertEqual({call['answer'] for call in calls}, {'error'})
                if name == 'forged source':
                    # The label reaches the grader stripped, so its health check runs as for any host fault.
                    self.assertEqual((calls[0]['device_healthy'], 'source' in calls[0]['result']['error']),
                                     (True, False))
                else:
                    text = json.dumps(printed)
                    self.assertEqual((calls[0]['result']['error']['code'], calls[0]['result']['printed']),
                                     ('malformed_result', {'prefix': text[:4096], 'characters': len(text),
                                                           'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}))
                    if name == 'a long print':
                        self.assertGreater(len(text), 4096)

    @unittest.skipUnless(hasattr(signal, 'SIGKILL'), 'a process sends itself SIGKILL on POSIX only')
    def test_a_package_that_kills_itself_on_a_healthy_device_is_the_drivers_error(self):
        # Catches a package that escapes its grade by dying from a signal of its own: only a process that could not
        # start is the host's whatever the device.
        kills_itself = b'\nimport os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n'
        graded = self.grade(REFERENCE_A, edit=lambda package: add_to_bundled_runtime(package, kills_itself),
                            healthy_device_failures=True)['grade']
        self.assertEqual(graded['verdict'], 'failed')
        call = next(event for event in self.raw['events'] if 'step' in event)
        error = call['result']['error']
        self.assertEqual((call['answer'], call['device_healthy']), ('error', True))
        self.assertEqual((error['code'], error['source'], error['reported_fault']), ('signal', 'package-process', 'host'))

    def run_steps(self, device, invoke):
        """The hidden sequences of the fixture key run on `device` with the scripted package call `invoke`."""
        truth = {**FIXTURE_ANSWER_KEY, 'artifact_sha256': 'a' * 64}
        self.addCleanup(device.close)
        with tempfile.TemporaryDirectory() as temp:
            return generated.execute(device.session(truth['recipe']), invoke, PIN,
                                     generated.contract(PIN, truth, 'final'), truth, Path(temp)/'raw.json',
                                     phase='final', healthy_device_failures=True)

    def test_a_package_process_that_could_not_start_is_the_hosts_even_on_a_healthy_device(self):
        spawn = {'ok': False, 'outputs': {}, 'units': {}, 'transcript': [],
                 'error': {'fault': 'host', 'source': 'package-process', 'code': 'spawn', 'message': 'no interpreter'}}
        device = FixtureDevice()
        with self.assertRaisesRegex(RuntimeError, 'behavioral grading is unavailable'):
            self.run_steps(device, lambda *call: dict(spawn))
        # A reset with its boot interval, its observation and one read of the state, then the call's window; no health
        # check follows.
        self.assertEqual(device.commands, ['pause', 'machine Reset', 'emulation RunFor "00:00:00.1"', 'cpu PC',
                                           'sysbus ReadDoubleWord 0x20000000', 'start', 'pause'])

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
    """classify_answer on the results the runtime really returns: each package is emitted by the toolkit and called by
    the evaluator's invoker on a fixture device. A forged result changes one field of a real one, as a package that
    misreports would."""

    @classmethod
    def setUpClass(cls):
        def mode_is(mode, operation='read_mode'):
            return {'id': operation + '-mode', 'description': 'Mode %d first.' % mode, 'operator': 'eq', 'expected': mode,
                    'observation': {'operation': operation, 'parameters': {}, 'output': 'mode'}}
        # Its identity insists on mode 2; another read asks a question the device refuses.
        strict, guarded = copy.deepcopy(REFERENCE_A), copy.deepcopy(REFERENCE_A)
        strict['model']['operations']['read_mode']['steps'][0]['expect'] = {'equals_hex': b'MODE:2\n'.hex()}
        status = copy.deepcopy(guarded['model']['operations']['read_mode'])
        status['steps'][0]['tx'] = [{'text': 'STATUS?\n'}]
        guarded['model']['operations']['status'] = status
        guarded['model']['provenance'].append({'item': 'operations.status', 'source': 'hand-written fixture'})
        set_one = ('set_mode', {'mode': 1}, ['write'])
        cls.real = {
            'value': call_package(REFERENCE_A, 'get_mode', {}, []),
            'device refusal': call_package(REFERENCE_A, 'set_mode', {'mode': 9}, ['write']),
            'bound refusal': call_package(BOUNDED, 'set_mode', {'mode': 9}, ['write']),
            'precondition refusal': call_package(REFERENCE_A, *set_one, preconditions={'write_mode': [mode_is(0)]}),
            'silence': call_package(REFERENCE_A, 'ping', {}, []),
            'identity mismatch': call_package(strict, *set_one, before=lambda device: setattr(device, 'mode', 1)),
            'precondition unavailable': call_package(guarded, *set_one,
                                                     preconditions={'write_mode': [mode_is(2, 'status')]}),
            'host fault': call_package(REFERENCE_A, 'ping', {}, [],
                                       before=lambda device: setattr(device, 'running', False))}

    def test_the_real_results_have_the_shapes_the_rules_read(self):
        # The runtime's own shapes: catches a rule written against a combination it never returns.
        real, shape = self.real, lambda name, *keys: tuple(self.real[name].get(key) for key in keys)
        fault = lambda name: (real[name]['error']['code'], real[name]['error']['fault'])
        self.assertEqual(shape('device refusal', 'identity_verified', 'transport_open_attempted'), (True, True))
        self.assertEqual(real['device refusal']['transcript'][-1]['rx_hex'], b'MODE ERR\n'.hex())
        self.assertEqual(shape('bound refusal', 'transport_open_attempted', 'transcript'), (False, []))
        self.assertEqual(fault('bound refusal'), ('capability_mapping', 'model'))
        self.assertEqual(fault('precondition refusal'), ('precondition_failed', 'operator'))
        self.assertEqual(shape('precondition refusal', 'identity_verified', 'transport_open_attempted', 'transcript'),
                         (False, True, []))
        self.assertEqual([(o['result']['ok'], o['result']['identity_verified'])
                          for o in real['precondition refusal']['observations']], [(True, True)])
        self.assertEqual(fault('silence'), ('timeout', 'model'))
        self.assertEqual(fault('identity mismatch'), ('execution_error', 'model'))
        self.assertEqual(shape('identity mismatch', 'identity_verified', 'transport_open_attempted'), (False, True))
        self.assertEqual({entry['operation'] for entry in real['identity mismatch']['transcript']}, {'read_mode'})
        self.assertEqual(fault('precondition unavailable'), ('precondition_unavailable', 'model'))
        self.assertEqual(fault('host fault')[1], 'host')

    def test_each_answer_is_classified_from_what_the_runtime_returned(self):
        from generative_driver.benchmark_support.emulated_actions import graded_result
        real = self.real
        forged = lambda name, **changes: {**real[name], **changes}
        failed_read = [{**observation, 'result': {**observation['result'], 'ok': False}}
                       for observation in real['precondition refusal']['observations']]
        healthy = FixtureDevice()
        self.addCleanup(healthy.close)
        rewritten = graded_result(healthy.session(FIXTURE_ANSWER_KEY['recipe']), real['host fault'], {},
                                  healthy_device_failures=True)
        rows = [
            ('value', real['value']),
            ('refusal', real['device refusal']),  # the device's own ERR, after a verified identity
            ('refusal', real['bound refusal']),  # declined by its bounds before any contact
            ('refusal', real['precondition refusal']),  # declined after reading the device's state
            ('no_reply', real['silence']),
            ('error', real['identity mismatch']),
            ('error', real['precondition unavailable']),
            ('error', real['host fault']),  # a fault the package labels the host's
            ('error', rewritten),  # the same on a device the evaluator found healthy
            ('error', forged('device refusal', transport_open_attempted=False)),  # contact all the same
            ('error', forged('device refusal', identity_verified=False)),
            ('error', forged('precondition refusal', observations=failed_read)),
            ('error', forged('bound refusal', observations=real['precondition refusal']['observations'])),
            ('error', {'ok': False}), ('error', {'ok': 'yes', 'error': 'unreadable', 'transcript': 'none'})]
        for expected, result in rows:
            with self.subTest(result=result):
                self.assertEqual(classify_answer(result, STEP), expected)
        self.assertEqual(rewritten['error']['reported_fault'], 'host')
        # The device's ERR is a refusal only where the step names it as the device's refusal.
        self.assertEqual(classify_answer(real['device refusal'], FIXTURE_ANSWER_KEY['sequences'][0]['steps'][2]),
                         'error')


class AnswerKeyTests(unittest.TestCase):
    def test_the_contract_checks_each_steps_answer_its_outputs_and_every_state_cell(self):
        contract = generated.contract(PIN, {**FIXTURE_ANSWER_KEY, 'artifact_sha256': 'a' * 64}, 'final')
        rows = {check['id']: check for check in contract['checks']}
        self.assertEqual((contract['schema'], contract['artifact_sha256'], len(rows)),
                         ('benchmark-behavior/1', 'a' * 64, 19))
        self.assertEqual([i for i in rows if i.startswith('s1/1/')], ['s1/1/answer', 's1/1/state:c1'])
        self.assertEqual([i for i in rows if i.startswith('s1/2/')],
                         ['s1/2/answer', 's1/2/out:mode', 's1/2/state:c1'])
        pick = lambda identifier, *keys: tuple(rows[identifier][key] for key in keys)
        self.assertEqual(pick('s2/1/answer', 'kind', 'expected', 'channel'), ('text', 'no_reply', 'runtime-transcript'))
        # An output's check expects the unit the public mission states for it.
        mission_unit = fixture_mission('0000d001')['tasks']['get_mode']['outputs']['mode']['unit']
        self.assertEqual(pick('s1/2/out:mode', 'kind', 'expected', 'absolute_tolerance', 'unit'),
                         ('number', 2, 0, mission_unit))
        self.assertEqual(pick('s1/3/state:c1', 'kind', 'expected', 'absolute_tolerance', 'channel'),
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
            'another schema': lambda key: key.update(schema='generated-answer-key/1'),
            'no reset commands': lambda key: key['recipe'].update(reset_commands=[]),
            'no observations': lambda key: key['recipe'].update(observations={}),
            'another backend': lambda key: key['recipe'].update(backend='native-sim'),
            'a 64-bit cell': lambda key: key['state']['c1'].update(width=64),
            'a negative address': lambda key: key['state']['c1'].update(address=-4),
            'a cell with an extra field': lambda key: key['state']['c1'].update(signed=False),
            'a reset that misses a cell': lambda key: key.update(reset={}),
            'a reset beyond its width': lambda key: key['reset'].update(c1=2 ** 32),
            'a step with an unknown field': lambda key: step(key).update(note='x'),
            'an answer that is no answer': lambda key: step(key).update(answer='error'),
            'outputs on a refusal':
                lambda key: step(key).update(outputs={'mode': {'value': 2, 'tolerance': 0, 'unit': MODE_UNIT}}),
            'a refusal witness on a value': lambda key: step(key, 1).update(refusal_rx_hex='00'),
            # A driver that forwards the request on a refusal step receives the device's refusal; without the step's
            # record of it, that correct answer reads as an error.
            'a refusal without its witness': lambda key: step(key).pop('refusal_rx_hex'),
            'a witness that is no hex': lambda key: step(key).update(refusal_rx_hex='ERR'),
            'a negative tolerance': lambda key: step(key, 1)['outputs']['mode'].update(tolerance=-1),
            'an output without a unit': lambda key: step(key, 1)['outputs']['mode'].pop('unit'),
            'an output with an empty unit': lambda key: step(key, 1)['outputs']['mode'].update(unit=''),
            'an output with no unit': lambda key: step(key, 1)['outputs']['mode'].update(unit=None),
            'an unknown grant': lambda key: step(key).update(grants=['everything']),
            'an input that is no scalar': lambda key: step(key).update(inputs={'mode': [9]}),
            'a step state beyond its width': lambda key: step(key).update(state={'c1': -1}),
            'a duplicate sequence': lambda key: key['sequences'].append(copy.deepcopy(key['sequences'][0])),
            'a sequence without steps': lambda key: key['sequences'][1].update(steps=[]),
            'a sequence id with a slash': lambda key: key['sequences'][1].update(id='s/2'),
            'one reference': lambda key: key['references'].pop('b'),
            'a mutant failing an unknown check':
                lambda key: key['mutants'][0].update(expected_failed_checks=['s9/1/answer']),
            'a duplicate mutant': lambda key: key['mutants'].append(copy.deepcopy(key['mutants'][0])),
            'a mission state of no cell': lambda key: key.update(mission={'state': {}})}
        artifact = {'artifact_sha256': 'a' * 64}
        self.assertIs(generated.answer_key(FIXTURE_ANSWER_KEY), FIXTURE_ANSWER_KEY)
        self.assertEqual(len(generated.contract(PIN, {**FIXTURE_ANSWER_KEY, **artifact}, 'final')['checks']), 19)
        for name, edit in defects.items():
            with self.subTest(name), self.assertRaises(ValueError):
                generated.answer_key(change(edit))
        with self.assertRaisesRegex(ValueError, 'final'):
            generated.contract(PIN, {**FIXTURE_ANSWER_KEY, **artifact}, 'diagnostic')


class RegistrationTests(unittest.TestCase):
    def test_the_generated_family_is_registered_unavailable_in_listings_and_shows_workers_no_instrument(self):
        from generative_driver.benchmark_support.families import family_for
        from generative_driver.benchmark_support.instruments import public_observation
        from generative_driver.benchmark_support.missions import validate_mission
        from generative_driver.benchmark_support.registrations import CaseRegistration, registration_for
        from generative_driver.benchmark_support.registry import CURRENT_VERSIONS, case_descriptors
        # The fixture's public mission is one the mission validator accepts, with the unit of its one output.
        self.assertEqual(validate_mission(fixture_mission('0000d001'), 'generated'), fixture_mission('0000d001'))
        self.assertEqual(registration_for('generated'),
                         CaseRegistration('generated', 'generated', '1', 'emulator-v2', 'benchmark-case/2',
                                          native_module='generated', truth_schema='generated-answer-key/2',
                                          calibration_kind='set', execution='actual-agent-emulation',
                                          evidence_track='firmware', approval_scope='emulator',
                                          default_effects=['write', 'actuate']))
        self.assertIs(family_for('generated').execute, generated.execute)
        self.assertEqual(CURRENT_VERSIONS['generated'], '1')
        rows = {row['id']: row for row in case_descriptors()}
        self.assertEqual({**rows['generated'], 'id': None}, {**rows['tq9-v3'], 'id': None})
        self.assertEqual(rows['generated']['status'], 'pending')
        # Workers see only a generated device's UART: its monitor reads project to nothing.
        self.assertEqual(public_observation('generated', {'values': {'pc': 0x08000100}}), {'values': {}, 'units': {}})
        self.assertEqual(generated_device.REQUIRED_MUTANTS, frozenset({'wrong-scale', 'constant-output', 'wrong-state'}))
        self.assertIsNone(generated.reuse_passed([], {}))

    def test_the_generated_identity_covers_the_core_and_the_generated_family(self):
        from generative_driver.benchmark_support.grader_identity import grader_closure, grader_identity
        core = {name for name in grader_closure(families=()) if not name.startswith('interface_runtime')}
        identity = grader_identity(case_id='generated')
        self.assertEqual(identity['scope'], 'generated')
        # The family's grading module, the validator that defines a valid generated device (ADR 0007) and the set table
        # its pin hook reads (Feature 03).
        self.assertEqual(set(identity['modules']),
                         core | {'benchmark_support.generated', 'benchmark_support.generated_device',
                                 'benchmark_support.generated_sets', 'benchmark_support.missions'})


if __name__ == '__main__':
    unittest.main()
