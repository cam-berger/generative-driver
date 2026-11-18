"""The benchmark's side of the move of the stage code to the tool.

Feature 05 of the untangle Run (docs/features/untangle/05-supplied-device-stages.md): 05-B3, the last point of 05-B4
and the last four points of 05-B6. The stage code is the tool's package `generative_driver.supplied` now. The
benchmark gives the service a case's firmware and mission in one place, and the grader's copies of what the stage
code calls agree with the tool's. This file is a benchmark item: it reads the benchmark package. The tool's side of
the Feature is in `tests/test_supplied_stages.py`, which stays with the tool.

Temporary directories and homes only; no model, no hardware, no real password.
"""
import hashlib
import json
import os
import signal
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver import benchmark
from generative_driver.benchmark_support import capabilities as grader_rules, case_assets, emulated_actions, evaluator
from generative_driver.benchmark_support.grader_identity import grader_closure
from generative_driver.benchmark_support.registry import pin_case
from generative_driver.benchmark_support.snapshots import snapshot_case
from generative_driver.supplied import capabilities as tool_rules, package_runner, stages
from tool_fixtures import package_env
from test_benchmark_legacy_cases_dropped import GRADING_CODE
from test_evaluation import PASS, reference
from tool_fixtures import BINDING, TemporaryDirectoryCase, hand_written_package
from test_unlocked_keys import SealedTrialFixture

CASES = ('tq9-v2', 'sampled-sensor-v1', 'parameter-store-v1', 'zephyr-modbus-v1')


class SuppliedInputsTests(TemporaryDirectoryCase):
    """The one place where the benchmark gives the service a case's firmware and mission."""

    def test_05_B3_supplied_inputs_gives_the_firmware_and_the_mission_of_each_case_and_nothing_else(self):
        for case in CASES:
            with self.subTest(case=case):
                given = benchmark.supplied_inputs(case, self.directory() / 'run')
                self.assertEqual(sorted(given), ['firmware', 'mission'])
                firmware, mission = given['firmware'], given['mission']
                self.assertEqual(sorted(firmware), ['path', 'sha256'])
                self.assertIsInstance(firmware['path'], str)
                self.assertEqual(hashlib.sha256(Path(firmware['path']).read_bytes()).hexdigest(), firmware['sha256'])
                self.assertEqual(pin_case(case, 'original', 0)['image_hashes'], {'firmware.bin': firmware['sha256']})
                filed = json.loads((case_assets.case_root() / 'missions' / f'{case}.json').read_text(encoding='utf-8'))
                self.assertEqual(filed['case_id'], case)
                self.assertEqual((mission['tasks'], mission['reuse_objective']),
                                 (filed['tasks'], filed['reuse_objective']))
                # Plain data that JSON can hold, as a client will supply it.
                self.assertEqual(json.loads(json.dumps(given)), given)

    def test_05_B3_the_mission_holds_no_key_but_tasks_objective_and_conditions_so_no_case_name(self):
        keys = {case: sorted(benchmark.supplied_inputs(case, self.directory() / 'run')['mission']) for case in CASES}
        plain = ['reuse_objective', 'tasks']
        self.assertEqual(keys, {'tq9-v2': plain, 'sampled-sensor-v1': plain, 'parameter-store-v1': plain,
                                'zephyr-modbus-v1': ['operating_conditions', 'reuse_objective', 'tasks']})
        mission = benchmark.supplied_inputs('zephyr-modbus-v1', self.directory() / 'run')['mission']
        filed = json.loads((case_assets.case_root() / 'missions' / 'zephyr-modbus-v1.json').read_text(encoding='utf-8'))
        self.assertEqual(mission['operating_conditions'], filed['operating_conditions'])
        self.assertEqual(len(mission['operating_conditions']), 3)

    def test_05_B3_the_firmware_of_a_run_with_an_execution_snapshot_lies_in_the_runs_inputs_folder(self):
        run = self.directory() / 'run'
        snapshot_case(run, pin_case('tq9-v2', 'original', 0), {})
        firmware = benchmark.supplied_inputs('tq9-v2', run, {'scenario_id': 'original'})['firmware']
        self.assertEqual(Path(firmware['path']), run / 'benchmark' / 'inputs' / 'firmware.bin')
        self.assertEqual(hashlib.sha256(Path(firmware['path']).read_bytes()).hexdigest(), firmware['sha256'])
        # A run without one reads the case's own folder.
        other = benchmark.supplied_inputs('tq9-v2', self.directory() / 'run')['firmware']
        self.assertEqual(Path(other['path']), case_assets.case_root() / 'cases' / 'tq9-v2' / 'firmware.bin')
        self.assertEqual(other['sha256'], firmware['sha256'])

    def test_05_B3_a_scenario_that_is_neither_absent_nor_original_is_refused(self):
        run = self.directory() / 'run'
        for options in (None, {}, {'scenario_id': None}, {'scenario_id': 'original'}):
            with self.subTest(options=options):
                self.assertEqual(sorted(benchmark.supplied_inputs('tq9-v2', run, options)), ['firmware', 'mission'])
        for scenario in ('shifted', '', 0):
            with self.subTest(scenario=scenario), self.assertRaisesRegex(ValueError, '^Unknown case scenario$'):
                benchmark.supplied_inputs('tq9-v2', run, {'scenario_id': scenario})

    def test_05_B3_the_setup_smoke_has_no_agent_stages(self):
        with self.assertRaisesRegex(ValueError, '^Setup smoke has no agent stages$'):
            benchmark.supplied_inputs('setup-smoke', self.directory() / 'run')


