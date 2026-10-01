import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark_support.registry import pin_case
from generative_driver.benchmark_support.snapshots import snapshot_case, require_snapshot


class SnapshotTests(unittest.TestCase):
    def test_changed_input_cannot_resume_as_original(self):
        with tempfile.TemporaryDirectory(prefix='bench snapshot ') as temp:
            root = Path(temp)
            pin = pin_case('tq9', None, 0)
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
            pin = pin_case('tq9', None, 0)
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
            saved = snapshot_case(Path(temp), pin_case('tq9', None, 0), {
                'runtime_configuration': {'model': 'scripted', 'password': 'secret-one',
                    'evaluator_password_file': '/secret-two', 'authkey': 'secret-three'}})
            text = (Path(temp) / 'benchmark/execution.json').read_text()
            self.assertNotIn('secret-', text)
            self.assertEqual(saved['executed']['runtime_configuration'], {'model': 'scripted'})


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
            try:
                config = root / 'config.json'
                config.write_text(json.dumps({'executors': {'codex': {'model': 'original'}}}))
                request = {'goal': 'snapshot contract', 'case': 'tq9', 'request_id': 'same-request'}
                run = controller.start(request)
                self.wait_stopped(controller, run)
                config.write_text('invalid edited configuration')
                with patch('generative_driver.benchmark.case_root', return_value=root / 'unavailable'):
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
                records = list((root / 'runs').glob('*/benchmark/execution.json'))
                seen.append((json.loads(records[0].read_text()) if records else None, dict(config)))
                return self.blocked_executor(request, config, cancel)
            with patch('generative_driver.configurator.execute', execute):
                controller = Controller(root)
                try:
                    run = controller.start({'goal': 'snapshot contract', 'case': 'tq9',
                        'executor_config': {'model': 'scripted'},
                        'case_options': {'evaluator_password_file': '/private/secret-handle'}})
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
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            controller = Controller(Path(temp))
            try:
                run = controller.start({'goal': 'resume integrity', 'case': 'tq9'})
                before = self.wait_stopped(controller, run)
                image = Path(temp) / 'runs' / run['run_id'] / 'benchmark/inputs/firmware.bin'
                image.write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError, 'hash'):
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
                image = next((root / 'runs').glob('*/benchmark/inputs/firmware.bin'))
                image.write_bytes(b'changed between stages')
                return {'runtime': 'codex', 'status': 'completed', 'elapsed_seconds': 0,
                    'usage': None, 'report': {'status': 'completed', 'summary': 'scripted acquisition',
                        'artifacts': [], 'checks': [], 'unresolved': []}}
            with patch('generative_driver.configurator.execute', execute):
                controller = Controller(root)
                try:
                    run = controller.start({'goal': 'stage integrity', 'case': 'tq9'})
                    result = self.wait_stopped(controller, run)
                    self.assertEqual(result['status'], 'failed', result)
                    self.assertIn('hash', result['reason'])
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
            with patch('generative_driver.benchmark.case_root', return_value=installed), patch('generative_driver.configurator.execute', execute):
                controller = Controller(root / 'home')
                try:
                    run = controller.start({'goal': 'use captured firmware', 'case': 'tq9'})
                    self.wait_stopped(controller, run)
                    image = installed / 'cases/tq9/firmware.bin'
                    image.write_bytes(b'new installed firmware')
                    manifest_path = installed / 'cases/tq9/case.json'
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
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            controller = Controller(Path(temp))
            try:
                run = controller.start({'goal': 'implementation identity', 'case': 'tq9'})
                before = self.wait_stopped(controller, run)
                with patch('generative_driver.reporting._tree_hash', return_value='changed-installation'):
                    with self.assertRaisesRegex(ValueError, 'implementation'):
                        controller.call('resume', run)
                self.assertEqual(controller.call('result', run)['worker_reports'], before['worker_reports'])
            finally:
                controller.close()

    def test_snapshot_refuses_a_stale_pin_at_initial_capture(self):
        import copy
        with tempfile.TemporaryDirectory() as temp:
            pin = copy.deepcopy(pin_case('tq9', None, 0))
            pin['manifest_sha256'] = '0' * 64
            with self.assertRaisesRegex(ValueError, 'pin'):
                snapshot_case(Path(temp), pin, {})
            self.assertFalse((Path(temp) / 'benchmark/execution.json').exists())

    def test_historical_run_without_snapshot_resumes_without_invented_provenance(self):
        import json
        import shutil
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        from generative_driver.reporting import report_run
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            root = Path(temp)
            controller = Controller(root)
            try:
                run = controller.start({'goal': 'legacy fixture', 'case': 'tq9'})
                self.wait_stopped(controller, run)
                # Model the persisted pre-snapshot schema, retaining actual controller storage.
                with controller._db() as db:
                    spec = json.loads(db.execute('SELECT spec FROM runs WHERE id=?', (run['run_id'],)).fetchone()['spec'])
                    for key in ('_snapshot_sha256', '_case_pin', '_execution_run_dir', '_submitted_intent'):
                        spec.pop(key, None)
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(spec), run['run_id']))
                benchmark = root / 'runs' / run['run_id'] / 'benchmark'
                (benchmark / 'execution.json').unlink()
                shutil.rmtree(benchmark / 'inputs')
                controller.call('resume', run)
                result = self.wait_stopped(controller, run)
                self.assertEqual(len(result['worker_reports']), 2)
                self.assertNotIn('snapshot_sha256', result)
                self.assertFalse((benchmark / 'execution.json').exists())
                with patch('generative_driver.client.call', side_effect=lambda method, params, **kwargs: controller.call(method, params)):
                    report = report_run(run['run_id'], home=root)
                self.assertNotIn('executed', report)
                self.assertIn('exporting installation', report['provenance_meaning']['toolchain_revision'])
            finally:
                controller.close()

    def test_generic_controller_keeps_uncalibrated_v2_admission_closed(self):
        from unittest.mock import patch
        from test_case_registry import RegistryTests
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            RegistryTests._v2_case(root / 'installed')
            with patch('generative_driver.benchmark.case_root', return_value=root / 'installed'):
                controller = Controller(root / 'home')
                try:
                    with self.assertRaisesRegex(ValueError, 'calibration'):
                        controller.start({'goal': 'pending v2', 'case': 'tq9-v2',
                            'effects': ['write', 'actuate'],
                            'case_options': {'scenario_id': 'semantic'}})
                    with controller._db() as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
                finally:
                    controller.close()

    def test_dedup_does_not_equate_boolean_with_explicit_numeric_intent(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp, patch('generative_driver.configurator.execute', self.blocked_executor):
            controller = Controller(Path(temp))
            try:
                request = {'goal': 'typed intent', 'case': 'tq9', 'request_id': 'typed',
                           'case_options': {'case_seed': 0}}
                run = controller.start(request)
                self.wait_stopped(controller, run)
                with self.assertRaisesRegex(ValueError, 'different inputs'):
                    controller.start({**request, 'case_options': {'case_seed': False}})
            finally:
                controller.close()
