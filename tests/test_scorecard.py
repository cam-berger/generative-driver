"""Trial metrics: what a finished run's retained records say about each stage, read afterwards and never recorded."""
import contextlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_configurator
from test_configurator import gateway_identity, scripted_stages, thermometer_model, wait_stopped
from generative_driver.benchmark_support.scorecard import trial_metrics

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


def retime(home, times):
    """Give events new times, as `{seq: time}`, so a test can say how long a stage took."""
    with contextlib.closing(database(home, 'rw')) as db, db:
        db.executemany('UPDATE events SET created=? WHERE seq=?', [(moment, seq) for seq, moment in times.items()])


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
    def scripted(self, work=None, *, runtime=completed, toolkit=None, check=accepting, skip=None, **start):
        """A scripted six-stage run in a temporary home: no model, tool or device runs. `work(controller, request)` acts
        as a stage's worker, `toolkit(name, arguments)` stands in for the tools it calls, `skip(spec, stage)` names the
        reason a stage runs no worker, and `start` adds to the run's parameters. Returns the home, the run's id and its
        result once the run has stopped and its controller is closed."""
        from generative_driver.configurator import Controller
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        home = Path(directory.name) / 'home'
        controller = Controller(home)
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            reason = skip and skip(spec, stage)
            return {'skip': reason} if reason else {'inputs': [], 'allowed_tools': (
                ['probe_run', 'interface_execute'] if stage == 'probe' else [])}
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
        self.assertEqual(metrics['stages']['acquire']['worker_actions'], NO_ACTIONS)
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
        # A stage that ran no worker has nothing to report, and counts nothing.
        self.assertEqual(stages['emit'], {'attempts': 0, 'wall_seconds': None, 'worker_seconds': None, 'managed_calls': {},
                                          'worker_actions': NO_ACTIONS, 'device': NO_DEVICE, 'tokens': UNKNOWN_TOKENS})

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

    def test_interpretation_also_counts_the_transcripts_frozen_into_its_preparation_and_tool_runs(self):
        home, run, result = self.scripted()
        run_dir = Path(home) / 'runs' / run
        def transcript(place, *kinds, lines=()):
            """A transcript with a completed action of each of `kinds`, after the other `lines` as they are."""
            path = run_dir / place / '_agent_events.jsonl'
            path.parent.mkdir(parents=True)
            actions = [json.dumps({'type': 'item.completed', 'item': {'type': kind}}) for kind in kinds]
            path.write_bytes(b''.join((line if isinstance(line, bytes) else line.encode()) + b'\n' for line in [*lines, *actions]))
        # Lines that are not completed actions: one that started, a message, records that are no item, JSON that is no
        # record, and bytes that are no text.
        others = [json.dumps({'type': 'item.started', 'item': {'type': 'command_execution'}}),
                  json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'done'}}),
                  json.dumps({'type': 'item.completed', 'item': 'file_change'}), json.dumps({'type': 'item.completed'}),
                  json.dumps({'type': 'turn.completed', 'usage': USAGE}), '[1, 2]', '3', 'not json', b'\xff\xfe not text']
        transcript('work/interpret/w1', 'command_execution', lines=others)
        transcript('preparation/p1/interpret/attempt1', 'command_execution', 'file_change')
        transcript('preparation/p2/interpret/attempt2', 'mcp_tool_call')
        transcript('benchmark/tools/interpret/attempt3', 'file_change')
        # Not interpretation's: the same places for another stage, a transcript nowhere a stage keeps one, and a directory
        # that has a transcript's name.
        transcript('work/probe/w2', 'command_execution')
        transcript('preparation/p1/probe/attempt1', 'command_execution')
        transcript('benchmark/tools/probe/attempt1', 'command_execution')
        transcript('interpret/attempt1', 'command_execution')
        (run_dir / 'work/interpret/w3/_agent_events.jsonl').mkdir(parents=True)
        stages = trial_metrics(home, run)['stages']
        self.assertEqual(stages['interpret']['worker_actions'], {'command_execution': 2, 'mcp_tool_call': 1, 'file_change': 2})
        self.assertEqual(stages['probe']['worker_actions'], {**NO_ACTIONS, 'command_execution': 1})

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


if __name__ == '__main__':
    unittest.main()