class ScriptedRunTests(SealedTrialFixture):
    """A scripted run of tq9-v2 through the real admission, `_prepare` and `_check`, graded by the bench harness. The
    grader's hidden sequences are scripted, since the fixture's answer key holds none. Its judgement of the mission is
    the real one."""

    def test_05_B4_the_reuse_handoff_of_a_scripted_run_holds_no_check_and_the_verdict_still_carries_the_mission(self):
        with patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            _, record, result = self.staged_trial('recorded')
        self.assertEqual(result['status'], 'completed', result['reason'])
        reuse = result['accepted_handoffs'][-1]
        self.assertEqual((reuse['stage'], len(reuse['artifacts'])), ('reuse', 1))
        self.assertEqual(reuse['checks'], [])
        self.assertEqual(reuse['notes']['inline']['configurator']['checks'], [])
        noted = [notes for notes in self.controller.call('status', {'run_id': record['run_id']})['stage_notes']
                 if notes['stage'] == 'reuse']
        self.assertEqual([notes['failed_checks'] for notes in noted], [[]])
        # The run recorded the fresh worker's four package calls, and the grader judged the mission from them.
        events = self.home / 'runs' / record['run_id'] / 'supplied' / 'package-events.jsonl'
        rows = [json.loads(line) for line in events.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(row['assignment_id'], row['operation']) for row in rows],
                         [(reuse['assignment_id'], name) for name in ('measure', 'arm', 'set_duty', 'disarm')])
        self.assertEqual(record['status'], 'evaluated', record)
        self.assertEqual(record['equivalence']['mission'], {'name': 'fresh_worker_mission', 'passed': True})


def prints(text, status=0):
    """The command line of a hand-written package that prints `text` and exits with `status`."""
    return f'import sys\nsys.stdout.write({text!r})\nsys.exit({status})\n'


# The command line of each hand-written package, with the error code its call must end in, or None for a result the
# package printed itself.
PACKAGES = {
    'a well-formed result': (prints('{"ok": true, "outputs": {"level": 40}, "units": {"level": "percent"}, '
                                    '"transcript": [{"op": "exchange"}]}'), None),
    'an error that carries a source': (prints('{"ok": false, "outputs": {}, "units": {}, "transcript": [], "error": '
                                              '{"fault": "host", "source": "package-process", "code": "printed"}}'),
                                       'printed'),
    'outputs that are a list': (prints('{"ok": true, "outputs": [40], "units": {}}'), 'malformed_result'),
    'a result nested 70 levels deep': (prints('{"ok": true, "outputs": ' + '{"inner": ' * 68 + '{}' + '}' * 68 + '}'),
                                       'malformed_result'),
    'a number that is not finite': (prints('{"ok": true, "outputs": {"level": NaN}, "units": {}}'),
                                    'malformed_result'),
    'text that is not JSON and status 3': (prints('the package failed', 3), 'no_result'),
    'nothing printed and status 0': (prints(''), 'no_result'),
}
OUTLASTS = 'import time\ntime.sleep(60)\n'
KILLS_ITSELF = 'import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n'


