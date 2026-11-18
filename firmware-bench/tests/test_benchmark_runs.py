"""A benchmark run is a supplied run: the bench harness pins, admits and freezes a case on its own side, and
supplies the device, the firmware and the mission to a service that knows no case.

Feature 06 of the untangle Run (docs/features/untangle/06-benchmark-runs.md): the benchmark's side, 06-B15 to
06-B19, the last point of 06-B6, the benchmark's side of 06-B20 to 06-B22, and of 06-B23 what the validator and the
registered sets do. This file is a benchmark item: it runs registered cases. The tool's side of the Feature is in
`tests/test_supplied_runs.py`, which stays with the tool.

Trials run on the installed `tq9-v2` against an in-process configurator, with scripted stage observations, devices
of synthetic peers, a toy answer key and a scripted final phase; never a model, an emulator or a real password.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark_support import evaluator, harness
from generative_driver.benchmark_support.registry import pin_case
from generative_driver.benchmark_support.snapshots import execution_provenance, read_snapshot
from generative_driver.client_interface import verdict_errors
from suite_fixtures import harness_devices
from tool_fixtures import scripted_stages
from test_evaluation import PASS, reference

CASE = 'tq9-v2'
CASE_KEYS = ('case', 'case_options', 'case_pin', 'admission', 'public_inputs')
# What the operator configured for the worker runtime, in the home's `config.json`.
CONFIGURED = {'command': ['scripted-runtime'], 'model': 'configured-model'}


class TrialCase(unittest.TestCase):
    """Trials the bench harness runs on the installed `tq9-v2` against an in-process configurator."""

    def setUp(self):
        from generative_driver.configurator import Controller
        from tq9_workflow_fixture import capabilities, model
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.model = {**model(), 'channel': {'type': 'tcp'}}
        self.mapping = capabilities()
        self.home = self.root / 'home'
        self.home.mkdir(mode=0o700)
        (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': CONFIGURED}}), encoding='utf-8')
        self.controller = Controller(self.home)
        self.addCleanup(self.controller.close)
        self.secrets = harness_devices(self, self.root / 'devices')
        self.calls = []

    def configurator(self, method, params=None, home=None, **kwargs):
        """The harness's requests, made to the in-process configurator and kept in `self.calls`."""
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'ping':
            return {'ok': True}
        try:
            return self.controller.call(method, params or {})
        except Exception as error:
            return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}

    def sent(self, method):
        """The params of every request of `method` the harness made, in order."""
        return [params for name, params in self.calls if name == method]

    def check(self, spec, stage, run_dir, workspace, report, accepted, assignment):
        """Scripted stage observations: probe hands on the model and its mapping, emit a package of that model."""
        if stage == 'probe':
            folder = workspace / 'model'
            folder.mkdir()
            (folder / 'model.json').write_text(json.dumps(self.model, indent=2) + '\n', encoding='utf-8')
            (workspace / 'capabilities.json').write_text(json.dumps(self.mapping), encoding='utf-8')
            return {'artifacts': [str(folder / 'model.json'), str(workspace / 'capabilities.json')], 'checks': []}
        if stage == 'emit':
            package = workspace / 'package'
            (package / 'driver').mkdir(parents=True)
            (package / 'manifest.json').write_text(json.dumps({'schema': 'interface-package/1', 'name': 'scripted'}),
                                                   encoding='utf-8')
            (package / 'driver/model.json').write_text(json.dumps(self.model, indent=2) + '\n', encoding='utf-8')
            return {'artifacts': [str(package)], 'checks': [{'name': 'package_integrity_and_replay', 'passed': True}]}
        return {'artifacts': [], 'checks': []}

    def request(self, name, **more):
        """The request of a trial named `name`, as `harness.launch` hands one to its harness."""
        return {'trial_id': name, 'home': str(self.home), 'goal': harness.GOAL, 'case': CASE,
                'case_options': {'scenario_id': 'original'}, 'effects': ['write', 'actuate'], 'budget_seconds': 120,
                'password_file': self.secrets['password_files'][CASE], 'renode': self.secrets['renode'], **more}

    def trial(self, name='trial', outcome=PASS, runtime=None, **more):
        """One trial through the harness, graded with `outcome` (values, or an exception the evaluator meets): (its
        folder, its record)."""
        folder = self.root / 'bench' / name

        def grade(*args, **kwargs):
            if isinstance(outcome, Exception):
                raise outcome
            return reference(outcome)(*args, **kwargs)
        with scripted_stages(self.controller, check=self.check, runtime=runtime), \
                patch('generative_driver.client.call', side_effect=self.configurator), \
                patch.object(evaluator, 'reference_execute', side_effect=grade):
            record = harness.run_trial(folder, self.request(name, **more))
        return folder, record

    def run_dir(self, record):
        return self.home / 'runs' / record['run_id']

    def result(self, record):
        return self.controller.call('result', {'run_id': record['run_id']})


