import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark import run


class SetupBenchmarkTests(unittest.TestCase):
    def test_setup_replay_emits_a_functionally_checked_package_without_inference(self):
        with tempfile.TemporaryDirectory(prefix='benchmark with spaces ') as directory:
            result = run('setup-smoke', directory)
            self.assertEqual(result['execution'], 'scripted-replay')
            self.assertEqual(result['verdict'], 'passed', result)
            self.assertEqual(result['observations']['temperature'], 21.5)
            self.assertIsNone(result['usage'])
            self.assertFalse(result['model_benchmark'])
            self.assertTrue(Path(result['package']).is_dir())
            self.assertEqual(result['stages']['maintain']['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()

class ApprovalScopeTests(unittest.TestCase):
    def test_emulator_tool_approval_cannot_be_applied_to_a_different_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError,'tq9'):
                run('setup-smoke',directory,options={'scoped_tool_approval':'emulator'})

    def test_bound_device_approval_requires_an_explicit_physical_binding(self):
        with self.assertRaisesRegex(ValueError,'binding'):
            run('bme280',options={'scoped_tool_approval':'bound-device'})