class AgreementTests(TemporaryDirectoryCase):
    """The grader's copies of what the stage code calls, beside the tool's: they give the same answers."""

    def answers(self, package, operation='level', parameters=None, grants=('read', 'write')):
        """What the tool's package runner and the grader's answer for the same call of `package`."""
        with patch.dict(os.environ, package_env()):
            return tuple(runner.package_invoker(package, BINDING, list(grants))(operation, parameters or {'samples': 1})
                         for runner in (package_runner, emulated_actions))

    def assert_same_answer(self, package, code):
        """The two runners answer the call of `package` alike, and the answer's error has `code`."""
        tool, grader = self.answers(package)
        self.assertEqual(tool, grader)
        self.assertEqual((grader.get('error') or {}).get('code'), code, grader)
        return grader

    def test_05_B6_the_tools_capability_rules_and_the_graders_hold_the_same_bytes(self):
        tool, grader = Path(tool_rules.__file__), Path(grader_rules.__file__)
        self.assertEqual((tool.parent.name, grader.parent.name), ('supplied', 'benchmark_support'))
        self.assertEqual(tool.read_bytes(), grader.read_bytes())

    def test_05_B6_the_graders_package_invoker_gives_the_tools_answer_for_each_package_that_exits_by_itself(self):
        for name, (body, code) in PACKAGES.items():
            with self.subTest(package=name):
                self.assert_same_answer(hand_written_package(self.directory(), body), code)
        tool = {name: self.answers(hand_written_package(self.directory(), PACKAGES[name][0]))[0]
                for name in ('a well-formed result', 'an error that carries a source')}
        self.assertEqual(tool['a well-formed result'], {'ok': True, 'outputs': {'level': 40},
                                                        'units': {'level': 'percent'},
                                                        'transcript': [{'op': 'exchange'}]})
        # A `source` is the runners' own label: one the package printed is dropped.
        self.assertEqual(tool['an error that carries a source']['error'], {'fault': 'host', 'code': 'printed'})

    def test_05_B6_both_runners_report_a_package_that_outlasts_the_call_limit_alike(self):
        package = hand_written_package(self.directory(), OUTLASTS)
        with patch.object(package_runner, 'PACKAGE_CALL_SECONDS', 1), \
                patch.object(emulated_actions, 'PACKAGE_CALL_SECONDS', 1):
            answer = self.assert_same_answer(package, 'timeout')
        self.assertEqual(answer['error']['message'], 'The package call did not finish within 1 s')

    def test_05_B6_both_runners_report_a_package_whose_process_cannot_start_alike(self):
        # The process starts in the folder that holds the package, and this package's folder does not exist.
        answer = self.assert_same_answer(self.directory() / 'absent' / 'package', 'spawn')
        self.assertEqual((answer['error']['fault'], answer['error']['source']), ('host', 'package-process'))

    @unittest.skipUnless(hasattr(signal, 'SIGKILL'), 'This platform has no SIGKILL for a package to end itself with')
    def test_05_B6_both_runners_report_a_package_killed_by_a_signal_alike(self):
        answer = self.assert_same_answer(hand_written_package(self.directory(), KILLS_ITSELF), 'signal')
        self.assertEqual(answer['error']['message'], 'The package process was killed by signal %d' % signal.SIGKILL)

    def test_05_B6_the_same_comparison_finds_a_runner_whose_limit_differs(self):
        package = hand_written_package(self.directory(), PACKAGES['a result nested 70 levels deep'][0])
        with patch.object(package_runner, 'MAX_RESULT_DEPTH', 80):
            tool, grader = self.answers(package)
        self.assertNotEqual(tool, grader)
        self.assertEqual((tool['ok'], grader['error']['code']), (True, 'malformed_result'))

    def test_05_B6_the_graders_artifact_finder_gives_the_answer_of_the_stage_packages(self):
        finder = stages._artifact
        self.assertIsNot(finder, case_assets.accepted_artifact, 'The stage package still uses the grader\'s finder')
        self.assertTrue(finder.__module__.startswith('generative_driver.supplied.'), finder.__module__)
        one, other = 'a' * 64, 'b' * 64
        held = {'stage': 'probe', 'artifacts': [{'path': '/accepted/notes.md', 'sha256': other},
                                                 {'path': '/accepted/model.json', 'sha256': one}]}
        numbered = {'stage': 'probe', 'artifacts': [{'path': '/accepted/2-model.json', 'sha256': one}]}
        without = {'stage': 'probe', 'artifacts': [{'path': '/accepted/0-notes.md', 'sha256': one},
                                                    {'path': '/accepted/x-model.json', 'sha256': one}]}
        for name, handoff, sha256, expected in (
                ('holds the artifact', held, None, (Path('/accepted/model.json'), one)),
                ('holds it with the hash asked for', held, one, (Path('/accepted/model.json'), one)),
                ('holds it under a numbered prefix', numbered, None, (Path('/accepted/2-model.json'), one)),
                ('holds it with another hash', held, other, None), ('does not hold it', without, None, None),
                ('has no artifacts', {'stage': 'probe'}, None, None), ('no handoff', None, None, None)):
            with self.subTest(handoff=name):
                self.assertEqual(case_assets.accepted_artifact(handoff, 'model.json', sha256), expected)
                self.assertEqual(finder(handoff, 'model.json', sha256), expected)

    def test_05_B6_grader_closure_names_the_same_34_modules_as_before(self):
        self.assertEqual(len(GRADING_CODE), 34)
        self.assertEqual(grader_closure(), GRADING_CODE)
        self.assertEqual([name for name in grader_closure() if 'supplied' in name.split('.')], [])


if __name__ == '__main__':
    unittest.main()
