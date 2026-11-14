"""Interpretation as tasks: the inventory, the handler tasks, the assembly, the cross-check, how each task is recorded
and the repair (docs/features/kit-fixes/06-interpret-tasks.md, 06-B5 to 06-B11).

The configurator in a temporary home, a stand-in analyzer and scripted task workers that recognise their task from
their workspace's instructions; no Ghidra, Java, model or device."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from interpret_task_fixtures import (FRAGMENTS, IDENTIFY, INVENTORY, MEASURE, SINGLE_MODEL, STATUS, RunCase,
                                     cortex_m_image, events, fragment, gone, interpret_notes, launches, scripted_worker,
                                     seen, stand_in_ghidra, stopped)


def interpretation(result, revision=None):
    """The interpret notes' account of how the stage interpreted."""
    return interpret_notes(result, revision)['configurator']['interpretation']


class TaskCase(RunCase):
    """A task-mode run in a temporary home."""

    def tasks_run(self, script=None, **params):
        """A run started with interpretation=tasks whose scripted workers follow `script` (by default: the canned
        inventory and fragments, and a cross-check that keeps the assembled files)."""
        if script is None:
            script = {'inventory': INVENTORY, 'fragments': FRAGMENTS, 'single': SINGLE_MODEL}
        return self.start(cortex_m_image(), script=script, interpretation='tasks', **params)


class ModeTests(TaskCase):
    def test_06_B5_interpretation_is_single_or_tasks_and_any_other_value_is_refused_at_start(self):
        controller = self.controller()
        start = {'goal': 'Recover the interface', 'executor_config': {'command': ['no-such-runtime']}}
        for value in ('parallel', 'Tasks', '', 1, ['tasks']):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as caught:
                    controller.call('start', {**start, 'interpretation': value})
                self.assertIn('interpretation', str(caught.exception))
        for value in ('single', 'tasks'):
            with self.subTest(value=value):
                self.assertTrue(controller.call('start', {**start, 'interpretation': value})['ok'])

    def test_06_B5_a_tasks_run_whose_analysis_was_made_interprets_as_tasks_and_the_default_stays_single(self):
        controller, run, _, folder = self.tasks_run()
        stopped(controller, run)
        self.assertEqual(seen(folder)[1]['task'], 'inventory', [record['task'] for record in seen(folder)])
        default = self.root / 'default'
        controller, run, _, folder = self.start(cortex_m_image(), root=default, home=default / 'home',
                                                script={'inventory': INVENTORY, 'single': SINGLE_MODEL})
        result = stopped(controller, run)
        self.assertEqual([record['task'] for record in seen(folder)], ['acquire', 'single'])
        self.assertEqual(interpretation(result)['mode'], 'single')

    def test_06_B5_a_tasks_run_whose_analysis_was_not_made_runs_the_single_worker_and_its_notes_say_so(self):
        controller, run, _, folder = self.tasks_run(named=False)
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([record['task'] for record in seen(folder)], ['acquire', 'single'])
        account = interpretation(result)
        self.assertEqual((account['mode'], account['requested']), ('single', 'tasks'))
        self.assertTrue(any('kit analysis was not made' in note for note in account['notes']), account)


LISTEN = {'schema': 'serial-listen/1', 'baudrate': 115200, 'unsolicited_output': True}


