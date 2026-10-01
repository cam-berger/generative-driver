"""Scripted service contracts, never model baselines or native qualification."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

from suite_fixtures import legacy_manifest, wait_owner_suite


class DecisionTests(unittest.TestCase):
    def test_only_a_recorded_settled_model_failure_advances(self):
        from generative_driver.benchmark_support.suites import child_disposition
        stopped = {'status':'blocked','stopping':False,'uncertain_effect':False,
                   'outcome_category':'model','reason':'Arbitrary localized message'}
        self.assertEqual(child_disposition(stopped), 'advance')
        for category in ('host','operator','unknown','cancelled'):
            self.assertEqual(child_disposition(dict(stopped,outcome_category=category)), 'block')
        self.assertEqual(child_disposition(dict(stopped,uncertain_effect=True)), 'block')
        self.assertEqual(child_disposition(dict(stopped,stopping=True)), 'wait')
        self.assertEqual(child_disposition(dict(stopped,status='running')), 'wait')
        self.assertEqual(child_disposition(dict(stopped,status='completed',outcome_category='completed')), 'advance')
        for key in ('stopping','uncertain_effect'):
            incomplete = dict(stopped); incomplete.pop(key)
            self.assertEqual(child_disposition(incomplete), 'block')


def wait_child(controller, run_id):
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        result = controller.call('result', {'run_id':run_id})
        if result['status'] in ('blocked','failed','cancelled','completed') and not result['stopping']:
            return result
        time.sleep(.02)
    raise AssertionError(result)


class CategoryTests(unittest.TestCase):
    def test_runtime_launch_failure_is_host_and_cancellation_overrides_it(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as home:
            controller = Controller(home)
            try:
                run = controller.start({'goal':'Scripted missing runtime','executor_config':{'command':['missing-suite-worker']}})
                result = wait_child(controller, run['run_id'])
                self.assertEqual(result.get('outcome_category'), 'host')
                event = controller.call('events',run)['events'][-1]
                self.assertEqual(event['data']['outcome_category'], 'host')
                cancelled = controller.call('cancel',run)
                self.assertEqual(cancelled['outcome_category'], 'cancelled')
            finally:
                controller.close()

    def test_scripted_qualification_exhaustion_is_a_trusted_model_failure(self):
        from generative_driver.client import call
        from suite_fixtures import write_scripted_case_worker
        with tempfile.TemporaryDirectory() as home:
            try:
                run=call('start',{'goal':'Scripted qualification contract','case':'tq9',
                    'executor_config':{'command':write_scripted_case_worker(home)}},home)
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    result=call('result',run,home)
                    if result['status'] in ('blocked','failed') and not result['stopping']:break
                    time.sleep(.02)
                self.assertEqual(result['progress']['repairs'],2,result)
                self.assertEqual(result.get('outcome_category'),'model')
            finally:
                call('shutdown',{},home)


class ServiceTests(unittest.TestCase):
    def test_model_failure_finishes_a_measurement_and_starts_the_next_slot(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        from suite_fixtures import write_scripted_case_worker, wait_suite
        with tempfile.TemporaryDirectory(prefix='suite scripted ') as home:
            configure('codex',write_scripted_case_worker(home),home=home)
            try:
                suite=call('suite_start',{'manifest':legacy_manifest(2),'executor':'codex',
                    'options':{},'request_id':'two-scripted-children'},home)
                state=wait_suite(call,suite['suite_id'],home,lambda s:s['status']=='completed')
                page=call('suite_result',{'suite_id':suite['suite_id'],'limit':100},home)
                self.assertEqual(state['completed'],2)
                self.assertEqual(len({t['run_id'] for t in page['trials']}),2)
                self.assertEqual([t['outcome_category'] for t in page['trials']],['model','model'])
                events=call('suite_events',suite,home)['events']
                kinds=[e['kind'] for e in events]
                self.assertLess(kinds.index('trial.finished'),[i for i,k in enumerate(kinds) if k=='trial.started'][1])
            finally:
                call('shutdown',{},home)

    def test_host_blockage_preserves_the_child_and_frozen_configuration(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        from suite_fixtures import wait_suite
        with tempfile.TemporaryDirectory() as home:
            configure('codex',['missing-first-runtime'],model='first-model',home=home)
            request={'manifest':legacy_manifest(2),'executor':'codex','options':{},'request_id':'freeze'}
            try:
                suite=call('suite_start',request,home)
                blocked=wait_suite(call,suite['suite_id'],home,lambda s:s['status']=='blocked' and not s['stopping'])
                child=blocked['active_child_id']
                configure('codex',['missing-second-runtime'],model='second-model',home=home)
                self.assertEqual(call('suite_start',request,home)['suite_id'],suite['suite_id'])
                resumed=call('suite_resume',suite,home)
                self.assertTrue(resumed['ok'],resumed)
                again=wait_suite(call,suite['suite_id'],home,lambda s:s['status']=='blocked' and not s['stopping'])
                self.assertEqual(again['active_child_id'],child)
                result=call('result',{'run_id':child},home)
                self.assertEqual(result['agent']['model'],'first-model')
                self.assertEqual(result['outcome_category'],'host')
                rows=call('suite_result',suite,home)['trials']
                self.assertEqual(sum(t['run_id'] is not None for t in rows),1)
            finally:call('shutdown',{},home)

    def test_cancellation_drains_active_child_without_dispatching_another(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        from suite_fixtures import wait_suite
        with tempfile.TemporaryDirectory() as home:
            configure('codex',[sys.executable,'-c','import time; time.sleep(120)'],home=home)
            try:
                suite=call('suite_start',{'manifest':legacy_manifest(2),'executor':'codex','options':{}},home)
                active=wait_suite(call,suite['suite_id'],home,lambda s:bool(s['active_child_id']))
                stopped=call('suite_cancel',suite,home)
                self.assertTrue(stopped['ok'],stopped)
                self.assertEqual(stopped['status'],'cancelled')
                self.assertFalse(stopped['stopping'])
                self.assertFalse(call('status',{'run_id':active['active_child_id']},home)['stopping'])
                rows=call('suite_result',suite,home)['trials']
                self.assertEqual(sum(t['run_id'] is not None for t in rows),1)
                self.assertEqual(rows[0]['status'],'cancelled')
                self.assertEqual(rows[0]['outcome_category'],'cancelled')
            finally:call('shutdown',{},home)


class SummaryTests(unittest.TestCase):
    def test_owner_v2_summary_projects_only_gate_identity_counts_and_maintenance(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as home:
            owner=Controller(home)
            try:
                run=owner.start({'goal':'Toy summary records','case':'tq9','executor_config':{'command':['missing-runtime']}})
                result=wait_child(owner,run['run_id'])
                self.assertNotIn('benchmark_summary',result)
                # Public toy producer fixture: no private source, emulator, or scoring.
                with owner._db() as db:
                    spec=json.loads(db.execute('SELECT spec FROM runs WHERE id=?',(run['run_id'],)).fetchone()['spec'])
                    spec['_case_pin']['manifest']['schema']='benchmark-case/2'
                    db.execute('UPDATE runs SET spec=? WHERE id=?',(json.dumps(spec),run['run_id']))
                safe={'accepted_gates':[{'stage':'reuse','revision':0,'assignment_id':'a','artifact_sha256':'a'*64,'verdict':'passed','passed':2,'total':2}],
                      'evaluations':[{'phase':'final','revision':0,'frozen_artifact_sha256':'a'*64,'verdict':'passed','passed':3,'total':3}],
                      'final_evaluation':{'verdict':'passed','passed':3,'total':3,'evidence_sha256':'e'*64},
                      'maintenance':{'drift_claimed':False,'drift_observed':False,'false_alarm':False,'repair_completed':False,'requalified':False,'fresh_reuse_passed':True}}
                state=json.loads(json.dumps(safe));state['alien']='/PRIVATE/raw'
                state['accepted_gates'][0]['raw']='/PRIVATE/checks'
                state['evaluations'][0]['records']=['PRIVATE']
                state['maintenance']['diagnostic']={'hidden':'PRIVATE'}
                state['final_evaluation']['vectors']=['PRIVATE']
                path=Path(home)/'runs'/run['run_id']/'benchmark/state.json'
                path.write_text(json.dumps(state))
                result=owner.call('result',run)
                self.assertEqual(result.get('benchmark_summary'),safe)
                self.assertNotIn('PRIVATE',json.dumps(result['benchmark_summary']))
                path.unlink()
                self.assertEqual(owner.call('result',run)['benchmark_summary'],{'accepted_gates':[],'evaluations':[],'final_evaluation':{},'maintenance':{}})
            finally:owner.close()




class BudgetTests(unittest.TestCase):
    def test_expired_suite_cancels_child_and_extension_is_absolute_and_idempotent(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        from suite_fixtures import wait_suite
        with tempfile.TemporaryDirectory() as home:
            configure('codex',[sys.executable,'-c','import time; time.sleep(120)'],home=home)
            manifest=legacy_manifest(2);manifest.update(child_budget_seconds=.3,suite_budget_seconds=.3)
            try:
                suite=call('suite_start',{'manifest':manifest},home)
                blocked=wait_suite(call,suite['suite_id'],home,lambda s:s['status']=='blocked')
                self.assertEqual(blocked['completed'],0)
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    child=call('status',{'run_id':blocked['active_child_id']},home)
                    state=call('suite_status',suite,home)
                    if not child['stopping'] and not state['stopping']:break
                    time.sleep(.02)
                self.assertEqual(child['status'],'cancelled')
                no_extension=call('suite_resume',suite,home)
                self.assertFalse(no_extension['ok'])
                resumed=call('suite_resume',{**suite,'suite_budget_seconds':120,'budget_reason':'PRIVATE-REASON'},home)
                # Child's exhausted budget needs its separate explicit extension.
                self.assertFalse(resumed['ok'])
                call('resume',{'run_id':child['run_id'],'budget_seconds':120,'budget_reason':'PRIVATE-CHILD'},home)
                resumed=call('suite_resume',{**suite,'suite_budget_seconds':120,'budget_reason':'PRIVATE-REASON'},home)
                self.assertTrue(resumed['ok'],resumed)
                cancelled=call('suite_cancel',suite,home)
                self.assertEqual(cancelled['created'],blocked['created'])
                repeated=call('suite_resume',{**suite,'suite_budget_seconds':120,'budget_reason':'repeat'},home)
                self.assertTrue(repeated['ok'],repeated)
                page=call('suite_result',suite,home)
                budgets=page['experiment']['comparison_identity']['budgets']
                self.assertEqual(budgets['original']['suite_budget_seconds'],.3)
                self.assertEqual(budgets['effective']['suite_budget_seconds'],120)
                self.assertEqual(budgets['effective']['child_budget_overrides'],[{'trial_key':'entry-000-repeat-000','budget_seconds':120}])
                events=call('suite_events',suite,home)['events']
                self.assertEqual(sum(e['kind']=='suite.budget_extended' for e in events),1)
                self.assertNotIn('PRIVATE',json.dumps(page))
            finally:call('shutdown',{},home)

    def test_invalid_suite_budget_extension_never_resumes_or_changes_creation(self):
        from generative_driver.configurator import Controller
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory() as home:
            configure('codex',['missing-runtime'],home=home)
            owner=Controller(home)
            try:
                suite=owner.call('suite_start',{'manifest':legacy_manifest()})
                before=wait_owner_suite(owner,suite['suite_id'],lambda s:s['status']=='blocked' and not s['stopping'])
                for fields in ({'suite_budget_seconds':True,'budget_reason':'x'},
                               {'suite_budget_seconds':float('inf'),'budget_reason':'x'},
                               {'suite_budget_seconds':0,'budget_reason':'x'},
                               {'suite_budget_seconds':119,'budget_reason':'x'},
                               {'suite_budget_seconds':604801,'budget_reason':'x'},
                               {'suite_budget_seconds':180,'budget_reason':' '}):
                    with self.subTest(fields=fields),self.assertRaises(ValueError):
                        owner.call('suite_resume',{**suite,**fields})
                after=owner.call('suite_status',suite)
                self.assertEqual(after['created'],before['created'])
                self.assertEqual(after['status'],'blocked')
            finally:owner.close()


class RecoveryTests(unittest.TestCase):
    def test_resume_verifies_snapshots_of_finished_children_before_any_new_assignment(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        from suite_fixtures import write_scripted_case_worker, wait_suite
        with tempfile.TemporaryDirectory() as home:
            command=write_scripted_case_worker(home);script=Path(command[1])
            source=script.read_text().replace("    task=json.loads", "    counter=pathlib.Path("+repr(str(Path(home)/'counter'))+")\n    n=int(counter.read_text())+1 if counter.exists() else 1\n    counter.write_text(str(n))\n    if n>1: sys.exit(3)\n    task=json.loads")
            script.write_text(source);configure('codex',command,home=home)
            try:
                suite=call('suite_start',{'manifest':legacy_manifest(3)},home)
                blocked=wait_suite(call,suite['suite_id'],home,lambda s:s['status']=='blocked' and not s['stopping'])
                self.assertEqual(blocked['completed'],1)
                trials=call('suite_result',suite,home)['trials']
                image=Path(home)/'runs'/trials[0]['run_id']/'benchmark/inputs/firmware.bin'
                image.write_bytes(b'changed temporary snapshot')
                before=call('events',{'run_id':blocked['active_child_id']},home)['cursor']
                refused=call('suite_resume',suite,home)
                self.assertFalse(refused['ok'],refused)
                self.assertIn('Snapshot',refused['reason'])
                self.assertEqual(call('events',{'run_id':blocked['active_child_id']},home)['cursor'],before)
            finally:call('shutdown',{},home)

    def test_recovered_terminal_final_reconciles_same_child_and_settles_without_resume(self):
        from generative_driver.configurator import Controller
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory() as home:
            configure('codex',['missing-runtime'],home=home)
            owner=Controller(home)
            suite=owner.call('suite_start',{'manifest':legacy_manifest(2)})
            blocked=wait_owner_suite(owner,suite['suite_id'],lambda s:s['status']=='blocked' and not s['stopping'])
            child=blocked['active_child_id'];owner.close()
            # Durable public toy final-verdict fixture and a lost attachment, not evaluation.
            with owner._db() as db:
                verdict={'stage':'reuse','assignment_id':'fixture-final','final_evaluation':True,'fault':'model','verdict':'failed','passed':0,'total':1}
                db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)',('fixture-final',child,'reuse',json.dumps(verdict),time.time()))
                db.execute("UPDATE runs SET status='failed',outcome_category='unknown' WHERE id=?",(child,))
                db.execute("UPDATE suite_trials SET run_id=NULL,status='launching' WHERE suite_id=? AND ordinal=0",(suite['suite_id'],))
                db.execute("UPDATE suites SET status='running' WHERE id=?",(suite['suite_id'],))
            owner=Controller(home)
            try:
                recovered=owner.call('suite_status',suite)
                self.assertEqual(recovered['status'],'blocked')
                self.assertIsNone(recovered['active_child_id'])
                self.assertEqual(owner.find_request('suite:'+suite['suite_id']+':trial:0')['run_id'],child)
                resumed=owner.call('suite_resume',suite)
                self.assertTrue(resumed['ok'])
                end=wait_owner_suite(owner,suite['suite_id'],lambda s:s['status']=='blocked' and not s['stopping'])
                self.assertEqual(end['completed'],1)
                rows=owner.call('suite_result',suite)['trials']
                self.assertEqual(rows[0]['run_id'],child)
                self.assertEqual(rows[0]['outcome_category'],'model')
                self.assertNotEqual(rows[1]['run_id'],child)
                self.assertFalse(any(e['kind']=='run.resumed' for e in owner.call('events',{'run_id':child})['events']))
                kinds=[e['kind'] for e in owner.call('suite_events',suite)['events']]
                self.assertIn('terminal_child_settled',kinds)
            finally:owner.close()

    def test_real_owner_process_loss_recovers_without_dispatch_and_requires_reconciliation(self):
        import os
        import signal
        from generative_driver.client import call, _wait_for_exit
        from generative_driver.setup import configure
        from suite_fixtures import wait_suite
        with tempfile.TemporaryDirectory() as home:
            marker=Path(home)/'worker.pid'
            code='import os,pathlib,time; p=pathlib.Path('+repr(str(marker))+'); p.write_text(str(os.getpid())); time.sleep(120)'
            configure('codex',[sys.executable,'-c',code],home=home)
            pid=None
            try:
                suite=call('suite_start',{'manifest':legacy_manifest(2)},home)
                active=wait_suite(call,suite['suite_id'],home,lambda s:bool(s['active_child_id']))
                deadline=time.monotonic()+5
                while not marker.exists() and time.monotonic()<deadline:time.sleep(.02)
                pid=int(marker.read_text())
                old=call('ping',{},home)['pid']
                os.kill(old,signal.SIGTERM)
                self.assertTrue(_wait_for_exit(old,5))
                os.kill(pid,signal.SIGTERM);pid=None
                new=call('ping',{},home)['pid'];self.assertNotEqual(new,old)
                state=call('suite_status',suite,home)
                self.assertEqual(state['status'],'blocked')
                self.assertEqual(state['active_child_id'],active['active_child_id'])
                events=call('suite_events',suite,home)['events']
                self.assertEqual(sum(e['kind']=='trial.started' for e in events),1)
                self.assertEqual(sum(e['kind']=='suite.recovered' for e in events),1)
                result=call('result',{'run_id':active['active_child_id']},home)
                self.assertTrue(result['uncertain_effect'])
                refused=call('suite_resume',suite,home)
                self.assertFalse(refused['ok']);self.assertIn('uncertain',refused['reason'])
                self.assertEqual(len([r for r in call('suite_result',suite,home)['trials'] if r['run_id']]),1)
            finally:
                if pid:
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                call('shutdown',{},home)


class InterventionTests(unittest.TestCase):
    def test_public_trials_retain_control_event_identity_without_operator_content(self):
        from generative_driver.configurator import Controller
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory() as home:
            configure('codex',['missing-runtime'],home=home)
            owner=Controller(home)
            try:
                suite=owner.call('suite_start',{'manifest':legacy_manifest()})
                state=wait_owner_suite(owner,suite['suite_id'],lambda s:s['status']=='blocked' and not s['stopping'])
                run={'run_id':state['active_child_id']}
                owner.call('respond',{**run,'message':'PRIVATE-MESSAGE','observation':{'alien':'/PRIVATE/PATH'}})
                event=owner.call('events',run)['events'][-1]
                page=owner.call('suite_result',suite)
                row=page['trials'][0]
                self.assertEqual(row.get('interventions'),[{'event_id':event['id'],'kind':'operator.response','time':event['time'],'effect_resolution':None,'has_observation':True}])
                self.assertNotIn('PRIVATE',json.dumps(page))
            finally:owner.close()


class DelayedOperationTests(unittest.TestCase):
    def test_suite_cancel_preserves_delayed_local_tcp_uncertainty_and_never_advances(self):
        import socketserver
        import threading
        from generative_driver.configurator import Controller
        from generative_driver.setup import configure
        from generative_driver.toolkit import resources_root
        entered,release=threading.Event(),threading.Event()
        received=[]
        class Device(socketserver.StreamRequestHandler):
            def handle(self):
                while command:=self.rfile.readline():
                    received.append(command)
                    if command==b'ID?\n':
                        self.wfile.write(b'DEMO-42\n');self.wfile.flush()
                    else:
                        entered.set();release.wait(5)
                        self.wfile.write(b'T:21.5\n');self.wfile.flush()
        server=socketserver.ThreadingTCPServer(('127.0.0.1',0),Device)
        server.daemon_threads=True
        server_thread=threading.Thread(target=server.serve_forever,daemon=True);server_thread.start()
        try:
            with tempfile.TemporaryDirectory() as home:
                configure('codex',[sys.executable,'-c','import time; time.sleep(120)'],home=home)
                owner=Controller(home);worker=None
                try:
                    suite=owner.call('suite_start',{'manifest':legacy_manifest(2)})
                    state=wait_owner_suite(owner,suite['suite_id'],lambda s:bool(s['active_child_id']))
                    run={'run_id':state['active_child_id']}
                    deadline=time.monotonic()+5;assignments=[]
                    while not assignments and time.monotonic()<deadline:
                        assignments=[e['data'] for e in owner.call('events',run)['events'] if e['kind']=='stage.assigned']
                        if not assignments:time.sleep(.01)
                    assignment=assignments[0]
                    # Public toy assignment fixture reaches the real managed TCP path
                    # without passing the scored legacy interpretation gate or any emulator.
                    model=json.loads((resources_root()/'bench/cases/setup-smoke/model.json').read_text())
                    model['channel']={'type':'tcp'};model['operations']['measure']['effect']='write'
                    model['operations']['measure']['steps'][0]['timeout_ms']=4000
                    model_dir=Path(assignment['workspace'])/'toy';model_dir.mkdir()
                    (model_dir/'model.json').write_text(json.dumps(model))
                    assignment.update(allowed_tools=['interface_execute'],binding={'host':'127.0.0.1','port':server.server_address[1]},effects=['write'])
                    with owner._db() as db:
                        db.execute('UPDATE assignments SET payload=? WHERE id=?',(json.dumps(assignment),assignment['id']))
                    responses=[]
                    def invoke():
                        responses.append(owner.call('tool',{**run,'assignment_id':assignment['id'],'name':'interface_execute','arguments':{'model_dir':str(model_dir),'operation':'measure'}}))
                    worker=threading.Thread(target=invoke);worker.start()
                    self.assertTrue(entered.wait(5))
                    self.assertTrue(owner.call('status',run)['uncertain_effect'])
                    cancelled=owner.call('suite_cancel',suite)
                    self.assertEqual(cancelled['status'],'cancelled');self.assertTrue(cancelled['stopping'])
                    with self.assertRaisesRegex(ValueError,'stopping'):owner.call('suite_resume',suite)
                    release.set();worker.join(5);self.assertFalse(worker.is_alive())
                    child=wait_child(owner,run['run_id'])
                    self.assertTrue(child['uncertain_effect']);self.assertEqual(child['outcome_category'],'cancelled')
                    with self.assertRaisesRegex(ValueError,'uncertain'):owner.call('suite_resume',suite)
                    self.assertEqual(sum(t['run_id'] is not None for t in owner.call('suite_result',suite)['trials']),1)
                    self.assertEqual(received.count(b'T\n'),1)
                    self.assertEqual(len(responses),1)
                finally:
                    release.set()
                    if worker:worker.join(5)
                    owner.close()
        finally:
            release.set();server.shutdown();server.server_close();server_thread.join(5)


class CleanupFailureTests(unittest.TestCase):
    def _stopped_suite_at_completion(self, home):
        """Public durable queue fixture; exercises cleanup, not seven-stage grading."""
        from generative_driver.configurator import Controller
        from generative_driver.setup import configure
        configure('codex',['missing-runtime'],home=home)
        owner=Controller(home)
        suite=owner.call('suite_start',{'manifest':legacy_manifest(2)})
        state=wait_owner_suite(owner,suite['suite_id'],lambda s:s['status']=='blocked' and not s['stopping'])
        child=state['active_child_id']
        # Resume a durable exhausted stage queue so the real _run completion and
        # finally paths execute, without pretending this is scored success.
        with owner._db() as db:
            progress={'revision':0,'next_stage':None,'repairs':0,'maintenance_cycles':0,'feedback':None}
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(child,json.dumps(progress)))
            owner._claim_binding({'host':'127.0.0.1','port':19381},child,db)
        return owner,suite,child

    def _assert_retained_lease(self, owner, child):
        with owner._db() as db:
            lease=db.execute('SELECT run_id FROM bindings WHERE run_id=?',(child,)).fetchone()
        self.assertIsNotNone(lease)
        with self.assertRaisesRegex(ValueError,'already owned'):
            owner.start({'goal':'Competing local lease fixture','binding':{'host':'127.0.0.1','port':19381},
                         'executor_config':{'command':['missing-runtime']}})

    def test_cleanup_failure_blocks_settlement_and_terminal_recovery_until_reconciled(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as home:
            owner,suite,child=self._stopped_suite_at_completion(home)
            try:
                # Only the external stop boundary fails; scheduling/state/leases are real.
                with patch('generative_driver.benchmark.cleanup',side_effect=TimeoutError('scripted stop timeout')):
                    owner.call('suite_resume',suite)
                    state=wait_owner_suite(owner,suite['suite_id'],lambda s:s['status']=='blocked' and not s['stopping'])
                self.assertEqual(state['completed'],0)
                result=owner.call('result',{'run_id':child})
                self.assertEqual((result['status'],result['outcome_category']),('blocked','host'))
                self.assertTrue(result['uncertain_effect'])
                self._assert_retained_lease(owner,child)
                rows=owner.call('suite_result',suite)['trials']
                self.assertEqual([t['run_id'] for t in rows],[child,None])
                events=owner.call('events',{'run_id':child})['events']
                self.assertIn('cleanup.failed',[e['kind'] for e in events])
                # A durable terminal-final verdict cannot override unresolved cleanup.
                with owner._db() as db:
                    verdict={'stage':'reuse','assignment_id':'cleanup-final','final_evaluation':True,
                             'fault':'model','verdict':'failed','passed':0,'total':1}
                    db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)',('cleanup-final',child,'reuse',json.dumps(verdict),time.time()))
                owner.close();owner=Controller(home)
                with self.assertRaises(ValueError):owner.call('suite_resume',suite)
                still=owner.call('suite_result',suite)
                self.assertEqual(still['completed'],0)
                self.assertEqual([t['run_id'] for t in still['trials']],[child,None])
                self.assertNotIn('terminal_child_settled',[e['kind'] for e in owner.call('suite_events',suite)['events']])
                self.assertTrue(owner.call('status',{'run_id':child})['uncertain_effect'])
                self._assert_retained_lease(owner,child)
            finally:owner.close()

    def test_cleanup_failure_preserves_cancellation_and_unresolved_ownership(self):
        import threading
        from unittest.mock import patch
        entered,release=threading.Event(),threading.Event()
        def failed_stop(*args,**kwargs):
            entered.set()
            if not release.wait(5):raise AssertionError('Fixture cancellation never released cleanup')
            raise TimeoutError('scripted stop timeout')
        with tempfile.TemporaryDirectory() as home:
            owner,suite,child=self._stopped_suite_at_completion(home)
            cancellation=None
            try:
                with patch('generative_driver.benchmark.cleanup',side_effect=failed_stop):
                    owner.call('suite_resume',suite)
                    self.assertTrue(entered.wait(5))
                    replies=[]
                    cancellation=threading.Thread(target=lambda:replies.append(owner.call('suite_cancel',suite)))
                    cancellation.start()
                    deadline=time.monotonic()+5
                    while time.monotonic()<deadline:
                        state=owner.call('status',{'run_id':child})
                        if state['status']=='cancelled':break
                        time.sleep(.01)
                    self.assertEqual(state['status'],'cancelled')
                    release.set();cancellation.join(5)
                    self.assertFalse(cancellation.is_alive())
                self.assertEqual(replies[0]['status'],'cancelled')
                result=wait_child(owner,child)
                self.assertEqual((result['status'],result['outcome_category']),('cancelled','cancelled'))
                self.assertTrue(result['uncertain_effect'])
                self._assert_retained_lease(owner,child)
                self.assertEqual(owner.call('suite_status',suite)['completed'],0)
                with self.assertRaisesRegex(ValueError,'uncertain'):owner.call('suite_resume',suite)
            finally:
                release.set()
                if cancellation:cancellation.join(5)
                owner.close()
