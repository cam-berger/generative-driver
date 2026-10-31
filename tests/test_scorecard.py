"""Trial metrics and the suite scorecard: what a finished run's retained records say about each stage, read afterwards and
never recorded, and what the trials of a suite add up to."""
import contextlib
import copy
import io
import json
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_configurator
from test_assistive_reports import AssistiveFixture
from test_configurator import gateway_identity, scripted_stages, thermometer_model, wait_stopped
from test_evaluation import FAIL, PASS
from test_suite_harness import manifest
from generative_driver.benchmark_support.outcomes import pass_rate
from generative_driver.benchmark_support.scorecard import scorecard_markdown, suite_scorecard, trial_metrics

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
BINDING = {'host': '127.0.0.1', 'port': 45681}
USAGE = {'input_tokens': 10, 'cached_input_tokens': 0, 'output_tokens': 5, 'reasoning_output_tokens': 0}
# What the runtime keeps of one exchange with the device.
EXCHANGE = {'operation': 'read_temperature', 'step': 0, 'op': 'exchange', 'tx_hex': '540a', 'rx_hex': '32312e350a',
            'status': 'completed'}
# What ground sees when the device contradicts the model: it sends the run back to interpret.
DIFF = {'operations': [{'name': 'read_temperature', 'predicted': 21.0, 'observed': 42.0}]}
UNKNOWN_TOKENS = dict.fromkeys(('input', 'cached_input', 'output', 'reasoning_output', 'total'))
NO_ACTIONS = dict.fromkeys(('command_execution', 'mcp_tool_call', 'file_change'), 0)
UNKNOWN_ACTIONS = dict.fromkeys(('command_execution', 'mcp_tool_call', 'file_change'))
NO_DEVICE = dict.fromkeys(('exchanges', 'writes', 'refusals', 'resets'), 0)


def database(home, mode):
    return sqlite3.connect(Path(home, 'runs.sqlite3').resolve().as_uri() + '?mode=' + mode, uri=True)


def event_count(home):
    """The number of events rows, counted over a read-only connection."""
    with contextlib.closing(database(home, 'ro')) as db:
        return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]


def seqs(home, run_id, kind, stage):
    """The sequence numbers of a run's events of this kind and stage, in order."""
    with contextlib.closing(database(home, 'ro')) as db:
        return [seq for seq, payload in db.execute('SELECT seq,payload FROM events WHERE run_id=? AND kind=? ORDER BY seq',
                                                   (run_id, kind)) if json.loads(payload).get('stage') == stage]


def assigned(home, run_id, stage):
    """The assignments of a stage in order, as its `stage.assigned` events recorded them."""
    with contextlib.closing(database(home, 'ro')) as db:
        events = [json.loads(payload) for (payload,) in db.execute(
            "SELECT payload FROM events WHERE run_id=? AND kind='stage.assigned' ORDER BY seq", (run_id,))]
    return [event for event in events if event.get('stage') == stage]


def retime(home, times):
    """Give events new times, as `{seq: time}`, so a test can say how long a stage took."""
    with contextlib.closing(database(home, 'rw')) as db, db:
        db.executemany('UPDATE events SET created=? WHERE seq=?', [(moment, seq) for seq, moment in times.items()])


def write_transcript(folder, *kinds, lines=()):
    """A worker transcript, `_agent_events.jsonl`, in `folder`: the `lines` as they are (text, or bytes), then a completed
    action of each of `kinds`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    actions = [json.dumps({'type': 'item.completed', 'item': {'type': kind}}) for kind in kinds]
    (folder / '_agent_events.jsonl').write_bytes(
        b''.join((line if isinstance(line, bytes) else line.encode()) + b'\n' for line in [*lines, *actions]))


def probe_result(*entries):
    """What the probe_run tool returns for one live read whose transcript holds `entries`."""
    return {'ok': True, '_exit': 0, 'evidence_kind': 'live', 'results': [
        {'schema': 'interface-result/1', 'ok': True, 'operation': 'read_temperature', 'outputs': {}, 'transcript': list(entries)}]}


def gateway_call(controller, request, model=None):
    """For a stage worker with tools: write `model` (the thermometer by default) into its workspace, and return
    `call(name, **arguments)`, which calls a tool of its gateway on that model."""
    folder = Path(request.workspace) / 'model'
    folder.mkdir()
    (folder / 'model.json').write_text(json.dumps(model or thermometer_model()), encoding='utf-8')
    return lambda name, **arguments: controller.call('tool', {**gateway_identity(request), 'name': name,
                                                              'arguments': {'model_dir': str(folder), **arguments}})


def completed(request):
    """A worker runtime's result: it finished, took two seconds and used 15 tokens."""
    return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 2, 'usage': dict(USAGE),
            'report': {'status': 'completed', 'summary': 'scripted ' + request.stage, 'artifacts': [], 'checks': [],
                       'unresolved': []}}


def reuse_report(status, *checks):
    return {'status': status, 'summary': 'scripted reuse', 'artifacts': [], 'unresolved': [],
            'checks': [{'name': 'check %d' % number, 'status': check, 'evidence': []} for number, check in enumerate(checks)]}


def reusing(status, *checks):
    """A runtime whose fresh-reuse worker reports `status` and one check for each result in `checks`; the other workers
    report as usual."""
    def runtime(request):
        result = completed(request)
        if request.stage == 'reuse':
            result['report'] = reuse_report(status, *checks)
        return result
    return runtime


def grade(home, run_id, mission):
    """Put the mission result an evaluator's verdict carries into the run's final verdict."""
    with contextlib.closing(database(home, 'rw')) as db, db:
        final = json.loads(db.execute('SELECT payload FROM verdicts WHERE id=?', (run_id + ':final',)).fetchone()[0])
        final['equivalence']['mission'] = mission
        db.execute('UPDATE verdicts SET payload=? WHERE id=?', (json.dumps(final), run_id + ':final'))


def reuse_again(home, run_id, report):
    """A later fresh-reuse handoff, with its worker's `report`, as the run would leave after a repair that came back to it."""
    with contextlib.closing(database(home, 'rw')) as db, db:
        handoff = db.execute("SELECT payload,created FROM handoffs WHERE run_id=? AND stage='reuse'", (run_id,)).fetchone()
        saved = db.execute("SELECT payload FROM reports WHERE run_id=? AND stage='reuse'", (run_id,)).fetchone()
        again = {**json.loads(handoff[0]), 'assignment_id': 'again'}
        db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)', ('again', run_id, 'reuse', json.dumps(again), handoff[1] + 1))
        db.execute('INSERT INTO reports VALUES(?,?,?,?,?)', ('again', run_id, 'reuse',
                   json.dumps({**json.loads(saved[0]), 'assignment_id': 'again', 'report': report}), handoff[1] + 1))


def accepting(spec, stage, run_dir, workspace, report, accepted, assignment):
    return {'ok': True, 'artifacts': [str(workspace / 'out.json')], 'checks': []}


def repairing(spec, stage, run_dir, workspace, report, accepted, assignment):
    """Ground sees the device contradict the model at revision 0, so interpretation is repaired once."""
    found = accepting(spec, stage, run_dir, workspace, report, accepted, assignment)
    return {**found, 'device_diff': DIFF} if stage == 'ground' and assignment['revision'] == 0 else found


