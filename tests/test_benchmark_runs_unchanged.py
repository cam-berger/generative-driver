"""What the kit-fixes Features 06 and 07 left unchanged for a benchmark run: its acquire worker's one tool, and an
interpretation with no kit analysis, no tasks, no documents, no contract files and no device-first mode.

The two tests came with `main` in `tests/test_interpret_tasks.py` and `tests/test_input_paths.py`
(docs/features/kit-fixes/06-interpret-tasks.md, 06-B12, and docs/features/kit-fixes/07-input-paths.md, 07-B9) and
keep their names. They moved here because they run a registered case, which a tool test module may not (06-B2 of
the untangle Run, docs/features/untangle/06-benchmark-runs.md). They run on `tq9-v2`, started as the bench harness
starts it.

In-process runs with scripted workers and synthetic peers; temporary homes only, no model, no hardware and no
password.
"""
import json
import unittest
from unittest.mock import patch

from input_path_fixtures import RunCase
from interpret_task_fixtures import INVENTORY, events, launches, scripted_worker, seen, stand_in_ghidra, stopped

CASE = 'tq9-v2'
CONTRACT_FILES = ('CONTRACT.md', 'contract.py', 'contract.json')


def harness_start(test, root, **request):
    """What the bench harness sends to start a `tq9-v2` run with `request`: a supplied run on a device of synthetic
    peers under `root`, with the firmware and the mission the harness froze."""
    from suite_fixtures import supplied_start
    return supplied_start(test, root, case=CASE, effects=['write', 'actuate'], **request)


class AcquireTests(RunCase):
    def test_07_B9_an_emulated_benchmark_acquire_worker_gets_its_one_tool_as_before(self):
        acquired, interpreted = {}, {}

        def acquire(worker):
            acquired.update(tools=worker.tools(), document=worker.tool('acquire_document', name='datasheet.pdf'))

        def interpret(worker):
            interpreted.update(tools=list(worker.request.allowed_tools), files=sorted(
                p.relative_to(worker.workspace).as_posix() for p in worker.workspace.rglob('*') if p.is_file()))
        start = harness_start(self, self.root / 'device', executor_config={'command': ['never-launched']})
        result = self.run_with({'acquire': acquire, 'interpret': interpret}, **start)
        self.assertEqual(acquired['tools'], ['acquire_firmware_artifact'])
        self.assertIsInstance(acquired['document'], ValueError)
        self.assertIn('not assigned', str(acquired['document']))
        # The interpret worker of a benchmark run: no documents, no device-first instructions, no contract files.
        self.assertTrue(interpreted, result['reason'])
        self.assertEqual(interpreted['tools'], [])
        self.assertIn('TASKS.json', interpreted['files'])
        for name in ('DOCUMENTS.json', 'DEVICE_FIRST.md', *CONTRACT_FILES):
            self.assertNotIn(name, interpreted['files'])
        self.assertFalse([name for name in interpreted['files'] if name.startswith('documents/')])
        [assignment] = self.assigned('interpret')
        self.assertNotIn('device_first', assignment)
        [handoff] = self.handoffs(result, 'interpret')
        self.assertNotIn('interpretation', handoff['notes']['inline']['configurator'])
        self.assertFalse([event['kind'] for event in self.events()
                          if event['kind'].startswith(('kit_analysis.', 'task.'))])


class BenchmarkTests(unittest.TestCase):
    def test_06_B12_a_benchmark_run_prepares_runs_and_checks_interpretation_as_today(self):
        import tempfile
        from pathlib import Path
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        ghidra, java = stand_in_ghidra(root)
        folder = root / 'worker'
        prepared, observed = [], []

        def prepare_stage(stage, run_dir, workspace, accepted=None, options=None, *, firmware, mission):
            prepared.append((stage, options))
            if stage != 'interpret':
                return {'skip': 'Not part of this test'}
            return {'inputs': [], 'allowed_tools': [], 'objective': 'Interpret the case', 'context': {'case': CASE}}

        def observe_stage(stage, run_dir, workspace, report, accepted=None, options=None, *, firmware, mission):
            observed.append(stage)
            return {'ok': True, 'artifacts': [], 'checks': []}

        with patch('generative_driver.supplied.stages.prepare_stage', prepare_stage), \
                patch('generative_driver.supplied.stages.observe_stage', observe_stage):
            controller = Controller(root / 'home')
            try:
                spec = harness_start(self, root / 'device', case_options={'ghidra_home': ghidra, 'java_home': java},
                                     goal='A benchmark run with the analysis named and tasks asked for',
                                     executor_config={'command': scripted_worker(folder, inventory=INVENTORY)},
                                     interpretation='tasks')
                run = controller.call('start', spec)
                result = stopped(controller, run)
            finally:
                controller.close()
        self.assertNotIn(result['status'], ('failed',), result)
        # No kit analysis, no index, no tasks.
        self.assertFalse(events(controller, run, 'kit_analysis.started', 'kit_analysis.finished', 'task.started',
                                'task.finished'))
        self.assertEqual(launches(ghidra), [])
        self.assertEqual([record['task'] for record in seen(folder)], ['interpret'])
        # The interpret worker got what the benchmark prepared, nothing more.
        worker, = seen(folder)
        task = json.loads(worker['prompt'].partition('\n')[2])
        self.assertEqual((task['objective'], task['inputs']), ('Interpret the case', []))
        self.assertEqual(set(task['context']) - {'prior_notes'}, {'case'})
        self.assertFalse([name for name in worker['files'] if 'ANALYSIS_INDEX' in name or name.startswith('analysis/')])
        interpret = [options for stage, options in prepared if stage == 'interpret']
        self.assertEqual(len(interpret), 1)
        self.assertEqual(observed, ['interpret'])
        handoff, = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret']
        self.assertNotIn('interpretation', handoff['notes']['inline']['configurator'])
        self.assertFalse((controller.home / 'runs' / run['run_id'] / 'kit-analysis').exists())
        self.assertFalse((controller.home / 'runs' / run['run_id'] / 'interpret-tasks').exists())


if __name__ == '__main__':
    unittest.main()
