import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class PublicMissionTests(unittest.TestCase):
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
