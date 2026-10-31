"""Current owner seam with real package execution and scripted TCP silicon."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from test_device_sessions import demonstration_device
from test_driver_contract import emit_fixture, controlled_model
from test_package import example_model
from generative_driver.configurator import Controller, digest, _frozen_package, _fresh_reuse


class OwnerGeneratedTests(unittest.TestCase):
    def test_generic_reuse_serves_generated_catalog_and_records_frozen_execution(self):
        with tempfile.TemporaryDirectory() as temporary, demonstration_device() as binding:
            root = Path(temporary)
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            owner = Controller(root/'owner')
            run_dir = owner.home/'runs/run'; run_dir.mkdir(parents=True)
            workspace = run_dir/'reuse'; workspace.mkdir()
            spec = {'goal': 'Measure temperature', 'binding': binding, 'effects': ['read']}
            accepted = [{'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}]
            prepared = owner._prepare(spec, 'reuse', run_dir, workspace, accepted)
            self.assertIn('device_mcp', prepared)
            assignment = {'id': 'reuse-1', 'stage': 'reuse', 'revision': 0, 'workspace': str(workspace),
                'inputs': {p: digest(p) for p in prepared['inputs']}, 'binding': binding,
                'effects': ['read'], 'allowed_tools': prepared['allowed_tools'], 'device_mcp': prepared['device_mcp']}
            with owner._db() as db:
                db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
                    ('run', json.dumps(spec), 'running', 'reuse', time.time(), time.time()))
                db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                    ('reuse-1','run','reuse','active',json.dumps(assignment),time.time()))
            identity = {'run_id': 'run', 'assignment_id': 'reuse-1'}
            catalog = owner.call('mcp_tools', identity)
            name = next(t['name'] for t in catalog['tools'] if t['name'].endswith('measure'))
            with self.assertRaisesRegex(ValueError, 'MCP'):
                owner.call('tool', {**identity,'name': name,'arguments': {}})
            result = owner.call('mcp_tool', {**identity, 'name': name, 'arguments': {}, 'request_id': 'measure-1'})
            self.assertTrue(result['ok'], result)
            self.assertEqual(result, owner.call('mcp_tool', {**identity, 'name': name, 'arguments': {}, 'request_id': 'measure-1'}))
            with owner._db() as db:
                events = [json.loads(r['payload']) for r in db.execute("SELECT payload FROM events WHERE kind='tool.finished'")]
            frozen = _frozen_package(accepted[0]['artifacts'][0])
            check, diff = _fresh_reuse(frozen, events, grants=['read'])
            self.assertTrue(check['passed'], (check, diff))
            self.assertFalse(_fresh_reuse(frozen, [{**events[0], 'name': 'emit_test_live',
                'result': {k:v for k,v in events[0]['result'].items() if k!='mcp'}}], grants=['read'])[0]['passed'])
            with owner._db() as db:
                db.execute('UPDATE runs SET spec=? WHERE id=?',(json.dumps({**spec,'_case_pin':{'execution':'actual-agent-emulation'}}),'run'))
            with self.assertRaisesRegex(ValueError,'bench harness'):
                owner.call('mcp_tool',{**identity,'name':name,'arguments':{},'request_id':'no-provider'})

    def test_reset_grant_and_open_failure_keep_uncertainty(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            owner = Controller(root/'owner')
            session = owner.call('device_open', {'package_dir': str(package), 'binding': {'host':'127.0.0.1','port':1},
                'effects': ['read','write','reset']})
            from generative_driver.configurator import SafetyError
            with owner._db() as db, self.assertRaises(SafetyError):
                owner._claim_binding({'host':'127.0.0.1','port':1},'another-run',db)
            failure = {'schema':'driver-result/1','operation':'set_level','ok':False,'outputs':{},'warnings':[],
                'transport_open_attempted':True,'requested_command_dispatched':False,
                'error':{'fault':'host','code':'transport_open_failed','message':'opening failed'}}
            with patch.object(owner.device_access, 'invoke_package', return_value=failure):
                owner.call('device_execute', {'session_id': session['session_id'], 'operation':'set_level',
                    'parameters':{'level':5},'request_id':'open-failure'})
            self.assertTrue(owner.call('device_status', {'session_id':session['session_id']})['uncertain_effect'])

    def test_finish_distinguishes_a_preopen_refusal_from_live_guard_reads(self):
        from unittest.mock import patch
        from test_driver_contract import rewrite_document
        with tempfile.TemporaryDirectory() as temporary, demonstration_device({'writes': 0, 'armed': 0}) as binding:
            root = Path(temporary)
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            contract = json.loads((package/'driver/contract.json').read_text())
            contract['operations']['set_level']['preconditions'] = [{
                'id': 'armed_required', 'description': 'Device must be armed.',
                'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
                'operator': 'eq', 'expected': 1}]
            rewrite_document(package, 'driver/contract.json', contract)
            owner = Controller(root/'owner')
            self.addCleanup(owner.close)
            opened = owner.call('device_open', {'package_dir': str(package), 'binding': binding, 'effects': ['write']})
            request = {'session_id': opened['session_id'], 'operation': 'set_level', 'parameters': {'level': 37}}
            preopen = {'schema': 'driver-result/1', 'ok': False, 'transport_open_attempted': False,
                       'requested_command_dispatched': False, 'error': {'code': 'refused', 'fault': 'operator'}}
            with patch.object(owner.device_access, 'invoke_package', return_value=preopen):
                owner.call('device_execute', {**request, 'request_id': 'before-open'})
            guard = owner.call('device_execute', {**request, 'request_id': 'guard-read'})
            self.assertEqual(guard['error']['code'], 'precondition_failed')
            self.assertTrue(guard['transport_open_attempted'])
            self.assertFalse(guard['requested_command_dispatched'])
            with owner._db() as db:
                events = [json.loads(row['payload']) for row in db.execute(
                    "SELECT payload FROM events WHERE kind='tool.finished' ORDER BY seq")]
            self.assertEqual([event['refused_before_open'] for event in events], [True, False])
            self.assertEqual([event['effects_reconciled'] for event in events], [True, True])
            self.assertFalse(owner.call('device_status', {'session_id': opened['session_id']})['uncertain_effect'])

    def test_invocation_cancellation_before_dispatch_is_durable_and_does_not_cancel_session(self):
        with tempfile.TemporaryDirectory() as temporary, demonstration_device() as binding:
            root = Path(temporary)
            model = example_model(); model['channel'] = {'type':'tcp'}
            package, _ = emit_fixture(root, model)
            owner = Controller(root/'owner')
            session = owner.call('device_open', {'package_dir':str(package),'binding':binding})
            identity = {'session_id':session['session_id'],'request_id':'cancel-me'}
            cancelled = owner.call('device_cancel_invocation', identity)
            self.assertEqual(cancelled['error']['code'], 'cancelled')
            result = owner.call('device_execute', {**identity,'operation':'measure','parameters':{}})
            self.assertEqual(result['error']['code'], 'cancelled')
            self.assertFalse(result['requested_command_dispatched'])
            self.assertEqual(owner.call('device_status', identity)['result'], result)
            self.assertTrue(owner.call('device_execute', {**identity,'request_id':'next','operation':'measure'})['ok'])

    def test_generated_profile_cannot_be_equivalent_without_sealed_owner_proof(self):
        from generative_driver.benchmark_support.evaluator import final_record
        from test_evaluation import contract, EXPECTED
        sha = 'a'*64
        definition = contract(sha)
        final = {'contract':definition,'records':[{'id':c['id'],'revision':0,'artifact_sha256':sha,
            'channel':c['channel'],'unit':c['unit'],'value':v} for c,v in zip(definition['checks'],(EXPECTED,370))],
            'frozen_artifact_sha256':sha, 'generated_reuse_required':True}
        verdict = final_record(final, protocol='assistive/1')
        self.assertFalse(verdict['equivalent'])
        self.assertIn('generated_mcp_reuse', [c['id'] for c in verdict['checks']])

    def test_benchmark_journal_records_complete_generated_arguments_and_proof(self):
        from generative_driver.benchmark_support.emulated import record_call
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'benchmark').mkdir()
            (root/'benchmark/state.json').write_text(json.dumps({'package_sha256':'a'*64,'package_attempts':{'reuse':'attempt'}}))
            record_call('tq9-v2',root,'reuse','benchmark_package_execute',
                {'operation':'set_level','parameters':{'level':37},'mcp':{'dispatch_id':'12'}},
                {'ok':True}, {'level':0},{'level':37},{'assignment_id':'reuse-1'})
            row=json.loads((root/'benchmark/package-events.jsonl').read_text())
            self.assertEqual(row['parameters'],{'level':37})
            self.assertEqual(row['mcp'],{'dispatch_id':'12'})

    def test_authored_contract_pair_survives_benchmark_adaptation_and_rejects_projection_mismatch(self):
        from generative_driver.benchmark_support.emulated import adapt
        from interface_runtime.contracts import from_model, compile_contract
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); candidate=root/'candidate';candidate.mkdir()
            contract, protocol=from_model(controlled_model())
            contract['operations']['set_level']['preconditions']=[{'id':'armed','description':'Must be armed',
                'observation':{'operation':'read_armed','parameters':{},'output':'armed'},'operator':'eq','expected':1}]
            model=compile_contract(contract,protocol)
            for name, document in [('model.json',model),('contract.json',contract),('protocol.json',protocol)]:
                (candidate/name).write_text(json.dumps(document))
            accepted=[{'stage':'interpret','artifacts':[{'path':str(p),'sha256':digest(p)} for p in candidate.iterdir()]}]
            ws=root/'probe';ws.mkdir()
            result=adapt(root,ws,accepted,{'device':{'binding':{'host':'localhost','port':42}}})
            self.assertIn('binding',result)
            self.assertEqual(json.loads((ws/'model/contract.json').read_text()),contract)
            adapted_protocol=json.loads((ws/'model/protocol.json').read_text())
            self.assertEqual(adapted_protocol['channel'],{'type':'tcp'})
            self.assertEqual(compile_contract(contract,adapted_protocol),json.loads((ws/'model/model.json').read_text()))
            protocol['operations']['set_level']['steps'][0]['tx'][0]={'hex':'57524f4e47'}
            (candidate/'protocol.json').write_text(json.dumps(protocol))
            accepted[0]['artifacts']=[{'path':str(p),'sha256':digest(p)} for p in candidate.iterdir()]
            mismatch = adapt(root,root/'bad-probe',accepted,{'device':{'binding':{'host':'localhost','port':42}}})
            self.assertIn('unwired_model', mismatch)
            self.assertIn('projection', mismatch['unwired_model']['reason'])

    def test_device_rpc_waits_for_long_operation_and_never_resends_lost_reply(self):
        from generative_driver import client
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            home=Path(temporary)
            (home/'service.json').write_text(json.dumps({'address':'fixture','family':'AF_PIPE','authkey':'aa','code':client.code_identity()}))
            for method in ('device_execute','mcp_tool'):
                sent=[];timeouts=[]
                class Connection:
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def send_bytes(self,data):sent.append(data)
                    def poll(self,timeout):timeouts.append(timeout);return True
                    def recv_bytes(self,limit):raise EOFError('lost response')
                with patch.object(client,'Client',return_value=Connection()),patch.object(client,'_start',side_effect=AssertionError('must not resend')):
                    result=client.call(method,{'request_id':'once'},home)
                self.assertFalse(result['ok'])
                self.assertEqual(len(sent),1)
                self.assertEqual(timeouts,[620])

    def test_recovery_accepts_only_owner_proven_generated_completed_model_failure(self):
        from generative_driver.device_recovery import unresolved_outcomes
        with tempfile.TemporaryDirectory() as temporary:
            owner=Controller(Path(temporary)/'owner')
            configured={'manifest_sha256':'a'*64,'catalog_sha256':'b'*64,'contract_sha256':'c'*64,'package_dir':'package'}
            assignment={'id':'reuse','stage':'reuse','device_mcp':configured,'inputs':{'package':'d'*64}}
            start={'stage':'reuse','assignment_id':'reuse','actor':'worker','name':'device_write','effectful':True,
                'connected':True,'argument_sha256':{'package_dir':'d'*64}}
            with owner._db() as db:
                dispatch=owner._event('run','tool.started',start,db)
                db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)',(dispatch,'run','reuse','worker'))
                proof={'schema':'driver-mcp-call/1','tool_name':'device_write','session_id':'run','assignment_id':'reuse',
                    'dispatch_id':str(dispatch),'package_sha256':'d'*64,**{k:v for k,v in configured.items() if k!='package_dir'}}
                result={'schema':'driver-result/1','ok':False,'identity_verified':True,'transport_open_attempted':True,
                    'requested_command_dispatched':True,'transcript':[{'op':'exchange'}],'mcp':proof,
                    'error':{'fault':'model','code':'response_mismatch'}}
                finish={**start,'result':result,'ok':False,'dispatch_id':str(dispatch),'effects_reconciled':False}
                identifier=owner._event('run','tool.finished',finish,db)
                self.assertEqual(unresolved_outcomes(db,'run',assignment)[0],'model')
                for changes in ({'error':{'fault':'host'}},{'requested_command_dispatched':False},
                                {'mcp':{**proof,'manifest_sha256':'f'*64}}):
                    db.execute('UPDATE events SET payload=? WHERE seq=?',(json.dumps({**finish,'result':{**result,**changes}}),identifier))
                    self.assertNotEqual(unresolved_outcomes(db,'run',assignment)[0],'model')


class ResetOpeningOwnershipTests(unittest.TestCase):
    @staticmethod
    def package(root, resets):
        from generative_driver.toolkit import call_tool
        from test_package import example_replay
        model = example_model()
        model['channel'] = {'type': 'uart', 'baudrate': 115200, 'resets_on_open': resets}
        source = root/'model'; source.mkdir()
        (source/'model.json').write_text(json.dumps(model))
        replay = root/'replay.json'; replay.write_text(json.dumps(example_replay()))
        probe = call_tool('probe_run', {'run_dir': str(root/'tools'), 'model_dir': str(source),
            'operation': 'measure', 'n': 1, 'replay': str(replay), 'allow_effects': ['reset']})
        if not probe.get('ok'):
            raise AssertionError(probe)
        emitted = call_tool('emit_package', {'run_dir': str(root/'tools'), 'model_dir': str(source), 'probe': probe['probe']})
        if not emitted.get('ok'):
            raise AssertionError(emitted)
        return Path(emitted['package_dir'])

    @staticmethod
    def failed_open():
        return {'schema': 'driver-result/1', 'operation': 'measure', 'ok': False, 'outputs': {}, 'warnings': [],
            'transport_open_attempted': True, 'requested_command_dispatched': False,
            'error': {'fault': 'host', 'code': 'transport_open_failed', 'message': 'Scripted opener failed after contact'}}

    def test_resetting_session_read_keeps_failed_open_uncertain_and_retains_binding(self):
        from unittest.mock import patch
        from generative_driver.configurator import SafetyError
        for resets in (True, False):
            with self.subTest(resets=resets), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                package = self.package(root, resets)
                owner = Controller(root/'owner')
                binding = {'port': str(root/'scripted-port')}
                session = owner.call('device_open', {'package_dir': str(package), 'binding': binding, 'effects': ['read','reset']})
                request = {'session_id': session['session_id'], 'operation': 'measure', 'request_id': 'opening'}
                started_pending = []
                def opening(*args):
                    with owner._db() as db:
                        started_pending.append(db.execute('SELECT COUNT(*) FROM pending_effects').fetchone()[0])
                    return self.failed_open()
                with patch.object(owner.device_access, 'invoke_package', side_effect=opening):
                    self.assertFalse(owner.call('device_execute', request)['ok'])
                    self.assertEqual(owner.call('device_status', {'session_id': session['session_id']})['uncertain_effect'], resets)
                    self.assertEqual(started_pending, [int(resets)])
                    if resets:
                        with self.assertRaisesRegex(ValueError, 'uncertain'):
                            owner.call('device_execute', {**request, 'request_id': 'another-opening'})
                        with self.assertRaisesRegex(ValueError, 'uncertain'):
                            owner.call('device_close', {'session_id': session['session_id']})
                        with self.assertRaises(SafetyError):
                            owner.call('device_open', {'package_dir': str(package), 'binding': binding, 'effects': ['read','reset']})
                        owner.call('device_reconcile', {'session_id': session['session_id'], 'effect_resolution': 'confirmed_safe',
                            'observation': {'source': 'scripted fixture', 'message': 'Execution stopped; device inspected',
                                            'prior_execution_stopped': True}})
                    self.assertEqual(owner.call('device_close', {'session_id': session['session_id']})['status'], 'closed')

    def test_resetting_assigned_read_marks_pending_before_open_and_blocks_another_open(self):
        from unittest.mock import patch
        for resets in (True, False):
            with self.subTest(resets=resets), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                package = self.package(root, resets)
                owner = Controller(root/'owner')
                run_dir = owner.home/'runs/run'; workspace = run_dir/'reuse'; workspace.mkdir(parents=True)
                spec = {'goal': 'Read temperature', 'binding': {'port': str(root/'scripted-port')}, 'effects': ['read','reset']}
                prepared = owner._prepare(spec, 'reuse', run_dir, workspace,
                    [{'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}])
                assignment = {'id': 'reuse-1', 'stage': 'reuse', 'revision': 0, 'workspace': str(workspace),
                    'inputs': {p: digest(p) for p in prepared['inputs']}, 'binding': spec['binding'],
                    'effects': spec['effects'], 'allowed_tools': prepared['allowed_tools'], 'device_mcp': prepared['device_mcp']}
                with owner._db() as db:
                    db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
                        ('run', json.dumps(spec), 'running', 'reuse', time.time(), time.time()))
                    db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                        ('reuse-1', 'run', 'reuse', 'active', json.dumps(assignment), time.time()))
                identity = {'run_id': 'run', 'assignment_id': 'reuse-1'}
                name = next(t['name'] for t in owner.call('mcp_tools', identity)['tools'] if t['name'].endswith('measure'))
                request = {**identity, 'name': name, 'arguments': {}, 'request_id': 'opening'}
                started_pending = []
                def opening(*args):
                    with owner._db() as db:
                        started_pending.append(db.execute('SELECT COUNT(*) FROM pending_effects').fetchone()[0])
                    return self.failed_open()
                with patch.object(owner.device_access, 'invoke_package', side_effect=opening):
                    self.assertFalse(owner.call('mcp_tool', request)['ok'])
                    self.assertEqual(owner.call('status', {'run_id': 'run'})['uncertain_effect'], resets)
                    self.assertEqual(started_pending, [int(resets)])
                    if resets:
                        with self.assertRaisesRegex(ValueError, 'uncertain'):
                            owner.call('mcp_tool', {**request, 'request_id': 'another-opening'})
                    else:
                        self.assertFalse(owner.call('mcp_tool', {**request, 'request_id': 'another-opening'})['ok'])
                        self.assertEqual(started_pending, [0, 0])