class InventoryTests(TaskCase):
    def test_06_B6_the_inventory_is_a_fresh_worker_given_the_index_the_dispatchers_the_strings_and_the_runs_tasks(self):
        controller, run, _, folder = self.tasks_run(evidence={'serial-listen.json': LISTEN})
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        inventory, = seen(folder, 'inventory')
        self.assertTrue({'ANALYSIS_INDEX.json', 'DISPATCHERS.c', 'strings.tsv', 'TASKS.json', 'serial-listen.json',
                         'BINDING_CONTEXT.json', 'INVENTORY.md', 'TASK.json'} <= set(inventory['files']),
                        inventory['files'])
        # Nothing else: not the export, not the image, not the single worker's brief.
        self.assertFalse([name for name in inventory['files']
                          if name.startswith('analysis/') or name in ('image.bin', 'BRIEF.md', 'model.json')])
        # A fresh worker in a workspace of its own.
        workspace = Path(inventory['workspace'])
        self.assertTrue(workspace.name.startswith('gd-task-')
                        and workspace.parent == Path(tempfile.gettempdir()).resolve(), workspace)
        self.assertIn(str(workspace / 'INVENTORY.md'), inventory['prompt'])
        finished, = [e['data'] for e in events(controller, run, 'kit_analysis.finished')]
        kept = self.run_dir(controller, run) / finished['folder']
        self.assertEqual(inventory['index_sha256'],
                         hashlib.sha256((kept / 'ANALYSIS_INDEX.json').read_bytes()).hexdigest())
        # The dispatcher candidates' code by rank, and no other function's.
        code = inventory['DISPATCHERS.c']
        self.assertLess(code.index('void dispatch(char *line)'), code.index('void menu(int key)'))
        self.assertNotIn('uart_puts("DEMO-42")', code)
        self.assertEqual(inventory['sizes']['strings.tsv'], (kept / 'export' / 'strings.tsv').stat().st_size)
        self.assertEqual(inventory['TASKS.json']['tasks'], {'requested': {'meaning': 'Recover the interface of the '
                                                                                     'canned firmware'}})
        self.assertEqual(inventory['TASK.json']['kind'], 'inventory')

    def test_06_B6_the_inventorys_code_and_strings_stay_within_their_bounds(self):
        with patch('generative_driver.analysis_index.CODE_MAX_BYTES', 250), \
                patch('generative_driver.analysis_index.STRINGS_MAX_BYTES', 150):
            controller, run, _, folder = self.tasks_run()
            stopped(controller, run)
        inventory, = seen(folder, 'inventory')
        self.assertLessEqual(inventory['sizes']['DISPATCHERS.c'], 250)
        self.assertRegex(inventory['DISPATCHERS.c'], r'Cut short|Left out')
        self.assertLessEqual(inventory['sizes']['strings.tsv'], 150)
        self.assertGreater(inventory['TASK.json']['strings_left_out'], 0)

    def test_06_B6_an_inventory_that_lists_no_command_or_cannot_be_read_sends_the_attempt_to_the_single_worker(self):
        many = [{'name': 'command_%d' % n, 'handlers': [], 'evidence': []} for n in range(65)]
        for index, (inventory, why) in enumerate([('unreadable', 'cannot be read'),
                                                  ({**INVENTORY, 'commands': []}, 'lists no command'),
                                                  ({**INVENTORY, 'identity': 'reboot'}, 'identity is not one of'),
                                                  ({**INVENTORY, 'commands': many, 'identity': 'command_0'},
                                                   'more than the model')]):
            with self.subTest(why=why):
                root = self.root / str(index)
                controller, run, _, folder = self.tasks_run({'inventory': inventory, 'fragments': FRAGMENTS,
                                                             'single': SINGLE_MODEL}, root=root, home=root / 'home')
                result = stopped(controller, run)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([record['task'] for record in seen(folder)], ['acquire', 'inventory', 'single'])
                account = interpretation(result)
                self.assertEqual(account['mode'], 'single')
                self.assertTrue(any(why in note for note in account['notes']), account)
                handoff, = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret']
                model = json.loads((Path(handoff['artifacts'][0]['path']) / 'model.json').read_text(encoding='utf-8'))
                self.assertEqual(model, SINGLE_MODEL)

    def test_06_B6_an_inventory_worker_that_changes_a_sealed_input_stops_the_run(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'tamper': 'inventory'})
        result = stopped(controller, run)
        self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'integrity'), result)
        self.assertIn('TASKS.json', result['reason'])
        self.assertEqual([record['task'] for record in seen(folder)], ['acquire', 'inventory'])


KIT = {'TASKS.json', 'INTERFACE_MODEL.md', 'CONTRACT.md', 'contract.py', 'validate.py', 'qualify.py'}