class TrialMetricsTests(unittest.TestCase):
    def scripted(self, work=None, *, runtime=completed, toolkit=None, check=accepting, skip=None, sealed=False, **start):
        """A scripted six-stage run in a temporary home: no model, tool or device runs. `work(controller, request)` acts
        as a stage's worker, `toolkit(name, arguments)` stands in for the tools it calls, `skip(spec, stage)` names the
        reason a stage runs no worker, `sealed` runs each interpretation in a workspace of its own outside the run, named
        attempt<N> as the interpretation tool names it, and `start` adds to the run's parameters. Returns the home, the
        run's id and its result once the run has stopped and its controller is closed."""
        from generative_driver.configurator import Controller
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        home = Path(directory.name) / 'home'
        controller = Controller(home)
        interpretations = []
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            reason = skip and skip(spec, stage)
            if reason:
                return {'skip': reason}
            prepared = {'inputs': [], 'allowed_tools': ['probe_run', 'interface_execute'] if stage == 'probe' else []}
            if sealed and stage == 'interpret':
                interpretations.append(stage)
                number = len(interpretations)
                prepared['work_dir'] = str(Path(directory.name) / 'sealed' / ('candidate-%d' % number) / ('attempt%d' % number))
            return prepared
        try:
            with scripted_stages(controller, check=check, prepare=prepare, runtime=runtime,
                                 work=work and (lambda request: work(controller, request))), \
                 patch('generative_driver.toolkit.call_tool', toolkit or (lambda name, arguments: probe_result(EXCHANGE))):
                run = controller.call('start', {'goal': 'Scripted scorecard contract; no model or device', 'binding': BINDING,
                                                'executor_config': {'command': ['never-launched']}, **start})
                result = wait_stopped(controller, run)
        finally:
            controller.close()
        return home, run['run_id'], result

    def probed_once(self):
        """A scripted run whose probe worker calls probe_run once, for the exchange the tool returned, and its metrics."""
        def work(controller, request):
            if request.stage == 'probe':
                gateway_call(controller, request)('probe_run', operation='read_temperature')
        home, run, result = self.scripted(work)
        metrics = trial_metrics(home, run)
        self.assertEqual((metrics['stages']['probe']['managed_calls'], metrics['stages']['probe']['device']['exchanges']),
                         ({'probe_run': 1}, 1))
        return home, run, metrics

    def test_metrics_come_from_a_scripted_runs_records(self):
        lines = [json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'ls'}}),
                 'a line that is not JSON',
                 json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'pwd'}}),
                 json.dumps({'type': 'item.completed', 'item': {'type': 'file_change', 'changes': []}})]
        def work(controller, request):
            if request.stage == 'probe':
                workspace = Path(request.workspace)
                (workspace / '_agent_events.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
                model = workspace / 'model'
                model.mkdir()
                (model / 'model.json').write_text(json.dumps(thermometer_model()), encoding='utf-8')
                controller.call('tool', {**gateway_identity(request), 'name': 'probe_run',
                                         'arguments': {'model_dir': str(model), 'operation': 'read_temperature'}})
        home, run, result = self.scripted(work)
        self.assertEqual(result['status'], 'completed')
        before = event_count(home)
        metrics = trial_metrics(home, run)
        probe = metrics['stages']['probe']
        self.assertEqual(probe['worker_actions'], {'command_execution': 2, 'mcp_tool_call': 0, 'file_change': 1})
        self.assertEqual((probe['managed_calls'], probe['device']['exchanges'], probe['tokens']['total']),
                         ({'probe_run': 1}, 1, 15))
        self.assertEqual(metrics['stages']['acquire']['attempts'], 1)
        self.assertEqual(metrics['stages']['acquire']['worker_actions'], UNKNOWN_ACTIONS)  # its worker left no transcript
        self.assertEqual(event_count(home), before)  # read-only: event_count counts events rows over a ?mode=ro connection

    def test_a_stages_wall_time_runs_from_its_first_assignment_to_its_last_acceptance_or_skip(self):
        # Ground sends the run back to interpret once, and then runs no worker: probe runs twice and ground is assigned
        # once, accepted twice and skipped once. Times are set here so the figures can be exact.
        home, run, result = self.scripted(check=repairing, skip=lambda spec, stage: 'scripted skip'
                                          if stage == 'ground' and spec['_revision'] == 1 else None)
        self.assertEqual(result['status'], 'completed')
        probe_assigned, probe_accepted = (seqs(home, run, kind, 'probe') for kind in ('stage.assigned', 'stage.accepted'))
        [ground_assigned], [ground_skipped] = (seqs(home, run, kind, 'ground') for kind in ('stage.assigned', 'stage.skipped'))
        ground_accepted = seqs(home, run, 'stage.accepted', 'ground')
        self.assertEqual((len(probe_assigned), len(probe_accepted), len(ground_accepted)), (2, 2, 2))
        retime(home, {probe_assigned[0]: 100.0, probe_accepted[0]: 110.0, probe_assigned[1]: 200.0, probe_accepted[1]: 250.5,
                      # Skipped last, although its acceptance is recorded after it.
                      ground_assigned: 120.0, ground_accepted[0]: 130.0, ground_skipped: 400.0, ground_accepted[1]: 300.0})
        stages = trial_metrics(home, run)['stages']
        self.assertEqual((stages['probe']['wall_seconds'], stages['probe']['worker_seconds']), (150.5, 4))
        self.assertEqual((stages['ground']['wall_seconds'], stages['ground']['worker_seconds']), (280.0, 2))
        self.assertGreaterEqual(stages['acquire']['wall_seconds'], 0)
        # Nothing ended after ground was first assigned, so its time is unknown rather than negative.
        retime(home, {ground_assigned: 500.0})
        self.assertIsNone(trial_metrics(home, run)['stages']['ground']['wall_seconds'])

    def test_repairs_are_the_ones_the_runs_progress_recorded_and_every_attempt_adds_up(self):
        # Ground sends the run back to interpret once, so every stage from interpret to ground runs twice.
        for check, repairs, attempts in ((accepting, 0, [1, 1, 1, 1, 1, 1]), (repairing, 1, [1, 2, 2, 2, 1, 1])):
            with self.subTest(repairs=repairs):
                home, run, result = self.scripted(check=check)
                self.assertEqual(result['status'], 'completed')
                metrics = trial_metrics(home, run)
                self.assertEqual(metrics['repairs'], repairs)
                self.assertEqual([metrics['stages'][stage]['attempts'] for stage in STAGES], attempts)
                self.assertEqual([metrics['stages'][stage]['tokens']['total'] for stage in STAGES], [15 * n for n in attempts])

    def test_tokens_are_unknown_unless_every_attempt_reported_usage_and_a_missing_counter_is_unknown_alone(self):
        # With one repair, interpret, probe and ground each run twice. Acquire reported input tokens and no output tokens,
        # the second interpretation reported no usage, probe's runtime does not count cached input or reasoning output, and
        # emit ran no worker.
        interpretations = []
        def runtime(request):
            result = completed(request)
            if request.stage == 'interpret':
                interpretations.append(request)
            if request.stage == 'acquire':
                result['usage'] = {'input_tokens': 10}
            if request.stage == 'interpret' and len(interpretations) == 2:
                result['usage'] = None
            if request.stage == 'probe':
                result['usage'] = {**USAGE, 'cached_input_tokens': None, 'reasoning_output_tokens': None}
            return result
        home, run, result = self.scripted(runtime=runtime, check=repairing,
                                          skip=lambda spec, stage: 'scripted skip' if stage == 'emit' else None)
        self.assertEqual(result['status'], 'completed')
        stages = trial_metrics(home, run)['stages']
        self.assertEqual(stages['acquire']['tokens'], UNKNOWN_TOKENS)
        self.assertEqual(stages['interpret']['tokens'], UNKNOWN_TOKENS)
        self.assertEqual(stages['probe']['tokens'],
                         {'input': 20, 'cached_input': None, 'output': 10, 'reasoning_output': None, 'total': 30})
        self.assertEqual(stages['ground']['tokens'], {'input': 20, 'cached_input': 0, 'output': 10, 'reasoning_output': 0, 'total': 30})
        # Time the workers took is a separate report: acquire and interpret reported it although not their tokens.
        self.assertEqual([stages[stage]['worker_seconds'] for stage in STAGES], [2, 4, 4, 4, None, 2])
        # A stage that ran no worker has nothing a worker left to report, and no call or device exchange to count.
        self.assertEqual(stages['emit'], {'attempts': 0, 'wall_seconds': None, 'worker_seconds': None, 'managed_calls': {},
                                          'worker_actions': UNKNOWN_ACTIONS, 'device': NO_DEVICE, 'tokens': UNKNOWN_TOKENS})

    def test_a_crashed_worker_still_counts_as_an_attempt_and_leaves_its_figures_unknown(self):
        # The probe worker wrote a transcript with a line that is not JSON and died before its runtime returned, so it
        # left no report and the run failed there. Its attempt counts from stage.assigned and its transcript is read.
        def work(controller, request):
            if request.stage == 'probe':
                action = json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'ls'}})
                (Path(request.workspace) / '_agent_events.jsonl').write_text(action + '\nunparsed\n', encoding='utf-8')
                raise RuntimeError('scripted crash')
        home, run, result = self.scripted(work)
        self.assertEqual(result['status'], 'failed')
        stages = trial_metrics(home, run)['stages']
        self.assertEqual([stages[stage]['attempts'] for stage in STAGES], [1, 1, 1, 0, 0, 0])
        self.assertEqual(stages['probe']['worker_actions'], {**NO_ACTIONS, 'command_execution': 1})
        unknown = {'worker_seconds': None, 'wall_seconds': None, 'tokens': UNKNOWN_TOKENS}
        for stage in ('probe', 'ground', 'emit', 'reuse'):
            self.assertEqual({key: stages[stage][key] for key in unknown}, unknown, stage)
        self.assertEqual((stages['interpret']['worker_seconds'], stages['interpret']['tokens']['total']), (2, 15))

    def test_one_attempt_without_a_report_leaves_the_sums_of_its_stage_unknown(self):
        # Probe runs twice and its second worker dies: the stage has a report for one attempt of two. Interpret, which
        # reported both of its attempts, adds up.
        probes = []
        def work(controller, request):
            if request.stage == 'probe':
                probes.append(request.workspace)
                if len(probes) == 2:
                    raise RuntimeError('scripted crash')
        home, run, result = self.scripted(work, check=repairing)
        self.assertEqual(result['status'], 'failed')
        stages = trial_metrics(home, run)['stages']
        self.assertEqual((stages['probe']['attempts'], stages['interpret']['attempts']), (2, 2))
        self.assertEqual((stages['probe']['worker_seconds'], set(stages['probe']['tokens'].values())), (None, {None}))
        self.assertEqual((stages['interpret']['worker_seconds'], stages['interpret']['tokens']['total']), (4, 30))

    def test_a_write_a_refusal_and_a_rejected_reply_are_counted_where_they_happened(self):
        # Probe asks for a write that the runtime refuses before it opens the device (the real probe_run, which opens no
        # transport here), asks the gateway for a tool it was not assigned, and reads a device whose reply the model's
        # expectation rejected.
        from generative_driver import toolkit
        real_call_tool, opened = toolkit.call_tool, []
        rejected = {'schema': 'interface-result/1', 'ok': False, 'operation': 'read_temperature', 'outputs': {}, 'units': {},
                    'identity_verified': False, 'transport_open_attempted': True, '_exit': 1,
                    'transcript': [{**EXCHANGE, 'device_rejected': True}],
                    'error': {'code': 'expect', 'message': 'response expectation mismatch', 'fault': 'model'}}
        def tools(name, arguments):
            return rejected if name == 'interface_execute' else real_call_tool(name, arguments)
        def work(controller, request):
            if request.stage == 'probe':
                call = gateway_call(controller, request, test_configurator.DeviceRepairTests.writing_thermometer())
                call('probe_run', operation='set_rate', n=101)
                call('interface_execute', operation='read_temperature')
                with self.assertRaisesRegex(ValueError, 'not assigned'):
                    call('emit_package')
        with patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
            home, run, result = self.scripted(work, toolkit=tools, effects=['read', 'write'])
        self.assertEqual((result['status'], opened), ('completed', []))
        stages = trial_metrics(home, run)['stages']
        self.assertEqual(stages['probe']['managed_calls'], {'interface_execute': 1, 'probe_run': 1})
        # The write the runtime refused, the tool the gateway refused and the reply the device rejected; the rejected
        # reply was also an exchange with the device.
        self.assertEqual(stages['probe']['device'], {'exchanges': 1, 'writes': 1, 'refusals': 3, 'resets': 0})
        for stage in STAGES:
            if stage != 'probe':
                self.assertEqual(stages[stage]['device'], NO_DEVICE, stage)

    def test_a_write_that_never_finished_still_counts_as_a_write(self):
        # Its tool raises after the write was dispatched, so the run stops for safety with the write uncertain: the call
        # has a start and no finish.
        def tools(name, arguments):
            raise RuntimeError('scripted tool failure')
        def work(controller, request):
            if request.stage == 'probe':
                call = gateway_call(controller, request, test_configurator.DeviceRepairTests.writing_thermometer())
                with self.assertRaisesRegex(RuntimeError, 'scripted tool failure'):
                    call('interface_execute', operation='set_rate')
        home, run, result = self.scripted(work, toolkit=tools, effects=['read', 'write'])
        self.assertEqual((result['status'], result['uncertain_effect']), ('blocked', True))
        probe = trial_metrics(home, run)['stages']['probe']
        self.assertEqual(probe['managed_calls'], {'interface_execute': 1})
        self.assertEqual(probe['device'], {'exchanges': 0, 'writes': 1, 'refusals': 0, 'resets': 0})

    def test_exchanges_count_where_a_result_keeps_them_and_a_call_that_raised_is_still_a_call(self):
        # An attempted exchange and a delay are no completed exchange. A probe's results each keep a transcript; a package
        # call keeps its own and those of the preconditions it checked first. The last call raises after it started, so
        # it has no finish and no refusal.
        entry = lambda op, status: {**EXCHANGE, 'op': op, 'status': status}
        results = {
            'probe_run': {'ok': True, '_exit': 0, 'results': [
                {'schema': 'interface-result/1', 'ok': True, 'transcript': [
                    entry('exchange', 'completed'), entry('exchange', 'attempted'), entry('delay', 'completed')]},
                {'schema': 'interface-result/1', 'ok': True, 'transcript': [entry('usb_control', 'completed')]}]},
            'interface_execute': {'schema': 'driver-result/1', 'ok': True, '_exit': 0, 'transcript': [entry('exchange', 'completed')],
                                  'observations': [{'precondition_id': 'armed', 'result': {'ok': True, 'transcript': [
                                      entry('exchange', 'completed'), entry('exchange', 'completed')]}}]}}
        def tools(name, arguments):
            if arguments.get('operation') == 'identify':
                raise RuntimeError('scripted tool failure')
            return results[name]
        def work(controller, request):
            if request.stage == 'probe':
                call = gateway_call(controller, request)
                call('probe_run', operation='read_temperature')
                call('interface_execute', operation='read_temperature')
                with self.assertRaisesRegex(RuntimeError, 'scripted tool failure'):
                    call('interface_execute', operation='identify')
        home, run, result = self.scripted(work, toolkit=tools)
        self.assertEqual(result['status'], 'completed')
        probe = trial_metrics(home, run)['stages']['probe']
        self.assertEqual(probe['managed_calls'], {'interface_execute': 2, 'probe_run': 1})
        self.assertEqual(probe['device'], {'exchanges': 5, 'writes': 0, 'refusals': 0, 'resets': 0})

    def test_calls_are_paired_by_dispatch_so_a_finish_counts_once_and_one_with_no_start_still_counts(self):
        home, run, before = self.probed_once()
        with contextlib.closing(database(home, 'rw')) as db, db:
            # The probe call's finish is recorded a second time; another finish names a dispatch that has no start.
            db.execute("INSERT INTO events(run_id,kind,payload,created) SELECT run_id,kind,payload,created FROM events "
                       "WHERE run_id=? AND kind='tool.finished'", (run,))
            unmatched = {'stage': 'probe', 'name': 'probe_run', 'dispatch_id': 'unmatched', 'result': {'transcript': [EXCHANGE]}}
            db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                       (run, 'tool.finished', json.dumps(unmatched), time.time()))
        after = trial_metrics(home, run)
        before['stages']['probe']['device']['exchanges'] += 1
        self.assertEqual(after, before)

    def test_an_event_naming_another_stage_counts_for_none(self):
        home, run, before = self.probed_once()
        other = json.dumps({'stage': 'finalize', 'name': 'probe_run', 'effectful': True, 'dispatch_id': '1',
                            'refused_before_open': True,
                            'result': {'transcript': [EXCHANGE, {**EXCHANGE, 'device_rejected': True}]}})
        with contextlib.closing(database(home, 'rw')) as db, db:
            for kind in ('stage.assigned', 'stage.accepted', 'stage.skipped', 'tool.started', 'tool.finished', 'tool.refused'):
                db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)', (run, kind, other, time.time()))
        self.assertEqual(trial_metrics(home, run), before)

    def test_resets_count_once_from_recovery_events_and_the_journal_in_the_stage_active_at_the_time(self):
        # Two stages see the emulator recovered, ground's receipt naming no session. The provider's journal also holds the
        # replacement of probe's recovery, which is the same reset, and others the configurator never recorded: two in the
        # stage that opened at the very time of one, and one that names no session and so cannot be matched with another.
        # Rows the journal cannot place (before the run, after it, with no time) and lines it cannot read add nothing.
        def recovered(session):
            return {'assignment_id': 'a', 'receipt': {'new_session_id': session} if session else {}}
        def work(controller, request):
            if request.stage in ('probe', 'ground'):
                # The workspace is runs/<run>/work/<stage>/<assignment>, and a stage with no tools has no gateway.
                controller._event(Path(request.workspace).parents[2].name, 'emulator.recovered',
                                  recovered('s1' if request.stage == 'probe' else None))
        home, run, result = self.scripted(work)
        self.assertEqual(result['status'], 'completed')
        # Each stage is assigned at a time of its own, 100 seconds apart, so a journal row can fall in one of them.
        retime(home, {seqs(home, run, 'stage.assigned', stage)[0]: 100.0 * (number + 1) for number, stage in enumerate(STAGES)})
        trial = Path(home).parent / 'trial'
        (trial / 'device').mkdir(parents=True)
        replaced = lambda session, **extra: {'kind': 'replaced', 'proof': {'receipt': {'new_session_id': session}}, **extra}
        rows = [{'kind': 'started', 'session_id': 'x', 'time': 350.0}, replaced('s1', time=350.0), replaced('s2', time=550.0),
                {'kind': 'replaced', 'proof': {}, 'time': 450.0}, replaced('s6', time=500.0),
                replaced('s3', time=50.0), replaced('s4', time=1e12), replaced('s5')]
        (trial / 'device' / 'journal.jsonl').write_text('\n'.join(['not json', *map(json.dumps, rows)]) + '\n', encoding='utf-8')
        resets = lambda metrics: [metrics['stages'][stage]['device']['resets'] for stage in STAGES]
        self.assertEqual(resets(trial_metrics(home, run)), [0, 0, 1, 1, 0, 0])
        self.assertEqual(resets(trial_metrics(home, run, trial_dir=trial)), [0, 0, 1, 2, 2, 0])
        # A trial directory with no journal adds nothing.
        self.assertEqual(resets(trial_metrics(home, run, trial_dir=Path(home).parent)), [0, 0, 1, 1, 0, 0])

    def test_the_claim_sets_the_fresh_reuse_workers_report_beside_the_graded_mission(self):
        # The worker claims success when its report says completed and every check passed; a report that says more or
        # less than that does not. Agreement compares the claim with the mission result when both are known.
        reports = [(('completed', 'pass', 'pass'), True), (('completed',), True), (('completed', 'pass', 'fail'), False),
                   (('completed', 'pass', 'not_run'), False), (('needs_revision', 'pass'), False), (('blocked', 'pass'), False),
                   (('failed', 'pass'), False)]
        def unchecked(request):
            """A report that says completed and holds no list of checks, which the configurator's notes read as none."""
            result = reusing('completed')(request)
            if request.stage == 'reuse':
                del result['report']['checks']
            return result
        cases = [(report, claimed, reusing(*report)) for report, claimed in reports]
        cases.append((('completed', 'and no list of checks'), True, unchecked))
        for report, claimed, runtime in cases:
            with self.subTest(report=report):
                home, run, result = self.scripted(runtime=runtime)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(trial_metrics(home, run)['claim'],
                                 {'claimed_success': claimed, 'mission_passed': None, 'agrees': None})
                for passed in (True, False, None):
                    grade(home, run, {'name': 'fresh_worker_mission', 'passed': passed})
                    self.assertEqual(trial_metrics(home, run)['claim'], {
                        'claimed_success': claimed, 'mission_passed': passed,
                        'agrees': None if passed is None else claimed == passed})

    def test_the_claim_is_the_latest_reuse_handoffs_and_none_without_a_report(self):
        home, run, result = self.scripted(runtime=reusing('completed', 'pass', 'fail'))
        self.assertEqual(trial_metrics(home, run)['claim']['claimed_success'], False)
        reuse_again(home, run, reuse_report('completed', 'pass'))
        self.assertEqual(trial_metrics(home, run)['claim']['claimed_success'], True)
        # No claim: the run stopped before reuse, reuse ran no worker, or its worker returned no report.
        def silent(request):
            return {**completed(request), 'report': None, 'status': 'failed', 'cause': 'no_report', 'reason': 'scripted'} \
                if request.stage == 'reuse' else completed(request)
        for name, started in (('stopped', dict(work=lambda controller, request: 1 / 0 if request.stage == 'probe' else None)),
                              ('skipped', dict(skip=lambda spec, stage: 'scripted skip' if stage == 'reuse' else None)),
                              ('silent', dict(runtime=silent))):
            with self.subTest(name):
                home, run, result = self.scripted(**started)
                self.assertEqual(trial_metrics(home, run)['claim'],
                                 {'claimed_success': None, 'mission_passed': None, 'agrees': None})

    def test_actions_are_unknown_unless_every_attempt_of_the_stage_left_a_transcript(self):
        # With one repair, probe and ground run twice. Probe's second worker left no transcript, both of ground's did, and
        # acquire's only worker left none.
        probes = []
        def work(controller, request):
            if request.stage == 'probe':
                probes.append(request)
            if request.stage == 'ground' or request.stage == 'probe' and len(probes) == 1:
                write_transcript(request.workspace, 'command_execution', 'file_change')
        home, run, result = self.scripted(work, check=repairing)
        self.assertEqual(result['status'], 'completed')
        stages = trial_metrics(home, run)['stages']
        self.assertEqual((stages['probe']['attempts'], stages['ground']['attempts']), (2, 2))
        self.assertEqual(stages['probe']['worker_actions'], UNKNOWN_ACTIONS)
        self.assertEqual(stages['ground']['worker_actions'], {'command_execution': 2, 'mcp_tool_call': 0, 'file_change': 2})
        self.assertEqual(stages['acquire']['worker_actions'], UNKNOWN_ACTIONS)

    def test_interpretation_actions_are_matched_to_the_attempts_that_made_them(self):
        # Interpretation runs twice, each time in a sealed workspace of its own outside the run, attempt1 and attempt2. Its
        # outputs, and the transcript among them, are frozen into the run when collected: under benchmark/tools for a
        # benchmark run, by the index of the attempt; under preparation, by the assignment, for another run. A worker that
        # was never collected leaves nothing in the run, and a transcript that no attempt owns is nobody's.
        places = {'in a benchmark run': (True, lambda number, assignment: 'benchmark/tools/interpret/attempt%d' % number),
                  'in another run': (True, lambda number, assignment: 'preparation/%s/interpret/attempt1' % assignment['id']),
                  'in its work directory': (False, lambda number, assignment: 'work/interpret/%s' % assignment['id'])}
        for name, (sealed, place) in places.items():
            with self.subTest(name):
                home, run, result = self.scripted(check=repairing, sealed=sealed)
                run_dir = Path(home) / 'runs' / run
                first, second = assigned(home, run, 'interpret')
                interpreted = lambda: trial_metrics(home, run)['stages']['interpret']['worker_actions']
                write_transcript(run_dir / place(1, first), 'command_execution')
                write_transcript(run_dir / 'benchmark/tools/interpret/attempt3', 'file_change')
                write_transcript(run_dir / 'preparation/nobody/interpret/attempt1', 'file_change')
                self.assertEqual(interpreted(), UNKNOWN_ACTIONS)
                write_transcript(run_dir / place(2, second), 'file_change')
                self.assertEqual(interpreted(), {'command_execution': 1, 'mcp_tool_call': 0, 'file_change': 1})

    def test_a_transcript_that_holds_no_codex_record_leaves_the_actions_unknown(self):
        # Nothing says a worker did nothing when its transcript is no Codex stream: the events of another runtime (Goose),
        # text the runtime printed, which `agents.execute` keeps as unparsed_stdout, and an empty file. The Codex stream of
        # a worker that took no action does say so.
        streams = {'acquire': [json.dumps({'type': 'message', 'message': {'role': 'assistant', 'content': []}}),
                               json.dumps({'type': 'complete', 'total_tokens': 15})],
                   'interpret': [json.dumps({'type': 'unparsed_stdout', 'text': 'plain text'}), 'not json'],
                   'probe': [],
                   'ground': [json.dumps({'type': 'thread.started', 'thread_id': 't'})],
                   'emit': [json.dumps({'type': 'turn.completed', 'usage': USAGE})]}
        def work(controller, request):
            if request.stage in streams:
                write_transcript(request.workspace, lines=streams[request.stage])
        home, run, result = self.scripted(work)
        stages = trial_metrics(home, run)['stages']
        self.assertEqual([stages[stage]['worker_actions'] for stage in ('acquire', 'interpret', 'probe')], [UNKNOWN_ACTIONS] * 3)
        self.assertEqual([stages[stage]['worker_actions'] for stage in ('ground', 'emit')], [NO_ACTIONS] * 2)

    def test_the_index_of_a_benchmark_attempt_is_read_from_a_workspace_path_recorded_on_any_host(self):
        # A run recorded on Windows names its sealed workspace with backslashes, and is scored on any host.
        home, run, result = self.scripted(check=repairing, sealed=True)
        with contextlib.closing(database(home, 'rw')) as db, db:
            for number, assignment in enumerate(assigned(home, run, 'interpret'), 1):
                recorded = {**assignment, 'workspace': 'C:\\Users\\x\\AppData\\Local\\Temp\\gd-candidate-%d\\attempt%d' % (number, number)}
                db.execute("UPDATE events SET payload=? WHERE run_id=? AND kind='stage.assigned' AND json_extract(payload, '$.id')=?",
                           (json.dumps(recorded), run, assignment['id']))
        run_dir = Path(home) / 'runs' / run
        write_transcript(run_dir / 'benchmark/tools/interpret/attempt1', 'command_execution')
        write_transcript(run_dir / 'benchmark/tools/interpret/attempt2', 'file_change')
        self.assertEqual(trial_metrics(home, run)['stages']['interpret']['worker_actions'],
                         {'command_execution': 1, 'mcp_tool_call': 0, 'file_change': 1})

    def test_an_attempt_whose_id_is_not_one_name_matches_no_transcript(self):
        # An attempt's id is joined to a path, so one that holds a separator names no transcript of its own.
        def work(controller, request):
            if request.stage == 'probe':
                write_transcript(request.workspace, 'command_execution')
        home, run, result = self.scripted(work)
        run_dir = Path(home) / 'runs' / run
        self.assertEqual(trial_metrics(home, run)['stages']['probe']['worker_actions'], {**NO_ACTIONS, 'command_execution': 1})
        write_transcript(run_dir / 'work/probe-nobody', 'file_change')  # where work/probe/../probe-nobody leads
        with contextlib.closing(database(home, 'rw')) as db, db:
            db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)', (
                run, 'stage.assigned', json.dumps({'id': '../probe-nobody', 'stage': 'probe', 'workspace': '/nowhere'}), time.time()))
        self.assertEqual(trial_metrics(home, run)['stages']['probe']['worker_actions'], UNKNOWN_ACTIONS)

    def test_a_transcript_counts_its_completed_actions_and_skips_every_other_line(self):
        # Lines that are not completed actions: one that started, a message, records that are no item, JSON that is no
        # record, and bytes that are no text. A directory with a transcript's name is no transcript.
        others = [json.dumps({'type': 'item.started', 'item': {'type': 'command_execution'}}),
                  json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}),
                  json.dumps({'type': 'item.completed', 'item': 'file_change'}), json.dumps({'type': 'item.completed'}),
                  json.dumps({'type': 'turn.completed', 'usage': USAGE}), '[1, 2]', '3', 'not json', b'\xff\xfe not text']
        def work(controller, request):
            if request.stage == 'probe':
                write_transcript(request.workspace, 'command_execution', 'mcp_tool_call', 'command_execution', lines=others)
            if request.stage == 'ground':
                (Path(request.workspace) / '_agent_events.jsonl').mkdir()
        home, run, result = self.scripted(work)
        stages = trial_metrics(home, run)['stages']
        self.assertEqual(stages['probe']['worker_actions'], {'command_execution': 2, 'mcp_tool_call': 1, 'file_change': 0})
        self.assertEqual(stages['ground']['worker_actions'], UNKNOWN_ACTIONS)

    def test_the_database_is_opened_read_only(self):
        home, run, result = self.scripted()
        with patch('generative_driver.benchmark_support.scorecard.sqlite3.connect', wraps=sqlite3.connect) as connect:
            trial_metrics(home, run)
        [call] = connect.call_args_list
        self.assertTrue(call.args[0].startswith('file:') and call.args[0].endswith('runs.sqlite3?mode=ro'), call)
        self.assertEqual(call.kwargs, {'uri': True})

    def test_a_run_the_home_does_not_hold_is_refused_rather_than_measured_as_an_empty_run(self):
        home, run, result = self.scripted()
        with self.assertRaisesRegex(ValueError, 'Unknown run_id'):
            trial_metrics(home, 'run-never-started')
        # A directory with no database is not a home, and looking in it creates none.
        elsewhere = Path(home).parent
        with self.assertRaises(FileNotFoundError):
            trial_metrics(elsewhere, run)
        self.assertFalse((elsewhere / 'runs.sqlite3').exists())

    def test_the_hand_made_metrics_of_the_scorecard_tests_have_the_keys_of_a_real_runs(self):
        # The scorecard tests below feed `suite_scorecard` metrics made by hand (`blank_metrics`); this keeps their keys
        # to those `trial_metrics` really returns, so a change to its result cannot leave them describing another shape.
        home, run, result = self.scripted()
        keys = lambda value: {key: keys(item) for key, item in value.items()} if isinstance(value, dict) else None
        self.assertEqual(keys(blank_metrics(run)), keys(trial_metrics(home, run)))


