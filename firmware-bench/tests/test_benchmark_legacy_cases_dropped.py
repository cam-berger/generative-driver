"""The two legacy benchmark cases, the old `tq9` and the physical `bme280`, are dropped.

Feature 04 of the untangle Run (docs/features/untangle/04-legacy-cases.md), 04-B1 and 04-B2. This file is a benchmark
item: it reads the benchmark package. The service's side, 04-B4, is in `tests/test_service_without_legacy_cases.py`,
which stays with the tool.
"""
import contextlib
from dataclasses import replace
import io
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

DROPPED_MODULES = ('cases', 'physical', 'recorded_grading', 'tq9_legacy')
DROPPED_CASES = ('tq9', 'bme280')
REGISTERED = ('setup-smoke', 'tq9-v2', 'tq9-v3', 'sampled-sensor-v1', 'parameter-store-v1', 'zephyr-modbus-v1',
              'generated')
# What `grader_closure()` names: every module it named before this Feature but the old tq9's family module.
GRADING_CODE = tuple('benchmark_support' + name for name in (
    '', '.behavior', '.calibration', '.capabilities', '.case_assets', '.device_provider', '.emulated_actions',
    '.emulator', '.evaluator', '.evidence', '.execution', '.families', '.family_support', '.generated',
    '.generated_device', '.generated_sets', '.grader_identity', '.instruments', '.integrity', '.labels', '.mcp_reuse',
    '.missions', '.native', '.native_comparison', '.native_firmware', '.parameter_store', '.registrations', '.registry',
    '.sampled_sensor', '.snapshots', '.tq9_v2', '.truth', '.upstream', '.zephyr_modbus'))


class RegistrationTests(unittest.TestCase):
    def test_04_B1_the_registered_cases_are_the_smoke_and_the_six_current_cases_in_order(self):
        from generative_driver.benchmark_support import registry
        self.assertEqual(registry.case_ids(), REGISTERED)

    def test_04_B1_a_dropped_case_is_refused_as_a_name_that_was_never_registered(self):
        from generative_driver.benchmark_support import registry
        for case in (*DROPPED_CASES, 'never-registered'):
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, f'^Unknown benchmark case: {case}$'):
                registry.pin_case(case, None, 0)

    def test_04_B1_a_start_through_the_service_that_names_a_dropped_case_is_refused_with_that_reason(self):
        # The bench harness pins the case before it sends a start, so the refusal is the reason its trial fails with;
        # the service, which knows no case, is sent no start.
        from generative_driver.benchmark_support import harness
        from generative_driver.configurator import Controller
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            controller = Controller(root / 'home')
            (root / 'password-handle').write_text('synthetic fixture credential', encoding='utf-8')
            configurator = lambda method, params=None, **kwargs: (
                {'ok': True} if method == 'ping' else controller.call(method, params))
            try:
                for case in (*DROPPED_CASES, 'never-registered'):
                    with self.subTest(case=case), patch('generative_driver.client.call', side_effect=configurator):
                        record = harness.run_trial(root / 'bench' / case, {
                            'trial_id': case, 'home': str(root / 'home'), 'goal': 'A run of a dropped case',
                            'case': case, 'executor_config': {'command': ['missing-runtime']},
                            'password_file': str(root / 'password-handle')})
                        self.assertEqual((record['status'], record['run_id']), ('failed', None))
                        self.assertEqual(record['reason'], f'ValueError: Unknown benchmark case: {case}')
                with controller._db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
            finally:
                controller.close()

    def test_04_B1_the_stage_adapters_are_the_current_one_and_the_smokes(self):
        from generative_driver.benchmark_support import adapters, registrations
        adapter = adapters.adapter_for('tq9-v2')
        self.assertTrue(callable(adapter.supplied_inputs) and callable(adapter.score))
        with self.assertRaisesRegex(ValueError, '^Setup smoke has no agent stages$'):
            adapters.adapter_for('setup-smoke').supplied_inputs('setup-smoke', 'run')
        for key in ('legacy-emulator', 'legacy-physical'):
            # No registered case has such a key any more, so a row with it is registered for as long as this runs.
            row = replace(registrations.registration_for('tq9-v2'), id='with-a-dropped-adapter', adapter_key=key)
            registered = MappingProxyType({**registrations._REGISTRATIONS, row.id: row})
            with self.subTest(key=key), patch.object(registrations, '_REGISTRATIONS', registered), \
                    self.assertRaisesRegex(ValueError, f'^Benchmark adapter is pending: {key}$'):
                adapters.adapter_for(row.id)

    def test_04_B1_the_truth_command_has_no_default_case_and_requires_one(self):
        from generative_driver import benchmark
        with tempfile.TemporaryDirectory() as output:
            printed = io.StringIO()
            with contextlib.redirect_stderr(printed), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as refused:
                benchmark.main(['truth', 'unlock', '--output', output])
            self.assertEqual(refused.exception.code, 2)
            self.assertIn('the following arguments are required: --case', printed.getvalue())
            for case in DROPPED_CASES:
                printed = io.StringIO()
                with self.subTest(case=case), contextlib.redirect_stderr(printed), self.assertRaises(SystemExit):
                    benchmark.main(['truth', 'unlock', '--case', case, '--output', output])
                self.assertIn('invalid choice', printed.getvalue())


class GradingCodeTests(unittest.TestCase):
    def test_04_B2_grading_code_is_the_34_modules_without_the_old_tq9_family(self):
        from generative_driver.benchmark_support.grader_identity import grader_closure
        self.assertEqual(len(GRADING_CODE), 34)
        self.assertEqual(grader_closure(), GRADING_CODE)
        self.assertNotIn('benchmark_support.' + DROPPED_MODULES[3], grader_closure())


if __name__ == '__main__':
    unittest.main()
