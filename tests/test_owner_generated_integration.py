"""Current owner seam with real package execution and scripted TCP silicon."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from test_device_sessions import demonstration_device, recorded
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
            # The stored run stands in for a supplied run, by its device, firmware and mission: fresh reuse reaches
            # only the device the run recorded, never the operator's binding.
            supplied = {'device': {'schema': 'device-descriptor/2'}, 'firmware': {}, 'mission': {}}
            progress = {'revision': 0, 'next_stage': 'reuse', 'repairs': 0, 'feedback': None, 'protocol': 'assistive/1',
                        'device': {'session_id': 'supplied-session', 'binding': {'host': '127.0.0.1', 'port': 1}}}
            with owner._db() as db:
                db.execute('UPDATE runs SET spec=? WHERE id=?',(json.dumps({**spec,**supplied}),'run'))
                db.execute('INSERT INTO progress VALUES(?,?)',('run',json.dumps(progress)))
            with self.assertRaisesRegex(ValueError,'Assigned binding differs from the supplied device'):
                owner.call('mcp_tool',{**identity,'name':name,'arguments':{},'request_id':'no-provider'})

    def test_01_B7_a_session_write_whose_open_failed_before_its_command_was_sent_leaves_no_uncertain_effect(self):
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
            # The runtime says the requested command was never sent, so nothing is left to resolve.
            self.assertFalse(owner.call('device_status', {'session_id':session['session_id']})['uncertain_effect'])
            [finished] = recorded(owner.home, session['session_id'], 'tool.finished')
            self.assertEqual((finished['effectful'], finished['effects_reconciled'], finished['refused_before_open']),
                             (True, True, False), finished)
            # A record that does not say whether the command was sent stays uncertain.
            unsaid = {key: value for key, value in failure.items() if key != 'requested_command_dispatched'}
            with patch.object(owner.device_access, 'invoke_package', return_value=unsaid):
                owner.call('device_execute', {'session_id': session['session_id'], 'operation':'set_level',
                    'parameters':{'level':5},'request_id':'unsaid'})
            self.assertTrue(owner.call('device_status', {'session_id':session['session_id']})['uncertain_effect'])

    def test_finish_distinguishes_a_preopen_refusal_from_live_guard_reads(self):
        from unittest.mock import patch
        from test_driver_contract import guarded_model
        with tempfile.TemporaryDirectory() as temporary, demonstration_device({'writes': 0, 'armed': 0}) as binding:
            root = Path(temporary)
            model = guarded_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
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

    def test_benchmark_journal_records_complete_generated_arguments_and_proof(self):
        from generative_driver.supplied.stages import record_call
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'supplied').mkdir()
            (root/'supplied/state.json').write_text(json.dumps({'package_sha256':'a'*64,'package_attempts':{'reuse':'attempt'}}))
            record_call(root,'reuse','package_execute',
                {'operation':'set_level','parameters':{'level':37},'mcp':{'dispatch_id':'12'}},
                {'ok':True}, {'level':0},{'level':37},{'assignment_id':'reuse-1'})
            row=json.loads((root/'supplied/package-events.jsonl').read_text())
            self.assertEqual(row['parameters'],{'level':37})
            self.assertEqual(row['mcp'],{'dispatch_id':'12'})

    def test_02_B13_a_run_on_a_supplied_device_keeps_the_meanings_and_reads_no_handed_contract_or_protocol(self):
        # The model wired to the supplied device differs from the interpreted one only in its channel, whatever else
        # the interpret handoff carries.
        from generative_driver.supplied.stages import adapt
        from generative_driver.toolkit import call_tool
        from test_driver_contract import GUARD, RANGE, described_model, exchange, replay_of
        interpreted = described_model()
        self.assertEqual(interpreted['channel']['type'], 'uart')
        beside = {'the model alone': {},
                  'a contract and a protocol of another driver': {
                      'contract.json': json.dumps({'schema': 'driver-contract/1', 'operations': {}}),
                      'protocol.json': json.dumps({'schema': 'driver-protocol/1', 'operations': {}})},
                  'a lone contract that is no JSON': {'contract.json': 'not JSON'},
                  'a lone protocol of an unknown format': {'protocol.json': '{"schema": "driver-protocol/9"}'}}
        with tempfile.TemporaryDirectory() as temporary:
            for index, (case, extra) in enumerate(beside.items()):
                with self.subTest(handed=case):
                    root = Path(temporary) / str(index)
                    candidate = root / 'candidate'; candidate.mkdir(parents=True)
                    (candidate / 'model.json').write_text(json.dumps(interpreted))
                    for name, text in extra.items():
                        (candidate / name).write_text(text)
                    accepted = [{'stage': 'interpret', 'artifacts': [{'path': str(p), 'sha256': digest(p)}
                                                                     for p in sorted(candidate.iterdir())]}]
                    ws = root / 'probe'; ws.mkdir()
                    result = adapt(root, ws, accepted, {'device': {'binding': {'host': 'localhost', 'port': 42}}})
                    # Neither file stops the probe, and neither is handed on.
                    self.assertEqual(result, {'binding': {'host': 'localhost', 'port': 42}})
                    self.assertEqual(sorted(path.name for path in (ws / 'model').iterdir()),
                                     ['adaptation.json', 'model.json'])
                    wired = json.loads((ws / 'model/model.json').read_text())
                    self.assertEqual(wired, {**interpreted, 'channel': {'type': 'tcp'}})
                    # So the package's tools show the meanings the worker wrote.
                    (root / 'replay.json').write_text(json.dumps(replay_of(exchange(b'T\n', b'T:21.5\n'))))
                    run = {'run_dir': str(root / 'run'), 'model_dir': str(ws / 'model')}
                    probe = call_tool('probe_run', {**run, 'operation': 'measure', 'n': 1,
                                                    'replay': str(root / 'replay.json')})
                    emitted = call_tool('emit_package', {**run, 'probe': probe['probe']})
                    self.assertTrue(emitted.get('ok'), emitted)
                    catalog = json.loads((Path(emitted['package_dir']) / 'mcp.json').read_text())
                    tools = {tool['name']: tool['description'].split('\n') for tool in catalog['tools']}
                    self.assertEqual(tools['fictional_demonstration_measure'],
                                     ['Read the temperature of the probe.', 'Warning: ' + RANGE['message']])
                    self.assertEqual(tools['fictional_demonstration_set_level'],
                                     ['Set the output level.', 'Precondition armed_required: ' + GUARD['description']])

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
                with patch.object(client,'open_connection',return_value=Connection()),patch.object(client,'_start',side_effect=AssertionError('must not resend')):
                    result=client.call(method,{'request_id':'once'},home)
                self.assertFalse(result['ok'])
                self.assertEqual(len(sent),1)
                self.assertEqual(len(timeouts),1)
                self.assertTrue(600 < timeouts[0] <= 620, timeouts)

    def test_recovery_accepts_only_owner_proven_generated_completed_model_failure(self):
        from generative_driver.device_recovery import unresolved_outcomes
        with tempfile.TemporaryDirectory() as temporary:
            owner=Controller(Path(temporary)/'owner')
            configured={'manifest_sha256':'a'*64,'catalog_sha256':'b'*64,'package_dir':'package'}
            assignment={'id':'reuse','stage':'reuse','device_mcp':configured,'inputs':{'package':'d'*64}}
            start={'stage':'reuse','assignment_id':'reuse','actor':'worker','name':'samp1_write','effectful':True,
                'connected':True,'argument_sha256':{'package_dir':'d'*64}}
            with owner._db() as db:
                # The run and its assignment as their rows hold them, so that the record starts the dispatch.
                db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
                    ('run','{}','running','reuse',time.time(),time.time()))
                db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                    ('reuse','run','reuse','active',json.dumps(assignment),time.time()))
            dispatch=owner.dispatch_record.begin('run',kind='run',name='samp1_write',actor='worker',effects=['write'],
                stage='reuse',assignment_id='reuse',facts={key:start[key] for key in ('connected','argument_sha256')})
            with owner._db() as db:
                proof={'schema':'driver-mcp-call/1','tool_name':'samp1_write','session_id':'run',
                    'assignment_id':'reuse','dispatch_id':str(dispatch),'package_sha256':'d'*64,
                    **{k:v for k,v in configured.items() if k!='package_dir'}}
                result={'schema':'driver-result/1','ok':False,'identity_verified':True,'transport_open_attempted':True,
                    'requested_command_dispatched':True,'transcript':[{'op':'exchange'}],'mcp':proof,
                    'error':{'fault':'model','code':'response_mismatch'}}
                finish={**start,'result':result,'ok':False,'dispatch_id':str(dispatch),'effects_reconciled':False}
                identifier=owner._event('run','tool.finished',finish,db)
                self.assertEqual(unresolved_outcomes(owner.dispatch_record,db,'run',assignment)[0],'model')
                for changes in ({'error':{'fault':'host'}},{'requested_command_dispatched':False},
                                {'mcp':{**proof,'manifest_sha256':'f'*64}}):
                    db.execute('UPDATE events SET payload=? WHERE seq=?',(json.dumps({**finish,'result':{**result,**changes}}),identifier))
                    self.assertNotEqual(unresolved_outcomes(owner.dispatch_record,db,'run',assignment)[0],'model')

    def test_01_B4_the_recovery_takes_the_faults_from_the_reading_and_holds_no_copy_of_their_rule(self):
        import inspect
        from generative_driver import device_recovery
        for function in (device_recovery.diagnostic_faults, device_recovery.unresolved_outcomes):
            with self.subTest(function.__name__):
                source = inspect.getsource(function)
                for field in ('transport_open_attempted', 'identity_verified', 'requested_command_dispatched',
                              "'fault'", "'results'"):
                    self.assertNotIn(field, source)
                self.assertIn('dispatch_outcome', inspect.getsource(device_recovery))
        self.assertIn('.other_faults', inspect.getsource(device_recovery.diagnostic_faults))
        unresolved = inspect.getsource(device_recovery.unresolved_outcomes)
        self.assertTrue('.pending_faults' in unresolved and '.model_failures' in unresolved)


class ResetOpeningOwnershipTests(unittest.TestCase):
    @staticmethod
    def package(root, resets, model=None):
        from generative_driver.toolkit import call_tool
        from test_package import example_replay
        model = model or example_model()
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

    def assigned(self, owner, root, package, effects):
        """A fresh-reuse assignment of a stored run over the generated tools of `package`, on a serial binding nothing
        listens at: what its worker's gateway calls carry, and the name of each operation's tool."""
        run_dir = owner.home/'runs/run'; workspace = run_dir/'reuse'; workspace.mkdir(parents=True)
        spec = {'goal': 'Read temperature', 'binding': {'port': str(root/'scripted-port')}, 'effects': effects}
        prepared = owner._prepare(spec, 'reuse', run_dir, workspace,
            [{'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}])
        assignment = {'id': 'reuse-1', 'stage': 'reuse', 'revision': 0, 'workspace': str(workspace),
            'inputs': {p: digest(p) for p in prepared['inputs']}, 'binding': spec['binding'],
            'effects': effects, 'allowed_tools': prepared['allowed_tools'], 'device_mcp': prepared['device_mcp']}
        with owner._db() as db:
            db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
                ('run', json.dumps(spec), 'running', 'reuse', time.time(), time.time()))
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                ('reuse-1', 'run', 'reuse', 'active', json.dumps(assignment), time.time()))
        identity = {'run_id': 'run', 'assignment_id': 'reuse-1'}
        tools = [tool['name'] for tool in owner.call('mcp_tools', identity)['tools']]
        return identity, {name.removeprefix('fictional_demonstration_'): name for name in tools}

    def test_01_B6_a_session_read_over_a_channel_that_resets_on_open_needs_the_reset_grant_and_marks_nothing(self):
        from unittest.mock import patch
        for resets in (True, False):
            with self.subTest(resets=resets), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                package = self.package(root, resets)
                owner = Controller(root/'owner')
                binding = {'port': str(root/'scripted-port')}
                opening = {'package_dir': str(package), 'binding': binding}
                opened = owner.call('device_open', {**opening, 'effects': ['read','reset']})
                session = {'session_id': opened['session_id']}
                request = {**session, 'operation': 'measure', 'request_id': 'opening'}
                pending = []
                def failing_open(*args):
                    pending.append(owner.dispatch_record.pending(session['session_id']))
                    return self.failed_open()
                with patch.object(owner.device_access, 'invoke_package', side_effect=failing_open):
                    self.assertFalse(owner.call('device_execute', request)['ok'])
                    # The read marked nothing, although opening its channel may have reset the device.
                    self.assertIs(owner.call('device_status', session)['uncertain_effect'], False)
                    # So another read goes on,
                    self.assertFalse(owner.call('device_execute', {**request, 'request_id': 'another-opening'})['ok'])
                    self.assertEqual(pending, [[], []])
                    self.assertEqual([(event['name'], event['effectful'])
                                      for event in recorded(owner.home, session['session_id'], 'tool.started')],
                                     [('measure', False), ('measure', False)])
                    # and the session is closed with no statement.
                    self.assertEqual(owner.call('device_close', session)['status'], 'closed')
                    # Opening such a channel still needs the reset grant: without it nothing starts.
                    ungranted = owner.call('device_open', {**opening, 'effects': ['read']})['session_id']
                    read = {'session_id': ungranted, 'operation': 'measure', 'request_id': 'ungranted'}
                    if resets:
                        with self.assertRaisesRegex(ValueError, 'requires an effect grant'):
                            owner.call('device_execute', read)
                    else:
                        self.assertFalse(owner.call('device_execute', read)['ok'])
                    self.assertEqual(len(recorded(owner.home, ungranted, 'tool.started')), int(not resets))

    def test_01_B6_an_assigned_read_over_a_channel_that_resets_on_open_marks_nothing_and_another_read_goes_on(self):
        from unittest.mock import patch
        for resets in (True, False):
            with self.subTest(resets=resets), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                owner = Controller(root/'owner')
                identity, tools = self.assigned(owner, root, self.package(root, resets), ['read','reset'])
                request = {**identity, 'name': tools['measure'], 'arguments': {}, 'request_id': 'opening'}
                pending = []
                def failing_open(*args):
                    pending.append(owner.dispatch_record.pending('run'))
                    return self.failed_open()
                with patch.object(owner.device_access, 'invoke_package', side_effect=failing_open):
                    self.assertFalse(owner.call('mcp_tool', request)['ok'])
                    self.assertIs(owner.call('status', {'run_id': 'run'})['uncertain_effect'], False)
                    self.assertFalse(owner.call('mcp_tool', {**request, 'request_id': 'another-opening'})['ok'])
                    self.assertEqual(pending, [[], []])
                    self.assertEqual([(event['name'], event['effectful'])
                                      for event in recorded(owner.home, 'run', 'tool.started')],
                                     [(tools['measure'], False), (tools['measure'], False)])

    def test_01_B6_the_reply_to_an_abandoned_generated_call_says_an_effect_is_uncertain_only_for_a_pending_effect(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            # Built as the service builds it: the service answers a worker whose call raised with the abandoned reply.
            owner = Controller(root/'owner', for_service=True)
            self.addCleanup(owner.close)
            identity, tools = self.assigned(owner, root, self.package(root, True, controlled_model()),
                                            ['read', 'write', 'reset'])
            with patch.object(owner.device_access, 'invoke_package', side_effect=RuntimeError('scripted')):
                read = owner.call('mcp_tool', {**identity, 'name': tools['measure'], 'arguments': {},
                                               'request_id': 'read'})
                self.assertIs(owner.call('status', {'run_id': 'run'})['uncertain_effect'], False)
                write = owner.call('mcp_tool', {**identity, 'name': tools['set_level'], 'arguments': {'level': 5},
                                                'request_id': 'write'})
            raised = ' did not finish: it raised RuntimeError: scripted'
            # The read ran over a channel that resets on open, and still left no pending effect: its reply holds no
            # `uncertain_effect`. The write's does.
            self.assertEqual(read, {'ok': False, 'abandoned': 'raised', 'reason': tools['measure'] + raised})
            self.assertEqual(write, {'ok': False, 'abandoned': 'raised', 'reason': tools['set_level'] + raised,
                                     'uncertain_effect': True})
            self.assertEqual([event['name'] for event in recorded(owner.home, 'run', 'tool.abandoned')],
                             [tools['measure'], tools['set_level']])
            self.assertEqual(recorded(owner.home, 'run', 'tool.finished'), [])
            self.assertIs(owner.call('status', {'run_id': 'run'})['uncertain_effect'], True)

    def test_01_B9_a_generated_write_that_succeeded_while_its_run_was_cancelled_leaves_no_uncertain_effect(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            owner = Controller(root/'owner')
            identity, tools = self.assigned(owner, root, self.package(root, False, controlled_model()),
                                            ['read', 'write', 'reset'])
            def cancelled_meanwhile(*args):
                # The operator cancels the run while the package's write runs; the write itself succeeds.
                self.assertEqual(owner.call('cancel', {'run_id': 'run'})['status'], 'cancelled')
                return {'schema': 'driver-result/1', 'operation': 'set_level', 'ok': True, 'outputs': {},
                        'warnings': [], 'identity_verified': True, 'requested_command_dispatched': True}
            with patch.object(owner.device_access, 'invoke_package', side_effect=cancelled_meanwhile):
                written = owner.call('mcp_tool', {**identity, 'name': tools['set_level'], 'arguments': {'level': 5},
                                                  'request_id': 'write'})
            self.assertTrue(written['ok'], written)
            [finished] = recorded(owner.home, 'run', 'tool.finished')
            self.assertEqual((finished['effectful'], finished['ok'], finished['effects_reconciled']),
                             (True, True, True), finished)
            status = owner.call('status', {'run_id': 'run'})
            self.assertEqual((status['status'], status['uncertain_effect']), ('cancelled', False), status)