def tokens(total, **counters):
    """A stage's token counters: `total` and the `counters` given, every other counter unknown."""
    return {**UNKNOWN_TOKENS, 'total': total, **counters}


def blank_metrics(run_id):
    """The shape of a `benchmark-trial-metrics/1` for a run nothing was measured of: no stage was assigned and no figure is
    known, so a test sets the figures it means."""
    stage = {'attempts': 0, 'wall_seconds': None, 'worker_seconds': None, 'managed_calls': {},
             'worker_actions': UNKNOWN_ACTIONS, 'device': NO_DEVICE, 'tokens': UNKNOWN_TOKENS}
    return {'schema': 'benchmark-trial-metrics/1', 'run_id': run_id, 'repairs': None,
            'claim': {'claimed_success': None, 'mission_passed': None, 'agrees': None},
            'stages': {name: copy.deepcopy(stage) for name in STAGES}}


def probed(run_id, total, **probe):
    """The metrics of a run whose probe stage was assigned once and used `total` tokens, and `probe` besides."""
    metrics = blank_metrics(run_id)
    metrics['stages']['probe'].update(attempts=1, tokens=tokens(total), **probe)
    return metrics


def scoring_report(outcomes, *, scoring=True, **labels):
    """A `benchmark-suite-report/3` down to what a scorecard reads of the one `report_suite` gives: a trial row for each case of
    `outcomes` (case -> scored outcome), with run id r<n> unless it was not run, and the scoring set in the comparison
    identity when `scoring`. `labels` are top-level fields such as `execution`."""
    rows = [{'trial_key': 'entry-%03d-repeat-000' % number, 'case': case, 'family': 'generated', 'scored': outcome,
             'run_id': None if outcome == 'not_run' else 'r%d' % number} for number, (case, outcome) in enumerate(outcomes.items())]
    identity = {'scoring_set': {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64}} if scoring else {}
    return {'schema': 'benchmark-suite-report/3', 'experiment': {'comparison_identity': identity}, 'trials': rows, **labels}


