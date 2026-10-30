import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class PublicMissionTests(unittest.TestCase):
    def test_operating_conditions_are_optional_bounded_public_text(self):
        from generative_driver.benchmark_support.missions import load_mission, validate_mission
        original = load_mission('sampled-sensor-v1')
        conditions = ['The device needs a quiet interval before each request.',
                      'The operator supplies a stable byte stream.']
        mission = {**original, 'operating_conditions': conditions}
        validated = validate_mission(mission, 'sampled-sensor-v1')
        self.assertEqual(validated['operating_conditions'], conditions)
        validated['operating_conditions'].clear()
        self.assertEqual(mission['operating_conditions'], conditions)
        boundary = {**original, 'operating_conditions': ['x' * 1000] * 16}
        self.assertEqual(validate_mission(boundary, 'sampled-sensor-v1'), boundary)
        for invalid in (None, '', 'Wait before a request.', [], [''], [' '], [False],
                        [{'module': 'future_conditions'}], ['x' * 1001], ['condition'] * 17):
            with self.subTest(conditions=invalid), self.assertRaises(ValueError):
                validate_mission({**original, 'operating_conditions': invalid}, 'sampled-sensor-v1')

    def test_future_case_operating_facts_reach_interpret_probe_ground_and_fresh_reuse(self):
        from generative_driver.benchmark_support import emulated
        from generative_driver.configurator import digest
        conditions = ['The device needs a quiet interval before every request.',
                      'The operator supplies a stable byte stream.']
        mission = {'schema': 'benchmark-public-mission/1', 'case_id': 'future-node',
            'tasks': {'read_pressure': {'meaning': 'Read pressure.', 'inputs': {},
                                       'outputs': {'pressure': {'unit': 'Pa'}}}},
            'reuse_objective': 'Read pressure twice and report observations.',
            'operating_conditions': conditions}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            assets = root/'assets'; (assets/'missions').mkdir(parents=True)
            mission_path = assets/'missions/future-node.json'
            mission_path.write_text(json.dumps(mission))
            inputs = root/'inputs'; inputs.mkdir()
            (inputs/'firmware.bin').write_bytes(b'public firmware fixture')
            model = root/'probe/model.json'; model.parent.mkdir(); model.write_text('{}')
            notes = root/'prior/NOTES.md'; notes.parent.mkdir(); notes.write_text('Prior-stage-only notes.')
            package = root/'emitted'; package.mkdir(); (package/'manifest.json').write_text('{}')
            accepted = [
                {'stage': 'interpret', 'artifacts': [{'path': str(notes), 'sha256': digest(notes)}]},
                {'stage': 'probe', 'artifacts': [{'path': str(model), 'sha256': digest(model)}]},
                {'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}]
            run = root/'run'
            emulated._write(run/'benchmark/state.json', {'package_sha256': digest(package)})
            binding = {'type': 'tcp', 'host': '127.0.0.1', 'port': 1}
            options = {'device': {'binding': binding}, 'configured_effects': ['write']}

            def sealed_interpret(name, arguments):
                self.assertEqual(name, 'interpret_run')
                sealed = Path(arguments['workspace_root'])
                self.addCleanup(shutil.rmtree, sealed)
                for relative, source in arguments['inputs'].items():
                    destination = sealed/relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                return {'ok': True, 'workspace': str(sealed), 'prompt_for_agent': 'Scripted prepare only.',
                        'sealed_inputs': list(arguments['inputs'])}

            with patch('generative_driver.benchmark.case_root', return_value=assets), \
                    patch('generative_driver.benchmark_support.case_assets.case_root', return_value=assets), \
                    patch.object(emulated, '_inputs', return_value=(inputs, {})), \
                    patch.object(emulated, 'adapt', return_value={'binding': binding}), \
                    patch('generative_driver.analysis.firmware_analysis_inputs', return_value={}), \
                    patch('generative_driver.toolkit.call_tool', side_effect=sealed_interpret), \
                    patch('generative_driver.benchmark_support.registrations.registration_for',
                          return_value=SimpleNamespace(mission_case=None)):
                for stage in ('interpret', 'probe', 'ground'):
                    with self.subTest(stage=stage):
                        prepared = emulated.prepare_stage('future-node', stage, run, root/stage, accepted, options)
                        tasks_path = next(Path(path) for path in prepared['inputs'] if Path(path).name == 'TASKS.json')
                        contract = json.loads(tasks_path.read_text())
                        self.assertEqual(contract['operating_conditions'], conditions)
                        self.assertIn('provided operating facts', contract['instructions'])
                        self.assertEqual(contract['tasks'], mission['tasks'])
                with self.subTest(stage='reuse'):
                    reuse = emulated.prepare_stage('future-node', 'reuse', run, root/'reuse', accepted, options)
                    for condition in conditions:
                        self.assertIn(condition, reuse['objective'])
                    self.assertEqual(reuse['inputs'], [str(root/'reuse/package')])
                    self.assertEqual(reuse['context'], {'package_dir': str(root/'reuse/package')})
                    self.assertNotIn('Prior-stage-only notes.', reuse['objective'])
                mission.pop('operating_conditions')
                mission_path.write_text(json.dumps(mission))
                plain = emulated.prepare_stage('future-node', 'reuse', run, root/'plain-reuse', accepted, options)
                self.assertEqual(plain['objective'], 'Use only the emitted package and this objective: '
                    + mission['reuse_objective'] + ' Use benchmark_package_execute for each operation. '
                    'Find names and arguments in the package reference. Each result also carries an instrument '
                    'reading of the emulated device after the call. Report actual observations and limitations.')

    def test_new_mission_drives_worker_capability_rules_without_a_case_name_branch(self):
        from generative_driver.benchmark_support import missions, emulated
        from benchmark_family_fixtures import toy_sample_model
        mission = {'schema': 'benchmark-public-mission/1', 'case_id': 'future-node',
                   'tasks': {'read_pressure': {'meaning': 'Read pressure.', 'inputs': {},
                                              'outputs': {'pressure': {'unit': 'Pa'}}}},
                   'reuse_objective': 'Read pressure twice and report observations.'}
        with tempfile.TemporaryDirectory() as temp, patch.object(missions, 'load_mission', return_value=mission):
            workspace = Path(temp)
            generated = emulated._capability_guidance(workspace, 'future-node')
            self.assertTrue(generated)
            model = workspace/'candidate'; model.mkdir()
            (model/'model.json').write_text(json.dumps(toy_sample_model()))
            mapping = workspace/'capabilities.json'
            mapping.write_text(json.dumps({'schema': 'benchmark-capabilities/2', 'tasks': {}}))
            checked = subprocess.run([sys.executable, str(workspace/'validate_capabilities.py'),
                                      str(model), str(mapping)], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn('read_pressure', checked.stdout)

    def test_missions_are_public_semantics_and_preserve_existing_contracts(self):
        from generative_driver.benchmark_support.missions import load_mission
        from generative_driver.benchmark_support.candidate_context import task_contract
        for case, tasks in [('tq9-v2', ['temperature', 'arm', 'set_duty', 'disarm']),
                            ('sampled-sensor-v1', ['measure', 'read']),
                            ('parameter-store-v1', ['read', 'update', 'stage', 'commit', 'abort'])]:
            mission = load_mission(case)
            contract = task_contract(case, ['write'])
            self.assertEqual(list(contract['tasks']), tasks)
            self.assertEqual(contract['tasks'], mission['tasks'])
            self.assertEqual(contract['effect_grants'], ['write'])
            self.assertEqual(set(contract), {'schema', 'tasks', 'effect_grants', 'instructions'})
            self.assertEqual(contract['instructions'],
                'These are requested observable behaviors, not protocol facts or hidden test values. '
                'Recover command bytes, operation composition, bounds and decoders from the supplied evidence. '
                'One canonical task can require several exchange steps in a model operation. '
                'Effect labels describe actual behavior: acquisition and configuration changes are writes; '
                'actuation controls a physical output. Read is implicit; other effects require the listed grants. '
                'Do not relabel unsafe operations merely to bypass a grant. Report unsupported behavior.')
            self.assertEqual(set(mission), {'schema', 'case_id', 'tasks', 'reuse_objective'})
            contract['tasks'].clear()
            self.assertTrue(load_mission(case)['tasks'])

    def test_missing_or_malformed_mission_cannot_fall_back_to_controller_tasks(self):
        from generative_driver.benchmark_support.missions import load_mission, validate_mission
        with self.assertRaises(ValueError):
            load_mission('../tq9-v2')
        with self.assertRaises(ValueError):
            load_mission('unregistered')
        original = load_mission('sampled-sensor-v1')
        for mutate in (lambda m: m.update(tasks={}), lambda m: m.update(reuse_objective=''),
                       lambda m: m['tasks']['measure']['outputs']['temperature'].pop('unit'),
                       lambda m: m.update(private_vectors=['answer'])):
            mission = copy.deepcopy(original); mutate(mission)
            with self.subTest(mission=mission), self.assertRaises(ValueError):
                validate_mission(mission, 'sampled-sensor-v1')
