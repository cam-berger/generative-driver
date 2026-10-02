"""Public workflow checks; synthetic workers are contract fixtures, not model trials."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark import prepare_stage, check_stage


class TQ9V2Tests(unittest.TestCase):
    def test_new_acquisition_contains_only_supplied_binary_evidence(self):
        # Catches adapter absence and leaking evaluator options into assignments.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = prepare_stage('tq9-v2', 'acquire', root/'run', root/'worker',
                options={'scenario_id': 'semantic', 'case_seed': 0,
                         'evaluator_password_file': '/private/never-copy'})
            self.assertEqual(len(prepared['inputs']), 1)
            binary = Path(prepared['inputs'][0])
            self.assertEqual(binary.suffix, '.bin')
            self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), prepared['context']['expected_sha256'])
            projected = json.dumps(prepared)
            self.assertNotIn('/private/never-copy', projected)
            self.assertNotIn('scenario_id', projected)
            self.assertEqual(prepared['context']['origin'], 'provided_binary')

    def test_acquisition_requires_imported_matching_bytes(self):
        # Catches accepting a success claim or stale hash without imported bytes.
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ws = root/'worker'
            prepared = prepare_stage('tq9-v2', 'acquire', root/'run', ws)
            self.assertFalse(check_stage('tq9-v2', 'acquire', root/'run', ws, {'status': 'completed'})['ok'])
            imported = call_tool('acquire_firmware_artifact', {'run_dir': str(ws/'tools'), **prepared['context']})
            self.assertTrue(imported.get('available'), imported)
            checked = check_stage('tq9-v2', 'acquire', root/'run', ws, {'status': 'completed'})
            self.assertTrue(checked['ok'], checked)
            Path(imported['artifact']).write_bytes(b'wrong firmware')
            self.assertFalse(check_stage('tq9-v2', 'acquire', root/'run', ws, {'status': 'completed'})['ok'])

    def test_interpretation_seals_inputs_and_rejects_candidate_tampering(self):
        # Catches unsealed interpretation or accepting edited original evidence.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = prepare_stage('tq9-v2', 'interpret', root/'run', root/'worker')
            ws = Path(prepared['work_dir'])
            self.assertFalse(prepared['report_required'])
            self.assertEqual(prepared['allowed_tools'], [])
            self.assertTrue((ws/'image.bin').is_file())
            (ws/'image.bin').write_bytes(b'tampered')
            checked = check_stage('tq9-v2', 'interpret', root/'run', ws, {})
            self.assertFalse(checked['ok'])
            self.assertFalse(next(c for c in checked['checks'] if c['name'] == 'sealed_inputs')['passed'])
            self.assertEqual(checked.get('fault'),'model')

    def test_action_records_keep_measured_units_and_pause_on_error(self):
        # Catches filling observed units from expected units and running during observation.
        from generative_driver.benchmark_support.emulated_actions import execute_plan
        from generative_driver.benchmark_support.behavior import validate_records
        class Session:
            running = False
            def set_running(self, value): self.running = value
            def observe(self):
                if self.running: raise AssertionError('Observation while running')
                return {'values': {'compare': 0, 'reload': 999}, 'reads': []}
        session = Session()
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a'*64, 'checks': [
            {'id': 'e/1/temperature', 'revision': 0, 'kind': 'number', 'expected': 12,
             'absolute_tolerance': 0, 'unit': 'degC', 'channel': 'runtime-transcript'}]}
        plan = [{'episode': 'e', 'step': '1', 'kind': 'call', 'task': 'temperature', 'inputs': {}, 'grants': []},
                {'episode': 'e', 'step': '1', 'kind': 'observe', 'checks': ['e/1/temperature']}]
        with tempfile.TemporaryDirectory() as temp:
            for unit in ('wrong', None):
                def invoke(task, inputs, grants):
                    self.assertTrue(session.running)
                    return {'ok': True, 'outputs': {'temperature': 12},
                            'units': {} if unit is None else {'temperature': unit}, 'transcript': []}
                rows = execute_plan(session, invoke, plan, contract, Path(temp)/'raw.json',
                    lambda raw, result, inputs: result['outputs'])
                self.assertEqual(validate_records(contract, rows)['verdict'], 'failed')
                self.assertTrue((Path(temp)/'raw.json').exists())
            def broken(*args): raise RuntimeError('operation failed')
            with self.assertRaises(RuntimeError):
                execute_plan(session, broken, plan, contract, Path(temp)/'raw.json', lambda *args: {})
            self.assertFalse(session.running)

    def test_family_contract_scores_varying_temperature_and_state_effects(self):
        # Catches constant readings and requested duty substituted for observed duty.
        from generative_driver.benchmark_support import tq9_v2
        from generative_driver.benchmark_support.behavior import validate_records
        truth = {'artifact_sha256': 'b'*64, 'contracts': {'units': {'temperature': 'degC', 'duty': 'fraction'},
            'scenarios': {'semantic': {'temperature_vectors': [
                {'stimulus': -7, 'expected': -7, 'absolute_tolerance': .1},
                {'stimulus': 29, 'expected': 29, 'absolute_tolerance': .1}]}},
            'effect_vectors': [{'task': 'arm', 'inputs': {}},
                               {'task': 'set_duty', 'inputs': {'duty': .4}},
                               {'task': 'disarm', 'inputs': {}}]}}
        pin = {'scenario_id': 'semantic', 'case_seed': 0, 'revision': 0}
        contract = tq9_v2.contract(pin, truth, 'final')
        plan = tq9_v2.build_plan(pin, truth, 'final')
        ids = [identifier for action in plan if action['kind'] == 'observe' for identifier in action['checks']]
        self.assertEqual(ids, [check['id'] for check in contract['checks']])
        rows = [{'id': c['id'], 'revision': 0, 'artifact_sha256': 'b'*64,
                 'unit': c['unit'], 'channel': c['channel'], 'value': c['expected']} for c in contract['checks']]
        self.assertEqual(validate_records(contract, rows)['verdict'], 'passed')
        for row in rows:
            if row['id'].endswith('/temperature'): row['value'] = -7
        self.assertEqual(validate_records(contract, rows)['verdict'], 'failed')
        observed = tq9_v2.observations({'values': {'compare': 0, 'reload': 999}},
                                     {'ok': True, 'outputs': {}}, {'duty': .4})
        self.assertEqual(observed['duty'], 0)

    def test_phase_inventories_are_disjoint_and_reject_constant_diagnostics(self):
        from generative_driver.benchmark_support import tq9_v2
        from generative_driver.benchmark_support.behavior import validate_records
        phases = {}
        for phase, temperatures, duties in (('diagnostic', (-8, 0, 31), (0, 450, 1000)),
                                            ('final', (-9, 1, 32), (1, 451, 999))):
            phases[phase] = {'temperature_vectors': [{'stimulus': v, 'expected': v, 'absolute_tolerance': 0} for v in temperatures],
                'effect_vectors': [{'task': 'arm', 'inputs': {}}] +
                    [{'task': 'set_duty', 'inputs': {'duty': v}} for v in duties] + [{'task': 'disarm', 'inputs': {}}]}
        truth = {'artifact_sha256': 'a'*64, 'contracts': {'units': {'temperature': 'degC', 'duty': 'permille'},
            'scenarios': {'semantic': {**phases['final'], 'phases': phases}}, 'effect_vectors': phases['final']['effect_vectors']}}
        plans = [tq9_v2.build_plan({'scenario_id': 'semantic'}, truth, phase) for phase in ('diagnostic', 'final')]
        values = [{a['values']['temperature'] for a in plan if a['kind'] == 'reset'} for plan in plans]
        targets = [{a['inputs']['duty'] for a in plan if a.get('task') == 'set_duty'} for plan in plans]
        for inventory in (*values, *targets): self.assertGreaterEqual(len(inventory), 3)
        self.assertFalse(values[0] & values[1]); self.assertFalse(targets[0] & targets[1])
        contract = tq9_v2.contract({'scenario_id': 'semantic'}, truth, 'diagnostic')
        records = [{'id': c['id'], 'revision': 0, 'artifact_sha256': 'a'*64, 'unit': c['unit'],
            'channel': c['channel'], 'value': 31 if c['id'].endswith('/temperature') else c['expected']} for c in contract['checks']]
        self.assertEqual(validate_records(contract, records)['verdict'], 'failed')

    def test_tq9_monitor_measurement_uses_permille(self):
        # Catches fraction/permille confusion at the actual family boundary.
        from generative_driver.benchmark_support.tq9_v2 import observations
        measured = observations({'values': {'compare': 400, 'reload': 999}}, {'outputs': {}}, {})
        self.assertEqual(measured['duty'], 400)
        self.assertEqual(observations.monitor_units['duty'], 'permille')

    def test_probe_requires_accepted_model_before_starting_native_process(self):
        # Catches using a proposed/unaccepted model as the probe input.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'accepted interpret'):
                prepare_stage('tq9-v2', 'probe', root/'run', root/'worker')

    def test_unknown_maintenance_claim_is_recorded_without_credit(self):
        # Catches treating a valid unknown claim as malformed rather than unsuccessful.
        from generative_driver.benchmark_support.scenarios import read_maintenance_claim, maintenance_decision
        with tempfile.TemporaryDirectory() as temp:
            ws = Path(temp)
            path = ws/'maintenance.json'
            path.write_text(json.dumps({'schema': 'benchmark-maintenance-claim/1', 'claim': 'unknown', 'evidence_ids': ['10']}))
            report = {'status': 'completed', 'artifacts': [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}
            claim = read_maintenance_claim(ws, report, 'assigned', [{'id': '10', 'assignment_id': 'assigned', 'actor': 'worker'}])
            self.assertEqual(claim['claim'], 'unknown')
            outcome = maintenance_decision(scenario='semantic', claim=claim['claim'],
                diagnostic={'evaluable': True, 'contradiction': True, 'evidence_ids': ['evaluator-20']}, repaired=False)
            self.assertFalse(outcome['ok'])
            self.assertIsNone(outcome['route'])

    def test_cleanup_refuses_lost_native_ownership(self):
        # Catches PID-only termination or silently treating an attached process as owned.
        from generative_driver.benchmark import cleanup
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'benchmark').mkdir()
            (root/'benchmark/state.json').write_text(json.dumps({'session': {'pid': 12345}}))
            with self.assertRaisesRegex(RuntimeError, 'ownership'):
                cleanup('tq9-v2', root)
            self.assertIn('session', json.loads((root/'benchmark/state.json').read_text()))

    def test_final_plan_requires_permission_refusal_without_effect(self):
        # Catches omitting denied-effect checks or accepting a refusal after I/O.
        from generative_driver.benchmark_support import tq9_v2
        truth = {'artifact_sha256': 'b'*64, 'contracts': {'units': {'temperature': 'degC', 'duty': 'permille'},
            'scenarios': {'semantic': {'temperature_vectors': [{'stimulus': 20, 'expected': 20, 'absolute_tolerance': .1}]}},
            'effect_vectors': [{'task': 'arm', 'inputs': {}}, {'task': 'set_duty', 'inputs': {'duty': 400}}, {'task': 'disarm', 'inputs': {}}]}}
        plan = tq9_v2.build_plan({'scenario_id': 'semantic'}, truth, 'final')
        denied = [a for a in plan if a['kind']=='call' and a['task']=='set_duty' and not a['grants']]
        self.assertEqual(len(denied), 1)
        result = {'ok': False, 'error': {'fault':'operator'}, 'transcript': [{'tx':'00'}]}
        observed = tq9_v2.observations({'values': {'compare':0,'reload':999}}, result, {})
        self.assertFalse(observed['refused_without_io'])

    def test_diagnostic_scalar_observation_is_measured_and_strict(self):
        # Catches coercing missing/nonfinite property readings into valid references.
        from test_benchmark_native import MonitorFixture
        from generative_driver.benchmark_support.native import NativeSession
        for value, good in ((12.5, True), (float('nan'), False), (float('inf'), False)):
            with self.subTest(value=value), MonitorFixture() as peer:
                peer.value = value
                session = NativeSession.attach({'monitor_port':peer.port, 'binding':{'host':'127.0.0.1','port':2}},
                    {'observations':{'reference':{'kind':'scalar','command':'read','unit':'degC'}}})
                if good:
                    self.assertEqual(session.observe()['values']['reference'], 12.5)
                else:
                    with self.assertRaises(RuntimeError): session.observe()

    def test_control_scenario_can_be_pinned_without_evaluator_password(self):
        # Catches omitting the unchanged-firmware environmental control from discovery.
        from generative_driver.benchmark_support.registry import pin_case
        pin = pin_case('tq9-v2', 'control', 0)
        self.assertEqual(pin['scenario_id'], 'control')
        self.assertNotIn('evaluator_password_file', pin)

    def test_pending_scenario_blocks_before_any_new_native_launch(self):
        # Catches replay of a crash-interrupted scenario during next preparation.
        from generative_driver.benchmark_support.scenarios import ScenarioJournal
        from generative_driver.configurator import digest
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root/'accepted/package'; package.mkdir(parents=True)
            (package/'manifest.json').write_text('{}')
            caps = root/'accepted/capabilities.json'; caps.write_text('{}')
            (root/'run/benchmark').mkdir(parents=True)
            (root/'run/benchmark/state.json').write_text(json.dumps({'package_sha256':digest(package)}))
            ScenarioJournal(root/'run/benchmark').begin('interrupted', 'a'*64)
            accepted = [{'stage':'emit','artifacts':[{'path':str(package),'sha256':digest(package)}]},
                        {'stage':'probe','artifacts':[{'path':str(caps),'sha256':digest(caps)}]}]
            with self.assertRaisesRegex(RuntimeError, 'reconciliation'):
                prepare_stage('tq9-v2','maintain',root/'run',root/'worker',accepted)

    def test_passed_manifest_label_cannot_bypass_calibration_admission(self):
        # Catches trusting a mutable public 'passed' label without authenticated evidence.
        from generative_driver.benchmark_support.registry import require_calibration, pin_case
        pin = pin_case('tq9-v2','semantic',0)
        pin['calibration'] = {'status':'passed','evidence_sha256':'a'*64}
        with self.assertRaises(ValueError):
            require_calibration(pin, {'evaluator_password_file':'/does/not/exist'})

    def test_case_seed_reorders_only_independent_final_episodes(self):
        # Catches pinned seeds being ignored or dropping final coverage during variation.
        from generative_driver.benchmark_support import tq9_v2
        truth = {'artifact_sha256':'a'*64,'contracts':{'units':{'temperature':'degC','duty':'permille'},
            'scenarios':{'semantic':{'temperature_vectors':[
                {'stimulus':-7,'expected':-7,'absolute_tolerance':0},
                {'stimulus':29,'expected':29,'absolute_tolerance':0}]}},
            'effect_vectors':[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':400}},{'task':'disarm','inputs':{}}]}}
        a=tq9_v2.build_plan({'scenario_id':'semantic','case_seed':0},truth,'final')
        b=tq9_v2.build_plan({'scenario_id':'semantic','case_seed':1},truth,'final')
        self.assertNotEqual([x['values'] for x in a if x['kind']=='reset'], [x['values'] for x in b if x['kind']=='reset'])
        self.assertEqual(sorted(i for x in a if x['kind']=='observe' for i in x['checks']),sorted(i for x in b if x['kind']=='observe' for i in x['checks']))
        self.assertEqual([x['task'] for x in a if x['kind']=='call' and x['task']!='temperature'],[x['task'] for x in b if x['kind']=='call' and x['task']!='temperature'])

    def test_scripted_controller_seals_actual_acceptances_for_control_and_semantic(self):
        # Catches sealing proposed paths, missing final maintenance, and bypassed worker reuse.
        import copy, http.server, shutil, sys, threading, time
        from unittest.mock import patch
        import generative_driver
        from generative_driver.benchmark import case_root
        from generative_driver.benchmark_support.truth import seal, unlock
        from generative_driver.configurator import Controller, digest
        from tq9_workflow_fixture import Silicon, WORKER
        for scenario, mode in (('control','normal'),('semantic','normal'),('control','unknown-retry'),('control','host-retry'),('control','hidden-wrong'),('control','monitor-missing'),('control','snapshot-next')):
            with self.subTest(scenario=scenario,mode=mode), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); resources=root/'resources'; case=resources/'cases/tq9-v2';case.mkdir(parents=True)
                manifest=json.loads((case_root()/'cases/tq9-v2/case.json').read_text())
                for name, content in [('firmware.bin',b'original'),('firmware-semantic.bin',b'semantic'),('firmware-identity.bin',b'identity')]:
                    (case/name).write_bytes(content);manifest['images'][name]=hashlib.sha256(content).hexdigest()
                password=root/'password';password.write_text('synthetic fixture credential')
                recipe={'stimuli':{'temperature':{'minimum':-100,'maximum':100,'command':'temp {value}'}},
                    'observations':{'compare':{'command':'compare','width':32,'count':1},'reload':{'command':'reload','width':32,'count':1},
                        'temperature':{'kind':'scalar','command':'temperature','unit':'degC'}},'reset_commands':['reset']}
                scenarios={name:{'base_image':'firmware.bin','next_image':'firmware-semantic.bin' if name=='semantic' else 'firmware.bin',
                    'temperature_vectors':[{'stimulus':-7,'expected':-7,'absolute_tolerance':.01},{'stimulus':29,'expected':29,'absolute_tolerance':.01}]}
                    for name in ('semantic','control','identity')}
                truth={'private_sentinel':'EVALUATOR SENTINEL','images':manifest['images'],'recipe':recipe,'contracts':{
                    'units':{'temperature':'degC','duty':'permille'},'scenarios':scenarios,
                    'effect_vectors':[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':400}},{'task':'disarm','inputs':{}}],
                    'time_policy':{'sample_settle_seconds':0}}}
                for definition in scenarios.values():
                    definition['phases'] = {'diagnostic': {
                        'temperature_vectors': [{'stimulus': v, 'expected': v, 'absolute_tolerance': .01} for v in (-8, 0, 31)],
                        'effect_vectors': [{'task':'arm','inputs':{}}] + [{'task':'set_duty','inputs':{'duty':v}} for v in (0,450,1000)] + [{'task':'disarm','inputs':{}}]}}
                manifest['truth']['sha256']=seal(truth,resources/manifest['truth']['path'],password.read_text())
                manifest['calibration']={'status':'pending'}
                (case/'case.json').write_text(json.dumps(manifest))
                controller=Controller(root/'home'); peers=[]; worker_live=[]
                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_POST(self):
                        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        try:
                            before=len(peers[-1].commands) if peers else 0
                            response=controller.call(request['method'],request['params'])
                            if request['params'].get('name') in ('interface_execute','probe_run'):
                                worker_live.append((request['params']['name'],response.get('ok'),peers[-1].running,peers[-1].commands[before:]))
                                if mode == 'monitor-missing' and request['params']['name'] == 'probe_run':
                                    peers[-1].monitor_available = False
                        except Exception as error: response={'fixture_error':str(error)}
                        body=json.dumps(response).encode();self.send_response(200);self.end_headers();self.wfile.write(body)
                    def log_message(self,*args):pass
                server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
                threading.Thread(target=server.serve_forever,daemon=True).start()
                script=root/'scripted_worker.py'
                script.write_text(WORKER.replace('PACKAGE_PARENT',repr(str(Path(generative_driver.__file__).resolve().parents[1])))
                    .replace('FIXTURE_PARENT',repr(str(Path(__file__).parent))).replace('SERVICE_URL',repr('http://127.0.0.1:'+str(server.server_port))))
                if mode == 'unknown-retry':
                    source = script.read_text()
                    source = source.replace("        pathlib.Path('maintenance.json').write_text",
                        "        if not pathlib.Path(" + repr(str(root/'unknown-claimed')) + ").exists():\n" +
                        "            claim['claim']='unknown';pathlib.Path(" + repr(str(root/'unknown-claimed')) + ").touch()\n" +
                        "        pathlib.Path('maintenance.json').write_text")
                    script.write_text(source)
                from generative_driver.benchmark_support.emulated_evidence import maintenance_observation
                observations = []
                def observed_maintenance(*args, **kwargs):
                    value = maintenance_observation(*args, **kwargs)
                    if mode == 'host-retry' and not observations:
                        value = dict(value, evaluable=False, fault='host')
                    observations.append(value)
                    return value
                def native_start(**kwargs):
                    peer=Silicon(kwargs['image']);peers.append(peer)
                    peer.wrong_hidden = mode == 'hidden-wrong'
                    peer.monitor_available = True
                    return peer.session(kwargs['recipe'],root/('native-'+str(len(peers))))
                original_accept=controller._accept
                def accept(*args,**kwargs):
                    handoff=original_accept(*args,**kwargs)
                    if mode=='snapshot-next' and handoff['stage']=='acquire':
                        (root/'home/runs'/args[0]/'benchmark/inputs/firmware.bin').write_bytes(b'tampered after acceptance')
                    return handoff
                try:
                    with patch.object(controller,'_accept',side_effect=accept), patch('generative_driver.benchmark.case_root',return_value=resources), \
                         patch('generative_driver.benchmark_support.registry.require_calibration',return_value={'ok':True}), \
                         patch('generative_driver.benchmark_support.native.NativeSession.start',side_effect=native_start), \
                         patch('generative_driver.benchmark_support.emulated_evidence.maintenance_observation',side_effect=observed_maintenance):
                        run=controller.start({'goal':'scripted-contract-fixture','case':'tq9-v2','budget_seconds':60,
                            'effects':['write','actuate'],'case_options':{'scenario_id':scenario,'evaluator_password_file':str(password),'renode':'synthetic TCP fixture'},
                            'executor_config':{'command':[sys.executable,str(script)]}})
                        deadline=time.monotonic()+60
                        recovered=False
                        while True:
                            status=controller.call('status',run)
                            if status['status'] in ('completed','failed','blocked','cancelled') and not status['stopping']:
                                if mode in ('unknown-retry','host-retry') and not recovered:
                                    self.assertEqual(status['status'],'blocked',status)
                                    self.assertEqual(status['outcome_category'],'unknown')
                                    if mode == 'host-retry':
                                        self.assertTrue(status['uncertain_effect'])
                                        with self.assertRaisesRegex(ValueError,'uncertain'):controller.call('resume',run)
                                        self.assertEqual(peers[-1].duty,0)
                                        controller.call('respond',{**run,'observation':{'source':'scripted independent peer',
                                            'effect_resolution':'confirmed_safe','evidence':'Toy peer duty is zero after disarm'}})
                                    controller.call('resume',run);recovered=True
                                    continue
                                break
                            if time.monotonic()>deadline:self.fail('Scripted controller deadline: '+str(status))
                            time.sleep(.03)
                        if mode not in ('normal','unknown-retry','host-retry'):
                            self.assertEqual(status['status'],'failed',status)
                            result=controller.call('result',run)
                            assigned=[e['data'] for e in controller.call('events',run)['events'] if e['kind']=='stage.assigned']
                            self.assertEqual(result['progress']['maintenance_cycles'],0)
                            if mode=='hidden-wrong':
                                self.assertEqual([a['stage'] for a in assigned],['acquire','interpret','probe','ground','emit','reuse'])
                                self.assertTrue(result['progress']['terminal_final_failure'])
                                with self.assertRaisesRegex(ValueError,'final'):controller.call('resume',run)
                                sidecar=root/'home/runs'/run['run_id']/'benchmark/run-evidence.enc'
                                self.assertTrue(sidecar.exists())
                            elif mode=='monitor-missing':
                                self.assertTrue(status['uncertain_effect'])
                                self.assertNotIn('maintain',[a['stage'] for a in assigned])
                            else:
                                self.assertEqual([a['stage'] for a in assigned],['acquire'])
                                self.assertIn('Snapshot input',status['reason'])
                            continue
                        self.assertEqual(status['status'],'completed',{'status':status,'progress':controller.call('result',run).get('progress')})
                        result=controller.call('result',run);accepted=result['accepted_handoffs']
                        self.assertEqual(result['progress']['maintenance_cycles'],int(scenario=='semantic'))
                        self.assertEqual({a['stage'] for a in accepted},{'acquire','interpret','probe','ground','emit','reuse','maintain'})
                        self.assertTrue(worker_live)
                        self.assertEqual([row[:2] for row in worker_live[:3]],
                            [('interface_execute',False),('interface_execute',True),('probe_run',True)])
                        self.assertTrue(all(not running and commands == ['start','pause'] for _,_,running,commands in worker_live))
                        self.assertFalse(any(peer.paused_requests for peer in peers))
                        events=controller.call('events',run)['events']
                        live_events=[e for e in events if e['kind']=='tool.finished' and e['data'].get('name') in ('interface_execute','probe_run')]
                        self.assertEqual(len(live_events),len(worker_live))
                        self.assertTrue(all(e['data']['actor']=='worker' for e in live_events))
                        assignments=[e['data'] for e in events if e['kind']=='stage.assigned']
                        self.assertNotIn('EVALUATOR SENTINEL',json.dumps(assignments))
                        reuse=[a for a in assignments if a['stage']=='reuse']
                        self.assertTrue(all(len(a['inputs'])==1 for a in reuse))
                        self.assertEqual(len({a['workspace'] for a in reuse}),len(reuse))
                        run_dir=root/'home/runs'/run['run_id']
                        state=json.loads((run_dir/'benchmark/state.json').read_text())
                        sidecar=run_dir/'benchmark/run-evidence.enc'
                        sealed=unlock(sidecar,password.read_text(),state['final_evaluation']['evidence_sha256'])
                        self.assertEqual(len(sealed['accepted_gates']),len(accepted))
                        self.assertTrue(all('/accepted/' in a['path'] for a in sealed['accepted_artifacts']))
                        self.assertEqual(sealed['accepted_gates'][-1]['stage'],'maintain')
                        self.assertEqual(state['maintenance']['drift_claimed'],scenario=='semantic')
                        self.assertEqual(state['maintenance']['drift_observed'],scenario=='semantic')
                        from generative_driver.benchmark_support.evidence import public_v2_report
                        from generative_driver.benchmark import score
                        public=public_v2_report({**sealed,'snapshot_sha256':sealed['execution_snapshot']['snapshot_sha256'],
                            'executed':sealed['execution_snapshot']['executed'],'final_evaluation':state['final_evaluation']})
                        self.assertEqual(score(public,password,evidence_path=sidecar)['verdict'],'passed')
                        from generative_driver.reporting import report_run
                        from generative_driver.benchmark_support.suite_reporting import _recorded_success
                        with patch('generative_driver.client.call',side_effect=lambda method,params,**kw:controller.call(method,params)):
                            actual_report=report_run(run['run_id'],home=root/'home')
                        self.assertTrue(_recorded_success(result,events,actual_report))
                        authenticated=score(actual_report,password,evidence_path=sidecar)
                        self.assertEqual(authenticated['verdict'],'passed')
                        self.assertEqual(authenticated['maintenance'],actual_report['maintenance'])
                        if mode in ('unknown-retry','host-retry'):
                            self.assertTrue(recovered)
                            self.assertEqual(len(sealed['maintenance']['attempts']),2)
                            self.assertEqual(actual_report['stages']['maintain']['attempt_count'],2)
                            self.assertEqual([a['accepted'] for a in actual_report['stages']['maintain']['attempts']],[False,True])
                            self.assertGreaterEqual(actual_report['stages']['maintain']['worker_seconds'],0)
                            self.assertFalse(actual_report['maintenance']['repair_completed'])
                            self.assertFalse(actual_report['maintenance']['requalified'])

                        from generative_driver.benchmark_support.registry import adapter_for
                        adapter=adapter_for('tq9-v2')
                        with self.assertRaisesRegex(ValueError,'evidence'):
                            adapter.score(public,password)
                        self.assertEqual(adapter.score(public,password,evidence_path=sidecar)['verdict'],'passed')
                        if scenario == 'control':
                            # A resumed partial run must reseal newly accepted handoffs.
                            from generative_driver.benchmark_support.emulated_evidence import finalize
                            options={'evaluator_password_file':str(password)}
                            for handoffs in (accepted[:1], accepted):
                                finalize('tq9-v2',run_dir,handoffs,options)
                                latest=json.loads((run_dir/'benchmark/state.json').read_text())
                                updated=unlock(sidecar,password.read_text(),latest['final_evaluation']['evidence_sha256'])
                                self.assertEqual(len(updated['accepted_gates']),len(handoffs))
                        initial=sealed['maintenance']['initial']
                        self.assertTrue(set(initial['worker_evidence_ids']).isdisjoint(initial['diagnostic']['evidence_ids']))
                        self.assertTrue(any(e['kind']=='tool.finished' and e['data'].get('actor')=='evaluator' for e in events))
                        with self.assertRaisesRegex(ValueError, 'inactive'):
                            controller.call('tools',{**run,'assignment_id':assignments[0]['id']})
                finally:
                    controller.close();server.shutdown();server.server_close()
                    for peer in peers:
                        if not getattr(peer,'closed',False):peer.close()

    def test_measured_calibration_checks_code_and_complete_reference_mutant_coverage(self):
        # Catches accepting stale evaluator code or incomplete measured qualification.
        import copy
        from generative_driver.benchmark_support.reference_calibration import calibration_identity, validate_record
        identity=calibration_identity()
        record={'schema':'benchmark-calibration/1','status':'passed','case':'tq9-v2','evaluator_version':'2',
            'execution':'reference-calibration','backend':'native-renode','native_process_observed':True,
            'images':{'firmware.bin':'a'*64},'input_hashes':{'source':'b'*64,'contract':'c'*64},
            'evaluator_identity':identity,'tools':{name:'observed fixture version' for name in ('compiler','objcopy','renode','ghidra','java')},
            'references':[{'id':name,'model_sha256':digest,'passed':True,'scenarios':['original','semantic','control','identity']}
                for name,digest in [('reference-a','d'*64),('reference-b','e'*64)]],
            'required_mutants':['wrong-scale','constant-output','wrong-state','false-drift'],
            'mutants':[{'id':name,'rejected':True,'failed_checks':['fixture/check'], 'failures':[{'id':'fixture/check','reason':'value mismatch'}], 'scenario':scenario}
                for name in ('wrong-scale','constant-output','wrong-state','false-drift') for scenario in ('original','semantic','control','identity')],
            'scenarios':{name:{'passed':True} for name in ('original','semantic','control','identity')}}
        names=[ref['id']+'-'+scenario for ref in record['references'] for scenario in ref['scenarios']]
        names += [row['id']+'-'+row['scenario'] for row in record['mutants']]
        record['execution_inputs']={name:{'model_sha256':'a'*64,'capabilities_sha256':'b'*64} for name in names}
        record['runs']=[{'id':name,**value} for name,value in record['execution_inputs'].items()]
        manifest={'id':'tq9-v2','evaluator_version':'2','images':record['images'], 'calibration':{'input_hashes':record['input_hashes']}}
        self.assertTrue(validate_record(manifest,record)['ok'])
        for change in ('code','reference','mutant','reason','backend','executed-model','executed-capabilities'):
            bad=copy.deepcopy(record)
            if change=='code':bad['evaluator_identity']['sha256']='f'*64
            elif change=='reference':bad['references'].pop()
            elif change=='mutant':bad['mutants'].pop()
            elif change=='reason':bad['mutants'][0]['failures']=[]
            elif change=='executed-model':bad['runs'][0]['model_sha256']='f'*64
            elif change=='executed-capabilities':bad['runs'][0]['capabilities_sha256']='f'*64
            else:bad['backend']='scripted TCP fixture'
            with self.subTest(change=change):self.assertFalse(validate_record(manifest,bad)['ok'])

        # Authenticated synthetic calibration exercises admission, never claims native work.
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import require_calibration
        from generative_driver.benchmark_support.reference_calibration import input_hashes
        from generative_driver.benchmark_support.snapshots import canonical_digest
        from generative_driver.benchmark_support.truth import seal
        truth = {key:{} for key in ('source_hashes','contracts','recipe','references','build','analysis')}
        truth['images'] = record['images']
        truth['mutations'] = {'required':[],'mutations':[]}
        model={'operations':{'temp':{'outputs':{'value':{'scale':1,'unit':'degC'}}},'arm':{'toy':1},'disarm':{'toy':0}}}
        caps={'tasks':{'temperature':{'operation':'temp','outputs':{'temperature':{'output':'value','unit':'degC'}}},
                       'arm':{'operation':'arm','outputs':{}},'disarm':{'operation':'disarm','outputs':{}}}}
        truth['references']={name:{'model':{**model,'toy_reference':name},'semantic_model':{**model,'toy_reference':name+'semantic'},
            'identity_model':{**model,'toy_reference':name+'identity'},'capabilities':caps} for name in ('reference-a','reference-b')}
        truth=json.loads(json.dumps(truth,sort_keys=True))
        from generative_driver.benchmark_support.reference_calibration import tq9_execution_inputs
        record['execution_inputs']=tq9_execution_inputs(truth)
        record['runs']=[{'id':name,**value} for name,value in record['execution_inputs'].items()]

        record['input_hashes'] = input_hashes(truth)
        manifest.update(schema='benchmark-case/2')
        manifest['calibration'] = {'status':'passed','input_hashes':record['input_hashes'],
                                  'evidence_sha256':canonical_digest(record)}
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);password=root/'password';password.write_text('synthetic admission fixture')
            truth['calibration']=record
            manifest['truth']={'path':'truth.enc','sha256':seal(truth,root/'truth.enc',password.read_text())}
            pin={'case_id':'tq9-v2','scenario_id':'control','case_seed':0,
                 'manifest':manifest,'calibration':manifest['calibration']}
            with patch('generative_driver.benchmark_support.registry.pin_case',return_value=pin), \
                 patch('generative_driver.benchmark.case_root',return_value=root):
                self.assertTrue(require_calibration(pin,{'evaluator_password_file':str(password)})['ok'])
                for field in ('model_sha256','capabilities_sha256'):
                    for name in ('reference-a-original','reference-b-semantic','wrong-state-control'):
                        row=next(row for row in record['runs'] if row['id']==name)
                        old=row[field];row[field]='f'*64;record['execution_inputs'][name][field]='f'*64
                        manifest['calibration']['evidence_sha256']=canonical_digest(record)
                        manifest['truth']['sha256']=seal(truth,root/'truth.enc',password.read_text())
                        with self.subTest(field=field,run=name),self.assertRaisesRegex(ValueError,'Calibration'):
                            require_calibration(pin,{'evaluator_password_file':str(password)})
                        row[field]=old;record['execution_inputs'][name][field]=old
                manifest['calibration']['evidence_sha256']=canonical_digest(record)
                truth['recipe']={'changed':True}
                manifest['truth']['sha256']=seal(truth,root/'truth.enc',password.read_text())
                with self.assertRaisesRegex(ValueError,'Calibration'):
                    require_calibration(pin,{'evaluator_password_file':str(password)})

    def test_reference_gate_refuses_structurally_invalid_mutant_before_native_start(self):
        # Catches recording a malformed model as a measured behavioral rejection.
        from generative_driver.benchmark_support.emulated import reference_execute
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'structurally valid'):
                reference_execute({}, {}, {'schema':'invalid'}, {}, renode='/absent',image='/absent',output_dir=Path(temp))

    def test_action_host_failure_cannot_be_a_behavioral_rejection(self):
        # Synthetic runtime fault: an unavailable channel cannot reject a mutant.
        from generative_driver.benchmark_support.emulated_actions import execute_plan
        class Session:
            running = False
            def set_running(self, value): self.running = value
        session = Session()
        plan = [{'kind':'call','task':'temperature','inputs':{},'grants':[]}]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'raw.json'
            with self.assertRaisesRegex(RuntimeError, 'Host execution failed'):
                execute_plan(session, lambda *args: {'ok':False,'error':{'fault':'host'}},
                    plan, {'checks':[]}, path, lambda *args: {})
            self.assertFalse(session.running)
            raw = json.loads(path.read_text())
            self.assertTrue(raw['incomplete'])
            self.assertEqual(raw['events'][0]['result']['error']['fault'], 'host')

    def test_frozen_final_replay_is_refused_before_device_access(self):
        # A saved final submission may not be replayed after a restart.
        from unittest.mock import patch
        from generative_driver.benchmark_support.emulated_evidence import _private, _final
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);password=root/'password';password.write_text('fixture')
            options={'evaluator_password_file':str(password)}
            _private(root,options,{'evaluations':[{'phase':'final','revision':0}],'maintenance':{}})
            with patch('generative_driver.benchmark_support.emulated._session',side_effect=AssertionError('device accessed')):
                with self.assertRaisesRegex(ValueError,'cannot be replayed'):
                    _final('tq9-v2',root,root/'absent',{},options)

    def test_fresh_reuse_acknowledgements_do_not_substitute_for_a_reading(self):
        # Public adapter seam: actuator ACK outputs alone do not finish the mission.
        from generative_driver.benchmark_support.cases import _write
        from tq9_workflow_fixture import capabilities
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);caps=root/'caps.json';caps.write_text(json.dumps(capabilities()))
            _write(root/'benchmark/state.json',{'package_attempts':{'reuse':'new'},
                'capabilities_path':str(caps),'package_copies':{'reuse':str(root/'absent')}})
            rows=[{'actor':'worker','stage':'reuse','attempt_id':'new','revision':0,
                   'operation':'set_duty' if duty else 'disarm','result':{'ok':True,'outputs':{'ack':1}},
                   'observation':{'duty':duty}} for duty in (370,0)]
            (root/'benchmark/package-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            result=check_stage('tq9-v2','reuse',root,root,{'status':'completed'})
            self.assertFalse(result['ok'])
            self.assertIn('reading',result['reason'])
            self.assertEqual(result.get('fault'),'model')
            for fault in ('host','operator'):
                rows[0]['result']={'ok':False,'error':{'fault':fault}}
                (root/'benchmark/package-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                blocked=check_stage('tq9-v2','reuse',root,root,{'status':'completed'})
                self.assertEqual(blocked.get('fault'),fault)

    def test_reuse_failed_calls_need_trusted_attribution_before_model_blame(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.suites import child_disposition
        from tq9_workflow_fixture import capabilities
        untyped={'ok':False,'error':{'code':'package_error','message':'Toy package filesystem read failed'}}
        modeled={'ok':False,'error':{'fault':'model'}}
        cases=[([untyped],None),([{'ok':False}],None),([{'ok':False,'error':'opaque'}],None),
            ([{'ok':False,'error':{'fault':'unknown'}}],None),([modeled],'model'),
            ([untyped,modeled],None),([modeled,untyped],None),
            ([untyped,{'ok':False,'error':{'fault':'host'}}],'host'),
            ([modeled,{'ok':False,'error':{'fault':'operator'}}],'operator'),
            ([{'ok':True,'outputs':{'ack':1}}],'model')]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);caps=root/'caps.json';caps.write_text(json.dumps(capabilities()))
            emulated._write(root/'benchmark/state.json',{'revision':0,'package_attempts':{'reuse':'current'},
                'capabilities_path':str(caps)})
            def event(result,attempt='current',revision=0):
                return {'actor':'worker','stage':'reuse','attempt_id':attempt,'revision':revision,
                    'operation':'disarm','result':result,'observation':{'duty':0}}
            for results,want in cases:
                with self.subTest(results=results):
                    # Other attempts/revisions cannot make a conclusive current failure ambiguous.
                    rows=[event(untyped,'old'),event(untyped,revision=1)]+[event(r) for r in results]
                    (root/'benchmark/package-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                    with patch.object(emulated,'_inputs',return_value=(root,{})):
                        checked=emulated.check_stage('tq9-v2','reuse',root,root,{'status':'completed'})
                    self.assertFalse(checked['ok']);self.assertEqual(checked.get('fault'),want)
                    disposition=child_disposition({'status':'blocked','stopping':False,'uncertain_effect':False,
                        'outcome_category':checked.get('fault') or 'unknown'})
                    self.assertEqual(disposition,'advance' if want=='model' else 'block')

    def test_unavailable_native_tools_leave_a_pending_calibration_release(self):
        # Synthetic encrypted authoring input, not a native qualification claim.
        from unittest.mock import patch
        from generative_driver.benchmark import case_root
        from generative_driver.benchmark_support.truth import seal
        from generative_driver.benchmark_support.authoring import calibrate
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);resources=root/'resources';case=resources/'cases/tq9-v2'
            shutil.copytree(case_root()/'cases/tq9-v2',case)
            manifest=json.loads((case/'case.json').read_text())
            truth={key:{} for key in ('source_hashes','contracts','recipe','references','build','analysis')}
            truth['mutations']={'required':[]}
            password=root/'password';password.write_text('synthetic calibration fixture')
            manifest['truth']['sha256']=seal(truth,resources/manifest['truth']['path'],password.read_text())
            (case/'case.json').write_text(json.dumps(manifest))
            with patch('generative_driver.benchmark.case_root',return_value=resources):
                result=calibrate('tq9-v2',{'evaluator_password_file':str(password),'output':str(root/'output'),
                    'compiler':'/absent','renode':None,'ghidra_home':None,'java_home':None})
            self.assertFalse(result['ok'])
            self.assertEqual(result['calibration'],'pending')
            record=json.loads((root/'output/calibration-record.json').read_text())
            self.assertFalse(record['runs'])
            self.assertFalse(record['mutants'])

    def test_calibration_identity_includes_the_executed_analysis_wrappers(self):
        # Static tooling is executable evidence, unlike enclosing case ciphertext.
        from generative_driver.benchmark_support.reference_calibration import calibration_identity
        files=calibration_identity()['files']
        self.assertIn('resources/tools/workspace/ghidra_run.py',files)
        self.assertIn('resources/toolchain/skills/interpret-firmware-binary/ExportDecomp.java',files)
        self.assertFalse(any('/bench/cases/' in path or path.endswith('.enc') for path in files))

    def test_monitor_rejection_keeps_recipe_and_response_in_private_evidence(self):
        # TCP fixture: a worker-visible exception must not echo private monitor data.
        from tq9_workflow_fixture import Silicon
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);image=root/'image.bin';image.write_bytes(b'fixture')
            peer=Silicon(image);peer.monitor_error='Error PRIVATE-MONITOR-SENTINEL'
            private=root/'private'
            session=peer.session({'observations':{'duty':{'command':'compare','width':32,'count':1}}},private)
            try:
                with self.assertRaises(RuntimeError) as caught:session.observe()
                self.assertNotIn('PRIVATE-MONITOR-SENTINEL',str(caught.exception))
                self.assertNotIn('compare',str(caught.exception))
                self.assertIn('PRIVATE-MONITOR-SENTINEL',(private/'monitor-errors.jsonl').read_text())
            finally:session.stop()

    def test_read_only_controller_refuses_case_effects_before_evaluator_io(self):
        # Public Controller admission; calibrated-label fixture avoids opening private truth.
        import sys
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        for grants in (None, ['read'], ['write'], ['actuate']):
            with self.subTest(grants=grants), tempfile.TemporaryDirectory() as temp:
                controller=Controller(Path(temp)/'home')
                spec={'goal':'scripted no-effects fixture','case':'tq9-v2','case_options':{'scenario_id':'control'},
                      'executor_config':{'command':[sys.executable,'-c','raise SystemExit(0)']}}
                if grants is not None:spec['effects']=grants
                try:
                    with patch('generative_driver.benchmark_support.registry.require_calibration',return_value={'ok':True}), \
                         patch('generative_driver.benchmark_support.native.NativeSession.start') as native:
                        with self.assertRaisesRegex(ValueError,'effect grants'):
                            controller.start(spec)
                        native.assert_not_called()
                        self.assertFalse(controller._workers)
                finally:controller.close()

class GateCategoryTests(unittest.TestCase):
    def test_missing_candidate_artifacts_are_model_failures(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(emulated,'_inputs',return_value=(root,{'images':{'firmware.bin':'a'*64}})):
                for stage in ('acquire','probe','emit'):
                    with self.subTest(stage=stage):
                        result=emulated.check_stage('tq9-v2',stage,root,root,{'status':'completed'})
                        self.assertFalse(result['ok']);self.assertEqual(result.get('fault'),'model')

    def test_ground_failure_uses_observed_diagnostic_not_missing_state(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            for diagnostic,want in (({'passed':False},'model'),({},None)):
                with self.subTest(diagnostic=diagnostic),patch.object(emulated,'_inputs',return_value=(root,{})), \
                     patch.object(emulated,'_state',return_value=(root/'state',{'diagnostic':diagnostic})):
                    result=emulated.check_stage('tq9-v2','ground',root,root,{'status':'completed'})
                    self.assertFalse(result['ok']);self.assertEqual(result.get('fault'),want)

    def test_emit_categories_follow_typed_validation_and_host_evidence(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'manifest.json').write_text('{}')
            outcomes=[({'ok':False,'integrity_ok':False},'model'),
                ({'ok':False,'integrity_ok':True,'runtime_current':True,'replay_status':'failed'},'model'),
                ({'ok':False,'error':{'fault':'host'}},'host'),
                ({'ok':False,'fault':'operator'},'operator'),({'ok':False},None)]
            for result,want in outcomes:
                with self.subTest(result=result),patch.object(emulated,'_inputs',return_value=(root,{})), \
                     patch('generative_driver.toolkit.call_tool',return_value=result):
                    checked=emulated.check_stage('tq9-v2','emit',root,root,{'status':'completed'})
                    self.assertFalse(checked['ok']);self.assertEqual(checked.get('fault'),want)

    def test_unreadable_acquisition_is_host_failure_not_candidate_failure(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'provenance.json').write_text('{}');(root/'firmware.bin').write_bytes(b'toy')
            with patch.object(emulated,'_inputs',return_value=(root,{'images':{'firmware.bin':'a'*64}})), \
                 patch.object(Path,'read_bytes',side_effect=OSError('toy filesystem unavailable')):
                result=emulated.check_stage('tq9-v2','acquire',root,root,{'status':'completed'})
                self.assertFalse(result['ok']);self.assertEqual(result.get('fault'),'host')
