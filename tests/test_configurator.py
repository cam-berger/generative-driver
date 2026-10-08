"""Public configurator behavior with a deliberately unavailable external runtime."""
from pathlib import Path
import contextlib
import json
import tempfile
import time
import unittest
import sys


def wait_stopped(controller, run, seconds=10):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        result = controller.call('result', run)
        if result['status'] not in ('queued', 'running') and not result['stopping']:
            return result
        time.sleep(.02)
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

    def test_benchmark_adapter_verdicts_keep_their_gated_path_until_part_b(self):
        # Adapter checks still grade against truth; Part B replaces them with truth-free checks.
        route = {'ok': False, 'route': 'interpret', 'fault': 'model', 'reason': 'defect', 'feedback': {'f': 1}}
        outcome = self.decide('interpret', checked=route, repairs=0)
        self.assertEqual((outcome.kind, outcome.diff), ('revise', {'f': 1}))
        spent = self.decide('probe', checked=route, repairs=2)
        self.assertEqual((spent.kind, spent.category), ('stop', 'model'))
        for fault, category in (('host', 'host'), ('operator', 'operator'), ('model', 'model'), (None, 'unknown')):
            with self.subTest(fault=fault):
                outcome = self.decide('emit', checked={'ok': False, 'fault': fault, 'reason': 'adapter'})
                self.assertEqual((outcome.kind, outcome.category, outcome.reason), ('stop', category, 'adapter'))


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
                    until = time.monotonic() + 10
                    while run['run_id'] in controller._workers:
                        self.assertLess(time.monotonic(), until, controller.call('result', run))
                        time.sleep(.02)
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
        from generative_driver.configurator import Controller, digest
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage == 'interpret':
                model = workspace / 'model'; model.mkdir()
                (model / 'model.json').write_text('{"schema": "scripted-model"}')
                return {'ok': True, 'artifacts': [str(model)], 'checks': []}
            if stage == 'probe':
                return {'ok': True, 'artifacts': [str(workspace / 'out.json')], 'checks': []}
            if stage == 'emit':
                package = workspace / 'package'; package.mkdir()
                (package / 'manifest.json').write_text('{"fixture": "scripted package"}')
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
                # Ground handed off nothing; emit still receives the model and the latest probe records.
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
                for stage in ('ground', 'emit'):
                    with self.subTest(stage=stage):
                        selected, missing = controller._stage_inputs(stage, accepted)
                        self.assertEqual(selected, [str(model), str(records)])
                        self.assertEqual(missing, [])
                (root / 'emit').mkdir()
                prepared = controller._prepare(spec, 'emit', root / 'run', root / 'emit', accepted)
                self.assertEqual(sorted(digest(p) for p in prepared['inputs']), sorted([digest(model), digest(records)]))
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
    return {'schema': 'interface-model/3', 'device': {'name': name}, 'channel': {'type': 'tcp'},
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
        refuted = 'refuted        measure reads 0xf7  frame 0xf7 returned 3 byte(s), model reads 8'
        def event(dispatch, name, ok, result, *, connected=None, model_sha='model-sha', artifacts=None, **shas):
            connected = name in ('probe_run', 'interface_execute') if connected is None else connected
            return {'name': name, 'ok': ok, 'dispatch_id': dispatch, 'result': result, 'connected': connected,
                    'argument_sha256': {'model_dir': model_sha, **shas}, 'artifacts': artifacts or {}}
        live_fault = lambda path, results: {'ok': False, 'fault': 'model', 'error_code': 'framing', 'error_message': 'short reply',
                                            'evidence_kind': 'live', 'probe': path, 'results': results, '_exit': 1}
        identity = lambda answered: {'ok': False, 'identity': answered, 'identity_ok': False, 'probe': probe('probe.json'), '_exit': 2}
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
            event('13', 'probe_run', False, {'ok': False, 'error': 'nack: no acknowledge at 0x76', '_exit': 3}),
            event('14', 'probe_run', False, {'ok': False, 'error': 'bus: USB fault', '_exit': 4}),
            event('15', 'probe_run', False, {'ok': False, 'error': 'schema 3 probe requires an explicit operation name', '_exit': 1}),
            event('16', 'probe_run', True, {'ok': True, 'evidence_kind': 'live', 'probe': probe('interface-2.json'), '_exit': 0}),
            # A capture the worker supplied is not something the configurator saw the device do; it is a note.
            event('17', 'probe_observe', False, {'confirmed': 1, 'refuted': 1, 'not_observed': 0, '_exit': 1,
                                                 'lines': ['confirmed      identify reads 0xd0', refuted]}, bus='capture-sha'),
            event('18', 'probe_observe', False, {'supported': False, 'reason': 'probe_observe covers spi captures', '_exit': 1}, bus='capture-sha'),
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
            # A register probe always writes probe.json: the later run replaces the earlier record, which is no longer
            # there to hand on. What the device did stays in the diff, but only the surviving record is named.
            event('40', 'probe_run', False, identity('0x58'), artifacts={probe('probe.json'): 'register-1-sha'}),
            event('41', 'probe_diff', False, {'ok': False, 'defects': defects, '_exit': 1}, probe='register-1-sha'),
            event('42', 'probe_run', False, identity('0x59'), artifacts={probe('probe.json'): 'register-2-sha'})]
        contract = [{'where': 'operations.read', 'what': 'requires 1–128 steps'},
                    {'where': 'convert.py', 'what': 'will not import: ' + str(Path(model) / 'convert.py')}]
        contracts = {model: {'ok': False, 'defects': contract, '_exit': 1}}
        # The earlier stage's notes bound the handed-on record to the assigned model, which wrote it.
        diff = _device_diff(events, assignment, contracts, {'record-sha': {'model-sha'}})
        # A handed-on record that another model wrote, or that no notes bind, says nothing about the assigned model.
        for writers in ({'record-sha': {'variant-sha'}}, {}, None):
            self.assertEqual([entry for entry in _device_diff(events, assignment, contracts, writers)],
                             [entry for entry in diff if entry.get('dispatch_id') != '35'])
        self.assertEqual([(entry['source'], entry.get('dispatch_id')) for entry in diff],
                         [('model_contract', None), ('probe_diff', '7'), ('probe_run', '10'), ('probe_run', '13'),
                          ('interface_execute', '19'), ('probe_diff', '35'), ('probe_run', '40'), ('probe_diff', '41'),
                          ('probe_run', '42')])
        # The configurator's validation of the assigned model claims nothing about the device and names the model by content.
        self.assertEqual(diff[0], {'source': 'model_contract', 'model': {'name': '0-model', 'sha256': 'model-sha'},
                                   'defects': [contract[0], {'where': 'convert.py', 'what': 'will not import: ' + str(Path('0-model') / 'convert.py')}]})
        self.assertEqual(diff[1], {'source': 'probe_diff', 'dispatch_id': '7', 'defects': defects,
                                   'probe': {'name': 'interface-1.json', 'sha256': 'probe-10-sha'}})
        self.assertEqual({k: diff[2][k] for k in ('code', 'what', 'probe', 'failed')},
                         {'code': 'framing', 'what': 'short reply', 'failed': [failed],
                          'probe': {'name': 'interface-1.json', 'sha256': 'probe-10-sha'}})
        self.assertEqual(diff[3]['what'], 'nack: no acknowledge at 0x76')
        self.assertEqual((diff[4]['code'], diff[4]['what']), ('framing', 'short reply'))
        self.assertEqual(diff[5]['probe'], {'name': '1-0-interface-1.json', 'sha256': 'record-sha'})
        self.assertEqual(diff[5]['defects'][0]['evidence'], '1-0-interface-1.json')
        self.assertEqual([entry.get('probe') for entry in diff[6:]], [None, None, {'name': 'probe.json', 'sha256': 'register-2-sha'}])
        self.assertEqual((diff[6]['what'], diff[8]['what']), ("device identity '0x58' is not one the model expects",
                                                              "device identity '0x59' is not one the model expects"))
        self.assertFalse(any(str(ws) in text for text in strings(diff)))
        # The stage hands on the surviving record of every run the device answered on its connection, of any model.
        kept, superseded = _probe_records(events)
        self.assertEqual({Path(path).name: sha for path, (sha, _) in kept.items()},
                         {'interface-1.json': 'probe-10-sha', 'interface-5.json': 'probe-28-sha', 'probe.json': 'register-2-sha'})
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
                         max_revisions=None):
        """A generic run in which `stage` (probe or ground, or a tuple of both) uses the real preparation, gateway and
        check and every other stage is scripted; interpret hands on `model` (the valid thermometer by default) with the
        `extra` files (name to text) beside it. The stage worker calls tools
        through `work(call, model, workspace)`, or `work[stage](...)` when `work` maps stages to workers.
        `respond(name, arguments)` stands in for each worker tool except those
        named in `real`, which run in the real toolkit; a stand-in probe_run writes its probe file as the real tool does
        unless `respond` names one. The configurator's own validation of the assigned model always runs the real
        validator. `effects` and `max_revisions` are the run's grants and repair budget. No device runs. Returns the
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
                    (folder / name).write_text(text)
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
                run = controller.call('start', {'goal': 'Scripted ' + '/'.join(stages) + ' contract; no model or device',
                                                'binding': self.BINDING, 'executor_config': {'command': ['never-launched']},
                                                **({'effects': effects} if effects else {}),
                                                **({'max_revisions': max_revisions} if max_revisions is not None else {})})
                result = wait_stopped(controller, run)
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
            self.assertEqual(assigned['ground']['allowed_tools'], ['probe_run', 'interface_execute', 'probe_diff', 'probe_observe'])
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
        refuted = 'refuted        read_temperature reads 0xf7  frame 0xf7 returned 3 byte(s), model reads 8'
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
            if name == 'probe_diff':
                return {'ok': False, 'defects': defects, 'diagnostics': [], 'source_evidence_kind': 'live', '_exit': 1}
            return {'confirmed': 0, 'refuted': 1, 'not_observed': 0, 'lines': [refuted], '_exit': 1}
        def work(call, model, workspace):
            transcript = workspace / 'transcript.json'; transcript.write_text('{"schema": "interface-replay/1", "transactions": []}')
            variant = workspace / 'variant'; variant.mkdir()
            (variant / 'model.json').write_text(json.dumps(thermometer_model('Worker variant')))
            written = workspace / 'observed.json'; written.write_text('{}')
            capture = workspace / 'capture.json'; capture.write_text('{"transactions": []}')
            call('probe_run', model_dir=model, operation='read_temperature', replay=str(transcript))
            call('interface_execute', model_dir=model, operation='read_temperature', replay=str(transcript))
            call('interface_execute', model_dir=model, operation='read_temperature')
            variant_probe = call('probe_run', model_dir=str(variant), operation='read_temperature')['probe']
            call('probe_run', model_dir=str(variant), operation='identify')
            call('probe_diff', model_dir=model, probe_json=str(written))
            call('probe_diff', model_dir=model, probe_json=variant_probe)
            call('probe_observe', model_dir=model, bus=str(capture))
        with tempfile.TemporaryDirectory() as directory:
            result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, respond)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([name for name, _ in calls],
                         ['probe_run', 'interface_execute', 'interface_execute', 'probe_run', 'probe_run', 'probe_diff', 'probe_diff', 'probe_observe'])
        self.assertEqual((result['progress']['repairs'], result['progress']['revision']), (0, 0))
        self.assertEqual([h['revision'] for h in result['accepted_handoffs']], [0] * 6)
        self.assertFalse(any(e['kind'] == 'run.revision' for e in events))
        notes = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')['notes']['inline']
        self.assertEqual((notes['outcome']['kind'], notes['configurator']['device_diff']), ('advance', None))
        # A capture the worker supplied is kept as a note about that capture, never as a repair trigger.
        capture = next(c for c in notes['configurator']['checks'] if c['name'] == 'supplied_capture_observations')
        self.assertEqual((capture['passed'], capture['refuted']), (False, [refuted]))
        self.assertIn('supplied by the worker', capture['basis'])
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

    def test_probe_and_ground_workers_cannot_validate_an_assigned_model_in_place(self):
        # The register validator writes defects.json beside the model it checks, which would alter an assigned input
        # and stop the run for integrity. The configurator validates each assigned model on its own copy and hands the
        # result on, so probe and ground workers are not given the validator.
        for stage in ('probe', 'ground'):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                refused = []
                def work(call, model, workspace):
                    with self.assertRaisesRegex(ValueError, 'not assigned') as raised:
                        call('model_validate', model_dir=model)
                    refused.append(str(raised.exception))
                result, events, _, calls = self.run_device_stage(Path(directory) / 'home', work, None, stage=stage,
                                                                 model=self.REGISTER, extra={'convert.py': self.CONVERT})
                self.assertEqual(result['status'], 'completed', result)
                assigned = next(e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == stage)
                self.assertNotIn('model_validate', assigned['allowed_tools'])
                self.assertEqual((refused, calls), (['Tool is not assigned to this worker'], []))
                contract = next(c for c in next(h for h in result['accepted_handoffs'] if h['stage'] == stage)
                                ['notes']['inline']['configurator']['checks'] if c['name'] == 'executable_model_contract')
                self.assertTrue(contract['passed'], contract)

    def test_the_configurator_validates_a_copy_so_a_validator_that_writes_beside_the_model_alters_no_input(self):
        # The register validator writes defects.json beside the model it checks. Validating the assigned input in place
        # would change it, and the configurator's re-hash of the inputs after the check would stop the run.
        register = {'schema': 'interface-model/1', 'device': {'name': 'invented register sensor'},
                    'channel': {'type': 'i2c', 'address_7bit': '0x20', 'speed_hz': 100000},
                    'identity': {'from': 'id', 'expect': ['0x42']},
                    'operations': {'identify': [{'op': 'reg_read', 'reg': '0x00', 'len': 1, 'as': 'id'}],
                                   'measure': [{'op': 'reg_read', 'reg': '0x10', 'len': 1, 'as': 'raw'}]},
                    'conversion': {'module': 'convert.py', 'entry': 'convert', 'outputs': {'value': {'unit': 'count'}}},
                    'provenance': [{'item': 'operations.' + key, 'source': 'invented register contract'} for key in ('identify', 'measure')]}
        convert = 'def convert(inputs):\n    return {"value": inputs["0x10"][0] / 2}\n'
        with tempfile.TemporaryDirectory() as directory:
            result, events, _, _ = self.run_device_stage(Path(directory) / 'home', lambda call, model, workspace: None, None,
                                                         model=register, extra={'convert.py': convert})
            self.assertEqual(result['status'], 'completed', result)
            assigned = next(e['data'] for e in events if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'ground')
            path = next(p for p in assigned['inputs'] if (Path(p) / 'model.json').is_file())
            self.assertEqual(sorted(child.name for child in Path(path).iterdir()), ['convert.py', 'model.json'])
            notes = next(h for h in result['accepted_handoffs'] if h['stage'] == 'ground')['notes']['inline']
            self.assertEqual([(entry['source'], entry['defects']) for entry in notes['configurator']['device_diff']],
                             [('model_contract', [{'where': 'safety', 'what': 'missing'}])])

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

    def test_a_register_probe_that_rewrites_its_record_supersedes_it_and_only_a_changed_record_stops(self):
        from generative_driver.configurator import digest
        def respond(name, arguments):
            # A register probe always writes probe.json.
            path = Path(arguments['run_dir']) / 'probe' / 'probe.json'; path.parent.mkdir(parents=True, exist_ok=True)
            reading = json.loads(path.read_text())['reading'] + 1 if path.exists() else 0
            path.write_text(json.dumps({'identity_ok': True, 'reading': reading}))
            return {'ok': True, 'identity_ok': True, 'samples': [{'reading': reading}], 'probe': str(path), '_exit': 0}
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

    REGISTER = {'schema': 'interface-model/1', 'device': {'name': 'invented register sensor'},
                'channel': {'type': 'i2c', 'address_7bit': '0x76', 'speed_hz': 100000},
                'identity': {'from': 'id', 'expect': ['0x60']}, 'safety': {'notes': 'read only'},
                'operations': {'identify': [{'op': 'reg_read', 'reg': '0xd0', 'len': 1, 'as': 'id'}],
                               'measure': [{'op': 'reg_read', 'reg': '0x10', 'len': 1, 'as': 'raw'}]},
                'conversion': {'module': 'convert.py', 'entry': 'convert', 'outputs': {'value': {'unit': 'count'}}},
                'provenance': [{'item': 'operations.' + key, 'source': 'invented register contract'}
                               for key in ('init', 'identify', 'measure')]}
    CONVERT = 'def convert(inputs):\n    return {"value": inputs["0x10"][0] / 2}\n'

    def test_ground_compares_a_handed_on_record_only_when_the_assigned_model_wrote_it(self):
        """Probe hands on every record the device answered, each bound in its notes to the model that wrote it. Ground
        compares a handed-on record with the assigned model only when the assigned model wrote it: a record of the
        probe worker's own variant says what the device did for that variant, so it never drives a revise. The
        register probe_diff is real; probe_run stands in for a register bus (the device answers identity 0x60)."""
        from generative_driver.configurator import digest
        def respond(name, arguments):
            # A register probe of whatever model it is given; the variant reads twice and gets fresh bytes.
            expected = json.loads((Path(arguments['model_dir']) / 'model.json').read_text())['identity']['expect']
            ok = '0x60' in expected
            raws = ['10', '10'] if ok else ['10', '12']
            path = Path(arguments['run_dir']) / 'probe' / 'probe.json'; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'address': '0x76', 'identity': '0x60', 'identity_ok': ok, 'evidence_kind': 'live',
                                        'samples': [{'raw': raw, 'values': {'value': 8.0}} for raw in raws]}))
            return {'ok': ok, 'address': '0x76', 'identity': '0x60', 'identity_ok': ok, 'samples': [{'value': 8.0}] * 2,
                    'probe': str(path), '_exit': 0 if ok else 2}
        variants = []
        def probe(own):
            def work(call, model, workspace):
                if own:
                    variant = workspace / 'variant'; variant.mkdir()
                    (variant / 'model.json').write_text(json.dumps({**self.REGISTER, 'identity': {'from': 'id', 'expect': ['0x58']}}))
                    (variant / 'convert.py').write_text(self.CONVERT)
                    variants.append(digest(variant))
                    model = str(variant)
                call('probe_run', model_dir=model)
            return work
        diffs = []
        def ground(call, model, workspace):
            for record in sorted(str(p) for p in (workspace / 'inputs').iterdir() if p.suffix == '.json'):
                diffs.append(call('probe_diff', model_dir=model, probe=record))
        for name, own in (("the probe worker's own variant wrote the record", True), ('the assigned model wrote it', False)):
            variants.clear(); diffs.clear()
            with self.subTest(plan=name), tempfile.TemporaryDirectory() as directory:
                result, events, _, _ = self.run_device_stage(Path(directory) / 'home', {'probe': probe(own), 'ground': ground},
                    respond, stage=('probe', 'ground'), model=self.REGISTER, extra={'convert.py': self.CONVERT},
                    real=('probe_diff',), max_revisions=1)
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
                # The real register probe_diff finds a difference either way.
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
            # Only an interface model is refused by the runtime before the device; a register model's validation
            # defects (a missing safety entry, say) are the configurator's findings, not a runtime refusal.
            self.assertIn("the configurator's own validation", context['feedback_meaning'])
            self.assertIn('for an interface model, the runtime refuses such a model before it opens the device',
                          context['feedback_meaning'])
            for words in ('Start from previous-model/', 'model.json', 'replies.json', 'workspace root', 'NOTES.md'):
                self.assertIn(words, context['instructions'])
            assigned = [e['data'] for e in controller.call('events', run)['events']
                        if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'interpret']
            self.assertEqual(sorted(Path(p).relative_to(second).as_posix() for p in assigned[1]['inputs']
                                    if Path(p).relative_to(second).parts[0] != '_toolkit'),
                             ['BRIEF.md', 'INTERFACE_MODEL.md', 'REPAIR_CONTEXT.json', 'defects.json', 'image.bin',
                              'image_map_check.py', 'previous-model/model.json', 'qualify.py', 'validate.py'])
            self.assertEqual(assigned[1]['collection']['expected_seal_sha256'], digest(Path(assigned[1]['collection']['run_dir']) / 'interpret/attempt1.seal.json'))

    def test_repair_context_names_every_sealed_repair_input_and_editing_one_stops_for_integrity(self):
        # The repair agent is told which inputs are sealed and what changing one does; the configurator holds it to that.
        for name in ('defects.json', 'REPAIR_CONTEXT.json', 'previous-model/model.json'):
            with self.subTest(edited=name):
                contexts = []
                def edit(workspace):
                    contexts.append(json.loads((workspace / 'REPAIR_CONTEXT.json').read_text(encoding='utf-8')))
                    (workspace / name).write_text('{"addressed": true}', encoding='utf-8')
                with self.repaired(edit) as (controller, run, result, sealed, probes, home):
                    context, = contexts
                    self.assertEqual(context['sealed_inputs'], ['image.bin', 'previous-model/', 'defects.json', 'REPAIR_CONTEXT.json'])
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
                (model / 'model.json').write_text(json.dumps({'schema': 'scripted-model', 'revision': assignment['revision']}))
                return {'ok': True, 'artifacts': [str(model)], 'checks': []}
            if stage == 'probe':
                return {'ok': True, 'artifacts': [str(workspace / 'out.json')], 'checks': [], 'device_diff': diff}
            return {'ok': True, 'artifacts': [], 'checks': []}
        def prepare(original, spec, stage, run_dir, workspace, accepted):
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


def driver_package(root, name='package'):
    """A minimal emitted package: its manifest and a driver model with one read operation."""
    package = Path(root) / name
    (package / 'driver').mkdir(parents=True)
    (package / 'manifest.json').write_text(json.dumps({'schema': 'interface-package/1', 'name': 'scripted'}))
    (package / 'driver' / 'model.json').write_text(json.dumps({'schema': 'scripted-model',
                                                               'operations': {'read_temperature': {'effect': 'read'}}}))
    return package


def final_verdicts(controller, run):
    with controller._db() as db:
        return [(row['id'], json.loads(row['payload'])) for row in
                db.execute("SELECT id,payload FROM verdicts WHERE run_id=? AND stage='finalize'", (run['run_id'],))]


class FinalVerdictTests(unittest.TestCase):
    """Each run ends with one functional-equivalence verdict, recorded once after reuse. A generic run's verdict is
    self-evidence: the emitted package passed emit_check replay and every live operation of fresh reuse succeeded."""
    BINDING = {'host': '127.0.0.1', 'port': 9}

    # Scripted live tool results in the toolchain's own shapes. None of them reaches a device.
    PASSED = {'schema': 'interface-live-test/1', 'ok': True, 'verdict': 'pass', 'passed': 1, 'failed': 0,
              'results': [{'ok': True, 'operation': 'read_temperature', 'fault': None}], 'live_record': 'live-1.json', '_exit': 0}
    MODEL_FAILURE = {'schema': 'interface-live-test/1', 'ok': False, 'verdict': 'fail', 'passed': 0, 'failed': 1,
                     'results': [{'ok': False, 'operation': 'read_temperature', 'error_code': 'expect', 'fault': 'model'}],
                     'live_record': 'live-1.json', '_exit': 1}
    # interface.emit_test_live raised before any call, as toolkit.call_tool returns it: no live record.
    TOOLKIT_REFUSAL = {'ok': False, '_exit': 1, 'error': 'parameters name an operation that is not tested'}
    # The plan stopped at a host fault (a timeout before identity): exit 4, inconclusive.
    HOST_FAULT = {'schema': 'interface-live-test/1', 'ok': False, 'verdict': 'inconclusive', 'passed': 0, 'failed': 1,
                  'results': [{'ok': False, 'operation': 'read_temperature', 'error_code': 'timeout', 'fault': 'host'}],
                  'live_record': 'live-1.json', '_exit': 4}
    # The runtime refused the operation before opening the transport.
    PREFLIGHT_REFUSAL = {'schema': 'interface-result/1', 'operation': 'read_temperature', 'ok': False, 'outputs': {},
                         'units': {}, 'identity_verified': False, 'transcript': [], 'transport_open_attempted': False,
                         'error': {'code': 'validation_error', 'message': 'argument violates parameter type or bounds',
                                   'fault': 'operator'}, '_exit': 1}
    # The runtime opened the device and the reply contradicted the model.
    EXECUTE_MODEL_FAILURE = {'schema': 'interface-result/1', 'operation': 'read_temperature', 'ok': False, 'outputs': {},
                             'units': {}, 'identity_verified': True, 'transcript': [], 'transport_open_attempted': True,
                             'error': {'code': 'expect', 'message': 'reply differs from the model', 'fault': 'model'}, '_exit': 1}
    # The package's own runtime refused the fresh agent's parameters before it opened the device (operator, exit 4),
    # in the shape of the toolchain's per-call summary.
    PACKAGE_REFUSAL = {'ok': False, 'passed': 0, 'failed': 1, 'not_run': [], 'verdict': 'inconclusive',
                       'results': [{'operation': 'read_temperature', 'ok': False, 'returncode': 1, 'error_code': 'validation_error',
                                    'error_message': 'arguments must match parameter names exactly', 'fault': 'operator',
                                    'identity_verified': False, 'outputs': {}, 'units': {}, 'transport_open_attempted': False}],
                       'live_record': 'live-1.json', '_exit': 4}
    # One package call: its first operation passed on the device, then the package's own runtime refused the fresh
    # agent's parameters for the second before it opened the device, so the plan stopped (operator, exit 4).
    PARTLY_REFUSED = {'ok': False, 'passed': 1, 'failed': 1, 'not_run': [], 'verdict': 'inconclusive',
                      'results': [{'operation': 'read_temperature', 'ok': True, 'returncode': 0, 'error_code': None,
                                   'error_message': None, 'fault': None, 'identity_verified': True,
                                   'outputs': {'temperature': 21.5}, 'units': {'temperature': 'C'}, 'transport_open_attempted': True},
                                  {'operation': 'read_pressure', 'ok': False, 'returncode': 1, 'error_code': 'validation_error',
                                   'error_message': 'arguments must match parameter names exactly', 'fault': 'operator',
                                   'identity_verified': False, 'outputs': {}, 'units': {}, 'transport_open_attempted': False}],
                      'live_record': 'live-1.json', '_exit': 4}
    # The runtime ran the operation on the device and the reply matched the model.
    EXECUTE_PASSED = {'schema': 'interface-result/1', 'operation': 'read_temperature', 'ok': True,
                      'outputs': {'temperature': 21.5}, 'units': {'temperature': 'C'}, 'identity_verified': True,
                      'transcript': [], 'transport_open_attempted': True, '_exit': 0}
    NEVER_RAN = 'fresh reuse never ran the frozen package with emit_test_live'

    @contextlib.contextmanager
    def finishing(self, controller, *, replay='passed', live=True, calls=None, attempts=None):
        """Acquire to ground are scripted. Emit and reuse use the real preparation and checks with scripted emit_check
        and live tools: the emit worker writes a package and the reuse worker runs it once on the device.

        `replay` is the emit_check outcome ('passed', 'failed' or 'seeded' for a register package's seeded check).
        `calls` scripts the reuse worker instead: (tool, result) pairs called in order, where the tool is emit_test_live
        of the frozen package or interface_execute of its model, and a result that is an exception is raised by the tool.
        A third element 'elsewhere' runs the tool on a changed copy the worker made in its workspace instead.
        `attempts` scripts several fresh-reuse attempts, one list of calls each: the runtime of every attempt but the
        last exits with an error after its calls, so the run stops for the host until it is resumed."""
        import shutil
        from unittest.mock import patch
        from generative_driver.configurator import digest
        real_check, tools = controller._check, []
        attempts = attempts or [list(calls) if calls is not None else [('emit_test_live', self.PASSED if live else self.MODEL_FAILURE)]]
        results = iter([call[1] for attempt in attempts for call in attempt])
        reused = []
        def fake_call_tool(name, arguments):
            tools.append(name)
            if name == 'emit_check':
                return ({'ok': True, '_exit': 0, 'runtime_current': True, 'seeds_run': 3} if replay == 'seeded' else
                        {'ok': replay == 'passed', '_exit': 0 if replay == 'passed' else 1, 'runtime_current': True,
                         'replay_status': replay})
            if name in ('emit_test_live', 'interface_execute'):
                result = next(results)
                if isinstance(result, BaseException):
                    raise result
                return json.loads(json.dumps(result))
            raise AssertionError('Unexpected tool ' + name)
        def prepare(original, spec, stage, run_dir, workspace, accepted):
            return original(spec, stage, run_dir, workspace, accepted) if stage in ('emit', 'reuse') else {'inputs': [], 'allowed_tools': []}
        def check(spec, stage, run_dir, workspace, report, accepted, assignment):
            if stage in ('emit', 'reuse'):
                return real_check(spec, stage, run_dir, workspace, report, accepted, assignment)
            return {'ok': True, 'artifacts': [], 'checks': []}
        def work(request):
            if request.stage == 'reuse':
                package = Path(task_of(request.prompt)['inputs'][0])
                for call in attempts[len(reused)]:
                    tool, target = call[0], package
                    if call[2:] == ('elsewhere',):
                        target = Path(request.workspace) / 'own-package'
                        if not target.exists():
                            shutil.copytree(package, target)
                            model = json.loads((target / 'driver' / 'model.json').read_text())
                            (target / 'driver' / 'model.json').write_text(json.dumps({**model, 'changed': True}))
                    arguments = ({'package_dir': str(target)} if tool == 'emit_test_live' else
                                 {'model_dir': str(target / 'driver'), 'operation': 'read_temperature'})
                    try:
                        controller.call('tool', {**gateway_identity(request), 'name': tool, 'arguments': arguments})
                    except RuntimeError:
                        pass
                reused.append(request.stage)
        def runtime(request):
            if request.stage == 'reuse' and len(reused) < len(attempts):
                return {'runtime': 'codex', 'status': 'failed', 'cause': 'exit_error', 'elapsed_seconds': 0, 'usage': None,
                        'reason': 'Configured runtime exited with an error'}
        def report(request):
            if request.stage == 'emit':
                package = driver_package(request.workspace)
                return {'status': 'completed', 'summary': 'scripted emission', 'checks': [], 'unresolved': [],
                        'artifacts': [{'path': str(package), 'sha256': digest(package), 'kind': 'package'}]}
        with scripted_stages(controller, check=check, prepare=prepare, work=work, report=report, runtime=runtime), \
             patch('generative_driver.toolkit.call_tool', fake_call_tool):
            yield tools

    def start(self, controller, **params):
        return controller.call('start', {'goal': 'Scripted final verdict; no model or device', 'binding': self.BINDING,
                                         'executor_config': {'command': ['never-launched']}, **params})

    def test_a_replayed_package_that_fresh_reuse_runs_on_the_device_is_equivalent_by_self_evidence(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller) as tools:
                    run = self.start(controller)
                    result = wait_stopped(controller, run)
                self.assertEqual((result['status'], result['outcome_category'], result['reason']),
                                 ('completed', 'completed', 'Final verdict: equivalent'), result)
                self.assertEqual(tools, ['emit_check', 'emit_test_live'])
                package = next(a for h in result['accepted_handoffs'] if h['stage'] == 'emit' for a in h['artifacts'])
                equivalence = result['equivalence']
                self.assertEqual((equivalence['equivalent'], equivalence['basis'], equivalence['diff']), (True, 'self-evidence', []))
                checks = {c['id']: c for c in equivalence['checks']}
                self.assertEqual(list(checks), ['emitted_package_replay', 'fresh_reuse_live_operations'])
                self.assertTrue(all(c['passed'] for c in checks.values()))
                self.assertEqual(checks['emitted_package_replay']['package'], {'name': Path(package['path']).name, 'sha256': package['sha256']})
                self.assertTrue(checks['emitted_package_replay']['basis'].startswith('emit_check replay of the recorded device exchanges'),
                                checks['emitted_package_replay'])
                self.assertEqual([(o['name'], o['ok'], o['outcome'], o['emitted_package'])
                                  for o in checks['fresh_reuse_live_operations']['operations']],
                                 [('emit_test_live', True, 'succeeded', True)])
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

    def test_a_package_checked_only_against_its_model_has_no_replay_and_is_not_equivalent(self):
        """Ruling 7 needs emit_check replay of the recorded device exchanges. A seeded package-versus-model check
        compares the package with its own model, not with the device, so it is labelled and never counted as replay."""
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory) / 'home')
            try:
                with self.finishing(controller, replay='seeded'):
                    run = self.start(controller)
                    result = wait_stopped(controller, run)
                equivalence = result['equivalence']
                self.assertEqual((result['status'], equivalence['equivalent']), ('completed', False), result)
                replay, operations = equivalence['checks']
                self.assertEqual((replay['id'], replay['passed']), ('emitted_package_replay', False))
                self.assertTrue(replay['basis'].startswith('emit_check seeded package-versus-model check'), replay)
                # Fresh reuse is still judged and reported; only the replay condition is unmet.
                self.assertIs(operations['passed'], True)
                reason = ('the emitted package passed emit_check without a replay of the recorded device exchanges '
                          '(seeded package-versus-model check)')
                self.assertEqual(equivalence['diff'], [{'id': 'emitted_package_replay', 'reason': reason}])
                self.assertEqual(result['reason'], 'Final verdict: not equivalent: ' + reason)
            finally:
                controller.close()

    def test_an_emit_check_that_recorded_no_replay_establishes_none(self):
        # An emit handoff from before the check named how it verified the package cannot show a replay.
        from generative_driver.configurator import _self_evidence, digest
        with tempfile.TemporaryDirectory() as directory:
            package = driver_package(Path(directory))
            emit = {'stage': 'emit', 'revision': 0, 'artifacts': [{'path': str(package), 'sha256': digest(package)}],
                    'checks': [{'name': 'package_integrity_and_replay', 'passed': True}]}
            evidence = _self_evidence([emit], [])
        replay = evidence['checks'][0]
        self.assertEqual((evidence['equivalent'], replay['passed'], replay['basis']),
                         (False, False, 'emit_check, run by the configurator at emit'))
        self.assertEqual(evidence['diff'][0], {'id': 'emitted_package_replay', 'reason': 'the emitted package passed '
                                               'emit_check without a replay of the recorded device exchanges'})

    def test_a_failed_live_reuse_or_an_unreplayed_package_completes_not_equivalent(self):
        from generative_driver.configurator import Controller, _has_terminal_final_failure
        for name, options, first in (('failed live operation', {'live': False}, 'fresh_reuse_live_operations'),
                                     ('model fault after an inconclusive call',
                                      {'calls': [('emit_test_live', self.HOST_FAULT), ('emit_test_live', self.MODEL_FAILURE)]},
                                      'fresh_reuse_live_operations'),
                                     ('replay failed', {'replay': 'failed'}, 'emitted_package_replay')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, **options) as tools:
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('completed', 'completed'), result)
                    self.assertTrue(result['reason'].startswith('Final verdict: not equivalent: '), result['reason'])
                    equivalence = result['equivalence']
                    self.assertEqual((equivalence['equivalent'], equivalence['basis']), (False, 'self-evidence'))
                    self.assertEqual(equivalence['diff'][0]['id'], first)
                    self.assertEqual({d['id'] for d in equivalence['diff']}, {c['id'] for c in equivalence['checks'] if not c['passed']})
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
                    else:
                        failed = [e['data']['dispatch_id'] for e in controller.call('events', run)['events']
                                  if e['kind'] == 'tool.finished' and e['data']['name'] == 'emit_test_live'][-1]
                        self.assertEqual(equivalence['diff'][0]['reason'], 'emit_test_live failed on the device (dispatch ' + failed + ')')
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                    # A verdict of not equivalent is a completed run, never a terminal failure to resume around.
                    with controller._db() as db:
                        self.assertFalse(_has_terminal_final_failure(db, run['run_id']))
                    with self.assertRaisesRegex(ValueError, 'Only a stopped incomplete run can resume'):
                        controller.call('resume', run)
                finally:
                    controller.close()

    def operations(self, result):
        check = next(c for c in result['equivalence']['checks'] if c['id'] == 'fresh_reuse_live_operations')
        return [(o['name'], o['outcome']) for o in check['operations']]

    def test_a_call_refused_before_the_device_was_contacted_is_not_a_live_operation(self):
        from generative_driver.configurator import Controller
        for name, calls, equivalent, outcomes in (
                ('toolchain refusal, then the frozen package passes',
                 [('emit_test_live', self.TOOLKIT_REFUSAL), ('emit_test_live', self.PASSED)], True, ['refused', 'succeeded']),
                ('runtime preflight refusal, then the frozen package passes',
                 [('interface_execute', self.PREFLIGHT_REFUSAL), ('emit_test_live', self.PASSED)], True, ['refused', 'succeeded']),
                ('package runtime refusal, then the frozen package passes',
                 [('emit_test_live', self.PACKAGE_REFUSAL), ('emit_test_live', self.PASSED)], True, ['refused', 'succeeded']),
                # The refused operation is not a live operation; the one that ran on the device passed.
                ('one package call that passed on the device, then refused its next operation before contact',
                 [('emit_test_live', self.PARTLY_REFUSED)], True, ['succeeded']),
                ('only refused calls', [('emit_test_live', self.TOOLKIT_REFUSAL), ('interface_execute', self.PREFLIGHT_REFUSAL)],
                 False, ['refused', 'refused'])):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', equivalent), result)
                    self.assertEqual([outcome for _, outcome in self.operations(result)], outcomes)
                    reasons = [d['reason'] for d in result['equivalence']['diff']]
                    self.assertFalse(any('failed on the device' in reason for reason in reasons), reasons)
                    if equivalent:
                        self.assertEqual((result['reason'], reasons), ('Final verdict: equivalent', []))
                    else:
                        self.assertEqual(reasons, [self.NEVER_RAN + ' (2 calls refused before the device was contacted)'])
                    # The configurator records its own reading of a runtime refusal on the event.
                    finished = {e['data']['name']: e['data'] for e in controller.call('events', run)['events'] if e['kind'] == 'tool.finished'}
                    if 'interface_execute' in finished:
                        self.assertIs(finished['interface_execute']['refused_before_open'], True)
                finally:
                    controller.close()

    def test_reuse_that_never_ran_the_frozen_package_with_emit_test_live_is_not_equivalent_and_says_so(self):
        """Fresh reuse must run the frozen package itself (emit_test_live). Running its driver directory as a model
        with interface_execute is a live operation that is judged, but it is not a run of the package, and the diff
        names that rule rather than claiming nothing live ran."""
        from generative_driver.configurator import Controller
        for name, calls, outcome, reasons in (
                ('interface_execute of the frozen driver passed', [('interface_execute', self.EXECUTE_PASSED)],
                 'succeeded', [self.NEVER_RAN]),
                ('interface_execute of the frozen driver failed', [('interface_execute', self.EXECUTE_MODEL_FAILURE)],
                 'failed', ['interface_execute failed on the device', self.NEVER_RAN])):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', False), result)
                    self.assertEqual(self.operations(result), [('interface_execute', outcome)])
                    diff = [d['reason'] for d in result['equivalence']['diff']]
                    self.assertEqual([reason.split(' (dispatch ')[0] for reason in diff], reasons)
                    self.assertFalse(any('no live operation' in reason for reason in diff), diff)
                    self.assertEqual(result['reason'], 'Final verdict: not equivalent: ' + '; '.join(diff))
                finally:
                    controller.close()

    def test_an_operator_or_host_fault_or_an_unrecorded_dispatch_leaves_the_verdict_unscored(self):
        from generative_driver.configurator import Controller
        for name, calls, outcome, detail in (
                ('host fault, then the frozen package passes', [('emit_test_live', self.HOST_FAULT), ('emit_test_live', self.PASSED)],
                 'inconclusive', 'host fault'),
                ('host fault only', [('emit_test_live', self.HOST_FAULT)], 'inconclusive', 'host fault'),
                ('dispatch never recorded, then the frozen package passes',
                 [('emit_test_live', RuntimeError('scripted gateway fault after dispatch')), ('emit_test_live', self.PASSED)],
                 'unresolved', 'never recorded')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=calls):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('completed', 'completed'), result)
                    self.assertIsNone(result['equivalence']['equivalent'], result['equivalence'])
                    self.assertEqual(self.operations(result)[0], ('emit_test_live', outcome))
                    started = [e['id'] for e in controller.call('events', run)['events']
                               if e['kind'] == 'tool.started' and e['data']['name'] == 'emit_test_live']
                    [entry] = result['equivalence']['diff']
                    self.assertEqual(entry['id'], 'fresh_reuse_live_operations')
                    self.assertIn(detail, entry['reason'])
                    self.assertIn('(dispatch ' + str(started[0]) + ')', entry['reason'])
                    self.assertNotIn('failed on the device', entry['reason'])
                    self.assertEqual(result['reason'], 'Final verdict: unscored: ' + entry['reason'])
                finally:
                    controller.close()

    def test_every_reuse_attempt_that_received_the_frozen_package_is_judged(self):
        """A fresh-reuse attempt that stopped before its handoff (its runtime exited with an error) and was resumed
        had already run the frozen package on the device. Its live operations count as the accepted attempt's do."""
        from generative_driver.configurator import Controller
        for name, first, equivalent, outcome, says in (
                ('driver failure', [('emit_test_live', self.MODEL_FAILURE)], False, 'failed', 'emit_test_live failed on the device'),
                ('host fault', [('emit_test_live', self.HOST_FAULT)], None, 'inconclusive', 'host fault'),
                ('dispatch never recorded', [('emit_test_live', RuntimeError('scripted gateway fault after dispatch'))],
                 None, 'unresolved', 'never recorded'),
                ('refusal before the device was contacted', [('emit_test_live', self.TOOLKIT_REFUSAL)], True, 'refused', None)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, attempts=[first, [('emit_test_live', self.PASSED)]]):
                        run = self.start(controller)
                        stopped = wait_stopped(controller, run)
                        self.assertEqual((stopped['status'], stopped['outcome_category'], stopped['stage']),
                                         ('failed', 'host', 'reuse'), stopped)
                        resume_when_idle(self, controller, run)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['outcome_category']), ('completed', 'completed'), result)
                    self.assertEqual(result['equivalence']['equivalent'], equivalent, result['equivalence'])
                    started = [e for e in controller.call('events', run)['events']
                               if e['kind'] == 'tool.started' and e['data']['name'] == 'emit_test_live']
                    self.assertEqual(len({e['data']['assignment_id'] for e in started}), 2)
                    self.assertEqual(self.operations(result), [('emit_test_live', outcome), ('emit_test_live', 'succeeded')])
                    reasons = [d['reason'] for d in result['equivalence']['diff']]
                    if says:
                        [reason] = reasons
                        self.assertIn(says, reason)
                        self.assertIn('(dispatch ' + str(started[0]['id']) + ')', reason)
                        self.assertTrue(result['reason'].endswith(reason), result['reason'])
                    else:
                        self.assertEqual((result['reason'], reasons), ('Final verdict: equivalent', []))
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                finally:
                    controller.close()

    def test_a_failure_outside_the_emitted_driver_is_never_counted_as_the_drivers(self):
        """Only bytes the configurator hashed before dispatch tie a failure to the driver: the frozen package for
        emit_test_live, or its driver directory for interface_execute. Another failure leaves the verdict unscored."""
        from generative_driver.configurator import Controller
        for name, first, equivalent, outcome, says in (
                ('interface_execute of the frozen driver', ('interface_execute', self.EXECUTE_MODEL_FAILURE),
                 False, 'failed', 'interface_execute failed on the device'),
                ('interface_execute of a model the worker changed', ('interface_execute', self.EXECUTE_MODEL_FAILURE, 'elsewhere'),
                 None, 'inconclusive', 'interface_execute was inconclusive: model fault outside the emitted driver'),
                ('emit_test_live of a package the worker changed', ('emit_test_live', self.MODEL_FAILURE, 'elsewhere'),
                 None, 'inconclusive', 'emit_test_live was inconclusive: model fault outside the emitted driver')):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                try:
                    with self.finishing(controller, calls=[first, ('emit_test_live', self.PASSED)]):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertEqual((result['status'], result['equivalence']['equivalent']), ('completed', equivalent), result)
                    self.assertEqual(self.operations(result), [(first[0], outcome), ('emit_test_live', 'succeeded')])
                    dispatch = next(e['id'] for e in controller.call('events', run)['events']
                                    if e['kind'] == 'tool.started' and e['data']['stage'] == 'reuse')
                    [entry] = result['equivalence']['diff']
                    self.assertTrue(entry['reason'].startswith(says), entry['reason'])
                    self.assertTrue(entry['reason'].endswith('(dispatch ' + str(dispatch) + ')'), entry['reason'])
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
                controller.call('respond', {**run, 'message': 'Checked the device', 'effect_resolution': 'confirmed_safe'})
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
                        until = time.monotonic() + 10
                        while run['run_id'] in controller._workers:
                            self.assertLess(time.monotonic(), until, controller.call('result', run))
                            time.sleep(.02)
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
                            reopened.call('respond', {**run, 'message': 'Checked the device', 'effect_resolution': 'confirmed_safe'})
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
        for mode in ('cleanup', 'sealing', 'uncertain'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                controller = Controller(Path(directory) / 'home')
                def fail_to_release(run_id, spec, run_dir):
                    if mode == 'sealing':
                        return [('evidence.sealing_failed', 'Evaluator evidence sealing failed: scripted', 'RuntimeError')]
                    if mode == 'uncertain':
                        with controller._db() as db:
                            db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run_id,))
                    # After the verdict the configurator owns no device resource: until B5 only generic runs have a
                    # verdict and own nothing, and B3 moves the emulator stop to the harness behind release().
                    raise RuntimeError('scripted release failure')
                try:
                    with self.finishing(controller), patch.object(controller, '_release', side_effect=fail_to_release):
                        run = self.start(controller)
                        result = wait_stopped(controller, run)
                    self.assertTrue(result['equivalence']['equivalent'], result)
                    self.assertEqual(len(final_verdicts(controller, run)), 1)
                    kind = 'evidence.sealing_failed' if mode == 'sealing' else 'cleanup.failed'
                    failed = [e['data'] for e in controller.call('events', run)['events'] if e['kind'] == kind]
                    self.assertEqual(len(failed), 1)
                    self.assertIn('RuntimeError' if mode == 'sealing' else 'scripted release failure', failed[0]['reason'])
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
                        until = time.monotonic() + 10
                        while run['run_id'] in controller._workers or controller._row(run['run_id'])['stage'] != 'finalize':
                            self.assertLess(time.monotonic(), until, controller.call('result', run))
                            time.sleep(.02)
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
                         {'ok': False, 'defects': [{'where': 'operations', 'what': 'missing'}]}, '{"schema": "interface-model/5"}', True),
                        ('unsupported schema', {'ok': False, 'sealed_inputs_unchanged': True, 'reason': 'no model.json with a supported schema'},
                         {'ok': False, 'defects': [{'where': 'schema', 'what': 'unsupported'}]}, '{"schema": "notes"}', True),
                        ('nothing written', {'ok': False, 'sealed_inputs_unchanged': True, 'reason': 'no model.json with a supported schema'},
                         None, None, False),
                        ('broken seal', {'ok': False, 'sealed_inputs_unchanged': False, 'reason': 'sealed inputs changed during the run: [image.bin]'},
                         None, '{"schema": "interface-model/5"}', None)):
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
                run=controller.call('start',{'goal':'Budget extension contract','budget_seconds':.3,
                    'executor_config':{'command':['missing-contract-runtime']}})
                until=time.monotonic()+5
                while time.monotonic()<until:
                    original=controller.call('result',run)
                    if original['status']=='blocked' and time.time()>original['created']+.4:break
                    time.sleep(.01)
                with self.assertRaisesRegex(ValueError,'budget exhausted'):
                    controller.call('resume',run)
                for invalid in (True,0,-1,float('inf'),float('nan'),604801,10**1000,.1):
                    with self.subTest(budget=repr(invalid)), self.assertRaises(ValueError):
                        controller.call('resume',{**run,'budget_seconds':invalid,'budget_reason':'Invalid test request'})
                with self.assertRaisesRegex(ValueError,'budget_reason'):
                    controller.call('resume',{**run,'budget_seconds':30,'budget_reason':' '})
                self.assertEqual(controller.call('result',run)['budget_seconds'],.3)
                controller.call('resume',{**run,'budget_seconds':30,'budget_reason':'Operator approved restoring paused time'})
                until=time.monotonic()+5
                while time.monotonic()<until:
                    result=controller.call('result',run)
                    if result['status']=='blocked' and len(result['worker_reports'])==2:break
                    time.sleep(.01)
                self.assertEqual(result['run_id'],original['run_id'])
                self.assertEqual(result['created'],original['created'])
                self.assertEqual(result['budget_seconds'],30)
                self.assertEqual(result['worker_reports'][0],original['worker_reports'][0])
                self.assertEqual(result['accepted_handoffs'],original['accepted_handoffs'])
                events=controller.call('events',run)['events']
                extension=[e['data'] for e in events if e['kind']=='run.budget_extended']
                self.assertEqual(len(extension),1)
                self.assertEqual(extension[0]['old_budget_seconds'],.3)
                self.assertEqual(extension[0]['new_budget_seconds'],30)
                self.assertAlmostEqual(extension[0]['extension_seconds'],29.7)
                self.assertAlmostEqual(extension[0]['old_deadline'],original['created']+.3)
                self.assertAlmostEqual(extension[0]['new_deadline'],original['created']+30)
                self.assertEqual(extension[0]['reason'],'Operator approved restoring paused time')
                self.assertFalse(any(e['kind']=='run.authorization' for e in events))
                until=time.monotonic()+5
                while time.monotonic()<until:
                    try:
                        controller.call('resume',{**run,'budget_seconds':30,'budget_reason':'Operator approved restoring paused time'})
                        break
                    except ValueError as exc:
                        self.assertIn('stopping',str(exc));time.sleep(.01)
                self.assertEqual(controller.call('result',run)['budget_seconds'],30)
                self.assertEqual(len([e for e in controller.call('events',run)['events'] if e['kind']=='run.budget_extended']),1)
            finally:
                controller.close()

    def test_physical_case_receives_the_operator_binding_before_worker_execution(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(Path(directory))
            try:
                binding={'url':'ftdi://fixture-no-device/1'}
                run=controller.call('start',{'goal':'No hardware contract','case':'bme280','binding':binding,'effects':['write'],
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
                with self.assertRaisesRegex(ValueError,'conflicting'):
                    controller.call('start',{**params,'binding':{'port':'COM9'},'case_options':{'binding':{'port':'COM10'}}})
            finally:
                controller.close()

    def test_emulator_approval_is_explicit_persisted_and_cannot_authorize_physical_runs(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(Path(directory))
            try:
                params={'goal':'Approval contract only','case':'tq9','executor_config':{'command':['missing-contract-runtime']}}
                run=controller.call('start',params)
                until=time.monotonic()+5
                while time.monotonic()<until and controller.call('status',run)['status']!='blocked': time.sleep(.01)
                self.assertIsNone(controller.call('result',run).get('scoped_tool_approval'))
                until=time.monotonic()+5
                while time.monotonic()<until:
                    try:
                        controller.call('resume',{**run,'scoped_tool_approval':'emulator'})
                        break
                    except ValueError as exc:
                        self.assertIn('stopping',str(exc)); time.sleep(.01)
                self.assertEqual(controller.call('result',run).get('scoped_tool_approval'),'emulator')
                events=controller.call('events',run)['events']
                self.assertEqual(len([e for e in events if e['kind']=='run.authorization']),1)
                self.assertEqual(len([e for e in events if e['kind']=='operator.response']),0)
                with self.assertRaisesRegex(ValueError,'emulator'):
                    controller.call('start',{**params,'case':'bme280','scoped_tool_approval':'emulator'})
                with self.assertRaisesRegex(ValueError,'binding'):
                    controller.call('start',{**params,'scoped_tool_approval':'emulator','binding':{'url':'ftdi://ftdi:232h/1'}})
            finally:
                controller.close()

    def test_recovery_preserves_uncertainty_until_operator_reconciles_it(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as directory:
            controller=Controller(Path(directory))
            run=controller.call('start',{'goal':'Scripted recovery contract','effects':['read','write'],
                'executor_config':{'command':[sys.executable,'-c','import time; time.sleep(20)']}})
            until=time.monotonic()+3
            while time.monotonic()<until and controller.call('status',run)['status']!='running':
                time.sleep(.01)
            controller.close()
            reopened=Controller(Path(directory))
            try:
                state=reopened.call('status',run)
                self.assertEqual(state['status'],'blocked')
                self.assertTrue(state['uncertain_effect'])
                with self.assertRaisesRegex(ValueError,'uncertain'):
                    reopened.call('resume',run)
                self.assertEqual(len([e for e in reopened.call('events',run)['events'] if e['kind']=='stage.assigned']),1)
                reconciled=reopened.call('respond',{**run,'observation':{'source':'scripted operator','effect_resolution':'confirmed_safe','evidence':'No device attached in contract test'}})
                self.assertFalse(reconciled['uncertain_effect'])
                self.assertIn(reopened.call('resume',run)['status'],('queued','running'))
            finally:
                reopened.close()

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
                    (model_dir / 'model.json').write_text(json.dumps({'channel': {'type': 'uart', 'baudrate': 115200},
                        'operations': {'read': {'effect': 'read'}}}))
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
                (model_dir / 'model.json').write_text(json.dumps({'channel': {'type': 'usb'}, 'operations': {'read': {'effect': 'read'}}}))
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
                      'case_options':{'evaluator_password_file':'not-an-agent-input'}}
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
            self.assertNotIn('not-an-agent-input',str(result))
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

    def test_listen_plus_imported_image_selects_the_image(self):
        seen, prepared, acquired = self._interpret_selection(
            [('listen-1.raw', b'boot text'), ('listen-1.json', b'{}'), ('image.bin', b'\xe9firmware')])
        self.assertEqual(seen['image.bin'], str(acquired / 'image.bin'))

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


if __name__ == '__main__':
    unittest.main()
