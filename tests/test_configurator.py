"""Public configurator behavior with a deliberately unavailable external runtime."""
from pathlib import Path
import hashlib
import contextlib
import json
import shutil
import tempfile
import time
import unittest
import sys
import types
import unittest.mock

from line_device import line_device


WORKFLOW_TIMEOUT = 120


def wait_worker_exit(controller, run, seconds=WORKFLOW_TIMEOUT):
    """Wait for a scripted crash's worker, whose durable status can remain running.

    Join the worker rather than repeatedly materializing every handoff while the
    six-stage fixture is still writing them. Read full diagnostics only on failure.
    """
    with controller._lock:
        worker = controller._workers.get(run['run_id'])
    if worker:
        worker[1].join(seconds)
        if worker[1].is_alive():
            raise AssertionError(controller.call('result', run))


def wait_stopped(controller, run, seconds=WORKFLOW_TIMEOUT):
    until = time.monotonic() + seconds
    # A full result expands all stage reports and handoffs. Re-reading it while
    # the same worker writes those files can starve Windows fixture progress.
    wait_worker_exit(controller, run, seconds)
    while time.monotonic() < until:
        status = controller.call('status', run)
        if status['status'] not in ('queued', 'running') and not status['stopping']:
            return controller.call('result', run)
        time.sleep(.05)
    raise AssertionError(controller.call('result', run))


def resume_when_idle(test, controller, run, **params):
    until = time.monotonic() + 5
    while True:
        try:
            return controller.call('resume', {**run, **params})
        except ValueError as exc:
            test.assertIn('stopping', str(exc))
            test.assertLess(time.monotonic(), until)
            time.sleep(.02)


class OutcomeModelTests(unittest.TestCase):
    """One pure decision after every worker: advance, revise, or stop for safety, integrity, operator or host."""
    def decide(self, stage='probe', result=None, checked=None, *, uncertain=False, repairs=0, max_repairs=2):
        from generative_driver.configurator import _outcome
        return _outcome(stage, result or {'status': 'completed'}, checked or {}, uncertain=uncertain,
                        repairs=repairs, max_repairs=max_repairs)

    def test_runtime_that_cannot_run_or_crashed_stops(self):
        for result, category in (
                ({'status': 'blocked', 'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: x'}, 'operator'),
                ({'status': 'blocked', 'cause': 'run_budget', 'reason': 'Stage exhausted the remaining run time budget'}, 'operator'),
                ({'status': 'cancelled', 'cause': 'cancelled', 'reason': 'Run cancelled; external worker stopped'}, 'operator'),
                ({'status': 'failed', 'cause': 'exit_error', 'reason': 'Configured runtime exited with an error'}, 'host'),
                ({'status': 'blocked', 'reason': 'Patched runtime without a cause'}, 'operator'),
                ({'status': 'failed', 'reason': 'Patched runtime without a cause'}, 'host')):
            with self.subTest(result=result):
                outcome = self.decide(result=result)
                self.assertEqual((outcome.kind, outcome.category, outcome.reason), ('stop', category, result['reason']))

    def test_report_content_never_stops(self):
        for cause in ('no_report', 'invalid_report', 'output_limit'):
            with self.subTest(cause=cause):
                outcome = self.decide(result={'status': 'failed', 'cause': cause, 'reason': 'scripted'})
                self.assertEqual((outcome.kind, outcome.category), ('advance', None))
        for status in ('completed', 'needs_revision', 'blocked', 'failed'):
            with self.subTest(status=status):
                report = {'status': status, 'summary': 's', 'artifacts': [], 'unresolved': ['x'],
                          'checks': [{'name': 'c', 'status': 'not_run', 'evidence': []}]}
                outcome = self.decide(result={'status': 'completed', 'report': report})
                self.assertEqual(outcome.kind, 'advance')

    def test_checked_stop_and_uncertainty_stop(self):
        outcome = self.decide(checked={'stop': {'category': 'integrity', 'reason': 'Observed artifact changed'}})
        self.assertEqual((outcome.kind, outcome.category, outcome.reason), ('stop', 'integrity', 'Observed artifact changed'))
        outcome = self.decide(uncertain=True)
        self.assertEqual((outcome.kind, outcome.category), ('stop', 'safety'))
        self.assertIn('uncertain', outcome.reason)

    def test_runtime_then_checked_then_uncertainty_take_precedence(self):
        crashed = {'status': 'failed', 'cause': 'exit_error', 'reason': 'crashed'}
        integrity = {'stop': {'category': 'integrity', 'reason': 'changed'}}
        self.assertEqual(self.decide(result=crashed, checked=integrity, uncertain=True).category, 'host')
        self.assertEqual(self.decide(checked=integrity, uncertain=True).category, 'integrity')

    def test_device_diff_revises_probe_and_ground_within_the_repair_budget(self):
        diff = {'operations': [{'name': 'read', 'predicted': 1, 'observed': 2}]}
        for stage in ('probe', 'ground'):
            with self.subTest(stage=stage):
                outcome = self.decide(stage, checked={'device_diff': diff}, repairs=1, max_repairs=2)
                self.assertEqual((outcome.kind, outcome.diff, outcome.category), ('revise', diff, None))
                spent = self.decide(stage, checked={'device_diff': diff}, repairs=2, max_repairs=2)
                self.assertEqual((spent.kind, spent.reason), ('advance', 'Repair budget exhausted; continuing with best revision'))
        for stage in ('acquire', 'interpret', 'emit', 'reuse'):
            with self.subTest(stage=stage):
                outcome = self.decide(stage, checked={'device_diff': diff})
                self.assertEqual((outcome.kind, outcome.reason), ('advance', None))

    def test_outcome_is_frozen_and_inputs_are_untouched(self):
        import copy
        import dataclasses
        result = {'status': 'completed', 'report': {'status': 'blocked'}}
        checked = {'device_diff': {'x': 1}, 'artifacts': []}
        before = copy.deepcopy((result, checked))
        outcome = self.decide(result=result, checked=checked)
        self.assertEqual((result, checked), before)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            outcome.kind = 'stop'

    def test_no_adapter_verdict_decides_a_stage(self):
        # Catches the gated protocol's adapter verdicts deciding a stage: a check that fails (ok False), names a route,
        # a fault, feedback or a frozen final neither stops the run nor revises it. Only the device diff revises.
        route = {'ok': False, 'route': 'interpret', 'fault': 'model', 'reason': 'defect', 'feedback': {'f': 1},
                 'final_evaluation': True}
        for stage in ('interpret', 'probe', 'ground', 'emit', 'reuse'):
            for repairs in (0, 2):
                with self.subTest(stage=stage, repairs=repairs):
                    outcome = self.decide(stage, checked=route, repairs=repairs)
                    self.assertEqual((outcome.kind, outcome.category, outcome.diff), ('advance', None, None))
        for fault in ('host', 'operator', 'model', None):
            with self.subTest(fault=fault):
                outcome = self.decide('emit', checked={'ok': False, 'fault': fault, 'reason': 'adapter'})
                self.assertEqual((outcome.kind, outcome.category), ('advance', None))
        revised = self.decide('probe', checked={**route, 'device_diff': [{'source': 'probe_run'}]}, repairs=0)
        self.assertEqual((revised.kind, revised.diff), ('revise', [{'source': 'probe_run'}]))


def scripted_stages(controller, *, check, prepare=None, prompts=None, work=None, report=None, runtime=None):
    """Patch one controller so every stage runs an in-process scripted worker; no model, tool or device runs.

    `check` replaces the stage checks; `prepare(original, spec, stage, run_dir, workspace, accepted)` may call
    the real preparation; `work(request)` may call gateway tools first; `report(request)` may return the worker's
    report instead of the default; `runtime(request)` may return the runtime's whole result after the work, such
    as an exit with an error. Each worker writes out.json in its workspace and reports completed.
    """
    from contextlib import ExitStack
    from unittest.mock import patch
    original = controller._prepare
    def fake_prepare(spec, stage, run_dir, workspace, accepted):
        if prepare:
            return prepare(original, spec, stage, run_dir, workspace, accepted)
        return {'inputs': [], 'allowed_tools': []}
    def fake_execute(request, config, cancel):
        if prompts is not None:
            prompts.append((request.stage, request.prompt))
        if work:
            work(request)
        (Path(request.workspace) / 'out.json').write_text(json.dumps({'stage': request.stage}))
        stopped = runtime and runtime(request)
        if stopped:
            return stopped
        return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0, 'usage': None,
                'report': (report and report(request)) or {'status': 'completed', 'summary': 'scripted ' + request.stage,
                           'artifacts': [], 'checks': [], 'unresolved': ['open in ' + request.stage]}}
    stack = ExitStack()
    stack.enter_context(patch.object(controller, '_prepare', side_effect=fake_prepare))
    stack.enter_context(patch.object(controller, '_check', side_effect=check))
    stack.enter_context(patch('generative_driver.configurator.execute', fake_execute))
    return stack


def task_of(prompt):
    """The JSON task after the default prompt's first line."""
    return json.loads(prompt.split('\n', 1)[1])


def context_of(prompt):
    return task_of(prompt)['context']


def gateway_identity(request):
    """The run and assignment a scripted worker's gateway command names."""
    args = request.gateway['args']
    return {'run_id': args[args.index('--run') + 1], 'assignment_id': args[args.index('--assignment') + 1]}