class HandlerTests(TaskCase):
    def test_06_B7_a_handler_task_per_command_runs_one_at_a_time_the_identity_commands_first_with_its_own_inputs(self):
        controller, run, _, folder = self.tasks_run()
        stopped(controller, run)
        handlers = seen(folder, 'handler')
        self.assertEqual([record['command'] for record in handlers], ['identify', 'measure'])
        self.assertEqual(len({record['workspace'] for record in handlers} | {r['workspace'] for r in seen(folder)}),
                         len(seen(folder)))
        identify, measure = handlers
        frame = {key: INVENTORY[key] for key in ('schema', 'device', 'channel', 'framing')}
        for record, command in ((identify, INVENTORY['commands'][1]), (measure, INVENTORY['commands'][0])):
            with self.subTest(command=command['name']):
                self.assertTrue(KIT | {'MODEL_FRAME.json', 'COMMAND.json', 'CODE.c', 'STRINGS.json', 'HANDLER.md',
                                       'TASK.json'} <= set(record['files']), record['files'])
                self.assertFalse([name for name in record['files'] if name.startswith('analysis/')
                                  or name in ('ANALYSIS_INDEX.json', 'inventory.json', 'image.bin', 'BRIEF.md')])
                self.assertEqual((record['MODEL_FRAME.json'], record['COMMAND.json']), (frame, command))
                self.assertIn('HANDLER.md', record['prompt'])
        # Only the tasks after the identity command's get the identity operation that task wrote.
        self.assertNotIn('IDENTITY.json', identify['files'])
        self.assertEqual(measure['IDENTITY.json'], {'operation': 'identify', 'model': FRAGMENTS['identify']['model']})
        # Its handler functions' code and their callees', nearest first, and the strings they reference.
        functions = lambda code: [line.split(': ')[1] for line in code.splitlines()
                                  if line.startswith('// entry address')]
        self.assertEqual(functions(identify['CODE.c']), ['08000300', '08000400', '08000440'])
        self.assertEqual(functions(measure['CODE.c']), ['08000340', '08000400', '08000480', '08000440'])
        self.assertEqual([string['value'] for string in identify['STRINGS.json']], ['DEMO-42'])
        self.assertEqual([string['value'] for string in measure['STRINGS.json']], ['T:%d.%d'])

    def test_06_B7_a_handlers_code_stays_within_its_bound_with_a_note_of_what_was_cut(self):
        with patch('generative_driver.analysis_index.CODE_MAX_BYTES', 450):
            controller, run, _, folder = self.tasks_run()
            stopped(controller, run)
        measure = seen(folder, 'handler')[1]
        self.assertLessEqual(measure['sizes']['CODE.c'], 450)
        self.assertIn('void handle_measure(', measure['CODE.c'])
        self.assertRegex(measure['CODE.c'], r'(Cut short|Left out): .*08000440')

    def test_06_B7_a_handler_that_wrote_no_usable_fragment_leaves_its_command_out_with_a_note(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'single': SINGLE_MODEL,
                                                     'fragments': {**FRAGMENTS, 'measure': 'unusable'}})
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([record['command'] for record in seen(folder, 'handler')], ['identify', 'measure'])
        self.assertTrue(any('measure' in note and 'no usable fragment' in note
                            for note in interpretation(result)['notes']), interpretation(result))

    def test_06_B7_an_unusable_identity_fragment_sends_the_attempt_to_the_single_worker(self):
        for index, written in enumerate(('unusable', None)):
            with self.subTest(written=written):
                root = self.root / str(index)
                fragments = {'measure': FRAGMENTS['measure'], **({'identify': written} if written else {})}
                controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'single': SINGLE_MODEL,
                                                             'fragments': fragments}, root=root, home=root / 'home')
                result = stopped(controller, run)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([(record['task'], record['command']) for record in seen(folder)][1:],
                                 [('inventory', None), ('handler', 'identify'), ('single', None)])
                account = interpretation(result)
                self.assertEqual(account['mode'], 'single')
                self.assertTrue(any('identity' in note and 'identify' in note for note in account['notes']), account)