class HarnessStartTests(TrialCase):
    """The bench harness starts a benchmark run as a supplied run (06-B15)."""

    def test_06_B15_the_start_has_the_device_the_firmware_the_mission_and_the_client_record(self):
        tools = {'ghidra_home': str(self.root / 'ghidra'), 'java_home': str(self.root / 'java')}
        folder, record = self.trial(case_options={'scenario_id': 'original', **tools})
        self.assertIsNotNone(record['run_id'], record)
        [start] = self.sent('start')
        pin = pin_case(CASE, 'original', 0)
        self.assertEqual([key for key in CASE_KEYS if key in start], [])
        device = start['device']
        self.assertEqual(device['schema'], 'device-descriptor/2')
        self.assertEqual(device['firmware_sha256'], pin['image_hashes']['firmware.bin'])
        self.assertEqual(set(device) - {'development_conditions'},
                         {'schema', 'control', 'session_id', 'binding', 'firmware_sha256', 'instrument'})
        self.assertEqual(set(device['instrument']), {'units'})
        # The firmware is the image the harness froze, under its own trial folder.
        frozen = folder / 'benchmark' / 'inputs' / 'firmware.bin'
        self.assertEqual(start['firmware'], {'path': str(frozen), 'sha256': pin['image_hashes']['firmware.bin']})
        self.assertEqual(hashlib.sha256(frozen.read_bytes()).hexdigest(), start['firmware']['sha256'])
        from generative_driver.benchmark import case_root
        mission = json.loads((case_root() / 'missions' / (CASE + '.json')).read_text(encoding='utf-8'))
        self.assertEqual(start['mission'], {key: mission[key] for key in ('tasks', 'reuse_objective')})
        self.assertRegex(start['evaluation_token_sha256'], '^[0-9a-f]{64}$')
        saved = read_snapshot(folder)
        self.assertEqual(start['client_record'],
                         {'case': CASE, 'case_pin': pin, 'snapshot_sha256': saved['snapshot_sha256']})
        self.assertEqual({key: start[key] for key in tools}, tools)
        self.assertEqual(start['request_id'], 'trial')

    def test_06_B15_the_harness_settles_the_executor_configuration_and_keeps_it_in_the_trials_record(self):
        folder, record = self.trial(executor_config={'reasoning_effort': 'high', 'model': 'requested-model'})
        settled = {'command': ['scripted-runtime'], 'model': 'requested-model', 'reasoning_effort': 'high',
                   'runtime': 'codex'}
        [start] = self.sent('start')
        self.assertEqual(start['executor_config'], settled)
        self.assertEqual(harness.read_trial(folder)['executor_config'], settled)
        # The run has the configuration the harness settled, and the snapshot names its provenance.
        agent = self.result(record)['agent']
        self.assertEqual((agent['model'], agent['settings']), ('requested-model', {'reasoning_effort': 'high'}))
        self.assertEqual(read_snapshot(folder)['executed'], execution_provenance(settled, case_ids=(CASE,)))

    def test_06_B15_without_a_requested_configuration_the_run_has_what_the_operator_configured(self):
        folder, record = self.trial()
        [start] = self.sent('start')
        self.assertEqual(start['executor_config'], {**CONFIGURED, 'runtime': 'codex'})
        self.assertEqual(read_snapshot(folder)['executed']['runtime_configuration'], {**CONFIGURED, 'runtime': 'codex'})

    def test_06_B15_the_harness_freezes_the_cases_inputs_in_its_trial_folder(self):
        folder, record = self.trial()
        saved = read_snapshot(folder)
        self.assertEqual(saved['case_pin'], pin_case(CASE, 'original', 0))
        self.assertEqual(sorted(p.name for p in (folder / 'benchmark').iterdir()), ['execution.json', 'inputs'])
        self.assertEqual(sorted(saved['public_files']), sorted(p.name for p in (folder / 'benchmark/inputs').iterdir()))

    def test_06_B15_a_scripted_trial_ends_with_a_graded_verdict(self):
        folder, record = self.trial()
        self.assertEqual(record['status'], 'evaluated', record)
        verdict = self.result(record)['equivalence']
        self.assertEqual((verdict['status'], verdict['equivalent'], verdict['schema']),
                         ('graded', True, 'equivalence/2'))
        self.assertEqual(self.result(record)['progress']['equivalence'], 'graded')


class HarnessAdmissionTests(TrialCase):
    """Before it starts a device, the harness refuses what the service refused (06-B15)."""

    def refused(self, name, **more):
        """The record of a trial that must end with no run, no device and no `start`."""
        folder, record = self.trial(name, **more)
        self.assertEqual((record['status'], record['run_id'], record.get('no_run')), ('failed', None, 'admit-refused'),
                         record)
        self.assertEqual(self.sent('start'), [])
        self.assertEqual(self.device_peers, [], 'a device was started')
        self.assertFalse((folder / 'benchmark').exists(), 'the case was frozen')
        return record['reason']

    def test_06_B15_a_trial_without_the_declared_effect_grants_is_refused(self):
        self.assertEqual(self.refused('effects', effects=['read']),
                         'ValueError: Case evaluator requires the declared write/actuate effect grants before '
                         'admission')

    def test_06_B15_a_case_whose_calibration_has_not_passed_is_refused(self):
        from generative_driver.benchmark_support import registry
        pin = pin_case(CASE, 'original', 0)
        pending = {**pin, 'calibration': {**pin['calibration'], 'status': 'pending'}}
        with patch.object(registry, 'pin_case', return_value=pending):
            reason = self.refused('calibration')
        self.assertEqual(reason, 'ValueError: V2 case requires independently validated passed calibration before a run')

    def test_06_B15_an_admission_whose_evidence_is_not_the_pins_is_refused(self):
        from generative_driver.benchmark_support import registry
        from generative_driver.benchmark_support.truth import UnlockedTruth
        from test_device_provider import RECIPE
        other = lambda pin, password: (UnlockedTruth({'recipe': RECIPE}, pin['truth_sha256']),
                                       {'ok': True, 'evidence_sha256': 'f' * 64})
        with patch.object(registry, 'admitted_truth', side_effect=other):
            reason = self.refused('evidence')
        self.assertEqual(reason, 'ValueError: Calibration admission evidence differs from the public case pin; the '
                                 'bench harness admits an emulated case')

    def test_06_B15_a_suite_childs_pin_that_is_not_the_cases_current_pin_is_refused(self):
        pin = pin_case(CASE, 'original', 0)
        child = {'goal': 'Execute one registered benchmark trial with checked evidence.', 'case': CASE,
                 'executor': 'codex', 'executor_config': {**CONFIGURED, 'runtime': 'codex'},
                 'case_options': {'scenario_id': 'original', 'case_seed': 0}, 'request_id': 'child-1',
                 'effects': ['write', 'actuate'], 'scoped_tool_approval': None, 'budget_seconds': 120}
        differs = 'ValueError: Expected case pin differs from current case inputs'
        stale = {**pin, 'manifest_sha256': 'f' * 64}
        self.assertEqual(self.refused('stale', start={**child, 'case_pin': stale}), differs)
        other_seed = {**child, 'case_options': {'scenario_id': 'original', 'case_seed': 1}, 'case_pin': pin}
        self.assertEqual(self.refused('seed', start=other_seed), differs)

    def test_06_B15_emulator_approval_of_a_case_that_is_no_emulator_profile_is_refused(self):
        from generative_driver.benchmark_support import registry
        real = registry.case_selection

        def no_profile(case_id):
            definition = real(case_id)
            return type(definition)(**{**vars(definition), 'approval_scope': None})
        with patch.object(registry, 'case_selection', side_effect=no_profile):
            reason = self.refused('approval', scoped_tool_approval='emulator')
        self.assertEqual(reason, 'ValueError: scoped_tool_approval supports only a registered emulator profile')


