"""Installed benchmark commands preserve explicit experiment selection."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class CorpusWorkflowTests(unittest.TestCase):
    def command(self, *arguments):
        return subprocess.run([sys.executable, '-m', 'generative_driver', 'benchmark', 'corpus',
                               *arguments], capture_output=True, text=True, timeout=30)

    def test_operator_can_exclude_a_lineage_without_deleting_its_coverage_slot(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / 'plan.json'
            started = self.command('plan', '--repetitions', '1', '--exclude', 'grbl-motion',
                                   'Timer observer is outside this experiment.', '--output', str(plan_path))
            self.assertEqual(started.returncode, 0, started.stderr + started.stdout)
            plan = json.loads(plan_path.read_text())
            self.assertEqual(len(plan['slots']), 7)
            motion = next(row for row in plan['slots'] if row['lineage'] == 'grbl-motion')
            self.assertEqual(motion['status'], 'excluded')
            self.assertEqual(motion['selection_reason'], 'Timer observer is outside this experiment.')
            reported = self.command('report', '--plan', str(plan_path))
            self.assertEqual(reported.returncode, 0, reported.stderr + reported.stdout)
            report = json.loads(reported.stdout)
            self.assertEqual(report['counts']['excluded'], 1)
            self.assertEqual(report['demonstrated_coverage']['denominator'], 7)
            self.assertEqual(report['selected_coverage']['denominator'], 6)
            self.assertIsNone(report['measured_success']['rate'])

    def test_operator_can_check_admission_before_launching_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / 'plan.json'
            options_path = Path(directory) / 'options.json'
            options_path.write_text('{"evaluator_password_files": {}}')
            planned = self.command('plan', '--repetitions', '1', '--include', 'zephyr-modbus',
                                   '--output', str(plan_path))
            self.assertEqual(planned.returncode, 0, planned.stderr + planned.stdout)
            checked = self.command('check', '--plan', str(plan_path), '--options', str(options_path))
            self.assertEqual(checked.returncode, 1, checked.stderr + checked.stdout)
            result = json.loads(checked.stdout)
            self.assertFalse(result['ok'])
            self.assertFalse(result['model_benchmark'])
            selected = next(row for row in result['cases'] if row['lineage'] == 'zephyr-modbus')
            self.assertEqual(selected['status'], 'missing-credential')
            self.assertEqual(sum(row['status'] == 'excluded' for row in result['cases']), 6)

    def test_operator_can_report_a_live_suite_through_the_corpus_command(self):
        from generative_driver.benchmark import main
        from test_corpus import measured_fixture
        plan, suite = measured_fixture()
        slot = {**suite['experiment']['comparison_identity']['trials'][0], 'status': 'pending',
                'run_id': None, 'outcome_category': None}
        page = {'ok': True, 'suite_id': 'example-suite', 'status': 'running', 'created': 0, 'updated': 1,
                'experiment': suite['experiment'], 'trials': [slot], 'next_offset': None}
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / 'plan.json'
            plan_path.write_text(json.dumps(plan))
            report_path = Path(directory) / 'coverage.json'
            output = io.StringIO()
            with patch('generative_driver.client.call', return_value=page), \
                    redirect_stdout(output):
                result = main(['corpus', 'report', '--plan', str(plan_path), '--suite-id', 'example-suite',
                               '--output', str(report_path)])
            self.assertEqual(result, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['counts']['not_run'], 1)
            self.assertEqual(report['counts']['blocked'], 6)
            self.assertIsNone(report['measured_success']['rate'])
            self.assertEqual(report['plan_sha256'], plan['plan_sha256'])
            exported = json.loads((Path(directory) / 'coverage-suite' / 'suite.json').read_text())
            self.assertEqual(exported['suite_id'], 'example-suite')
            self.assertEqual(json.loads(report_path.read_text()), report)