def handed_off(result, revision=None):
    """The files of the interpret stage's handoff (of `revision` when given), by name, as JSON where they are."""
    handoff = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret'
               and (revision is None or h['revision'] == revision)][-1]
    folder = Path(handoff['artifacts'][0]['path'])
    read = lambda path: (json.loads(path.read_text(encoding='utf-8')) if path.suffix == '.json'
                         else path.read_text(encoding='utf-8'))
    return {path.name: read(path) for path in folder.iterdir() if path.is_file()}


ASSEMBLED = {'schema': 'interface-model/3', 'device': {'name': 'DEMO-42'}, 'channel': {'type': 'tcp'},
             'identity': {'operation': 'identify'}, 'operations': {'identify': IDENTIFY, 'measure': MEASURE},
             'provenance': FRAGMENTS['identify']['provenance'] + FRAGMENTS['measure']['provenance']}


class AssemblyTests(TaskCase):
    def test_06_B8_the_configurator_assembles_the_fragments_into_the_model_its_replies_and_its_contract(self):
        controller, run, _, folder = self.tasks_run()
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        files = handed_off(result)
        self.assertEqual(files['model.json'], ASSEMBLED)
        self.assertEqual(files['replies.json'], {'schema': 'interface-replies/1', 'operations': {
            'identify': {'steps': [['DEMO-42']]}, 'measure': {'steps': [['T:21.5']]}}})
        self.assertEqual(files['contract.json'], {
            'schema': 'driver-contract/1', 'device': {'name': 'DEMO-42'}, 'identity': {'operation': 'identify'},
            'operations': {name: FRAGMENTS[name]['contract'] for name in ('identify', 'measure')},
            'provenance': ASSEMBLED['provenance']})
        self.assertEqual(interpretation(result)['mode'], 'tasks')
        self.assertNotIn('single', [record['task'] for record in seen(folder)])

    def test_06_B8_a_fragment_that_does_not_validate_on_its_own_or_whose_name_is_taken_is_left_out_with_a_note(self):
        inventory = {**INVENTORY, 'commands': INVENTORY['commands'] + [
            {'name': 'status', 'handlers': ['08000500'], 'evidence': ['menu case 0x1 at 08000530']}]}
        cases = [('does not validate', {**FRAGMENTS, 'measure': fragment('measure', {**MEASURE, 'steps': []}, []),
                                        'status': fragment('status', STATUS, ['S:ok'])}, ['identify', 'status']),
                 ('already taken', {**FRAGMENTS, 'status': fragment('measure', STATUS, ['S:ok'])},
                  ['identify', 'measure']),
                 ('no usable fragment', {**FRAGMENTS, 'measure': 'unusable',
                                         'status': fragment('status', STATUS, ['S:ok'])}, ['identify', 'status'])]
        for index, (why, fragments, kept) in enumerate(cases):
            with self.subTest(why=why):
                root = self.root / str(index)
                controller, run, _, folder = self.tasks_run({'inventory': inventory, 'fragments': fragments,
                                                             'single': SINGLE_MODEL}, root=root, home=root / 'home')
                result = stopped(controller, run)
                self.assertEqual(result['status'], 'completed', result)
                files = handed_off(result)
                self.assertEqual(sorted(files['model.json']['operations']), kept)
                self.assertEqual(sorted(files['replies.json']['operations']), kept)
                self.assertEqual(sorted(files['contract.json']['operations']), kept)
                self.assertEqual(files['model.json']['operations']['identify'], IDENTIFY)
                self.assertTrue(any(why in note for note in interpretation(result)['notes']), interpretation(result))

    def test_06_B8_an_identity_fragment_that_does_not_validate_on_its_own_sends_the_attempt_to_the_single_worker(self):
        unchecked = {**IDENTIFY, 'steps': [{key: value for key, value in IDENTIFY['steps'][0].items()
                                            if key != 'expect'}]}
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'single': SINGLE_MODEL, 'fragments': {
            **FRAGMENTS, 'identify': fragment('identify', unchecked, ['DEMO-42'])}})
        result = stopped(controller, run)
        self.assertEqual(handed_off(result)['model.json'], SINGLE_MODEL)
        account = interpretation(result)
        self.assertEqual(account['mode'], 'single')
        self.assertTrue(any('identity' in note and 'does not validate' in note for note in account['notes']), account)