def stopping_probe():
    """A runtime that stops the first probe for the operator, as a runtime that cannot start does."""
    stopped = []

    def runtime(request):
        if request.stage == 'probe' and not stopped:
            stopped.append(request.stage)
            return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                    'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
    return runtime


class SnapshotCase(TrialCase):
    """Trials whose snapshot the harness keeps and checks itself (06-B16)."""

    def password(self):
        from generative_driver.benchmark_support.truth import read_password
        return read_password(self.secrets['password_files'][CASE])

    def again(self, continue_trial, folder, outcome=PASS):
        """The trial's record after `continue_trial` (`harness.resume` or `harness.reattach`) ran on it. A resume
        unlocks the answer key again: here the toy one, under the hash the installed case pins."""
        from generative_driver.benchmark_support.truth import UnlockedTruth
        from test_device_provider import RECIPE
        truth = UnlockedTruth({'recipe': RECIPE}, pin_case(CASE, 'original', 0)['truth_sha256'])
        with scripted_stages(self.controller, check=self.check), \
                patch('generative_driver.client.call', side_effect=self.configurator), \
                patch('generative_driver.benchmark_support.registry.case_truth', return_value=truth), \
                patch.object(evaluator, 'reference_execute', side_effect=reference(outcome)):
            return continue_trial(folder, password=self.password(), renode=self.secrets['renode'])

    def stopped_trial(self, name):
        """A trial whose run stopped at probe for the operator: (its folder, its record)."""
        folder, record = self.trial(name, runtime=stopping_probe())
        self.assertEqual((record['status'], record['run_status']['status']), ('run-stopped', 'blocked'), record)
        return folder, record

    def ungraded_trial(self, name):
        """A trial whose run completed and whose grade the evaluator's host could not give: (its folder, its record)."""
        folder, record = self.trial(name, outcome=OSError('the evaluator host stopped'))
        self.assertEqual(record['status'], 'unscored', record)
        return folder, record

    @staticmethod
    def files(folder):
        """The bytes of every file under `folder`, by its path from there."""
        return {path.relative_to(folder).as_posix(): path.read_bytes()
                for path in sorted(Path(folder).rglob('*')) if path.is_file()}


class SnapshotCopyTests(SnapshotCase):
    def test_06_B16_after_start_the_harness_copies_the_snapshot_into_the_runs_folder(self):
        folder, record = self.trial()
        kept = self.run_dir(record) / 'benchmark'
        frozen = self.files(folder / 'benchmark')
        self.assertEqual(sorted(frozen), ['execution.json', 'inputs/firmware.bin'])
        self.assertEqual({name: self.files(kept)[name] for name in frozen}, frozen)
        self.assertEqual(read_snapshot(self.run_dir(record)), read_snapshot(folder))

    def test_06_B16_a_resumed_trial_copies_the_snapshot_if_it_is_not_there(self):
        import shutil
        folder, record = self.ungraded_trial('ungraded')
        shutil.rmtree(self.run_dir(record) / 'benchmark')
        resumed = self.again(harness.resume, folder)
        self.assertEqual(resumed['status'], 'evaluated', resumed)
        self.assertEqual(read_snapshot(self.run_dir(record)), read_snapshot(folder))

    def test_06_B16_a_reattached_trial_copies_the_snapshot_if_it_is_not_there(self):
        import shutil
        folder, record = self.stopped_trial('stopped')
        shutil.rmtree(self.run_dir(record) / 'benchmark')
        continued = self.again(harness.reattach, folder)
        self.assertEqual(continued['status'], 'evaluated', continued)
        self.assertEqual(read_snapshot(self.run_dir(record)), read_snapshot(folder))