def three_probed():
    """The report and the metrics (by run id) of three trials that probed. The first measured every figure; the second has
    no wall time, no action counts and no cached or reasoning tokens; the third has no worker time and no tokens."""
    def probe(run_id, **figures):
        metrics = blank_metrics(run_id)
        metrics['stages']['probe'].update(figures)
        return metrics
    counts = lambda command, mcp, files: {'command_execution': command, 'mcp_tool_call': mcp, 'file_change': files}
    metrics = {
        'r0': probe('r0', attempts=1, wall_seconds=10.0, worker_seconds=4, managed_calls={'probe_run': 2},
                    worker_actions=counts(3, 0, 1), device={'exchanges': 5, 'writes': 1, 'refusals': 0, 'resets': 0},
                    tokens={'input': 100, 'cached_input': 40, 'output': 20, 'reasoning_output': 5, 'total': 120}),
        'r1': probe('r1', attempts=2, worker_seconds=6, managed_calls={'probe_run': 1, 'interface_execute': 4},
                    device={'exchanges': 3, 'writes': 0, 'refusals': 2, 'resets': 1}, tokens=tokens(60, input=50, output=10)),
        'r2': probe('r2', attempts=1, wall_seconds=30.0, worker_actions=counts(1, 2, 0))}
    return scoring_report({'d0': 'pass', 'd1': 'failure', 'd2': 'failure'}), metrics