class CrossCheckTests(TaskCase):
    def test_06_B9_the_cross_check_starts_from_the_assembled_files_and_whatever_it_leaves_is_handed_off(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'fragments': FRAGMENTS,
                                                     'cross_check': 'edit'})
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([record['task'] for record in seen(folder)][-1], 'cross-check')
        check, = seen(folder, 'cross-check')
        self.assertTrue(KIT | {'model.json', 'replies.json', 'contract.json', 'VALIDATION.json', 'inventory.json',
                               'ANALYSIS_INDEX.json', 'HANDLERS.c', 'CROSS_CHECK.md', 'TASK.json',
                               'notes/inventory.md', 'notes/handler-identify.md', 'notes/handler-measure.md'}
                        <= set(check['files']), check['files'])
        self.assertFalse([name for name in check['files'] if name.startswith('analysis/') or name == 'image.bin'])
        self.assertIn('CROSS_CHECK.md', check['prompt'])
        self.assertEqual((check['model.json'], check['inventory.json']), (ASSEMBLED, INVENTORY))
        self.assertEqual((check['VALIDATION.json']['ok'], check['VALIDATION.json']['defects']), (True, []))
        functions = [line.split(': ')[1] for line in check['HANDLERS.c'].splitlines()
                     if line.startswith('// entry address')]
        self.assertEqual(functions, ['08000300', '08000340'])
        # Its files are the attempt's output, handed off as the interpret stage's one handoff.
        handoff, = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret']
        files = handed_off(result)
        self.assertEqual(files['model.json'], {**ASSEMBLED, 'device': {'name': 'DEMO-42 (cross-checked)'}})
        self.assertEqual(files['NOTES.md'], 'Cross-check notes.\n')
        self.assertEqual(files['replies.json']['operations'], {'identify': {'steps': [['DEMO-42']]},
                                                               'measure': {'steps': [['T:21.5']]}})

    def test_06_B9_a_cross_check_without_a_usable_result_hands_off_the_assembled_files_with_a_note(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'fragments': FRAGMENTS,
                                                     'cross_check': 'break'})
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        files = handed_off(result)
        self.assertEqual(files['model.json'], ASSEMBLED)
        self.assertEqual(sorted(files['contract.json']['operations']), ['identify', 'measure'])
        note, = [note for note in interpretation(result)['notes'] if 'cross-check' in note]
        self.assertIn('assembled', note)
        self.assertIn(note, files['NOTES.md'])