def strings(value):
    """Every string a JSON value holds, keys included, as decoded text: a Windows path inside json.dumps output is
    escaped, so a search there would never find it."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def probed(workspace, assignment):
    """A scripted probe stage's observation: one successful record of its assigned model, bound to that model's digest
    in the notes as the configurator binds the records it observed."""
    record = Path(workspace) / 'interface-1.json'
    record.write_text(json.dumps({'schema': 'interface-probe/1', 'ok': True, 'revision': assignment['revision']}))
    sha = hashlib.sha256(record.read_bytes()).hexdigest()
    bound = [{'name': record.name, 'sha256': sha, 'model_sha256': model} for model in (assignment.get('inputs') or {}).values()]
    return {'ok': True, 'artifacts': [str(record)], 'checks': [], 'notes': {'records': bound}}


class AssistiveHandoffTests(unittest.TestCase):
    """Every stage hands off notes; a revise commits with its partial handoff; protocol labels gate resume."""
    def test_crash_after_an_accepted_revise_resumes_at_interpret_with_the_new_revision(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        diff = {'operations': [{'name': 'read_temperature', 'predicted': 21.0, 'observed': 42.0}]}
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            out = {'ok': True, 'artifacts': [str(workspace / 'out.json')], 'checks': []}
            if stage == 'probe' and assignment['revision'] == 0:
                out['device_diff'] = diff
            return out
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            controller = Controller(home)
            real_accept = controller._accept
            def accept_then_crash(*args, **kwargs):
                handoff = real_accept(*args, **kwargs)
                if kwargs.get('revise'):
                    raise SystemExit('scripted configurator death right after the revise transaction')
                return handoff
            try:
                with scripted_stages(controller, check=check), patch.object(controller, '_accept', side_effect=accept_then_crash):
                    run = controller.call('start', {'goal': 'Scripted revise contract; no model or device',
                                                    'executor_config': {'command': ['never-launched']}})
                    wait_worker_exit(controller, run)
                self.assertEqual(controller.call('status', run)['status'], 'running')
            finally:
                controller.close()
            reopened = Controller(home)
            try:
                crashed = reopened.call('result', run)
                self.assertEqual(crashed['status'], 'blocked')
                self.assertEqual({k: crashed['progress'][k] for k in ('revision', 'next_stage', 'repairs', 'feedback')},
                                 {'revision': 1, 'next_stage': 'interpret', 'repairs': 1, 'feedback': diff})
                probe = crashed['accepted_handoffs'][-1]
                self.assertEqual((probe['stage'], probe['revision'], probe['route']), ('probe', 0, 'interpret'))
                self.assertEqual(probe['notes']['inline']['outcome']['kind'], 'revise')
                self.assertEqual(probe['notes']['inline']['configurator']['device_diff'], diff)
                events = reopened.call('events', run)['events']
                kinds = [e['kind'] for e in events]
                self.assertEqual(kinds.count('run.revision'), 1)
                self.assertEqual(kinds[kinds.index('run.revision') - 1], 'stage.accepted')
                with scripted_stages(reopened, check=check):
                    resume_when_idle(self, reopened, run)
                    result = wait_stopped(reopened, run)
                events = reopened.call('events', run)['events']
                start = max(i for i, e in enumerate(events) if e['kind'] == 'run.resumed')
                assigned = [(e['data']['stage'], e['data']['revision']) for e in events[start:] if e['kind'] == 'stage.assigned']
                self.assertEqual(assigned, [(stage, 1) for stage in ('interpret', 'probe', 'ground', 'emit', 'reuse')])
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([(h['stage'], h['revision']) for h in result['accepted_handoffs']],
                                 [('acquire', 0), ('interpret', 0), ('probe', 0), ('interpret', 1), ('probe', 1),
                                  ('ground', 1), ('emit', 1), ('reuse', 1)])
            finally:
                reopened.close()

    def test_generic_model_reaches_probe_and_emit_and_notes_travel_except_to_reuse(self):
        import shutil
        from generative_driver.configurator import Controller, digest
        from test_package_data import emitted
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'interpret':
                model = workspace / 'model'; model.mkdir()
                (model / 'model.json').write_text('{"schema": "interface-model/7"}')
                return {'ok': True, 'artifacts': [str(model)], 'checks': []}
            if stage == 'probe':
                return probed(workspace, assignment)
            if stage == 'emit':
                # A package the tool emits, so that the real preparation of reuse finds its generated tools.
                package = shutil.copytree(emitted(Path(run_dir) / 'emitted-for-the-test')[0]['package_dir'],
                                          workspace / 'package')
                return {'ok': True, 'artifacts': [str(package)], 'checks': []}
            return {'ok': True, 'artifacts': [], 'checks': []}
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            # Generic probe, emit and reuse use the real input selection; sealed interpret and ground stay scripted here.
            if stage in ('probe', 'emit', 'reuse'):
                return original(spec, stage, run_dir, workspace, accepted)
            return {'inputs': [], 'allowed_tools': []}
        prompts = []
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with scripted_stages(controller, check=check, prepare=prepare, prompts=prompts):
                    run = controller.call('start', {'goal': 'Scripted input selection; no model or device',
                                                    'executor_config': {'command': ['never-launched']}})
                    result = wait_stopped(controller, run)
                self.assertEqual(result['status'], 'completed', result)
                model = next(Path(a['path']) for h in result['accepted_handoffs'] if h['stage'] == 'interpret' for a in h['artifacts'])
                probe = next(Path(a['path']) for h in result['accepted_handoffs'] if h['stage'] == 'probe' for a in h['artifacts'])
                assigned = {e['data']['stage']: e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'}
                hashes = lambda stage: sorted(assigned[stage]['inputs'].values())
                self.assertEqual(hashes('probe'), [digest(model)])
                # Ground handed off nothing; emit receives the model and the successful probe record it wrote.
                self.assertEqual(hashes('emit'), sorted([digest(model), digest(probe)]))
                self.assertTrue(any((Path(p) / 'model.json').is_file() for p in assigned['emit']['inputs']))
                contexts = {stage: context_of(prompt) for stage, prompt in prompts if prompt.startswith('Execute exactly')}
                self.assertEqual([n['stage'] for n in contexts['probe']['prior_notes']], ['acquire', 'interpret'])
                self.assertEqual([n['stage'] for n in contexts['emit']['prior_notes']], ['acquire', 'interpret', 'probe', 'ground'])
                self.assertIn('open in ground', contexts['emit']['prior_notes'][-1]['notes']['open_questions'])
                self.assertNotIn('prior_notes', contexts['reuse'])
                reuse_prompt = next(prompt for stage, prompt in prompts if stage == 'reuse')
                self.assertNotIn('open in', reuse_prompt)
                self.assertNotIn('stage-notes/1', reuse_prompt)
            finally:
                controller.close()

    def test_inputs_come_from_the_latest_handoff_that_has_them_and_absence_is_a_note(self):
        from generative_driver.configurator import Controller, digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                model = root / 'model-r0'; model.mkdir(); (model / 'model.json').write_text('{}')
                records = root / 'probe-r0.json'; records.write_text('{"records": []}')
                notes = lambda stage, revision: {'inline': {'schema': 'stage-notes/1', 'open_questions': [stage + str(revision)]}}
                handoff = lambda stage, revision, paths: {'stage': stage, 'revision': revision, 'assignment_id': stage + str(revision),
                    'artifacts': [{'path': str(p), 'sha256': digest(p)} for p in paths], 'notes': notes(stage, revision)}
                accepted = [handoff('acquire', 0, []), handoff('interpret', 0, [model]), handoff('probe', 0, [records]),
                            handoff('interpret', 1, []), handoff('probe', 1, [])]
                spec = {'goal': 'x', 'effects': ['read'], '_revision': 1}
                selected, missing = controller._stage_inputs('ground', accepted)
                self.assertEqual(selected, [str(model), str(records)])
                self.assertEqual(missing, [])
                (root / 'emit').mkdir()
                prepared = controller._prepare(spec, 'emit', root / 'run', root / 'emit', accepted)
                # Emit takes only a successful record the model wrote, as the notes bind it: none here, so the model alone.
                self.assertEqual([digest(p) for p in prepared['inputs']], [digest(model)])
                self.assertEqual(prepared['input_notes'], ['No successful probe record of an interpreted model available'])
                # Notes of the current revision only: acquire is shared, revision-0 interpret and probe are not.
                self.assertEqual([(n['stage'], n['revision']) for n in prepared['context']['prior_notes']],
                                 [('acquire', 0), ('interpret', 1), ('probe', 1)])
                (root / 'probe').mkdir()
                prepared = controller._prepare(spec, 'probe', root / 'run', root / 'probe', accepted[:1])
                self.assertEqual(prepared['inputs'], [])
                self.assertEqual(prepared['context']['input_notes'], ['No interpreted model available'])
                self.assertEqual(prepared['input_notes'], ['No interpreted model available'])
                self.assertEqual(controller._stage_inputs('ground', accepted[:2]),
                                 ([str(model)], ['No probe records available']))
            finally:
                controller.close()

    def test_reuse_without_a_package_is_skipped_with_a_note(self):
        from generative_driver.configurator import Controller
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            return {'ok': True, 'artifacts': [], 'checks': []}
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            return original(spec, stage, run_dir, workspace, accepted) if stage == 'reuse' else {'inputs': [], 'allowed_tools': []}
        prompts = []
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with scripted_stages(controller, check=check, prepare=prepare, prompts=prompts):
                    run = controller.call('start', {'goal': 'Scripted empty emission; no model or device',
                                                    'executor_config': {'command': ['never-launched']}})
                    result = wait_stopped(controller, run)
                self.assertEqual(result['status'], 'completed', result)
                # The run still ends with its verdict: nothing was emitted, so nothing is equivalent.
                self.assertTrue(result['reason'].startswith('Final verdict: not equivalent: no driver emitted'), result['reason'])
                self.assertEqual((result['equivalence']['equivalent'], result['equivalence']['basis']), (False, 'self-evidence'))
                self.assertNotIn('reuse', [stage for stage, _ in prompts])
                self.assertNotIn('reuse', [r['stage'] for r in result['worker_reports']])
                reuse = result['accepted_handoffs'][-1]
                self.assertEqual((reuse['stage'], reuse['artifacts']), ('reuse', []))
                notes = reuse['notes']['inline']
                self.assertTrue(any('emitted package' in q for q in notes['open_questions']), notes)
                self.assertEqual((notes['worker']['runtime_status'], notes['outcome']['kind']), ('skipped', 'advance'))
                kinds = [e['kind'] for e in controller.call('events', run)['events']]
                self.assertEqual(kinds.count('stage.skipped'), 1)
                self.assertEqual(kinds.count('stage.assigned'), 5)
            finally:
                controller.close()

    def test_a_run_from_the_earlier_gated_protocol_cannot_resume(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = Controller(root)
            run = controller.call('start', {'goal': 'Protocol label contract',
                                            'executor_config': {'command': ['missing-scripted-runtime']}})
            stopped = wait_stopped(controller, run)
            self.assertEqual(stopped['progress']['protocol'], 'assistive/1')
            earlier = {k: v for k, v in stopped['progress'].items() if k != 'protocol'}
            earlier['feedback'] = {'graded': 'feedback from the earlier protocol'}
            with controller._db() as db:
                db.execute('UPDATE progress SET payload=? WHERE run_id=?', (json.dumps(earlier), run['run_id']))
            before = controller.call('events', run)['events']
            with self.assertRaisesRegex(ValueError, 'Run was created under the earlier gated protocol; start a new run'):
                controller.call('resume', run)
            self.assertEqual(controller.call('events', run)['events'], before)
            self.assertEqual(controller.call('status', run)['status'], 'blocked')
            with controller._db() as db:
                db.execute("UPDATE runs SET status='queued' WHERE id=?", (run['run_id'],))
            controller.close()
            reopened = Controller(root)
            try:
                status = reopened.call('status', run)
                self.assertEqual(status['status'], 'blocked')
                self.assertIn('earlier gated protocol', status['reason'])
                self.assertFalse(any(e['kind'] == 'stage.assigned' for e in reopened.call('events', run)['events'][len(before):]))
            finally:
                reopened.close()


def thermometer_model(name='Scripted thermometer', *, steps=True):
    """A schema-3 line-protocol thermometer on a TCP channel that the real validator accepts; with `steps=False`
    its read has no steps, which the validator and the runtime refuse."""
    def until(command, capture, **extra):
        return {'op': 'exchange', 'tx': [{'text': command}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64},
                'timeout_ms': 1000, 'capture': capture, **extra}
    return {'schema': 'interface-model/7', 'device': {'name': name}, 'channel': {'type': 'tcp'},
            'identity': {'operation': 'identify'},
            'operations': {
                'identify': {'effect': 'read', 'parameters': {}, 'outputs': {},
                             'steps': [until('ID\n', 'id', expect={'contains_text': 'THERMO'})]},
                'read_temperature': {'effect': 'read', 'parameters': {}, 'steps': [until('T\n', 't')] if steps else [],
                                     'outputs': {'temperature_c': {'from': 't', 'kind': 'ascii_number', 'unit': 'degC'}}}},
            'provenance': [{'item': 'operations.' + operation, 'source': 'scripted fixture'}
                           for operation in ('identify', 'read_temperature')]}


class DeviceRepairTests(unittest.TestCase):
    """Generic ground runs a worker; model-versus-device differences the configurator observed drive repair."""
    def test_device_diff_holds_only_model_faults_on_the_device_connection_with_the_assigned_model(self):
        from generative_driver.configurator import _device_diff, _probe_records
        ws = Path('/w').resolve()
        model, record = str(ws / 'inputs' / '0-model'), str(ws / 'inputs' / '1-0-interface-1.json')
        assignment = {'stage': 'ground', 'workspace': str(ws), 'inputs': {model: 'model-sha', record: 'record-sha'}}
        probe = lambda name: str(ws / 'tools' / 'probe' / name)
        defects = [{'where': 'operations.read.steps.0', 'what': 'device answered 2a; model predicted 15', 'fault': 'model'}]
        failed = {'schema': 'interface-result/1', 'ok': False, 'operation': 'read', 'transport_open_attempted': True,
                  'error': {'code': 'framing', 'message': 'short reply'}, 'transcript': [{'tx_hex': '01', 'rx_hex': '2a'}]}
        def before_open(code, message):
            return {'schema': 'interface-result/1', 'ok': False, 'operation': 'read', 'transport_open_attempted': False,
                    'error': {'code': code, 'message': message}, 'transcript': []}
        # What interface_execute and probe_run really return for a model the runtime refuses: run_samples validates
        # before any transport opens and raises, and the tool wrapper reports the exception.
        refused = {'_exit': 1, 'error': "ValueError: invalid model: [{'where': 'operations.read', 'what': 'requires 1–128 steps'}]"}
        def event(dispatch, name, ok, result, *, connected=None, model_sha='model-sha', artifacts=None, **shas):
            connected = name in ('probe_run', 'interface_execute') if connected is None else connected
            return {'name': name, 'ok': ok, 'dispatch_id': dispatch, 'result': result, 'connected': connected,
                    'argument_sha256': {'model_dir': model_sha, **shas}, 'artifacts': artifacts or {}}
        live_fault = lambda path, results: {'ok': False, 'fault': 'model', 'error_code': 'framing', 'error_message': 'short reply',
                                            'evidence_kind': 'live', 'probe': path, 'results': results, '_exit': 1}
        events = [
            event('7', 'probe_diff', False, {'ok': False, 'defects': defects, 'diagnostics': [], 'source_evidence_kind': 'live', '_exit': 1},
                  probe_json='probe-10-sha'),
            event('8', 'probe_diff', True, {'ok': True, 'defects': [], '_exit': 0}, probe_json='probe-10-sha'),
            event('9', 'probe_diff', False, {'ok': False, 'defects': [], 'diagnostics': [{'where': 'transport', 'fault': 'host'}], '_exit': 4},
                  probe_json='probe-10-sha'),
            event('10', 'probe_run', False, live_fault(probe('interface-1.json'), [failed]),
                  artifacts={probe('interface-1.json'): 'probe-10-sha'}),
            event('11', 'probe_run', False, {'ok': False, 'fault': 'operator', 'reason': 'Explicit write effect grant required', '_exit': 4}),
            event('12', 'probe_run', False, {'ok': False, 'fault': 'host', 'error_code': 'open', 'results': [], '_exit': 4}),
            event('15', 'probe_run', False, {'ok': False, 'error': 'a probe requires an explicit operation name', '_exit': 1}),
            event('16', 'probe_run', True, {'ok': True, 'evidence_kind': 'live', 'probe': probe('interface-2.json'), '_exit': 0}),
            event('19', 'interface_execute', False, {**failed, 'evidence_kind': 'live', '_exit': 1}),
            event('20', 'interface_execute', False, {**before_open('validation_error', 'operation requires explicit effect grant'),
                                                     'evidence_kind': 'live', '_exit': 1}),
            event('21', 'model_validate', False, {'ok': False, 'defects': [{'where': 'model.json', 'what': 'missing'}], '_exit': 1}),
            # A replay of a transcript the worker wrote reaches no device.
            event('22', 'probe_run', False, {**live_fault(probe('interface-3.json'), [failed]), 'evidence_kind': 'replay'},
                  connected=False, replay='worker-transcript-sha', artifacts={probe('interface-3.json'): 'probe-22-sha'}),
            event('23', 'interface_execute', False, {**failed, 'evidence_kind': 'replay', '_exit': 1}, connected=False,
                  replay='worker-transcript-sha'),
            # The runtime refuses an invalid assigned model before any transport opens, as a tool error. The device did
            # nothing, so neither is in the diff from tool results; the configurator's own validation of the assigned
            # model is the model-contract entry.
            event('24', 'interface_execute', False, refused),
            event('25', 'probe_run', False, refused),
            # A failure before the transport opened holds nothing the device did, whatever its code.
            event('26', 'interface_execute', False, {**before_open('invalid', 'operations.read: no steps'), 'evidence_kind': 'live', '_exit': 1}),
            event('27', 'probe_run', False, {**live_fault(probe('interface-4.json'), [before_open('channel', 'uart channel requires baudrate')]),
                                             'error_code': 'channel', 'error_message': 'uart channel requires baudrate'},
                  artifacts={probe('interface-4.json'): 'probe-27-sha'}),
            # A model the worker wrote is not the assigned model.
            event('28', 'probe_run', False, live_fault(probe('interface-5.json'), [failed]), model_sha='variant-sha',
                  artifacts={probe('interface-5.json'): 'probe-28-sha'}),
            event('29', 'probe_diff', False, {'ok': False, 'defects': defects, 'source_evidence_kind': 'live', '_exit': 1},
                  probe_json='worker-written-sha'),
            event('30', 'probe_diff', False, {'ok': False, 'defects': defects, 'source_evidence_kind': 'live', '_exit': 1},
                  probe_json='probe-28-sha'),
            event('31', 'probe_diff', False, {'ok': False, 'defects': defects, 'source_evidence_kind': 'replay', '_exit': 1},
                  probe_json='probe-22-sha'),
            event('32', 'interface_execute', False, {**failed, 'evidence_kind': 'live', '_exit': 1}, model_sha='variant-sha'),
            event('33', 'probe_diff', False, {'ok': False, 'defects': defects, 'source_evidence_kind': 'replay', '_exit': 1},
                  probe_json='record-sha'),
            # The record of a run that failed before the transport opened holds nothing the device did.
            event('36', 'probe_diff', False, {'ok': False, 'defects': defects, 'source_evidence_kind': 'live', '_exit': 1},
                  probe_json='probe-27-sha'),
            {k: v for k, v in event('34', 'interface_execute', False, {**failed, 'evidence_kind': 'live', '_exit': 1}).items()
             if k != 'connected'},
            # An accepted probe record handed on to this stage is device evidence too. Host paths never leave.
            event('35', 'probe_diff', False, {'ok': False, 'source_evidence_kind': 'live', '_exit': 1, 'defects': [
                {'where': 'identity', 'what': 'device answered 0x58, model expects 0x60', 'evidence': record}]},
                  probe_json='record-sha'),
            # A run that writes a record path an earlier run wrote replaces the earlier record, which is no longer
            # there to hand on. What the device did stays in the diff, but only the surviving record is named.
            event('40', 'probe_run', False, live_fault(probe('probe.json'), [failed]), artifacts={probe('probe.json'): 'rewritten-1-sha'}),
            event('41', 'probe_diff', False, {'ok': False, 'defects': defects, '_exit': 1}, probe='rewritten-1-sha'),
            event('42', 'probe_run', False, live_fault(probe('probe.json'), [failed]), artifacts={probe('probe.json'): 'rewritten-2-sha'})]
        contract = [{'where': 'operations.read', 'what': 'requires 1–128 steps'},
                    {'where': 'model.json', 'what': 'names a file beside it: ' + str(Path(model) / 'notes.txt')}]
        contracts = {model: {'ok': False, 'defects': contract, '_exit': 1}}
        # The earlier stage's notes bound the handed-on record to the assigned model, which wrote it.
        diff = _device_diff(events, assignment, contracts, {'record-sha': {'model-sha'}})
        # A handed-on record that another model wrote, or that no notes bind, says nothing about the assigned model.
        for writers in ({'record-sha': {'variant-sha'}}, {}, None):
            self.assertEqual([entry for entry in _device_diff(events, assignment, contracts, writers)],
                             [entry for entry in diff if entry.get('dispatch_id') != '35'])
        self.assertEqual([(entry['source'], entry.get('dispatch_id')) for entry in diff],
                         [('model_contract', None), ('probe_diff', '7'), ('probe_run', '10'),
                          ('interface_execute', '19'), ('probe_diff', '35'), ('probe_run', '40'), ('probe_diff', '41'),
                          ('probe_run', '42')])
        # The configurator's validation of the assigned model claims nothing about the device and names the model by content.
        self.assertEqual(diff[0], {'source': 'model_contract', 'model': {'name': '0-model', 'sha256': 'model-sha'},
                                   'defects': [contract[0], {'where': 'model.json', 'what': 'names a file beside it: ' + str(Path('0-model') / 'notes.txt')}]})
        self.assertEqual(diff[1], {'source': 'probe_diff', 'dispatch_id': '7', 'defects': defects,
                                   'probe': {'name': 'interface-1.json', 'sha256': 'probe-10-sha'}})
        self.assertEqual({k: diff[2][k] for k in ('code', 'what', 'probe', 'failed')},
                         {'code': 'framing', 'what': 'short reply', 'failed': [failed],
                          'probe': {'name': 'interface-1.json', 'sha256': 'probe-10-sha'}})
        self.assertEqual((diff[3]['code'], diff[3]['what']), ('framing', 'short reply'))
        self.assertEqual(diff[4]['probe'], {'name': '1-0-interface-1.json', 'sha256': 'record-sha'})
        self.assertEqual(diff[4]['defects'][0]['evidence'], '1-0-interface-1.json')
        self.assertEqual([entry.get('probe') for entry in diff[5:]], [None, None, {'name': 'probe.json', 'sha256': 'rewritten-2-sha'}])
        self.assertEqual((diff[5]['what'], diff[7]['what']), ('short reply', 'short reply'))
        self.assertFalse(any(str(ws) in text for text in strings(diff)))
        # The stage hands on the surviving record of every run the device answered on its connection, of any model.
        kept, superseded = _probe_records(events)
        self.assertEqual({Path(path).name: sha for path, (sha, _) in kept.items()},
                         {'interface-1.json': 'probe-10-sha', 'interface-5.json': 'probe-28-sha', 'probe.json': 'rewritten-2-sha'})
        self.assertEqual(superseded, ['40'])
        self.assertEqual(_device_diff([], assignment), [])
        # Only defects the validator found in an assigned model are a contract entry: a validator that did not run is not.
        for result in ({'ok': True, 'defects': []}, {'ok': False, '_exit': 1, 'error': 'OSError: disk unavailable'}):
            self.assertEqual(_device_diff([], assignment, {model: result}), [])
        self.assertEqual(_device_diff([], assignment, {str(ws / 'variant'): {'ok': False, 'defects': contract}}), [])

    BINDING = {'host': '127.0.0.1', 'port': 45681}
    MODEL = thermometer_model()
    FAILED = {'schema': 'interface-result/1', 'ok': False, 'operation': 'read_temperature', 'transport_open_attempted': True,
              'error': {'code': 'framing', 'message': 'short reply'}, 'transcript': [{'tx_hex': '01', 'rx_hex': '2a'}]}

    def run_device_stage(self, home, work, respond, *, stage='ground', model=None, extra=None, real=(), effects=None,
                         max_revisions=None, binding=None):
        """A generic run in which `stage` (probe or ground, or a tuple of both) uses the real preparation, gateway and
        check and every other stage is scripted; interpret hands on `model` (the valid thermometer by default) with the
        `extra` files (name to text, or bytes) beside it. The stage worker calls tools
        through `work(call, model, workspace)`, or `work[stage](...)` when `work` maps stages to workers.
        `respond(name, arguments)` stands in for each worker tool except those
        named in `real`, which run in the real toolkit; a stand-in probe_run writes its probe file as the real tool does
        unless `respond` names one. The configurator's own validation of the assigned model always runs the real
        validator. `effects` and `max_revisions` are the run's grants and repair budget, and `binding` its device binding
        (the TCP thermometer's by default). No device runs. Returns the
        result, the events, the prompts and each worker tool's configurator-built arguments."""
        stages = (stage,) if isinstance(stage, str) else tuple(stage)
        from unittest.mock import patch
        from generative_driver import toolkit
        from generative_driver.configurator import Controller
        real_call_tool = toolkit.call_tool
        calls = []
        def fake_call_tool(name, arguments):
            if name == 'model_validate':
                return real_call_tool(name, arguments)
            calls.append((name, dict(arguments)))
            if name in real:
                return real_call_tool(name, arguments)
            out = dict(respond(name, arguments))
            if name == 'probe_run' and 'probe' not in out:
                folder = Path(arguments['run_dir']) / 'probe'; folder.mkdir(parents=True, exist_ok=True)
                probe = folder / ('interface-' + str(len(list(folder.iterdir())) + 1) + '.json')
                probe.write_text(json.dumps({'schema': 'interface-probe/1', 'ok': out['ok'], 'results': out.get('results', [])}))
                out['probe'] = str(probe)
            return out
        controller = Controller(home)
        real_check = controller._check
        def check(spec, name, run_dir, workspace, report, accepted, assignment):
            if name in stages:
                return real_check(spec, name, run_dir, workspace, report, accepted, assignment)
            if name == 'interpret':
                folder = workspace / 'model'; folder.mkdir()
                (folder / 'model.json').write_text(json.dumps(model or self.MODEL))
                for name, text in (extra or {}).items():
                    (folder / name).write_bytes(text) if isinstance(text, bytes) else (folder / name).write_text(text)
                return {'ok': True, 'artifacts': [str(folder)], 'checks': []}
            if name == 'probe':
                return {'ok': True, 'artifacts': [str(workspace / 'out.json')], 'checks': []}
            return {'ok': True, 'artifacts': [], 'checks': []}
        def prepare(original, spec, name, run_dir, workspace, accepted):
            return original(spec, name, run_dir, workspace, accepted) if name in (*stages, 'reuse') else {'inputs': [], 'allowed_tools': []}
        def worker(request):
            act = (work.get(request.stage) if isinstance(work, dict) else work) if request.stage in stages else None
            if act is not None:
                assigned = next(p for p in task_of(request.prompt)['inputs'] if (Path(p) / 'model.json').is_file())
                call = lambda name, **arguments: controller.call('tool', {**gateway_identity(request), 'name': name, 'arguments': arguments})
                act(call, assigned, Path(request.workspace))
        prompts = []
        try:
            with scripted_stages(controller, check=check, prepare=prepare, prompts=prompts, work=worker), \
                 patch('generative_driver.toolkit.call_tool', fake_call_tool):
                try:
                    run = controller.call('start', {'goal': 'Scripted ' + '/'.join(stages) + ' contract; no model or device',
                                                    'binding': binding or self.BINDING, 'executor_config': {'command': ['never-launched']},
                                                    **({'effects': effects} if effects else {}),
                                                    **({'max_revisions': max_revisions} if max_revisions is not None else {})})
                    result = wait_stopped(controller, run)
                finally:
                    self.assertTrue(controller.close(timeout=WORKFLOW_TIMEOUT), 'Fixture worker did not release its files')
            return result, controller.call('events', run)['events'], prompts, calls
        finally:
            controller.close()

    def test_generic_ground_runs_a_worker_that_tests_the_model_on_the_device(self):
        from generative_driver.configurator import digest
        ok = {'ok': True, 'evidence_kind': 'live', 'results': [{'ok': True, 'outputs': {'temperature_c': 21.5}}], '_exit': 0}
        with tempfile.TemporaryDirectory() as directory:
            result, events, prompts, calls = self.run_device_stage(Path(directory) / 'home',
                lambda call, model, workspace: call('probe_run', model_dir=model, operation='read_temperature'), lambda name, arguments: ok)
            self.assertEqual(result['status'], 'completed', result)
            handoffs = {h['stage']: h for h in result['accepted_handoffs']}
            assigned = {e['data']['stage']: e['data'] for e in events if e['kind'] == 'stage.assigned'}
            self.assertEqual(assigned['ground']['allowed_tools'], ['probe_run', 'interface_execute', 'probe_diff'])
            model, records = (Path(handoffs[s]['artifacts'][0]['path']) for s in ('interpret', 'probe'))
            self.assertEqual(sorted(assigned['ground']['inputs'].values()), sorted([digest(model), digest(records)]))
            task = task_of(next(prompt for stage, prompt in prompts if stage == 'ground'))
            for meaning in ('units', 'scaling', 'effects', 'operator observations'):
                self.assertIn(meaning, task['objective'])
            self.assertIn('device binding is attached', task['context']['device'])
            self.assertNotIn('45681', json.dumps(task))
            # The configurator, never the worker, supplied the connection, and it records that and which model ran.
            self.assertEqual([(name, arguments['binding']) for name, arguments in calls], [('probe_run', self.BINDING)])
            finished = next(e['data'] for e in events if e['kind'] == 'tool.finished')
            self.assertEqual((finished['connected'], finished['argument_sha256']), (True, {'model_dir': digest(model)}))
            ground = handoffs['ground']
            self.assertEqual([Path(a['path']).name for a in ground['artifacts']], ['0-interface-1.json'])
            notes = ground['notes']['inline']
            checks = {c['name']: c for c in notes['configurator']['checks']}
            self.assertTrue(checks['observed_device_test']['passed'])
            # The configurator validated the assigned model itself.
            self.assertEqual(checks['executable_model_contract']['passed'], True)
            self.assertEqual((notes['outcome']['kind'], notes['configurator']['device_diff']), ('advance', None))
            self.assertFalse(any(e['kind'] == 'tool.refused' for e in events))

    def test_01_B5_a_generic_ground_worker_gets_three_tools_and_no_check_of_a_supplied_capture(self):
        import re
        ok = {'ok': True, 'evidence_kind': 'live', '_exit': 0,
              'results': [{'ok': True, 'outputs': {'temperature_c': 21.5}}]}
        # What the deleted tool returned for a capture that contradicts the model.
        refuted = {'confirmed': 0, 'refuted': 1, 'not_observed': 0, '_exit': 1,
                   'lines': ['refuted        read_temperature']}
        tools = ['probe_run', 'interface_execute', 'probe_diff']
        refusals = []
        def work(call, model, workspace):
            capture = workspace / 'capture.json'; capture.write_text('{"transactions": []}')
            call('probe_run', model_dir=model, operation='read_temperature')
            try:
                call('probe_observe', model_dir=model, bus=str(capture))
            except ValueError as refusal:
                refusals.append(str(refusal))
        with tempfile.TemporaryDirectory() as directory:
            result, events, prompts, calls = self.run_device_stage(Path(directory) / 'home', work,
                lambda name, arguments: ok if name == 'probe_run' else refuted)
        self.assertEqual(result['status'], 'completed', result)
        assigned = next(e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'ground')
        self.assertEqual(assigned['allowed_tools'], tools)
        objective = task_of(next(prompt for stage, prompt in prompts if stage == 'ground'))['objective']
        self.assertLessEqual(set(re.findall(r'[a-z]+(?:_[a-z]+)+', objective)), set(tools), objective)
        self.assertNotIn('capture', objective)
        # A tool that is not assigned never runs, so no capture a worker supplies reaches the notes.
        self.assertEqual([name for name, _ in calls], ['probe_run'])
        self.assertEqual(len(refusals), 1, refusals)
        self.assertIn('not assigned', refusals[0])
        notes = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')['notes']['inline']
        self.assertNotIn('supplied_capture_observations', [check['name'] for check in notes['configurator']['checks']])
        self.assertNotIn('supplied_capture_observations', json.dumps(notes))

    def test_ground_counts_live_executions_as_device_tests_and_never_a_replay(self):
        from generative_driver.configurator import digest
        replayed = {'ok': True, 'evidence_kind': 'replay', 'results': [{'ok': True, 'outputs': {'temperature_c': 21.5}}], '_exit': 0}
        executed = {'schema': 'interface-result/1', 'ok': True, 'outputs': {'temperature_c': 21.5}, 'evidence_kind': 'live', '_exit': 0}
        transcripts = []
        def plan(execute):
            def work(call, model, workspace):
                transcript = workspace / 'transcript.json'; transcript.write_text('{"schema": "interface-replay/1", "transactions": []}')
                transcripts.append(transcript)
                call('probe_run', model_dir=model, operation='read_temperature', replay=str(transcript))
                if execute:
                    call('interface_execute', model_dir=model, operation='read_temperature')
            return work
        for name, expect in (('replay only', False), ('replay and a live execution', True)):
            with self.subTest(plan=name), tempfile.TemporaryDirectory() as directory:
                result, events, _, calls = self.run_device_stage(Path(directory) / 'home', plan(expect),
                    lambda name, arguments: replayed if name == 'probe_run' else executed)
                self.assertEqual(result['status'], 'completed', result)
                ground = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')
                # A replay reaches no device: it gets no binding, is recorded unconnected and is never handed on.
                self.assertEqual(ground['artifacts'], [])
                self.assertNotIn('binding', calls[0][1])
                finished = {e['data']['name']: e['data'] for e in events if e['kind'] == 'tool.finished'}
                self.assertEqual((finished['probe_run']['connected'], finished['probe_run']['argument_sha256']['replay']),
                                 (False, digest(transcripts[-1])))
                test = next(c for c in ground['notes']['inline']['configurator']['checks'] if c['name'] == 'observed_device_test')
                self.assertEqual(test['passed'], expect)
                if expect:
                    self.assertEqual(test['executions'], [finished['interface_execute']['dispatch_id']])
                else:
                    self.assertIn('No observed successful device test', test['reason'])

    def test_differences_the_configurator_never_saw_the_device_show_on_the_assigned_model_never_revise(self):
        failed = self.FAILED
        # The operator, not the model, owns a missing effect grant.
        before_open = {'schema': 'interface-result/1', 'ok': False, 'operation': 'read_temperature', 'transport_open_attempted': False,
                       'error': {'code': 'validation_error', 'message': 'operation requires explicit effect grant'}, 'transcript': []}
        defects = [{'where': 'operations.read_temperature.steps.0', 'what': 'device answered 2a', 'code': 'framing', 'fault': 'model'}]
        def respond(name, arguments):
            if name == 'probe_run' and arguments['operation'] == 'identify':
                # Only the worker's variant model answers on the device.
                return {'ok': True, 'evidence_kind': 'live', 'results': [{'ok': True, 'outputs': {'identity': '0x60'}}], '_exit': 0}
            if name == 'probe_run':
                kind = 'replay' if arguments.get('replay') else 'live'
                return {'ok': False, 'fault': 'model', 'error_code': 'framing', 'error_message': 'short reply',
                        'evidence_kind': kind, 'results': [failed], '_exit': 1}
            if name == 'interface_execute':
                return {**(failed if arguments.get('replay') else before_open),
                        'evidence_kind': 'replay' if arguments.get('replay') else 'live', '_exit': 1}
            return {'ok': False, 'defects': defects, 'diagnostics': [], 'source_evidence_kind': 'live', '_exit': 1}
        def work(call, model, workspace):
            transcript = workspace / 'transcript.json'; transcript.write_text('{"schema": "interface-replay/1", "transactions": []}')
            variant = workspace / 'variant'; variant.mkdir()
            (variant / 'model.json').write_text(json.dumps(thermometer_model('Worker variant')))
            written = workspace / 'observed.json'; written.write_text('{}')
            call('probe_run', model_dir=model, operation='read_temperature', replay=str(transcript))
            call('interface_execute', model_dir=model, operation='read_temperature', replay=str(transcript))
            call('interface_execute', model_dir=model, operation='read_temperature')
            variant_probe = call('probe_run', model_dir=str(variant), operation='read_temperature')['probe']
            call('probe_run', model_dir=str(variant), operation='identify')
            call('probe_diff', model_dir=model, probe_json=str(written))
            call('probe_diff', model_dir=model, probe_json=variant_probe)
        with tempfile.TemporaryDirectory() as directory:
            result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, respond)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([name for name, _ in calls],
                         ['probe_run', 'interface_execute', 'interface_execute', 'probe_run', 'probe_run', 'probe_diff', 'probe_diff'])
        self.assertEqual((result['progress']['repairs'], result['progress']['revision']), (0, 0))
        self.assertEqual([h['revision'] for h in result['accepted_handoffs']], [0] * 6)
        self.assertFalse(any(e['kind'] == 'run.revision' for e in events))
        notes = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')['notes']['inline']
        self.assertEqual((notes['outcome']['kind'], notes['configurator']['device_diff']), ('advance', None))
        # The variant answered on the device, but the assigned model was never tested there; the variant run stays visible.
        variant = [e['data']['dispatch_id'] for e in events if e['kind'] == 'tool.finished' and e['data']['name'] == 'probe_run' and e['data']['ok']]
        test = next(c for c in notes['configurator']['checks'] if c['name'] == 'observed_device_test')
        self.assertEqual((test['passed'], test.get('evidence'), test.get('executions'), test['other_model_runs']), (False, None, None, variant))
        self.assertIn('assigned model', test['reason'])
        self.assertEqual(len(variant), 1)

    def test_an_assigned_model_that_fails_validation_goes_back_to_interpret_as_a_contract_defect(self):
        # The real tools refuse an invalid model before any transport opens, and report it as a tool error. The
        # configurator validates the assigned model itself, so the contract defect goes back to interpret whether or
        # not the worker ran the model.
        from unittest.mock import patch
        from generative_driver.configurator import digest
        invalid = thermometer_model(steps=False)
        def run(call, model, workspace):
            for name in ('interface_execute', 'probe_run'):
                refused = call(name, model_dir=model, operation='read_temperature')
                self.assertEqual((refused['_exit'], refused['error']), (1, "ValueError: invalid model: "
                                 "[{'where': 'operations.read_temperature', 'what': 'requires 1–128 steps'}]"))
        for plan, work in (('the worker runs the model', run), ('the worker runs nothing', lambda call, model, workspace: None)):
            opened = []
            with self.subTest(plan=plan), tempfile.TemporaryDirectory() as directory, \
                    patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
                result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None, model=invalid,
                                                                 real=('interface_execute', 'probe_run'))
                self.assertEqual(result['status'], 'completed', result)
                # The configurator supplied the device connection, and no transport opened on it.
                self.assertEqual([(name, arguments['binding']) for name, arguments in calls[:2]],
                                 [('interface_execute', self.BINDING), ('probe_run', self.BINDING)] if work is run else [])
                self.assertEqual(opened, [])
                assigned = next(e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'ground')
                path, sha = next((p, s) for p, s in assigned['inputs'].items() if (Path(p) / 'model.json').is_file())
                self.assertEqual(sha, digest(path))
                ground = [h for h in result['accepted_handoffs'] if h['stage'] == 'ground']
                notes = ground[0]['notes']['inline']
                self.assertEqual((ground[0]['route'], notes['outcome']),
                                 ('interpret', {'kind': 'revise', 'reason': 'Run evidence contradicts the model'}))
                # Only the configurator's validation is in the diff: the tool refusals reached no device.
                self.assertEqual(notes['configurator']['device_diff'], [{
                    'source': 'model_contract', 'model': {'name': Path(path).name, 'sha256': sha},
                    'defects': [{'where': 'operations.read_temperature', 'what': 'requires 1–128 steps'}]}])
                contract = next(c for c in notes['configurator']['checks'] if c['name'] == 'executable_model_contract')
                self.assertFalse(contract['passed'])
                # Validation never touched the assigned input, which the configurator re-hashes after the check.
                self.assertEqual(digest(path), sha)
                # The scripted interpreter writes the same model each time, so the budget runs out and the run continues.
                self.assertEqual(result['progress']['repairs'], 2)
                self.assertEqual(ground[-1]['notes']['inline']['outcome']['reason'], 'Repair budget exhausted; continuing with best revision')

    def test_an_effectful_dispatch_of_an_invalid_model_is_refused_before_it_could_leave_an_uncertain_effect(self):
        # With a write grant, the gateway refuses a write of a model that fails validation before it marks the dispatch
        # uncertain, as the runtime would refuse it before opening the device. Nothing is left to reconcile, so the
        # configurator's own validation sends the model back to interpret instead of a safety stop.
        from unittest.mock import patch
        invalid = thermometer_model(steps=False)
        invalid['operations']['set_rate'] = {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [
            {'op': 'exchange', 'tx': [{'text': 'R\n'}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64},
             'timeout_ms': 1000}]}
        refusals = []
        def work(call, model, workspace):
            for name in ('interface_execute', 'probe_run'):
                with self.assertRaisesRegex(ValueError, 'fails validation') as refused:
                    call(name, model_dir=model, operation='set_rate')
                refusals.append(str(refused.exception))
        opened = []
        with tempfile.TemporaryDirectory() as directory, \
                patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
            result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None, model=invalid,
                real=('interface_execute', 'probe_run'), effects=['read', 'write'], max_revisions=1)
        self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)
        # Nothing was dispatched: no tool ran, no transport opened, and each refusal is recorded for the stage's notes.
        self.assertEqual((calls, opened), ([], []))
        self.assertFalse(any(e['kind'] in ('tool.started', 'tool.finished') for e in events))
        refused = [e['data'] for e in events if e['kind'] == 'tool.refused' and e['data']['stage'] == 'ground']
        self.assertEqual([(e['name'], e['reason']) for e in refused[:2]], list(zip(('interface_execute', 'probe_run'), refusals[:2])))
        self.assertIn('requires 1–128 steps', refusals[0])
        ground = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')
        notes = ground['notes']['inline']
        self.assertEqual((ground['route'], notes['outcome']['kind']), ('interpret', 'revise'))
        self.assertEqual([entry['source'] for entry in notes['configurator']['device_diff']], ['model_contract'])
        self.assertEqual([r['name'] for r in notes['configurator']['tool_refusals']], ['interface_execute', 'probe_run'])
        assigned = next(e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'ground')
        model = notes['configurator']['device_diff'][0]['model']
        self.assertEqual(assigned['inputs'][next(p for p in assigned['inputs'] if Path(p).name == model['name'])], model['sha256'])

    @staticmethod
    def writing_thermometer():
        """The valid thermometer with a write operation, so a run granted write dispatches it as an effect."""
        model = thermometer_model()
        model['operations']['set_rate'] = {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [
            {'op': 'exchange', 'tx': [{'text': 'R\n'}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64},
             'timeout_ms': 1000}]}
        model['provenance'].append({'item': 'operations.set_rate', 'source': 'scripted fixture'})
        return model

    def test_an_effectful_dispatch_of_a_model_the_runtime_cannot_load_is_refused_at_the_gateway(self):
        # The assigned model.json repeats a key. Plain JSON parsing keeps the last value and validation passes, but the
        # runtime's strict reader refuses those bytes before it opens the device. The gateway reads the model as the
        # runtime does, so the write is refused before it is marked uncertain, and the configurator's own validation
        # sends the model back to interpret instead of a safety stop.
        from unittest.mock import patch
        model = self.writing_thermometer()
        text = json.dumps(model).replace('"device": {', '"device": {"name": "twice", ', 1)
        refusals = []
        def work(call, assigned, workspace):
            for name, arguments in (('probe_run', {'n': 1}), ('interface_execute', {})):
                with self.assertRaisesRegex(ValueError, 'duplicate JSON key: name') as refused:
                    call(name, model_dir=assigned, operation='set_rate', **arguments)
                refusals.append(str(refused.exception))
        opened = []
        with tempfile.TemporaryDirectory() as directory, \
                patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
            result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None, model=model,
                extra={'model.json': text}, real=('interface_execute', 'probe_run'), effects=['read', 'write'], max_revisions=1)
        self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)
        self.assertEqual((calls, opened), ([], []))
        self.assertFalse(any(e['kind'] in ('tool.started', 'tool.finished') for e in events))
        ground = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')
        notes = ground['notes']['inline']
        self.assertEqual([(r['name'], r['reason']) for r in notes['configurator']['tool_refusals']],
                         list(zip(('probe_run', 'interface_execute'), refusals)))
        self.assertEqual((ground['route'], [entry['source'] for entry in notes['configurator']['device_diff']]),
                         ('interpret', ['model_contract']))

    def test_an_effectful_probe_run_the_tool_refuses_before_it_opens_the_device_leaves_no_uncertain_effect(self):
        # The gateway dispatches a write probe of a valid model, marking it uncertain; the real probe_run then refuses the
        # worker's sample count before any transport opens, and declares that. The configurator reconciles the effect,
        # so the run goes on instead of stopping for safety.
        from unittest.mock import patch
        seen = []
        def work(call, assigned, workspace):
            seen.append(call('probe_run', model_dir=assigned, operation='set_rate', n=101))
        opened = []
        with tempfile.TemporaryDirectory() as directory, \
                patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
            result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None,
                model=self.writing_thermometer(), real=('probe_run',), effects=['read', 'write'], max_revisions=1)
        self.assertEqual((seen[0]['_exit'], seen[0]['transport_open_attempted']), (1, False), seen)
        self.assertIn('sample count must be integer 1..100', seen[0]['error'])
        self.assertEqual(([name for name, _ in calls], opened), (['probe_run'], []))
        self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)
        [finished] = [e['data'] for e in events if e['kind'] == 'tool.finished']
        self.assertEqual((finished['effectful'], finished['refused_before_open'], finished['effects_reconciled']),
                         (True, True, True), finished)

    # The one refusal of a call that would change the device while an effect is uncertain.
    UNCERTAIN = ('Outstanding effect is uncertain; nothing that would change the device is sent until the operator '
                 'resolves it')
    THERMOMETER = {b'ID': b'THERMO-1\n', b'T': b'21.5\n', b'R': b'OK\n'}

    def test_01_B11_while_a_run_is_uncertain_the_gateway_refuses_a_write_before_it_starts_and_reads_go_on(self):
        ran, seen = [], []
        def work(call, assigned, workspace):
            def attempt(name, operation, **arguments):
                try:
                    return call(name, model_dir=assigned, operation=operation, **arguments)
                except ValueError as refusal:
                    return str(refusal)
            ran.extend([attempt('interface_execute', 'set_rate'), attempt('interface_execute', 'set_rate'),
                        attempt('probe_run', 'set_rate', n=1), attempt('interface_execute', 'read_temperature')])
        # The device takes the write and never answers it: the command went out and its execution did not succeed.
        silent = {line: reply for line, reply in self.THERMOMETER.items() if line != b'R'}
        with tempfile.TemporaryDirectory() as directory, line_device(silent, seen) as binding:
            result, events, _, _ = self.run_device_stage(Path(directory) / 'home', work, None, stage='probe',
                model=self.writing_thermometer(), real=('interface_execute', 'probe_run'), effects=['read', 'write'],
                binding=binding)
        lost, again, probed, read = ran
        self.assertEqual((lost['ok'], lost['error']['code']), (False, 'timeout'), lost)
        self.assertEqual((again, probed), (self.UNCERTAIN, self.UNCERTAIN))
        self.assertEqual((read['ok'], read['outputs']), (True, {'temperature_c': 21.5}), read)
        # Neither refused write reached the device, and neither is recorded as started.
        self.assertEqual(seen, [b'ID', b'R', b'ID', b'T'])
        started = [e['data'] for e in events if e['kind'] == 'tool.started']
        self.assertEqual([(e['name'], e['effectful']) for e in started],
                         [('interface_execute', True), ('interface_execute', False)])
        refused = [e['data'] for e in events if e['kind'] == 'tool.refused']
        self.assertEqual([(e['name'], e['reason']) for e in refused],
                         [('interface_execute', self.UNCERTAIN), ('probe_run', self.UNCERTAIN)])
        self.assertEqual((result['status'], result['outcome_category'], result['uncertain_effect']),
                         ('blocked', 'safety', True), result)

    def test_01_B7_a_gateway_write_stays_uncertain_only_when_its_command_may_have_gone_out_and_did_not_succeed(self):
        # The real tools run the model's write on a scripted line device. `sent` is what the device then received:
        # only a write whose own step went out and did not succeed stays uncertain.
        device = self.THERMOMETER
        cases = (('the write succeeded', device, [b'ID', b'R'], True, False),
                 ('the identity read failed', {**device, b'ID': b'OTHER\n'}, [b'ID'], False, False),
                 ('nothing listens at the binding', None, [], False, False),
                 ('the device never answered the write', {b'ID': device[b'ID']}, [b'ID', b'R'], False, True))
        for case, replies, sent, succeeded, uncertain in cases:
            for tool, more in (('interface_execute', {}), ('probe_run', {'n': 1})):
                seen = []
                work = lambda call, assigned, workspace: call(tool, model_dir=assigned, operation='set_rate', **more)
                with self.subTest(case=case, tool=tool), tempfile.TemporaryDirectory() as directory, \
                        line_device(replies or {}, seen) as binding:
                    # With no repair budget the probe worker runs once, whatever its device difference.
                    result, events, _, _ = self.run_device_stage(Path(directory) / 'home', work, None, stage='probe',
                        model=self.writing_thermometer(), real=('interface_execute', 'probe_run'), max_revisions=0,
                        effects=['read', 'write'], binding=binding if replies else {'host': '127.0.0.1', 'port': 1})
                    self.assertEqual(seen, sent)
                    [finished] = [e['data'] for e in events if e['kind'] == 'tool.finished']
                    self.assertEqual((finished['effectful'], finished['ok'], finished['effects_reconciled']),
                                     (True, succeeded, not uncertain), finished)
                    # The device was opened, or the open was tried: none of these is a refusal before the open.
                    self.assertIs(finished['refused_before_open'], False)
                    self.assertEqual((result['uncertain_effect'], result['outcome_category'] == 'safety'),
                                     (uncertain, uncertain), result)

    def test_01_B8_a_gateway_write_whose_result_holds_no_execution_record_is_read_as_a_whole(self):
        # What the toolkit returns when a tool ends before it wrote a record: a refusal or a host fault it declares
        # before its first device call, and an error it does not declare, which may have come after the device was
        # contacted.
        stopped = {'ok': False, 'transport_open_attempted': False}
        rows = (('a refusal before the first device call',
                 {**stopped, 'error': 'ValueError: model_dir holds no model.json', '_exit': 1}, True, False),
                ('a host fault before the first device call',
                 {**stopped, 'fault': 'host', 'error': 'PermissionError: copy locked', '_exit': 4}, False, False),
                ('an error the tool did not declare', {'ok': False, 'error': 'OSError: disk full', '_exit': 1},
                 False, True))
        for case, returned, refused, uncertain in rows:
            work = lambda call, assigned, workspace: call('interface_execute', model_dir=assigned, operation='set_rate')
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                result, events, _, _ = self.run_device_stage(Path(directory) / 'home', work,
                    lambda name, arguments: returned, stage='probe', model=self.writing_thermometer(),
                    effects=['read', 'write'], max_revisions=0)
                [finished] = [e['data'] for e in events if e['kind'] == 'tool.finished']
                self.assertEqual((finished['effectful'], finished['ok'], finished['refused_before_open'],
                                  finished['effects_reconciled']), (True, False, refused, not uncertain), finished)
                self.assertEqual((result['uncertain_effect'], result['outcome_category'] == 'safety'),
                                 (uncertain, uncertain), result)

    def test_01_B5_the_gateway_records_a_dispatch_as_started_and_as_finished_with_the_fields_of_the_journal(self):
        def work(call, assigned, workspace):
            call('interface_execute', model_dir=assigned, operation='set_rate')
            call('probe_run', model_dir=assigned, operation='read_temperature', n=1)
        with tempfile.TemporaryDirectory() as directory, line_device(self.THERMOMETER) as binding:
            result, events, _, _ = self.run_device_stage(Path(directory) / 'home', work, None, stage='probe',
                model=self.writing_thermometer(), real=('interface_execute', 'probe_run'), effects=['read', 'write'],
                binding=binding)
            self.assertEqual(result['status'], 'completed', result)
            [assigned] = [e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'probe']
            model = next(sha for path, sha in assigned['inputs'].items() if (Path(path) / 'model.json').is_file())
        started = [e for e in events if e['kind'] == 'tool.started']
        finished = [e['data'] for e in events if e['kind'] == 'tool.finished']
        self.assertEqual((len(started), len(finished)), (2, 2))
        for start, end, name, marked in zip(started, finished, ('interface_execute', 'probe_run'), (True, False)):
            with self.subTest(name=name):
                # The record's fields, and the gateway's own facts: that it supplied the connection, and what ran.
                recorded = {'stage': 'probe', 'assignment_id': assigned['id'], 'name': name, 'actor': 'worker',
                            'effectful': marked, 'connected': True, 'argument_sha256': {'model_dir': model}}
                self.assertEqual(start['data'], recorded)
                result_of, artifacts = end.pop('result'), end.pop('artifacts')
                self.assertEqual(end, {**recorded, 'ok': True, 'dispatch_id': str(start['id']),
                                       'effects_reconciled': marked, 'refused_before_open': False})
                self.assertIs(result_of['ok'], True)
                # A probe's record in the stage's workspace is an artifact of the call, by its hash.
                self.assertEqual(list(artifacts), [result_of['probe']] if name == 'probe_run' else [])

    def test_01_B7_a_device_call_of_a_model_format_the_runtime_does_not_know_is_refused_at_the_gateway(self):
        # A model is data the installed runtime executes. The gateway reads a model's schema before it classifies a
        # device call by its effects, so a model of a format the runtime does not know is refused there, whatever the
        # run grants: the tool never starts, no transport opens and no effect is marked uncertain.
        from unittest.mock import patch
        from test_package import REGISTER_MODEL, marking
        register = json.loads(json.dumps(REGISTER_MODEL))
        register['operations']['init'] = [{'op': 'reg_write', 'reg': '0xf4', 'bytes': ['0x27']}]
        renamed = dict(self.writing_thermometer(), schema='interface-model/99')
        unnamed = {key: value for key, value in self.writing_thermometer().items() if key != 'schema'}
        formats = (('register', register, "'interface-model/1'", 'measure'),
                   ('renamed', renamed, "'interface-model/99'", 'set_rate'), ('unnamed', unnamed, 'None', 'read_temperature'))
        expected = [(name, schema) for _, _, schema, _ in formats for name in ('probe_run', 'interface_execute')]
        for grants in (None, ['read', 'write']):
            refusals, returned, opened = [], [], []
            with self.subTest(grants=grants), tempfile.TemporaryDirectory() as directory, \
                    patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
                marker = Path(directory) / 'convert-ran'
                def work(call, assigned, workspace):
                    for folder, model, schema, operation in formats:
                        variant = workspace / folder; variant.mkdir()
                        (variant / 'model.json').write_text(json.dumps(model))
                        (variant / 'convert.py').write_text(marking(marker))
                        for name in ('probe_run', 'interface_execute'):
                            try:
                                returned.append((name, schema, call(name, model_dir=str(variant), operation=operation)))
                            except ValueError as refusal:
                                refusals.append((name, schema, str(refusal)))
                result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None,
                    real=('probe_run', 'interface_execute'), effects=grants)
                # Every call was refused, with a reason that names the schema found.
                self.assertEqual(returned, [])
                self.assertEqual([(name, schema) for name, schema, _ in refusals], expected)
                for name, schema, reason in refusals:
                    self.assertIn('model format is not one the runtime knows', reason)
                    self.assertTrue(reason.endswith('nothing was dispatched: ' + schema), reason)
                # No tool started, the device was not contacted and no file of a model ran.
                self.assertEqual((calls, opened), ([], []))
                self.assertFalse(any(e['kind'] in ('tool.started', 'tool.finished') for e in events))
                self.assertFalse(marker.exists(), 'a file of a refused model was imported or run')
                refused = [e['data'] for e in events if e['kind'] == 'tool.refused' and e['data']['stage'] == 'ground']
                self.assertEqual([(e['name'], e['reason']) for e in refused],
                                 [(name, reason) for name, _, reason in refusals])
                self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)

    def test_02_B2_the_gateway_refuses_a_device_call_of_a_model_with_an_earlier_tag(self):
        # The runtime reads one driver format. A model that names an earlier tag is refused at the gateway like any
        # format the runtime does not know: the tool never starts, no transport opens and no effect is left uncertain.
        from unittest.mock import patch
        tags = ('interface-model/3', 'interface-model/4', 'interface-model/5', 'interface-model/6')
        expected = [(name, tag) for tag in tags for name in ('probe_run', 'interface_execute')]
        for grants in (None, ['read', 'write']):
            refusals, returned, opened = [], [], []
            with self.subTest(grants=grants), tempfile.TemporaryDirectory() as directory, \
                    patch('interface_runtime.evidence.open_transport', lambda channel, binding: opened.append(binding)):
                def work(call, assigned, workspace):
                    for tag in tags:
                        variant = workspace / ('earlier-' + tag[-1]); variant.mkdir()
                        (variant / 'model.json').write_text(json.dumps(dict(self.writing_thermometer(), schema=tag)))
                        for name in ('probe_run', 'interface_execute'):
                            try:
                                returned.append((name, tag, call(name, model_dir=str(variant), operation='set_rate')))
                            except ValueError as refusal:
                                refusals.append((name, tag, str(refusal)))
                result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None,
                    real=('probe_run', 'interface_execute'), effects=grants)
                self.assertEqual(returned, [])
                self.assertEqual([(name, tag) for name, tag, _ in refusals], expected)
                for name, tag, reason in refusals:
                    self.assertIn('interface-model/7', reason)
                    self.assertTrue(reason.endswith('nothing was dispatched: ' + repr(tag)), reason)
                self.assertEqual((calls, opened), ([], []))
                self.assertFalse(any(e['kind'] in ('tool.started', 'tool.finished') for e in events))
                self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)

    def test_a_model_the_worker_rewrites_during_a_call_runs_as_the_gateway_read_it(self):
        # The gateway reads a model to classify and hash a dispatch, and the tool reads it again. A worker that rewrites
        # its model in between, here turning a heater query labelled read into a heater command labelled write, which
        # this run grants, would run a write the gateway never marked uncertain, under a digest of other bytes. The
        # stand-in below rewrites the worker's model after the gateway's read, then runs the real tool.
        from generative_driver import toolkit
        from generative_driver.configurator import digest
        real_call_tool = toolkit.call_tool
        def variant(effect, command):
            model = thermometer_model()
            model['operations']['heater'] = {'effect': effect, 'parameters': {}, 'outputs': {}, 'steps': [
                {'op': 'exchange', 'tx': [{'text': command}], 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64},
                 'timeout_ms': 1000}]}
            model['provenance'].append({'item': 'operations.heater', 'source': 'scripted fixture'})
            return json.dumps(model)
        seen, ran, read = [], [], {}
        def work(call, assigned, workspace):
            folder = workspace / 'variant'; folder.mkdir()
            (folder / 'model.json').write_text(variant('read', 'H?\n'))
            read.update(folder=folder, sha256=digest(folder))
            ran.append(call('interface_execute', model_dir=str(folder), operation='heater'))
        def respond(name, arguments):
            (read['folder'] / 'model.json').write_text(variant('write', 'H1\n'))
            return real_call_tool(name, arguments)
        with tempfile.TemporaryDirectory() as directory, \
                line_device({b'ID': b'THERMO-1\n', b'H?': b'OFF\n', b'H1': b'OK\n'}, seen) as binding:
            result, events, _, _ = self.run_device_stage(Path(directory) / 'home', work, respond, stage='probe',
                                                         effects=['read', 'write'], binding=binding)
            # The gateway's copy lasts only as long as its call.
            self.assertEqual(list((Path(directory) / 'home' / 'runs').glob('*/snapshots/*')), [])
        self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)
        # The device received the query the gateway classified as a read, never the rewritten command.
        self.assertEqual(seen, [b'ID', b'H?'])
        self.assertEqual((ran[0]['ok'], ran[0]['outputs']), (True, {}), ran)
        [started] = [e['data'] for e in events if e['kind'] == 'tool.started' and e['data']['name'] == 'interface_execute']
        self.assertEqual((started['effectful'], started['argument_sha256']['model_dir']), (False, read['sha256']))

    def test_probe_hands_on_every_record_the_device_answered_and_counts_only_the_assigned_model_as_probed(self):
        from generative_driver.configurator import digest
        ok = {'ok': True, 'evidence_kind': 'live', 'results': [{'ok': True, 'outputs': {}}], '_exit': 0}
        fault = {'ok': False, 'fault': 'model', 'error_code': 'framing', 'error_message': 'short reply',
                 'evidence_kind': 'live', 'results': [self.FAILED], '_exit': 1}
        rounds = []
        def work(call, model, workspace):
            variant = workspace / 'variant'; variant.mkdir()
            (variant / 'model.json').write_text(json.dumps(thermometer_model('Worker variant')))
            runs = [call('probe_run', model_dir=str(variant), operation='identify')]
            if not rounds:
                # The device answers the assigned model's read with bytes it did not predict, then its identity.
                runs += [call('probe_run', model_dir=model, operation='read_temperature'),
                         call('probe_run', model_dir=model, operation='identify')]
            rounds.append([str(Path(run['probe']).resolve()) for run in runs])
        with tempfile.TemporaryDirectory() as directory:
            result, events, _, _ = self.run_device_stage(Path(directory) / 'home', work,
                lambda name, arguments: fault if arguments['operation'] == 'read_temperature' else ok, stage='probe')
            self.assertEqual(result['status'], 'completed', result)
            probes = [h for h in result['accepted_handoffs'] if h['stage'] == 'probe']
            self.assertEqual([(h['revision'], h['route']) for h in probes], [(0, 'interpret'), (1, None)])
            finished = [e['data'] for e in events if e['kind'] == 'tool.finished']
            (variant, failed, identified), (variant_again,) = rounds
            # Every record of the device answering on the owned connection is handed on, whatever model ran;
            # each record names its own model.
            self.assertEqual(sorted(a['sha256'] for a in probes[0]['artifacts']), sorted(digest(p) for p in (variant, failed, identified)))
            self.assertEqual([a['sha256'] for a in probes[1]['artifacts']], [digest(variant_again)])
            notes = probes[0]['notes']['inline']['configurator']
            # The diff names the failed run's record, which the stage handed on.
            self.assertEqual([(entry['source'], entry['probe']) for entry in notes['device_diff']],
                             [('probe_run', {'name': Path(failed).name, 'sha256': digest(failed)})])
            # Only a probe of the assigned model counts as probing it; runs of other models stay visible.
            run_of = {str(Path(e['result']['probe']).resolve()): e['dispatch_id'] for e in finished}
            probed = next(c for c in notes['checks'] if c['name'] == 'observed_probe_execution')
            self.assertEqual((probed['passed'], probed['evidence'], probed['other_model_runs']), (True, [identified], [run_of[variant]]))
            again = next(c for c in probes[1]['notes']['inline']['configurator']['checks'] if c['name'] == 'observed_probe_execution')
            self.assertEqual((again['passed'], again.get('evidence'), again['other_model_runs']), (False, None, [run_of[variant_again]]))
            self.assertEqual(again['reason'], 'No observed successful probe of the assigned model through the owned connection')

    def test_a_probe_that_rewrites_its_record_supersedes_it_and_only_a_changed_record_stops(self):
        from generative_driver.configurator import digest
        def respond(name, arguments):
            # A probe that writes the same record path on every run.
            path = Path(arguments['run_dir']) / 'probe' / 'probe.json'; path.parent.mkdir(parents=True, exist_ok=True)
            reading = json.loads(path.read_text())['reading'] + 1 if path.exists() else 0
            path.write_text(json.dumps({'ok': True, 'reading': reading}))
            return {'ok': True, 'evidence_kind': 'live', 'results': [{'ok': True, 'outputs': {'reading': reading}}],
                    'probe': str(path), '_exit': 0}
        records = []
        def plan(alter):
            def work(call, model, workspace):
                records.extend(call('probe_run', model_dir=model, operation='read_temperature') for _ in range(2))
                if alter:
                    Path(records[-1]['probe']).write_text('{"rewritten": "by the worker"}')
            return work
        for name, alter in (('two runs', False), ('record changed after the tool returned', True)):
            records.clear()
            with self.subTest(plan=name), tempfile.TemporaryDirectory() as directory:
                result, events, _, _ = self.run_device_stage(Path(directory) / 'home', plan(alter), respond)
                if alter:
                    self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('blocked', 'integrity', 'ground'), result)
                    self.assertIn('Observed probe artifact changed after the tool returned', result['reason'])
                    continue
                self.assertEqual(result['status'], 'completed', result)
                first, second = [e['data'] for e in events if e['kind'] == 'tool.finished']
                ground = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')
                # The later run's record replaced the earlier one; the stage hands on what the path holds now.
                self.assertEqual([a['sha256'] for a in ground['artifacts']], [digest(records[-1]['probe'])])
                self.assertEqual(second['artifacts'], {str(Path(records[-1]['probe']).resolve()): digest(records[-1]['probe'])})
                test = next(c for c in ground['notes']['inline']['configurator']['checks'] if c['name'] == 'observed_device_test')
                self.assertEqual((test['passed'], test['evidence'], test['superseded']),
                                 (True, [str(Path(records[-1]['probe']).resolve())], [first['dispatch_id']]))

    def test_ground_compares_a_handed_on_record_only_when_the_assigned_model_wrote_it(self):
        """Probe hands on every record the device answered, each bound in its notes to the model that wrote it. Ground
        compares a handed-on record with the assigned model only when the assigned model wrote it: a record of the
        probe worker's own variant says what the device did for that variant, so it never drives a revise. probe_run
        and probe_diff are stand-ins: the device answers either model, and the comparison finds a difference."""
        from generative_driver.configurator import digest
        answered = {'ok': True, 'evidence_kind': 'live', 'results': [{'ok': True, 'outputs': {'temperature_c': 21.5}}], '_exit': 0}
        differs = {'ok': False, 'diagnostics': [], 'source_evidence_kind': 'live', '_exit': 1, 'defects': [
            {'where': 'operations.read_temperature', 'what': 'device answered 21.5; the model predicted 20', 'fault': 'model'}]}
        variants = []
        def probe(own):
            def work(call, model, workspace):
                if own:
                    variant = workspace / 'variant'; variant.mkdir()
                    (variant / 'model.json').write_text(json.dumps(thermometer_model('Worker variant')))
                    variants.append(digest(variant))
                    model = str(variant)
                call('probe_run', model_dir=model, operation='read_temperature')
            return work
        diffs = []
        def ground(call, model, workspace):
            for record in sorted(str(p) for p in (workspace / 'inputs').iterdir() if p.suffix == '.json'):
                diffs.append(call('probe_diff', model_dir=model, probe=record))
        for name, own in (("the probe worker's own variant wrote the record", True), ('the assigned model wrote it', False)):
            variants.clear(); diffs.clear()
            with self.subTest(plan=name), tempfile.TemporaryDirectory() as directory:
                result, events, _, _ = self.run_device_stage(Path(directory) / 'home', {'probe': probe(own), 'ground': ground},
                    lambda name, arguments: answered if name == 'probe_run' else differs, stage=('probe', 'ground'),
                    max_revisions=1)
                self.assertEqual(result['status'], 'completed', result)
                probe_handoff, ground_handoff = (next(h for h in result['accepted_handoffs'] if h['stage'] == stage and h['revision'] == 0)
                                                 for stage in ('probe', 'ground'))
                assigned = next(e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'probe')
                model_sha = next(sha for path, sha in assigned['inputs'].items() if (Path(path) / 'model.json').is_file())
                # The probe notes bind each handed-on record to the digest of the model whose run wrote it.
                [artifact] = probe_handoff['artifacts']
                [record] = probe_handoff['notes']['inline']['configurator']['records']
                self.assertEqual((record['sha256'], record['model_sha256']),
                                 (artifact['sha256'], variants[0] if own else model_sha))
                # The comparison finds a difference either way.
                found = diffs[0]
                self.assertTrue(found['defects'], found)
                notes = ground_handoff['notes']['inline']
                if own:
                    self.assertEqual((ground_handoff['route'], notes['outcome']['kind'], notes['configurator']['device_diff']),
                                     (None, 'advance', None))
                    self.assertEqual(result['progress']['repairs'], 0)
                else:
                    self.assertEqual((ground_handoff['route'], notes['outcome']['kind']), ('interpret', 'revise'))
                    [entry] = notes['configurator']['device_diff']
                    self.assertEqual((entry['source'], entry['probe']['sha256'], entry['defects']),
                                     ('probe_diff', artifact['sha256'], found['defects']))

    DEFECTS = [{'where': 'operations.read_temperature.steps.0', 'what': 'device answered 2a; the model predicted 15',
                'code': 'framing', 'fault': 'model'}]

    @contextlib.contextmanager
    def repaired(self, on_repair=None):
        """A generic run whose rev-0 probe sees the device contradict the assigned model, so interpret is prepared
        again, for real, as a repair. `on_repair(workspace)` acts as the rev-1 interpret worker. Yields the
        controller, the run, its stopped result, the sealed interpret workspaces and the probe records, and
        removes the sealed workspaces afterwards. No model or device runs."""
        import shutil
        from unittest.mock import patch
        from generative_driver import toolkit
        from generative_driver.configurator import Controller
        defects = self.DEFECTS
        real_call_tool = toolkit.call_tool
        def fake_call_tool(name, arguments):
            if name in ('interpret_run', 'model_validate'):
                return real_call_tool(name, arguments)
            if name == 'probe_run':
                # The device answered the assigned model's read with bytes it did not predict.
                self.assertEqual(arguments['binding'], self.BINDING)
                probe = Path(arguments['run_dir']) / 'probe' / 'interface-1.json'
                probe.parent.mkdir(parents=True, exist_ok=True)
                probe.write_text(json.dumps({'schema': 'interface-probe/1', 'ok': False, 'results': [self.FAILED]}))
                return {'ok': False, 'probe': str(probe), 'evidence_kind': 'live', 'results': [self.FAILED], 'error_code': 'framing',
                        'error_message': 'short reply', 'fault': 'model', '_exit': 1}
            self.assertEqual(name, 'probe_diff')
            return {'ok': False, 'defects': defects, 'diagnostics': [], 'source_evidence_kind': 'live', '_exit': 1}
        sealed, probes = [], []
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'home'
            controller = Controller(home)
            real_check = controller._check
            def check(spec, stage, run_dir, workspace, report, accepted, assignment):
                if stage == 'acquire':
                    image = workspace / 'image.bin'; image.write_bytes(b'\xe9 scripted image')
                    return {'ok': True, 'artifacts': [str(image)], 'checks': []}
                if stage == 'interpret':
                    model = workspace / 'model'; model.mkdir()
                    (model / 'model.json').write_text(json.dumps(thermometer_model('Scripted thermometer, revision ' + str(assignment['revision']))))
                    return {'ok': True, 'artifacts': [str(model)], 'checks': []}
                if stage == 'probe':
                    return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
                return {'ok': True, 'artifacts': [], 'checks': []}
            def prepare(original, spec, stage, run_dir, workspace, accepted):
                return original(spec, stage, run_dir, workspace, accepted) if stage in ('interpret', 'probe', 'reuse') else {'inputs': [], 'allowed_tools': []}
            def work(request):
                if request.stage == 'interpret':
                    sealed.append(Path(request.workspace))
                    if len(sealed) == 2 and on_repair:
                        on_repair(sealed[1])
                elif request.stage == 'probe' and len(sealed) == 1:
                    model = next(p for p in task_of(request.prompt)['inputs'] if (Path(p) / 'model.json').is_file())
                    call = lambda name, **arguments: controller.call('tool', {**gateway_identity(request), 'name': name, 'arguments': arguments})
                    probes.append(Path(call('probe_run', model_dir=model, operation='read_temperature')['probe']))
                    call('probe_diff', model_dir=model, probe_json=str(probes[0]))
            try:
                with scripted_stages(controller, check=check, prepare=prepare, work=work), \
                     patch('generative_driver.toolkit.call_tool', fake_call_tool):
                    run = controller.call('start', {'goal': 'Scripted device repair contract; no model or device', 'binding': self.BINDING,
                                                    'executor_config': {'command': ['never-launched']}})
                    result = wait_stopped(controller, run)
                yield controller, run, result, sealed, probes, home
            finally:
                controller.close()
                for workspace in sealed:
                    shutil.rmtree(workspace.parent, ignore_errors=True)

    def test_observed_device_difference_routes_to_interpret_with_the_diff_in_the_next_sealed_workspace(self):
        from generative_driver.configurator import digest
        with self.repaired() as (controller, run, result, sealed, probes, home):
            self.assertEqual(result['status'], 'completed', result)
            self.assertEqual([(h['stage'], h['revision']) for h in result['accepted_handoffs']],
                             [('acquire', 0), ('interpret', 0), ('probe', 0), ('interpret', 1), ('probe', 1),
                              ('ground', 1), ('emit', 1), ('reuse', 1)])
            diff = result['progress']['feedback']
            record = {'name': 'interface-1.json', 'sha256': digest(probes[0])}
            self.assertEqual([(entry['source'], entry['probe']) for entry in diff], [('probe_run', record), ('probe_diff', record)])
            self.assertEqual((diff[0]['code'], diff[0]['failed'], diff[1]['defects']), ('framing', [self.FAILED], self.DEFECTS))
            # The diff names records by content: no host path or run name reaches the sealed workspace.
            self.assertFalse(any(str(home) in text or run['run_id'] in text for text in strings(diff)))
            self.assertEqual((result['progress']['repairs'], result['progress']['revision']), (1, 1))
            probe = result['accepted_handoffs'][2]
            self.assertEqual((probe['route'], probe['notes']['inline']['outcome']['kind']), ('interpret', 'revise'))
            # The record the diff names is handed on with the probe, although its run failed.
            self.assertEqual([a['sha256'] for a in probe['artifacts']], [record['sha256']])
            self.assertEqual(probe['notes']['inline']['configurator']['device_diff'], diff)
            first, second = sealed
            for name in ('defects.json', 'previous-model', 'REPAIR_CONTEXT.json'):
                self.assertFalse((first / name).exists(), name)
            # The repair workspace stays sealed and holds the device diff, the candidate it contradicts and
            # what each of them is.
            self.assertEqual(json.loads((second / 'defects.json').read_text(encoding='utf-8')), diff)
            previous = Path(result['accepted_handoffs'][1]['artifacts'][0]['path'])
            self.assertEqual(digest(second / 'previous-model'), digest(previous))
            context = json.loads((second / 'REPAIR_CONTEXT.json').read_text(encoding='utf-8'))
            self.assertEqual((context['previous_candidate'], context['feedback']), ('previous-model/', 'defects.json'))
            self.assertIn('model_contract', context['feedback_meaning'])
            # A model_contract defect is the configurator's finding about the model, not something the device did.
            self.assertIn("the configurator's own validation", context['feedback_meaning'])
            self.assertIn('for an interface model, the runtime refuses such a model before it opens the device',
                          context['feedback_meaning'])
            for words in ('Start from previous-model/', 'model.json', 'replies.json', 'workspace root', 'NOTES.md'):
                self.assertIn(words, context['instructions'])
            assigned = [e['data'] for e in controller.call('events', run)['events']
                        if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'interpret']
            self.assertEqual(sorted(Path(p).relative_to(second).as_posix() for p in assigned[1]['inputs']
                                    if Path(p).relative_to(second).parts[0] != '_toolkit'),
                             ['ANALYSIS_TOOLS.json', 'BRIEF.md', 'ExportDecomp.java',
                              'INTERFACE_MODEL.md', 'REPAIR_CONTEXT.json', 'SeedCortexM.java', 'TASKS.json',
                              'defects.json',
                              'ghidra_run.py', 'image.bin', 'image_map_check.py', 'previous-model/model.json',
                              'qualify.py', 'validate.py'])
            self.assertEqual(assigned[1]['collection']['expected_seal_sha256'], digest(Path(assigned[1]['collection']['run_dir']) / 'interpret/attempt1.seal.json'))

    def test_02_B8_the_repair_workspace_of_a_generic_run_holds_the_one_model_document_and_names_no_contract(self):
        from worker_texts import earlier_names, model_document, texts_of
        with self.repaired() as (controller, run, result, sealed, probes, home):
            repair = sealed[1]
            self.assertEqual((repair / 'INTERFACE_MODEL.md').read_bytes(), model_document().read_bytes())
            for name in ('CONTRACT.md', 'contract.py', 'previous-model/contract.json'):
                self.assertFalse((repair / name).exists(), name)
            # REPAIR_CONTEXT.json and TASKS.json among them: no text names an earlier format or a contract to write.
            self.assertEqual(earlier_names(texts_of(repair)), [])

    def test_repair_context_names_every_sealed_repair_input_and_editing_one_stops_for_integrity(self):
        # The repair agent is told which inputs are sealed and what changing one does; the configurator holds it to that.
        for name in ('defects.json', 'REPAIR_CONTEXT.json', 'previous-model/model.json',
                     'ghidra_run.py', 'ExportDecomp.java', 'SeedCortexM.java', 'ANALYSIS_TOOLS.json'):
            with self.subTest(edited=name):
                contexts = []
                def edit(workspace):
                    contexts.append(json.loads((workspace / 'REPAIR_CONTEXT.json').read_text(encoding='utf-8')))
                    (workspace / name).write_text('{"addressed": true}', encoding='utf-8')
                with self.repaired(edit) as (controller, run, result, sealed, probes, home):
                    context, = contexts
                    self.assertEqual(context['sealed_inputs'], ['image.bin', 'ghidra_run.py', 'ExportDecomp.java',
                        'SeedCortexM.java', 'ANALYSIS_TOOLS.json', 'TASKS.json',
                        'previous-model/', 'defects.json', 'REPAIR_CONTEXT.json'])
                    for words in ('read them and leave them unchanged', 'changing any sealed input stops the run'):
                        self.assertIn(words, context['instructions'])
                    self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('blocked', 'integrity', 'interpret'), result)
                    self.assertIn('an assigned input was altered', result['reason'])
                    self.assertTrue(result['reason'].endswith(str(sealed[1] / name)), result['reason'])
                    self.assertEqual([(h['stage'], h['revision']) for h in result['accepted_handoffs']],
                                     [('acquire', 0), ('interpret', 0), ('probe', 0)])

    def test_exhausted_repair_budget_advances_to_emit_with_the_best_revision_and_a_note(self):
        from generative_driver.configurator import Controller, digest
        diff = [{'source': 'probe_diff', 'dispatch_id': '1', 'defects': [{'where': 'operations.read', 'what': 'scaled by 10'}]}]
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'interpret':
                model = workspace / 'model'; model.mkdir()
                (model / 'model.json').write_text(json.dumps({'schema': 'interface-model/7', 'revision': assignment['revision']}))
                return {'ok': True, 'artifacts': [str(model)], 'checks': []}
            if stage == 'probe':
                return {**probed(workspace, assignment), 'device_diff': diff}
            return {'ok': True, 'artifacts': [], 'checks': []}
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            if stage == 'probe':
                return original(spec, stage, run_dir, workspace, accepted)
            return original(spec, stage, run_dir, workspace, accepted) if stage in ('ground', 'emit', 'reuse') else {'inputs': [], 'allowed_tools': []}
        prompts = []
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with scripted_stages(controller, check=check, prepare=prepare, prompts=prompts):
                    run = controller.call('start', {'goal': 'Scripted repair budget contract; no model or device', 'max_revisions': 1,
                                                    'executor_config': {'command': ['never-launched']}})
                    result = wait_stopped(controller, run)
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([(h['stage'], h['revision']) for h in result['accepted_handoffs']],
                                 [('acquire', 0), ('interpret', 0), ('probe', 0), ('interpret', 1), ('probe', 1),
                                  ('ground', 1), ('emit', 1), ('reuse', 1)])
                self.assertEqual((result['progress']['repairs'], result['progress']['revision']), (1, 1))
                last = result['accepted_handoffs'][4]
                self.assertIsNone(last['route'])
                self.assertEqual(last['notes']['inline']['outcome'],
                                 {'kind': 'advance', 'reason': 'Repair budget exhausted; continuing with best revision'})
                self.assertEqual(last['notes']['inline']['configurator']['device_diff'], diff)
                model, records = (Path(result['accepted_handoffs'][i]['artifacts'][0]['path']) for i in (3, 4))
                assigned = {e['data']['stage']: e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'}
                self.assertEqual(sorted(assigned['emit']['inputs'].values()), sorted([digest(model), digest(records)]))
                emit = context_of(next(prompt for stage, prompt in prompts if stage == 'emit'))
                note = next(n for n in emit['prior_notes'] if n['stage'] == 'probe')
                self.assertEqual((note['revision'], note['notes']['outcome']['reason']),
                                 (1, 'Repair budget exhausted; continuing with best revision'))
            finally:
                controller.close()


def thermometer_package(root, name='package', operations=None, channel=None, probed=None):
    """The package the toolkit emits for the scripted thermometer, with replayed evidence of `probed` (defaulting to
    `read_temperature` when present, else `identify`), at `root`/`name`. Unless `operations` names others, the model
    has the identity operation and one read. Each operation it names sends its own name to the device unless it names
    its steps. `channel` gives the model a channel. Call it where `toolkit.call_tool` is the real one."""
    from generative_driver.toolkit import call_tool
    model = thermometer_model()
    rx = {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64}
    if operations is not None:
        known = model['operations']
        asks = lambda text: {'parameters': {}, 'outputs': {}, 'steps': [
            {'op': 'exchange', 'tx': [{'text': text + '\n'}], 'rx': rx, 'timeout_ms': 1000}]}
        model['operations'] = {operation: {**known.get(operation, asks(operation)), **given}
                               for operation, given in operations.items()}
        model['provenance'] = [{'item': 'operations.' + operation, 'source': 'scripted fixture'}
                               for operation in operations]
    if channel is not None:
        model['channel'] = channel
    if probed is None:
        probed = 'read_temperature' if 'read_temperature' in model['operations'] else 'identify'
    transactions = [{'tx_hex': b'ID\n'.hex(), 'rx_hex': b'THERMO-1\n'.hex(), 'rx': rx, 'timeout_ms': 1000}]
    if probed == 'read_temperature':
        transactions.append({'tx_hex': b'T\n'.hex(), 'rx_hex': b'21.5\n'.hex(), 'rx': rx, 'timeout_ms': 1000})
    elif probed != 'identify':
        transactions.append({'tx_hex': (probed + '\n').encode().hex(), 'rx_hex': b'OK\n'.hex(),
                             'rx': rx, 'timeout_ms': 1000})
    source = Path(root) / (name + '-source')
    (source / 'model').mkdir(parents=True)
    (source / 'model' / 'model.json').write_text(json.dumps(model))
    (source / 'replay.json').write_text(json.dumps({'schema': 'interface-replay/1', 'transactions': transactions}))
    grants = ['reset'] if model['channel'].get('resets_on_open') else []
    effect = model['operations'][probed].get('effect', 'actuate')
    if effect != 'read' and effect not in grants:
        grants.append(effect)
    probe = call_tool('probe_run', {'run_dir': str(source / 'run'), 'model_dir': str(source / 'model'), 'n': 1,
                                    'operation': probed, 'replay': str(source / 'replay.json'),
                                    'allow_effects': grants})
    assert probe.get('ok'), probe
    emitted = call_tool('emit_package', {'run_dir': str(source / 'run'), 'model_dir': str(source / 'model'),
                                         'probe': probe['probe']})
    assert emitted.get('ok'), emitted
    return Path(shutil.move(emitted['package_dir'], Path(root) / name))


def final_verdicts(controller, run):
    with controller._db() as db:
        return [(row['id'], json.loads(row['payload'])) for row in
                db.execute("SELECT id,payload FROM verdicts WHERE run_id=? AND stage='finalize'", (run['run_id'],))]


class FinalVerdictTests(unittest.TestCase):
    """Each run ends with one functional-equivalence verdict, recorded once after reuse. A generic run's verdict is
    self-evidence: the emitted package passed emit_check replay and every live operation of fresh reuse succeeded,
    one of them a generated tool call that ran a function of the frozen package
    (docs/features/package-format/03-package.md, Behaviour 03-B6)."""
    BINDING = {'host': '127.0.0.1', 'port': 9}
    # The generated tools of the scripted thermometer's package.
    IDENTIFY, READ = 'scripted_thermometer_identify', 'scripted_thermometer_read_temperature'

    # What the child process of a generated call gives, scripted in the runtime's own shape. None reaches a device.
    PASSED = {'schema': 'driver-result/1', 'operation': 'read_temperature', 'ok': True,
              'outputs': {'temperature_c': 21.5}, 'units': {'temperature_c': 'degC'}, 'identity_verified': True,
              'transcript': [], 'warnings': [], 'transport_open_attempted': True}
    # The runtime opened the device and the reply contradicted the model.
    MODEL_FAILURE = {'schema': 'driver-result/1', 'operation': 'read_temperature', 'ok': False, 'outputs': {},
                     'units': {}, 'identity_verified': True, 'transcript': [], 'warnings': [],
                     'transport_open_attempted': True,
                     'error': {'code': 'expect', 'message': 'reply differs from the model', 'fault': 'model'}}
    # The call stopped at a host fault after the transport opened (a timeout before identity).
    HOST_FAULT = {'schema': 'driver-result/1', 'operation': 'read_temperature', 'ok': False, 'outputs': {},
                  'units': {}, 'identity_verified': False, 'transcript': [], 'warnings': [],
                  'transport_open_attempted': True,
                  'error': {'code': 'timeout', 'message': 'the device did not answer', 'fault': 'host'}}
    # The runtime refused the fresh agent's parameters before it opened the transport.
    REFUSAL = {'schema': 'driver-result/1', 'operation': 'read_temperature', 'ok': False, 'outputs': {}, 'units': {},
               'identity_verified': False, 'transcript': [], 'warnings': [], 'transport_open_attempted': False,
               'error': {'code': 'validation_error', 'message': 'arguments must match parameter names exactly',
                         'fault': 'operator'}}
    # The child process of a call gave no result, as the gateway words it: a host fault that does not say whether the
    # device was opened.
    NO_RESULT = {'schema': 'driver-result/1', 'operation': 'read_temperature', 'ok': False, 'outputs': {},
                 'warnings': [],
                 'error': {'code': 'package_output', 'message': 'package printed no JSON result', 'fault': 'host'}}
    NEVER_RAN = 'fresh reuse never ran a tool of the frozen package'

    @classmethod
    def passed(cls, operation):
        """The result of `operation` that ran on the device and gave no output."""
        return {**cls.PASSED, 'operation': operation, 'outputs': {}, 'units': {}}

    @contextlib.contextmanager
    def finishing(self, controller, *, replay='passed', live=True, calls=None, attempts=None, operations=None,
                  channel=None, listen=None, probed=None):
        """Acquire to ground are scripted. Emit and reuse use the real preparation and checks with a scripted
        emit_package and emit_check: the emit worker builds a package with emit_package through the gateway, and the
        reuse worker calls the generated tool of the package's read once through the gateway. What the child process
        of a generated call gives is scripted (`DeviceAccess.invoke_package`), so no call reaches a device.

        `replay` is the emit_check outcome ('passed', 'failed', or 'supplied' for a passed replay of exchanges the
        worker supplied). `listen`, when given, has the acquire worker
        listen to the bound serial device through the gateway, which hears unsolicited output (a reset on open) when
        it is True. With 'unopened' the real listen tool runs on the bound port, which does not exist (with 'unfound' the
        gateway cannot resolve the bound port at all, and with 'no-pyserial' the host cannot list serial ports); with 'bad-arguments' it refuses the worker's own seconds; with
        'unrecorded' the dispatched listen never returns a result to record.
        `calls` scripts the reuse worker instead: (operation, result) pairs, each a call of the generated tool of that
        operation, in order. A result that is an exception is raised in place of the child's result, so the dispatch
        ends without a recorded result. A result of None scripts a call the gateway refuses before dispatch.
        `operations` and `channel` give the emitted package's model operations and channel (`thermometer_package`).
        `attempts` scripts several fresh-reuse attempts, one list of calls each: the runtime of every attempt but the
        last exits with an error after its calls, so the run stops for the host until it is resumed.
        Yields the names of the kit's tools the configurator ran."""
        import shutil
        from unittest.mock import patch
        from generative_driver import toolkit
        from generative_driver.configurator import digest
        real_check, real_call_tool, tools = controller._check, toolkit.call_tool, []
        attempts = attempts or [list(calls) if calls is not None else
                                [('read_temperature', self.PASSED if live else self.MODEL_FAILURE)]]
        results = iter([call[1] for attempt in attempts for call in attempt if call[1] is not None])
        reused = []
        # The package is emitted before the toolkit is scripted; the scripted emit_package hands out its bytes.
        built = tempfile.TemporaryDirectory()
        emitted = thermometer_package(built.name, operations=operations, channel=channel, probed=probed)
        def fake_call_tool(name, arguments):
            if name == 'emit_package':
                package = Path(shutil.copytree(emitted, Path(arguments['run_dir']) / 'package'))
                return {'ok': True, '_exit': 0, 'package_dir': str(package), 'manifest_sha256': digest(package / 'manifest.json')}
            tools.append(name)
            if name == 'acquire_serial_listen':
                if listen == 'unrecorded':
                    raise RuntimeError('scripted: the listen ended without a result the gateway could record')
                if isinstance(listen, str):
                    return real_call_tool(name, arguments)
                return {'available': True, 'unsolicited_output': listen, '_exit': 0, 'artifacts': []}
            if name == 'emit_check':
                passed = replay in ('passed', 'supplied')
                return {'ok': passed, '_exit': 0 if passed else 1, 'integrity_ok': True,
                        'replay_status': 'passed' if passed else replay,
                        'source_evidence_kind': 'replay' if replay == 'supplied' else 'live'}
            raise AssertionError('Unexpected tool ' + name)
        def invoke_package(package, pin, operation, parameters, binding, effects):
            result = next(results)
            if isinstance(result, BaseException):
                raise result
            return json.loads(json.dumps(result))
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            if stage == 'acquire' and listen is not None:
                return {'inputs': [], 'allowed_tools': ['acquire_serial_listen']}
            return original(spec, stage, run_dir, workspace, accepted) if stage in ('emit', 'reuse') else {'inputs': [], 'allowed_tools': []}
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage in ('emit', 'reuse'):
                return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
            return {'ok': True, 'artifacts': [], 'checks': []}
        def work(request):
            if request.stage == 'acquire' and listen is not None:
                try:
                    controller.call('tool', {**gateway_identity(request), 'name': 'acquire_serial_listen',
                                             'arguments': {'seconds': 0} if listen == 'bad-arguments' else {}})
                except (RuntimeError, OSError):
                    pass
            if request.stage == 'emit':
                workspace = Path(request.workspace)
                controller.call('tool', {**gateway_identity(request), 'name': 'emit_package', 'arguments': {
                    'model_dir': str(workspace / 'model'), 'probe': str(workspace / 'probe.json')}})
            if request.stage == 'reuse':
                identity = gateway_identity(request)
                server = controller.call('mcp_tools', identity)['server']
                for index, (operation, result) in enumerate(attempts[len(reused)]):
                    try:
                        request_id = 'attempt-' + str(len(reused)) + '-call-' + str(index)
                        controller.call('mcp_tool', {**identity, 'name': server + '_' + operation, 'arguments': {},
                                                     'request_id': request_id})
                    except RuntimeError:
                        pass
                    except ValueError:
                        if result is not None:
                            raise
                reused.append(request.stage)
        def runtime(request):
            if request.stage == 'reuse' and len(reused) < len(attempts):
                return {'runtime': 'codex', 'status': 'failed', 'cause': 'exit_error', 'elapsed_seconds': 0, 'usage': None,
                        'reason': 'Configured runtime exited with an error'}
        def report(request):
            if request.stage == 'emit':
                package = Path(request.workspace) / 'tools' / 'package'
                return {'status': 'completed', 'summary': 'scripted emission', 'checks': [], 'unresolved': [],
                        'artifacts': [{'path': str(package), 'sha256': digest(package), 'kind': 'package'}]}
        stages = scripted_stages(controller, check=check, prepare=prepare, work=work, report=report, runtime=runtime)
        with built, stages, patch('generative_driver.toolkit.call_tool', fake_call_tool), \
             patch.object(controller.device_access, 'invoke_package', side_effect=invoke_package):
            yield tools

    def start(self, controller, **params):
        return controller.call('start', {'goal': 'Scripted final verdict; no model or device', 'binding': self.BINDING,
                                         'executor_config': {'command': ['never-launched']}, **params})

    def test_03_B6_a_replayed_package_whose_generated_tool_fresh_reuse_runs_on_the_device_is_equivalent(self):
        # Self-evidence: emit_check replayed the package, and fresh reuse ran a function of it with a generated tool.
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller) as tools:
                    run = self.start(controller)
                    result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['outcome_category'], result['reason']),
                                 ('completed', 'completed', 'Final verdict: equivalent'), result)
                self.assertEqual(tools, ['emit_check'])
                package = next(a for h in result['accepted_handoffs'] if h['stage'] == 'emit' for a in h['artifacts'])
                equivalence = result['equivalence']
                self.assertEqual((equivalence['equivalent'], equivalence['basis'], equivalence['diff']), (True, 'self-evidence', []))
                checks = {c['id']: c for c in equivalence['checks']}
                self.assertEqual(list(checks), ['emitted_package_replay'])
                self.assertTrue(all(c['passed'] for c in checks.values()))
                self.assertEqual(checks['emitted_package_replay']['package'], {'name': Path(package['path']).name, 'sha256': package['sha256']})
                self.assertTrue(checks['emitted_package_replay']['basis'].startswith('emit_check replay of the recorded device exchanges'),
                                checks['emitted_package_replay'])
                self.assertTrue(equivalence['mission']['passed'], equivalence['mission'])
                self.assertEqual([(o['name'], o['ok'], o['outcome'], o['emitted_package'])
                                  for o in equivalence['mission']['operations']],
                                 [(self.READ, True, 'succeeded', True)])
                # Recorded once under the run's final id, apart from evaluator verdicts; the run needs no further stage.
                [(verdict_id, payload)] = final_verdicts(controller, run)
                self.assertEqual(verdict_id, run['run_id'] + ':final')
                self.assertEqual(payload, {'stage': 'finalize', 'equivalence': equivalence, 'protocol': 'assistive/1',
                                           'frozen_artifact_sha256': package['sha256']})
                self.assertEqual(result['evaluator_verdicts'], [])
                self.assertIsNone(result['progress']['next_stage'])
                kinds = [e['kind'] for e in controller.call('events', run)['events']]
                self.assertEqual(kinds.count('run.finalized'), 1)
                self.assertLess(max(i for i, k in enumerate(kinds) if k == 'stage.accepted'), kinds.index('run.finalized'))
                status = controller.call('status', run)
                self.assertEqual(status['equivalence'], equivalence)
                self.assertEqual([n['stage'] for n in status['stage_notes']], ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'])
            finally:
                controller.close()

    def test_an_emit_check_that_recorded_no_replay_establishes_none(self):
        # An emit handoff from before the check named how it verified the package cannot show a replay.
        from generative_driver.configurator import _self_evidence, digest
        with tempfile.TemporaryDirectory() as directory:
            package = thermometer_package(Path(directory))
            emit = {'stage': 'emit', 'revision': 0, 'artifacts': [{'path': str(package), 'sha256': digest(package)}],
                    'checks': [{'name': 'package_integrity_and_replay', 'passed': True}]}
            evidence = _self_evidence([emit], [])
        replay = evidence['checks'][0]
        self.assertEqual((evidence['equivalent'], replay['passed'], replay['basis']),
                         (False, False, 'emit_check, run by the configurator at emit'))
        self.assertEqual(evidence['diff'][0], {'id': 'emitted_package_replay', 'reason': 'the emitted package passed '
                                               'emit_check without a replay of the recorded device exchanges'})

    def test_03_B6_a_driver_that_failed_on_the_device_or_an_unreplayed_package_completes_not_equivalent(self):
        from generative_driver.configurator import Controller, REPLAY_IDENTITY_ONLY, _has_terminal_final_failure
        for name, options, equivalent in (
                ('failed live operation', {'live': False}, True),
                ('model fault after an inconclusive call',
                 {'calls': [('read_temperature', self.HOST_FAULT), ('read_temperature', self.MODEL_FAILURE)]}, True),
                ('replay of identity alone', {'probed': 'identify'}, False),
                ('replay failed', {'replay': 'failed'}, False)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, **options) as tools:
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('completed', 'completed'), result)
                    equivalence = result['equivalence']
                    self.assertEqual((equivalence['equivalent'], equivalence['basis']), (equivalent, 'self-evidence'))
                    self.assertEqual({d['id'] for d in equivalence['diff']},
                                     {c['id'] for c in equivalence['checks'] if not c['passed']})
                    if name == 'replay failed':
                        # A driver was emitted and failed emit_check: emit hands off no package, drops it with the
                        # reason in its notes, and reuse is skipped.
                        self.assertEqual(tools, ['emit_check'])
                        self.assertTrue(result['reason'].startswith('Final verdict: not equivalent: the emitted package '
                                                                    'did not pass emit_check: replay failed'), result['reason'])
                        emit = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
                        self.assertEqual(emit['artifacts'], [])
                        self.assertEqual(next(c for c in emit['checks'] if c['name'] == 'package_integrity_and_replay')['candidates'], 1)
                        [drop] = emit['notes']['inline']['configurator']['dropped_artifacts']
                        self.assertEqual((Path(drop['path']).name, drop['reason']), ('package', 'emit_check did not pass: replay failed'))
                    elif name == 'replay of identity alone':
                        self.assertEqual(equivalence['diff'],
                                         [{'id': 'emitted_package_replay', 'reason': REPLAY_IDENTITY_ONLY}])
                        self.assertTrue(equivalence['mission']['passed'])
                    else:
                        # Decision D2: fresh reuse failures are reported in mission without failing the driver verdict.
                        self.assertEqual(result['reason'], 'Final verdict: equivalent')
                        self.assertIs(equivalence['mission']['passed'], False, equivalence['mission'])
                        failed = [e['data']['dispatch_id'] for e in controller.call('events', run)['events']
                                  if e['kind'] == 'tool.finished' and e['data']['name'] == self.READ][-1]
                        self.assertEqual(equivalence['mission']['diff'][0]['reason'],
                                         self.READ + ' failed on the device (dispatch ' + failed + ')')
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                    # A verdict of not equivalent is a completed run, never a terminal failure to resume around.
                    with controller._db() as db:
                        self.assertFalse(_has_terminal_final_failure(db, run['run_id']))
                    with self.assertRaisesRegex(ValueError, 'Only a stopped incomplete run can resume'):
                        controller.call('resume', run)
                finally:
                    controller.close()

    def operations(self, result):
        return [(o['name'], o['outcome']) for o in result['equivalence']['mission']['operations']]

    def test_03_B6_a_generated_call_refused_before_the_device_was_contacted_is_not_a_live_operation(self):
        from generative_driver.configurator import Controller
        for name, calls, mission_passed, outcomes in (
                ('a refusal before the device, then a function of the frozen package passes',
                 [('read_temperature', self.REFUSAL), ('read_temperature', self.PASSED)], True,
                 ['refused', 'succeeded']),
                ('only refused calls',
                 [('read_temperature', self.REFUSAL), ('identify', {**self.REFUSAL, 'operation': 'identify'})],
                 False, ['refused', 'refused'])):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', True), result)
                    self.assertEqual(result['equivalence']['mission']['passed'], mission_passed, result['equivalence'])
                    self.assertEqual([outcome for _, outcome in self.operations(result)], outcomes)
                    reasons = [d['reason'] for d in result['equivalence']['mission']['diff']]
                    self.assertFalse(any('failed on the device' in reason for reason in reasons), reasons)
                    if mission_passed:
                        self.assertEqual((result['reason'], reasons), ('Final verdict: equivalent', []))
                    else:
                        self.assertEqual(reasons, [self.NEVER_RAN + ' (2 calls refused before the device was contacted)'])
                    # The configurator records its own reading of a runtime refusal on the event.
                    finished = [e['data'] for e in controller.call('events', run)['events']
                                if e['kind'] == 'tool.finished' and e['data']['stage'] == 'reuse']
                    self.assertEqual([e['refused_before_open'] for e in finished],
                                     [True, False] if mission_passed else [True, True])
                finally:
                    controller.close()

    def test_01_B3_fresh_reuse_reads_a_missing_result_as_failed_never_as_succeeded(self):
        # The verdict reads the stored result, not the event's `ok`: a finished call with no result object is no
        # success, so it leaves the verdict unsettled.
        from generative_driver.configurator import _fresh_reuse
        event = {'name': 'interface_execute', 'connected': True, 'ok': True, 'dispatch_id': '1'}
        check, _ = _fresh_reuse(None, [event], grants=['read'])
        self.assertEqual([(o['outcome'], o['ok']) for o in check['operations']], [('inconclusive', False)])

    def test_01_B3_the_verdict_and_the_emit_check_ask_the_reading_and_hold_no_copy(self):
        import inspect
        from generative_driver import configurator
        from generative_driver.supplied import stages
        for name in ('_live_outcome', '_answered', '_host_fault', '_toolchain_fault'):
            with self.subTest(name):
                self.assertFalse(hasattr(configurator, name), 'the configurator holds ' + name)
        self.assertNotIn('_host_fault', inspect.getsource(stages._host))
        self.assertIn('dispatch_outcome', inspect.getsource(stages._host))

    def test_01_B1_the_gateway_and_the_device_access_hold_no_copy_of_the_success_test_or_of_the_record(self):
        import inspect
        import re
        import generative_driver
        from generative_driver import configurator, device_access
        # The last two copies of the success test were in the gateway's finish and in the device access's.
        for holder in (configurator.Controller._dispatch, device_access):
            self.assertNotIn("get('available'", inspect.getsource(holder))
        for name in ('_before_contact', '_declared_refusal'):
            self.assertFalse(hasattr(configurator, name), 'the configurator holds ' + name)
        for name in ('begin', 'finish'):
            self.assertFalse(hasattr(device_access.DeviceAccess, name), 'the device access holds ' + name)
        # Only the record's SQL names the pending effects and writes the flag of a run or a session.
        package = Path(generative_driver.__file__).parent
        for path in sorted(package.rglob('*.py')):
            if path.name != 'dispatch_record.py':
                with self.subTest(module=path.relative_to(package).as_posix()):
                    source = path.read_text(encoding='utf-8')
                    self.assertNotIn('pending_effects', source)
                    self.assertIsNone(re.search(r'\bSET\b[^\'"]*\buncertain\s*=', source))

    def test_only_a_named_host_failure_is_a_host_fault(self):
        """The emit stage stops for the host only on a positive list; any other toolchain error refuses the content."""
        from generative_driver.dispatch_outcome import read
        for error, host in (('OSError: [Errno 28] No space left on device', True), ('PermissionError: denied', True),
                            ('FileNotFoundError: no temporary directory', True), ('ModuleNotFoundError: no module', True),
                            ('MemoryError: ', False), ('TimeoutExpired: Command timed out after 260 seconds', True),
                            ('[Errno 20] Not a directory', True),
                            ('ValueError: duplicate JSON key: name', False), ('KeyError: files', False),
                            ('AttributeError: list has no attribute get', False),
                            ('RecursionError: maximum recursion depth exceeded', False),
                            ('ZeroDivisionError: division by zero', False), ('ModelError: identity', False)):
            with self.subTest(error=error):
                self.assertIs(read('emit_check', {'ok': False, 'error': error, '_exit': 1}).host_fault, host)
        self.assertIs(read('emit_check', {'ok': False, 'fault': 'host', '_exit': 4}).host_fault, True)
        # A timeout is named as one, so an undeclared one reads as a host fault (TimeoutExpired), not an unnamed error.
        timeout = read('emit_check', {'ok': False, 'error': 'TimeoutExpired: Command timed out after 260 seconds',
                                      '_exit': 1})
        self.assertEqual((timeout.outcome, timeout.detail), ('inconclusive', 'host fault (TimeoutExpired)'))
        self.assertIs(read('emit_check', {'ok': False, 'defects': [{'where': 'probe', 'what': 'invalid'}], '_exit': 1})
                      .host_fault, False)

    def test_03_B6_functions_the_operator_did_not_grant_leave_the_verdict_unscored_never_not_equivalent(self):
        """The frozen package's only function writes and the operator granted read alone: the gateway refuses every call
        of it, so identity is all fresh reuse can run. That limit is the operator's, not the driver's, so the once-only
        verdict is unscored. With the write grant, running identity alone is still not equivalent."""
        from generative_driver.configurator import (Controller, IDENTITY_ONLY, REPLAY_IDENTITY_ONLY, REPLAY_UNGRANTED,
                                                    UNGRANTED)
        self.assertEqual(IDENTITY_ONLY, 'fresh reuse ran no function of the frozen package beyond its identity '
                         'operation; identity alone is not evidence of function, nor is an operation that only waits '
                         "or repeats identity's requests")
        operations = {'identify': {'effect': 'read'}, 'set_rate': {'effect': 'write'},
                      'settle': {'effect': 'read', 'steps': [{'op': 'delay', 'ms': 1}]}}
        for grants, calls, equivalent, replay_reason, mission_reason in (
                (['read'], [('set_rate', None), ('identify', self.passed('identify'))], None, REPLAY_UNGRANTED, UNGRANTED),
                # A granted operation that only waits is no function: the operator's grants still permit none.
                (['read'], [('settle', self.passed('settle'))], None, REPLAY_UNGRANTED, UNGRANTED),
                (['read', 'write'], [('identify', self.passed('identify'))], False, REPLAY_IDENTITY_ONLY, IDENTITY_ONLY)):
            called = [operation for operation, _ in calls]
            with self.subTest(grants=grants, calls=called), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls, operations=operations):
                        run = self.start(controller, effects=grants)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', equivalent), result)
                    self.assertEqual(self.operations(result), [('scripted_thermometer_' + calls[-1][0], 'succeeded')])
                    self.assertEqual([d['reason'] for d in result['equivalence']['diff']], [replay_reason])
                    self.assertEqual(result['equivalence']['mission']['passed'], equivalent)
                    self.assertEqual([d['reason'] for d in result['equivalence']['mission']['diff']], [mission_reason])
                    reuse = next(h for h in result['accepted_handoffs'] if h['stage'] == 'reuse')
                    note = next(c for c in reuse['checks'] if c['name'] == 'fresh_package_execution')
                    self.assertIs(note['passed'], equivalent)
                    refused = [e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'tool.refused']
                    self.assertEqual([r['reason'] for r in refused],
                                     ['Operation requires an effect grant the operator has not supplied'] if len(calls) > 1 else [])
                finally:
                    controller.close()

    def test_03_B6_a_reset_the_operator_did_not_grant_leaves_the_verdict_unscored_never_not_equivalent(self):
        """The reset grant is an operator grant like the effect grants. Without it, the runtime of a package whose
        channel resets the device on open refuses every operation before it opens the port, including a live probe of
        those model bytes, so emit_check can replay only exchanges the worker supplied, and the gateway refuses every
        generated call of that package before it dispatches it. The gateway also refuses every
        connection to a serial device the run observed resetting on open, or whose every listen failed for the
        operator or the host, so no live probe could run either. Neither says anything about the driver: the verdict is
        unscored, unless the package failed its offline emit_check. Listening needs no grant, so a device no worker
        listened to is the candidate pipeline's omission: not equivalent. With the grant, or on a device that was heard
        to stay quiet, the same runs are judged as usual."""
        from generative_driver.configurator import (Controller, REPLAY_UNGRANTED, SUPPLIED, UNGRANTED, UNHEARD,
                                                    UNLISTENED, UNREACHABLE, _fresh_reuse, _frozen_package, _ungranted,
                                                    digest)
        from generative_driver.toolkit import call_tool
        from test_package import example_model, example_replay
        # The real tools under the read grant alone, as the gateway dispatches them, on a model whose uart channel
        # declares resets_on_open. The runtime refuses before it opens the port, so none is needed.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            model = root / 'model'; model.mkdir()
            resetting = example_model(); resetting['channel'] = {**resetting['channel'], 'resets_on_open': True}
            (model / 'model.json').write_text(json.dumps(resetting))
            # No live probe of these model bytes can be recorded without the reset grant.
            live_probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(model), 'operation': 'measure',
                                                 'n': 1, 'binding': {'port': str(root / 'no-such-serial-port')},
                                                 'allow_effects': ['read']})
            self.assertEqual((live_probe['_exit'], [(r['error']['code'], r['transport_open_attempted'])
                                                    for r in live_probe['results']]), (4, [('effect', False)]), live_probe)
            (root / 'replay.json').write_text(json.dumps(example_replay()))
            probe = call_tool('probe_run', {'run_dir': str(root / 'run'), 'model_dir': str(model), 'operation': 'measure',
                                            'n': 1, 'replay': str(root / 'replay.json'), 'allow_effects': ['reset']})
            self.assertEqual(probe['_exit'], 0, probe)
            package = call_tool('emit_package', {'run_dir': str(root / 'run'), 'model_dir': str(model),
                                                 'probe_json': probe['probe']})['package_dir']
            checked = call_tool('emit_check', {'run_dir': str(root / 'checks'), 'package_dir': package})
            self.assertEqual((checked['_exit'], checked['replay_status'], checked['source_evidence_kind']),
                             (0, 'passed', 'replay'), checked)
            # No generated call of this package is dispatched under the read grant alone, so fresh reuse has none.
            frozen = _frozen_package({'path': package, 'sha256': digest(package)}, strict=True)
            self.assertIs(frozen['resets_on_open'], True)
            check, diff = _fresh_reuse(frozen, [], [], ['read'])
            self.assertEqual((check['passed'], [d['reason'] for d in diff]), (None, [UNGRANTED]))
            self.assertTrue(_ungranted(frozen, ['read']))
            self.assertFalse(_ungranted(frozen, ['read', 'reset']))
        # Whole runs on a serial binding: the reuse worker calls the generated tool of the frozen package's read once.
        # A call of None is one the gateway refuses before dispatch.
        uart, resets = {'type': 'uart', 'baudrate': 115200}, {'type': 'uart', 'baudrate': 115200, 'resets_on_open': True}
        unreplayed = ('the emitted package passed emit_check without a replay of the recorded device exchanges (' + SUPPLIED + ')')
        rejected = 'the emitted package did not pass emit_check: replay failed'
        for name, grants, listen, channel, replay, result, equivalent, note, reasons in (
                # Its runtime refused the live probe of these bytes, so emit_check could replay only supplied exchanges.
                ('a package that resets on open, without the reset grant', ['read'], False, resets, 'supplied',
                 None, None, None, [unreplayed, REPLAY_UNGRANTED]),
                ('a package that resets on open, with the reset grant', ['read', 'reset'], False, resets, 'passed',
                 self.PASSED, True, True, []),
                ('a device observed resetting on open, without the reset grant', ['read'], True, resets, 'supplied', None,
                 None, None, [unreplayed, UNREACHABLE]),
                # A package that fails its offline check failed whatever the device access.
                ('a device observed resetting on open, whose package failed emit_check', ['read'], True, resets, 'failed',
                 None, False, None, [rejected]),
                ('a listen that could not open the bound port, without the reset grant', ['read'], 'unopened', uart,
                 'supplied', None, None, None, [unreplayed, UNHEARD]),
                ('a listen whose result was never recorded, without the reset grant', ['read'], 'unrecorded', uart,
                 'supplied', None, None, None, [unreplayed, UNHEARD]),
                ('a bound device the gateway cannot find to listen to, without the reset grant', ['read'], 'unfound', uart,
                 'supplied', None, None, None, [unreplayed, UNHEARD]),
                # A host without the optional pyserial extra cannot list ports: the host's setup, not the driver.
                ('a host that cannot list serial ports, without the reset grant', ['read'], 'no-pyserial', uart,
                 'supplied', None, None, None, [unreplayed, UNHEARD]),
                # Listening needs no grant: the pipeline that never listened kept fresh reuse from the device.
                ('a device no worker listened to, without the reset grant', ['read'], None, uart, 'supplied', None,
                 False, False, [unreplayed, UNLISTENED]),
                ("a listen refused for the worker's own arguments, without the reset grant", ['read'], 'bad-arguments',
                 uart, 'supplied', None, False, False, [unreplayed, UNLISTENED]),
                ('a device never listened to, with the reset grant', ['read', 'reset'], None, uart, 'passed', self.PASSED,
                 True, True, []),
                ('a device never listened to, with the reset grant but no live probe', ['read', 'reset'], None, uart,
                 'supplied', self.PASSED, False, True, [unreplayed]),
                ('a device heard to stay quiet, with the read grant', ['read'], False, uart, 'passed', self.PASSED, True,
                 True, [])):
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                # The host lists no serial port, whether or not the optional pyserial extra is installed.
                no_ports = types.ModuleType('serial.tools.list_ports')
                no_ports.comports = lambda: []
                ports = (unittest.mock.patch.dict(sys.modules, {'serial': types.ModuleType('serial'),
                         'serial.tools': types.ModuleType('serial.tools'), 'serial.tools.list_ports': no_ports})
                         if listen == 'unfound' else
                         unittest.mock.patch.dict(sys.modules, dict.fromkeys(('serial', 'serial.tools', 'serial.tools.list_ports')))
                         if listen == 'no-pyserial' else contextlib.nullcontext())
                try:
                    with ports, self.finishing(controller, calls=[('read_temperature', result)],
                                               replay=replay, channel=channel, listen=listen):
                        # A port that does not exist: the real listen tool cannot open it. No port matches the
                        # identity of a device that is not attached.
                        run = self.start(controller, effects=grants, binding=(
                            {'vendor_id': 0xFFFF, 'product_id': 0xFFFE, 'serial_number': 'no-such-device'} if listen in ('unfound', 'no-pyserial')
                            else {'port': str(Path(directory).resolve() / 'no-such-serial-port')}))
                        result_of_run = wait_stopped(controller, run)
                    self.assertEqual((result_of_run['status'], result_of_run['equivalence']['equivalent']),
                                     ('completed', equivalent), result_of_run)
                    self.assertEqual([d['reason'] for d in result_of_run['equivalence']['diff']], reasons)
                    reuse = next(h for h in result_of_run['accepted_handoffs'] if h['stage'] == 'reuse')
                    self.assertIs(next((c for c in reuse['checks'] if c['name'] == 'fresh_package_execution'), {}).get('passed'), note)
                    events = controller.call('events', run)['events']
                    refusals = [e['data']['reason'] for e in events if e['kind'] == 'tool.refused' and e['data']['stage'] == 'reuse']
                    listens = [e['data'] for e in events if e['kind'] == 'tool.finished' and e['data']['name'] == 'acquire_serial_listen']
                    if listen in ('unfound', 'no-pyserial'):
                        # The gateway refused the listen: no port matches the bound device (the operator's), or the
                        # host cannot list ports at all (the host's).
                        [unfound] = [e['data'] for e in events if e['kind'] == 'tool.refused' and e['data']['stage'] == 'acquire']
                        self.assertEqual((unfound['name'], unfound['fault']),
                                         ('acquire_serial_listen', 'operator' if listen == 'unfound' else 'host'), unfound)
                        self.assertEqual(listens, [])
                    if listen in ('unopened', 'bad-arguments'):
                        # The real listen tool: it could not open the port, or refused the worker's seconds.
                        [heard] = listens
                        self.assertFalse(heard['ok'], heard)
                        # pyserial raises opening a port that does not exist; a host without pyserial declares a host
                        # fault. Either way the listen failed outside the worker's own arguments.
                        failed_outside = 'error' in heard['result'] or heard['result'].get('fault') == 'host'
                        self.assertEqual(failed_outside, listen == 'unopened', heard)
                    if replay == 'failed':
                        # Emit handed off no package, so reuse was skipped and made no call.
                        self.assertEqual(refusals, [])
                    elif result is None:
                        # The gateway refused the call for want of the reset grant its package or its device needs.
                        self.assertEqual(refusals, [
                            'Operation or transport opening requires an effect grant the operator has not supplied'
                            if channel is resets else
                            'Listen to the bound serial device first, or supply a reset grant'])
                        self.assertEqual(self.operations(result_of_run), [])
                    else:
                        self.assertEqual(refusals, [])
                        self.assertEqual(self.operations(result_of_run), [(self.READ, 'succeeded')])
                finally:
                    controller.close()

    def test_a_run_with_no_device_bound_leaves_the_verdict_unscored_unless_its_package_failed_offline(self):
        """Without an operator-owned binding the gateway refuses every device connection, so neither a live probe nor
        fresh reuse could reach a device. That is the operator's limit, not the driver's: the verdict is unscored,
        unless the emitted package failed its offline emit_check."""
        from generative_driver.configurator import Controller, SUPPLIED, UNBOUND
        unreplayed = ('the emitted package passed emit_check without a replay of the recorded device exchanges (' + SUPPLIED + ')')
        for replay, equivalent, reasons, refusals in (
                ('supplied', None, [unreplayed, UNBOUND], ['No operator-owned device binding for this stage']),
                ('failed', False, ['the emitted package did not pass emit_check: replay failed'], [])):
            with self.subTest(replay=replay), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=[('read_temperature', None)], replay=replay):
                        run = self.start(controller, binding=None)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', equivalent), result)
                    self.assertEqual([d['reason'] for d in result['equivalence']['diff']], reasons)
                    self.assertEqual([e['data']['reason'] for e in controller.call('events', run)['events']
                                      if e['kind'] == 'tool.refused'], refusals)
                finally:
                    controller.close()

    def test_a_device_no_live_probe_could_reach_excuses_only_a_replay_missing_for_want_of_device_exchanges(self):
        """When the operator's limits kept every connection from the device, no live probe could be recorded: a package
        that passed emit_check without a replay of device exchanges leaves the verdict unscored. No driver emitted, a
        candidate emit_check rejected offline, or several packages handed off is the pipeline's failure whatever the
        device access: not equivalent. So is a device no worker listened to, which needs no grant. A package whose
        runtime needs the reset grant its channel declares could not be probed live either."""
        from generative_driver.configurator import SUPPLIED, UNBOUND, UNHEARD, UNLISTENED, UNREACHABLE, _self_evidence, digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one, two = thermometer_package(root, 'one'), thermometer_package(root, 'two')
            resetting = thermometer_package(root, 'resetting',
                                            channel={'type': 'uart', 'baudrate': 115200, 'resets_on_open': True})
            artifact = lambda package: {'path': str(package), 'sha256': digest(package)}
            emit = lambda artifacts, **check: [{'stage': 'emit', 'revision': 0, 'artifacts': artifacts,
                                                'checks': [{'name': 'package_integrity_and_replay', **check}]}]
            supplied = emit([artifact(one)], passed=True, candidates=1, verified_by=[SUPPLIED])
            emits = {'passed without a device replay': (supplied, True),
                     'no candidate emitted': (emit([], passed=False, candidates=0), False),
                     'a candidate emit_check rejected': (emit([], passed=False, candidates=1,
                                                              rejected=[{'package': 'one', 'reason': 'replay failed'}]), False),
                     'two packages handed off': (emit([artifact(one), artifact(two)], passed=True, candidates=2,
                                                      verified_by=[SUPPLIED, SUPPLIED]), False)}
            for reach, excused in ((UNBOUND, True), (UNREACHABLE, True), (UNHEARD, True), (UNLISTENED, False), (None, False)):
                for name, (accepted, unexchanged) in emits.items():
                    with self.subTest(reach=reach and reach[:40], emit=name):
                        evidence = _self_evidence(accepted, [], grants=['read'], unreachable=reach)
                        self.assertIs(evidence['equivalent'], None if excused and unexchanged else False, evidence)
            resets = emit([artifact(resetting)], passed=True, candidates=1, verified_by=[SUPPLIED])
            for grants, equivalent in ((['read'], None), (['read', 'reset'], False)):
                with self.subTest(package='resets on open', grants=grants):
                    self.assertIs(_self_evidence(resets, [], grants=grants)['equivalent'], equivalent)

    def test_reachability_is_judged_as_of_fresh_reuse_never_by_a_later_operator_observation(self):
        """The gateway refused fresh reuse every connection to a serial device the run observed resetting on open,
        without a reset grant. The run then stops for the host before its verdict, and the operator records that the
        device does not reset on open. Fresh reuse could not reach the device whatever the operator observed later, so
        resume grades it as unscored, never as a driver that never ran."""
        from unittest.mock import patch
        import interface_runtime.package as runtime_package
        from generative_driver import configurator
        real_self_evidence, real_digest = configurator._self_evidence, runtime_package.digest
        attempts = []
        def unreadable(path):
            if Path(path).parent.name == 'drivers':
                raise PermissionError(13, 'Permission denied', str(path))
            return real_digest(path)
        def self_evidence(*args, **kwargs):
            attempts.append(1)
            if len(attempts) > 1:
                return real_self_evidence(*args, **kwargs)
            with patch.object(runtime_package, 'digest', unreadable):
                return real_self_evidence(*args, **kwargs)
        resets = {'type': 'uart', 'baudrate': 115200, 'resets_on_open': True}
        unreplayed = ('the emitted package passed emit_check without a replay of the recorded device exchanges ('
                      + configurator.SUPPLIED + ')')
        with tempfile.TemporaryDirectory() as directory:
            controller = configurator.Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller, calls=[('read_temperature', None)],
                                    replay='supplied', channel=resets, listen=True), \
                        patch('generative_driver.configurator._self_evidence', self_evidence):
                    run = self.start(controller, effects=['read'],
                                     binding={'port': str(Path(directory).resolve() / 'no-such-serial-port')})
                    stopped = wait_stopped(controller, run)
                    self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']),
                                     ('failed', 'host', 'finalize'), stopped)
                    controller.call('respond', {**run, 'message': 'The device does not reset when opened',
                                                'observation': {'binding_property': 'resets_on_open', 'value': False}})
                    self.assertIs(controller._binding_state(run['run_id'])['resets_on_open'], False)
                    resume_when_idle(self, controller, run)
                    result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', None), result)
                self.assertEqual([d['reason'] for d in result['equivalence']['diff']], [unreplayed, configurator.UNREACHABLE])
                refusals = [e['data']['reason'] for e in controller.call('events', run)['events']
                            if e['kind'] == 'tool.refused']
                self.assertEqual(refusals, [
                    'Operation or transport opening requires an effect grant the operator has not supplied'])
                self.assertEqual(len(attempts), 2)
            finally:
                controller.close()

    def records(self, controller, run, result):
        """What the final verdict judged of a finished run, from the configurator's own records: the facts of the frozen
        package and the tool.finished payloads of fresh reuse, in order."""
        from generative_driver.configurator import _frozen_package
        package = next(a for h in result['accepted_handoffs'] if h['stage'] == 'emit' for a in h['artifacts'])
        return _frozen_package(package, strict=True), [
            e['data'] for e in controller.call('events', run)['events']
            if e['kind'] == 'tool.finished' and e['data']['stage'] == 'reuse']

    @staticmethod
    def judged(check, diff):
        """A judgement of `_fresh_reuse` in short: whether it passed, each operation as (name, outcome, whether it ran
        the frozen package), and the reasons without their dispatch ids."""
        return (check['passed'], [(o['name'], o['outcome'], o['emitted_package']) for o in check['operations']],
                [d['reason'].split(' (dispatch ')[0] for d in diff])

    def test_03_B6_a_call_counts_only_when_its_proof_names_the_package_manifest_assignment_dispatch_and_tool(self):
        """The gateway writes an `mcp` proof into the result of each generated call it dispatches. The verdict counts a
        call as a run of the frozen package only when that proof names the frozen package and its manifest, the call's
        own assignment and dispatch, and its tool. A call whose proof names anything else, or that has none, ran no
        tool of the frozen package: its pass is no evidence of the driver, and its failure is not the driver's."""
        from generative_driver.configurator import Controller, _fresh_reuse
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller, calls=[('read_temperature', self.MODEL_FAILURE),
                                                       ('read_temperature', self.PASSED)]):
                    run = self.start(controller)
                    result = wait_stopped(controller, run)
                frozen, (failure, passed) = self.records(controller, run, result)
            finally:
                controller.close()
        judge = lambda *events: self.judged(*_fresh_reuse(frozen, list(events), grants=['read']))
        inconclusive = self.READ + ' was inconclusive: model fault outside the emitted driver, not a driver failure'
        # The proof the gateway wrote names all five, and the call counts.
        for event in (failure, passed):
            proof = event['result']['mcp']
            named = {'schema': 'driver-mcp-call/1', 'package_sha256': frozen['sha256'],
                     'manifest_sha256': frozen['manifest_sha256'], 'assignment_id': event['assignment_id'],
                     'dispatch_id': event['dispatch_id'], 'tool_name': self.READ}
            self.assertEqual({key: proof[key] for key in named}, named)
        self.assertEqual(judge(passed), (True, [(self.READ, 'succeeded', True)], []))
        self.assertEqual(judge(failure, passed), (False, [(self.READ, 'failed', True), (self.READ, 'succeeded', True)],
                                                  [self.READ + ' failed on the device']))
        others = {'package_sha256': '0' * 64, 'manifest_sha256': '0' * 64, 'assignment_id': 'another-assignment',
                  'dispatch_id': '0', 'tool_name': self.IDENTIFY, 'schema': 'driver-mcp-call/0'}
        def unproved(event, key):
            """The event with a proof that names another `key`, with none, or with one that is no object."""
            result = {k: v for k, v in event['result'].items() if k != 'mcp'}
            if key in others:
                result['mcp'] = {**event['result']['mcp'], key: others[key]}
            elif key == 'no object':
                result['mcp'] = 'driver-mcp-call/1'
            return {**event, 'result': result}
        for key in (*others, 'absent', 'no object'):
            with self.subTest(proof=key):
                self.assertEqual(judge(unproved(passed, key)),
                                 (False, [(self.READ, 'succeeded', False)], [self.NEVER_RAN]))
                self.assertEqual(judge(unproved(failure, key)),
                                 (False, [(self.READ, 'inconclusive', False)], [inconclusive, self.NEVER_RAN]))
                # Beside a run of the frozen package that passed, a failure of other bytes leaves the verdict unsettled.
                self.assertEqual(judge(unproved(failure, key), passed), (
                    None, [(self.READ, 'inconclusive', False), (self.READ, 'succeeded', True)], [inconclusive]))

    def test_03_B6_no_other_call_counts_as_a_run_of_the_frozen_package(self):
        """Only a generated tool call runs the frozen package. A record of another tool is judged as a live operation,
        but it is no run of the package, whatever bytes it names: a live test of the frozen package with emit_test_live
        that passed is no evidence of function, and a model failure it or interface_execute reports is not the
        driver's. Fresh reuse is assigned neither tool, so the records are written here."""
        from generative_driver.configurator import Controller, _fresh_reuse
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller):
                    run = self.start(controller)
                    result = wait_stopped(controller, run)
                frozen, [generated] = self.records(controller, run, result)
            finally:
                controller.close()
        def other(name, ok, dispatch):
            """A record of the tool `name` for the bytes of the frozen package, as the gateway would write it: of a
            read that passed, or that the runtime attributes to the model after the device was opened."""
            if name == 'emit_test_live':
                hashed = {'package_dir': frozen['sha256']}
                result = {'schema': 'interface-live-test/1', 'ok': ok, 'manifest_sha256': frozen['manifest_sha256'],
                          'results': [{'ok': ok, 'operation': 'read_temperature',
                                       **({} if ok else {'error_code': 'expect', 'fault': 'model'})}]}
            else:
                hashed = {'model_dir': frozen['sha256']}
                result = {'schema': 'interface-result/1', 'operation': 'read_temperature', 'ok': ok,
                          'identity_verified': True, 'transport_open_attempted': True,
                          **({} if ok else {'error': {'code': 'expect', 'message': 'reply differs from the model',
                                                      'fault': 'model'}})}
            return {**generated, 'name': name, 'ok': ok, 'dispatch_id': str(dispatch), 'argument_sha256': hashed,
                    'result': {**result, '_exit': 0 if ok else 1}}
        judge = lambda events, started=(): self.judged(*_fresh_reuse(frozen, events, started, grants=['read']))
        for name in ('emit_test_live', 'interface_execute'):
            with self.subTest(tool=name):
                inconclusive = name + ' was inconclusive: model fault outside the emitted driver, not a driver failure'
                self.assertEqual(judge([other(name, True, 1)]), (False, [(name, 'succeeded', False)], [self.NEVER_RAN]))
                self.assertEqual(judge([other(name, False, 1)]),
                                 (False, [(name, 'inconclusive', False)], [inconclusive, self.NEVER_RAN]))
                self.assertEqual(judge([other(name, False, 1), generated]), (
                    None, [(name, 'inconclusive', False), (self.READ, 'succeeded', True)], [inconclusive]))
                # A dispatch of it without a recorded result is unsettled, but it cannot have run the frozen package.
                unrecorded = {k: v for k, v in other(name, True, 1).items() if k not in ('ok', 'result')}
                self.assertEqual(judge([], [unrecorded]),
                                 (False, [(name, 'unresolved', False)],
                                  [name + ' was dispatched but its result was never recorded', self.NEVER_RAN]))
        self.assertEqual(judge([generated]), (True, [(self.READ, 'succeeded', True)], []))

    def test_03_B6_fresh_reuse_that_ran_no_tool_of_the_frozen_package_is_not_equivalent_and_says_so(self):
        """Fresh reuse must run a tool of the frozen package. A fresh agent that hands off without one call leaves the
        driver untested, and the verdict names that rule. So does one whose only dispatch was of the identity tool and
        has no recorded result: identity is no function, so that dispatch cannot have run one."""
        from generative_driver.configurator import Controller, NEVER_RAN
        self.assertEqual(NEVER_RAN, 'fresh reuse never ran a tool of the frozen package')
        unrecorded = self.IDENTIFY + ' was dispatched but its result was never recorded'
        for name, calls, operations, says in (
                ('no call', [], [], [NEVER_RAN]),
                ('one dispatch of the identity tool, never recorded',
                 [('identify', RuntimeError('scripted fault after dispatch'))], [(self.IDENTIFY, 'unresolved')],
                 [unrecorded, NEVER_RAN])):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', True), result)
                    self.assertEqual(self.operations(result), operations)
                    mission = result['equivalence']['mission']
                    self.assertIs(mission['passed'], False, mission)
                    diff = mission['diff']
                    self.assertEqual({d['id'] for d in diff}, {'fresh_reuse_live_operations'})
                    reasons = [d['reason'] for d in diff]
                    self.assertEqual([reason.split(' (dispatch ')[0] for reason in reasons], says)
                    self.assertEqual(mission['reason'], '; '.join(reasons))
                    reuse = next(h for h in result['accepted_handoffs'] if h['stage'] == 'reuse')
                    note = next(c for c in reuse['checks'] if c['name'] == 'fresh_package_execution')
                    self.assertEqual((note['passed'], note['reason']), (False, '; '.join(reasons)))
                finally:
                    controller.close()

    def test_03_B6_a_host_fault_or_an_unrecorded_dispatch_of_a_generated_call_leaves_the_verdict_unscored(self):
        from generative_driver.configurator import Controller
        lost = lambda: RuntimeError('scripted fault after dispatch')
        for name, calls, outcome, detail in (
                ('host fault, then the frozen package passes',
                 [('read_temperature', self.HOST_FAULT), ('read_temperature', self.PASSED)], 'inconclusive',
                 'host fault'),
                ('host fault only', [('read_temperature', self.HOST_FAULT)], 'inconclusive', 'host fault'),
                # It may have come after the device ran the package, so it never reads as a refusal.
                ('a child process that gave no result', [('read_temperature', self.NO_RESULT)], 'inconclusive',
                 'host fault (package_output)'),
                ('dispatch never recorded, then the frozen package passes',
                 [('read_temperature', lost()), ('read_temperature', self.PASSED)], 'unresolved', 'never recorded'),
                # The call was a function of the frozen package, so it may have run one.
                ('dispatch never recorded only', [('read_temperature', lost())], 'unresolved', 'never recorded')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('completed', 'completed'), result)
                    self.assertTrue(result['equivalence']['equivalent'], result['equivalence'])
                    mission = result['equivalence']['mission']
                    self.assertIsNone(mission['passed'], mission)
                    self.assertEqual(self.operations(result)[0], (self.READ, outcome))
                    started = [e['id'] for e in controller.call('events', run)['events']
                               if e['kind'] == 'tool.started' and e['data']['name'] == self.READ]
                    [entry] = mission['diff']
                    self.assertEqual(entry['id'], 'fresh_reuse_live_operations')
                    self.assertTrue(entry['reason'].startswith(self.READ + ' was '), entry['reason'])
                    self.assertIn(detail, entry['reason'])
                    self.assertIn('(dispatch ' + str(started[0]) + ')', entry['reason'])
                    self.assertNotIn('failed on the device', entry['reason'])
                    self.assertEqual(mission['reason'], entry['reason'])
                finally:
                    controller.close()

    def test_03_B6_every_reuse_attempt_that_received_the_frozen_package_is_judged(self):
        """A fresh-reuse attempt that stopped before its handoff (its runtime exited with an error) and was resumed
        had already run a tool of the frozen package on the device. Its live operations count as the accepted
        attempt's do."""
        from generative_driver.configurator import Controller
        for name, first, equivalent, outcome, says in (
                ('driver failure', self.MODEL_FAILURE, False, 'failed', self.READ + ' failed on the device'),
                ('host fault', self.HOST_FAULT, None, 'inconclusive', 'host fault'),
                ('dispatch never recorded', RuntimeError('scripted fault after dispatch'), None, 'unresolved',
                 'never recorded'),
                ('refusal before the device was contacted', self.REFUSAL, True, 'refused', None)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, attempts=[[('read_temperature', first)],
                                                              [('read_temperature', self.PASSED)]]):
                        run = self.start(controller)
                        stopped = wait_stopped(controller, run)
                        self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']),
                                         ('failed', 'host', 'reuse'), stopped)
                        resume_when_idle(self, controller, run)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('completed', 'completed'), result)
                    self.assertTrue(result['equivalence']['equivalent'], result['equivalence'])
                    mission = result['equivalence']['mission']
                    self.assertEqual(mission['passed'], equivalent, mission)
                    started = [e for e in controller.call('events', run)['events']
                               if e['kind'] == 'tool.started' and e['data']['name'] == self.READ]
                    self.assertEqual(len({e['data']['assignment_id'] for e in started}), 2)
                    self.assertEqual(self.operations(result), [(self.READ, outcome), (self.READ, 'succeeded')])
                    reasons = [d['reason'] for d in mission['diff']]
                    if says:
                        [reason] = reasons
                        self.assertIn(says, reason)
                        self.assertIn('(dispatch ' + str(started[0]['id']) + ')', reason)
                        self.assertEqual(mission['reason'], reason)
                    else:
                        self.assertEqual((result['reason'], reasons), ('Final verdict: equivalent', []))
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                finally:
                    controller.close()

    def test_resuming_a_stopped_run_with_a_verdict_completes_from_it_without_budget_binding_or_worker(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller, SafetyError
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller):
                    with patch.object(controller, '_release', side_effect=self.uncertain_release(controller)):
                        run = self.start(controller)
                        stopped = wait_stopped(controller, run)
                self.assertEqual((stopped['status'], stopped['outcome_category']), ('blocked', 'safety'), stopped)
                controller.call('respond', {**run, 'effect_resolution': 'confirmed_safe', 'observation': {
                    'source': 'scripted operator', 'message': 'Checked the device', 'prior_execution_stopped': True}})
                with controller._db() as db:
                    # The wall-clock budget has lapsed since the verdict.
                    db.execute('UPDATE runs SET created=created-? WHERE id=?', (10 * 86400, run['run_id']))
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM bindings WHERE run_id=?', (run['run_id'],)).fetchone()[0], 1)
                with patch('generative_driver.configurator._self_evidence', side_effect=AssertionError('graded twice')), \
                     patch.object(controller, '_claim_binding', side_effect=SafetyError('Device binding is already owned by another run')), \
                     patch.object(controller, '_spawn', side_effect=AssertionError('no worker is needed')):
                    resumed = controller.call('resume', run)
                self.assertEqual((resumed['status'], resumed['reason']), ('completed', 'Final verdict: equivalent'), resumed)
                result = controller.call('result', run)
                self.assertEqual((result['status'], result['outcome_category'], result['uncertain_effect']), ('completed', 'completed', False))
                self.assertEqual(len(final_verdicts(controller, run)), 1)
                with controller._db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM bindings WHERE run_id=?', (run['run_id'],)).fetchone()[0], 0)
                kinds = [e['kind'] for e in controller.call('events', run)['events']]
                self.assertEqual(kinds[-2:], ['run.resumed', 'run.completed'])
                self.assertEqual(kinds.count('run.finalized'), 1)
            finally:
                controller.close()

    @staticmethod
    def uncertain_release(controller):
        def release(run_id, spec, run_dir):
            with controller._db() as db:
                db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run_id,))
            return []
        return release

    def test_cancellation_never_takes_the_completed_status_from_a_run_with_a_verdict(self):
        import threading
        from generative_driver.configurator import Controller
        for when in ('after completion', 'between the verdict and completion'):
            with self.subTest(when=when), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                cancellers = []
                real_finalize = controller._finalize
                def finalize_then_cancel(run_id, spec, accepted):
                    final = real_finalize(run_id, spec, accepted)
                    canceller = threading.Thread(target=controller.call, args=('cancel', {'run_id': run_id}))
                    canceller.start()
                    cancellers.append(canceller)
                    until = time.monotonic() + 5
                    while controller._row(run_id)['status'] != 'cancelled':
                        self.assertLess(time.monotonic(), until)
                        time.sleep(.01)
                    return final
                try:
                    from unittest.mock import patch
                    with self.finishing(controller), contextlib.ExitStack() as stack:
                        if when != 'after completion':
                            stack.enter_context(patch.object(controller, '_finalize', side_effect=finalize_then_cancel))
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    for canceller in cancellers:
                        canceller.join(10)
                    if when == 'after completion':
                        cancelled = controller.call('cancel', run)
                        self.assertEqual((cancelled['status'], cancelled['reason']), ('completed', 'Final verdict: equivalent'))
                    else:
                        self.assertEqual(len(cancellers), 1)
                    result = controller.call('result', run)
                    self.assertEqual((result['status'], result['outcome_category'], result['reason']),
                                     ('completed', 'completed', 'Final verdict: equivalent'), result)
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                    with controller._db() as db:
                        self.assertEqual(db.execute('SELECT cancelled FROM runs WHERE id=?', (run['run_id'],)).fetchone()[0], 0)
                finally:
                    controller.close()

    def test_a_restart_after_the_verdict_completes_from_it_and_never_grades_again(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / 'home'
                controller = Controller(home)
                real_state = controller._state
                def die_on_completion(run_id, status, *args, **kwargs):
                    if status == 'completed':
                        raise SystemExit('scripted configurator death right after the verdict transaction')
                    return real_state(run_id, status, *args, **kwargs)
                try:
                    with self.finishing(controller), patch.object(controller, '_state', side_effect=die_on_completion):
                        run = self.start(controller)
                        wait_worker_exit(controller, run)
                    crashed = controller.call('result', run)
                    self.assertEqual((crashed['status'], crashed['stage']), ('running', 'finalize'))
                    self.assertTrue(crashed['equivalence']['equivalent'])
                    if uncertain:
                        with controller._db() as db:
                            db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run['run_id'],))
                finally:
                    controller.close()
                with patch('generative_driver.configurator._self_evidence', side_effect=AssertionError('graded twice')):
                    reopened = Controller(home)
                    try:
                        recovered = reopened.call('result', run)
                        self.assertEqual(recovered['equivalence'], crashed['equivalence'])
                        if uncertain:
                            # An uncertain effect still blocks for safety; the verdict stays and resume completes from it.
                            self.assertEqual((recovered['status'], recovered['outcome_category']), ('blocked', 'safety'), recovered)
                            self.assertIn('uncertain', recovered['reason'])
                            with self.assertRaisesRegex(ValueError, 'uncertain'):
                                reopened.call('resume', run)
                            reopened.call('respond', {**run, 'effect_resolution': 'confirmed_safe', 'observation': {
                                'source': 'scripted operator', 'message': 'Checked the device',
                                'prior_execution_stopped': True}})
                            resume_when_idle(self, reopened, run)
                            recovered = wait_stopped(reopened, run)
                        self.assertEqual((recovered['status'], recovered['outcome_category'], recovered['reason']),
                                         ('completed', 'completed', 'Final verdict: equivalent'), recovered)
                        events = reopened.call('events', run)['events']
                        self.assertTrue(any(e['kind'] == 'run.recovered' and e['data'].get('final_verdict') for e in events))
                        self.assertEqual([e['kind'] for e in events].count('run.finalized'), 1)
                        self.assertEqual(len(final_verdicts(reopened, run)), 1)
                        with self.assertRaisesRegex(ValueError, 'Only a stopped incomplete run can resume'):
                            reopened.call('resume', run)
                    finally:
                        reopened.close()

    def test_a_sealing_or_cleanup_failure_after_the_verdict_keeps_the_verdict_and_the_completed_status(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        for mode in ('cleanup', 'release', 'uncertain'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                def fail_to_release(run_id, spec, run_dir):
                    if mode == 'release':
                        # The configurator seals nothing: the harness does. Releasing a provided device can still fail.
                        return [('device.release_failed', 'DeviceUnavailable: scripted', None)]
                    if mode == 'uncertain':
                        with controller._db() as db:
                            db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run_id,))
                    # After the verdict the configurator owns no device resource: generic runs own nothing, and the
                    # bench harness, not the configurator, stops and seals a provided device.
                    raise RuntimeError('scripted release failure')
                try:
                    with self.finishing(controller), patch.object(controller, '_release', side_effect=fail_to_release):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertTrue(result['equivalence']['equivalent'], result)
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                    kind = 'device.release_failed' if mode == 'release' else 'cleanup.failed'
                    failed = [e['data'] for e in controller.call('events', run)['events'] if e['kind'] == kind]
                    self.assertEqual(len(failed), 1)
                    self.assertIn('DeviceUnavailable' if mode == 'release' else 'scripted release failure', failed[0]['reason'])
                    with controller._db() as db:
                        leased = db.execute('SELECT COUNT(*) FROM bindings WHERE run_id=?', (run['run_id'],)).fetchone()[0]
                    if mode == 'uncertain':
                        # An uncertain effect still blocks for safety and keeps the lease; the verdict stays recorded.
                        self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'safety'), result)
                        self.assertIn('uncertain', result['reason'])
                        self.assertEqual(leased, 1)
                    else:
                        self.assertEqual((result['status'], result['outcome_category'], result['reason']),
                                         ('completed', 'completed', 'Final verdict: equivalent'), result)
                        self.assertFalse(result['uncertain_effect'])
                        self.assertEqual(leased, 0)
                finally:
                    controller.close()

    def test_a_host_fault_while_finalizing_stops_at_finalize_and_resume_grades_once(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller):
                    with patch('generative_driver.configurator._self_evidence', side_effect=RuntimeError('scripted host fault')):
                        run = self.start(controller)
                        stopped = wait_stopped(controller, run)
                    self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']), ('failed', 'host', 'finalize'), stopped)
                    self.assertEqual(stopped['progress']['next_stage'], 'finalize')
                    self.assertIsNone(stopped['equivalence'])
                    self.assertEqual(final_verdicts(controller, run), [])
                    resume_when_idle(self, controller, run)
                    result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['reason']), ('completed', 'Final verdict: equivalent'), result)
                events = controller.call('events', run)['events']
                resumed = max(i for i, e in enumerate(events) if e['kind'] == 'run.resumed')
                self.assertFalse(any(e['kind'] == 'stage.assigned' for e in events[resumed:]))
                self.assertEqual([e['kind'] for e in events].count('run.finalized'), 1)
                self.assertEqual(len(final_verdicts(controller, run)), 1)
            finally:
                controller.close()

    def test_a_host_failure_reading_the_frozen_package_at_finalize_stops_for_the_host_and_resume_grades_once(self):
        """Finalize reads the frozen package's driver and manifest to judge fresh reuse. A host that cannot read them
        says nothing about the driver: the run stops for the host at finalize, records no verdict, and resume grades it."""
        from unittest.mock import patch
        import interface_runtime.package as runtime_package
        from generative_driver import configurator
        real_self_evidence, real_digest = configurator._self_evidence, runtime_package.digest
        attempts = []
        def unreadable(path):
            # The host refuses to read the frozen driver, which the configurator reads through the runtime's check.
            if Path(path).parent.name == 'drivers':
                raise PermissionError(13, 'Permission denied', str(path))
            return real_digest(path)
        def self_evidence(*args, **kwargs):
            attempts.append(1)
            if len(attempts) > 1:
                return real_self_evidence(*args, **kwargs)
            with patch.object(runtime_package, 'digest', unreadable):
                return real_self_evidence(*args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            controller = configurator.Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller), patch('generative_driver.configurator._self_evidence', self_evidence):
                    run = self.start(controller)
                    stopped = wait_stopped(controller, run)
                    self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']),
                                     ('failed', 'host', 'finalize'), stopped)
                    self.assertIn('PermissionError', stopped['reason'])
                    self.assertIsNone(stopped['equivalence'])
                    self.assertEqual(final_verdicts(controller, run), [])
                    resume_when_idle(self, controller, run)
                    result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['reason']), ('completed', 'Final verdict: equivalent'), result)
                self.assertEqual((len(attempts), len(final_verdicts(controller, run))), (2, 1))
            finally:
                controller.close()

    def test_the_frozen_package_facts_raise_only_for_a_host_failure_and_only_when_read_strictly(self):
        """The facts of a frozen package are read through the runtime's check of it: its tools, each with its operation,
        and its functions, which are its operations beyond identity with a step that reaches the device. A package the
        check refuses, such as one whose driver is absent or does not parse, gives none of them (None) even when read
        strictly, as finalize reads it; a host failure to read a file raises there, and reads as None where the reuse
        notes read it."""
        from unittest.mock import patch
        import interface_runtime.package as runtime_package
        from generative_driver.configurator import _frozen_package, digest
        with tempfile.TemporaryDirectory() as directory:
            package = thermometer_package(Path(directory), operations={
                'identify': {'effect': 'read'}, 'read_temperature': {'effect': 'read'}, 'set_rate': {'effect': 'write'},
                'settle': {'effect': 'read', 'steps': [{'op': 'delay', 'ms': 1}]}})
            frozen = {'path': str(package), 'sha256': digest(package)}
            facts = _frozen_package(frozen, strict=True)
            self.assertEqual((facts['identity'], facts['functions']), ('identify', ['read_temperature', 'set_rate']))
            self.assertEqual(facts['effects'],
                             {'identify': 'read', 'read_temperature': 'read', 'set_rate': 'write', 'settle': 'read'})
            self.assertEqual(facts['tools'], {'scripted_thermometer_' + name: name for name in facts['effects']})
            self.assertEqual((facts['sha256'], facts['manifest_sha256'], facts['resets_on_open']),
                             (frozen['sha256'], digest(package / 'manifest.json'), False))
            self.assertEqual((facts['probed_operation'], facts['replayed_function']), ('read_temperature', True))
            # Nothing of a package's own command line or of an earlier format is among the facts.
            self.assertEqual(set(facts), {'sha256', 'manifest_sha256', 'tools', 'identity', 'effects', 'resets_on_open',
                                          'functions', 'probed_operation', 'replayed_function'})
            real_digest = runtime_package.digest
            def unreadable(path):
                if Path(path).parent.name == 'drivers':
                    raise PermissionError(13, 'Permission denied', str(path))
                return real_digest(path)
            with patch.object(runtime_package, 'digest', unreadable):
                self.assertIsNone(_frozen_package(frozen)['identity'])
                with self.assertRaises(PermissionError):
                    _frozen_package(frozen, strict=True)
            driver = package / 'drivers' / 'scripted_thermometer.json'
            for name, change in (('unparseable', lambda: driver.write_text('{"identity": ')),
                                 ('absent', driver.unlink)):
                with self.subTest(driver=name):
                    change()
                    facts = _frozen_package(frozen, strict=True)
                    self.assertEqual((facts['identity'], facts['effects'], facts['functions'], facts['tools']),
                                     (None, None, None, None))
                    self.assertEqual(facts['manifest_sha256'], digest(package / 'manifest.json'))

    def test_the_configurator_reads_a_model_as_the_runtime_does(self):
        """The runtime reads a model with its strict bounded reader, which accepts a UTF-8 byte-order mark (as
        PowerShell 5.1 writes) or UTF-16. The configurator's readers of a frozen package and of a dispatched model read
        the same bytes the same way, so such a model keeps its identity, functions, effects and channel."""
        from generative_driver.configurator import _frozen_package, _model_channel, digest, operation_effects
        model = thermometer_model()
        model['channel'] = {'type': 'uart', 'baudrate': 115200, 'resets_on_open': True}
        for encoding, encoded in (('UTF-8 with a byte-order mark', lambda text: b'\xef\xbb\xbf' + text.encode('utf-8')),
                                  ('UTF-16', lambda text: text.encode('utf-16'))):
            with self.subTest(encoding), tempfile.TemporaryDirectory() as directory:
                # A package whose driver file holds the same model in that encoding, under the hash of those bytes.
                package = thermometer_package(Path(directory), channel=model['channel'])
                driver = package / 'drivers' / 'scripted_thermometer.json'
                driver.write_bytes(encoded(driver.read_text(encoding='utf-8')))
                manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
                manifest['files']['drivers/scripted_thermometer.json'] = digest(driver)
                (package / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                facts = _frozen_package({'path': str(package), 'sha256': digest(package)}, strict=True)
                self.assertEqual((facts['identity'], facts['functions'], facts['effects'], facts['resets_on_open']),
                                 ('identify', ['read_temperature'], {'identify': 'read', 'read_temperature': 'read'}, True))
                # The gateway's readers of a model that a worker's call names, in a folder of its own.
                named = Path(directory) / 'named'; (named / 'driver').mkdir(parents=True)
                (named / 'driver' / 'model.json').write_bytes(encoded(json.dumps(model)))
                arguments = {'model_dir': str(named / 'driver'), 'operation': 'read_temperature'}
                self.assertEqual(operation_effects('interface_execute', arguments), {'read'})
                live_test = {'package_dir': str(named), 'operations': ['identify']}
                self.assertEqual(operation_effects('emit_test_live', live_test), {'read'})
                self.assertEqual(_model_channel('interface_execute', arguments)['resets_on_open'], True)
                self.assertEqual(_model_channel('emit_test_live', {'package_dir': str(named)})['type'], 'uart')

    def test_a_run_stopped_at_finalize_resumes_to_its_verdict_without_budget_binding_or_assumed_effects(self):
        """Finalize only reads the configurator's records and contacts no device. A generic run that stopped there
        before its verdict (a host fault, or a configurator restart) resumes to it with the budget lapsed and the
        device leased to another run. Restart recovery assumes no outstanding effect from its write grant: reuse
        was accepted, so nothing is dispatched."""
        from unittest.mock import patch
        from generative_driver.configurator import Controller, SafetyError
        for stop in ('host fault', 'restart'):
            with self.subTest(stop=stop), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / 'home'
                controller = Controller(home)
                fault = RuntimeError('scripted host fault') if stop == 'host fault' else SystemExit('scripted configurator death')
                try:
                    with self.finishing(controller), \
                         patch('generative_driver.configurator._self_evidence', side_effect=fault):
                        run = self.start(controller, effects=['read', 'write'])
                        wait_worker_exit(controller, run)
                    stopped = controller.call('result', run)
                    self.assertEqual(stopped['status'], 'failed' if stop == 'host fault' else 'running', stopped)
                    if stop == 'restart':
                        controller.close()
                        controller = Controller(home)
                        stopped = controller.call('result', run)
                        self.assertEqual((stopped['status'], stopped['reason']), ('blocked', 'Configurator restarted; explicit resume required'))
                    self.assertEqual((stopped['stage'], stopped['progress']['next_stage'], stopped['uncertain_effect']),
                                     ('finalize', 'finalize', False), stopped)
                    self.assertEqual(final_verdicts(controller, run), [])
                    with controller._db() as db:
                        # The wall-clock budget has lapsed, and another run now leases the device.
                        db.execute('UPDATE runs SET created=created-? WHERE id=?', (10 * 86400, run['run_id']))
                    with patch.object(controller, '_claim_binding', side_effect=SafetyError('Device binding is already owned by another run')):
                        resume_when_idle(self, controller, run)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['reason']), ('completed', 'Final verdict: equivalent'), result)
                    events = controller.call('events', run)['events']
                    resumed = max(i for i, e in enumerate(events) if e['kind'] == 'run.resumed')
                    self.assertFalse(any(e['kind'] == 'stage.assigned' for e in events[resumed:]))
                    self.assertEqual([e['kind'] for e in events].count('run.finalized'), 1)
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                finally:
                    controller.close()