def scorecard_of(report, metrics, **arguments):
    """The scorecard of `report`, with `trial_metrics` answering from `metrics` by run id (so no home is opened)."""
    with patch('generative_driver.benchmark_support.scorecard.trial_metrics',
               side_effect=lambda home, run_id, **_: metrics[run_id]):
        return suite_scorecard(report, home=Path('no-such-home'), **arguments)


class SuiteScorecardTests(unittest.TestCase):
    home = Path('no-such-home')

    def card(self, report, metrics, **arguments):
        return scorecard_of(report, metrics, **arguments)

    def test_a_scoring_suites_scorecard_has_aggregates_only(self):
        report = scoring_report({'d0': 'pass', 'd1': 'failure'})
        card = self.card(report, {'r0': probed('r0', 10), 'r1': probed('r1', 20)})
        self.assertEqual(card['schema'], 'benchmark-scorecard/1')
        self.assertNotIn('trials', card)
        self.assertEqual(card['pass_line'], '1/2 pass, 0 unknown, 0 faults, 2 planned')
        self.assertEqual(card['stages']['probe']['tokens']['total'], 30)

    def test_a_stage_figure_sums_the_trials_that_measured_it_and_says_how_many_did(self):
        # See `three_probed`: a figure no trial measured (emit's) is unknown, not zero. Counts that are never unknown
        # (attempts, managed calls, device) are plain sums.
        card = self.card(*three_probed())
        counts = lambda command, mcp, files: {'command_execution': command, 'mcp_tool_call': mcp, 'file_change': files}
        self.assertEqual(card['stages']['probe'], {
            'attempts': 4, 'wall_seconds': 40.0, 'worker_seconds': 10, 'managed_calls': {'interface_execute': 4, 'probe_run': 3},
            'worker_actions': counts(4, 2, 1), 'device': {'exchanges': 8, 'writes': 1, 'refusals': 2, 'resets': 1},
            'tokens': {'input': 150, 'cached_input': 40, 'output': 30, 'reasoning_output': 5, 'total': 180},
            'median': {'wall_seconds': 20.0, 'worker_seconds': 5.0,
                       'tokens': {'input': 75.0, 'cached_input': 40, 'output': 15.0, 'reasoning_output': 5, 'total': 90.0}},
            'measured': {'wall_seconds': 2, 'worker_seconds': 2, 'worker_actions': counts(2, 2, 2),
                         'tokens': {'input': 2, 'cached_input': 1, 'output': 2, 'reasoning_output': 1, 'total': 2}}})
        self.assertEqual(card['stages']['emit'], {
            'attempts': 0, 'wall_seconds': None, 'worker_seconds': None, 'managed_calls': {}, 'worker_actions': UNKNOWN_ACTIONS,
            'device': NO_DEVICE, 'tokens': UNKNOWN_TOKENS,
            'median': {'wall_seconds': None, 'worker_seconds': None, 'tokens': UNKNOWN_TOKENS},
            'measured': {'wall_seconds': 0, 'worker_seconds': 0, 'worker_actions': dict.fromkeys(UNKNOWN_ACTIONS, 0),
                         'tokens': dict.fromkeys(UNKNOWN_TOKENS, 0)}})
        self.assertEqual(list(card['stages']), list(STAGES))

    def test_repairs_are_summed_and_each_claim_figure_is_counted_by_answer(self):
        # Repairs add up over the trials that gave them. The yes/no figures of the claim are counted as true, false and
        # unknown, so a trial whose reuse ran no worker or whose run has no mission result is told from one that said no.
        def run(run_id, repairs, claimed, mission, agrees):
            metrics = blank_metrics(run_id)
            metrics.update(repairs=repairs, claim={'claimed_success': claimed, 'mission_passed': mission, 'agrees': agrees})
            return metrics
        metrics = {'r0': run('r0', 1, True, True, True), 'r1': run('r1', None, False, True, False),
                   'r2': run('r2', 2, None, None, None)}
        card = self.card(scoring_report({'d0': 'pass', 'd1': 'failure', 'd2': 'failure'}), metrics)
        answers = lambda true, false, unknown: {'true': true, 'false': false, 'unknown': unknown}
        self.assertEqual(card['run'], {'repairs': 3, 'measured': {'repairs': 2}, 'claim': {
            'claimed_success': answers(1, 1, 1), 'mission_passed': answers(2, 0, 1), 'agrees': answers(1, 1, 1)}})
        # No trial gave repairs: unknown, not zero.
        card = self.card(scoring_report({'d0': 'pass'}), {'r0': blank_metrics('r0')})
        self.assertEqual((card['run']['repairs'], card['run']['measured']), (None, {'repairs': 0}))
        self.assertEqual(card['run']['claim']['agrees'], answers(0, 0, 1))

    def test_metrics_come_from_every_trial_with_a_run_whatever_its_outcome(self):
        # A trial with no run counts in the pass rate alone, and asks for no metrics. The tokens of the faulted, flagged
        # and unsettled trials are in the totals: the scorecard measures what the system used, not only what it passed.
        outcomes = {'d0': 'pass', 'd1': 'failure', 'd2': 'unknown', 'd3': 'fault', 'd4': 'flagged', 'd5': 'running',
                    'd6': 'not_run'}
        metrics = {'r%d' % number: probed('r%d' % number, 10 * (number + 1)) for number in range(6)}
        card = self.card(scoring_report(outcomes), metrics)
        self.assertEqual(card['pass_line'], '1/2 pass, 1 unknown, 1 faults, 7 planned, 1 flagged')
        self.assertEqual((card['measured_trials'], card['pass_rate']['planned'], card['pass_rate']['not_run']), (6, 7, 1))
        self.assertEqual(card['stages']['probe']['tokens']['total'], 210)
        self.assertEqual(card['stages']['probe']['measured']['tokens']['total'], 6)
        # Nothing ran: the scorecard is the pass rate alone, with nothing measured.
        card = self.card(scoring_report({'d0': 'not_run'}), {})
        self.assertEqual((card['measured_trials'], card['stages']['probe']['tokens']['total'], card['run']['repairs']),
                         (0, None, None))

    def test_groups_give_the_pass_rate_of_each_value_of_each_key_the_rows_carry(self):
        # `family` is on every row; `level` is on three of these four, one of them with a group of its own, and no row
        # carries a `framing`. A row without a key, or with none for it, is in none of its groups and a key that no row
        # carries has none. A group of one trial is kept: a scoring suite shows aggregates only, and its `planned` count
        # is the size.
        report = scoring_report({'d0': 'pass', 'd1': 'failure', 'd2': 'fault', 'd3': 'pass', 'd4': 'not_run'})
        for row, family, level in zip(report['trials'], ('generated', 'generated', 'tq9', 'generated', 'generated'),
                                      (1, 1, 2, None, None)):
            row['family'] = family
            if level:
                row['level'] = level
        report['trials'][4]['level'] = None
        card = self.card(report, {'r%d' % number: blank_metrics('r%d' % number) for number in range(4)})
        self.assertEqual(card['groups'], {'family': {'generated': pass_rate(['pass', 'failure', 'pass', 'not_run']),
                                                     'tq9': pass_rate(['fault'])},
                                          'level': {'1': pass_rate(['pass', 'failure']), '2': pass_rate(['fault'])}})
        self.assertEqual(card['groups']['family']['tq9']['planned'], 1)

    def test_the_execution_labels_of_the_report_are_carried_when_it_has_them(self):
        # A replay suite's report says no model ran its trials; a report that says nothing is not made to.
        plain = self.card(scoring_report({'d0': 'pass'}), {'r0': blank_metrics('r0')})
        self.assertFalse({'execution', 'model_benchmark'} & set(plain))
        replayed = self.card(scoring_report({'d0': 'pass'}, execution='replay-emulation', model_benchmark=False),
                             {'r0': blank_metrics('r0')})
        self.assertEqual((replayed['execution'], replayed['model_benchmark']), ('replay-emulation', False))

    def test_a_suite_with_no_scoring_set_adds_a_row_for_each_trial_and_never_the_audit(self):
        # Every planned trial has a row, one with no run included, and a row is its key, outcome and metrics. A flagged
        # trial's findings quote the worker's raw command: they are in no scorecard.
        report = scoring_report({'d0': 'pass', 'd1': 'not_run', 'd2': 'flagged'}, scoring=False)
        command = 'cat "/Volumes/Answer Vault/evaluator/x.password"'
        report['trials'][2]['audit'] = [{'transcript': 'work/probe/a1/_agent_events.jsonl', 'command': command}]
        metrics = {'r0': probed('r0', 10), 'r2': probed('r2', 20)}
        card = self.card(report, metrics)
        self.assertEqual(card['trials'], [
            {'trial_key': 'entry-000-repeat-000', 'scored': 'pass', 'metrics': metrics['r0']},
            {'trial_key': 'entry-001-repeat-000', 'scored': 'not_run', 'metrics': None},
            {'trial_key': 'entry-002-repeat-000', 'scored': 'flagged', 'metrics': metrics['r2']}])
        self.assertEqual(card['pass_line'], '1/1 pass, 0 unknown, 0 faults, 3 planned, 1 flagged')
        self.assertNotIn('Groundtruth', json.dumps(card))

    def test_a_scoring_suites_scorecard_names_no_case_run_or_trial_and_quotes_no_command(self):
        # The rows of a report carry the case, run id and trial key of each trial and, when an audit ran, findings that
        # quote worker commands. None reaches the scorecard of a scoring suite, whatever its groups are.
        report = scoring_report({'secret-device-7': 'flagged', 'secret-device-8': 'pass'})
        report['trials'][0]['audit'] = [{'transcript': 'work/probe/a1/_agent_events.jsonl', 'command': 'cat /vault/key'}]
        for row, run_id, level in zip(report['trials'], ('run-5ecret1', 'run-5ecret2'), (1, 2)):
            row.update(run_id=run_id, level=level)
        card = self.card(report, {'run-5ecret1': probed('run-5ecret1', 10), 'run-5ecret2': probed('run-5ecret2', 20)})
        self.assertNotIn('trials', card)
        text = json.dumps(card) + '\n' + scorecard_markdown(card)
        for private in ('secret-device', 'run-5ecret', 'entry-00', 'work/probe', 'cat /vault/key', 'audit'):
            self.assertNotIn(private, text)
        self.assertEqual(sorted(card['groups']['level']), ['1', '2'])

    def test_a_trial_is_measured_in_its_own_directory_when_it_has_one(self):
        # The provider's journal in a trial's directory adds the resets the configurator never recorded. A trial the map
        # leaves out is measured without one, and a key the report does not hold is no trial.
        asked = []
        def measure(home, run_id, **arguments):
            asked.append((home, run_id, arguments.get('trial_dir')))
            return blank_metrics(run_id)
        with patch('generative_driver.benchmark_support.scorecard.trial_metrics', side_effect=measure):
            suite_scorecard(scoring_report({'d0': 'pass', 'd1': 'failure'}), home=self.home,
                            trial_dirs={'entry-001-repeat-000': Path('trial-one'), 'entry-009-repeat-000': Path('nobody')})
        self.assertEqual(asked, [(self.home, 'r0', None), (self.home, 'r1', Path('trial-one'))])

    def test_a_report_that_stores_no_scored_outcome_has_no_scorecard(self):
        # An earlier suite report counts no pass rate; its rows have no outcome to count, so it is refused outright
        # rather than scored from rows it does not have.
        for schema in ('benchmark-suite-report/1', 'benchmark-suite-report/2'):
            with self.subTest(schema):
                report = {**scoring_report({'d0': 'pass'}), 'schema': schema}
                for row in report['trials']:
                    del row['scored']
                with self.assertRaisesRegex(ValueError, 'benchmark-suite-report/3'):
                    self.card(report, {'r0': blank_metrics('r0')})