USAGE = {'input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 10, 'reasoning_output_tokens': 0}
TASKS = [('inventory', None), ('handler', 'identify'), ('handler', 'measure'), ('cross-check', None)]


class RecordTests(TaskCase):
    def test_06_B10_each_task_is_recorded_under_the_attempts_one_assignment_with_a_report_row_of_its_own(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'fragments': FRAGMENTS, 'usage': True})
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        assigned, = [e['data'] for e in events(controller, run, 'stage.assigned') if e['data']['stage'] == 'interpret']
        recorded = [(e['kind'], e['data']) for e in events(controller, run, 'task.started', 'task.finished')]
        self.assertEqual([(kind, data['kind'], data.get('command')) for kind, data in recorded],
                         [(kind, *task) for task in TASKS for kind in ('task.started', 'task.finished')])
        self.assertEqual({data['assignment_id'] for _, data in recorded}, {assigned['id']})
        self.assertEqual([(data['status'], data['usage']) for kind, data in recorded if kind == 'task.finished'],
                         [('completed', USAGE)] * 4)
        workspaces = [data['workspace'] for kind, data in recorded if kind == 'task.started']
        self.assertEqual(workspaces, [record['workspace'] for record in seen(folder)][1:])
        for workspace in workspaces:
            self.assertTrue((Path(workspace) / '_agent_events.jsonl').is_file(), workspace)
        # A reports row per task, under an id of its own, naming the assignment and the task: the stage's token total
        # sums every task.
        with controller._db() as db:
            rows = [(row['id'], json.loads(row['payload'])) for row in db.execute(
                "SELECT id, payload FROM reports WHERE run_id=? AND stage='interpret' ORDER BY created",
                (run['run_id'],))]
        self.assertEqual([(payload['task']['kind'], payload['task'].get('command')) for _, payload in rows], TASKS)
        self.assertEqual({payload['assignment_id'] for _, payload in rows}, {assigned['id']})
        self.assertEqual(len({row_id for row_id, _ in rows} | {assigned['id']}), 5)
        self.assertEqual(sum(payload['usage']['input_tokens'] for _, payload in rows), 400)

    def test_06_B10_a_task_whose_worker_exits_with_an_error_stops_the_run_as_a_stage_workers_does(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'fragments': FRAGMENTS,
                                                     'exit_error': 'handler:measure'})
        result = stopped(controller, run)
        self.assertEqual((result['status'], result['outcome_category']), ('failed', 'host'), result)
        self.assertEqual([(record['task'], record['command']) for record in seen(folder)][1:], TASKS[:3])
        self.assertFalse([h for h in result['accepted_handoffs'] if h['stage'] == 'interpret'])
        finished = [e['data'] for e in events(controller, run, 'task.finished')]
        self.assertEqual((finished[-1]['command'], finished[-1]['status']), ('measure', 'failed'))

    def test_06_B10_the_runs_budget_bounds_each_task_and_one_that_runs_out_stops_the_run(self):
        controller, run, _, folder = self.tasks_run({'inventory': INVENTORY, 'fragments': FRAGMENTS,
                                                     'hang': 'handler:measure'}, budget_seconds=8)
        result = stopped(controller, run, seconds=60)
        self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'operator'), result)
        self.assertIn('budget', result['reason'])
        self.assertTrue(gone(int((folder / 'waiting-handler-measure').read_text(encoding='utf-8'))))
        self.assertEqual([(record['task'], record['command']) for record in seen(folder)][1:], TASKS[:3])


