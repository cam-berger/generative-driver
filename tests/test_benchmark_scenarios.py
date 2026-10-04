"""Original-only case admission and behavior phases."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from suite_fixtures import public_case_fixture


class ScenarioTests(unittest.TestCase):
    def test_unknown_scenario_cannot_fall_back_to_another_private_contract(self):
        from generative_driver.benchmark_support import tq9_v2
        vectors = {'temperature_vectors':[{'stimulus':20,'expected':20,'absolute_tolerance':0}],
            'effect_vectors':[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':200}},
                              {'task':'disarm','inputs':{}}]}
        truth = {'artifact_sha256':'a'*64, 'contracts':{'units':{'temperature':'degC','duty':'permille'},
            'scenarios':{'semantic':{'phases':{'diagnostic':vectors}},'original':{'phases':{'diagnostic':vectors}}}}}
        with self.assertRaisesRegex(ValueError, 'scenario'):
            tq9_v2.contract({'scenario_id':'unknown','revision':0}, truth, 'diagnostic')

    def test_controller_rejects_obsolete_scenario_before_any_assignment(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public_case_fixture(root/'resources')
            with patch('generative_driver.benchmark.case_root', return_value=root/'resources'):
                controller = Controller(root/'home')
                try:
                    for scenario in ('semantic','control','identity','unknown'):
                        with self.subTest(scenario=scenario), self.assertRaisesRegex(ValueError, 'scenario'):
                            controller.start({'goal':'scripted rejection','case':'tq9-v2',
                                'effects':['write','actuate'], 'case_options':{'scenario_id':scenario}})
                    with controller._db() as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
                finally:
                    controller.close()

    def test_reference_execution_rejects_unknown_scenario_before_native_start(self):
        from generative_driver.benchmark_support.emulated import reference_execute
        from tq9_workflow_fixture import model, capabilities
        with tempfile.TemporaryDirectory() as temp, patch(
                'generative_driver.benchmark_support.native.NativeSession.start',
                side_effect=AssertionError('native process started before scenario rejection')):
            with self.assertRaisesRegex(ValueError, 'scenario'):
                reference_execute({'case_id':'tq9-v2','scenario_id':'unknown'}, {'recipe':{}}, model(), capabilities(),
                    renode='unused', image=Path(temp)/'missing.bin', output_dir=Path(temp)/'output')