class SnapshotCheckTests(SnapshotCase):
    def refused(self, name, tamper):
        """The reason `reattach` gives after `tamper(trial folder, run folder)`: the run must stay stopped, with no
        device started for it and nothing asked of the configurator but its result."""
        folder, record = self.stopped_trial(name)
        tamper(folder, self.run_dir(record))
        devices = len(self.device_peers)
        self.calls.clear()
        continued = self.again(harness.reattach, folder)
        self.assertEqual(continued['status'], 'failed', continued)
        self.assertEqual(len(self.device_peers), devices, 'a device was started')
        self.assertEqual([name for name, _ in self.calls], ['result'])
        self.assertEqual(self.controller.call('status', {'run_id': record['run_id']})['status'], 'blocked')
        return continued['reason']

    def test_06_B16_reattach_requires_the_runs_snapshot_before_it_starts_a_device(self):
        differs = 'IntegrityError: Execution snapshot hash differs from saved run'

        def changed_input(folder, run_dir):
            (run_dir / 'benchmark/inputs/firmware.bin').write_bytes(b'another image')

        def changed_record(folder, run_dir):
            path = run_dir / 'benchmark/execution.json'
            saved = json.loads(path.read_text(encoding='utf-8'))
            saved['executed']['runtime_configuration']['model'] = 'another-model'
            body = {key: value for key, value in saved.items() if key != 'snapshot_sha256'}
            from generative_driver.benchmark_support.snapshots import canonical_digest
            path.write_text(json.dumps({**body, 'snapshot_sha256': canonical_digest(body)}), encoding='utf-8')

        def changed_trial(folder, run_dir):
            (folder / 'benchmark/inputs/firmware.bin').write_bytes(b'another image')
        for name, tamper in (('run-input', changed_input), ('run-record', changed_record), ('trial', changed_trial)):
            with self.subTest(changed=name):
                self.assertEqual(self.refused(name, tamper), differs)

    def test_06_B16_reattach_compares_the_implementation_with_the_configuration_the_trial_recorded(self):
        def other_configuration(folder, run_dir):
            record = harness.read_trial(folder)
            record['executor_config'] = {**record['executor_config'], 'model': 'another-model'}
            (folder / 'trial.json').write_text(json.dumps(record), encoding='utf-8')
        self.assertEqual(self.refused('provenance', other_configuration),
                         'IntegrityError: Execution snapshot implementation or runtime environment changed; start a '
                         'new run')

    def test_06_B16_an_unchanged_trial_is_reattached_and_graded(self):
        folder, record = self.stopped_trial('unchanged')
        continued = self.again(harness.reattach, folder)
        self.assertEqual(continued['status'], 'evaluated', continued)
        self.assertIs(self.result(record)['equivalence']['equivalent'], True)

    def test_06_B16_before_it_grades_a_snapshot_difference_is_an_integrity_stop(self):
        def changed_input(folder, run_dir):
            (run_dir / 'benchmark/inputs/firmware.bin').write_bytes(b'another image')

        def changed_trial(folder, run_dir):
            (folder / 'benchmark/inputs/firmware.bin').write_bytes(b'another image')
        for name, tamper in (('run-input', changed_input), ('trial', changed_trial)):
            with self.subTest(changed=name):
                folder, record = self.ungraded_trial(name)
                tamper(folder, self.run_dir(record))
                self.calls.clear()
                resumed = self.again(harness.resume, folder)
                self.assertEqual(resumed['status'], 'unscored', resumed)
                [posted] = [params['equivalence'] for params in self.sent('record_evaluation')]
                self.assertEqual(posted['status'], 'unscored')
                self.assertEqual(posted['diff'][0]['reason'],
                                 'integrity: Execution snapshot hash differs from saved run')

    def test_06_B16_the_implementation_is_not_compared_before_grading(self):
        folder, record = self.ungraded_trial('graded-later')
        saved = harness.read_trial(folder)
        (folder / 'trial.json').write_text(json.dumps({**saved, 'executor_config': {'model': 'another-model'}}),
                                           encoding='utf-8')
        resumed = self.again(harness.resume, folder)
        self.assertEqual(resumed['status'], 'evaluated', resumed)


