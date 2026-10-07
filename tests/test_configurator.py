"""Public configurator behavior with a deliberately unavailable external runtime."""
from pathlib import Path
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


class ConfiguratorTests(unittest.TestCase):
    def _scripted_generic(self, root, report, *, alter=False, command=None):
        """Drive a generic run with an in-process worker that imports one image through the real gateway."""
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
            if request.stage == 'acquire':
                args = request.gateway['args']
                out = controller.call('tool', {'run_id': args[args.index('--run') + 1],
                    'assignment_id': args[args.index('--assignment') + 1],
                    'name': 'acquire_firmware_artifact', 'arguments': {'origin': 'provided_binary'}})
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
        self.assertEqual(result['progress']['next_stage'], 'interpret')
        self.assertEqual(result['stage'], 'interpret')

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
        self.assertEqual(result['progress']['next_stage'], 'interpret')

    def test_honest_not_run_check_advances(self):
        # run-0bddf9e2448f46c7 stopped here after acquire had produced its image.
        report = {'status': 'completed', 'summary': 'Imported; boot log not captured', 'artifacts': [],
                  'checks': [{'name': 'boot_log', 'status': 'not_run', 'evidence': []}], 'unresolved': []}
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._scripted_generic(Path(directory), report)
        acquire = result['accepted_handoffs'][0]
        self.assertEqual(len(acquire['artifacts']), 1)
        self.assertEqual(acquire['notes']['inline']['worker']['checks'][0]['status'], 'not_run')
        self.assertEqual(result['progress']['next_stage'], 'interpret')

    def test_runtime_without_a_report_advances_with_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _ = self._scripted_generic(Path(directory), {'runtime_status': 'failed', 'cause': 'no_report',
                'reason': 'Runtime did not produce a structured stage report'})
        acquire = result['accepted_handoffs'][0]
        notes = acquire['notes']['inline']
        self.assertEqual(notes['worker']['runtime_status'], 'failed')
        self.assertTrue(any('no usable report' in q for q in notes['open_questions']), notes)
        self.assertEqual(result['progress']['next_stage'], 'interpret')

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
                self.assertIn('blocked', prepared)
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
        self.assertIn('blocked', prepared)
        self.assertIn('one acquired binary', prepared['blocked'])

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