class ScorecardMarkdownTests(unittest.TestCase):
    def markdown(self, report, metrics):
        return scorecard_markdown(scorecard_of(report, metrics))

    def table(self, text, heading):
        """The rows of the table under `## heading`, each as its cells, header first and separator line left out."""
        section = text.split('## ' + heading + '\n', 1)[1].split('\n## ', 1)[0]
        lines = [line for line in section.splitlines() if line.startswith('|')]
        return [[cell.strip() for cell in line.strip('|').split('|')] for number, line in enumerate(lines) if number != 1]

    def table_row(self, text, heading, name):
        """The cells of the row named `name` in the table under `## heading`."""
        return next(row for row in self.table(text, heading)[1:] if row[0] == name)

    def test_the_markdown_leads_with_the_pass_line_and_a_suite_no_model_ran_says_so_on_its_second_line(self):
        blank = {'r0': blank_metrics('r0'), 'r1': blank_metrics('r1')}
        lines = self.markdown(scoring_report({'d0': 'pass', 'd1': 'failure'}), blank).splitlines()
        self.assertEqual(lines[:2], ['1/2 pass, 0 unknown, 0 faults, 2 planned', ''])
        report = scoring_report({'d0': 'pass'}, execution='replay-emulation', model_benchmark=False)
        lines = self.markdown(report, blank).splitlines()
        self.assertEqual(lines[0], '1/1 pass, 0 unknown, 0 faults, 1 planned')
        self.assertRegex(lines[1], r'^No model ran these trials \(replay-emulation\): none of these figures is model performance')

    def test_each_group_is_given_as_a_pass_line(self):
        report = scoring_report({'d0': 'pass', 'd1': 'failure', 'd2': 'pass'})
        for row, level in zip(report['trials'], (1, 1, 2)):
            row['level'] = level
        text = self.markdown(report, {'r%d' % number: blank_metrics('r%d' % number) for number in range(3)})
        for line in ('- family `generated`: 2/3 pass, 0 unknown, 0 faults, 3 planned',
                     '- level `1`: 1/2 pass, 0 unknown, 0 faults, 2 planned',
                     '- level `2`: 1/1 pass, 0 unknown, 0 faults, 1 planned'):
            self.assertIn('\n' + line + '\n', text)

    def test_a_stage_row_marks_a_figure_that_fewer_trials_measured_and_one_none_did(self):
        # Of the three trials, two measured probe's wall and worker time, its tokens and its action counts; the plain
        # counts are exact. Emit was never assigned, so what a worker leaves is unknown there, not zero.
        text = self.markdown(*three_probed())
        self.assertEqual(self.table(text, 'Stages')[0], ['Stage', 'Attempts', 'Wall seconds', 'Worker seconds', 'Tokens',
                         'Shell commands', 'MCP calls', 'File edits', 'Exchanges', 'Writes', 'Refusals', 'Resets'])
        self.assertEqual([row[0] for row in self.table(text, 'Stages')[1:]], list(STAGES))
        self.assertEqual(self.table_row(text, 'Stages', 'probe'),
                         ['probe', '4', '40 (2/3)', '10 (2/3)', '180 (2/3)', '4 (2/3)', '2 (2/3)', '1 (2/3)', '8', '1', '2', '1'])
        self.assertEqual(self.table_row(text, 'Stages', 'emit'), ['emit', '0'] + ['unknown'] * 6 + ['0'] * 4)
        # Every trial measured: no count beside the sum, and a half is written as a decimal.
        text = self.markdown(scoring_report({'d0': 'pass', 'd1': 'failure'}), {'r0': probed('r0', 10), 'r1': probed('r1', 15)})
        self.assertEqual(self.table_row(text, 'Stages', 'probe')[4], '25')
        self.assertEqual(self.table_row(text, 'Medians', 'probe')[3], '12.50')

    def test_the_medians_the_managed_calls_and_the_run_figures_are_given_too(self):
        text = self.markdown(*three_probed())
        self.assertEqual(self.table_row(text, 'Medians', 'probe'), ['probe', '20 (2/3)', '5 (2/3)', '90 (2/3)'])
        self.assertEqual(self.table_row(text, 'Medians', 'emit'), ['emit', 'unknown', 'unknown', 'unknown'])
        self.assertIn('\n- probe: interface_execute 4, probe_run 3\n', text)
        self.assertNotIn('- emit:', text)
        report = scoring_report({'d0': 'pass', 'd1': 'failure', 'd2': 'failure'})
        metrics = {'r%d' % number: blank_metrics('r%d' % number) for number in range(3)}
        for run_id, repairs, claimed in (('r0', 1, True), ('r1', None, False), ('r2', 2, None)):
            metrics[run_id].update(repairs=repairs, claim={'claimed_success': claimed, 'mission_passed': None, 'agrees': None})
        text = self.markdown(report, metrics)
        self.assertIn('\n- Repairs: 3 (2/3)\n', text)
        self.assertIn('\n- Fresh-reuse worker claimed success: 1 true, 1 false, 1 unknown\n', text)
        self.assertIn('\n- Claim agrees with the mission result: 0 true, 0 false, 3 unknown\n', text)
        self.assertIn('\n- Repairs: unknown\n', self.markdown(scoring_report({'d0': 'pass'}), {'r0': blank_metrics('r0')}))

    def test_a_suite_nothing_of_which_ran_is_unknown_everywhere_and_calls_nothing(self):
        text = self.markdown(scoring_report({'d0': 'not_run'}), {})
        self.assertEqual(text.splitlines()[0], '0/0 pass, 0 unknown, 0 faults, 1 planned')
        self.assertIn('\nFigures cover 0 of 1 planned trials, those with a run.', text)
        for stage in STAGES:
            self.assertEqual(self.table_row(text, 'Stages', stage), [stage, '0'] + ['unknown'] * 6 + ['0'] * 4)
            self.assertEqual(self.table_row(text, 'Medians', stage), [stage] + ['unknown'] * 3)
        self.assertTrue(text.endswith('\n## Managed calls\n\nnone\n'))


