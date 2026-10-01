"""Durable suite storage contracts; no inference, native tools or truth access."""
import tempfile
import unittest
from pathlib import Path
from generative_driver.configurator import Controller
from suite_fixtures import legacy_manifest,sqlite_context


class StoreTests(unittest.TestCase):
    def test_created_child_is_reused_after_lost_attachment(self):
        from generative_driver.benchmark_support.suites import freeze_suite,child_request
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);controller=Controller(home)
            try:
                frozen=freeze_suite(legacy_manifest(),'codex',
                    {'command':['missing-scripted-runtime'],'model':'frozen-model'},{},{})
                store=SuiteStore(sqlite_context(home/'runs.sqlite3'))
                suite=store.create(frozen,'one-request')
                store.set_state(suite['suite_id'],'running')
                reserved=store.reserve(suite['suite_id'])
                started=controller.call('start',child_request(frozen,reserved))
                reopened=SuiteStore(sqlite_context(home/'runs.sqlite3'))
                same=reopened.reserve(suite['suite_id'])
                self.assertEqual(same['child_request_id'],reserved['child_request_id'])
                repeated=controller.call('start',child_request(frozen,same))
                self.assertEqual(repeated['run_id'],started['run_id'])
                self.assertTrue(repeated['duplicate'])
                reopened.attach(suite['suite_id'],same['ordinal'],same['child_request_id'],repeated['run_id'])
                self.assertEqual(reopened.trials(suite['suite_id'])['trials'][0]['run_id'],started['run_id'])
            finally:controller.close()

    def test_duplicate_request_returns_original_frozen_inputs_and_conflicts_refuse(self):
        from copy import deepcopy
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            store=SuiteStore(sqlite_context(Path(temporary)/'runs.sqlite3'))
            frozen=freeze_suite(legacy_manifest(2),'codex',{'command':['scripted'],'model':'original'},{},{})
            first=store.create(frozen,'request')
            current=deepcopy(frozen);current['executor_config']['model']='changed'
            repeated=store.create(current,'request')
            self.assertEqual(repeated['suite_id'],first['suite_id'])
            self.assertEqual(repeated['frozen']['executor_config']['model'],'original')
            self.assertEqual(store.find_request('request',frozen['request'])['suite_id'],first['suite_id'])
            changed=deepcopy(frozen);changed['request']['manifest']['repetitions']=3
            with self.assertRaises(ValueError):store.create(changed,'request')
            with self.assertRaises(ValueError):store.find_request('request',changed['request'])
            self.assertIsNone(store.find_request('absent',frozen['request']))

    def test_reservation_is_shared_and_blocking_preserves_active_slot(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            db=sqlite_context(Path(temporary)/'runs.sqlite3')
            first,second=SuiteStore(db),SuiteStore(db)
            suite=first.create(freeze_suite(legacy_manifest(2),'codex',{'command':['scripted']},{},{}))
            sid=suite['suite_id']
            self.assertIsNone(first.reserve(sid))
            first.set_state(sid,'running');barrier=Barrier(2)
            def reserve(store):barrier.wait();return store.reserve(sid)
            with ThreadPoolExecutor(max_workers=2) as pool:
                a=pool.submit(reserve,first);b=pool.submit(reserve,second)
                one,two=a.result(),b.result()
            self.assertEqual(one['child_request_id'],two['child_request_id'])
            self.assertEqual(one['child_request_id'],f"suite:{sid}:trial:0")
            first.attach(sid,0,one['child_request_id'],'run-one')
            second.attach(sid,0,one['child_request_id'],'run-one')
            with self.assertRaises(ValueError):second.attach(sid,0,one['child_request_id'],'run-other')
            with self.assertRaises(ValueError):second.attach(sid,0,'wrong-request','run-one')
            first.set_state(sid,'blocked','private host detail')
            self.assertEqual(second.reserve(sid)['run_id'],'run-one')
            first.set_state(sid,'cancelled')
            self.assertEqual(second.reserve(sid)['run_id'],'run-one')
            self.assertEqual(first.trials(sid)['trials'][1]['status'],'pending')

    def test_public_pages_validate_cursors_and_exclude_private_storage(self):
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            store=SuiteStore(sqlite_context(Path(temporary)/'runs.sqlite3'))
            frozen=freeze_suite(legacy_manifest(3),'codex',{'command':['PRIVATE-COMMAND']},{},{})
            sid=store.create(frozen)['suite_id']
            for key,values in {'offset':[-1,True,1.5,'0'],'limit':[0,101,True,1.5,'2']}.items():
                for value in values:
                    with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                        store.trials(sid,**{key:value})
            page=store.trials(sid,limit=2)
            self.assertEqual([t['ordinal'] for t in page['trials']],[0,1])
            self.assertEqual(page['next_offset'],2)
            last=store.trials(sid,offset=page['next_offset'],limit=2)
            self.assertEqual([t['ordinal'] for t in last['trials']],[2])
            self.assertIsNone(last['next_offset'])
            self.assertEqual(set(page['trials'][0]),{'ordinal','trial_key','case','scenario','case_seed','repeat_index','run_id','status','outcome_category'})
            for value in [-1,True,1.5,'0']:
                with self.subTest(after=value),self.assertRaises(ValueError):store.events(sid,after=value)
            for index in range(501):store.set_state(sid,'blocked','PRIVATE-REASON')
            events=store.events(sid)
            self.assertEqual(len(events['events']),500)
            self.assertEqual(events['events'][0]['kind'],'suite.started')
            rest=store.events(sid,after=events['cursor'])
            self.assertEqual(len(rest['events']),2)
            self.assertGreater(rest['events'][0]['id'],events['cursor'])
            self.assertEqual(store.events(sid,after=rest['cursor'])['cursor'],rest['cursor'])
            self.assertNotIn('PRIVATE',str(events)+str(rest)+str(page))

    def test_settlement_requires_matching_settled_child_and_is_idempotent(self):
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            store=SuiteStore(sqlite_context(Path(temporary)/'runs.sqlite3'))
            sid=store.create(freeze_suite(legacy_manifest(2),'codex',{'command':['scripted']},{},{}))['suite_id']
            store.set_state(sid,'running');slot=store.reserve(sid)
            store.attach(sid,0,slot['child_request_id'],'run-one')
            result={'run_id':'run-one','status':'failed','outcome_category':'model','stopping':False,'uncertain_effect':False}
            for change in [{'run_id':'other'},{'stopping':True},{'uncertain_effect':True},{'stopping':None},{'uncertain_effect':None},{'status':'running'}]:
                with self.subTest(change=change),self.assertRaises(ValueError):store.settle(sid,0,dict(result,**change))
            store.settle(sid,0,dict(result,reason='PRIVATE-REASON',executor_config={'command':['PRIVATE']}))
            cursor=store.events(sid)['cursor']
            store.settle(sid,0,result)
            self.assertEqual(store.events(sid)['cursor'],cursor)
            with self.assertRaises(ValueError):store.settle(sid,0,dict(result,status='completed',outcome_category='completed'))
            saved=store.get(sid)['trials'][0]
            self.assertEqual(saved['status'],'finished')
            self.assertEqual(saved['outcome_category'],'model')
            self.assertEqual(saved['result'],{'run_id':'run-one','status':'failed','outcome_category':'model'})
            self.assertEqual(store.reserve(sid)['ordinal'],1)

    def test_recovery_blocks_unfinished_suites_without_retry_or_repeated_events(self):
        from generative_driver.benchmark_support.suites import freeze_suite
        from generative_driver.benchmark_support.suite_store import SuiteStore
        with tempfile.TemporaryDirectory() as temporary:
            db=sqlite_context(Path(temporary)/'runs.sqlite3');store=SuiteStore(db)
            frozen=freeze_suite(legacy_manifest(),'codex',{'command':['scripted']},{},{})
            queued=store.create(frozen)['suite_id'];running=store.create(frozen)['suite_id']
            done=store.create(frozen)['suite_id'];store.set_state(done,'completed')
            store.set_state(running,'running');reserved=store.reserve(running)
            reopened=SuiteStore(db)
            self.assertEqual(reopened.get(queued)['status'],'queued')
            reopened.recover()
            for sid in (queued,running):
                self.assertEqual(reopened.get(sid)['status'],'blocked')
                events=reopened.events(sid);self.assertEqual(events['events'][-1]['kind'],'suite.recovered')
                reopened.recover();self.assertEqual(reopened.events(sid)['cursor'],events['cursor'])
            self.assertIsNone(reopened.reserve(queued))
            self.assertEqual(reopened.reserve(running)['child_request_id'],reserved['child_request_id'])
            self.assertIsNone(reopened.reserve(running)['run_id'])
            self.assertEqual(reopened.get(done)['status'],'completed')

class ControllerStorageTests(unittest.TestCase):
    def test_expected_pin_refuses_changed_inputs_before_snapshot_or_child(self):
        from copy import deepcopy
        from generative_driver.benchmark_support.suites import freeze_suite,child_request
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);controller=Controller(home)
            try:
                frozen=freeze_suite(legacy_manifest(),'codex',{'command':['missing-scripted-runtime']},{},{})
                request=child_request(frozen,dict(frozen['trials'][0],child_request_id='pinned'))
                changed=deepcopy(request);changed['case_pin']['manifest_sha256']='a'*64
                with self.assertRaisesRegex(ValueError,'pin'):controller.start(changed)
                self.assertFalse((home/'runs').exists())
                started=controller.start(request)
                (home/'config.json').write_text('INVALID CONFIG',encoding='utf-8')
                duplicate=controller.start(request)
                self.assertEqual(duplicate['run_id'],started['run_id'])
                self.assertTrue(duplicate['duplicate'])
            finally:controller.close()

    def test_frozen_child_config_does_not_merge_new_home_defaults(self):
        import json
        from generative_driver.benchmark_support.suites import freeze_suite,child_request
        from generative_driver.benchmark_support.snapshots import read_snapshot
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary);controller=Controller(home)
            try:
                frozen=freeze_suite(legacy_manifest(),'codex',{'command':['missing-scripted-runtime'],'model':'frozen-model'},{},{})
                (home/'config.json').write_text(json.dumps({'executors':{'codex':{'model':'new-model','reasoning_effort':'high'}}}),encoding='utf-8')
                request=child_request(frozen,dict(frozen['trials'][0],child_request_id='frozen'))
                missing=dict(request,request_id='missing-config');missing.pop('executor_config')
                started=controller.start(request)
                config=read_snapshot(home/'runs'/started['run_id'])['executed']['runtime_configuration']
                self.assertEqual(config['model'],'frozen-model')
                self.assertNotIn('reasoning_effort',config)
                with self.assertRaisesRegex(ValueError,'executor_config'):controller.start(missing)
                ordinary=dict(request,request_id='ordinary');ordinary.pop('case_pin');ordinary.pop('executor_config')
                # Keep a missing executable while retaining ordinary model/reasoning defaults.
                ordinary['executor_config']={'command':['missing-scripted-runtime']}
                normal=controller.start(ordinary)
                normal_config=read_snapshot(home/'runs'/normal['run_id'])['executed']['runtime_configuration']
                self.assertEqual(normal_config['model'],'new-model')
                self.assertEqual(normal_config['reasoning_effort'],'high')
            finally:controller.close()

    def test_suite_facade_deduplicates_before_config_and_recovers_without_launch(self):
        import json
        from generative_driver.benchmark_support.suites import child_request
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)
            config=home/'config.json'
            config.write_text(json.dumps({'executors':{'codex':{'command':['missing-scripted-runtime'],'model':'frozen-model'}}}),encoding='utf-8')
            controller=Controller(home)
            request={'manifest':legacy_manifest(2),'executor':'codex','request_id':'suite-request'}
            try:
                started=controller.call('suite_start',request);sid=started['suite_id']
                self.assertFalse(started['duplicate'])
                status=controller.call('suite_status',{'suite_id':sid})
                self.assertEqual((status['status'],status['planned'],status['completed']),('queued',2,0))
                self.assertIsNone(status['active_child_id']);self.assertFalse(status['stopping'])
                self.assertFalse((home/'runs').exists())
                config.write_text('INVALID CONFIG',encoding='utf-8')
                duplicate=controller.call('suite_start',request)
                self.assertTrue(duplicate['duplicate']);self.assertEqual(duplicate['suite_id'],sid)
                with self.assertRaises(ValueError):controller.call('suite_start',dict(request,manifest=legacy_manifest(3)))
                frozen=controller._suites.get(sid)['frozen']
                controller._suites.set_state(sid,'running');slot=controller._suites.reserve(sid)
                self.assertEqual(child_request(frozen,slot)['executor_config']['model'],'frozen-model')
                self.assertEqual(controller.call('suite_events',{'suite_id':sid})['events'][0]['kind'],'suite.started')
            finally:controller.close()
            recovered=Controller(home)
            try:
                self.assertEqual(recovered.call('suite_status',{'suite_id':sid})['status'],'blocked')
                self.assertEqual(recovered.call('suite_events',{'suite_id':sid})['events'][-1]['kind'],'suite.recovered')
                page=recovered.call('suite_result',{'suite_id':sid,'limit':1})
                self.assertEqual(page['trials'][0]['status'],'launching')
                self.assertEqual(page['next_offset'],1)
                self.assertIsNone(page['trials'][0]['run_id'])
                self.assertFalse((home/'runs').exists())
            finally:recovered.close()

    def test_experiment_is_complete_allowlisted_frozen_and_snapshot_bound(self):
        import json
        from generative_driver.benchmark_support.suites import child_request
        from generative_driver.benchmark_support.evidence import public_pin
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)
            config={'command':['missing-scripted-runtime','PRIVATE-COMMAND'],'model':'frozen-model','provider':'provider',
                'version':'version','reasoning_effort':'high','max_turns':4,'endpoint':'PRIVATE-ENDPOINT','api_key':'PRIVATE-KEY',
                'settings':{'token':'PRIVATE-NESTED'}}
            (home/'config.json').write_text(json.dumps({'executors':{'codex':config}}),encoding='utf-8')
            controller=Controller(home)
            try:
                request={'manifest':legacy_manifest(3),'options':{'evaluator_password_files':{'tq9':'PRIVATE-HANDLE'},'renode':'PRIVATE-PATH'}}
                sid=controller.call('suite_start',request)['suite_id']
                frozen=controller._suites.get(sid)['frozen']
                first=controller.call('suite_result',{'suite_id':sid,'limit':1})
                experiment=first['experiment']
                self.assertEqual(experiment['schema'],'benchmark-suite-experiment/1')
                self.assertEqual(set(experiment),{'schema','comparison_identity','dimensions','execution_snapshots'})
                identity=experiment['comparison_identity']
                self.assertEqual(identity['manifest'],legacy_manifest(3))
                self.assertEqual(identity['manifest_sha256'],frozen['manifest_sha256'])
                self.assertEqual(identity['entry_pins'],[public_pin(pin) for pin in frozen['entry_pins']])
                self.assertEqual(identity['trials'],frozen['trials'])
                self.assertEqual(identity['repetitions'],3)
                self.assertEqual(identity['execution'],'actual-agent-emulation')
                self.assertEqual(identity['evidence_track'],'firmware')
                self.assertEqual(identity['scope'],'full-workflow')
                self.assertEqual(identity['time_policy'],[frozen['entry_pins'][0]['time_policy']])
                self.assertEqual(identity['evaluator_revision'],frozen['provenance']['evaluator_revision'])
                self.assertEqual(identity['budgets'],{'original':{'child_budget_seconds':120,'suite_budget_seconds':120},'effective':{'child_budget_seconds':120,'suite_budget_seconds':120,'child_budget_overrides':[]}})
                self.assertEqual(identity['intervention_policy'],{'scoped_tool_approval':None,'max_model_repairs':2,'max_maintenance_cycles':1})
                self.assertEqual(set(experiment['dimensions']),{'runtime','model','provider','version','reasoning_effort','max_turns','skills_revision','toolchain_revision'})
                self.assertEqual(experiment['dimensions']['model'],'frozen-model')
                self.assertEqual(experiment['execution_snapshots'],[{'trial_key':slot['trial_key'],'snapshot_sha256':None} for slot in frozen['trials']])
                self.assertNotIn('PRIVATE',json.dumps(first))
                (home/'config.json').write_text('INVALID CONFIG',encoding='utf-8')
                self.assertEqual(controller.call('suite_result',{'suite_id':sid,'offset':2})['experiment'],experiment)
                controller._suites.set_state(sid,'running');slot=controller._suites.reserve(sid)
                started=controller.start(child_request(frozen,slot))
                controller._suites.attach(sid,0,slot['child_request_id'],started['run_id'])
                after=controller.call('suite_result',{'suite_id':sid,'offset':2})['experiment']
                child=controller.call('result',{'run_id':started['run_id']})
                self.assertEqual(after['comparison_identity'],identity)
                self.assertEqual(after['execution_snapshots'][0]['snapshot_sha256'],child['snapshot_sha256'])
                self.assertIsNone(after['execution_snapshots'][1]['snapshot_sha256'])
                self.assertNotIn('PRIVATE',json.dumps(after))
            finally:controller.close()

    def test_owner_child_budget_extension_changes_comparison_policy_only(self):
        import json
        import time
        from generative_driver.benchmark_support.suites import child_request
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)
            (home/'config.json').write_text(json.dumps({'executors':{'codex':{'command':['missing-scripted-runtime']}}}),encoding='utf-8')
            controller=Controller(home)
            try:
                sid=controller.call('suite_start',{'manifest':legacy_manifest(2)})['suite_id']
                frozen=controller._suites.get(sid)['frozen']
                controller._suites.set_state(sid,'running');slot=controller._suites.reserve(sid)
                started=controller.start(child_request(frozen,slot));rid=started['run_id']
                controller._suites.attach(sid,0,slot['child_request_id'],rid)
                before=controller.call('suite_result',{'suite_id':sid})['experiment']
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    status=controller.call('status',{'run_id':rid})
                    if status['status'] in ('failed','blocked') and not status['stopping']:break
                    time.sleep(.01)
                self.assertIn(status['status'],('failed','blocked'));self.assertFalse(status['stopping'])
                controller.call('resume',{'run_id':rid,'budget_seconds':180,'budget_reason':'PRIVATE-REASON'})
                after=controller.call('suite_result',{'suite_id':sid,'offset':1})['experiment']
                self.assertEqual(after['comparison_identity']['budgets']['effective']['child_budget_overrides'],
                    [{'trial_key':slot['trial_key'],'budget_seconds':180}])
                self.assertEqual(after['comparison_identity']['budgets']['original'],before['comparison_identity']['budgets']['original'])
                self.assertEqual(after['execution_snapshots'],before['execution_snapshots'])
                self.assertNotIn('PRIVATE',json.dumps(after))
            finally:controller.close()
