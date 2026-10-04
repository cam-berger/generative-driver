"""Public configurator behavior with a deliberately unavailable external runtime."""
from pathlib import Path
import tempfile
import time
import unittest
import sys


class ConfiguratorTests(unittest.TestCase):
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

    def test_worker_claims_and_hashed_text_cannot_substitute_for_stage_evidence(self):
        from generative_driver.configurator import Controller, STAGES
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
                until = time.monotonic() + 10
                while time.monotonic() < until:
                    result = controller.call('result', run)
                    if result['status'] in ('completed','failed','blocked'):
                        break
                    time.sleep(.02)
                self.assertEqual(result['status'], 'blocked', result)
                self.assertIn('observed acquisition',result['reason'])
                self.assertEqual([r['stage'] for r in result['worker_reports']], ['acquire'])
                self.assertEqual(result['accepted_handoffs'], [])
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


if __name__ == '__main__':
    unittest.main()