class BenchTrialDirectoryTests(unittest.TestCase):
    def test_a_trial_is_found_by_the_run_its_record_names_under_any_suite_of_the_bench_home(self):
        # The bench home is a sibling of the configurator home. A suite resumed by a new harness has a suite directory of
        # its own, and a trial found there is still the trial of its run; a trial that never learnt of a run, one whose
        # record cannot be read and a run with no directory at all are left out of the map.
        from generative_driver.benchmark_support.scorecard import bench_trial_dirs
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()  # the bench home is found from the resolved home
            home = root / 'state'
            trials = lambda suite: root / 'state-bench' / 'suites' / suite / 'trials'
            for suite, name, record in (('suite-run-a', 'entry-000-repeat-000-1a2b3c4d', {'run_id': 'r0'}),
                                        ('suite-run-a', 'entry-001-repeat-000-0badc0de', {'run_id': None}),
                                        ('suite-run-b', 'entry-001-repeat-000-5e6f7a8b', {'run_id': 'r1'}),
                                        ('suite-run-b', 'entry-002-repeat-000-00000000', 'not a record')):
                (trials(suite) / name).mkdir(parents=True)
                (trials(suite) / name / 'trial.json').write_text(json.dumps(record), encoding='utf-8')
            report = scoring_report({'d0': 'pass', 'd1': 'failure', 'd2': 'fault', 'd3': 'not_run'})
            self.assertEqual(bench_trial_dirs(report, home), {
                'entry-000-repeat-000': trials('suite-run-a') / 'entry-000-repeat-000-1a2b3c4d',
                'entry-001-repeat-000': trials('suite-run-b') / 'entry-001-repeat-000-5e6f7a8b'})
            # A home with no bench home beside it has no trial directories.
            self.assertEqual(bench_trial_dirs(report, root / 'elsewhere'), {})


