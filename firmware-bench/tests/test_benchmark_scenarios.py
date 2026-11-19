"""Original-only case admission and behavior phases."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ScenarioTests(unittest.TestCase):
    def test_unknown_scenario_cannot_fall_back_to_another_private_contract(self):
        from firmware_bench.benchmark_support import tq9_v2
        vectors = {'temperature_vectors':[{'stimulus':20,'expected':20,'absolute_tolerance':0}],
            'effect_vectors':[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':200}},
                              {'task':'disarm','inputs':{}}]}
        truth = {'artifact_sha256':'a'*64, 'contracts':{'units':{'temperature':'degC','duty':'permille'},
            'scenarios':{'semantic':{'phases':{'diagnostic':vectors}},'original':{'phases':{'diagnostic':vectors}}}}}
        with self.assertRaisesRegex(ValueError, 'scenario'):
            tq9_v2.contract({'scenario_id':'unknown','revision':0}, truth, 'diagnostic')

    def test_reference_execution_rejects_unknown_scenario_before_native_start(self):
        from firmware_bench.benchmark_support.evaluator import reference_execute
        from firmware_bench.benchmark_support.reference_calibration import run_reference
        from tq9_workflow_fixture import model, capabilities
        with tempfile.TemporaryDirectory() as temp, patch(
                'firmware_bench.benchmark_support.native.NativeSession.start',
                side_effect=AssertionError('native process started before scenario rejection')):
            pin = {'case_id':'tq9-v2','scenario_id':'unknown'}
            place = {'renode': 'unused', 'image': Path(temp)/'missing.bin', 'output_dir': Path(temp)/'output'}
            with self.assertRaisesRegex(ValueError, 'scenario'):
                reference_execute(pin, {'recipe':{}}, capabilities(), package=Path(temp)/'package',
                                  expected_package_sha256='a'*64, **place)
            with self.assertRaisesRegex(ValueError, 'scenario'):
                run_reference(pin, {'recipe':{}}, model(), capabilities(), phase='final', **place)