class StageBoundaryTests(unittest.TestCase):
    """One stage at a time: a gateway call dispatches only while its assignment is the run's active one, and the
    configurator observes a stage only after every call of it has left the gateway."""

    def emit_run(self, work, call_tool):
        """A generic run whose stages are scripted except emit's real checks; `work(controller, request, call)` is the
        emit worker, where `call(name, arguments)` sends a gateway call. Returns the stopped result and the events."""
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            real_check = controller._check
            def check(spec, stage, run_dir, workspace, report, accepted, assignment):
                if stage == 'emit':
                    return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
                return {'ok': True, 'artifacts': [], 'checks': []}
            def prepare(original, spec, stage, run_dir, workspace, accepted):
                return original(spec, stage, run_dir, workspace, accepted) if stage == 'emit' else {'inputs': [], 'allowed_tools': []}
            def staged(request):
                if request.stage == 'emit':
                    call = lambda name, arguments: controller.call('tool', {**gateway_identity(request), 'name': name,
                                                                            'arguments': arguments})
                    work(controller, request, call)
            try:
                with scripted_stages(controller, check=check, prepare=prepare, work=staged), \
                        patch('generative_driver.toolkit.call_tool', side_effect=call_tool):
                    run = controller.call('start', {'goal': 'Scripted stage boundary; no model or device',
                                                    'executor_config': {'command': ['never-launched']}})
                    result = wait_stopped(controller, run)
                return result, controller.call('events', run)['events']
            finally:
                controller.close()

    def test_a_call_whose_assignment_was_accepted_before_it_was_marked_is_refused(self):
        # Catches a dispatch checked only on arrival: the stage's handoff committed while the call was on its way to
        # being marked, so the call ran against a closed assignment, outside the stage that observed it.
        from unittest.mock import patch
        from generative_driver import configurator
        real_digests, outcome = configurator._argument_digests, []
        def work(controller, request, call):
            assignment = gateway_identity(request)['assignment_id']
            def accepted_meanwhile(arguments):
                with controller._db() as db:
                    db.execute("UPDATE assignments SET state='accepted' WHERE id=?", (assignment,))
                return real_digests(arguments)
            with patch.object(configurator, '_argument_digests', side_effect=accepted_meanwhile):
                try:
                    call('emit_package', {'model_dir': str(Path(request.workspace) / 'model'),
                                          'probe': str(Path(request.workspace) / 'probe.json')})
                except ValueError as error:
                    outcome.append(str(error))
            with controller._db() as db:
                db.execute("UPDATE assignments SET state='active' WHERE id=?", (assignment,))
        def call_tool(name, arguments):
            raise AssertionError('dispatched ' + name)
        result, events = self.emit_run(work, call_tool)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(len(outcome), 1)
        self.assertIn('inactive', outcome[0])
        self.assertFalse([e for e in events if e['kind'] == 'tool.started'])
        self.assertEqual([e['data']['name'] for e in events if e['kind'] == 'tool.refused'], ['emit_package'])

    def test_a_stage_is_observed_only_after_its_calls_in_the_gateway_have_finished(self):
        # Catches a stage observed and accepted while a call of its worker is still in the gateway (a worker that gave
        # up waiting on a slow call and exited, with another queued behind it): that call's effect would be missing
        # from the stage's observation and land against a closed assignment.
        import threading
        slow, entered = threading.Event(), threading.Event()
        def work(controller, request, call):
            workspace = Path(request.workspace)
            arguments = {'model_dir': str(workspace / 'model'), 'probe': str(workspace / 'probe.json')}
            def attempt():
                try:
                    call('emit_package', arguments)
                except ValueError:
                    pass
            threading.Thread(target=attempt, daemon=True).start()
            self.assertTrue(entered.wait(5))
            threading.Thread(target=attempt, daemon=True).start()
            threading.Timer(.5, slow.set).start()
        def call_tool(name, arguments):
            if name == 'emit_package':
                entered.set()
                slow.wait(5)
            return {'ok': False, '_exit': 1, 'error': 'scripted: nothing packaged'}
        result, events = self.emit_run(work, call_tool)
        self.assertEqual(result['status'], 'completed', result)
        accepted = next(e['id'] for e in events if e['kind'] == 'stage.accepted' and e['data']['stage'] == 'emit')
        emit = next(e['data']['id'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'emit')
        calls = [e for e in events if e['kind'] in ('tool.started', 'tool.finished', 'tool.refused')
                 and e['data'].get('assignment_id') == emit]
        self.assertTrue([e for e in calls if e['kind'] == 'tool.finished'])
        self.assertTrue(all(e['id'] < accepted for e in calls if e['kind'] != 'tool.refused'), calls)

    def test_a_client_that_sees_a_stage_assigned_or_skipped_reads_that_stage_in_the_status(self):
        # Catches a stage's assignment (or skip) and the run's stage committed one after the other: a client that read
        # `events` and then `status` between the two saw the stage begin while the status named the previous stage.
        import threading
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            real_db, reading, run = controller._db, threading.local(), {}
            stale, open_stages = [], set()
            def read_as_a_client():
                events = controller.call('events', run)['events']
                status = controller.call('status', run)
                begun = [e for e in events if e['kind'] in ('stage.assigned', 'stage.skipped')]
                if not begun or any(e['kind'] == 'stage.accepted' and e['id'] > begun[-1]['id'] for e in events):
                    return
                stage = begun[-1]['data']['stage']
                open_stages.add(stage)
                if status['stage'] != stage:
                    stale.append((stage, status['stage']))
            @contextlib.contextmanager
            def committing():
                with real_db() as db:
                    yield db
                # The transaction has committed: read the run the way any client may read it now.
                if run and not getattr(reading, 'active', False):
                    reading.active = True
                    try:
                        read_as_a_client()
                    finally:
                        reading.active = False
            def prepare(original, spec, stage, *_):
                return {'skip': 'scripted: nothing to ground'} if stage == 'ground' else {'inputs': [], 'allowed_tools': []}
            try:
                with scripted_stages(controller, check=lambda *_: {'ok': True, 'artifacts': [], 'checks': []},
                                     prepare=prepare), \
                        unittest.mock.patch.object(controller, '_db', committing):
                    run.update(controller.call('start', {'goal': 'Scripted stages; no model or device',
                                                         'executor_config': {'command': ['never-launched']}}))
                    result = wait_stopped(controller, run)
                events = controller.call('events', run)['events']
            finally:
                controller.close()
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([e['data']['stage'] for e in events if e['kind'] == 'stage.skipped'], ['ground'])
        self.assertEqual(open_stages, {'acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'})
        self.assertEqual(stale, [])


class GenericEmitInputTests(unittest.TestCase):
    """Generic emit receives an interpreted model and a successful probe record that model wrote, from probe or ground,
    as the configurator bound each record to its writer in the handoff notes."""

    def setUp(self):
        from generative_driver.configurator import Controller
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.controller = Controller(self.root / 'home')
        self.addCleanup(self.controller.close)

    def model(self, revision, name):
        from generative_driver.configurator import digest
        folder = self.root / 'accepted' / str(revision) / 'interpret' / 'model'
        folder.mkdir(parents=True)
        (folder / 'model.json').write_text(json.dumps(thermometer_model(name)))
        return {'stage': 'interpret', 'revision': revision, 'assignment_id': 'i' + str(revision),
                'artifacts': [{'path': str(folder), 'sha256': digest(folder), 'kind': 'directory'}]}

    def records(self, stage, revision, *written):
        """A probe or ground handoff of records, each (name, ok, writer model's digest), bound in its notes."""
        folder = self.root / 'accepted' / str(revision) / stage
        folder.mkdir(parents=True, exist_ok=True)
        artifacts, bound = [], []
        for name, ok, writer in written:
            path = folder / name
            path.write_text(json.dumps({'schema': 'interface-probe/1', 'ok': ok, 'results': [], 'sample': name}))
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            artifacts.append({'path': str(path), 'sha256': sha, 'kind': 'file'})
            bound.append({'name': name, 'sha256': sha, 'model_sha256': writer})
        return {'stage': stage, 'revision': revision, 'assignment_id': stage[0] + str(revision), 'artifacts': artifacts,
                'notes': {'inline': {'configurator': {'records': bound}}}}

    def prepared(self, accepted):
        workspace = self.root / 'work' / str(len(list(self.root.glob('work/*'))))
        workspace.mkdir(parents=True)
        prepared = self.controller._prepare({'goal': 'g', 'effects': ['read']}, 'emit', self.root / 'run', workspace, accepted)
        names = [Path(path).name.split('-', 1)[1] for path in prepared['inputs']]
        shas = [Path(path).read_bytes() if Path(path).is_file() else json.loads((Path(path) / 'model.json').read_text())['device']['name']
                for path in prepared['inputs']]
        return names, shas, prepared.get('input_notes', [])

    def test_emit_takes_the_latest_model_with_a_successful_record_it_wrote_from_probe_or_ground(self):
        first, second = self.model(0, 'first'), self.model(1, 'second')
        a, b = first['artifacts'][0]['sha256'], second['artifacts'][0]['sha256']
        # Revision 1's probe handed on nothing; its ground wrote a successful record of the second model. An older
        # probe's records, and records of other models, are not the second model's.
        accepted = [first, self.records('probe', 0, ('interface-1.json', True, a)), second,
                    self.records('probe', 1), self.records('ground', 1, ('interface-2.json', True, b),
                                                           ('interface-3.json', True, 'f' * 64))]
        names, held, notes = self.prepared(accepted)
        self.assertEqual(names, ['model', 'interface-2.json'])
        self.assertEqual(held[0], 'second')
        self.assertEqual(notes, [])

    def test_emit_falls_back_to_the_model_that_wrote_a_successful_record_and_notes_it(self):
        first, second = self.model(0, 'first'), self.model(1, 'second')
        a, b = first['artifacts'][0]['sha256'], second['artifacts'][0]['sha256']
        # The second model's only record failed: emit packages the first model with the record it wrote.
        accepted = [first, self.records('probe', 0, ('interface-1.json', True, a)), second,
                    self.records('probe', 1, ('interface-2.json', False, b))]
        names, held, notes = self.prepared(accepted)
        self.assertEqual((names, held[0]), (['model', 'interface-1.json'], 'first'))
        self.assertEqual(len(notes), 1)
        self.assertIn('latest interpreted model', notes[0])
        # No successful record at all: the latest model alone, with a note.
        names, held, notes = self.prepared([first, second, self.records('probe', 1, ('interface-2.json', False, b))])
        self.assertEqual((names, held), (['model'], ['second']))
        self.assertIn('No successful probe record', notes[0])


class RealToolVerdictTests(unittest.TestCase):
    """03-B6 on whole generic runs with a fictional line-protocol thermometer served on a localhost socket. The probe
    and emit workers call the real tools through the configurator's gateway. The fresh-reuse worker calls the generated
    tools of the package emit handed off, and each call runs the installed runtime over that package in a child
    process. No hardware and no model run: acquire, interpret and ground are scripted, and interpret hands on the
    thermometer model."""
    DEVICE = {b'ID': b'THERMO-1\n', b'T': b'21.5\n'}
    DRIVER = 'drivers/scripted_thermometer.json'
    EVIDENCE = 'evidence/scripted_thermometer.probe.json'
    IDENTITY_ONLY = ('fresh reuse ran no function of the frozen package beyond its identity operation; identity alone '
                     "is not evidence of function, nor is an operation that only waits or repeats identity's requests")

    @staticmethod
    def tool(operation):
        """The name of the generated tool of `operation` of the thermometer's package."""
        return 'scripted_thermometer_' + operation

    def run_generic(self, home, reuse, *, emit=None, replies=None, on_emit_check=None, resume=False, model=None,
                    effects=None, emit_report=None, emit_runtime=None, seen=None, probe_operation='read_temperature'):
        """`reuse(call, package, workspace)` is the fresh-reuse worker. Its `call(operation, **arguments)` calls the
        generated tool of that operation of the package it was assigned, through the gateway, and returns the result
        or the exception the gateway raised. Interpret hands on `model` (the thermometer by default).
        `emit(call, model, record, workspace)` returns the package the emit worker hands off; its `call(name,
        **arguments)` calls a tool of the kit. By default it packages the assigned model with the probe record handed
        on. `emit_report(package)` is the emit worker's report (None: one that names no artifact); by default it names
        the package with the configurator's digest. `emit_runtime(request)` may replace the emit runtime's whole
        result. `on_emit_check(attempt)` may return a context manager to run the configurator's own emit_check in.
        With `resume`, a run that stopped is resumed once. `effects` are the run's grants. The device appends each
        request line to `seen`. Returns the stopped result (or the first stop and the final result when resumed) and
        the events."""
        from unittest.mock import patch
        from generative_driver import toolkit
        from generative_driver.configurator import Controller, digest
        real_call_tool, packages, checks, requests = toolkit.call_tool, {}, [], []
        interpreted = model or thermometer_model()
        def call_tool(name, arguments):
            if name == 'emit_check' and Path(arguments['run_dir']).parent.name == 'checks' and on_emit_check:
                checks.append(name)
                with on_emit_check(len(checks)):
                    return real_call_tool(name, arguments)
            return real_call_tool(name, arguments)
        controller = Controller(home)
        real_check = controller._check
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage in ('probe', 'emit', 'reuse'):
                return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
            if stage == 'interpret':
                model = workspace / 'model'; model.mkdir()
                (model / 'model.json').write_text(json.dumps(interpreted))
                return {'ok': True, 'artifacts': [str(model)], 'checks': []}
            return {'ok': True, 'artifacts': [], 'checks': []}
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            if stage in ('probe', 'emit', 'reuse'):
                return original(spec, stage, run_dir, workspace, accepted)
            return {'inputs': [], 'allowed_tools': []}
        def work(request):
            inputs = [Path(p) for p in task_of(request.prompt)['inputs']]
            def call(name, **arguments):
                try:
                    return controller.call('tool', {**gateway_identity(request), 'name': name, 'arguments': arguments})
                except ValueError as error:
                    return error
            workspace = Path(request.workspace)
            if request.stage == 'probe':
                call('probe_run', model_dir=str(inputs[0]), operation=probe_operation, n=1)
            elif request.stage == 'emit':
                model = next(p for p in inputs if (p / 'model.json').is_file())
                record = next((p for p in inputs if p.suffix == '.json'), None)
                packages[request.workspace] = (emit or (lambda call, model, record, workspace: call(
                    'emit_package', model_dir=str(model), probe_json=str(record))['package_dir']))(call, model, record, workspace)
            elif request.stage == 'reuse':
                identity = gateway_identity(request)
                server = controller.call('mcp_tools', identity)['server']
                def generated(operation, **arguments):
                    requests.append(operation)
                    try:
                        return controller.call('mcp_tool', {**identity, 'name': server + '_' + operation,
                                                            'arguments': arguments,
                                                            'request_id': 'call-' + str(len(requests))})
                    except (ValueError, OSError) as error:
                        return error
                reuse(generated, inputs[0], workspace)
        def report(request):
            if request.stage == 'emit':
                package = packages[request.workspace]
                if emit_report:
                    return emit_report(package)
                return {'status': 'completed', 'summary': 'packaged', 'checks': [], 'unresolved': [],
                        'artifacts': [{'path': package, 'sha256': digest(package), 'kind': 'package'}]}
        def runtime(request):
            return emit_runtime(request) if emit_runtime and request.stage == 'emit' else None
        try:
            with line_device(replies if replies is not None else dict(self.DEVICE), seen) as binding, \
                    scripted_stages(controller, check=check, prepare=prepare, work=work, report=report, runtime=runtime), \
                    patch('generative_driver.toolkit.call_tool', call_tool):
                run = controller.call('start', {'goal': 'Real-tool verdict contract on a localhost line device; no model',
                                                'binding': binding, 'executor_config': {'command': ['never-launched']},
                                                **({'effects': effects} if effects else {})})
                result = wait_stopped(controller, run, 60)
                if resume:
                    first = result
                    resume_when_idle(self, controller, run)
                    result = (first, wait_stopped(controller, run, 60))
            return result, controller.call('events', run)['events']
        finally:
            controller.close()

    @staticmethod
    def outcomes(result):
        return [(o['name'], o['outcome']) for o in result['equivalence']['mission']['operations']]

    @staticmethod
    def reuse_note(result):
        reuse = next(h for h in result['accepted_handoffs'] if h['stage'] == 'reuse')
        return next(c for c in reuse['checks'] if c['name'] == 'fresh_package_execution')

    @staticmethod
    def reads(call, package, workspace):
        """The fresh-reuse worker that runs the frozen package's read once."""
        return call('read_temperature')

    def test_01_B11_while_a_run_is_uncertain_a_generated_write_is_refused_before_it_starts_and_reads_go_on(self):
        replies, seen, ran = {**self.DEVICE, b'R': b'OK\n'}, [], []
        def reuse(call, package, workspace):
            # From here on the device takes the write and never answers it.
            del replies[b'R']
            del seen[:]
            ran.extend([call('set_rate'), call('set_rate'), call('read_temperature')])
        with tempfile.TemporaryDirectory() as directory:
            result, events = self.run_generic(Path(directory) / 'home', reuse, replies=replies, seen=seen,
                                              model=DeviceRepairTests.writing_thermometer(), effects=['read', 'write'])
        lost, refused, read = ran
        self.assertEqual((lost['ok'], lost['requested_command_dispatched']), (False, True), lost)
        self.assertIsInstance(refused, ValueError, refused)
        self.assertEqual(str(refused), DeviceRepairTests.UNCERTAIN)
        self.assertEqual((read['ok'], read['outputs']), (True, {'temperature_c': 21.5}), read)
        # The refused write reached no device and is not recorded as started.
        self.assertEqual(seen, [b'ID', b'R', b'ID', b'T'])
        reused = lambda kind: [e['data'] for e in events if e['kind'] == kind and e['data']['stage'] == 'reuse']
        self.assertEqual([(e['name'], e['effectful']) for e in reused('tool.started')],
                         [(self.tool('set_rate'), True), (self.tool('read_temperature'), False)])
        self.assertEqual([(e['name'], e['reason']) for e in reused('tool.refused')],
                         [(self.tool('set_rate'), DeviceRepairTests.UNCERTAIN)])
        self.assertEqual((result['status'], result['outcome_category'], result['uncertain_effect']),
                         ('blocked', 'safety', True), result)

    def test_01_B5_a_generated_tool_call_is_recorded_as_started_and_as_finished_with_the_fields_of_the_journal(self):
        from generative_driver.configurator import digest
        ran, packages = [], []
        def reuse(call, package, workspace):
            packages.append(digest(package))
            ran.extend([call('set_rate'), call('read_temperature')])
        with tempfile.TemporaryDirectory() as directory:
            result, events = self.run_generic(Path(directory) / 'home', reuse, replies={**self.DEVICE, b'R': b'OK\n'},
                                              model=DeviceRepairTests.writing_thermometer(), effects=['read', 'write'])
        self.assertEqual(result['status'], 'completed', result)
        [assigned] = [e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'reuse']
        reused = lambda kind: [e for e in events if e['kind'] == kind and e['data']['stage'] == 'reuse']
        started, finished = reused('tool.started'), reused('tool.finished')
        self.assertEqual((len(started), len(finished)), (2, 2))
        for start, end, reply, operation, marked in zip(started, finished, ran, ('set_rate', 'read_temperature'),
                                                         (True, False)):
            with self.subTest(operation=operation):
                # The record's fields, and the facts of a generated call: the package that ran and its operation.
                recorded = {'stage': 'reuse', 'assignment_id': assigned['id'], 'name': self.tool(operation),
                            'actor': 'worker', 'effectful': marked, 'connected': True,
                            'argument_sha256': {'package_dir': packages[0]}, 'operation': operation, 'parameters': {}}
                self.assertEqual(start['data'], recorded)
                data = dict(end['data'])
                stored = data.pop('result')
                self.assertEqual(data, {**recorded, 'ok': True, 'artifacts': {}, 'dispatch_id': str(start['id']),
                                        'effects_reconciled': marked, 'refused_before_open': False})
                # The worker gets the recorded result with the ids of the run, the invocation and both events.
                self.assertEqual(stored['mcp']['dispatch_id'], str(start['id']))
                self.assertEqual(reply, {**stored, 'session_id': result['run_id'], 'request_id': reply['request_id'],
                                         'dispatch_id': str(start['id']), 'evidence_id': str(end['id'])})

    def test_01_B7_a_generated_write_whose_identity_read_failed_leaves_no_uncertain_effect(self):
        replies, seen, ran = {**self.DEVICE, b'R': b'OK\n'}, [], []
        def reuse(call, package, workspace):
            # From here on the device answers the identity read with another name.
            replies[b'ID'] = b'OTHER\n'
            del seen[:]
            ran.append(call('set_rate'))
        with tempfile.TemporaryDirectory() as directory:
            result, events = self.run_generic(Path(directory) / 'home', reuse, replies=replies, seen=seen,
                                              model=DeviceRepairTests.writing_thermometer(), effects=['read', 'write'])
        [write] = ran
        self.assertEqual((write['ok'], write['requested_command_dispatched'], write['transport_open_attempted']),
                         (False, False, True), write)
        # The write's own command never went out, so it left nothing uncertain and the run got its verdict.
        self.assertEqual(seen, [b'ID'])
        [finished] = [e['data'] for e in events if e['kind'] == 'tool.finished' and e['data']['stage'] == 'reuse']
        self.assertEqual((finished['effectful'], finished['effects_reconciled'], finished['refused_before_open']),
                         (True, True, False), finished)
        self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)

    def test_03_B6_fresh_reuse_must_run_a_function_of_the_frozen_package_beyond_its_identity(self):
        """Identity alone is not evidence of function: fresh reuse's mission report and reuse note require running,
        with a generated tool, at least one operation of the frozen package beyond its identity operation on the
        device, while the driver verdict judges the emitted package's own live replay."""
        for name, operations, mission_passed in (
                ('a function beyond identity', ['read_temperature'], True),
                ('the identity operation only', ['identify'], False),
                ('the identity operation, then a function', ['identify', 'read_temperature'], True)):
            with self.subTest(plan=name), tempfile.TemporaryDirectory() as directory:
                ran = []
                result, _ = self.run_generic(Path(directory) / 'home', lambda call, package, workspace: ran.extend(
                    call(operation) for operation in operations))
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([(r['operation'], r['ok']) for r in ran],
                                 [(operation, True) for operation in operations], ran)
                equivalence = result['equivalence']
                self.assertTrue(equivalence['equivalent'], equivalence)
                [replay] = equivalence['checks']
                live = equivalence['mission']
                self.assertTrue(replay['passed'] and replay['basis'].startswith(
                    'emit_check replay of the recorded device exchanges'), replay)
                self.assertEqual(live['passed'], mission_passed, live)
                self.assertEqual([(o['name'], o['outcome'], o['emitted_package']) for o in live['operations']],
                                 [(self.tool(operation), 'succeeded', True) for operation in operations])
                note = self.reuse_note(result)
                self.assertEqual(note['passed'], mission_passed, note)
                if not mission_passed:
                    self.assertEqual(live['diff'],
                                     [{'id': 'fresh_reuse_live_operations', 'reason': self.IDENTITY_ONLY}])
                    self.assertEqual(note['reason'], self.IDENTITY_ONLY)

    def test_an_operation_that_only_waits_is_no_function_of_the_frozen_package(self):
        """A settle operation of delays alone sends nothing to the device once identity is verified, so probing or
        running it alone is running identity alone: not equivalent. Running or probing a function that exchanges with
        the device is."""
        from generative_driver.configurator import REPLAY_IDENTITY_ONLY
        model = thermometer_model()
        model['operations']['settle'] = {'effect': 'read', 'parameters': {}, 'outputs': {}, 'steps': [{'op': 'delay', 'ms': 1}]}
        model['provenance'].append({'item': 'operations.settle', 'source': 'scripted fixture'})
        for operations, equivalent in ((['settle'], False), (['settle', 'read_temperature'], True)):
            with self.subTest(operations=operations), tempfile.TemporaryDirectory() as directory:
                ran = []
                result, _ = self.run_generic(Path(directory) / 'home', lambda call, package, workspace: ran.extend(
                    call(operation) for operation in operations), model=model,
                    probe_operation=operations[-1])
                self.assertEqual(result['status'], 'completed', result)
                self.assertEqual([r['operation'] for r in ran if r['ok']], operations, ran)
                equivalence = result['equivalence']
                self.assertEqual(equivalence['equivalent'], equivalent, equivalence)
                self.assertEqual(self.outcomes(result),
                                 [(self.tool(operation), 'succeeded') for operation in operations])
                self.assertEqual(equivalence['diff'], [] if equivalent else [
                    {'id': 'emitted_package_replay', 'reason': REPLAY_IDENTITY_ONLY}])
                self.assertEqual(equivalence['mission']['passed'], equivalent)
                self.assertEqual(equivalence['mission']['diff'], [] if equivalent else [
                    {'id': 'fresh_reuse_live_operations', 'reason': self.IDENTITY_ONLY}])
                self.assertEqual(self.reuse_note(result)['passed'], equivalent)

    def test_an_operation_that_repeats_the_identity_request_is_no_function_of_the_frozen_package(self):
        """get_chip_id sends the device what identify sends: running or probing it on the device asks nothing identity
        does not, so it is no function, whatever it accepts back (another capture name or timeout, no expectation, a
        looser one or another receive bound). Running and probing a function that sends something else is equivalent."""
        from generative_driver.configurator import REPLAY_IDENTITY_ONLY, _frozen_package, digest
        identify = thermometer_model()['operations']['identify']['steps'][0]
        unexpecting = {key: value for key, value in identify.items() if key != 'expect'}
        for alias_name, alias in (
                ('another capture name and timeout', dict(identify, capture='chip', timeout_ms=500)),
                ('no expectation', dict(unexpecting, capture='chip')),
                ('a looser expectation', dict(identify, capture='chip', expect={'contains_text': 'T'})),
                ('another receive bound', dict(unexpecting, capture='chip', rx={'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 32}))):
            model = thermometer_model()
            model['operations']['get_chip_id'] = {'effect': 'read', 'parameters': {}, 'outputs': {}, 'steps': [alias]}
            model['provenance'].append({'item': 'operations.get_chip_id', 'source': 'scripted fixture'})
            for operations, equivalent in ((['get_chip_id'], False), (['get_chip_id', 'read_temperature'], True)):
                with self.subTest(alias=alias_name, operations=operations), tempfile.TemporaryDirectory() as directory:
                    ran, frozen = [], []
                    def reuse(call, package, workspace):
                        frozen.append(_frozen_package({'path': str(package), 'sha256': digest(package)}, strict=True))
                        ran.extend(call(operation) for operation in operations)
                    result, _ = self.run_generic(Path(directory) / 'home', reuse, model=model,
                                                 probe_operation=operations[-1])
                    self.assertEqual(result['status'], 'completed', result)
                    self.assertEqual(frozen[0]['functions'], ['read_temperature'])
                    self.assertEqual([r['operation'] for r in ran if r['ok']], operations, ran)
                    equivalence = result['equivalence']
                    self.assertEqual(equivalence['equivalent'], equivalent, equivalence)
                    self.assertEqual(self.outcomes(result),
                                     [(self.tool(operation), 'succeeded') for operation in operations])
                    self.assertEqual(equivalence['diff'], [] if equivalent else [
                        {'id': 'emitted_package_replay', 'reason': REPLAY_IDENTITY_ONLY}])
                    self.assertEqual(equivalence['mission']['passed'], equivalent)
                    self.assertEqual(equivalence['mission']['diff'], [] if equivalent else [
                        {'id': 'fresh_reuse_live_operations', 'reason': self.IDENTITY_ONLY}])
                    self.assertEqual(self.reuse_note(result)['passed'], equivalent)

    def test_03_B6_a_call_the_runtime_refused_before_it_opened_the_device_is_not_a_live_operation(self):
        """The runtime of the frozen package refuses arguments an operation does not take before it opens the device.
        Such a generated call is not a live operation: fresh reuse that then runs a function that passes is
        equivalent, and the reuse note judges the attempt by the same rule."""
        seen, ran, sent = [], [], []
        def reuse(call, package, workspace):
            sent.append(len(seen))
            ran.append(call('read_temperature', unexpected=1))
            sent.append(len(seen))
            ran.append(call('read_temperature'))
        with tempfile.TemporaryDirectory() as directory:
            result, events = self.run_generic(Path(directory) / 'home', reuse, seen=seen)
        refused, passed = ran
        self.assertEqual((refused['ok'], refused['transport_open_attempted']), (False, False), refused)
        self.assertEqual(sent[0], sent[1], 'the device received a request of the refused call')
        self.assertTrue(passed['ok'], passed)
        self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', True),
                         result['equivalence'])
        outcomes = [(self.tool('read_temperature'), 'refused'), (self.tool('read_temperature'), 'succeeded')]
        self.assertEqual(self.outcomes(result), outcomes)
        finished = [e['data'] for e in events if e['kind'] == 'tool.finished' and e['data']['stage'] == 'reuse']
        self.assertEqual([e['refused_before_open'] for e in finished], [True, False])
        note = self.reuse_note(result)
        self.assertEqual((note['passed'], [(o['name'], o['outcome']) for o in note['operations']]), (True, outcomes))

    def test_03_B6_a_package_the_worker_swaps_in_never_runs(self):
        """A generated call runs the package the gateway assigned, by the hashes it recorded. A worker that swaps
        another package that is consistent in itself into its place, here one whose read_temperature became a heater
        command, never has it run. Before a call, the gateway refuses the changed input and dispatches nothing. During
        a call, after the gateway read the package, the copy the child would run is checked against the frozen manifest
        and refused: that dispatch has no recorded result, which leaves the verdict unscored. The stand-in swaps the
        package at the moment named and puts the frozen bytes back afterwards."""
        from unittest.mock import patch
        from generative_driver.device_access import DeviceAccess
        from interface_runtime.package import package_files, verify_package
        read = self.tool('read_temperature')
        def swapped(package):
            """Swap the heater package into `package`; returns the call that puts the frozen bytes back."""
            kept = {name: (package / name).read_bytes() for name in (self.DRIVER, 'manifest.json')}
            model = json.loads(kept[self.DRIVER])
            model['operations']['read_temperature'] = {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [
                {'op': 'exchange', 'tx': [{'text': 'H1\n'}], 'timeout_ms': 1000,
                 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64}}]}
            (package / self.DRIVER).write_text(json.dumps(model))
            manifest = json.loads(kept['manifest.json'])
            manifest['files'] = package_files(package)
            (package / 'manifest.json').write_text(json.dumps(manifest))
            self.assertTrue(verify_package(package)['ok'], 'the package swapped in is not consistent in itself')
            return lambda: [(package / name).write_bytes(data) for name, data in kept.items()]
        real_invoke = DeviceAccess.invoke_package
        for when in ('before the call', 'during the call'):
            seen, ran, during = [], [], []
            def reuse(call, package, workspace):
                if when == 'before the call':
                    restore = swapped(package)
                    ran.append(call('read_temperature'))
                    restore()
                else:
                    during.append(True)
                    ran.append(call('read_temperature'))
                ran.append(call('read_temperature'))
            def invoke(access, package, *rest):
                if not during:
                    return real_invoke(access, package, *rest)
                during.clear()
                restore = swapped(Path(package))
                try:
                    return real_invoke(access, package, *rest)
                finally:
                    restore()
            with self.subTest(swapped=when), tempfile.TemporaryDirectory() as directory, \
                    patch.object(DeviceAccess, 'invoke_package', invoke):
                result, events = self.run_generic(Path(directory) / 'home', reuse, effects=['read', 'write'],
                                                  replies={**self.DEVICE, b'H1': b'OK\n'}, seen=seen)
                # The heater command never reached the device, and no write was marked uncertain.
                self.assertNotIn(b'H1', seen)
                self.assertEqual((result['status'], result['uncertain_effect']), ('completed', False), result)
                refusal, passed = ran
                self.assertIsInstance(refusal, ValueError, ran)
                self.assertTrue(passed['ok'], passed)
                refused = [e['data'] for e in events if e['kind'] == 'tool.refused' and e['data']['stage'] == 'reuse']
                abandoned = [e['data'] for e in events if e['kind'] == 'tool.abandoned']
                mission = result['equivalence']['mission']
                reasons = [d['reason'] for d in mission['diff']]
                self.assertTrue(result['equivalence']['equivalent'], result['equivalence'])
                if when == 'before the call':
                    self.assertEqual([(e['name'], e['reason']) for e in refused], [(read, str(refusal))])
                    self.assertIn('an assigned input was altered', str(refusal))
                    self.assertEqual((abandoned, self.outcomes(result)), ([], [(read, 'succeeded')]))
                    self.assertEqual((mission['passed'], reasons), (True, []))
                else:
                    self.assertIn('manifest pin mismatch', str(refusal))
                    self.assertEqual((refused, [e['name'] for e in abandoned]), ([], [read]))
                    self.assertEqual(self.outcomes(result), [(read, 'unresolved'), (read, 'succeeded')])
                    self.assertEqual((mission['passed'], reasons), (None, [
                        read + ' was dispatched but its result was never recorded (dispatch '
                        + abandoned[0]['dispatch_id'] + ')']))

    def test_03_B6_a_host_failure_before_the_child_starts_leaves_the_call_unrecorded_and_the_verdict_unscored(self):
        """A generated call runs a checked copy of the frozen package. When the host cannot read that copy back, nothing
        reached the device and the dispatch ends without a recorded result: fresh reuse's mission report is unsettled,
        never a refusal or a driver failure."""
        from unittest.mock import patch
        import interface_runtime.package as runtime_package
        read = self.tool('read_temperature')
        seen, ran, sent = [], [], []
        def reuse(call, package, workspace):
            real_digest, own = runtime_package.digest, package.resolve()
            def locked(path):
                if Path(path).name == Path(self.DRIVER).name and not Path(path).resolve().is_relative_to(own):
                    raise PermissionError(13, 'The process cannot access the file because it is being used by another '
                                          'process', str(path))
                return real_digest(path)
            sent.append(len(seen))
            with patch.object(runtime_package, 'digest', locked):
                ran.append(call('read_temperature'))
            sent.append(len(seen))
        with tempfile.TemporaryDirectory() as directory:
            result, events = self.run_generic(Path(directory) / 'home', reuse, seen=seen)
        self.assertIsInstance(ran[0], PermissionError, ran)
        self.assertEqual(sent[0], sent[1], 'the device received a request of the call that never started')
        self.assertEqual((result['status'], result['uncertain_effect'], result['equivalence']['equivalent']),
                         ('completed', False, True), result)
        mission = result['equivalence']['mission']
        self.assertIsNone(mission['passed'], mission)
        [abandoned] = [e['data'] for e in events if e['kind'] == 'tool.abandoned']
        self.assertEqual((abandoned['name'], abandoned['type']), (read, 'PermissionError'), abandoned)
        self.assertEqual(self.outcomes(result), [(read, 'unresolved')])
        self.assertEqual([d['reason'] for d in mission['diff']], [
            read + ' was dispatched but its result was never recorded (dispatch ' + abandoned['dispatch_id'] + ')'])

    def test_an_emit_check_the_host_could_not_run_stops_for_the_host_and_resumes(self):
        """A host fault in the configurator's own emit_check says nothing about the package: the run stops for the
        host at emit, is resumable, and is never judged not equivalent for it. The fault is the host's whether the
        check reports it (no temporary directory) or the toolchain fails around it (its own event log)."""
        from unittest.mock import patch
        faults = (('no temporary directory', 'FileNotFoundError',
                   lambda: patch.object(tempfile, 'tempdir', str(Path(tempfile.gettempdir()) / 'no such temporary directory'))),
                  ('the tool cannot write its event log', 'No space left on device',
                   lambda: patch('generative_driver._toolchain.core.log_event', side_effect=OSError(28, 'No space left on device'))))
        for name, says, fault in faults:
            with self.subTest(fault=name), tempfile.TemporaryDirectory() as directory:
                (stopped, result), _ = self.run_generic(Path(directory) / 'home', self.reads, resume=True,
                    on_emit_check=lambda attempt: fault() if attempt == 1 else contextlib.nullcontext())
                self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']), ('failed', 'host', 'emit'), stopped)
                self.assertIn('emit_check could not run', stopped['reason'])
                self.assertIn(says, stopped['reason'])
                self.assertIsNone(stopped['equivalence'])
                self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', True), result)

    def test_01_B7_emit_check_stops_for_budget_timeout_or_exit_and_rejects_on_memory_error(self):
        """Decision D1 / core-13: only MemoryError rejects a package; budget exhaustion stops for the operator,
        and a timeout or a process that ends without a result stops for the host so resume can check it again."""
        from unittest.mock import patch
        from generative_driver import tool_process
        from generative_driver.configurator import Controller
        # MemoryError from emit_check rejects the package without stopping for the host.
        with tempfile.TemporaryDirectory() as directory:
            result, events = self.run_generic(
                Path(directory) / 'home', self.reads,
                on_emit_check=lambda attempt: patch('generative_driver._toolchain.interface.emit_check',
                                                    side_effect=MemoryError('out of memory')))
            self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', False), result)
            self.assertFalse(any(e['kind'] == 'run.failed' for e in events))
            emitted = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
            check = next(c for c in emitted['checks'] if c['name'] == 'package_integrity_and_replay')
            self.assertFalse(check['passed'])
            self.assertIn('MemoryError', check['rejected'][0]['reason'])
        # Unfinished reasons through _kit_tool: budget -> operator stop; timeout / ToolProcessExit -> host stop.
        real_check_tool = Controller._check_tool
        for label, unfinished, expected_status, expected_category in (
                ('budget', tool_process.Unfinished('emit_check', 'budget'), 'blocked', 'operator'),
                ('timeout', tool_process.Unfinished('emit_check', 'timeout'), 'failed', 'host'),
                ('process exit', tool_process.Unfinished('emit_check', 'raised', 'ToolProcessExit', 'signal 9'),
                 'failed', 'host')):
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                calls = []
                def check_tool(controller, call_tool, run_id, stage, name, arguments):
                    if name == 'emit_check' and not calls:
                        calls.append(1)
                        with patch.object(controller, '_kit_tool', side_effect=unfinished):
                            return real_check_tool(controller, call_tool, run_id, stage, name, arguments)
                    return real_check_tool(controller, call_tool, run_id, stage, name, arguments)
                with patch.object(Controller, '_check_tool', check_tool):
                    (stopped, result), _ = self.run_generic(Path(directory) / 'home', self.reads, resume=True)
                self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']),
                                 (expected_status, expected_category, 'emit'), stopped)
                self.assertIsNone(stopped['equivalence'])
                self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', True), result)

    def test_a_hand_edited_package_is_rejected_and_never_stops_the_run_for_the_host(self):
        """Worker content never stops a run. A package the emit worker edited and listed again, however malformed, fails
        the configurator's emit_check and is dropped; and a file the worker leaves where a check folder might go cannot
        block the configurator's own check, which runs outside the worker's workspace."""
        from interface_runtime.package import package_files
        def edited(content):
            def emit(call, model, record, workspace):
                package = Path(call('emit_package', model_dir=str(model), probe_json=str(record))['package_dir'])
                (package / self.EVIDENCE).write_text(content(package))
                manifest = json.loads((package / 'manifest.json').read_text())
                manifest['files'] = package_files(package)
                (package / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
                # The worker checks its edit through the configurator, which hashes the package it is given.
                call('emit_check', package_dir=str(package))
                return str(package)
            return emit
        def unparsable_manifest(call, model, record, workspace):
            package = Path(call('emit_package', model_dir=str(model), probe_json=str(record))['package_dir'])
            (package / 'manifest.json').write_text('{"x": ' + '[' * 100000 + ']' * 100000 + '}')
            call('emit_check', package_dir=str(package))
            return str(package)
        def blocking(call, model, record, workspace):
            (workspace / 'checks').write_text('a file where a folder could go')
            return call('emit_package', model_dir=str(model), probe_json=str(record))['package_dir']
        nested = lambda package: ((package / self.EVIDENCE).read_text().rstrip()[:-1] + ', "x": ' + '[' * 100000
                                  + ']' * 100000 + '}')
        for name, emit, equivalent in (('a probe record that is a JSON array', edited(lambda package: '[1, 2]'), False),
                                       ('a probe record nested deeper than the parser allows', edited(nested), False),
                                       ('a manifest nested deeper than the parser allows', unparsable_manifest, False),
                                       ('a file where a check folder could go', blocking, True)):
            with self.subTest(package=name), tempfile.TemporaryDirectory() as directory:
                result, events = self.run_generic(Path(directory) / 'home', self.reads, emit=emit)
                self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', equivalent), result)
                self.assertFalse(any(e['kind'] == 'run.failed' for e in events), [e for e in events if e['kind'] == 'run.failed'])
                emitted = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
                check = next(c for c in emitted['checks'] if c['name'] == 'package_integrity_and_replay')
                self.assertEqual((check['passed'], 'rejected' in check), (equivalent, not equivalent), check)

    def test_emit_hands_off_the_package_the_configurator_saw_emit_package_build_whatever_the_report_claims(self):
        """The configurator's own record of emit_package decides which bytes are the driver: a report that gives the
        manifest hash, names nothing or is missing still hands off the package emit_package built, and reuse runs it.
        A report may name which package; it never supplies the hash."""
        from interface_runtime.package import digest as file_digest
        named = lambda package: {'status': 'completed', 'summary': 'packaged', 'checks': [], 'unresolved': [], 'artifacts': [
            {'path': package, 'sha256': file_digest(Path(package) / 'manifest.json'), 'kind': 'package'}]}
        silent = lambda request: {'runtime': 'codex', 'status': 'failed', 'cause': 'no_report', 'elapsed_seconds': 0,
                                  'usage': None, 'report': None, 'reason': 'Runtime did not produce a structured stage report'}
        def copied(call, model, record, workspace):
            built = call('emit_package', model_dir=str(model), probe_json=str(record))['package_dir']
            shutil.copytree(built, workspace / 'copy')
            return str(workspace / 'copy')
        for name, options in (('a report giving the manifest hash', {'emit_report': named}),
                              ('a report naming a copy of the build', {'emit': copied}),
                              ('a report naming no artifact', {'emit_report': lambda package: None}),
                              ('no report', {'emit_runtime': silent})):
            with self.subTest(report=name), tempfile.TemporaryDirectory() as directory:
                result, events = self.run_generic(Path(directory) / 'home', self.reads, **options)
                self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', True), result)
                emitted = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
                built = next(e['data'] for e in events if e['kind'] == 'tool.finished' and e['data']['name'] == 'emit_package')
                self.assertEqual([a['sha256'] for a in emitted['artifacts']], list(built['artifacts'].values()))
                self.assertFalse(emitted['notes']['inline']['configurator']['dropped_artifacts'])
                # Fresh reuse received that package.
                reused = [e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'reuse']
                self.assertEqual([list(a['inputs'].values()) for a in reused], [[emitted['artifacts'][0]['sha256']]])

    def test_a_package_changed_after_emit_package_returned_is_not_handed_off_and_the_verdict_says_so(self):
        """Bytes the configurator never observed are no driver it hands off, and the verdict names why rather than
        saying no driver was emitted."""
        def edited(call, model, record, workspace):
            package = Path(call('emit_package', model_dir=str(model), probe_json=str(record))['package_dir'])
            (package / 'README.md').write_text('rewritten after the tool returned')
            return str(package)
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_generic(Path(directory) / 'home', self.reads, emit=edited)
        self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', False), result)
        emitted = next(h for h in result['accepted_handoffs'] if h['stage'] == 'emit')
        self.assertEqual(emitted['artifacts'], [])
        check = next(c for c in emitted['checks'] if c['name'] == 'package_integrity_and_replay')
        self.assertEqual((check['passed'], check['candidates']), (False, 0))
        self.assertIn('changed after emit_package returned', json.dumps(check['not_handed_off']))
        self.assertEqual(result['equivalence']['diff'][0]['reason'],
                         'emitted package not handed off: changed after emit_package returned')

    def test_a_package_built_from_supplied_exchanges_is_no_replay_of_the_device(self):
        """A probe record made from a replay the worker supplied reached no device. Its package passes emit_check
        replay, but that is a replay of supplied exchanges, which Ruling 7 does not count."""
        from generative_driver.toolkit import call_tool
        rx = {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / 'model'; model.mkdir()
            (model / 'model.json').write_text(json.dumps(thermometer_model()))
            replay = root / 'replay.json'
            replay.write_text(json.dumps({'schema': 'interface-replay/1', 'transactions': [
                {'tx_hex': b'ID\n'.hex(), 'rx_hex': b'THERMO-1\n'.hex(), 'rx': rx, 'timeout_ms': 1000},
                {'tx_hex': b'T\n'.hex(), 'rx_hex': b'21.5\n'.hex(), 'rx': rx, 'timeout_ms': 1000}]}))
            supplied = call_tool('probe_run', {'run_dir': str(root / 'supplied'), 'model_dir': str(model),
                                               'operation': 'read_temperature', 'n': 1, 'replay': str(replay)})
            self.assertEqual((supplied['_exit'], supplied['evidence_kind']), (0, 'replay'), supplied)
            def emit(call, model, record, workspace):
                own = workspace / 'supplied-probe.json'; shutil.copyfile(supplied['probe'], own)
                return call('emit_package', model_dir=str(model), probe_json=str(own))['package_dir']
            result, _ = self.run_generic(root / 'home', self.reads, emit=emit)
        equivalence = result['equivalence']
        self.assertEqual((result['status'], equivalence['equivalent']), ('completed', False), result)
        [replay_check] = equivalence['checks']
        operations = equivalence['mission']
        self.assertEqual((replay_check['passed'], operations['passed']), (False, True))
        kind = 'replay of supplied exchanges, not recorded from the device'
        self.assertTrue(replay_check['basis'].startswith('emit_check ' + kind), replay_check)
        self.assertEqual(equivalence['diff'], [{'id': 'emitted_package_replay', 'reason': 'the emitted package passed '
                         'emit_check without a replay of the recorded device exchanges (' + kind + ')'}])

    def test_01_B8_generic_verdict_grades_emitted_driver_alone_and_reports_reuse_in_mission(self):
        """Decision D2: a generic run's verdict judges the emitted driver alone from emit_check's replay of recorded
        live exchanges including a function beyond identity; fresh reuse runs and is reported beside it in `mission`."""
        from generative_driver.configurator import REPLAY_IDENTITY_ONLY, REPLAY_UNGRANTED
        # 1. Live replay of a function (read_temperature) is equivalent even when fresh reuse does nothing or fails.
        for label, reuse_fn, expected_mission in (
                ('reuse calls nothing', lambda call, pkg, ws: None, False),
                ('reuse calls identify only', lambda call, pkg, ws: call('identify'), False),
                ('reuse calls read_temperature and succeeds', lambda call, pkg, ws: call('read_temperature'), True)):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as directory:
                result, _ = self.run_generic(Path(directory) / 'home', reuse_fn)
                eq = result['equivalence']
                self.assertEqual((result['status'], eq['equivalent'], eq['basis'], eq['diff']),
                                 ('completed', True, 'self-evidence', []), eq)
                self.assertEqual([c['id'] for c in eq['checks']], ['emitted_package_replay'])
                self.assertIs(eq['mission']['passed'], expected_mission, eq['mission'])
        # 2. Live replay of identity alone is not equivalent even when fresh reuse calls read_temperature.
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_generic(Path(directory) / 'home', self.reads, probe_operation='identify')
            eq = result['equivalence']
            self.assertEqual((result['status'], eq['equivalent']), ('completed', False), eq)
            self.assertEqual(eq['diff'], [{'id': 'emitted_package_replay', 'reason': REPLAY_IDENTITY_ONLY}])
            self.assertIs(eq['mission']['passed'], True, eq['mission'])
        # 3. Live replay of identity when grants permit no function beyond identity is unscored (None).
        write_only = thermometer_model()
        write_only['operations'] = {
            'identify': write_only['operations']['identify'],
            'set_mode': {'effect': 'write', 'parameters': {}, 'outputs': {}, 'steps': [
                {'op': 'exchange', 'tx': [{'text': 'M1\n'}],
                 'rx': {'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 64}, 'timeout_ms': 1000}]},
        }
        write_only['provenance'] = [{'item': 'operations.identify', 'source': 'scripted'},
                                    {'item': 'operations.set_mode', 'source': 'scripted'}]
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self.run_generic(Path(directory) / 'home', lambda call, pkg, ws: call('identify'),
                                         probe_operation='identify', model=write_only, effects=['read'])
            eq = result['equivalence']
            self.assertEqual((result['status'], eq['equivalent']), ('completed', None), eq)
            self.assertEqual(eq['diff'], [{'id': 'emitted_package_replay', 'reason': REPLAY_UNGRANTED}])
            self.assertIsNone(eq['mission']['passed'], eq['mission'])


class ConfiguratorTests(unittest.TestCase):
    def _scripted_generic(self, root, report, *, alter=False, imports=True, later=None, refuse=False):
        """Drive a generic run with an in-process worker that imports one image through the real gateway.

        `later` replaces the runtime result of every stage after acquire; `refuse` makes acquire ask for an
        unassigned tool first.
        """
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        controller = Controller(root / 'home')
        stages = []
        def fake_call_tool(name, arguments):
            if name == 'acquire_firmware_artifact':
                out = Path(arguments['run_dir']); out.mkdir(parents=True, exist_ok=True)
                image = out / 'image.bin'; image.write_bytes(b'\xe9 scripted image')
                return {'available': True, 'artifact': str(image), '_exit': 0}
            return {'ok': False, 'reason': 'scripted interpretation preparation stop'}
        def fake_execute(request, config, cancel):
            stages.append(request.stage)
            if request.stage != 'acquire' and later:
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, **later}
            if request.stage == 'acquire':
                args = request.gateway['args']
                identity = {'run_id': args[args.index('--run') + 1], 'assignment_id': args[args.index('--assignment') + 1]}
                if refuse:
                    with self.assertRaisesRegex(ValueError, 'not assigned'):
                        controller.call('tool', {**identity, 'name': 'probe_run', 'arguments': {}})
                if imports:
                    out = controller.call('tool', {**identity, 'name': 'acquire_firmware_artifact',
                                                   'arguments': {'origin': 'provided_binary'}})
                    if alter:
                        Path(out['artifact']).write_bytes(b'changed after the tool returned')
            if isinstance(report, dict) and 'runtime_status' in report:
                return {'runtime': 'codex', 'status': report['runtime_status'], 'cause': report.get('cause'),
                        'reason': report.get('reason'), 'elapsed_seconds': 0, 'usage': None, 'report': None}
            return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0,
                    'usage': None, 'report': report(request.workspace) if callable(report) else report}
        try:
            with patch('generative_driver.toolkit.call_tool', fake_call_tool), \
                 patch('generative_driver.configurator.execute', fake_execute):
                run = controller.call('start', {'goal': 'Scripted outcome contract; no model or device',
                                                'executor_config': {'command': ['never-launched']}})
                result = wait_stopped(controller, run)
            return result, stages
        finally:
            controller.close()

    def test_needs_revision_on_acquire_with_observed_artifact_advances_to_interpret(self):
        report = {'status': 'needs_revision', 'summary': 'Image imported; flash layout unresolved', 'artifacts': [],
                  'checks': [{'name': 'flash_layout', 'status': 'fail', 'evidence': []}], 'unresolved': ['flash layout']}
        with tempfile.TemporaryDirectory() as directory:
            result, stages = self._scripted_generic(Path(directory), report)
        self.assertEqual(stages[0], 'acquire')
        acquire = result['accepted_handoffs'][0]
        self.assertEqual(acquire['stage'], 'acquire')
        self.assertEqual([Path(a['path']).name.split('-', 1)[1] for a in acquire['artifacts']], ['image.bin'])
        notes = acquire['notes']['inline']
        self.assertEqual(notes['schema'], 'stage-notes/1')
        self.assertEqual(notes['worker']['status'], 'needs_revision')
        self.assertEqual(notes['worker']['checks'], report['checks'])
        self.assertIn('flash layout', notes['open_questions'])
        self.assertEqual(notes['outcome']['kind'], 'advance')
        self.assertTrue(any(c['name'] == 'observed_acquisition_hashes' and c['passed'] for c in notes['configurator']['checks']))
        self.assert_interpreted_the_acquired_image(result)

    def assert_interpreted_the_acquired_image(self, result):
        """Acquire advanced: interpretation was prepared from its image (the fixture refuses that preparation,
        which is a note) and the run went on to completion."""
        interpret = result['accepted_handoffs'][1]
        self.assertEqual((interpret['stage'], interpret['artifacts']), ('interpret', []))
        self.assertTrue(any('scripted interpretation preparation stop' in q for q in interpret['notes']['inline']['open_questions']),
                        interpret['notes']['inline'])
        self.assertEqual(result['status'], 'completed', result)

    def test_mismatched_worker_claims_are_dropped_and_noted_without_stopping(self):
        outside = []
        def report(workspace):
            claimed = Path(workspace) / 'claimed.bin'; claimed.write_bytes(b'worker bytes')
            outside.append(str(Path(workspace).parent / 'outside.bin'))
            return {'status': 'completed', 'summary': 'scripted claims', 'checks': [], 'unresolved': [],
                    'artifacts': [{'path': str(claimed), 'sha256': '0' * 64, 'kind': 'evidence'},
                                  {'path': outside[0], 'sha256': '0' * 64, 'kind': 'evidence'}]}
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._scripted_generic(Path(directory), report)
        acquire = result['accepted_handoffs'][0]
        self.assertEqual(len(acquire['artifacts']), 1)
        dropped = {Path(d['path']).name: d['reason'] for d in acquire['notes']['inline']['configurator']['dropped_artifacts']}
        self.assertEqual(dropped, {'claimed.bin': 'Worker artifact hash disagrees with observed bytes',
                                   'outside.bin': 'Reported artifact is outside the assigned workspace'})
        self.assert_interpreted_the_acquired_image(result)

    def test_partial_handoff_records_hashed_notes_beside_its_artifacts(self):
        from generative_driver.configurator import digest
        report = {'status': 'blocked', 'summary': 'No image yet', 'artifacts': [],
                  'checks': [{'name': 'image', 'status': 'not_run', 'evidence': []}], 'unresolved': ['operator must supply the image']}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            result, _ = self._scripted_generic(root, report, imports=False, refuse=True, later={'status': 'blocked',
                'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'})
            acquire = result['accepted_handoffs'][0]
            self.assertEqual((acquire['stage'], acquire['artifacts']), ('acquire', []))
            notes = acquire['notes']
            path = Path(notes['path'])
            self.assertEqual(path, root / 'home/runs' / result['run_id'] / 'accepted/0/acquire' / acquire['assignment_id'] / 'notes.json')
            self.assertEqual(notes['sha256'], digest(path))
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), notes['inline'])
            self.assertIn('operator must supply the image', notes['inline']['open_questions'])
            self.assertEqual(notes['inline']['configurator']['tool_refusals'],
                             [{'name': 'probe_run', 'reason': 'Tool is not assigned to this worker'}])
            self.assertEqual([h['stage'] for h in result['accepted_handoffs']], ['acquire', 'interpret'])
            self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('blocked', 'operator', 'probe'))
            self.assertEqual(result['progress']['next_stage'], 'probe')

    def test_tampered_notes_stop_resume_for_integrity(self):
        from generative_driver.configurator import Controller, IntegrityError
        report = {'status': 'completed', 'summary': 'scripted import', 'artifacts': [], 'checks': [], 'unresolved': []}
        operator_stop = {'status': 'blocked', 'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
        for where in ('file', 'record'):
            with self.subTest(where=where), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result, _ = self._scripted_generic(root, report, later=operator_stop)
                self.assertEqual(result['status'], 'blocked')
                self.assertNotEqual(result['stage'], 'acquire')
                acquire = result['accepted_handoffs'][0]
                controller = Controller(root / 'home')
                try:
                    if where == 'file':
                        path = Path(acquire['notes']['path'])
                        path.write_text(path.read_text(encoding='utf-8').replace('scripted import', 'rewritten later'), encoding='utf-8')
                    else:
                        acquire['notes']['inline']['worker']['summary'] = 'rewritten later'
                        with controller._db() as db:
                            db.execute('UPDATE handoffs SET payload=? WHERE id=?', (json.dumps(acquire), acquire['assignment_id']))
                    with self.assertRaisesRegex(IntegrityError, 'Stale handoff'):
                        controller.call('resume', {'run_id': result['run_id']})
                    after = controller.call('result', {'run_id': result['run_id']})
                    self.assertEqual((after['status'], after['outcome_category']), ('blocked', 'integrity'))
                    self.assertFalse(any(e['kind'] == 'run.resumed' for e in controller.call('events', {'run_id': result['run_id']})['events']))
                finally:
                    controller.close()

    def test_honest_not_run_check_advances(self):
        # run-0bddf9e2448f46c7 stopped here after acquire had produced its image.
        report = {'status': 'completed', 'summary': 'Imported; boot log not captured', 'artifacts': [],
                  'checks': [{'name': 'boot_log', 'status': 'not_run', 'evidence': []}], 'unresolved': []}
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._scripted_generic(Path(directory), report)
        acquire = result['accepted_handoffs'][0]
        self.assertEqual(len(acquire['artifacts']), 1)
        self.assertEqual(acquire['notes']['inline']['worker']['checks'][0]['status'], 'not_run')
        self.assert_interpreted_the_acquired_image(result)

    def test_runtime_without_a_report_advances_with_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._scripted_generic(Path(directory), {'runtime_status': 'failed', 'cause': 'no_report',
                'reason': 'Runtime did not produce a structured stage report'})
        acquire = result['accepted_handoffs'][0]
        notes = acquire['notes']['inline']
        self.assertEqual(notes['worker']['runtime_status'], 'failed')
        self.assertTrue(any('no usable report' in q for q in notes['open_questions']), notes)
        self.assert_interpreted_the_acquired_image(result)

    def test_blocked_acquire_without_artifacts_hands_interpret_an_empty_handoff_and_the_run_completes(self):
        from generative_driver.configurator import STAGES
        report = {'status': 'blocked', 'summary': 'No firmware image could be read', 'artifacts': [], 'checks': [],
                  'unresolved': ['operator must supply the image']}
        with tempfile.TemporaryDirectory() as directory:
            result, stages = self._scripted_generic(Path(directory), report, imports=False)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([h['stage'] for h in result['accepted_handoffs']], list(STAGES))
        # No binary: interpretation runs no worker and hands off empty with the reason; every later stage starts.
        interpret = result['accepted_handoffs'][1]
        self.assertEqual(interpret['artifacts'], [])
        notes = interpret['notes']['inline']
        self.assertEqual((notes['worker']['runtime_status'], notes['outcome']['kind']), ('skipped', 'advance'))
        self.assertTrue(any('acquired binary' in q for q in notes['open_questions']), notes)
        self.assertEqual(stages, ['acquire', 'probe', 'ground', 'emit'])
        self.assertIn('No interpreted model available', result['accepted_handoffs'][2]['notes']['inline']['open_questions'])
        self.assertEqual(result['outcome_category'], 'completed')
        # Reuse had no package; the run reaches finalize and is not equivalent because no driver was emitted.
        self.assertTrue(result['reason'].startswith('Final verdict: not equivalent: no driver emitted'), result['reason'])
        self.assertFalse(result['equivalence']['equivalent'])

    def test_interpreted_model_is_handed_off_with_validation_notes_and_only_a_broken_seal_stops(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                for name, collected, validated, model_json, handed in (
                        ('invalid model', {'ok': True, 'sealed_inputs_unchanged': True},
                         {'ok': False, 'defects': [{'where': 'operations', 'what': 'missing'}]}, '{"schema": "interface-model/7"}', True),
                        ('unsupported schema', {'ok': False, 'sealed_inputs_unchanged': True, 'reason': 'no model.json with a supported schema'},
                         {'ok': False, 'defects': [{'where': 'schema', 'what': 'unsupported'}]}, '{"schema": "notes"}', True),
                        ('nothing written', {'ok': False, 'sealed_inputs_unchanged': True, 'reason': 'no model.json with a supported schema'},
                         None, None, False),
                        ('broken seal', {'ok': False, 'sealed_inputs_unchanged': False, 'reason': 'sealed inputs changed during the run: [image.bin]'},
                         None, '{"schema": "interface-model/7"}', None)):
                    with self.subTest(name=name):
                        work = root / name.replace(' ', '-'); model = work / 'record'; model.mkdir(parents=True)
                        if model_json:
                            (model / 'model.json').write_text(model_json)
                        calls = []
                        def fake_call_tool(tool, arguments):
                            calls.append(tool)
                            return collected if tool == 'interpret_collect' else validated
                        assignment = {'id': 'scripted', 'collection': {'run_dir': str(work / 'prep'), 'expected_seal_sha256': '0' * 64,
                                                                       'model_dir': str(model)}}
                        with patch('generative_driver.toolkit.call_tool', fake_call_tool):
                            checked = controller._check({'goal': 'x', 'effects': ['read']}, 'interpret', root / 'run', work, None, [], assignment)
                        if handed is None:
                            self.assertEqual(checked['stop']['category'], 'integrity')
                            continue
                        self.assertNotIn('stop', checked)
                        self.assertEqual(checked['artifacts'], [str(model)] if handed else [])
                        notes = {c['name']: c for c in checked['notes']['checks']}
                        self.assertEqual(notes['sealed_model_collection']['passed'], collected['ok'])
                        self.assertFalse(notes['executable_model_contract']['passed'])
                        if validated:
                            self.assertEqual(notes['executable_model_contract']['defects'], validated['defects'])
                        self.assertEqual(calls, ['interpret_collect'] + (['model_validate'] if validated else []))
            finally:
                controller.close()

    def test_observed_acquisition_changed_after_the_tool_returned_stops_for_integrity(self):
        report = {'status': 'completed', 'summary': 'scripted', 'artifacts': [], 'checks': [], 'unresolved': []}
        with tempfile.TemporaryDirectory() as directory:
            result, stages = self._scripted_generic(Path(directory), report, alter=True)
        self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('blocked', 'integrity', 'acquire'))
        self.assertIn('changed after the tool returned', result['reason'])
        self.assertEqual(result['accepted_handoffs'], [])
        self.assertEqual(stages, ['acquire'])

    def test_runtime_that_cannot_start_stops_for_the_operator_and_resumes(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run = controller.call('start', {'goal': 'Missing executor contract',
                    'executor_config': {'command': ['missing-contract-runtime']}})
                result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'operator'))
                self.assertIn('Cannot start configured runtime', result['reason'])
                self.assertIn(resume_when_idle(self, controller, run)['status'], ('queued', 'running', 'blocked'))
                self.assertEqual(len([e for e in controller.call('events', run)['events'] if e['kind'] == 'run.resumed']), 1)
            finally:
                controller.close()

    def test_codex_is_the_default_even_with_an_old_executor_configuration(self):
        from generative_driver.configurator import Controller
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            controller = Controller(home)
            try:
                (home / 'config.json').write_text(json.dumps({'executors': {
                    'unknown-runtime': {'command': ['missing-configured-runtime']}}}), encoding='utf-8')
                for request in ({'goal': 'Default executor'}, {'goal': 'Null executor', 'executor': None},
                                {'goal': 'Unconfigured Codex', 'executor': 'codex'}):
                    with self.subTest(request=request), self.assertRaisesRegex(
                            ValueError, 'codex is not configured.*configure --executor codex --command <path>'):
                        controller.call('start', request)
                with self.assertRaisesRegex(ValueError, 'Unsupported executor'):
                    controller.call('start', {'goal': 'Unsupported executor', 'executor': 'unknown-runtime'})
                self.assertEqual(list((home / 'runs').glob('*')) if (home / 'runs').exists() else [], [])
                configure('codex', ['missing-codex-runtime'], home=home)
                run = controller.call('start', {'goal': 'Default after configuration'})
                self.assertEqual(wait_stopped(controller, run)['agent']['runtime'], 'codex')
            finally:
                controller.close()

    def test_saved_run_with_an_unsupported_executor_is_readable_but_cannot_resume_work(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run = controller.call('start', {'goal': 'Saved worker fixture',
                    'executor_config': {'command': ['missing-contract-runtime']}})
                wait_stopped(controller, run)
                run_id = run['run_id']
                spec = json.loads(controller._row(run_id)['spec'])
                spec['executor'] = spec['executor_config']['runtime'] = 'unknown-runtime'
                with controller._db() as db:
                    db.execute('UPDATE runs SET spec=? WHERE id=?', (json.dumps(spec), run_id))
                saved = controller.call('result', run)
                self.assertEqual(saved['agent']['runtime'], 'unknown-runtime')
                with unittest.mock.patch.object(controller, '_resume_device') as device:
                    with self.assertRaisesRegex(ValueError, 'unsupported executor'):
                        controller.call('resume', run)
                    device.assert_not_called()
                self.assertEqual(controller.call('result', run), saved)
            finally:
                controller.close()

    def test_runtime_that_exits_with_an_error_stops_for_the_host_and_resumes(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run = controller.call('start', {'goal': 'Crashing executor contract',
                    'executor_config': {'command': [sys.executable, '-c', 'import sys; sys.exit(3)']}})
                result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['outcome_category'], result['stage']), ('failed', 'host', 'acquire'))
                self.assertIn('exited with an error', result['reason'])
                self.assertEqual(result['accepted_handoffs'], [])
                self.assertIn(resume_when_idle(self, controller, run)['status'], ('queued', 'running', 'failed'))
                self.assertEqual(len([e for e in controller.call('events', run)['events'] if e['kind'] == 'run.resumed']), 1)
            finally:
                controller.close()

    def test_expired_run_resumes_with_an_audited_absolute_budget_increase(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(Path(directory))
            try:
                run=controller.call('start',{'goal':'Budget extension contract','budget_seconds':120,
                    'executor_config':{'command':['missing-contract-runtime']}})
                first=wait_stopped(controller,run)
                self.assertEqual(len(first['worker_reports']),1,first)
                # Expire the budget only after its first attempt exists; startup
                # speed must not decide whether there is a report to preserve.
                with controller._db() as db:
                    db.execute('UPDATE runs SET created=created-121 WHERE id=?',(run['run_id'],))
                original=controller.call('result',run)
                with self.assertRaisesRegex(ValueError,'budget exhausted'):
                    controller.call('resume',run)
                for invalid in (True,0,-1,float('inf'),float('nan'),604801,10**1000,.1):
                    with self.subTest(budget=repr(invalid)), self.assertRaises(ValueError):
                        controller.call('resume',{**run,'budget_seconds':invalid,'budget_reason':'Invalid test request'})
                with self.assertRaisesRegex(ValueError,'budget_reason'):
                    controller.call('resume',{**run,'budget_seconds':360,'budget_reason':' '})
                self.assertEqual(controller.call('result',run)['budget_seconds'],120)
                controller.call('resume',{**run,'budget_seconds':360,'budget_reason':'Operator approved restoring paused time'})
                result=wait_stopped(controller,run)
                self.assertEqual(len(result['worker_reports']),2,result)
                self.assertEqual(result['run_id'],original['run_id'])
                self.assertEqual(result['created'],original['created'])
                self.assertEqual(result['budget_seconds'],360)
                self.assertEqual(result['worker_reports'][0],original['worker_reports'][0])
                self.assertEqual(result['accepted_handoffs'],original['accepted_handoffs'])
                events=controller.call('events',run)['events']
                extension=[e['data'] for e in events if e['kind']=='run.budget_extended']
                self.assertEqual(len(extension),1)
                self.assertEqual(extension[0]['old_budget_seconds'],120)
                self.assertEqual(extension[0]['new_budget_seconds'],360)
                self.assertAlmostEqual(extension[0]['extension_seconds'],240)
                self.assertAlmostEqual(extension[0]['old_deadline'],original['created']+120)
                self.assertAlmostEqual(extension[0]['new_deadline'],original['created']+360)
                self.assertEqual(extension[0]['reason'],'Operator approved restoring paused time')
                self.assertFalse(any(e['kind']=='run.authorization' for e in events))
                controller.call('resume',{**run,'budget_seconds':360,'budget_reason':'Operator approved restoring paused time'})
                self.assertEqual(wait_stopped(controller,run)['budget_seconds'],360)
                self.assertEqual(len([e for e in controller.call('events',run)['events'] if e['kind']=='run.budget_extended']),1)
            finally:
                controller.close()

    def test_physical_case_receives_the_operator_binding_before_worker_execution(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(Path(directory))
            try:
                binding={'url':'ftdi://fixture-no-device/1'}
                run=controller.call('start',{'goal':'No hardware contract','binding':binding,'effects':['write'],
                    'scoped_tool_approval':'bound-device','executor_config':{'command':['missing-contract-runtime']}})
                until=time.monotonic()+5
                while time.monotonic()<until and controller.call('status',run)['status']!='blocked': time.sleep(.01)
                self.assertIn('Cannot start configured runtime',controller.call('status',run)['reason'])
                assigned=[e['data'] for e in controller.call('events',run)['events'] if e['kind']=='stage.assigned']
                self.assertEqual(assigned[0]['binding'],binding)
                self.assertEqual(assigned[0]['effects'],['write'])
            finally:
                controller.close()

    def test_bound_device_approval_requires_a_binding_and_effect_grants(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(Path(directory))
            try:
                params={'goal':'Bound device approval contract; no device calls','scoped_tool_approval':'bound-device',
                    'executor_config':{'command':['missing-contract-runtime']},'effects':['read']}
                with self.assertRaisesRegex(ValueError,'binding'):
                    controller.call('start',params)
                with self.assertRaisesRegex(ValueError,'effect'):
                    controller.call('start',{**params,'binding':{'port':'COM9'},'effects':[]})
                run=controller.call('start',{**params,'binding':{'port':'COM9'}})
                self.assertEqual(controller.call('result',run)['scoped_tool_approval'],'bound-device')
                with self.assertRaisesRegex(ValueError,'start takes no case_options'):
                    controller.call('start',{**params,'binding':{'port':'COM9'},'case_options':{'binding':{'port':'COM10'}}})
            finally:
                controller.close()

    # The operator's statement that resolves an uncertain effect, without its `effect_resolution`.
    STATEMENT = {'source': 'scripted operator', 'message': 'The earlier execution has stopped; the device is safe.',
                 'prior_execution_stopped': True}
    INCOMPLETE = ('Resolving an uncertain effect requires effect_resolution "confirmed_safe" and an observation with '
                  'source, message and prior_execution_stopped true')

    def _idle_run(self, controller, **start):
        """A running run whose acquire worker idles, and that worker's assignment."""
        run = controller.call('start', {'goal': 'Scripted statement contract', **start,
            'executor_config': {'command': [sys.executable, '-c', 'import time; time.sleep(20)']}})
        until = time.monotonic() + 5
        while time.monotonic() < until:
            assignment = next((e['data'] for e in controller.call('events', run)['events']
                               if e['kind'] == 'stage.assigned'), None)
            if assignment and controller.call('status', run)['status'] == 'running':
                return run, assignment
            time.sleep(.02)
        self.fail('no running acquire assignment')

    @staticmethod
    def _pending_write(controller, run, assignment):
        """The id of a write dispatched for the run's worker that has not ended: its pending effect stays."""
        return controller.dispatch_record.begin(run['run_id'], kind='run', name='interface_execute', actor='worker',
            effects=['write'], stage=assignment['stage'], assignment_id=assignment['id'])

    @staticmethod
    def _responses(controller, run):
        return [e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'operator.response']

    def test_01_B12_recovery_preserves_uncertainty_only_for_a_pending_effect_until_the_operator_resolves_it(self):
        from generative_driver.configurator import Controller
        for pending in (False, True):
            with self.subTest(pending_effect=pending), tempfile.TemporaryDirectory() as directory:
                controller=Controller(Path(directory))
                # The run is granted a write. Only a write that was dispatched and has not ended leaves a pending
                # effect.
                run,assignment=self._idle_run(controller,effects=['read','write'])
                dispatch=self._pending_write(controller,run,assignment) if pending else None
                self.assertEqual(controller.call('status',run)['uncertain_effect'],pending)
                controller.close()
                reopened=Controller(Path(directory))
                try:
                    state=reopened.call('status',run)
                    self.assertEqual((state['status'],state['reason']),
                                     ('blocked','Configurator restarted; explicit resume required'))
                    # The restart leaves the flag as it was.
                    self.assertEqual(state['uncertain_effect'],pending)
                    events=reopened.call('events',run)['events']
                    self.assertEqual([e['data'] for e in events if e['kind']=='run.recovered'],
                                     [{'uncertain_effect':pending}])
                    self.assertEqual(len([e for e in events if e['kind']=='stage.assigned']),1)
                    if pending:
                        with self.assertRaisesRegex(ValueError,'uncertain'):
                            reopened.call('resume',run)
                        reconciled=reopened.call('respond',{**run,'observation':{
                            **self.STATEMENT,'effect_resolution':'confirmed_safe'}})
                        self.assertFalse(reconciled['uncertain_effect'])
                        self.assertEqual(self._responses(reopened,run)[0]['resolved_dispatch_ids'],[str(dispatch)])
                    # Without a pending effect the run resumes with no statement.
                    self.assertIn(reopened.call('resume',run)['status'],('queued','running'))
                finally:
                    reopened.close()

    def test_01_B14_a_respond_with_an_incomplete_statement_is_refused_and_changes_nothing(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run, assignment = self._idle_run(controller, effects=['read', 'write'])
                dispatch = self._pending_write(controller, run, assignment)
                # The caller's own refusal comes before the check of the statement.
                with self.assertRaisesRegex(ValueError, 'Respond only while the run is stopped'):
                    controller.call('respond', {**run, 'message': 'Checked', 'effect_resolution': 'confirmed_safe'})
                controller.call('cancel', run)
                wait_stopped(controller, run)
                stated = {**self.STATEMENT, 'effect_resolution': 'confirmed_safe'}
                for label, params in (
                        ('a message alone', {'message': 'Checked the device', 'effect_resolution': 'confirmed_safe'}),
                        ('another resolution', {'observation': self.STATEMENT, 'effect_resolution': 'probably_safe'}),
                        ('another resolution in the observation',
                         {'observation': {**self.STATEMENT, 'effect_resolution': 'unknown'}}),
                        ('no source', {'observation': {key: stated[key] for key in stated if key != 'source'}}),
                        ('a source that is no text', {'observation': {**stated, 'source': 7}}),
                        ('a blank message', {'observation': {**stated, 'message': ' '}}),
                        ('execution not stated as stopped',
                         {'observation': {**stated, 'prior_execution_stopped': 'yes'}})):
                    with self.subTest(statement=label):
                        with self.assertRaises(ValueError) as refused:
                            controller.call('respond', {**run, **params})
                        self.assertEqual(str(refused.exception), self.INCOMPLETE)
                self.assertTrue(controller.call('status', run)['uncertain_effect'])
                self.assertEqual(self._responses(controller, run), [])
                pending = controller.dispatch_record.pending(run['run_id'])
                self.assertEqual([effect['dispatch_id'] for effect in pending], [dispatch])
                with self.assertRaisesRegex(ValueError, 'uncertain'):
                    controller.call('resume', run)
            finally:
                controller.close()

    def test_01_B14_a_complete_statement_resolves_the_runs_pending_effects_and_its_event_lists_them(self):
        from generative_driver.configurator import Controller
        # `respond` reads the resolution from its parameters or, when absent there, from the observation.
        for where, params in (('parameters', {'observation': self.STATEMENT, 'effect_resolution': 'confirmed_safe'}),
                              ('observation',
                               {'observation': {**self.STATEMENT, 'effect_resolution': 'confirmed_safe'}})):
            with self.subTest(resolution_in=where), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory))
                try:
                    run, assignment = self._idle_run(controller, effects=['read', 'write'])
                    dispatch = self._pending_write(controller, run, assignment)
                    controller.call('cancel', run)
                    self.assertTrue(wait_stopped(controller, run)['uncertain_effect'])
                    resolved = controller.call('respond', {**run, **params})
                    self.assertFalse(resolved['uncertain_effect'])
                    [response] = self._responses(controller, run)
                    self.assertEqual((response['effect_resolution'], response['resolved_dispatch_ids']),
                                     ('confirmed_safe', [str(dispatch)]))
                    self.assertEqual(response['observation'], params['observation'])
                    self.assertEqual(controller.dispatch_record.pending(run['run_id']), [])
                    self.assertIn(resume_when_idle(self, controller, run)['status'], ('queued', 'running'))
                finally:
                    controller.close()

    def test_01_B14_a_complete_statement_on_a_run_that_is_not_uncertain_is_accepted_and_lists_no_dispatch(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run, _ = self._idle_run(controller)
                controller.call('cancel', run)
                self.assertFalse(wait_stopped(controller, run)['uncertain_effect'])
                accepted = controller.call('respond', {**run, 'observation': self.STATEMENT,
                                                       'effect_resolution': 'confirmed_safe'})
                self.assertFalse(accepted['uncertain_effect'])
                [response] = self._responses(controller, run)
                self.assertEqual((response['effect_resolution'], response['resolved_dispatch_ids']),
                                 ('confirmed_safe', []))
            finally:
                controller.close()

    def test_01_B14_a_respond_that_states_no_resolution_stays_an_operator_message_and_resolves_nothing(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run, assignment = self._idle_run(controller, effects=['read', 'write'])
                dispatch = self._pending_write(controller, run, assignment)
                controller.call('cancel', run)
                wait_stopped(controller, run)
                # The statement's fields alone state no resolution: the observation is kept as the operator's message.
                for params in ({'message': 'The cable was loose'}, {'observation': self.STATEMENT}):
                    self.assertTrue(controller.call('respond', {**run, **params})['uncertain_effect'])
                first, second = self._responses(controller, run)
                self.assertEqual(first, {'message': 'The cable was loose', 'observation': None,
                                         'effect_resolution': None})
                self.assertEqual((second['observation'], second['effect_resolution']), (self.STATEMENT, None))
                self.assertNotIn('resolved_dispatch_ids', second)
                pending = controller.dispatch_record.pending(run['run_id'])
                self.assertEqual([effect['dispatch_id'] for effect in pending], [dispatch])
            finally:
                controller.close()

    def test_01_B16_a_flag_an_earlier_version_set_on_a_run_stays_until_the_statement_which_lists_no_dispatch(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run, assignment = self._idle_run(controller)
                with controller._db() as db:
                    # As an earlier version left it: the flag is set and no pending effect is recorded.
                    db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run['run_id'],))
                self.assertEqual(controller.dispatch_record.pending(run['run_id']), [])
                # The end of a read does not clear it.
                with patch('generative_driver.toolkit.call_tool', return_value={'available': True, '_exit': 0}):
                    controller.call('tool', {**run, 'assignment_id': assignment['id'],
                                             'name': 'acquire_firmware_artifact', 'arguments': {}})
                [finished] = [e['data'] for e in controller.call('events', run)['events']
                              if e['kind'] == 'tool.finished']
                self.assertEqual((finished['ok'], finished['effectful']), (True, False), finished)
                self.assertTrue(controller.call('status', run)['uncertain_effect'])
            finally:
                controller.close()
            # Neither does a restart, nor a cancellation.
            reopened = Controller(Path(directory))
            try:
                state = reopened.call('status', run)
                self.assertEqual((state['status'], state['uncertain_effect']), ('blocked', True), state)
                recovered = [e['data'] for e in reopened.call('events', run)['events'] if e['kind'] == 'run.recovered']
                self.assertEqual(recovered, [{'uncertain_effect': True}])
                cancelled = reopened.call('cancel', run)
                self.assertEqual((cancelled['status'], cancelled['uncertain_effect']), ('cancelled', True), cancelled)
                with self.assertRaisesRegex(ValueError, 'uncertain'):
                    reopened.call('resume', run)
                resolved = reopened.call('respond', {**run, 'observation': self.STATEMENT,
                                                     'effect_resolution': 'confirmed_safe'})
                self.assertFalse(resolved['uncertain_effect'])
                [response] = self._responses(reopened, run)
                self.assertEqual(response['resolved_dispatch_ids'], [])
                self.assertIn(resume_when_idle(self, reopened, run)['status'], ('queued', 'running'))
            finally:
                reopened.close()

    def test_01_B9_a_gateway_write_that_succeeded_while_its_run_was_cancelled_leaves_no_uncertain_effect(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        steps = lambda *operations: [{'operation': operation, 'step': 0, 'op': 'exchange', 'tx_hex': '', 'rx_hex': '',
                                      'status': 'completed'} for operation in operations]
        record = {'schema': 'interface-result/1', 'operation': 'set_rate', 'outputs': {}, 'units': {},
                  'identity_verified': True, 'transcript': steps('identify', 'set_rate'), 'evidence_kind': 'live'}
        lost = {**record, 'ok': False, 'transport_open_attempted': True, '_exit': 1,
                'error': {'code': 'timeout', 'message': 'timed out', 'fault': 'model'}}
        # What the runtime writes for a write that succeeded, and for one whose step went out and was never answered.
        for case, written, uncertain in (('the write succeeded', {**record, 'ok': True, '_exit': 0}, False),
                                         ('the write was never answered', lost, True)):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory))
                try:
                    with patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': ['interface_execute']}):
                        run, assignment = self._idle_run(controller, effects=['read', 'write'],
                                                         binding={'host': '127.0.0.1', 'port': 9})
                    model = Path(assignment['workspace']) / 'model'; model.mkdir(parents=True)
                    (model / 'model.json').write_text(json.dumps(DeviceRepairTests.writing_thermometer()))
                    def cancelled_meanwhile(name, arguments):
                        # The operator cancels the run while the tool runs its write.
                        self.assertEqual(controller.call('cancel', run)['status'], 'cancelled')
                        return dict(written)
                    with patch('generative_driver.toolkit.call_tool', cancelled_meanwhile):
                        controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'interface_execute',
                                                 'arguments': {'model_dir': str(model), 'operation': 'set_rate'}})
                    [finished] = [e['data'] for e in controller.call('events', run)['events']
                                  if e['kind'] == 'tool.finished']
                    self.assertEqual((finished['effectful'], finished['ok'], finished['effects_reconciled']),
                                     (True, not uncertain, not uncertain))
                    stopped = wait_stopped(controller, run)
                    self.assertEqual((stopped['status'], stopped['uncertain_effect']), ('cancelled', uncertain))
                    if uncertain:
                        with self.assertRaisesRegex(ValueError, 'uncertain'):
                            controller.call('resume', run)
                    else:
                        # Nothing is left to resolve: the run resumes with no statement.
                        self.assertIn(resume_when_idle(self, controller, run)['status'], ('queued', 'running'))
                finally:
                    controller.close()

    def test_binding_is_exclusive_until_the_active_run_stops(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            params = {'goal':'Scripted shared binding contract', 'binding':{'host':'127.0.0.1','port':45678},
                'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}}
            try:
                first = controller.call('start',params)
                with self.assertRaisesRegex(ValueError,'already owned'):
                    controller.call('start',{**params,'binding':{**params['binding'],'timeout':5}})
                controller.call('cancel',first)
            finally:
                controller.close()

    def test_cancelled_run_needs_explicit_resume_and_gets_new_assignment(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run = controller.call('start', {'goal':'Scripted cancellation contract',
                    'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}})
                def assigned_ids(count):
                    until = time.monotonic() + 3
                    while time.monotonic() < until:
                        ids = [e['data']['id'] for e in controller.call('events',run)['events']
                               if e['kind']=='stage.assigned']
                        if len(ids) >= count:
                            self.assertEqual(len(ids), count)
                            return ids
                        state = controller.call('status',run)
                        self.assertIn(state['status'], ('queued','running'), state)
                        time.sleep(.02)
                    self.fail(f'Expected {count} assignments; last result: {controller.call("result",run)}')
                first = assigned_ids(1)[0]
                self.assertEqual(controller.call('cancel',run)['status'],'cancelled')
                self.assertEqual(controller.call('status',run)['status'],'cancelled')
                self.assertEqual([e['data']['id'] for e in controller.call('events',run)['events']
                                  if e['kind']=='stage.assigned'], [first])
                until = time.monotonic() + 3
                resumed = None
                while time.monotonic() < until:
                    try:
                        resumed = controller.call('resume',run)
                        break
                    except ValueError as exc:
                        self.assertIn('stopping', str(exc))
                        time.sleep(.02)
                self.assertIsNotNone(resumed, controller.call('result',run))
                self.assertIn(resumed['status'], ('queued','running'))
                ids = assigned_ids(2)
                self.assertNotEqual(first,ids[-1])
                with self.assertRaisesRegex(ValueError,'inactive'):
                    controller.call('tools',{**run,'assignment_id':first})
            finally:
                controller.close()

    def test_worker_gateway_rejects_unassigned_tools_and_cancelled_assignments(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run = controller.call('start', {'goal':'Scripted gateway contract',
                    'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}})
                until = time.monotonic() + 3
                assignment = None
                while time.monotonic() < until:
                    events = controller.call('events', run)['events']
                    assignment = next((e['data'] for e in events if e['kind'] == 'stage.assigned'), None)
                    state = controller.call('status', run)
                    if assignment and state['status'] == 'running':
                        break
                    time.sleep(.02)
                self.assertIsNotNone(assignment)
                self.assertEqual(state['status'], 'running', state)
                request = {**run, 'assignment_id':assignment['id'], 'name':'model_validate', 'arguments':{}}
                with self.assertRaisesRegex(ValueError, 'not assigned'):
                    controller.call('tool', request)
                controller.call('cancel', run)
                with self.assertRaisesRegex(ValueError, 'inactive'):
                    controller.call('tools', {**run, 'assignment_id':assignment['id']})
            finally:
                controller.close()

    def test_acquire_receives_the_operator_serial_binding_not_a_worker_port(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            bound = str(Path(directory).resolve() / 'operator-serial')
            try:
                run = controller.call('start', {'goal':'Scripted acquire binding contract', 'binding':{'port':bound},
                    'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}})
                until = time.monotonic() + 3
                assignment = None
                while time.monotonic() < until:
                    assignment = next((e['data'] for e in controller.call('events', run)['events']
                                       if e['kind'] == 'stage.assigned'), None)
                    if assignment and controller.call('status', run)['status'] == 'running':
                        break
                    time.sleep(.02)
                self.assertEqual(assignment['stage'], 'acquire')
                request = {**run, 'assignment_id':assignment['id'], 'name':'acquire_classify'}
                with self.assertRaisesRegex(ValueError, 'configurator-owned'):
                    controller.call('tool', {**request, 'arguments':{'port':'/dev/worker-selected'}})
                # Opening the bound port requires listen evidence or a reset grant first.
                with self.assertRaisesRegex(ValueError, '(?i)listen'):
                    controller.call('tool', {**request, 'arguments':{}})
                controller.call('cancel', run)
            finally:
                controller.close()

    def _serial_run(self, controller, effects=('read',), binding=None):
        run = controller.call('start', {'goal':'Scripted serial contract', 'effects':list(effects),
            'binding': binding or {'port': '/dev/cu.contract'},
            'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}})
        until = time.monotonic() + 3
        while time.monotonic() < until:
            assignment = next((e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'), None)
            if assignment and controller.call('status', run)['status'] == 'running':
                return run, assignment
            time.sleep(.02)
        self.fail('no running acquire assignment')

    def test_listen_records_reset_and_later_connections_need_the_grant(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            if name == 'acquire_serial_listen':
                return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
            return {'device_class': None, 'reason': 'scripted', '_exit': 1}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller)
                request = {**run, 'assignment_id': assignment['id']}
                with self.assertRaisesRegex(ValueError, '(?i)listen'):
                    controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'flash_backend': 'esp32'}})
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {'seconds': 1}})
                self.assertEqual(captured['acquire_serial_listen']['port'], '/dev/cu.contract')
                with self.assertRaisesRegex(ValueError, 'reset grant'):
                    controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'flash_backend': 'esp32'}})
                with self.assertRaisesRegex(ValueError, 'configurator-owned'):
                    controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {'allow_reset': True}})
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_reset_grant_lets_esp_acquisition_auto_reset(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller, effects=('read', 'reset'))
                request = {**run, 'assignment_id': assignment['id']}
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'flash_backend': 'esp32'}})
                self.assertIs(captured['acquire_classify']['allow_reset'], True)
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_model_must_declare_an_observed_reset(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory) / 'home')
            try:
                with patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': ['acquire_serial_listen', 'probe_run']}):
                    run, assignment = self._serial_run(controller, effects=('read', 'reset'))
                    request = {**run, 'assignment_id': assignment['id']}
                    model_dir = Path(assignment['workspace']) / 'model'; model_dir.mkdir(parents=True)
                    (model_dir / 'model.json').write_text(json.dumps({'schema': 'interface-model/7',
                        'channel': {'type': 'uart', 'baudrate': 115200}, 'operations': {'read': {'effect': 'read'}}}))
                    controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                    with self.assertRaisesRegex(ValueError, 'omits resets_on_open'):
                        controller.call('tool', {**request, 'name': 'probe_run',
                                                 'arguments': {'model_dir': str(model_dir), 'operation': 'read'}})
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_identity_binding_is_resolved_for_serial_tools(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            return {'available': True, 'unsolicited_output': False, '_exit': 0, 'artifacts': []}
        identity = {'vendor_id': 0x10C4, 'product_id': 0xEA60, 'serial_number': 'abc'}
        with tempfile.TemporaryDirectory() as directory, \
             patch('generative_driver.toolkit.call_tool', fake_call_tool), \
             patch('interface_runtime.transports.resolve_serial_port', lambda binding, ports=None: '/dev/cu.usbserial-11140'):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller, binding=identity)
                controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_serial_listen', 'arguments': {}})
                self.assertEqual(captured['acquire_serial_listen']['port'], '/dev/cu.usbserial-11140')
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_operator_can_clear_a_detected_reset(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller)
                controller.call('tool', {**run, 'assignment_id': assignment['id'], 'name': 'acquire_serial_listen', 'arguments': {}})
                self.assertTrue(controller._binding_state(run['run_id'])['resets_on_open'])
                controller.call('cancel', run)
                until = time.monotonic() + 5
                while controller.call('status', run)['status'] in ('queued', 'running') and time.monotonic() < until:
                    time.sleep(.02)
                for near_miss in ({'binding_property': 'resets_on_open', 'value': 0},
                                  {'binding_property': 'other_property', 'value': False}):
                    controller.call('respond', {**run, 'observation': near_miss})
                    self.assertTrue(controller._binding_state(run['run_id'])['resets_on_open'])
                controller.call('respond', {**run, 'observation': {'binding_property': 'resets_on_open', 'value': False}})
                self.assertIs(controller._binding_state(run['run_id'])['resets_on_open'], False)
            finally:
                controller.close()

    def test_operator_override_sticks_across_later_listens(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller)
                listen = {**run, 'assignment_id': assignment['id'], 'name': 'acquire_serial_listen', 'arguments': {}}
                controller.call('tool', listen)
                self.assertTrue(controller._binding_state(run['run_id'])['resets_on_open'])
                controller.call('cancel', run)
                until = time.monotonic() + 5
                while controller.call('status', run)['status'] in ('queued', 'running') and time.monotonic() < until:
                    time.sleep(.02)
                controller.call('respond', {**run, 'observation': {'binding_property': 'resets_on_open', 'value': False}})
                controller._event(run['run_id'], 'tool.finished', {'name': 'acquire_serial_listen', 'ok': True,
                                  'result': {'unsolicited_output': True}})
                state = controller._binding_state(run['run_id'])
                self.assertTrue(state['listened'])
                self.assertIs(state['resets_on_open'], False)
            finally:
                controller.close()

    def test_classify_without_a_serial_binding_runs_ungated_without_a_port(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        for binding in (None, {'url': 'ftdi://x/1'}):
            captured = {}
            def fake_call_tool(name, arguments):
                captured[name] = dict(arguments)
                return {'available': True, '_exit': 0, 'artifacts': []}
            with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool):
                controller = Controller(Path(directory))
                try:
                    start = {'goal': 'Datasheet rung', 'executor_config': {'command': [sys.executable, '-c', 'import time; time.sleep(20)']}}
                    if binding:
                        start['binding'] = binding
                    run = controller.call('start', start)
                    until = time.monotonic() + 3
                    assignment = None
                    while time.monotonic() < until:
                        assignment = next((e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'), None)
                        if assignment and controller.call('status', run)['status'] == 'running':
                            break
                        time.sleep(.02)
                    request = {**run, 'assignment_id': assignment['id'], 'name': 'acquire_classify'}
                    controller.call('tool', {**request, 'arguments': {'part_hint': 'bme280'}})
                    self.assertNotIn('port', captured['acquire_classify'])
                    with self.assertRaisesRegex(ValueError, 'configurator-owned'):
                        controller.call('tool', {**request, 'arguments': {'allow_reset': True}})
                    controller.call('cancel', run)
                finally:
                    controller.close()

    def test_classify_scans_only_the_bound_ftdi_bus(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        for binding, expected in (({'url': 'ftdi://ftdi:232h/1'}, 'ftdi://ftdi:232h/1'), (None, None)):
            captured = {}
            def fake_call_tool(name, arguments):
                captured[name] = dict(arguments)
                return {'available': True, '_exit': 0, 'artifacts': []}
            with self.subTest(binding=binding), tempfile.TemporaryDirectory() as directory, \
                 patch('generative_driver.toolkit.call_tool', fake_call_tool):
                controller = Controller(Path(directory))
                try:
                    start = {'goal': 'Bus rung', 'executor_config': {'command': [sys.executable, '-c', 'import time; time.sleep(20)']}}
                    if binding:
                        start['binding'] = binding
                    run = controller.call('start', start)
                    until = time.monotonic() + 3
                    assignment = None
                    while time.monotonic() < until:
                        assignment = next((e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'), None)
                        if assignment and controller.call('status', run)['status'] == 'running':
                            break
                        time.sleep(.02)
                    request = {**run, 'assignment_id': assignment['id'], 'name': 'acquire_classify'}
                    controller.call('tool', {**request, 'arguments': {}})
                    # The operator's bridge is the only bus a worker's acquisition walks; it names none itself.
                    self.assertEqual(captured['acquire_classify'].get('bus_url'), expected)
                    self.assertNotIn('port', captured['acquire_classify'])
                    with self.assertRaisesRegex(ValueError, 'configurator-owned'):
                        controller.call('tool', {**request, 'arguments': {'bus_url': 'ftdi://ftdi:232h/2'}})
                    controller.call('cancel', run)
                finally:
                    controller.close()

    def test_identity_binding_with_a_non_uart_model_still_needs_the_reset_grant(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, 'unsolicited_output': True, '_exit': 0, 'artifacts': []}
        identity = {'vendor_id': 0x10C4, 'product_id': 0xEA60}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool), \
             patch('interface_runtime.transports.resolve_serial_port', lambda binding, ports=None: '/dev/cu.x'), \
             patch.dict('generative_driver.configurator.STAGE_TOOLS', {'acquire': ['acquire_serial_listen', 'interface_execute']}):
            controller = Controller(Path(directory) / 'home')
            try:
                run, assignment = self._serial_run(controller, binding=identity)
                request = {**run, 'assignment_id': assignment['id']}
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                model_dir = Path(assignment['workspace']) / 'model'; model_dir.mkdir(parents=True)
                (model_dir / 'model.json').write_text(json.dumps({'schema': 'interface-model/7', 'channel': {'type': 'usb'},
                                                                   'operations': {'read': {'effect': 'read'}}}))
                with self.assertRaisesRegex(ValueError, 'reset grant'):
                    controller.call('tool', {**request, 'name': 'interface_execute',
                                             'arguments': {'model_dir': str(model_dir), 'operation': 'read'}})
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_worker_acquire_tools_hide_port_and_reset_grant(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller)
                tools = controller.call('tools', {**run, 'assignment_id': assignment['id']})['tools']
                names = {t['name'] for t in tools}
                self.assertIn('acquire_serial_listen', names)
                for tool in tools:
                    properties = tool['inputSchema'].get('properties', {})
                    self.assertNotIn('port', properties)
                    self.assertNotIn('allow_reset', properties)
                controller.call('cancel', run)
            finally:
                controller.close()

    def _acquire_run(self, controller, binding=None):
        start = {'goal': 'Scripted acquire contract', 'executor_config': {'command': [sys.executable, '-c', 'import time; time.sleep(20)']}}
        if binding:
            start['binding'] = binding
        run = controller.call('start', start)
        until = time.monotonic() + 3
        while time.monotonic() < until:
            assignment = next((e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'stage.assigned'), None)
            if assignment and controller.call('status', run)['status'] == 'running':
                return run, assignment
            time.sleep(.02)
        self.fail('no running acquire assignment')

    def test_classify_refuses_a_usb_device_the_worker_selects(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, 'unsolicited_output': False, '_exit': 0, 'artifacts': []}
        identity = {'vendor_id': 0x10C4, 'product_id': 0xEA60, 'serial_number': 'abc'}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool), \
             patch('interface_runtime.transports.resolve_serial_port', lambda binding, ports=None: '/dev/cu.x'):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller, binding=identity)
                request = {**run, 'assignment_id': assignment['id'], 'name': 'acquire_classify'}
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                # Another attached device, or one another run leases, is never the worker's to name.
                for chosen in ({'transports': ['usb'], 'address': '05ac:1234'}, {'transports': ['usb'], 'serial_number': 'other'}):
                    with self.assertRaisesRegex(ValueError, 'configurator-owned'):
                        controller.call('tool', {**request, 'arguments': chosen})
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_classify_reads_the_bound_usb_identity(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        captured = {}
        def fake_call_tool(name, arguments):
            captured[name] = dict(arguments)
            return {'available': True, 'unsolicited_output': False, '_exit': 0, 'artifacts': []}
        identity = {'vendor_id': 0x10C4, 'product_id': 0xEA60, 'serial_number': 'abc'}
        with tempfile.TemporaryDirectory() as directory, patch('generative_driver.toolkit.call_tool', fake_call_tool), \
             patch('interface_runtime.transports.resolve_serial_port', lambda binding, ports=None: '/dev/cu.x'):
            controller = Controller(Path(directory))
            try:
                run, assignment = self._serial_run(controller, binding=identity)
                request = {**run, 'assignment_id': assignment['id']}
                controller.call('tool', {**request, 'name': 'acquire_serial_listen', 'arguments': {}})
                controller.call('tool', {**request, 'name': 'acquire_classify', 'arguments': {'transports': ['usb']}})
                self.assertEqual(captured['acquire_classify']['address'], '10c4:ea60')
                self.assertEqual(captured['acquire_classify']['serial_number'], 'abc')
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_classify_refuses_usb_selection_without_a_bound_usb_identity(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        def fake_call_tool(name, arguments):
            return {'available': True, '_exit': 0, 'artifacts': []}
        for binding in (None, {'port': '/dev/cu.contract'}, {'url': 'ftdi://x/1'}):
            with self.subTest(binding=binding), tempfile.TemporaryDirectory() as directory, \
                 patch('generative_driver.toolkit.call_tool', fake_call_tool):
                controller = Controller(Path(directory))
                try:
                    run, assignment = self._acquire_run(controller, binding)
                    request = {**run, 'assignment_id': assignment['id'], 'name': 'acquire_classify'}
                    with self.assertRaisesRegex(ValueError, 'USB'):
                        controller.call('tool', {**request, 'arguments': {'transports': ['usb']}})
                    controller.call('cancel', run)
                finally:
                    controller.close()

    def test_classify_hides_usb_device_selectors_from_workers(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                run, assignment = self._acquire_run(controller)
                tools = controller.call('tools', {**run, 'assignment_id': assignment['id']})['tools']
                properties = next(t for t in tools if t['name'] == 'acquire_classify')['inputSchema'].get('properties', {})
                self.assertIn('transports', properties)
                self.assertNotIn('address', properties)
                self.assertNotIn('serial_number', properties)
                controller.call('cancel', run)
            finally:
                controller.close()

    def test_generic_worker_receives_operator_observations_without_the_connection(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            prompts = root / 'prompts.txt'
            held = root / 'held'
            fake = root / 'record_prompt.py'
            # The first worker records its prompt and waits so the operator can stop the run; later workers
            # record and exit without a report, which leaves notes rather than stopping the run.
            fake.write_text('import sys,pathlib,time\n'
                            'with pathlib.Path(sys.argv[1]).open("a") as f: f.write(sys.stdin.read()+"\\n<end>\\n")\n'
                            'marker=pathlib.Path(sys.argv[2])\n'
                            'if not marker.exists():\n marker.touch(); time.sleep(20)\n')
            bound = str(root / 'operator-serial')
            controller = Controller(root / 'home')
            try:
                run = controller.call('start', {'goal':'Scripted observation contract', 'binding':{'port':bound},
                    'executor_config':{'command':[sys.executable,str(fake),str(prompts),str(held)]}})
                until = time.monotonic() + 5
                while not (prompts.is_file() and '<end>' in prompts.read_text()):
                    self.assertLess(time.monotonic(), until, 'Scripted worker did not record its prompt')
                    time.sleep(.02)
                controller.call('cancel', run)
                wait_stopped(controller, run)
                controller.call('respond', {**run, 'observation':{'source':'operator','chip_family':'esp32'}})
                resume_when_idle(self, controller, run)
                wait_stopped(controller, run)
                first, second = prompts.read_text().split('<end>')[:2]
                self.assertNotIn('esp32', first)
                self.assertIn('esp32', second)
                self.assertIn('operator_observations', second)
                self.assertIn('device binding is attached', second)
                self.assertNotIn(bound, second)
                acquire = controller.call('result', run)['accepted_handoffs'][0]
                self.assertEqual(acquire['stage'], 'acquire')
                self.assertEqual(acquire['notes']['inline']['worker']['runtime_status'], 'failed')
            finally:
                controller.close()

    def test_worker_claims_and_hashed_text_cannot_substitute_for_stage_evidence(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / 'scripted_agent.py'
            fake.write_text('''import sys,json,pathlib,hashlib
prompt = sys.stdin.read()
p = pathlib.Path('evidence.txt'); p.write_text('scripted contract evidence')
r = {'status':'completed','summary':'scripted contract only','artifacts':[{'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'kind':'test'}],'checks':[{'name':'fixture','status':'pass','evidence':[str(p)]}],'unresolved':[]}
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(r)}}))
''')
            controller = Controller(root / 'state')
            try:
                run = controller.call('start', {'goal':'Scripted six-stage contract, no model',
                    'executor_config':{'command':[sys.executable,str(fake)]}})
                result = wait_stopped(controller, run)
                # The claim is recorded as notes and the run continues, but it is never accepted evidence.
                acquire = result['accepted_handoffs'][0]
                self.assertEqual((acquire['stage'], acquire['artifacts']), ('acquire', []))
                notes = acquire['notes']['inline']
                self.assertEqual(notes['worker']['checks'], [{'name':'fixture','status':'pass','evidence':['evidence.txt']}])
                observed = next(c for c in notes['configurator']['checks'] if c['name'] == 'observed_acquisition_hashes')
                self.assertFalse(observed['passed'])
                self.assertIn('observed acquisition', observed['reason'])
                self.assertEqual([Path(p).name for p in observed['unobserved']], ['evidence.txt'])
                self.assertNotEqual(result['stage'], 'acquire')
                self.assertFalse(any(Path(a['path']).name.endswith('evidence.txt')
                                     for h in result['accepted_handoffs'] for a in h['artifacts']))
                self.assertEqual(result['evaluator_verdicts'], [])
            finally:
                controller.close()

    def test_idempotent_start_can_be_reconnected_without_relaunching(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            params = {'goal':'Describe a supplied device', 'executor':'codex', 'request_id':'test-start',
                      'executor_config':{'command':['a-runtime-that-does-not-exist'],'model':'configured-model','version':'contract-version'},
                      'operator_note':'not-an-agent-input'}
            # The configurator holds no password file: a client does, where it needs one.
            with self.assertRaisesRegex(ValueError, 'password'):
                controller.call('start', {**params, 'request_id':'test-password',
                                          'evaluator_password_file':'not-an-agent-input'})
            run = controller.call('start', params)
            second = controller.call('start', params)
            self.assertEqual(run['run_id'], second['run_id'])
            until = time.monotonic() + 5
            while time.monotonic() < until:
                status = controller.call('status', {'run_id':run['run_id']})
                if status['status'] == 'blocked':
                    break
                time.sleep(.02)
            self.assertEqual(status['status'], 'blocked')
            self.assertIn('Cannot start', status['reason'])
            result=controller.call('result',run)
            self.assertEqual(result['agent']['model'],'configured-model')
            self.assertEqual(result['agent']['version'],'contract-version')
            self.assertEqual(result['budget_seconds'],10800)
            # A key of the start that the service does not know reaches no part of the result but `request`, which
            # gives the start's params back as they came (07-B2).
            self.assertNotIn('not-an-agent-input',str({key:value for key,value in result.items() if key!='request'}))
            self.assertEqual(result['request'],params)
            controller.close()
            reopened = Controller(Path(directory))
            self.assertEqual(reopened.call('status', {'run_id':run['run_id']})['status'], 'blocked')
            self.assertEqual(len(reopened.call('events', {'run_id':run['run_id']})['events']), len(controller.call('events', {'run_id':run['run_id']})['events']))
            reopened.close()

    def test_interpret_prefers_the_named_acquisition_input(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                acquired = root / 'acquire'; acquired.mkdir()
                for name in ('flash.bin', 'app.bin', 'interpret-input.elf'):
                    (acquired / name).write_bytes(b'\x7fELF' if name.endswith('.elf') else b'\xe9')
                accepted = [{'stage': 'acquire', 'artifacts': [{'path': str(acquired / n), 'sha256': '', 'kind': 'file'}
                                                               for n in ('flash.bin', 'app.bin', 'interpret-input.elf')]}]
                seen = {}
                def fake_call_tool(name, arguments):
                    seen.update(arguments['inputs'])
                    return {'ok': False, 'reason': 'scripted stop'}
                from unittest.mock import patch
                with patch('generative_driver.toolkit.call_tool', fake_call_tool):
                    prepared = controller._prepare({'goal': 'x', 'effects': ['read']}, 'interpret', root / 'run', root / 'work', accepted)
                self.assertEqual(seen['image.bin'], str(acquired / 'interpret-input.elf'))
                # A preparation the toolchain refuses hands interpretation off empty with the reason.
                self.assertNotIn('blocked', prepared)
                self.assertIn('Could not prepare a sealed interpretation workspace: scripted stop', prepared['skip'])
            finally:
                controller.close()

    def _interpret_selection(self, names_and_bytes):
        from generative_driver.configurator import Controller
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                acquired = root / 'acquire'; acquired.mkdir()
                for name, data in names_and_bytes:
                    (acquired / name).write_bytes(data)
                accepted = [{'stage': 'acquire', 'artifacts': [{'path': str(acquired / n), 'sha256': '', 'kind': 'file'}
                                                               for n, _ in names_and_bytes]}]
                seen = {}
                def fake_call_tool(name, arguments):
                    seen.update(arguments['inputs'])
                    for key in ('BINDING_CONTEXT.json', 'TASKS.json'):
                        if key in arguments['inputs']:
                            seen[key] = json.loads(Path(arguments['inputs'][key]).read_text())
                    return {'ok': False, 'reason': 'scripted stop'}
                with patch('generative_driver.toolkit.call_tool', fake_call_tool):
                    prepared = controller._prepare({'goal': 'x', 'effects': ['read']}, 'interpret', root / 'run', root / 'work', accepted)
                return seen, prepared, acquired
            finally:
                controller.close()

    def test_listen_only_acquisition_is_not_sealed_as_an_image(self):
        seen, prepared, _ = self._interpret_selection([('listen-1.raw', b'ESP-ROM:esp32\r\n'), ('listen-1.json', b'{}')])
        self.assertEqual(seen, {})
        self.assertNotIn('blocked', prepared)
        self.assertIn('no single acquired binary', prepared['skip'])

    def test_interpret_selects_named_image_after_acceptance_prefixing(self):
        seen, _, acquired = self._interpret_selection([
            ('2-app.bin', b'app'), ('3-app.elf', b'elf'), ('5-flash.bin', b'flash'),
            ('6-interpret-input.elf', b'elf')])
        self.assertIn('image.bin', seen)
        self.assertEqual(seen['image.bin'], str(acquired / '6-interpret-input.elf'))

    def test_listen_plus_imported_image_selects_the_image(self):
        seen, prepared, acquired = self._interpret_selection(
            [('listen-1.raw', b'boot text'), ('listen-1.json', b'{}'), ('image.bin', b'\xe9firmware')])
        self.assertEqual(seen['image.bin'], str(acquired / 'image.bin'))

    def test_interpret_receives_observed_serial_settings_as_sealed_evidence(self):
        trace = json.dumps({'schema': 'serial-listen/1', 'baudrate': 115200,
                            'unsolicited_output': True, 'text': 'boot evidence'}).encode()
        seen, _, acquired = self._interpret_selection([
            ('0-listen-1.json', trace), ('1-listen-1.raw', b'boot evidence'),
            ('2-interpret-input.elf', b'elf')])
        self.assertIn('serial-listen.json', seen)
        self.assertEqual(seen['serial-listen.json'], str(acquired / '0-listen-1.json'))

    def test_interpret_binding_context_declares_observed_uart_and_reset(self):
        trace = json.dumps({'schema': 'serial-listen/1', 'baudrate': 115200,
                            'unsolicited_output': True, 'text': 'boot evidence'}).encode()
        seen, _, _ = self._interpret_selection([
            ('0-listen-1.json', trace), ('2-interpret-input.elf', b'elf')])
        self.assertIn('BINDING_CONTEXT.json', seen)
        self.assertEqual(seen['BINDING_CONTEXT.json']['runtime_channel'],
                         {'type': 'uart', 'baudrate': 115200, 'resets_on_open': True})

    def test_interpret_receives_requested_behavior_and_effect_grants(self):
        seen, _, _ = self._interpret_selection([('interpret-input.elf', b'elf')])
        self.assertIn('TASKS.json', seen)
        self.assertEqual(seen['TASKS.json']['tasks']['requested']['meaning'], 'x')
        self.assertEqual(seen['TASKS.json']['effect_grants'], ['read'])

    def test_acquire_context_explains_listen_and_reset_without_the_port(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            controller = Controller(root / 'home')
            try:
                (root / 'work').mkdir()
                prepared = controller._prepare({'goal': 'x', 'effects': ['read', 'reset'], 'binding': {'port': '/dev/cu.secret'}},
                                               'acquire', root / 'run', root / 'work', [])
                text = json.dumps(prepared['context'])
                self.assertIn('acquire_serial_listen', text)
                self.assertIn('reset', text)
                self.assertNotIn('/dev/cu.secret', text)
            finally:
                controller.close()


class DeviceReleaseAndRecoveryTests(unittest.TestCase):
    """Feature 3: a device is never held or left running without a way out."""

    def test_03_B2_restart_blocked_run_without_uncertain_effect_keeps_binding_claimed_until_resumed_or_cancelled(self):
        from generative_driver.configurator import Controller, SafetyError
        binding = {'host': '127.0.0.1', 'port': 45679}
        params = {'goal': 'Restart lease contract', 'binding': binding, 'effects': ['read'],
                  'executor_config': {'command': [sys.executable, '-c', 'import time; time.sleep(30)']}}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / 'home'
            controller = Controller(home)
            try:
                run = controller.call('start', params)
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    if controller.call('status', run)['status'] == 'running':
                        break
                    time.sleep(.02)
                self.assertEqual(controller.call('status', run)['status'], 'running')
                # Simulate a hard crash: close without stopping the run cleanly (status stays 'running', bindings stays).
                with controller._db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM bindings WHERE run_id=?',
                                                (run['run_id'],)).fetchone()[0], 1)
                worker = controller._workers.pop(run['run_id'])
                worker[0].set()
                worker[1].join(5)
                with controller._db() as db:
                    db.execute("UPDATE runs SET status='running', cancelled=0, uncertain=0 WHERE id=?", (run['run_id'],))
                    controller._claim_binding(binding, run['run_id'], db)
            finally:
                controller.close()
            reopened = Controller(home)
            try:
                status = reopened.call('status', run)
                self.assertEqual((status['status'], status['uncertain_effect']), ('blocked', False), status)
                # Another run MUST NOT claim the binding while the restart-blocked run may still have an old child.
                with self.assertRaisesRegex(SafetyError, 'already owned'):
                    reopened.call('start', params)
                # Cancelling the blocked run releases the binding cleanly for a new run.
                reopened.call('cancel', run)
                second = reopened.call('start', params)
                self.assertTrue(second['ok'], second)
                reopened.call('cancel', second)
            finally:
                reopened.close()

    def test_03_B6_respond_holds_controller_lock_across_worker_check_and_resolution(self):
        import threading
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory).resolve() / 'home')
            try:
                run, assignment = ConfiguratorTests()._idle_run(controller, effects=['read', 'write'])
                ConfiguratorTests._pending_write(controller, run, assignment)
                controller.call('cancel', run)
                wait_stopped(controller, run)
                lock_held = []
                real_resolve = controller.dispatch_record.resolve_by_operator
                def check_lock(*args, **kwargs):
                    # Try acquiring _lock from another thread; must fail if respond holds _lock across resolution.
                    acquired = []
                    def probe():
                        got = controller._lock.acquire(blocking=False)
                        if got:
                            controller._lock.release()
                        acquired.append(got)
                    t = threading.Thread(target=probe)
                    t.start()
                    t.join(2)
                    lock_held.append(not acquired[0])
                    return real_resolve(*args, **kwargs)
                with patch.object(controller.dispatch_record, 'resolve_by_operator', side_effect=check_lock):
                    controller.call('respond', {**run, 'effect_resolution': 'confirmed_safe',
                                                'observation': ConfiguratorTests.STATEMENT})
                self.assertEqual(lock_held, [True], 'respond did not hold _lock during resolve_by_operator')
            finally:
                controller.close()

    def test_03_B7_spawn_failure_and_cleanup_failure_never_leave_run_stuck_in_workers(self):
        import threading
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        binding = {'host': '127.0.0.1', 'port': 45680}
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory).resolve() / 'home')
            try:
                # Part 1: Thread.start failure in _spawn rolls back _workers, marks run failed, releases binding.
                with patch.object(threading.Thread, 'start', side_effect=RuntimeError("can't start new thread")):
                    with self.assertRaisesRegex(RuntimeError, "can't start new thread"):
                        controller.call('start', {'goal': 'Spawn fail contract', 'binding': binding,
                                                  'executor_config': {'command': [sys.executable, '-c', 'pass']}})
                self.assertEqual(controller._workers, {})
                with controller._db() as db:
                    row = db.execute('SELECT id, status FROM runs').fetchone()
                    leased = db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0]
                self.assertEqual((row['status'], leased), ('failed', 0))
                # Resume works once Thread.start recovers.
                resumed = controller.call('resume', {'run_id': row['id']})
                self.assertIn(resumed['status'], ('queued', 'running', 'completed', 'failed', 'blocked'))
                wait_stopped(controller, {'run_id': row['id']})
                # Part 2: Cleanup raising inside _run finally still pops _workers so shutdown/resume never wedge.
                with patch.object(controller, '_settle_release', side_effect=RuntimeError('cleanup exploded')):
                    run2 = controller.call('start', {'goal': 'Cleanup fail contract', 'binding': binding,
                                                     'executor_config': {'command': [sys.executable, '-c', 'raise SystemExit(1)']}})
                    wait_worker_exit(controller, run2)
                self.assertNotIn(run2['run_id'], controller._workers)
                self.assertFalse(controller.call('status', run2)['stopping'])
                self.assertTrue(controller.close(timeout=2))
            finally:
                controller.close()


if __name__ == '__main__':
    unittest.main()
