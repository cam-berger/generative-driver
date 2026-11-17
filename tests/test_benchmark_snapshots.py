import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark_support.grader_identity import grader_identity
from generative_driver.benchmark_support.registry import pin_case
from generative_driver.benchmark_support.snapshots import snapshot_case, require_snapshot, execution_provenance


class SnapshotTests(unittest.TestCase):
    def test_changed_input_cannot_resume_as_original(self):
        with tempfile.TemporaryDirectory(prefix='bench snapshot ') as temp:
            root = Path(temp)
            pin = pin_case('tq9-v2', None, 0)
            saved = snapshot_case(root, pin, {'toolchain_revision': 'test-fixture'})
            self.assertEqual(saved['case_pin']['case_seed'], 0)
            image = root / 'benchmark' / 'inputs' / 'firmware.bin'
            image.write_bytes(image.read_bytes() + b'changed')
            with self.assertRaisesRegex(ValueError, 'hash'):
                require_snapshot(root, pin)

    def test_snapshot_is_idempotent_and_rejects_changed_identity(self):
        import copy
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pin = pin_case('tq9-v2', None, 0)
            first = snapshot_case(root, pin, {'evaluator_revision': 'a'})
            self.assertEqual(snapshot_case(root, pin, {'evaluator_revision': 'a'}), first)
            for key, value in [('scenario_id', 'changed'), ('case_seed', 1)]:
                changed = copy.deepcopy(pin)
                changed[key] = value
                with self.assertRaises(ValueError):
                    snapshot_case(root, changed, {'evaluator_revision': 'a'})
            with self.assertRaises(ValueError):
                snapshot_case(root, pin, {'evaluator_revision': 'b'})
            self.assertEqual(require_snapshot(root, pin), first)

    def test_secrets_do_not_enter_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            saved = snapshot_case(Path(temp), pin_case('tq9-v2', None, 0), {
                'runtime_configuration': {'model': 'scripted', 'password': 'secret-one',
                    'evaluator_password_file': '/secret-two', 'authkey': 'secret-three'}})
            text = (Path(temp) / 'benchmark/execution.json').read_text()
            self.assertNotIn('secret-', text)
            self.assertEqual(saved['executed']['runtime_configuration'], {'model': 'scripted'})

    def test_evaluator_revision_is_the_grader_identity_of_the_case(self):
        identity = grader_identity(case_id='tq9-v2')['sha256']
        provenance = execution_provenance({}, case_ids=('tq9-v2',))
        self.assertEqual(provenance['evaluator_revision'], identity)
        self.assertEqual(provenance['evaluator_revisions'], {'tq9-v2': identity})

    def test_cases_of_several_families_are_stamped_with_the_digest_of_their_revisions(self):
        from generative_driver.benchmark_support.snapshots import canonical_digest
        revisions = {case: grader_identity(case_id=case)['sha256'] for case in ('tq9-v2', 'sampled-sensor-v1')}
        provenance = execution_provenance({}, case_ids=('tq9-v2', 'sampled-sensor-v1'))
        self.assertEqual(provenance['evaluator_revision'], canonical_digest(revisions))
        self.assertEqual(provenance['evaluator_revisions'], revisions)

    def test_a_system_change_moves_the_toolchain_and_skills_revisions_but_not_the_evaluator_revision(self):
        from unittest.mock import patch
        before = execution_provenance({}, case_ids=('tq9-v2',))
        with patch('generative_driver.benchmark_support.integrity.tree_hash', return_value='0' * 64):
            after = execution_provenance({}, case_ids=('tq9-v2',))
        for field in ('toolchain_revision', 'skills_revision'):
            self.assertNotEqual(after[field], before[field])
        self.assertEqual(after['evaluator_revision'], before['evaluator_revision'])

def case_start(test, root, home=None, **fields):
    """The start its bench harness sends for the current case `tq9-v2`: a device of synthetic peers under `root`, the
    firmware and the mission the harness froze in a trial folder there, the token hash and the client record. `home`
    is the configurator home whose configured runtime the run has, `root` unless given."""
    from suite_fixtures import supplied_start
    return supplied_start(test, Path(root) / 'fixture', home=home or root, effects=['write', 'actuate'], **fields)