class SuiteTests(TrialCase):
    """Suites run as before (06-B19). Since Feature 07 the bench harness keeps a suite in its suite book and the
    service serves no suite call (docs/features/untangle/07-suites.md): these tests ask the book, and where they
    pinned the service's private start they assert the same of the public calls."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.join_harnesses)

    def join_harnesses(self):
        for thread in getattr(self.controller, '_test_harness_threads', []):
            thread.join(60)

    def suite(self, *outcomes, runtime=None, repetitions=2, intercept=None, **options):
        """The record of a suite harness that ran `repetitions` scripted trials of `tq9-v2`, each graded with the next
        of `outcomes`. `intercept(method, params, forward)` may stand between the harness and the service."""
        from suite_fixtures import drive_suite, scripted_manifest
        queue = list(outcomes)
        manifest = scripted_manifest(repetitions=repetitions)
        with scripted_stages(self.controller, check=self.check, runtime=runtime), \
                patch.object(evaluator, 'reference_execute',
                             side_effect=lambda *args, **kwargs: reference(queue.pop(0))(*args, **kwargs)):
            return drive_suite(self.controller, self.root / 'home-bench' / 'suites', {
                'password_files': self.secrets['password_files'], 'renode': self.secrets['renode'],
                'manifest': manifest, 'executor': 'codex', 'request_id': 'scripted-suite',
                'options': {'scoped_tool_approval': 'emulator', **options}}, calls=self.calls, intercept=intercept)

    def children(self, suite_id):
        """The run of each slot of the suite, in order: (its `result`, its stored spec)."""
        from suite_fixtures import book
        slots = book(self.controller).store.get(suite_id)['trials']
        return [(self.controller.call('result', {'run_id': slot['run_id']}),
                 json.loads(self.controller._row(slot['run_id'])['spec'])) for slot in slots]

    def test_06_B19_the_harness_keys_are_the_five_a_harness_adds(self):
        from generative_driver.benchmark_support import suites
        self.assertEqual(suites.HARNESS_KEYS, ('device', 'firmware', 'mission', 'evaluation_token_sha256',
                                               'client_record'))

    def test_06_B19_a_scripted_suite_of_two_trials_completes_and_each_child_holds_its_client_record(self):
        from suite_fixtures import suite_call
        record = self.suite(PASS, PASS)
        self.assertEqual(record['status'], 'finished', record)
        status = suite_call(self.controller, 'suite_status', {'suite_id': record['suite_id']})
        self.assertEqual((status['status'], status['planned'], status['completed'], status['equivalent']),
                         ('completed', 2, 2, 2), status)
        pin = pin_case(CASE, 'original', 0)
        for result, spec in self.children(record['suite_id']):
            self.assertEqual(set(result['client_record']), {'case', 'case_pin', 'snapshot_sha256'})
            self.assertEqual((result['client_record']['case'], result['client_record']['case_pin']), (CASE, pin))
            self.assertRegex(result['client_record']['snapshot_sha256'], '^[0-9a-f]{64}$')
            self.assertIs(result['equivalence']['equivalent'], True)

    def test_06_B19_a_child_starts_as_a_supplied_run_and_its_recorded_request_is_the_plain_starts_params(self):
        from suite_fixtures import book
        tools = {'ghidra_home': str(self.root / 'ghidra'), 'java_home': str(self.root / 'java')}
        record = self.suite(PASS, repetitions=1, **tools)
        [(result, spec)] = self.children(record['suite_id'])
        [request] = [params['request'] for params in self.sent('suite_start_child')]
        # The harness sends the frozen slot's request with the five keys it adds, and nothing else.
        self.assertEqual(set(request), {'goal', 'case', 'case_pin', 'executor', 'executor_config', 'case_options',
                                        'request_id', 'effects', 'scoped_tool_approval', 'budget_seconds', 'device',
                                        'firmware', 'mission', 'evaluation_token_sha256', 'client_record'})
        self.assertEqual({key: request['case_options'][key] for key in tools}, tools)
        # The run is the request without the case, with the analysis tools as keys of its own.
        self.assertEqual([key for key in ('case', 'case_pin', 'case_options') if key in spec], [])
        self.assertEqual({key: spec[key] for key in tools}, tools)
        # The request the run recorded is the plain start's params: what the suite book sent with `start`.
        plain = {**{key: value for key, value in request.items() if key not in ('case', 'case_pin', 'case_options')},
                 **tools}
        self.assertEqual(result['request'], plain)
        for key in ('device', 'firmware', 'mission', 'client_record', 'evaluation_token_sha256', 'goal', 'effects'):
            self.assertEqual(spec[key], request[key], key)
        # The child's configuration is the frozen one, taken as it is.
        frozen = book(self.controller).store.get(record['suite_id'])['frozen']
        self.assertEqual(spec['executor_config'], frozen['executor_config'])
        self.assertEqual(request['executor_config'], frozen['executor_config'])

    def test_06_B19_a_childs_executor_config_is_taken_as_complete_whatever_the_home_holds_by_then(self):
        # After the suite is frozen, and before the service starts its child, the operator's settings change: one
        # the frozen configuration has gets another value, and one it lacks is added.
        from suite_fixtures import book
        later = {**CONFIGURED, 'model': 'changed-model', 'reasoning_effort': 'high'}
        frozen_at_start = []

        def change_home_first(method, params, forward):
            if method == 'suite_start_child' and not frozen_at_start:
                frozen_at_start.append(book(self.controller).store.get(params['suite_id'])['frozen']['executor_config'])
                (self.home / 'config.json').write_text(json.dumps({'executors': {'codex': later}}), encoding='utf-8')
            return forward(method, params)
        record = self.suite(PASS, repetitions=1, intercept=change_home_first)
        self.assertEqual(record['status'], 'finished', record)
        [frozen] = frozen_at_start
        self.assertEqual(frozen, {**CONFIGURED, 'runtime': 'codex'})
        # The service read the changed home by the time it started the child, and laid nothing of it under the
        # child's configuration.
        self.assertEqual(self.controller._configured_executors()['codex'], later)
        [(result, spec)] = self.children(record['suite_id'])
        self.assertEqual(spec['executor_config'], frozen)
        self.assertNotIn('reasoning_effort', spec['executor_config'])
        self.assertEqual(spec['executor_config']['model'], 'configured-model')
        self.assertIs(result['equivalence']['equivalent'], True)

    def test_06_B19_the_same_child_start_again_finds_the_child_by_its_recorded_request(self):
        from suite_fixtures import suite_call
        record = self.suite(PASS, repetitions=1)
        [(result, spec)] = self.children(record['suite_id'])
        [again] = [params for params in self.sent('suite_start_child')]
        # The same start again, through the public calls: the suite book finds the child by the slot's request id
        # and the request the run recorded, whatever the suite did since, and starts nothing.
        self.assertEqual(suite_call(self.controller, 'suite_start_child', again),
                         {'ok': True, 'run_id': result['run_id'], 'duplicate': True, 'suite_id': record['suite_id']})
        # A start with another client record is not that start again: the completed suite starts no child for it.
        changed = {**again['request'], 'client_record': {**again['request']['client_record'], 'snapshot_sha256': SHA}}
        with self.assertRaisesRegex(ValueError, 'The suite is completed; no child starts for it'):
            suite_call(self.controller, 'suite_start_child', {**again, 'request': changed})
        self.assertEqual(self.controller.call('status', {'request_id': again['request']['request_id']})['run_id'],
                         result['run_id'])
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 1)

    def test_06_B19_the_experiment_takes_each_trials_snapshot_hash_from_the_childs_client_record(self):
        from suite_fixtures import suite_call
        record = self.suite(PASS, PASS)
        experiment = suite_call(self.controller, 'suite_result', {'suite_id': record['suite_id']})['experiment']
        hashes = [result['client_record']['snapshot_sha256'] for result, _ in self.children(record['suite_id'])]
        self.assertEqual([row['snapshot_sha256'] for row in experiment['execution_snapshots']], hashes)
        self.assertEqual(len(set(hashes)), 1, 'two trials of one case under one configuration freeze the same inputs')

    def test_06_B19_the_suites_check_on_resume_reads_no_snapshot_of_a_child_run(self):
        import os
        import shutil
        from suite_fixtures import suite_call
        record = self.suite(runtime=stopping_probe(), repetitions=1)
        suite_id = record['suite_id']
        self.assertEqual(suite_call(self.controller, 'suite_status', {'suite_id': suite_id})['status'], 'blocked')
        [(result, spec)] = self.children(suite_id)
        self.assertNotIn('_case_pin', spec)
        self.assertNotIn('_snapshot_sha256', spec)
        shutil.rmtree(self.home / 'runs' / result['run_id'] / 'benchmark')
        resumed = suite_call(self.controller, 'suite_resume', {'suite_id': suite_id, 'harness_token_sha256': SHA,
                                                               'harness_pid': os.getpid()})
        self.assertEqual((resumed['ok'], resumed['status'], resumed['attached']), (True, 'running', True))


TWELVE = ('schema', 'status', 'equivalent', 'basis', 'checks', 'diff', 'passed', 'total', 'mission',
          'frozen_artifact_sha256', 'evidence_sha256', 'protocol')
SHA = 'a' * 64


class GraderRecordTests(unittest.TestCase):
    """The grader's verdict record is the tool's `equivalence/2` (06-B18)."""

    def test_06_B18_the_record_builders_give_the_twelve_fields_less_the_evidence_hash(self):
        from test_evaluation import contract, final_of, record_for
        fields = set(TWELVE) - {'evidence_sha256'}
        toy = contract(SHA)
        final = final_of(toy, [record_for(toy, check['id'], check['expected']) for check in toy['checks']])
        mission = {'name': 'fresh_worker_mission', 'passed': True}
        built = {
            'record': evaluator.record('graded', False, [{'id': 'c', 'passed': False}], [{'id': 'c', 'reason': 'r'}],
                                       SHA, mission, protocol='assistive/1'),
            'unscored': evaluator.unscored('the evaluator could not run', SHA, protocol='assistive/1'),
            'final_record': evaluator.final_record(final, mission, protocol='assistive/1')}
        for name, record in built.items():
            with self.subTest(builder=name):
                self.assertEqual(set(record), fields)
                self.assertEqual(record['schema'], 'equivalence/2')
                sealed = None if record['status'] == 'unscored' else 'b' * 64
                self.assertEqual(verdict_errors({**record, 'evidence_sha256': sealed}), [])
        self.assertEqual((built['final_record']['status'], built['final_record']['equivalent']), ('graded', True))
        self.assertEqual(built['final_record']['mission'], mission)

    def test_06_B18_the_public_record_and_the_verdict_identity_hold_no_method_and_no_capability_hash(self):
        from generative_driver.benchmark_support import evidence
        for names in (evidence.EQUIVALENCE, evidence.VERDICT_IDENTITY):
            self.assertEqual(set(names) & {'method', 'capabilities_sha256'}, set())
        self.assertEqual(set(evidence.EQUIVALENCE), set(TWELVE) - {'checks', 'diff', 'mission'})
        self.assertEqual(set(evidence.VERDICT_IDENTITY), {'schema', 'status', 'basis', 'protocol',
                                                          'frozen_artifact_sha256'})


class PostedRecordTests(TrialCase):
    """What the harness posts, seals and regrades after this Feature (06-B18)."""

    def posted(self):
        return [params['equivalence'] for params in self.sent('record_evaluation')]

    def test_06_B18_every_record_the_harness_posts_has_no_fault(self):
        outcomes = {'graded': PASS, 'not-equivalent': (PASS[0], 0), 'host-fault': OSError('the evaluator host stopped')}
        for name, outcome in outcomes.items():
            with self.subTest(trial=name):
                self.calls.clear()
                self.trial(name, outcome=outcome)
                [record] = self.posted()
                self.assertEqual(verdict_errors(record), [])
                self.assertEqual(set(record), set(TWELVE))
                self.assertEqual(record['schema'], 'equivalence/2')
        self.assertEqual(record['status'], 'unscored')

    def test_06_B18_the_sealed_final_keeps_the_mapping_hash_and_the_regrade_of_the_sealed_evidence_passes(self):
        from generative_driver.benchmark_support.evidence import regrade_v3
        from generative_driver.benchmark_support.truth import read_password
        from generative_driver.benchmark_support.run_reporting import report_run
        folder, record = self.trial()
        password = read_password(self.secrets['password_files'][CASE])
        sealed = password.unlock(folder / evaluator.EVIDENCE, record['evidence']['sha256'])
        mapping = next(a for h in self.result(record)['accepted_handoffs'] if h['stage'] == 'probe'
                       for a in h['artifacts'] if a['path'].endswith('capabilities.json'))
        self.assertEqual(sealed['final']['capabilities_sha256'], mapping['sha256'])
        self.assertNotIn('capabilities_sha256', sealed['equivalence'])
        self.assertNotIn('method', sealed['equivalence'])
        with patch('generative_driver.client.call', side_effect=self.configurator):
            report = report_run(record['run_id'], home=self.home, output=self.root / 'report.json')
        regraded = regrade_v3(report, folder / evaluator.EVIDENCE, password)
        self.assertEqual((regraded['equivalence']['status'], regraded['equivalence']['equivalent']), ('graded', True))
        self.assertEqual(regraded['equivalence'], report['equivalence'])


class ClientRecordReaderTests(TrialCase):
    """What read the case, the pin and the snapshot hash from a run reads them from its client record (06-B17)."""

    def test_06_B17_the_run_gives_them_in_its_client_record_and_nowhere_else(self):
        folder, record = self.trial()
        result = self.result(record)
        self.assertEqual(result['client_record'], {'case': CASE, 'case_pin': pin_case(CASE, 'original', 0),
                                                   'snapshot_sha256': read_snapshot(folder)['snapshot_sha256']})
        for key in ('case', 'case_pin', 'snapshot_sha256'):
            self.assertNotIn(key, result)

    def test_06_B17_the_grader_takes_the_pin_and_the_hash_from_the_client_record(self):
        from generative_driver.benchmark_support.integrity import IntegrityError
        from generative_driver.benchmark_support.truth import read_password
        folder, record = self.trial(outcome=OSError('the evaluator host stopped'))
        result = self.result(record)
        password = read_password(self.secrets['password_files'][CASE])
        self.assertIs(evaluator.needs_grading(folder, result, password=password), True)
        snapshot, driver = evaluator.accepted_driver(CASE, folder, self.run_dir(record), result)
        self.assertEqual(snapshot['snapshot_sha256'], result['client_record']['snapshot_sha256'])
        self.assertIsNotNone(driver)
        other = {**result, 'client_record': {**result['client_record'], 'snapshot_sha256': 'f' * 64}}
        with self.assertRaisesRegex(IntegrityError, 'Execution snapshot differs from the run record'):
            evaluator.accepted_driver(CASE, folder, self.run_dir(record), other)
        unpinned = {**result, 'client_record': {**result['client_record'], 'case_pin': {'case_id': 'another'}}}
        with self.assertRaisesRegex(IntegrityError, 'Execution snapshot unverifiable'):
            evaluator.accepted_driver(CASE, folder, self.run_dir(record), unpinned)

    def test_06_B17_a_report_of_a_scripted_run_holds_what_it_held(self):
        from generative_driver.benchmark_support.evidence import public_pin
        from generative_driver.benchmark_support.run_reporting import report_run
        folder, record = self.trial(executor_config={'reasoning_effort': 'high'})
        with patch('generative_driver.client.call', side_effect=self.configurator):
            report = report_run(record['run_id'], home=self.home, output=self.root / 'report.json')
        saved = read_snapshot(folder)
        self.assertEqual(report['schema'], 'benchmark-report/3')
        self.assertEqual((report['case'], report['case_version'], report['scenario_id'], report['case_seed']),
                         (CASE, saved['case_pin']['case_version'], 'original', 0))
        self.assertEqual(report['case_pin'], public_pin(saved['case_pin']))
        self.assertEqual(report['snapshot_sha256'], saved['snapshot_sha256'])
        self.assertEqual(report['evaluator_revision'], saved['executed']['evaluator_revision'])
        # The executor settings the operator configured and the request asked for, as before this Feature.
        self.assertEqual(report['executed']['runtime_configuration'],
                         {'runtime': 'codex', 'model': 'configured-model', 'reasoning_effort': 'high'})
        self.assertEqual((report['verdict'], report['workflow_status']), ('equivalent', 'completed'))
        # The verdict as the run recorded it: the fields it had, less the two the record lost.
        self.assertEqual(set(report['equivalence']), set(TWELVE))
        self.assertEqual(report['equivalence']['evidence_sha256'], record['evidence']['sha256'])

    def test_06_B17_the_comparison_of_two_reports_reads_what_it_read(self):
        from generative_driver import benchmark
        from generative_driver.benchmark_support.run_reporting import report_run
        reports = []
        for name in ('before', 'after'):
            folder, record = self.trial(name)
            with patch('generative_driver.client.call', side_effect=self.configurator):
                reports.append(report_run(record['run_id'], home=self.home, output=self.root / (name + '.json')))
        compared = benchmark.compare(*reports)
        # Two runs of one case under one pin and one evaluator revision: nothing it reads is missing or differs.
        self.assertEqual((compared['compatible'], compared['incompatible_fields']), (True, []), compared)
        self.assertEqual((compared['comparison_kind'], compared['before_verdict'], compared['after_verdict']),
                         ('repeat', 'equivalent', 'equivalent'))


class CaseMissionTests(unittest.TestCase):
    def test_06_B6_the_mission_supplied_for_each_registered_case_has_no_fault(self):
        from generative_driver import benchmark
        from generative_driver.client_interface import mission_errors
        with tempfile.TemporaryDirectory() as temporary:
            for case in ('tq9-v2', 'sampled-sensor-v1', 'parameter-store-v1', 'zephyr-modbus-v1'):
                with self.subTest(case=case):
                    mission = benchmark.supplied_inputs(case, Path(temporary) / 'no-run')['mission']
                    self.assertEqual(mission_errors(mission), [])
                    self.assertTrue(mission['tasks'])


class RunFolderTests(TrialCase):
    def test_06_B16_the_tool_writes_nothing_under_a_runs_benchmark_folder(self):
        # Without the harness's copy, a run that went through its stages has no `benchmark/` folder at all.
        with patch.object(harness, 'keep_snapshot'):
            folder, record = self.trial('uncopied')
        self.assertEqual(self.result(record)['status'], 'completed')
        self.assertFalse((self.run_dir(record) / 'benchmark').exists(),
                         'the tool wrote under the run\'s benchmark/ folder')
        # With it, what lies there is the harness's copy of its snapshot, and nothing else.
        folder, record = self.trial('copied')
        self.assertEqual(record['status'], 'evaluated', record)
        run = self.run_dir(record)
        kept = sorted(path.relative_to(run / 'benchmark').as_posix() for path in (run / 'benchmark').rglob('*')
                      if path.is_file())
        self.assertEqual(kept, ['execution.json', 'inputs/firmware.bin'])
        self.assertEqual({name: (run / 'benchmark' / name).read_bytes() for name in kept},
                         {name: (folder / 'benchmark' / name).read_bytes() for name in kept})


class DocumentTests(unittest.TestCase):
    """The documents say what is true after this Feature (06-B23)."""

    def test_06_B23_the_validator_of_a_generated_device_refuses_a_mapping_with_the_old_schema_name(self):
        from generated_fixtures import FIXTURE_ANSWER_KEY, FIXTURE_IMAGE, fixture_mission
        from generative_driver.benchmark_support.generated_device import validate_device
        device = {'id': '0000d001', 'level': 1, 'framing': 'text-lines'}
        given = {'image': FIXTURE_IMAGE, 'mission': fixture_mission(device['id'])}
        validate_device(device, key=FIXTURE_ANSWER_KEY, **given)
        for reference in ('a', 'b'):
            sealed_before = copy.deepcopy(FIXTURE_ANSWER_KEY)
            sealed_before['references'][reference]['capabilities']['schema'] = 'benchmark-capabilities/2'
            with self.subTest(reference=reference), self.assertRaises(ValueError) as refused:
                validate_device(device, key=sealed_before, **given)
            self.assertIn('Invalid capability schema or input', str(refused.exception.__cause__))

    def test_06_B23_no_set_is_registered(self):
        from generative_driver.benchmark_support.generated_sets import set_registrations
        self.assertEqual(set_registrations(), {})


class RenameTests(unittest.TestCase):
    """The benchmark's side of the renames (06-B20 to 06-B22)."""

    def test_06_B20_the_scorecard_counts_a_package_execute_call_as_a_device_call(self):
        from generative_driver.benchmark_support import scorecard
        self.assertIn('package_execute', scorecard.DEVICE_TOOLS)
        self.assertNotIn('benchmark_package_execute', scorecard.DEVICE_TOOLS)

    def test_06_B21_the_graders_capability_rules_accept_capabilities_2_and_refuse_the_old_name(self):
        from generative_driver.benchmark_support import capabilities
        from tq9_workflow_fixture import capabilities as mapping_of, model as model_of
        model, mapping = model_of(), mapping_of()
        self.assertEqual(mapping['schema'], 'capabilities/2')
        self.assertEqual(capabilities.validate_capabilities(mapping, model), {'ok': True, 'errors': []})
        old = {**mapping, 'schema': 'benchmark-capabilities/2'}
        refused = capabilities.validate_capabilities(old, model)
        self.assertEqual([(error['path'], error['message']) for error in refused['errors']],
                         [('tasks', 'Declare schema capabilities/2 and a tasks object')])
        task = next(iter(mapping['tasks']))
        inputs = {name: 1 for name in mapping['tasks'][task]['inputs']}
        with self.assertRaisesRegex(ValueError, 'Invalid capability schema or input'):
            capabilities.bind_task(old, task, inputs, model)

    def test_06_B22_the_scorecard_looks_for_an_interpretations_transcript_under_supplied_tools(self):
        from generative_driver.benchmark_support import scorecard
        run = Path('a-run')
        sealed = {'id': 'assignment-1', 'workspace': '/elsewhere/interpret/attempt2'}
        places = [path.relative_to(run).as_posix() for path in scorecard._kept(run, 'interpret', sealed)]
        self.assertEqual(places, ['work/interpret/assignment-1/' + scorecard.TRANSCRIPT,
                                  'supplied/tools/interpret/attempt2/' + scorecard.TRANSCRIPT])


