import copy
import unittest
from suite_fixtures import pilot_manifest


class ManifestTests(unittest.TestCase):
    def test_repeat_slots_preserve_three_entries_and_three_families(self):
        from generative_driver.benchmark_support.suites import normalize_manifest, expand_trials
        manifest = normalize_manifest(pilot_manifest())
        slots = expand_trials(manifest)
        self.assertEqual(len(slots), 9)
        self.assertEqual([s['entry_index'] for s in slots[:3]], list(range(3)))
        self.assertEqual([s['repeat_index'] for s in slots], [0]*3 + [1]*3 + [2]*3)
        self.assertEqual(slots[3]['trial_key'], 'entry-000-repeat-001')
        self.assertEqual(len({s['case'] for s in slots}), 3)
        self.assertEqual({s['case_seed'] for s in slots}, {0})
        self.assertEqual([s['ordinal'] for s in slots], list(range(9)))
        changed = copy.deepcopy(manifest)
        changed['repetitions'] = 1
        self.assertEqual(len(expand_trials(changed)), 3)

    def test_normalization_rejects_incomplete_unknown_and_blank_fields(self):
        from generative_driver.benchmark_support.suites import normalize_manifest
        mutations = [lambda m: m.update(unrecognized=True), lambda m: m.pop('id'),
                     lambda m: m.update(schema='benchmark-suite/2'), lambda m: m.update(id='  '),
                     lambda m: m.update(version=1), lambda m: m.update(entries=[]),
                     lambda m: m.update(entries={}), lambda m: m['entries'].append(None),
                     lambda m: m['entries'][0].update(unrecognized=1),
                     lambda m: m['entries'][0].pop('case_seed'),
                     lambda m: m['entries'][0].update(case=''),
                     lambda m: m['entries'][0].update(scenario=None)]
        for index, mutate in enumerate(mutations):
            manifest = pilot_manifest(); mutate(manifest)
            with self.subTest(index=index), self.assertRaises(ValueError):
                normalize_manifest(manifest)
        for value in (None, [], 'manifest'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_manifest(value)

    def test_normalization_rejects_invalid_numbers_and_duplicate_entries(self):
        from generative_driver.benchmark_support.suites import normalize_manifest
        mutations = [lambda m: m['entries'][0].update(case_seed=True),
                     lambda m: m['entries'][0].update(case_seed=float('nan')),
                     lambda m: m['entries'][0].update(case_seed=0.0),
                     lambda m: m.update(repetitions=True), lambda m: m.update(repetitions=0),
                     lambda m: m.update(repetitions=1.5), lambda m: m.update(max_active_children=2),
                     lambda m: m.update(max_active_children=True),
                     lambda m: m.update(suite_budget_seconds=float('inf')),
                     lambda m: m.update(child_budget_seconds=float('nan')),
                     lambda m: m.update(child_budget_seconds=True),
                     lambda m: m.update(child_budget_seconds=0),
                     lambda m: m.update(suite_budget_seconds=604801),
                     lambda m: m.update(suite_budget_seconds=100),
                     lambda m: m.update(child_budget_seconds=10**1000),
                     lambda m: m['entries'].append(dict(m['entries'][0]))]
        for index, mutate in enumerate(mutations):
            manifest = pilot_manifest(); mutate(manifest)
            with self.subTest(index=index), self.assertRaises(ValueError):
                normalize_manifest(manifest)

    def test_defensive_copies_preserve_submitted_order_and_legal_limits(self):
        from generative_driver.benchmark_support.suites import normalize_manifest, expand_trials
        original = pilot_manifest()
        original['entries'].reverse()
        original['entries'][0]['case_seed'] = -1
        original.update(child_budget_seconds=604800, suite_budget_seconds=604800)
        normalized = normalize_manifest(original)
        self.assertEqual(normalized, original)
        normalized['entries'][0]['scenario'] = 'changed'
        self.assertEqual(original['entries'][0]['scenario'], 'original')
        slots = expand_trials(original)
        slots[0]['case_seed'] = 22
        self.assertEqual(slots[6]['case_seed'], -1)
        self.assertEqual(original['entries'][0]['case_seed'], -1)

    def test_distributed_pilot_expands_without_tools_or_credentials(self):
        import json
        from generative_driver.benchmark import case_root
        from generative_driver.benchmark_support.suites import expand_trials
        manifest = json.loads((case_root() / 'suites/development-pilot.json').read_text())
        self.assertEqual(manifest, pilot_manifest())
        slots = expand_trials(manifest)
        self.assertEqual(len(slots), 9)
        self.assertEqual(slots[-1], {'case': 'parameter-store-v1', 'scenario': 'original', 'case_seed': 0,
                                    'ordinal': 8, 'entry_index': 2, 'repeat_index': 2,
                                    'trial_key': 'entry-002-repeat-002'})


class FreezeTests(unittest.TestCase):
    def test_legacy_registry_freeze_is_defensive_and_hashes_only_manifest(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from suite_fixtures import legacy_manifest
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.benchmark_support.registry import pin_case
        manifest = legacy_manifest()
        config = {'command': ['C:/Program Files/toy/runtime.exe'], 'model': 'toy-model', 'env': {'TOKEN': 'private-toy'}}
        options = {'evaluator_password_files': {'tq9': 'C:/Private Keys/toy.handle'}, 'scoped_tool_approval': 'emulator'}
        provenance = {'skills_revision': 'a' * 64, 'environment': {'python': 'toy-version'}}
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'not-created'
            with patch.dict('os.environ', {'GENERATIVE_DRIVER_HOME': str(home)}):
                frozen = freeze_suite(manifest, 'codex', config, options, provenance)
            self.assertFalse(home.exists())
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
        self.assertEqual(frozen['manifest_sha256'], digest)
        self.assertEqual(frozen['entry_pins'], [pin_case('tq9', 'original', 0)])
        self.assertEqual(frozen['entry_effects'], [['write', 'actuate']])
        self.assertEqual(frozen['entry_pins'][0]['calibration'], {'status': 'legacy-not-required'})
        self.assertEqual(frozen['scope'], 'full-workflow')
        self.assertEqual(frozen['request'], {'manifest': manifest, 'executor': 'codex', 'options': options})
        original = copy.deepcopy(frozen)
        manifest['entries'].clear(); config['command'].clear(); options['evaluator_password_files'].clear()
        provenance['environment']['python'] = 'changed'
        self.assertEqual(frozen, original)
        other = freeze_suite(original['manifest'], 'goose', {'command': ['other-toy']}, {}, {})
        self.assertEqual(other['manifest_sha256'], digest)

    def test_replay_physical_unknown_and_bad_scenario_do_not_create_home(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from generative_driver.benchmark_support.suites import freeze_suite
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / 'not-created'
            with patch.dict('os.environ', {'GENERATIVE_DRIVER_HOME': str(home)}):
                for case in ('setup-smoke', 'bme280', 'unknown-case', 'tq9'):
                    manifest = pilot_manifest()
                    manifest['entries'] = [{'case': case, 'scenario': 'unsupported', 'case_seed': 0}]
                    with self.subTest(case=case), self.assertRaises(ValueError):
                        freeze_suite(manifest, 'codex', {'command': ['scripted-runtime']}, {}, {})
            self.assertFalse(home.exists())

    def test_unknown_options_invalid_commands_and_unselected_handles_are_refused(self):
        from suite_fixtures import legacy_manifest
        from generative_driver.benchmark_support.suites import freeze_suite
        for config in ({}, {'command': []}, {'command': ''}, {'command': ['']}, {'command': [None]}, None):
            with self.subTest(config=config), self.assertRaises(ValueError):
                freeze_suite(legacy_manifest(), 'codex', config, {}, {})
        for options in ({'binding': {}}, {'home': 'not-a-suite-option'}, {'scoped_tool_approval': 'bound-device'},
                        {'evaluator_password_files': {'other-case': 'private.handle'}},
                        {'evaluator_password_files': {'tq9': ''}}, {'evaluator_password_files': []},
                        {'renode': []}, None):
            with self.subTest(options=options), self.assertRaises(ValueError):
                freeze_suite(legacy_manifest(), 'codex', {'command': ['toy']}, options, {})
        with self.assertRaises(ValueError):
            freeze_suite(legacy_manifest(), 'other-runtime', {'command': ['toy']}, {}, {})

    def test_v2_pending_or_legacy_exception_and_stage_local_work_are_refused(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from suite_fixtures import public_case_fixture
        from generative_driver.benchmark_support.suites import freeze_suite
        for changes in ({'calibration': {'status': 'pending'}}, {'calibration': {'status': 'legacy-not-required'}},
                        {'calibration': {}}, {'scope': 'stage-local'}, {'approval_scope': None}):
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp); public_case_fixture(root, **changes)
                manifest = pilot_manifest(); manifest['entries'] = manifest['entries'][:1]
                with patch('generative_driver.benchmark.case_root', return_value=root), self.assertRaises(ValueError):
                    freeze_suite(manifest, 'codex', {'command': ['toy']}, {'scoped_tool_approval': 'emulator'}, {})

    def test_mixed_evidence_tracks_are_refused(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from suite_fixtures import public_case_fixture
        from generative_driver.benchmark_support.suites import freeze_suite
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public_case_fixture(root)
            public_case_fixture(root, 'sampled-sensor-v1', evidence_track='other-track')
            manifest = pilot_manifest(); manifest['entries'] = [manifest['entries'][0], manifest['entries'][2]]
            with patch('generative_driver.benchmark.case_root', return_value=root), self.assertRaises(ValueError):
                freeze_suite(manifest, 'codex', {'command': ['toy']}, {}, {})

    def test_child_projection_uses_selected_pin_handle_and_frozen_configuration(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from suite_fixtures import public_case_fixture
        from generative_driver.benchmark_support.suites import freeze_suite, child_request
        from generative_driver.configurator import case_options
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            public_case_fixture(root)
            public_case_fixture(root, 'sampled-sensor-v1')
            manifest = pilot_manifest(); manifest['entries'] = [manifest['entries'][0], manifest['entries'][1]]
            manifest['entries'][1]['case_seed'] = 73
            config = {'command': ['scripted-contract-fixture'], 'model': 'toy-model'}
            options = {'renode': 'C:/Tools/renode.exe', 'ghidra_home': 'C:/Tools/Ghidra',
                       'java_home': 'C:/Tools/Java', 'scoped_tool_approval': 'emulator',
                       'evaluator_password_files': {'tq9-v2': 'first.private', 'sampled-sensor-v1': 'second.private'}}
            with patch('generative_driver.benchmark.case_root', return_value=root):
                frozen = freeze_suite(manifest, 'codex', config, options, {'skills_revision': 'toy'})
                trial = dict(frozen['trials'][3], child_request_id='suite-toy-entry-001-repeat-001')
                child = child_request(frozen, trial)
            self.assertEqual(child['case'], 'sampled-sensor-v1')
            self.assertEqual(child['case_pin'], frozen['entry_pins'][1])
            self.assertEqual(child['request_id'], 'suite-toy-entry-001-repeat-001')
            self.assertEqual(child['effects'], ['write'])
            self.assertEqual(child['executor_config'], config)
            self.assertEqual(child['budget_seconds'], 10800)
            self.assertEqual(child['scoped_tool_approval'], 'emulator')
            self.assertEqual(child['case_options'], {'renode': 'C:/Tools/renode.exe', 'ghidra_home': 'C:/Tools/Ghidra',
                'java_home': 'C:/Tools/Java', 'evaluator_password_file': 'second.private',
                'scenario_id': 'original', 'case_seed': 73})
            self.assertNotIn('first.private', str(child))
            self.assertNotIn('scenario', child['goal'])
            self.assertNotIn('development-pilot', child['goal'])
            self.assertEqual(case_options(child)['scenario_id'], 'original')
            self.assertEqual(case_options(child)['case_seed'], 73)
            child['executor_config']['command'].clear(); child['case_pin']['manifest'].clear()
            child['case_options'].clear()
            self.assertEqual(frozen['executor_config']['command'], ['scripted-contract-fixture'])
            self.assertTrue(frozen['entry_pins'][1]['manifest'])
            self.assertEqual(frozen['options'], options)

    def test_child_projection_refuses_changed_or_unreserved_slot_identity(self):
        from suite_fixtures import legacy_manifest
        from generative_driver.benchmark_support.suites import freeze_suite, child_request
        frozen = freeze_suite(legacy_manifest(), 'codex', {'command': ['toy']}, {}, {})
        original = dict(frozen['trials'][0], child_request_id='suite-child-toy')
        mutations = [lambda t: t.update(case='tq9-v2'), lambda t: t.update(scenario='semantic'),
                     lambda t: t.update(case_seed=1), lambda t: t.update(entry_index=-1),
                     lambda t: t.update(entry_index=True), lambda t: t.update(ordinal=-1),
                     lambda t: t.update(repeat_index=True), lambda t: t.update(trial_key='other-slot'),
                     lambda t: t.pop('child_request_id'), lambda t: t.update(child_request_id='')]
        for index, mutate in enumerate(mutations):
            trial = copy.deepcopy(original); mutate(trial)
            with self.subTest(index=index), self.assertRaises(ValueError):
                child_request(frozen, trial)