def attach_device(test, controller, run, root, image=None):
    """Play the run's bench harness before a resume: the stopped run released its device, so another one is attached."""
    from suite_fixtures import HARNESS_TOKEN, device_descriptor
    controller.call('attach_device', {**run, 'token': HARNESS_TOKEN,
                                      'device': device_descriptor(test, Path(root) / 'fixture', image)})


def scripted_runtime(home):
    """A configured runtime for runs whose worker launch these tests replace; the configurator launches no other."""
    import json
    Path(home).mkdir(parents=True, exist_ok=True)
    (Path(home) / 'config.json').write_text(json.dumps({'executors': {'codex': {'command': ['scripted-codex']}}}))


class ControllerSnapshotTests(unittest.TestCase):
    def wait_stopped(self, controller, run):
        import time
        until = time.monotonic() + 5
        while time.monotonic() < until:
            status = controller.call('status', run)
            if status['status'] in ('blocked', 'failed') and not status['stopping']:
                return controller.call('result', run)
            time.sleep(.01)
        self.fail('Scripted run did not stop')

    @staticmethod
    def blocked_executor(request, config, cancel):
        return {'runtime': config['runtime'], 'model': config.get('model'), 'status': 'blocked',
                'reason': 'scripted contract fixture', 'elapsed_seconds': 0, 'usage': None, 'report': None}

    def test_request_deduplicates_before_mutable_defaults_and_assets(self):
        import json
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            root = Path(temp)
            controller = Controller(root)
            scripted_runtime(root)
            try:
                config = root / 'config.json'
                config.write_text(json.dumps({'executors': {'codex': {'model': 'original', 'command': ['scripted-codex']}}}))
                request = case_start(self, root, goal='snapshot contract', request_id='same-request')
                run = controller.start(request)
                self.wait_stopped(controller, run)
                config.write_text('invalid edited configuration')
                with patch('generative_driver.benchmark.case_root', return_value=root / 'unavailable'), \
                        patch('generative_driver.benchmark_support.case_assets.case_root', return_value=root / 'unavailable'):
                    duplicate = controller.start(dict(request))
                self.assertEqual(duplicate['run_id'], run['run_id'])
                self.assertTrue(duplicate['duplicate'])
                with self.assertRaisesRegex(ValueError, 'different inputs'):
                    controller.start({**request, 'goal': 'changed explicit intent'})
                self.assertEqual(controller.call('result', run)['agent']['model'], 'original')
            finally:
                controller.close()

    def test_worker_receives_frozen_configuration_after_snapshot_is_saved(self):
        import json
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        seen = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def execute(request, config, cancel):
                # The harness saved the snapshot in its trial folder before it sent the start.
                records = list((root / 'fixture').glob('trial-*/benchmark/execution.json'))
                seen.append((json.loads(records[0].read_text()) if records else None, dict(config)))
                return self.blocked_executor(request, config, cancel)
            with patch('generative_driver.configurator.execute', execute):
                controller = Controller(root)
                scripted_runtime(root)
                try:
                    # The configurator takes no evaluator handle for any run: the bench harness holds it.
                    request = case_start(self, root, goal='snapshot contract', executor_config={'model': 'scripted'})
                    with self.assertRaisesRegex(ValueError, 'password'):
                        controller.start({**request, 'evaluator_password_file': '/private/secret-handle'})
                    self.assertFalse(list((root / 'runs').glob('*')) if (root / 'runs').exists() else [])
                    run = controller.start(request)
                    result = self.wait_stopped(controller, run)
                    self.assertTrue(seen and seen[0][0], result)
                    snapshot, config = seen[0]
                    self.assertEqual(snapshot['executed']['runtime_configuration'], config)
                    self.assertEqual(snapshot['executed']['runtime_configuration']['model'], 'scripted')
                    for field in ('toolchain_revision', 'skills_revision', 'evaluator_revision'):
                        self.assertEqual(len(snapshot['executed'][field]), 64)
                    self.assertIn('python', snapshot['executed']['environment'])
                    self.assertNotIn('secret-handle', json.dumps(snapshot))
                finally:
                    controller.close()

    def test_resume_rejects_tampered_snapshot_before_new_assignment(self):
        # The configurator holds the run to the firmware it was started with, the image its harness froze (06-B5).
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            controller = Controller(Path(temp))
            scripted_runtime(Path(temp))
            try:
                start = case_start(self, temp, goal='resume integrity')
                run = controller.start(start)
                before = self.wait_stopped(controller, run)
                attach_device(self, controller, run, temp)
                Path(start['firmware']['path']).write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'The supplied firmware changed after the run started'):
                    controller.call('resume', run)
                after = controller.call('result', run)
                self.assertEqual(after['worker_reports'], before['worker_reports'])
                self.assertEqual(after['status'], 'blocked')
            finally:
                controller.close()

    def test_each_preparation_checks_snapshot_before_assigning_next_worker(self):
        import json
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def execute(request, config, cancel):
                if request.stage != 'acquire':
                    return self.blocked_executor(request, config, cancel)
                (request.workspace / 'provenance.json').write_text('{}')
                # The firmware the harness froze and supplied changes between two stages.
                image = next((root / 'fixture').glob('trial-*/benchmark/inputs/firmware.bin'))
                image.write_bytes(b'changed between stages')
                return {'runtime': 'codex', 'status': 'completed', 'elapsed_seconds': 0,
                    'usage': None, 'report': {'status': 'completed', 'summary': 'scripted acquisition',
                        'artifacts': [], 'checks': [], 'unresolved': []}}
            with patch('generative_driver.configurator.execute', execute):
                controller = Controller(root)
                scripted_runtime(root)
                try:
                    run = controller.start(case_start(self, root, goal='stage integrity'))
                    result = self.wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'integrity'), result)
                    self.assertIn('The supplied firmware changed after the run started', result['reason'])
                    self.assertEqual(len(result['worker_reports']), 1)
                finally:
                    controller.close()

    def test_resume_uses_captured_firmware_after_installed_case_changes(self):
        import hashlib
        import json
        import shutil
        from unittest.mock import patch
        from generative_driver.benchmark import case_root
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installed = root / 'installation'
            shutil.copytree(case_root(), installed)
            observed = []
            def execute(request, config, cancel):
                observed.append((request.workspace / 'description.bin').read_bytes())
                return self.blocked_executor(request, config, cancel)
            with patch('generative_driver.benchmark.case_root', return_value=installed), \
                    patch('generative_driver.benchmark_support.case_assets.case_root', return_value=installed), \
                    patch('generative_driver.configurator.execute', execute):
                controller = Controller(root / 'home')
                scripted_runtime(root / 'home')
                try:
                    start = case_start(self, root, home=root / 'home', goal='use captured firmware')
                    run = controller.start(start)
                    self.wait_stopped(controller, run)
                    # The device its harness attaches for the resume runs the image the harness captured.
                    attach_device(self, controller, run, root, start['firmware']['path'])
                    image = installed / 'cases/tq9-v2/firmware.bin'
                    image.write_bytes(b'new installed firmware')
                    manifest_path = installed / 'cases/tq9-v2/case.json'
                    manifest = json.loads(manifest_path.read_text())
                    manifest['images']['firmware.bin'] = hashlib.sha256(image.read_bytes()).hexdigest()
                    manifest_path.write_text(json.dumps(manifest))
                    controller.call('resume', run)
                    self.wait_stopped(controller, run)
                    self.assertEqual(len(observed), 2)
                    self.assertEqual(observed[1], observed[0])
                finally:
                    controller.close()

    def test_resume_rejects_changed_executing_implementation(self):
        # The bench harness compares the implementation itself before it resumes a stopped run (06-B16): its check
        # of the snapshot is asked here, with the configuration the trial recorded.
        from unittest.mock import patch
        from generative_driver.benchmark_support import harness
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            controller = Controller(Path(temp))
            scripted_runtime(Path(temp))
            try:
                start = case_start(self, temp, goal='implementation identity')
                run = start.keep(temp, controller.start(start))
                before = self.wait_stopped(controller, run)
                run_dir = Path(temp) / 'runs' / run['run_id']
                harness.check_snapshot(start.trial_dir, run_dir, before, config=start['executor_config'])
                with patch('generative_driver.benchmark_support.integrity.tree_hash', return_value='changed-installation'):
                    with self.assertRaisesRegex(ValueError, 'implementation'):
                        harness.check_snapshot(start.trial_dir, run_dir, before, config=start['executor_config'])
                self.assertEqual(controller.call('result', run)['worker_reports'], before['worker_reports'])
            finally:
                controller.close()

    def test_snapshot_refuses_a_stale_pin_at_initial_capture(self):
        import copy
        with tempfile.TemporaryDirectory() as temp:
            pin = copy.deepcopy(pin_case('tq9-v2', None, 0))
            pin['manifest_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'pin'):
                snapshot_case(Path(temp), pin, {})
            self.assertFalse((Path(temp) / 'benchmark/execution.json').exists())

    def test_historical_run_without_snapshot_is_not_resumed_and_reports_without_invented_provenance(self):
        import json
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        from generative_driver.reporting import report_run
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            root = Path(temp)
            controller = Controller(root)
            scripted_runtime(root)
            try:
                run = controller.start(case_start(self, root, goal='legacy fixture'))
                self.wait_stopped(controller, run)
                # Model the persisted pre-snapshot schema, retaining actual controller storage: a run of that time
                # named its case, and had no device, firmware, mission or record from a bench harness.
                with controller._db() as db:
                    spec = json.loads(db.execute('SELECT spec FROM runs WHERE id=?', (run['run_id'],)).fetchone()['spec'])
                    for key in ('_submitted_intent', 'device', 'firmware', 'mission', 'client_record',
                                'evaluation_token_sha256'):
                        spec.pop(key, None)
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps({**spec, 'case': 'tq9-v2'}), run['run_id']))
                benchmark = root / 'runs' / run['run_id'] / 'benchmark'
                # A run an earlier version started on a case is not resumed (06-B11 of the untangle Run).
                with self.assertRaisesRegex(ValueError, 'started on a benchmark case by an earlier version'):
                    controller.call('resume', run)
                result = controller.call('result', run)
                self.assertEqual(len(result['worker_reports']), 1)
                self.assertNotIn('snapshot_sha256', result)
                self.assertFalse((benchmark / 'execution.json').exists())
                with patch('generative_driver.client.call', side_effect=lambda method, params, **kwargs: controller.call(method, params)):
                    report = report_run(run['run_id'], home=root)
                self.assertNotIn('executed', report)
                self.assertIn('exporting installation', report['provenance_meaning']['toolchain_revision'])
            finally:
                controller.close()

    def test_generic_controller_keeps_uncalibrated_v2_admission_closed(self):
        # The bench harness admits a case now: a trial of an uncalibrated v2 case ends before any run starts.
        from unittest.mock import patch
        from test_case_registry import RegistryTests
        from generative_driver.benchmark_support import harness
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            RegistryTests._v2_case(root / 'installed')
            (root / 'password-handle').write_text('synthetic fixture credential')
            with patch('generative_driver.benchmark.case_root', return_value=root / 'installed'), \
                    patch('generative_driver.benchmark_support.case_assets.case_root', return_value=root / 'installed'):
                controller = Controller(root / 'home')
                scripted_runtime(root / 'home')
                configurator = lambda method, params=None, **kwargs: (
                    {'ok': True} if method == 'ping' else controller.call(method, params))
                try:
                    with patch('generative_driver.client.call', side_effect=configurator):
                        record = harness.run_trial(root / 'bench' / 'pending', {
                            'trial_id': 'pending', 'home': str(root / 'home'), 'goal': 'pending v2', 'case': 'tq9-v2',
                            'effects': ['write', 'actuate'], 'case_options': {'scenario_id': 'original'},
                            'password_file': str(root / 'password-handle')})
                    self.assertEqual((record['status'], record['run_id'], record['no_run']),
                                     ('failed', None, 'admit-refused'))
                    self.assertRegex(record['reason'], 'calibration')
                    with controller._db() as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
                finally:
                    controller.close()

    def test_dedup_does_not_equate_boolean_with_explicit_numeric_intent(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            controller = Controller(Path(temp))
            scripted_runtime(Path(temp))
            try:
                # A typed value of the submitted intent: the record a client sends with its start.
                request = case_start(self, temp, goal='typed intent', request_id='typed')
                request['client_record'] = {**request['client_record'], 'case_seed': 0}
                run = controller.start(request)
                self.wait_stopped(controller, run)
                with self.assertRaisesRegex(ValueError, 'different inputs'):
                    controller.start({**request, 'client_record': {**request['client_record'], 'case_seed': False}})
            finally:
                controller.close()