MEASURE_FIXED = {**MEASURE, 'outputs': {'temperature': {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T='}}}
IDENTIFY_FIXED = {**IDENTIFY, 'steps': [{**IDENTIFY['steps'][0], 'expect': {'equals_hex': '44454d4f2d34330a'}}]}
MEASURE_DIFF = [{'source': 'probe_diff', 'dispatch_id': '1', 'defects': [
    {'where': 'operations.measure.steps.0', 'what': 'the device answered T=21.5'}]}]
# A run of measure that failed in the identity operation's steps: the transcript's last step is identify's.
IDENTITY_DIFF = [{'source': 'probe_run', 'dispatch_id': '2', 'code': 'expectation', 'what': 'the reply differs',
                  'failed': [{'ok': False, 'operation': 'measure', 'transcript': [
                      {'operation': 'identify', 'step': 0, 'rx_hex': '44454d4f2d34330a'}]}]}]
UNNAMED_DIFF = [{'source': 'model_contract', 'defects': [{'where': 'channel', 'what': 'the device is not on TCP'}]},
                {'source': 'probe_diff', 'dispatch_id': '3',
                 'defects': [{'where': 'operations.reboot', 'what': 'unknown'}]}]


class RepairTests(TaskCase):
    def repaired(self, diff, script):
        """A task-mode run whose probe sends the first attempt back with `diff`."""
        controller, run, ghidra, folder = self.tasks_run(script, diffs=lambda revision: diff if revision == 0 else None)
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        tasks = [(record['task'], record['command']) for record in seen(folder)]
        return result, tasks[tasks.index(('probe', None)) + 1:], folder, ghidra

    def test_06_B11_a_repair_reruns_only_the_handler_task_of_each_operation_a_difference_names(self):
        result, repair, folder, ghidra = self.repaired(MEASURE_DIFF, {
            'inventory': INVENTORY, 'fragments': FRAGMENTS,
            'repair_fragments': {'measure': fragment('measure', MEASURE_FIXED, ['T=21.5'])}})
        self.assertEqual(repair, [('handler', 'measure'), ('cross-check', None)])
        handler = seen(folder, 'handler')[-1]
        first = handed_off(result, 0)
        self.assertEqual(handler['PREVIOUS_FRAGMENT.json'], FRAGMENTS['measure'])
        self.assertEqual(handler['PREVIOUS_OPERATION.json'], {'operation': 'measure', 'model': MEASURE})
        self.assertEqual(handler['DIFFERENCES.json'], MEASURE_DIFF)
        self.assertEqual(handler['IDENTITY.json'], {'operation': 'identify', 'model': IDENTIFY})
        self.assertEqual(handler['MODEL_FRAME.json'], {'schema': 'interface-model/3', 'device': {'name': 'DEMO-42'},
                                                       'channel': {'type': 'tcp'}, 'framing': INVENTORY['framing']})
        # The cross-check runs again with the previous final model and every difference.
        check = seen(folder, 'cross-check')[-1]
        self.assertEqual(check['DIFFERENCES.json'], MEASURE_DIFF)
        self.assertIn('previous-model/model.json', check['files'])
        self.assertEqual(check['model.json']['operations'], {'identify': IDENTIFY, 'measure': MEASURE_FIXED})
        # Every other operation comes from the previous final model; the re-run operation replaces its own.
        files = handed_off(result, 1)
        self.assertEqual(files['model.json']['operations'], {'identify': first['model.json']['operations']['identify'],
                                                             'measure': MEASURE_FIXED})
        self.assertEqual(files['replies.json']['operations']['measure'], {'steps': [['T=21.5']]})
        self.assertEqual([entry['item'] for entry in files['model.json']['provenance']],
                         ['operations.identify', 'operations.measure'])
        self.assertEqual(len(launches(ghidra)), 1)
        self.assertEqual(interpretation(result, 1)['mode'], 'tasks')

    def test_06_B11_a_failure_inside_the_identity_operations_steps_names_the_identity_operation(self):
        result, repair, folder, _ = self.repaired(IDENTITY_DIFF, {
            'inventory': INVENTORY, 'fragments': FRAGMENTS,
            'repair_fragments': {'identify': fragment('identify', IDENTIFY_FIXED, ['DEMO-43'])}})
        self.assertEqual(repair, [('handler', 'identify'), ('cross-check', None)])
        self.assertEqual(handed_off(result, 1)['model.json']['operations'], {'identify': IDENTIFY_FIXED,
                                                                             'measure': MEASURE})

    def test_06_B11_differences_that_name_no_operation_a_handler_wrote_go_to_the_cross_check(self):
        result, repair, folder, _ = self.repaired(UNNAMED_DIFF, {'inventory': INVENTORY, 'fragments': FRAGMENTS})
        self.assertEqual(repair, [('cross-check', None)])
        check = seen(folder, 'cross-check')[-1]
        self.assertEqual(check['DIFFERENCES.json'], UNNAMED_DIFF)
        self.assertEqual(check['model.json'], handed_off(result, 0)['model.json'])

    def test_06_B11_the_inventory_and_fragments_are_kept_in_the_run_folder_with_their_hashes_in_the_notes_alone(self):
        controller, run, _, folder = self.tasks_run(diffs=lambda revision: MEASURE_DIFF if revision == 0 else None)
        result = stopped(controller, run)
        account = interpretation(result, 0)
        run_dir = self.run_dir(controller, run)
        kept = [account['record'], account['inventory'], *account['fragments'].values()]
        for entry in kept:
            path = run_dir / entry['path']
            self.assertTrue(path.is_file() and path.is_relative_to(run_dir / 'interpret-tasks'), entry)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
        self.assertEqual(json.loads((run_dir / account['inventory']['path']).read_text(encoding='utf-8')), INVENTORY)
        self.assertEqual(sorted(account['fragments']), ['identify', 'measure'])
        # Not among the handoff's artifacts, so probe and ground never receive them.
        handoff = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret' and h['revision'] == 0][-1]
        artifacts = [Path(a['path']) for a in handoff['artifacts']]
        self.assertFalse([a for a in artifacts if a.is_relative_to(run_dir / 'interpret-tasks')])
        self.assertFalse([name for a in artifacts for name in (p.name for p in a.rglob('*'))
                          if name in ('inventory.json', 'fragment.json', 'record.json')])
        probe, = [e['data'] for e in events(controller, run, 'stage.assigned') if e['data']['stage'] == 'probe']
        self.assertFalse([path for path in probe['inputs'] if 'interpret-tasks' in path])

    def test_06_B11_a_repair_after_an_attempt_that_fell_back_repairs_the_single_workers_way(self):
        result, repair, folder, _ = self.repaired(MEASURE_DIFF, {'inventory': 'unreadable', 'single': SINGLE_MODEL})
        self.assertEqual(repair, [('single', None)])
        self.assertEqual(interpretation(result, 1)['mode'], 'single')

    def test_06_B11_a_resume_repeats_an_interrupted_repair_with_the_one_kit_analysis(self):
        script = {'inventory': INVENTORY, 'fragments': FRAGMENTS,
                  'repair_fragments': {'measure': fragment('measure', MEASURE_FIXED, ['T=21.5'])}}
        controller, run, ghidra, folder = self.tasks_run({**script, 'exit_error': 'repair-handler:measure'},
                                                         diffs=lambda revision: MEASURE_DIFF if revision == 0 else None)
        self.assertEqual(stopped(controller, run)['status'], 'failed')
        scripted_worker(folder, **script)
        controller.call('resume', run)
        result = stopped(controller, run)
        self.assertEqual(result['status'], 'completed', result)
        tasks = [(record['task'], record['command']) for record in seen(folder)]
        self.assertEqual(tasks[tasks.index(('probe', None)) + 1:],
                         [('handler', 'measure'), ('handler', 'measure'), ('cross-check', None)])
        self.assertEqual(handed_off(result, 1)['model.json']['operations']['measure'], MEASURE_FIXED)
        self.assertEqual(len(launches(ghidra)), 1)


class BenchmarkTests(unittest.TestCase):
    def test_06_B12_a_benchmark_run_prepares_runs_and_checks_interpretation_as_today(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        ghidra, java = stand_in_ghidra(root)
        folder = root / 'worker'
        prepared, observed = [], []

        def prepare_stage(case_id, stage, run_dir, workspace, accepted, options):
            prepared.append((case_id, stage, options))
            if stage != 'interpret':
                return {'skip': 'Not part of this test'}
            return {'inputs': [], 'allowed_tools': [], 'objective': 'Interpret the case', 'context': {'case': 'tq9'}}

        def observe_stage(case_id, stage, run_dir, workspace, report, accepted, options):
            observed.append(stage)
            return {'ok': True, 'artifacts': [], 'checks': []}

        with patch('generative_driver.benchmark.prepare_stage', prepare_stage), \
                patch('generative_driver.benchmark.observe_stage', observe_stage):
            controller = Controller(root / 'home')
            try:
                spec = {'goal': 'A benchmark run with the analysis named and tasks asked for', 'case': 'tq9',
                        'executor_config': {'command': scripted_worker(folder, inventory=INVENTORY)},
                        'ghidra_home': ghidra, 'java_home': java, 'interpretation': 'tasks'}
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
        interpret = [options for _, stage, options in prepared if stage == 'interpret']
        self.assertEqual(len(interpret), 1)
        self.assertEqual(observed, ['interpret'])
        handoff, = [h for h in result['accepted_handoffs'] if h['stage'] == 'interpret']
        self.assertNotIn('interpretation', handoff['notes']['inline']['configurator'])
        self.assertFalse((controller.home / 'runs' / run['run_id'] / 'kit-analysis').exists())
        self.assertFalse((controller.home / 'runs' / run['run_id'] / 'interpret-tasks').exists())


if __name__ == '__main__':
    unittest.main()