class ScorecardCommandTests(unittest.TestCase):
    def command(self, report, metrics, *arguments, home='state'):
        """`benchmark suite scorecard` as the command line runs it, in a home named by the environment as `generative-driver
        --home` sets it, with `report_suite` returning `report` and `trial_metrics` answering from `metrics` (by run id, or
        a function of the home and run id): (exit code, the JSON it printed, the call `report_suite` got, the home each
        `trial_metrics` call got)."""
        from generative_driver.benchmark import main
        asked, output = [], io.StringIO()
        def measure(home, run_id, **_):
            asked.append(home)
            return metrics(home, run_id) if callable(metrics) else metrics[run_id]
        with patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(home)}), \
                patch('generative_driver.benchmark_support.suite_reporting.report_suite', return_value=report) as export, \
                patch('generative_driver.benchmark_support.scorecard.trial_metrics', side_effect=measure), \
                contextlib.redirect_stdout(output):
            code = main(['suite', 'scorecard', *arguments])
        return code, json.loads(output.getvalue()), export.call_args, asked

    def test_the_command_writes_the_json_and_the_markdown_and_prints_the_json(self):
        report = scoring_report({'d0': 'pass', 'd1': 'failure'})
        metrics = {'r0': probed('r0', 10), 'r1': probed('r1', 20)}
        with tempfile.TemporaryDirectory() as temporary:
            home, out = Path(temporary).resolve() / 'state', Path(temporary) / 'score card' / 'one'
            code, printed, export, asked = self.command(report, metrics, 'S', '--output', str(out), '--audit-path', '/vault',
                                                        '--audit-path', 'C:\\Generator', home=home)
            self.assertEqual(code, 0)
            # Only the aggregate scorecard stays: the export was made in a temporary directory, which is gone.
            self.assertEqual(sorted(path.name for path in out.iterdir()), ['scorecard.json', 'scorecard.md'])
            self.assertEqual(export.args, ('S',))
            self.assertEqual((export.kwargs['home'], export.kwargs['audit_paths']), (home, ('/vault', 'C:\\Generator')))
            self.assertNotEqual(Path(export.kwargs['output']).resolve(), out.resolve())
            self.assertFalse(Path(export.kwargs['output']).exists())
            self.assertEqual(asked, [home, home])
            written = (out / 'scorecard.json').read_text(encoding='utf-8')
            self.assertEqual(json.loads(written), printed)
            self.assertEqual(written, json.dumps(printed, indent=2) + '\n')
            self.assertEqual(printed['stages']['probe']['tokens']['total'], 30)
            text = (out / 'scorecard.md').read_text(encoding='utf-8')
            self.assertTrue(text.startswith('1/2 pass, 0 unknown, 0 faults, 2 planned\n'))
            self.assertNotIn('d0', text)

    def test_a_scorecard_that_cannot_be_measured_is_an_error_and_writes_nothing(self):
        # A run the home does not hold would otherwise be a trial that did nothing.
        def unknown(home, run_id):
            raise ValueError('Unknown run_id')
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'scorecard'
            code, printed, export, asked = self.command(scoring_report({'d0': 'pass'}), unknown, 'S', '--output', str(out))
            self.assertEqual((code, printed), (1, {'ok': False, 'error': 'Unknown run_id'}))
            self.assertFalse(out.exists())

    def test_a_figure_that_is_not_a_number_is_an_error_and_writes_no_invalid_json(self):
        nan = probed('r0', 10, wall_seconds=float('nan'))
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / 'scorecard'
            code, printed, export, asked = self.command(scoring_report({'d0': 'pass'}), {'r0': nan}, 'S', '--output', str(out))
            self.assertEqual((code, printed['ok']), (1, False))
            self.assertFalse(out.exists())


class RealSuiteTests(AssistiveFixture):
    """A suite the bench harness drove against the in-process configurator: an equivalent driver, one graded not equivalent
    and one whose grade the evaluator's host failed to give. Scripted stages, a toy truth and no model, tool or device;
    the scorecard reads what the harness and the configurator kept."""
    VAULT = '/Volumes/Answer Vault'
    COMMAND = 'cat "' + VAULT + '/evaluator/x.password"'

    def suite(self):
        """The suite's id and its rows as the configurator keeps them, the first trial's worker having read the vault."""
        with self.harness(PASS, FAIL, RuntimeError('evaluator host stopped')):
            record = self.drive(manifest=manifest(), executor='codex', options={'scoped_tool_approval': 'emulator'},
                                request_id='three-children')
        rows = self.controller.call('suite_result', {'suite_id': record['suite_id']})['trials']
        write_transcript(self.home / 'runs' / rows[0]['run_id'] / 'work' / 'probe' / 'a1', lines=[json.dumps(
            {'type': 'item.completed', 'item': {'type': 'command_execution', 'command': self.COMMAND}})])
        return record['suite_id'], rows

    def test_the_command_measures_each_trial_in_the_directory_its_harness_kept_and_quotes_no_command(self):
        # Catches a lookup of trial directories that assumes a layout the harness does not keep, a scorecard that leaves
        # out a flagged trial's metrics, one that quotes the command that flagged it, and a home or export that differs
        # between the report and the measurement.
        from generative_driver.benchmark import main
        sid, rows = self.suite()
        out, output = self.root / 'scorecard', io.StringIO()
        with self.owner(), patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.home)}), \
                patch('generative_driver.benchmark_support.scorecard.trial_metrics', wraps=trial_metrics) as measured, \
                contextlib.redirect_stdout(output):
            code = main(['suite', 'scorecard', sid, '--output', str(out), '--audit-path', self.VAULT])
        card = json.loads(output.getvalue())
        self.assertEqual((code, card['pass_line'], card['measured_trials']),
                         (0, '0/1 pass, 0 unknown, 1 faults, 3 planned, 1 flagged', 3))
        self.assertEqual([(row['trial_key'], row['scored'], row['metrics']['run_id']) for row in card['trials']],
                         [(rows[0]['trial_key'], 'flagged', rows[0]['run_id']), (rows[1]['trial_key'], 'failure', rows[1]['run_id']),
                          (rows[2]['trial_key'], 'fault', rows[2]['run_id'])])
        self.assertEqual([card['stages'][stage]['attempts'] for stage in STAGES], [3] * 6)
        directories = {call.args[1]: call.kwargs['trial_dir'] for call in measured.call_args_list}
        self.assertEqual(sorted(directories), sorted(row['run_id'] for row in rows))
        for row in rows:
            self.assertEqual(json.loads((directories[row['run_id']] / 'trial.json').read_text())['run_id'], row['run_id'])
            self.assertTrue(directories[row['run_id']].name.startswith(row['trial_key'] + '-'))
        for name in ('scorecard.json', 'scorecard.md'):
            self.assertNotIn('Groundtruth', (out / name).read_text(encoding='utf-8'))
        self.assertNotIn('Groundtruth', output.getvalue())

    def test_a_real_reports_scorecard_names_no_trial_when_its_suite_ran_a_scoring_set(self):
        # Catches a field of a real report's rows (its keys, runs, cases, reasons and findings) reaching the aggregate.
        from generative_driver.benchmark_support.scorecard import bench_trial_dirs
        from generative_driver.benchmark_support.suite_reporting import report_suite
        sid, rows = self.suite()
        with self.owner():
            report = report_suite(sid, home=self.home, output=self.root / 'export', audit_paths=(self.VAULT,))
        report['experiment']['comparison_identity']['scoring_set'] = {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64}
        card = suite_scorecard(report, home=self.home, trial_dirs=bench_trial_dirs(report, self.home))
        self.assertNotIn('trials', card)
        self.assertEqual(card['pass_line'], '0/1 pass, 0 unknown, 1 faults, 3 planned, 1 flagged')
        text = json.dumps(card) + scorecard_markdown(card)
        for row in rows:
            for private in (row['run_id'], row['trial_key'], row['case'], self.VAULT, 'password'):
                self.assertNotIn(private, text)


if __name__ == '__main__':
    unittest.main()
