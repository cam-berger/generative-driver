import unittest

from generative_driver.benchmark_support.registry import resolve_case, case_ids
from generative_driver.configurator import validate_scoped_approval


class RegistryTests(unittest.TestCase):
    @staticmethod
    def _v2_case(root, *, calibration=None):
        import hashlib
        import json
        case = root / 'cases' / 'tq9-v2'
        case.mkdir(parents=True)
        image = case / 'image.bin'
        image.write_bytes(b'fixture image')
        truth = root / 'groundtruth' / 'tq9-v2.enc'
        truth.parent.mkdir()
        truth.write_bytes(b'ciphertext fixture')
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = {'schema': 'benchmark-case/2', 'id': 'tq9-v2', 'family': 'tq9',
                    'version': '2', 'evaluator_version': '2',
                    'execution': 'actual-agent-emulation', 'evidence_track': 'firmware',
                    'adapter_key': 'emulator-v2', 'approval_scope': 'emulator',
                    'default_effects': ['write', 'actuate'], 'scenarios': ['semantic'],
                    'required_stages': ['acquire'], 'images': {'image.bin': digest(image)},
                    'truth': {'path': 'groundtruth/tq9-v2.enc', 'sha256': digest(truth)},
                    'time_policy': {'probe': 'continuous'}, 'provenance': {'license': 'test'},
                    'limitations': ['fixture'], 'calibration': calibration or {'status': 'pending'}}
        (case / 'case.json').write_text(json.dumps(manifest))
        return manifest

    def test_legacy_identity_and_emulator_grant_boundary(self):
        self.assertEqual(resolve_case('tq9').version, '1')
        self.assertEqual(resolve_case('tq9').family, 'tq9')
        self.assertTrue({'setup-smoke', 'tq9', 'bme280'} <= set(case_ids()))
        with self.assertRaises(ValueError):
            resolve_case('../tq9')
        with self.assertRaises(ValueError):
            validate_scoped_approval({'case': 'tq9',
                'scoped_tool_approval': 'emulator',
                'binding': {'host': '127.0.0.1', 'port': 1234}})

    def test_resource_paths_and_seed_types_are_bounded(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import resource_path, pin_case
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'image.bin').write_bytes(b'toy')
            self.assertEqual(resource_path(root, 'image.bin'), root / 'image.bin')
            for path in ('../outside.bin', str(root.parent / 'outside.bin')):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    resource_path(root, path)
            outside = root.with_name(root.name + '-outside.bin')
            outside.write_bytes(b'outside')
            try:
                (root / 'escape.bin').symlink_to(outside)
            except OSError:
                pass  # Windows may disallow unprivileged symlinks.
            else:
                with self.assertRaises(ValueError):
                    resource_path(root, 'escape.bin')
            finally:
                outside.unlink()
        for seed in (True, 1.5, float('nan')):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                pin_case('tq9', None, seed)

    def test_duplicate_manifest_ids_are_rejected(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'tq9-v2' / 'case.json'
            path.parent.mkdir()
            path.write_text('{"schema":"benchmark-case/2","id":"tq9-v2","id":"tq9-v2"}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                load_definition(path, resource_root=root)

    def test_adapter_dispatch_rejects_unknown_case_before_stage_work(self):
        from generative_driver.benchmark import prepare_stage
        with self.assertRaisesRegex(ValueError, 'Unknown benchmark case'):
            prepare_stage('not-installed', 'acquire', '/missing/run', '/missing/worker')

    def test_incomplete_v2_manifest_is_rejected_before_pin(self):
        import json
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'tq9-v2' / 'case.json'
            path.parent.mkdir()
            path.write_text(json.dumps({'schema': 'benchmark-case/2', 'id': 'tq9-v2',
                                        'version': '2', 'evaluator_version': '2',
                                        'execution': 'actual-agent-emulation'}))
            with self.assertRaisesRegex(ValueError, 'required'):
                load_definition(path, resource_root=root)

    def test_cases_command_lists_pending_without_evaluator_password(self):
        import contextlib
        import io
        import json
        from generative_driver.benchmark import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = main(['cases'])
        self.assertEqual(status, 0)
        rows = json.loads(out.getvalue())
        self.assertIn('tq9-v2', {row['id'] for row in rows})
        self.assertIn('tq9', {row['id'] for row in rows})

    def test_legacy_run_rejects_unsupported_scenario_before_owner(self):
        from generative_driver.benchmark import run
        with self.assertRaisesRegex(ValueError, 'scenario'):
            run('tq9', options={'scenario_id': 'semantic'}, autostart=False)

    def test_cli_accepts_scenario_and_rejects_it_for_legacy(self):
        import contextlib
        import io
        import json
        from generative_driver.benchmark import main
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = main(['run', '--case', 'tq9', '--scenario', 'semantic'])
        self.assertEqual(status, 1)
        self.assertIn('scenario', json.loads(out.getvalue())['error'])

    def test_forged_v2_passed_calibration_cannot_start_owner(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark import run
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._v2_case(root, calibration={'status': 'passed'})
            with patch('generative_driver.benchmark.case_root', return_value=root):
                with self.assertRaisesRegex(ValueError, 'calibration'):
                    run('tq9-v2', options={'scenario_id': 'semantic'}, autostart=False)

    def test_registered_emulated_v2_uses_registry_approval_scope(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._v2_case(root)
            with patch('generative_driver.benchmark.case_root', return_value=root):
                validate_scoped_approval({'case': 'tq9-v2', 'scoped_tool_approval': 'emulator'})
                with self.assertRaisesRegex(ValueError, 'binding'):
                    validate_scoped_approval({'case': 'tq9-v2', 'scoped_tool_approval': 'emulator',
                                              'binding': {'host': '127.0.0.1', 'port': 1234}})

    def test_truth_management_rejects_unknown_case_before_output(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.evaluator_cli import manage
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            with self.assertRaisesRegex(ValueError, 'Unknown benchmark case'):
                manage('unlock', '../unknown', '/missing/password', output)
            self.assertFalse(output.exists())

    def test_duplicate_v2_scenario_ids_are_rejected(self):
        import json
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._v2_case(root)
            manifest['scenarios'] = ['semantic', 'semantic']
            path = root / 'cases' / 'tq9-v2' / 'case.json'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'duplicate scenario'):
                load_definition(path, resource_root=root)

    def test_emulator_approval_rejects_operator_binding_before_owner(self):
        from generative_driver.benchmark import run
        with self.assertRaisesRegex(ValueError, 'binding'):
            run('tq9', options={'scoped_tool_approval': 'emulator',
                                'binding': {'host': '127.0.0.1', 'port': 1234}}, autostart=False)

    def test_emulator_approval_rejects_empty_binding_before_owner(self):
        from generative_driver.benchmark import run
        with self.assertRaisesRegex(ValueError, 'binding'):
            run('tq9', options={'scoped_tool_approval': 'emulator', 'binding': {}}, autostart=False)

    def test_owner_rejects_present_empty_bindings_in_every_location(self):
        for case, top, case_options in (
            ('tq9', {'binding': {}}, {}),
            ('tq9', {}, {'binding': {}}),
            ({'id': 'tq9', 'options': {'binding': {}}}, {}, {}),
        ):
            with self.subTest(case=case, top=top, case_options=case_options):
                with self.assertRaisesRegex(ValueError, 'binding'):
                    validate_scoped_approval({'case': case, 'scoped_tool_approval': 'emulator',
                                              'case_options': case_options, **top})

    def test_v2_case_cannot_downgrade_manifest_schema(self):
        import json
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._v2_case(root)
            manifest['schema'] = 'benchmark-case/1'
            path = root / 'cases' / 'tq9-v2' / 'case.json'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'schema'):
                load_definition(path, resource_root=root)

    def test_v2_public_asset_hashes_are_verified(self):
        import json
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._v2_case(root)
            asset = root / 'cases' / 'tq9-v2' / 'contract.json'
            asset.write_text('{}')
            manifest['assets'] = {'contract.json': '0' * 64}
            path = asset.parent / 'case.json'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'asset hash'):
                load_definition(path, resource_root=root)
            for bad in ('not-a-sha256', '0' * 63):
                manifest['assets'] = {'contract.json': bad}
                path.write_text(json.dumps(manifest))
                with self.subTest(hash=bad), self.assertRaises(ValueError):
                    load_definition(path, resource_root=root)
            for bad_path in ('../contract.json', str(asset)):
                manifest['assets'] = {bad_path: '0' * 64}
                path.write_text(json.dumps(manifest))
                with self.subTest(path=bad_path), self.assertRaises(ValueError):
                    load_definition(path, resource_root=root)

    def test_missing_identity_fields_are_validation_errors(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'tq9' / 'case.json'
            path.parent.mkdir()
            path.write_text('{"schema":"benchmark-case/1","id":"tq9"}')
            with self.assertRaisesRegex(ValueError, 'required'):
                load_definition(path, resource_root=root)

    def test_unbuilt_definition_is_listed_pending_but_cannot_pin(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import case_descriptors, pin_case
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._v2_case(root)
            (root / 'cases' / 'tq9-v2' / 'image.bin').unlink()
            with patch('generative_driver.benchmark.case_root', return_value=root):
                rows = {row['id']: row for row in case_descriptors()}
                self.assertEqual(rows['tq9-v2']['status'], 'pending')
                with self.assertRaises(ValueError):
                    pin_case('tq9-v2', 'semantic', 0)

    def test_extracted_legacy_package_checks_assignment_before_execution(self):
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark import package_execute
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = package_execute('tq9', root, 'reuse', root / 'unassigned', 'read')
            self.assertFalse(result['ok'])
            self.assertEqual(result['error']['code'], 'package')

    def test_v2_effect_defaults_must_be_a_grant_list(self):
        import json
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._v2_case(root)
            manifest['default_effects'] = 'write'
            path = root / 'cases' / 'tq9-v2' / 'case.json'
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'effect'):
                load_definition(path, resource_root=root)

    def test_malformed_optional_resource_maps_are_validation_errors(self):
        import json
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.registry import load_definition
        for field in ('images', 'assets', 'truth'):
            for value in (None, [], 'invalid', 3):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    case = root / 'tq9'
                    case.mkdir()
                    manifest = {'schema': 'benchmark-case/1', 'id': 'tq9',
                                'version': '1', 'evaluator_version': '1', 'execution': 'actual-agent-emulation',
                                field: value}
                    path = case / 'case.json'
                    path.write_text(json.dumps(manifest))
                    with self.assertRaises(ValueError):
                        load_definition(path, resource_root=root)

    def test_corrupted_installed_v2_is_invalid_not_unbuilt_pending(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import case_descriptors
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._v2_case(root)
            (root / 'cases' / 'tq9-v2' / 'image.bin').write_bytes(b'corrupt')
            with patch('generative_driver.benchmark.case_root', return_value=root):
                rows = {row['id']: row for row in case_descriptors()}
            self.assertEqual(rows['tq9-v2']['status'], 'invalid')
            self.assertIn('hash mismatch', rows['tq9-v2']['error'])

    def test_built_v2_with_pending_calibration_is_discovered_as_pending(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import case_descriptors
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._v2_case(root)
            with patch('generative_driver.benchmark.case_root', return_value=root):
                row = next(row for row in case_descriptors() if row['id'] == 'tq9-v2')
            self.assertEqual(row['status'], 'pending')
            self.assertEqual(row['calibration'], {'status': 'pending'})

    def test_malformed_calibration_is_invalid_during_discovery(self):
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import case_descriptors
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = self._v2_case(root)
            manifest['calibration'] = None
            (root / 'cases' / 'tq9-v2' / 'case.json').write_text(json.dumps(manifest))
            with patch('generative_driver.benchmark.case_root', return_value=root):
                row = next(row for row in case_descriptors() if row['id'] == 'tq9-v2')
            self.assertEqual(row['status'], 'invalid')