class RecordReaderTests(TrialCase):
    """The grader reads the run's own records under `supplied/` (06-B22)."""

    def check(self, spec, stage, run_dir, workspace, report, accepted, assignment):
        """As the parent's, and fresh reuse hands on a note and records three package calls that do the mission of
        `tq9-v2`, in the file `self.recorded` of the run's folder."""
        if stage != 'reuse':
            return super().check(spec, stage, run_dir, workspace, report, accepted, assignment)
        frozen = next(a['sha256'] for h in accepted if h['stage'] == 'emit' for a in h['artifacts'])
        row = {'actor': 'worker', 'stage': 'reuse', 'assignment_id': assignment['id'], 'package_sha256': frozen}
        calls = [('measure', {'ok': True, 'outputs': {'temperature': 21.5}}, 0),
                 ('set_duty', {'ok': True, 'outputs': {}}, 370), ('disarm', {'ok': True, 'outputs': {}}, 0)]
        path = Path(run_dir) / self.recorded
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps({**row, 'operation': operation, 'result': result,
                                            'observation': {'duty': duty}}) + '\n'
                                for operation, result, duty in calls), encoding='utf-8')
        (workspace / 'reuse-notes.txt').write_text('three calls', encoding='utf-8')
        return {'artifacts': [str(workspace / 'reuse-notes.txt')], 'checks': []}

    def mission(self, recorded):
        """The grader's mission in the verdict of a scripted trial whose reuse calls lie in `recorded`."""
        self.recorded = recorded
        folder, record = self.trial(recorded.split('/')[0])
        self.assertEqual(record['status'], 'evaluated', record)
        return self.result(record)['equivalence']['mission']

    def test_06_B22_a_scripted_trials_verdict_carries_the_mission_judged_from_supplied_package_events(self):
        self.assertEqual(self.mission('supplied/package-events.jsonl'),
                         {'name': 'fresh_worker_mission', 'passed': True})

    def test_06_B22_calls_recorded_under_the_old_folder_are_not_read(self):
        mission = self.mission('benchmark/package-events.jsonl')
        self.assertEqual((mission['name'], mission['passed']), ('fresh_worker_mission', False), mission)


if __name__ == '__main__':
    unittest.main()
