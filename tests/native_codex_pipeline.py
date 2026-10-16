"""Explicit acceptance gate for an already executed, blind Codex firmware suite.

Set GD_NATIVE_CODEX_SUITE to an operator-owned JSON file as described in
docs/codex-acceptance.md. This gate authenticates and regrades every trial's sealed
evidence. It neither launches inference nor touches a physical device.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest


class NativeCodexPipelineTests(unittest.TestCase):
    def test_blind_firmware_recovery_and_fresh_package_reuse(self):
        from generative_driver.benchmark_support.suite_reporting import report_suite
        configuration = os.environ.get('GD_NATIVE_CODEX_SUITE')
        self.assertTrue(configuration, 'Set GD_NATIVE_CODEX_SUITE to the private acceptance configuration')
        options = json.loads(Path(configuration).read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory(prefix='codex acceptance ') as directory:
            report = report_suite(options['suite_id'], home=options['home'], output=directory,
                                  evidence_files=options['evidence_files'], password_files=options['password_files'])
        self.assertEqual(report['qualification'], 'regraded-encrypted-evidence')
        self.assertEqual(report['protocol'], 'assistive/1')
        experiment = report['experiment']
        self.assertEqual(experiment['dimensions']['runtime'], 'codex')
        self.assertTrue(experiment['dimensions'].get('model'), 'Select and record an explicit Codex model')
        self.assertEqual(experiment['comparison_identity']['execution'], 'actual-agent-emulation')
        manifest = experiment['comparison_identity']['manifest']
        self.assertGreaterEqual(manifest['repetitions'], 3)
        self.assertGreaterEqual(len(report['family_equivalence']), 3)
        self.assertFalse(report['provisional'])
        planned = report['counts']['planned']
        self.assertGreaterEqual(planned, 9)
        self.assertEqual(report['equivalent_and_mission'], {'passed': planned, 'denominator': planned, 'rate': 1.0})
        for trial in report['trials']:
            with self.subTest(trial=trial['trial_key']):
                self.assertEqual(trial['outcome'], 'equivalent')
                self.assertIs(trial['equivalence']['mission']['passed'], True)
                self.assertIs(trial['equivalent_and_mission'], True)


if __name__ == '__main__':
    unittest.main()
