"""Public workflow checks; synthetic workers are contract fixtures, not model trials."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from generative_driver.benchmark import prepare_stage, observe_stage

GRADES = {'passed', 'expected', 'checks', 'verdict', 'grade', 'absolute_tolerance', 'evaluator'}


def grade_keys(value):
    """Keys anywhere in a JSON value that would carry a grade or an answer."""
    if isinstance(value, dict):
        return {key for key in value if key in GRADES} | {k for item in value.values() for k in grade_keys(item)}
    return {k for item in value for k in grade_keys(item)} if isinstance(value, list) else set()


class TQ9V2Tests(unittest.TestCase):
    def test_new_acquisition_contains_only_supplied_binary_evidence(self):
        # Catches adapter absence and leaking evaluator options into assignments.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = prepare_stage('tq9-v2', 'acquire', root/'run', root/'worker',
                options={'scenario_id': 'original', 'case_seed': 0,
                         'evaluator_password_file': '/private/never-copy'})
            self.assertEqual(len(prepared['inputs']), 1)
            binary = Path(prepared['inputs'][0])
            self.assertEqual(binary.suffix, '.bin')
            self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(), prepared['context']['expected_sha256'])
            projected = json.dumps(prepared)
            self.assertNotIn('/private/never-copy', projected)
            self.assertNotIn('scenario_id', projected)
            self.assertEqual(prepared['context']['origin'], 'provided_binary')

    def test_acquisition_hands_on_only_imported_matching_bytes_and_notes_the_rest(self):
        # Catches accepting a success claim or stale hash without imported bytes, or stopping on worker output.
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ws = root/'worker'
            prepared = prepare_stage('tq9-v2', 'acquire', root/'run', ws)
            def observed():
                checked = observe_stage('tq9-v2', 'acquire', root/'run', ws, {'status': 'completed'})
                self.assertNotIn('stop', checked); self.assertNotIn('ok', checked)
                check = next(c for c in checked['checks'] if c['name'] == 'imported_firmware_hash')
                self.assertEqual(checked['notes']['checks'], checked['checks'])
                return checked['artifacts'], check
            self.assertEqual(observed(), ([], {'name': 'imported_firmware_hash', 'passed': False,
                                               'reason': 'No matching imported firmware'}))
            imported = call_tool('acquire_firmware_artifact', {'run_dir': str(ws/'tools'), **prepared['context']})
            self.assertTrue(imported.get('available'), imported)
            artifacts, check = observed()
            self.assertTrue(check['passed'])
            self.assertEqual({Path(p).resolve() for p in artifacts},
                             {Path(imported['artifact']).resolve(), Path(imported['provenance']).resolve()})
            Path(imported['artifact']).write_bytes(b'wrong firmware')
            self.assertEqual(observed()[0], [])

    def test_interpretation_seals_inputs_and_stops_for_integrity_when_they_change(self):
        # Catches unsealed interpretation or accepting edited original evidence.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = prepare_stage('tq9-v2', 'interpret', root/'run', root/'worker')
            ws = Path(prepared['work_dir'])
            self.addCleanup(__import__('shutil').rmtree, ws.parent, True)
            self.assertFalse(prepared['report_required'])
            self.assertEqual(prepared['allowed_tools'], [])
            self.assertTrue((ws/'image.bin').is_file())
            (ws/'image.bin').write_bytes(b'tampered')
            checked = observe_stage('tq9-v2', 'interpret', root/'run', ws, {})
            self.assertEqual(checked['stop']['category'], 'integrity')
            self.assertNotIn('artifacts', checked)

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
            'scenarios': {'original': {'temperature_vectors': [
                {'stimulus': -7, 'expected': -7, 'absolute_tolerance': .1},
                {'stimulus': 29, 'expected': 29, 'absolute_tolerance': .1}]}},
            'effect_vectors': [{'task': 'arm', 'inputs': {}},
                               {'task': 'set_duty', 'inputs': {'duty': .4}},
                               {'task': 'disarm', 'inputs': {}}]}}
        definition = truth['contracts']['scenarios']['original']
        definition['phases'] = {'final':dict(definition)}
        pin = {'scenario_id': 'original', 'case_seed': 0, 'revision': 0}
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
            'scenarios': {'original': {**phases['final'], 'phases': phases}}, 'effect_vectors': phases['final']['effect_vectors']}}
        plans = [tq9_v2.build_plan({'scenario_id': 'original'}, truth, phase) for phase in ('diagnostic', 'final')]
        values = [{a['values']['temperature'] for a in plan if a['kind'] == 'reset'} for plan in plans]
        targets = [{a['inputs']['duty'] for a in plan if a.get('task') == 'set_duty'} for plan in plans]
        for inventory in (*values, *targets): self.assertGreaterEqual(len(inventory), 3)
        self.assertFalse(values[0] & values[1]); self.assertFalse(targets[0] & targets[1])
        contract = tq9_v2.contract({'scenario_id': 'original'}, truth, 'diagnostic')
        records = [{'id': c['id'], 'revision': 0, 'artifact_sha256': 'a'*64, 'unit': c['unit'],
            'channel': c['channel'], 'value': 31 if c['id'].endswith('/temperature') else c['expected']} for c in contract['checks']]
        self.assertEqual(validate_records(contract, records)['verdict'], 'failed')

    def test_tq9_monitor_measurement_uses_permille(self):
        # Catches fraction/permille confusion at the actual family boundary.
        from generative_driver.benchmark_support.tq9_v2 import observations
        measured = observations({'values': {'compare': 400, 'reload': 999}}, {'outputs': {}}, {})
        self.assertEqual(measured['duty'], 400)
        self.assertEqual(observations.monitor_units['duty'], 'permille')

    def test_probe_without_an_accepted_model_skips_before_starting_native_process(self):
        # Catches probing an unaccepted model, starting an emulator for nothing, or accepting changed bytes.
        from unittest.mock import patch
        from generative_driver.configurator import IntegrityError
        from tq9_workflow_fixture import model
        unwired = model(); unwired['channel'] = {'type': 'usb'}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            def handoff(name, raw):
                path = root/'run/accepted'/name/'0-model.json'; path.parent.mkdir(parents=True); path.write_text(raw)
                return [{'stage': 'interpret', 'artifacts': [{'path': str(path), 'sha256': hashlib.sha256(raw.encode()).hexdigest()}]}]
            cases = [([], 'no interpreted model available'),
                     ([{'stage': 'interpret', 'artifacts': []}], 'no interpreted model available'),
                     (handoff('unfinished', '{unfinished'), 'cannot be wired to the emulator'),
                     (handoff('unwired', json.dumps(unwired)), 'cannot be wired to the emulator')]
            with patch('generative_driver.benchmark_support.emulated._session', side_effect=AssertionError('emulator started')):
                for accepted, reason in cases:
                    with self.subTest(reason=reason, accepted=accepted):
                        prepared = prepare_stage('tq9-v2', 'probe', root/'run', root/'worker', accepted)
                        self.assertIn(reason, prepared['skip'])
                        if not accepted or not accepted[0]['artifacts']:
                            self.assertNotIn('unwired_model', prepared)
                            continue
                        # A model the emulator cannot carry is named for the configurator's own validation, and the
                        # candidate is kept for the repair that validation drives.
                        artifact = accepted[0]['artifacts'][0]
                        self.assertEqual({k: prepared['unwired_model'][k] for k in ('path', 'sha256')},
                                         {'path': artifact['path'], 'sha256': artifact['sha256']})
                        self.assertIn('cannot carry', prepared['unwired_model']['reason'])
                        entry = json.loads((root/'run/benchmark/state.json').read_text())['candidate_history'][-1]
                        self.assertEqual(entry['hashes']['model.json'], artifact['sha256'])
                        self.assertEqual((Path(entry['root'])/'model.json').read_bytes(), Path(artifact['path']).read_bytes())
                changed = handoff('changed', json.dumps(model()))
                changed[0]['artifacts'][0]['sha256'] = 'f'*64
                with self.assertRaisesRegex(IntegrityError, 'changed'):
                    prepare_stage('tq9-v2', 'probe', root/'run', root/'worker', changed)

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
            'scenarios': {'original': {'temperature_vectors': [{'stimulus': 20, 'expected': 20, 'absolute_tolerance': .1}]}},
            'effect_vectors': [{'task': 'arm', 'inputs': {}}, {'task': 'set_duty', 'inputs': {'duty': 400}}, {'task': 'disarm', 'inputs': {}}]}}
        definition = truth['contracts']['scenarios']['original']
        definition['phases'] = {'final':dict(definition)}
        plan = tq9_v2.build_plan({'scenario_id': 'original'}, truth, 'final')
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

    def test_original_scenario_can_be_pinned_without_evaluator_password(self):
        # Catches omitting the unchanged-firmware environmental control from discovery.
        from generative_driver.benchmark_support.registry import pin_case
        pin = pin_case('tq9-v2', 'original', 0)
        self.assertEqual(pin['scenario_id'], 'original')
        self.assertNotIn('evaluator_password_file', pin)


    def test_passed_manifest_label_cannot_bypass_calibration_admission(self):
        # Catches trusting a mutable public 'passed' label without authenticated evidence.
        from generative_driver.benchmark_support.registry import require_calibration, pin_case
        pin = pin_case('tq9-v2','original',0)
        pin['calibration'] = {'status':'passed','evidence_sha256':'a'*64}
        with self.assertRaises(ValueError):
            require_calibration(pin, {'evaluator_password_file':'/does/not/exist'})

    def test_case_seed_reorders_only_independent_final_episodes(self):
        # Catches pinned seeds being ignored or dropping final coverage during variation.
        from generative_driver.benchmark_support import tq9_v2
        truth = {'artifact_sha256':'a'*64,'contracts':{'units':{'temperature':'degC','duty':'permille'},
            'scenarios':{'original':{'temperature_vectors':[
                {'stimulus':-7,'expected':-7,'absolute_tolerance':0},
                {'stimulus':29,'expected':29,'absolute_tolerance':0}]}},
            'effect_vectors':[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':400}},{'task':'disarm','inputs':{}}]}}
        definition = truth['contracts']['scenarios']['original']
        definition['phases'] = {'final':dict(definition)}
        a=tq9_v2.build_plan({'scenario_id':'original','case_seed':0},truth,'final')
        b=tq9_v2.build_plan({'scenario_id':'original','case_seed':1},truth,'final')
        self.assertNotEqual([x['values'] for x in a if x['kind']=='reset'], [x['values'] for x in b if x['kind']=='reset'])
        self.assertEqual(sorted(i for x in a if x['kind']=='observe' for i in x['checks']),sorted(i for x in b if x['kind']=='observe' for i in x['checks']))
        self.assertEqual([x['task'] for x in a if x['kind']=='call' and x['task']!='temperature'],[x['task'] for x in b if x['kind']=='call' and x['task']!='temperature'])

    def test_scripted_controller_observes_stages_without_the_evaluator_and_awaits_independent_grading(self):
        self._scripted_controller_modes(('normal','rejected-write','repair-mapping','repair-decoder','repair-ground',
            'final-bounds','hidden-wrong','missing-mission','monitor-missing','snapshot-next'))

    def test_a_provider_lost_before_dispatch_stops_for_the_operator_with_nothing_uncertain(self):
        # Catches a device the configurator can no longer reach turning a refused call into an uncertain write, a
        # host failure, or a call that reaches the device without its instrument readings and window.
        self._scripted_controller_modes(('device-lost',))

    def test_live_calls_on_a_provided_device_take_turns_inside_its_window(self):
        # Catches a live call opening or closing the device's window while another call is inside it (the device
        # paused under a call, readings that mix two calls), and a run that lets go of the device while a cancelled
        # worker's call is still inside its window.
        self._scripted_controller_modes(('concurrent-calls','cancel-in-call'))

    def test_an_interpreted_model_the_emulator_cannot_carry_drives_bounded_repair(self):
        # Catches a model the configurator's validation would reject skipping probe silently, with no repair.
        self._scripted_controller_modes(('unwired-model','unwired-budget'))

    def test_a_wired_model_that_fails_validation_is_repaired_from_its_device_diff(self):
        # Catches the worker's mapping of an invalid but wired model turning into a host stop at probe, which would
        # lose the configurator's model_contract diff that sends the model back for repair.
        self._scripted_controller_modes(('invalid-model',))

    def test_a_changed_interpretation_at_probe_preparation_leaves_no_uncertain_effect(self):
        # Catches preparation that reaches no device (hash checks, model wiring) running inside the emulator start's
        # effect wrapper, which would leave a resumable stop uncertain although nothing was dispatched.
        self._scripted_controller_modes(('probe-input-changed',))

    def test_owned_emulator_recovery_revalidates_through_fresh_reuse(self):
        self._scripted_controller_modes(('recover-write',))

    def test_recovery_after_a_variant_write_commits_with_the_handoff_and_advances(self):
        # A failed write of the worker's own variant says nothing about the assigned model: the verified replacement
        # commits with the probe handoff, the effect is cleared, nothing routes and the write is never re-executed.
        self._scripted_controller_modes(('recover-variant',))

    def test_recovery_with_the_repair_budget_spent_commits_and_advances_with_the_best_revision(self):
        # Catches the repair budget gating a recovery: replacing the emulator is as safe with no repair left as with
        # one, so the replacement commits with the probe handoff and the spent budget only means advance, not stop.
        self._scripted_controller_modes(('recover-budget','recover-repeat'))

    def test_emulator_recovery_safety_boundaries(self):
        self._scripted_controller_modes(('recover-lost','recover-stop','recover-reset','recover-cancel','recover-commit-cancel','recover-restart','recover-physical','recover-commit-crash','recover-observe-stop','recover-observe-raise'))

    def test_recovery_requires_outcomes_for_every_uncertain_dispatch(self):
        self._scripted_controller_modes(('recover-dispatch-exception','recover-mixed-unknown'))

    def test_initial_observation_stop_failure_preserves_controller_uncertainty(self):
        self._scripted_controller_modes(('recover-initial-stop-raise','recover-initial-stop-live'))

    def test_other_runtime_host_and_operator_faults_block_model_recovery(self):
        self._scripted_controller_modes(('recover-write-then-host','recover-write-then-operator'))

    def _scripted_controller_modes(self, modes):
        # Real TCP peer, external worker processes and emitted package; no inference/native qualification.
        import http.server, sys, threading, time
        from unittest.mock import patch
        import generative_driver
        from generative_driver.benchmark_support.truth import seal, unlock
        from generative_driver.benchmark_support import harness
        from generative_driver import device_client
        from generative_driver.configurator import Controller
        from tq9_workflow_fixture import Silicon, WORKER
        from suite_fixtures import public_case_fixture
        for mode in modes:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); resources=root/'resources';trial=root/'trial';evidence='e'*64
                # The test plays the bench harness: it admits the case, provides the device and holds the password.
                manifest=public_case_fixture(resources,calibration={'status':'passed','evidence_sha256':evidence})
                case=resources/'cases/tq9-v2'
                password=root/'password';password.write_text('synthetic fixture credential')
                recipe={'stimuli':{'temperature':{'minimum':-100,'maximum':100,'command':'temp {value}'}},
                    'observations':{'compare':{'command':'compare','width':32,'count':1},'reload':{'command':'reload','width':32,'count':1},
                        'temperature':{'kind':'scalar','command':'temperature','unit':'degC'}},'reset_commands':['reset']}
                effects=[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':400}},{'task':'disarm','inputs':{}}]
                phases={'diagnostic':{'temperature_vectors':[{'stimulus':v,'expected':v,'absolute_tolerance':.01} for v in (-8,0,31)],
                    'effect_vectors':[{'task':'arm','inputs':{}}]+[{'task':'set_duty','inputs':{'duty':v}} for v in (0,450,1000)]+[{'task':'disarm','inputs':{}}]},
                    'final':{'temperature_vectors':[{'stimulus':v,'expected':v,'absolute_tolerance':.01} for v in (-7,29)],'effect_vectors':effects}}
                if mode == 'final-bounds':
                    phases['diagnostic']['effect_vectors'][3]['inputs']['duty'] = 800
                    effects[1]['inputs']['duty'] = 900
                truth={'private_sentinel':'EVALUATOR SENTINEL','images':manifest['images'],'recipe':recipe,
                    'contracts':{'units':{'temperature':'degC','duty':'permille'},'scenarios':{'original':{'phases':phases}},
                                 'time_policy':{'sample_settle_seconds':0}}}
                if mode=='recover-physical':manifest['approval_scope']='bound-device'
                manifest['truth']['sha256']=seal(truth,resources/manifest['truth']['path'],password.read_text())
                (case/'case.json').write_text(json.dumps(manifest))
                controller=Controller(root/'home');peers=[];worker_live=[]
                class Handler(http.server.BaseHTTPRequestHandler):
                    def do_POST(self):
                        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        try:
                            if mode=='device-lost' and request['params'].get('arguments',{}).get('parameters')=={'unexpected':1}:
                                # The provider becomes unreachable once probe has its assignment, before its first call.
                                endpoint.close()
                            response=controller.call(request['method'],request['params'])
                            if request['params'].get('name') in ('interface_execute','probe_run'):
                                worker_live.append((request['params']['name'],response.get('ok'),peers[-1].running))
                                if mode=='monitor-missing' and request['params']['name']=='probe_run':peers[-1].monitor_available=False
                            if mode=='recover-restart' and request['params'].get('arguments',{}).get('operation')=='disarm' and not response.get('ok'):
                                # The configurator loses its control of the device: the harness withdraws the endpoint.
                                endpoint.close()
                        except Exception as error:response={'fixture_error':str(error)}
                        body=json.dumps(response).encode()
                        try:self.send_response(200);self.end_headers();self.wfile.write(body)
                        except OSError:pass  # A cancelled worker no longer reads its reply.
                    def log_message(self,*args):pass
                server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
                threading.Thread(target=server.serve_forever,daemon=True).start()
                script=root/'scripted_worker.py'
                source=WORKER.replace('PACKAGE_PARENT',repr(str(Path(generative_driver.__file__).resolve().parents[1])))\
                    .replace('FIXTURE_PARENT',repr(str(Path(__file__).parent))).replace('SERVICE_URL',repr('http://127.0.0.1:'+str(server.server_port)))
                if mode=='missing-mission':source=source.replace("('set_duty',{'duty':370}),",'')
                if mode=='rejected-write':
                    source=source.replace("'operation':'measure','parameters':{'unexpected':1}",
                                          "'operation':'set_duty','parameters':{'unexpected':1}")
                if mode=='repair-mapping':
                    source=source.replace("pathlib.Path('capabilities.json').write_text(json.dumps(capabilities()))",
                        "mapping=capabilities()\n    marker=pathlib.Path("+repr(str(root/'first-probe'))+")\n"
                        "    if not marker.exists():\n        marker.write_text('first diagnostic')\n"
                        "        mapping['tasks'].pop('temperature');status='needs_revision'\n"
                        "    pathlib.Path('capabilities.json').write_text(json.dumps(mapping))")
                if mode == 'repair-decoder':
                    # The first decoder reads a key the device's reply lacks: a model fault on the device connection.
                    # Repair sees only that device diff, the previous candidate and the device readings of its runs.
                    source = source.replace("pathlib.Path('model.json').write_text(json.dumps(model()))",
                        "candidate=model()\n    context_path=pathlib.Path('REPAIR_CONTEXT.json')\n"
                        "    if context_path.exists():\n"
                        "        context=json.loads(context_path.read_text());assert context['mode']=='repair'\n"
                        "        defects=json.loads(pathlib.Path(context['feedback']).read_text())\n"
                        "        assert defects and {d['source'] for d in defects}<={'interface_execute','probe_run'},defects\n"
                        "        prior=context['history'][-1]['files']\n"
                        "        candidate=json.loads(pathlib.Path(prior['model.json']).read_text())\n"
                        "        tasks=json.loads(pathlib.Path(context['task_contract']).read_text())\n"
                        "        assert tasks['tasks']['temperature']['outputs']['temperature']['unit']=='degC'\n"
                        "        rows=[json.loads(line) for line in pathlib.Path(prior['live-observations.jsonl']).read_text().splitlines()]\n"
                        "        assert any(row['operation']=='measure' and row['observation']['temperature_reference']==20 for row in rows)\n"
                        "        candidate['operations']['measure']['outputs']['temperature']['key']='T:'\n"
                        "    else:\n        candidate['operations']['measure']['outputs']['temperature']['key']='X:'\n"
                        "    pathlib.Path('model.json').write_text(json.dumps(candidate))")
                    source = source.replace("    assert measured['ok'], 'live interface_execute failed'\n", '')
                    source = source.replace("    assert probed['ok'], 'live probe_run failed'\n",
                        "    status='completed' if measured['ok'] and probed['ok'] else 'needs_revision'\n")
                if mode == 'repair-ground':
                    # Probe never runs `status`; ground does, and the device rejects it: ground's device diff repairs.
                    source = source.replace("pathlib.Path('model.json').write_text(json.dumps(model()))",
                        "candidate=model()\n    if pathlib.Path('REPAIR_CONTEXT.json').exists():\n"
                        "        context=json.loads(pathlib.Path('REPAIR_CONTEXT.json').read_text())\n"
                        "        defects=json.loads(pathlib.Path(context['feedback']).read_text())\n"
                        "        assert [d['source'] for d in defects]==['interface_execute'] and 'status' in json.dumps(defects),defects\n"
                        "    else:\n"
                        "        arm=candidate['operations']['arm']\n"
                        "        candidate['operations']['status']=dict(arm,effect='read',steps=[dict(arm['steps'][0],tx=[{'text':'S\\n'}])])\n"
                        "        candidate['provenance'].append({'item':'operations.status','source':'synthetic scripted fixture'})\n"
                        "    pathlib.Path('model.json').write_text(json.dumps(candidate))")
                if mode == 'normal':
                    # Each worker's task context, so the test can see which earlier notes reached it.
                    source = source.replace("artifacts=[];status='completed'\n",
                        "artifacts=[];status='completed'\npathlib.Path('task-context.json').write_text(json.dumps(task['context']))\n")
                if mode in ('unwired-model','unwired-budget'):
                    # The first interpretation is a model the emulator binding cannot carry (unparseable, or no channel).
                    first = "'{unfinished'" if mode == 'unwired-model' else "json.dumps({k:v for k,v in model().items() if k!='channel'})"
                    source = source.replace("pathlib.Path('model.json').write_text(json.dumps(model()))",
                        "if pathlib.Path('REPAIR_CONTEXT.json').exists():\n"
                        "        context=json.loads(pathlib.Path('REPAIR_CONTEXT.json').read_text())\n"
                        "        defects=json.loads(pathlib.Path(context['feedback']).read_text())\n"
                        "        assert [d['source'] for d in defects]==['model_contract'],defects\n"
                        "        prior=context['history'][-1]['files']\n"
                        "        assert pathlib.Path(prior['model.json']).read_text()=="+first+"\n"
                        "        pathlib.Path('model.json').write_text(json.dumps(model()))\n"
                        "    else:\n        pathlib.Path('model.json').write_text("+first+")")
                if mode == 'invalid-model':
                    # The first interpretation is wired (a uart channel) but fails validation: measure lacks parameters,
                    # so the runtime refuses every call before the device opens, while the worker still maps it.
                    source = source.replace("pathlib.Path('model.json').write_text(json.dumps(model()))",
                        "candidate=model()\n    if pathlib.Path('REPAIR_CONTEXT.json').exists():\n"
                        "        context=json.loads(pathlib.Path('REPAIR_CONTEXT.json').read_text())\n"
                        "        defects=json.loads(pathlib.Path(context['feedback']).read_text())\n"
                        "        assert [d['source'] for d in defects]==['model_contract'],defects\n"
                        "    else:\n        del candidate['operations']['measure']['parameters']\n"
                        "    pathlib.Path('model.json').write_text(json.dumps(candidate))")
                    source = source.replace("    assert measured['ok'], 'live interface_execute failed'\n", '')
                    source = source.replace("    assert probed['ok'], 'live probe_run failed'\n",
                        "    status='completed' if measured['ok'] and probed['ok'] else 'needs_revision'\n")
                if mode == 'concurrent-calls':
                    # One worker makes two live reads at once; the gateway serves them on separate threads.
                    source = source.replace("    probed=call('probe_run',",
                        "    import threading\n    pair=[]\n"
                        "    threads=[threading.Thread(target=lambda:pair.append(call('interface_execute',{'model_dir':model_dir,'operation':'measure','parameters':{}}))) for _ in range(2)]\n"
                        "    [thread.start() for thread in threads];[thread.join() for thread in threads]\n"
                        "    assert len(pair)==2 and all(result['ok'] for result in pair),pair\n"
                        "    probed=call('probe_run',")
                if mode == 'device-lost':
                    # Every probe call, the effectful disarm too, is refused before it reaches the device.
                    source = source.replace("    assert measured['ok'], 'live interface_execute failed'\n", '')
                    source = source.replace("    assert probed['ok'], 'live probe_run failed'\n",
                        "    disarmed=call('interface_execute',{'model_dir':model_dir,'operation':'disarm','parameters':{}})\n"
                        "    assert not disarmed['ok'] and disarmed['error']['code']=='device_unavailable',disarmed\n"
                        "    status='needs_revision'\n")
                if mode == 'recover-variant':
                    # The worker runs its own copy of the model with a wrong disarm expectation: the write reaches the
                    # device and fails for that variant's model, never for the assigned one.
                    source = source.replace("    pathlib.Path('capabilities.json').write_text(json.dumps(capabilities()));artifacts=['capabilities.json']",
                        "    import shutil\n    shutil.copytree(model_dir,'variant')\n"
                        "    variant=json.loads(pathlib.Path('variant/model.json').read_text())\n"
                        "    variant['operations']['disarm']['steps'][0]['expect']['final_line_equals']='WRONG'\n"
                        "    pathlib.Path('variant/model.json').write_text(json.dumps(variant))\n"
                        "    failed=call('interface_execute',{'model_dir':str(pathlib.Path('variant').resolve()),'operation':'disarm','parameters':{}})\n"
                        "    assert not failed['ok'] and failed['error']['fault']=='model',failed\n"
                        "    pathlib.Path('capabilities.json').write_text(json.dumps(capabilities()));artifacts=['capabilities.json']")
                if mode.startswith('recover-') and mode != 'recover-variant':
                    source = source.replace("pathlib.Path('model.json').write_text(json.dumps(model()))",
                        "candidate=model()\n    if MODE_REPEAT or not pathlib.Path('REPAIR_CONTEXT.json').exists():\n        candidate['operations']['disarm']['steps'][0]['expect']['final_line_equals']='WRONG'\n"
                        "    pathlib.Path('model.json').write_text(json.dumps(candidate))")
                    source = source.replace("pathlib.Path('replies.json').write_text(json.dumps(replies()))",
                        "captures=replies()\n    if MODE_REPEAT or not pathlib.Path('REPAIR_CONTEXT.json').exists():\n        captures['operations']['disarm']['steps']=[['WRONG']]\n"
                        "    pathlib.Path('replies.json').write_text(json.dumps(captures))")
                    source = source.replace("    pathlib.Path('capabilities.json').write_text(json.dumps(capabilities()));artifacts=['capabilities.json']",
                        "    failed=call('interface_execute',{'model_dir':model_dir,'operation':'disarm','parameters':{}})\n"
                        "    if not failed['ok']:status='needs_revision'\n"
                        "    pathlib.Path('capabilities.json').write_text(json.dumps(capabilities()));artifacts=['capabilities.json']")
                if mode == 'recover-observe-stop':
                    # After the failed write the worker alters its probe record: the stage's observation after the
                    # recovery stops for integrity before the replacement commits.
                    source = source.replace("    if not failed['ok']:status='needs_revision'\n",
                        "    if not failed['ok']:status='needs_revision'\n"
                        "    record=pathlib.Path(probed['probe']);record.write_text(record.read_text()+' ')\n")
                if mode in ('recover-budget','recover-repeat'):
                    # The packaged model keeps the wrong disarm expectation; a fresh write of it would leave reuse with
                    # an uncertain effect no recovery covers, so this reuse worker does not disarm.
                    source = source.replace(",('disarm',{})]", "]")
                if mode in ('recover-dispatch-exception','recover-mixed-unknown','recover-write-then-host','recover-write-then-operator'):
                    source=source.replace("    failed=call('interface_execute',{'model_dir':model_dir,'operation':'disarm','parameters':{}})\n    if not failed['ok']:status='needs_revision'",
                        "    try:\n        failed=call('interface_execute',{'model_dir':model_dir,'operation':'disarm','parameters':{}})\n"
                        "    except RuntimeError:pass\n"
                        "    later=call('interface_execute',{'model_dir':model_dir,'operation':'measure','parameters':{}})\n"
                        "    assert later['error']['fault']==EXPECTED_POST_WRITE_FAULT\n    status='needs_revision'")
                if mode == 'repair-ground':
                    source = source.replace("elif stage=='emit':",
                        "elif stage=='ground':\n"
                        "    marker=pathlib.Path("+repr(str(root/'first-ground'))+")\n"
                        "    if not marker.exists():\n        marker.write_text('first ground')\n"
                        "        failed=call('interface_execute',{'model_dir':task['context']['model_dir'],'operation':'status','parameters':{}})\n"
                        "        assert not failed['ok'] and failed['error']['fault']=='model',failed\n"
                        "        status='needs_revision'\n"
                        "elif stage=='emit':")
                if mode == 'final-bounds':
                    source = source.replace("pathlib.Path('model.json').write_text(json.dumps(model()))",
                        "candidate=model();candidate['operations']['set_duty']['parameters']['duty']['maximum']=800\n"
                        "    pathlib.Path('model.json').write_text(json.dumps(candidate))")
                source=source.replace('EXPECTED_POST_WRITE_FAULT',repr('host' if mode=='recover-write-then-host' else 'operator' if mode=='recover-write-then-operator' else 'model'))
                source=source.replace('MODE_REPEAT',str(mode=='recover-repeat'))
                script.write_text(source)
                def native_start(**kwargs):
                    self.assertEqual(Path(kwargs['image']).name,'firmware.bin')
                    peer=Silicon(kwargs['image']);peers.append(peer);peer.wrong_hidden=mode=='hidden-wrong'
                    session=peer.session(kwargs['recipe'],root/('native-'+str(len(peers))))
                    class Process:
                        def poll(self):return 0 if getattr(peer,'closed',False) else None
                    session.process=Process()
                    if mode.startswith('recover-'):
                        if mode=='recover-lost':session.process=None
                        if mode=='recover-stop':session.stop=lambda:None
                        if mode in ('recover-initial-stop-raise','recover-initial-stop-live'):
                            session.observe=lambda:{'values':{},'reads':[]}
                            def stop():
                                if mode=='recover-initial-stop-raise':raise RuntimeError('Controlled native stop failure')
                            session.stop=stop
                        if len(peers)==2 and mode=='recover-reset':peer.temperature=21
                        if len(peers)==2 and mode=='recover-cancel':controller.call('cancel',run)
                    return session
                from generative_driver.benchmark_support import emulated
                from generative_driver import toolkit
                original_call_tool=toolkit.call_tool
                turns={'measures':0,'first_done':False};counter=threading.Lock();device_log=[]
                inside,closed_under,release_entered,released=(threading.Event() for _ in range(4))
                def runtime_boundary(name,arguments):
                    if name=='interface_execute' and arguments.get('operation')=='measure' and arguments.get('parameters')=={}:
                        with counter:
                            turns['measures']+=1;count=turns['measures']
                        if mode=='concurrent-calls' and count==2:
                            # The first of the two concurrent reads stays inside its window long enough for the other
                            # to open and close its own there, if anything let it.
                            inside.set();closed_under.wait(1)
                            try:return original_call_tool(name,arguments)
                            finally:turns['first_done']=True
                        if mode=='cancel-in-call' and count==1:
                            # The run is cancelled while this live call is inside the device's window; the run lets go
                            # of the device on its way out, which must wait for this call.
                            controller.call('cancel',run)
                            release_entered.wait(5);released.wait(1)
                    outcome=original_call_tool(name,arguments)
                    if mode in ('recover-dispatch-exception','recover-mixed-unknown','recover-write-then-host','recover-write-then-operator') and name=='interface_execute' and arguments.get('operation')=='disarm':
                        # Real device I/O happened; the boundary then loses or cannot classify its outcome.
                        peers[-1].broken_measure=True
                        if mode=='recover-dispatch-exception':raise RuntimeError('Controlled adapter failure after device I/O')
                        if mode=='recover-mixed-unknown':outcome['error'].pop('fault',None)
                    if mode in ('recover-write-then-host','recover-write-then-operator') and name=='interface_execute' and arguments.get('operation')=='measure' and not outcome['ok']:
                        # The external runtime boundary reports its own host/operator diagnostic ownership.
                        outcome['error']['fault']='host' if mode=='recover-write-then-host' else 'operator'
                    return outcome
                original_replace=device_client.DeviceClient.replace
                def replace(client,*args,**kwargs):
                    # The configurator's recovery goes through its device handle; the harness's provider replaces.
                    receipt=original_replace(client,*args,**kwargs)
                    if mode=='recover-commit-cancel':controller.call('cancel',run)
                    return receipt
                original_event=controller._event
                def event(run_id,kind,payload,db=None):
                    # A failure inside the recovery commit: the replacement, the cleared effect and the handoff roll back together.
                    if mode=='recover-commit-crash' and kind=='emulator.recovered':raise RuntimeError('Controlled failure inside the recovery commit')
                    return original_event(run_id,kind,payload,db)
                controller._event=event
                original_accept=controller._accept;interpreted=[]
                def accept(*args,**kwargs):
                    handoff=original_accept(*args,**kwargs)
                    if mode=='snapshot-next' and handoff['stage']=='acquire':
                        (root/'home/runs'/args[0]/'benchmark/inputs/firmware.bin').write_bytes(b'tampered after acceptance')
                    if handoff['stage']=='interpret':interpreted.append(handoff)
                    return handoff
                original_check=controller._check
                def check(*args,**kwargs):
                    # In recover modes the probe is observed only after the recovery, which has not committed yet.
                    if mode=='recover-observe-raise' and args[1]=='probe':raise RuntimeError('Controlled observation failure after recovery')
                    return original_check(*args,**kwargs)
                original_snapshot=controller._verify_snapshot
                def verify_snapshot(*args,**kwargs):
                    # Between the loop's handoff verification and probe preparation, the accepted model changes.
                    if mode=='probe-input-changed' and interpreted:
                        changed=next(Path(a['path']) for a in interpreted.pop()['artifacts'] if a['path'].endswith('model.json'))
                        changed.write_text(changed.read_text()+' ')
                    return original_snapshot(*args,**kwargs)
                # Only the harness unlocks the truth, to take the device recipe before the run; the configurator never
                # does, and no stage runs the evaluator.
                import inspect
                from generative_driver.benchmark_support import emulator as emulator_module
                real_truth=emulator_module.truth_for_case;truth_callers=[]
                def truth_for_case(*args,**kwargs):
                    names={frame.function for frame in inspect.stack(0)}
                    truth_callers.append(names)
                    if not {'device_recipe','admit'}<=names or names&{'replace','recover_emulator','reconcile','_run','_prepare','_check','_dispatch'}:
                        raise AssertionError('Truth unlocked outside the harness')
                    return real_truth(*args,**kwargs)
                def evaluator(*args,**kwargs):raise AssertionError('Evaluator called in the stage loop')
                endpoint=provider=None
                try:
                    with patch.object(toolkit,'call_tool',side_effect=runtime_boundary),patch.object(device_client.DeviceClient,'replace',replace),patch.object(controller,'_accept',side_effect=accept),patch('generative_driver.benchmark.case_root',return_value=resources),\
                         patch.object(controller,'_check',side_effect=check),patch.object(controller,'_verify_snapshot',side_effect=verify_snapshot),\
                         patch('generative_driver.benchmark_support.registry.require_calibration',return_value={'ok':True,'evidence_sha256':evidence}),\
                         patch.object(emulator_module,'truth_for_case',side_effect=truth_for_case),\
                         patch('generative_driver.benchmark_support.emulated_evidence._diagnose',side_effect=evaluator),\
                         patch('generative_driver.benchmark_support.emulated_evidence._final',side_effect=evaluator),\
                         patch('generative_driver.benchmark_support.native.NativeSession.start',side_effect=native_start):
                        if mode in ('recover-initial-stop-raise','recover-initial-stop-live'):
                            # The emulator's startup reading fails and so does its stop. The harness keeps the live
                            # emulator owned and names it for manual reconciliation; no run starts on an unverified device.
                            reached=lambda method,params=None,home=None,**kwargs:{'ok':True} if method=='ping' else controller.call(method,params or {})
                            with patch('generative_driver.client.call',side_effect=reached):
                                record=harness.run_trial(trial,{'trial_id':trial.name,'home':str(root/'home'),'case':'tq9-v2',
                                    'case_options':{'scenario_id':'original'},'password_file':str(password)})
                            self.assertEqual((record['status'],record['run_id']),('stop-unconfirmed',None))
                            self.assertIn('emulator cleanup unverified',record['reason'])
                            self.assertIs(record['device_stop']['confirmed'],False)
                            self.assertEqual(len(peers),1);self.assertFalse(getattr(peers[0],'closed',False))
                            self.assertTrue(record['device']['session_id']);self.assertIs(record['device']['healthy'],True)
                            self.assertEqual(harness.read_trial(trial),record);self.assertNotIn(str(password),json.dumps(record))
                            with controller._db() as db:
                                self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0],0)
                            continue
                        provider,admission=harness.admit('tq9-v2',{'scenario_id':'original'},trial,password_file=password)
                        provider.start()
                        if mode in ('concurrent-calls','cancel-in-call'):
                            # Every edge of the device's window and the run's release, as the provider receives them.
                            original_set_running,original_release=provider.set_running,provider.release
                            def set_running(running):
                                device_log.append(('window',running))
                                if not running and inside.is_set() and not turns['first_done']:closed_under.set()
                                return original_set_running(running)
                            def release(session_id):
                                device_log.append(('release',));released.set()
                                return original_release(session_id)
                            provider.set_running,provider.release=set_running,release
                            original_run_release=controller._release
                            def run_release(*args,**kwargs):
                                release_entered.set()
                                return original_run_release(*args,**kwargs)
                            controller._release=run_release
                        endpoint=harness.DeviceServer(provider,root/'endpoint');descriptor=endpoint.descriptor
                        run=controller.start({'goal':'scripted-contract-fixture','case':'tq9-v2','budget_seconds':60,
                            'effects':['write','actuate'],'max_revisions':0 if mode in ('recover-budget','unwired-budget') else 2,'case_options':{'scenario_id':'original'},
                            'device':descriptor,'admission':admission,'evaluation_token_sha256':hashlib.sha256(b'scripted harness token').hexdigest(),
                            'executor_config':{'command':[sys.executable,str(script)]}})
                        deadline=time.monotonic()+60
                        while True:
                            status=controller.call('status',run)
                            if status['status'] in ('completed','failed','blocked','cancelled') and not status['stopping']:break
                            if time.monotonic()>deadline:self.fail('Scripted controller deadline: '+str(status))
                            time.sleep(.01)
                        result=controller.call('result',run);events=controller.call('events',run)['events']
                        held=provider.status()
                        # As the harness does once the run is terminal: stop the device, confirmed, and seal the handoffs.
                        finished=harness.finish(provider,'tq9-v2',root/'home/runs'/run['run_id'],result['accepted_handoffs'],password,status)
                        self.assertNotIn(str(password),json.dumps(json.loads(controller._row(run['run_id'])['spec'])))
                        self.assertFalse(truth_callers and any(names&{'_run','_dispatch'} for names in truth_callers))
                        assignments=[e['data'] for e in events if e['kind']=='stage.assigned']
                        self.assertNotIn('EVALUATOR SENTINEL',json.dumps(assignments))
                        self.assertNotIn('maintain',[a['stage'] for a in assignments])
                        self.assertNotIn('maintenance_cycles',result['progress'])
                        if mode=='cancel-in-call':
                            # The run was cancelled while a live read was inside the device's window. Its release
                            # waited for that call, which ended on a running device with both readings; only then did
                            # the run let go of the device.
                            self.assertEqual(status['status'],'cancelled')
                            self.assertTrue(release_entered.is_set())
                            self.assertFalse([e for e in events if e['kind'] in ('device.unavailable','device.release_failed')])
                            reads=[e['data'] for e in events if e['kind']=='tool.finished' and e['data']['name']=='interface_execute'
                                   and e['data'].get('connected') and not e['data'].get('refused_before_open')]
                            self.assertEqual(len(reads),1)
                            self.assertTrue(reads[0]['ok'],reads[0])
                            self.assertEqual(reads[0]['result']['observation']['source'],'instrument')
                            self.assertEqual(device_log[-1],('release',))
                            self.assertGreaterEqual(len(device_log),3)
                            self.assertEqual(device_log[:-1],[('window',True),('window',False)]*((len(device_log)-1)//2))
                            self.assertFalse(peers[0].paused_requests)
                            self.assertFalse(result['uncertain_effect'])
                            continue
                        if mode.startswith('recover-') and mode not in ('recover-write','recover-variant','recover-budget','recover-repeat'):
                            self.assertNotEqual(status['status'],'completed')
                            self.assertTrue(result['uncertain_effect'])
                            self.assertEqual(result['emulator_recovery_count'],0)
                            self.assertEqual(result['progress']['repairs'],0)
                            self.assertEqual(len(peers),2 if mode in ('recover-reset','recover-cancel','recover-commit-cancel','recover-commit-crash','recover-observe-stop','recover-observe-raise') else 1)
                            if mode in ('recover-cancel','recover-commit-cancel'):self.assertEqual(status['status'],'cancelled')
                            # An effect left uncertain after the check is a safety stop, and an observed record that
                            # changed stays an integrity stop. The configurator stops no emulator, so no cleanup fails.
                            else:self.assertEqual(status['outcome_category'],'integrity' if mode=='recover-observe-stop' else 'safety')
                            self.assertFalse([e for e in events if e['kind']=='cleanup.failed'])
                            # No recovery committed, so the run's device record still names the session it started with.
                            self.assertEqual(result['progress']['device'],{'session_id':descriptor['session_id'],
                                                                           'binding':descriptor['binding'],'receipts':[]})
                            if mode in ('recover-commit-crash','recover-observe-stop','recover-observe-raise'):
                                # Nothing of the recovery committed: no handoff, the write's pending effect and the old
                                # lease remain. The replacement started and was verified, so its abandonment is recorded
                                # as a failed recovery: in the commit, at a stop of the stage's observation, or when that
                                # observation raised (a safety stop, not a host failure, while the effect is uncertain).
                                reason={'recover-commit-crash':'Controlled failure inside the recovery commit',
                                        'recover-observe-stop':'Observed probe artifact changed after the tool returned',
                                        'recover-observe-raise':'Controlled observation failure after recovery'}[mode]
                                self.assertEqual((status['status'],status['outcome_category']),
                                                 ('blocked','integrity' if mode=='recover-observe-stop' else 'safety'))
                                self.assertIn(reason if mode=='recover-observe-stop' else 'Emulator recovery unverified: '+reason,status['reason'])
                                started=[e for e in events if e['kind']=='emulator.recovery_started']
                                failed=[e for e in events if e['kind']=='emulator.recovery_failed']
                                self.assertEqual(len(started),1);self.assertEqual(len(failed),1)
                                self.assertGreater(failed[0]['id'],started[0]['id'])
                                self.assertIn(reason,failed[0]['data']['reason'])
                                self.assertEqual(failed[0]['data']['assignment_id'],started[0]['data']['assignment_id'])
                                self.assertFalse([e for e in events if e['kind']=='emulator.recovered'])
                                self.assertNotIn('probe',[h['stage'] for h in result['accepted_handoffs']])
                                with controller._db() as db:
                                    pending=db.execute('SELECT COUNT(*) FROM pending_effects WHERE run_id=?',(run['run_id'],)).fetchone()[0]
                                self.assertEqual(pending,1)
                                self.assertEqual(peers[0].device_commands.count('D'),1)
                            if mode in ('recover-dispatch-exception','recover-mixed-unknown'):
                                starts=[e for e in events if e['kind']=='tool.started' and e['data'].get('effectful') and e['data'].get('actor')=='worker']
                                self.assertEqual(len(starts),1)
                                self.assertEqual(peers[0].device_commands.count('D'),1)
                                with controller._db() as db:
                                    pending=db.execute('SELECT dispatch_id FROM pending_effects WHERE run_id=?',(run['run_id'],)).fetchall()
                                self.assertEqual([row['dispatch_id'] for row in pending],[starts[0]['id']])
                                completions=[e for e in events if e['kind']=='tool.finished' and e['data'].get('dispatch_id')==str(starts[0]['id'])]
                                self.assertEqual(len(completions),0 if mode=='recover-dispatch-exception' else 1)
                                reads=[e for e in events if e['kind']=='tool.finished' and e['data'].get('result',{}).get('operation')=='measure' and e['data'].get('result',{}).get('error',{}).get('fault')=='model']
                                self.assertTrue(reads)
                            if mode=='recover-reset':self.assertTrue(peers[1].closed)
                            continue
                        if mode in ('monitor-missing','snapshot-next','probe-input-changed','device-lost'):
                            self.assertNotEqual(status['status'],'completed')
                            if mode in ('monitor-missing','device-lost'):
                                # The provided device cannot serve its readings or cannot be reached: each later call is
                                # refused before it reaches the device, nothing is left uncertain, and the stage stops
                                # for the operator once its worker returns.
                                self.assertEqual((status['status'],status['outcome_category']),('blocked','operator'))
                                self.assertIn('nothing was dispatched',status['reason'])
                                self.assertIn('Device provider unavailable' if mode=='device-lost' else 'Device provider refused observe',status['reason'])
                                self.assertFalse(result['uncertain_effect'])
                                with controller._db() as db:
                                    self.assertEqual(db.execute('SELECT COUNT(*) FROM pending_effects WHERE run_id=?',(run['run_id'],)).fetchone()[0],0)
                                lost=[e['data'] for e in events if e['kind']=='device.unavailable']
                                self.assertTrue(lost);self.assertEqual({e['phase'] for e in lost},{'before'})
                                refused=[e['data'] for e in events if e['kind']=='tool.finished' and e['data']['assignment_id']==lost[0]['assignment_id']
                                         and e['data'].get('connected')]
                                self.assertTrue(refused)
                                self.assertTrue(all(e['refused_before_open'] and not e['ok'] and 'observation' not in e['result'] for e in refused))
                            if mode=='device-lost':
                                self.assertEqual(status['stage'],'probe')
                                disarm=next(e for e in refused if e['result'].get('error',{}).get('code')=='device_unavailable' and e['effectful'])
                                self.assertTrue(disarm['effects_reconciled'])
                                self.assertEqual(peers[0].device_commands,[]);self.assertEqual(len(peers),1)
                                self.assertTrue(any(e['kind']=='device.release_failed' for e in events))
                            if mode=='snapshot-next':
                                self.assertIn('hash',status['reason']);self.assertEqual(len(assignments),1)
                                self.assertEqual(peers[0].device_commands,[])
                            if mode=='probe-input-changed':
                                # The changed bytes stop the run for integrity before any device use: nothing was
                                # dispatched, so no effect is pending and the run is not uncertain. Sealing is the
                                # harness's now, so the integrity stop stands as the run's outcome.
                                blocked=[e['data'] for e in events if e['kind']=='run.blocked']
                                self.assertEqual([b['outcome_category'] for b in blocked],['integrity'])
                                self.assertEqual(status['outcome_category'],'integrity')
                                self.assertIn('Accepted interpretation bytes changed',blocked[0]['reason'])
                                self.assertFalse(result['uncertain_effect']);self.assertFalse(status['uncertain_effect'])
                                with controller._db() as db:
                                    self.assertEqual(db.execute('SELECT COUNT(*) FROM pending_effects WHERE run_id=?',(run['run_id'],)).fetchone()[0],0)
                                self.assertFalse([e for e in events if e['data'].get('actor')=='evaluator'])
                                self.assertEqual([a['stage'] for a in assignments],['acquire','interpret'])
                                self.assertEqual(peers[0].device_commands,[])
                            continue
                        if mode in ('unwired-model','unwired-budget'):
                            # Probe preparation finds an interpreted model the emulator cannot carry: no emulator starts
                            # for it, the configurator's own validation of that model is the diff, and it revises
                            # within the repair budget; once the budget is spent the run advances with the note.
                            handoffs=result['accepted_handoffs']
                            self.assertEqual(status['status'],'completed',result)
                            self.assertEqual(result['progress']['equivalence'],'pending')
                            unwired=next(h for h in handoffs if h['stage']=='probe' and h['revision']==0)
                            self.assertEqual(unwired['artifacts'],[])
                            notes=unwired['notes']['inline'];diff=notes['configurator']['device_diff']
                            first=next(h for h in handoffs if h['stage']=='interpret' and h['revision']==0)
                            model_sha=next(a['sha256'] for a in first['artifacts'] if a['path'].endswith('model.json'))
                            self.assertEqual([(d['source'],d['model']['sha256']) for d in diff],[('model_contract',model_sha)])
                            found=' '.join(str(d.get('what')) for d in diff[0]['defects'])
                            self.assertIn('cannot carry',found)
                            self.assertIn('not valid JSON' if mode=='unwired-model' else 'missing required fields',found)
                            self.assertFalse(grade_keys(diff));self.assertNotIn('EVALUATOR SENTINEL',json.dumps(handoffs))
                            self.assertFalse(next(c for c in unwired['checks'] if c['name']=='emulator_wiring')['passed'])
                            self.assertFalse(next(c for c in unwired['checks'] if c['name']=='executable_model_contract')['passed'])
                            skipped=[e['data'] for e in events if e['kind']=='stage.skipped']
                            if mode=='unwired-model':
                                self.assertEqual(status['reason'],'Frozen driver awaits independent evaluation')
                                self.assertEqual([h['stage'] for h in handoffs],['acquire','interpret','probe','interpret','probe','ground','emit','reuse'])
                                self.assertEqual([a['stage'] for a in assignments],['acquire','interpret','interpret','probe','ground','emit','reuse'])
                                self.assertEqual([(s['stage'],s['revision']) for s in skipped],[('probe',0)])
                                self.assertEqual(result['progress']['repairs'],1)
                                self.assertEqual([(h['stage'],h['revision']) for h in handoffs if h.get('route')=='interpret'],[('probe',0)])
                                self.assertEqual(notes['outcome']['kind'],'revise')
                                # The repair receives that diff and the candidate it is about.
                                repair=next(a for a in assignments if a['stage']=='interpret' and a['revision']==1)
                                self.assertEqual(json.loads((Path(repair['workspace'])/'DEFECTS.json').read_text()),diff)
                                context=json.loads((Path(repair['workspace'])/'REPAIR_CONTEXT.json').read_text())
                                self.assertEqual((Path(repair['workspace'])/context['history'][-1]['files']['model.json']).read_text(),'{unfinished')
                                self.assertEqual(len(peers),1)
                                self.assertIs(next(c for c in handoffs[-1]['checks'] if c['name']=='fresh_worker_mission')['passed'],True)
                            else:
                                self.assertEqual(status['reason'],'Fresh reuse skipped: no accepted emitted package to reuse; '
                                                                  'the run awaits independent evaluation')
                                self.assertEqual([h['stage'] for h in handoffs],['acquire','interpret','probe','ground','emit','reuse'])
                                self.assertEqual([a['stage'] for a in assignments],['acquire','interpret'])
                                self.assertEqual([s['stage'] for s in skipped],['probe','ground','emit','reuse'])
                                self.assertEqual(result['progress']['repairs'],0)
                                self.assertFalse([h for h in handoffs if h.get('route')])
                                self.assertEqual(notes['outcome'],{'kind':'advance','reason':'Repair budget exhausted; continuing with best revision'})
                                self.assertEqual(len(peers),1);self.assertEqual(peers[0].device_commands,[])
                            continue
                        # Every stage hands off, worker reports and the reuse mission are notes, and the frozen driver
                        # awaits independent grading after the run: no stage graded it against the truth.
                        self.assertEqual(status['status'],'completed',result)
                        self.assertEqual(status['reason'],'Frozen driver awaits independent evaluation')
                        self.assertEqual(result['progress']['equivalence'],'pending')
                        self.assertIsNone(result['equivalence']);self.assertEqual(result['evaluator_verdicts'],[])
                        self.assertNotIn('terminal_final_failure',result['progress'])
                        # Only the harness unlocked the truth, once, for the device recipe; nothing in the run carries an
                        # evaluator label, the emulator start included, which the harness made before the run.
                        self.assertEqual(len(truth_callers),1)
                        self.assertTrue(all({'device_recipe','admit'}<=names for names in truth_callers))
                        evaluated=[e['data'] for e in events if e['kind'] in ('tool.started','tool.finished') and e['data'].get('actor')=='evaluator']
                        self.assertFalse(evaluated)
                        # Every live call that reached the device carries the instrument readings around it, and runs
                        # inside the device's window.
                        live_calls=[e['data'] for e in events if e['kind']=='tool.finished' and e['data'].get('connected')
                                    and not e['data'].get('refused_before_open')]
                        self.assertTrue(live_calls)
                        for call in live_calls:
                            self.assertEqual((call['result']['before_observation']['source'],call['result']['observation']['source']),
                                             ('instrument','instrument'),call['name'])
                            self.assertEqual(call['result']['observation']['units'],{'duty':'permille','temperature_reference':'degC'})
                        self.assertEqual({c['name'] for c in live_calls}>={'interface_execute','probe_run','benchmark_package_execute'},True)
                        self.assertFalse([e for e in events if e['kind'] in ('device.unavailable','device.release_failed')])
                        # The configurator released its control of the device; the harness stopped it, confirmed.
                        self.assertTrue(provider.released)
                        self.assertEqual(finished['device_stop'],{'stopped':True,'confirmed':True})
                        self.assertEqual(finished['status'],'awaiting-evaluation');self.assertIs(finished['sealed'],True)
                        self.assertEqual(result['progress']['device'],{'session_id':held['session_id'],'binding':held['binding'],
                            'receipts':[r['receipt'] for r in result['emulator_recoveries']]})
                        handoffs=result['accepted_handoffs']
                        self.assertNotIn('EVALUATOR SENTINEL',json.dumps(handoffs))
                        expected=['acquire','interpret','probe','ground','emit','reuse']
                        if mode in ('repair-decoder','recover-write','invalid-model'):expected[3:3]=['interpret','probe']
                        if mode=='repair-ground':expected[4:4]=['interpret','probe','ground']
                        if mode=='recover-repeat':expected[3:3]=['interpret','probe','interpret','probe']
                        self.assertEqual([a['stage'] for a in assignments],expected)
                        self.assertEqual([h['stage'] for h in handoffs],expected)
                        repaired=mode in ('repair-decoder','repair-ground','recover-write','invalid-model','recover-repeat')
                        self.assertEqual(result['progress']['repairs'],2 if mode=='recover-repeat' else 1 if repaired else 0)
                        routed=[h for h in handoffs if h.get('route')=='interpret']
                        self.assertEqual([(h['stage'],h['revision']) for h in routed],
                                         [('probe',0),('probe',1)] if mode=='recover-repeat' else
                                         [('ground' if mode=='repair-ground' else 'probe',0)] if repaired else [])
                        if repaired:
                            # Repair receives the device diff the routed handoff's notes carry, and nothing graded.
                            repair=next(a for a in assignments if a['stage']=='interpret' and a['revision']==1)
                            defects=json.loads((Path(repair['workspace'])/'DEFECTS.json').read_text())
                            self.assertEqual(defects,routed[0]['notes']['inline']['configurator']['device_diff'])
                            self.assertTrue(defects)
                            self.assertLessEqual({d['source'] for d in defects},{'interface_execute','probe_run','probe_diff','model_contract'})
                            for marker in ('EVALUATOR SENTINEL','diagnostic/','final/'):self.assertNotIn(marker,json.dumps(defects))
                            self.assertFalse(grade_keys(defects))
                            context=json.loads((Path(repair['workspace'])/'REPAIR_CONTEXT.json').read_text())
                            self.assertTrue(context['history'])
                            self.assertTrue(all('feedback.json' not in entry['files'] for entry in context['history']))
                            self.assertIn('device evidence',context['evidence_policy'])
                        if mode=='invalid-model':
                            # The model is wired, so probe ran; the worker's mapping of the invalid model is a note, and
                            # the configurator's own validation of the model is the diff that sent it back.
                            self.assertEqual([d['source'] for d in defects],['model_contract'])
                            self.assertEqual(defects[0]['model']['name'],'model')
                            self.assertIn('operations.measure',[d.get('where') for d in defects[0]['defects']])
                            mapping=next(c for c in routed[0]['checks'] if c['name']=='capability_mapping')
                            self.assertFalse(mapping['passed'])
                            self.assertIn('The mapping cannot be checked against this model: KeyError',mapping['reason'])
                            self.assertIn('capabilities.json',{Path(a['path']).name.split('-',1)[1] for a in routed[0]['artifacts']})
                            self.assertEqual(routed[0]['notes']['inline']['outcome']['kind'],'revise')
                        if mode=='concurrent-calls':
                            # The two concurrent reads took turns: no window opened or closed inside another call's.
                            self.assertGreaterEqual(turns['measures'],3)
                            self.assertTrue(inside.is_set());self.assertFalse(closed_under.is_set())
                            windows=[edge for edge in device_log if edge[0]=='window']
                            self.assertEqual(windows,[('window',True),('window',False)]*(len(windows)//2))
                            self.assertEqual(device_log[-1],('release',))
                        if mode=='repair-mapping':
                            probe=next(h for h in handoffs if h['stage']=='probe')
                            mapping=next(c for c in probe['checks'] if c['name']=='capability_mapping')
                            self.assertFalse(mapping['passed'])
                            self.assertIn('tasks.temperature',[error['path'] for error in mapping['errors']])
                            self.assertIn('Worker reported needs_revision',' '.join(probe['notes']['inline']['open_questions']))
                        mission=next(c for c in handoffs[-1]['checks'] if c['name']=='fresh_worker_mission')
                        self.assertIs(mission['passed'],False if mode in ('missing-mission','recover-budget','recover-repeat')
                                      else None if mode=='repair-mapping' else True)
                        if mode=='normal':
                            # The probe hands on the worker's own observed probe record; emit packages it; ground gets
                            # the model, that record and the instrument journal with no grade field anywhere.
                            probe=next(h for h in handoffs if h['stage']=='probe')
                            assigned=next(a for a in assignments if a['stage']=='probe')
                            run_event=next(e['data'] for e in events if e['kind']=='tool.finished' and e['data']['name']=='probe_run'
                                           and e['data']['assignment_id']==assigned['id'])
                            probe_sha=run_event['artifacts'][str(Path(run_event['result']['probe']).resolve())]
                            records=[a for a in probe['artifacts'] if a['path'].endswith('.json')
                                     and json.loads(Path(a['path']).read_text()).get('schema')=='interface-probe/1']
                            self.assertEqual([a['sha256'] for a in records],[probe_sha])
                            self.assertLessEqual({'capabilities.json','model.json','adaptation.json','live-observations-0.jsonl'},
                                                 {Path(a['path']).name.split('-',1)[1] for a in probe['artifacts']})
                            emit=next(a for a in assignments if a['stage']=='emit')
                            self.assertEqual(hashlib.sha256((Path(emit['workspace'])/'probe.json').read_bytes()).hexdigest(),probe_sha)
                            ground=next(a for a in assignments if a['stage']=='ground')
                            self.assertEqual(sorted(ground['allowed_tools']),['interface_execute','model_validate','probe_diff','probe_run'])
                            evidence=json.loads((Path(ground['workspace'])/'ground-evidence/device-observations.json').read_text())
                            self.assertFalse(grade_keys(evidence))
                            self.assertEqual([p['sha256'] for p in evidence['probes']],[probe_sha])
                            for path in ground['inputs']:
                                if Path(path).is_file():self.assertNotIn(b'EVALUATOR SENTINEL',Path(path).read_bytes())
                            # Every stage but fresh reuse receives the notes of the stages before it in its revision.
                            contexts={stage:json.loads((Path(next(a for a in assignments if a['stage']==stage)['workspace'])/'task-context.json').read_text())
                                      for stage in ('probe','ground','emit','reuse')}
                            for stage,before in (('probe',['acquire','interpret']),('ground',['acquire','interpret','probe']),
                                                 ('emit',['acquire','interpret','probe','ground'])):
                                self.assertEqual([n['stage'] for n in contexts[stage]['prior_notes']],before)
                            self.assertIn({'stage':'probe','revision':0,'notes':probe['notes']['inline']},contexts['ground']['prior_notes'])
                            self.assertNotIn('prior_notes',contexts['reuse'])
                            self.assertNotIn('EVALUATOR SENTINEL',json.dumps(contexts))
                        self.assertEqual(len(peers),4 if mode=='recover-repeat' else 2 if mode in ('recover-write','recover-variant','recover-budget') else 1)
                        if mode=='recover-write':
                            self.assertTrue(peers[0].closed)
                            self.assertEqual(peers[0].device_commands.count('D'),1)
                            self.assertEqual(result['emulator_recovery_count'],1)
                            self.assertEqual(result['emulator_recoveries'][0].get('revision'),1)
                            self.assertEqual(result['emulator_recoveries'][0].get('repairs'),1)
                            self.assertFalse(result['uncertain_effect'])
                            old=next(a for a in assignments if a['stage']=='probe')
                            with self.assertRaisesRegex(ValueError,'inactive'):
                                controller.call('tools',{**run,'assignment_id':old['id']})
                            history=next(a for a in assignments if a['stage']=='interpret' and a['revision']==1)
                            context=json.loads((Path(history['workspace'])/'REPAIR_CONTEXT.json').read_text())
                            self.assertIn('live-observations.jsonl',context['history'][-1]['files'])
                            preserved=Path(history['workspace'])/context['history'][-1]['files']['live-observations.jsonl']
                            failed=[json.loads(line) for line in preserved.read_text().splitlines() if json.loads(line)['operation']=='disarm']
                            self.assertEqual(len(failed),1)
                            self.assertEqual(failed[0]['result']['error']['fault'],'model')
                            self.assertTrue(failed[0]['result']['transcript'])
                        if mode=='recover-variant':
                            # The write ran once, on the replaced emulator; the replacement, the cleared effect and the
                            # probe handoff committed together, and with no difference from the assigned model nothing routed.
                            self.assertTrue(peers[0].closed)
                            self.assertEqual(peers[0].device_commands.count('D'),1)
                            self.assertEqual(result['emulator_recovery_count'],1)
                            self.assertEqual((result['emulator_recoveries'][0].get('revision'),result['emulator_recoveries'][0].get('repairs')),(0,0))
                            self.assertFalse(result['uncertain_effect'])
                            with controller._db() as db:
                                self.assertEqual(db.execute('SELECT COUNT(*) FROM pending_effects WHERE run_id=?',(run['run_id'],)).fetchone()[0],0)
                            probe=next(h for h in handoffs if h['stage']=='probe')
                            self.assertIsNone(probe.get('route'));self.assertFalse(probe['notes']['inline']['configurator']['device_diff'])
                            self.assertEqual(probe['notes']['inline']['outcome']['kind'],'advance')
                            accepted_probe=next(e for e in events if e['kind']=='stage.accepted' and e['data']['stage']=='probe')
                            recovered=[e for e in events if e['kind']=='emulator.recovered']
                            grounding=next(e for e in events if e['kind']=='stage.assigned' and e['data']['stage']=='ground')
                            self.assertEqual(len(recovered),1)
                            self.assertEqual(recovered[0]['data']['assignment_id'],accepted_probe['data']['assignment_id'])
                            self.assertLess(accepted_probe['id'],recovered[0]['id']);self.assertLess(recovered[0]['id'],grounding['id'])
                            self.assertFalse([e for e in events if e['kind']=='emulator.recovery_failed'])
                        if mode in ('recover-budget','recover-repeat'):
                            # Every failed write ran once and was never re-executed; each recovery closed the old
                            # emulator and committed with its probe handoff, claiming the verified replacement's binding.
                            # The last one came with the repair budget spent: the handoff advances with the best
                            # revision, nothing routes, and the effect is cleared, so the run goes on to fresh reuse.
                            recoveries=len(peers)-1
                            for old in peers[:-1]:
                                self.assertTrue(old.closed)
                                self.assertEqual(old.device_commands.count('D'),1)
                            self.assertEqual(peers[-1].device_commands.count('D'),0)
                            self.assertEqual(result['emulator_recovery_count'],recoveries)
                            self.assertEqual([(r.get('revision'),r.get('repairs')) for r in result['emulator_recoveries']],
                                             [(1,1),(2,2),(2,2)] if mode=='recover-repeat' else [(0,0)])
                            self.assertFalse(result['uncertain_effect'])
                            with controller._db() as db:
                                self.assertEqual(db.execute('SELECT COUNT(*) FROM pending_effects WHERE run_id=?',(run['run_id'],)).fetchone()[0],0)
                            self.assertFalse([e for e in events if e['kind']=='emulator.recovery_failed'])
                            started=[e for e in events if e['kind']=='emulator.recovery_started']
                            recovered=[e for e in events if e['kind']=='emulator.recovered']
                            self.assertEqual(len(started),recoveries);self.assertEqual(len(recovered),recoveries)
                            probes=[h for h in handoffs if h['stage']=='probe']
                            self.assertEqual([r['data']['assignment_id'] for r in recovered],[h['assignment_id'] for h in probes])
                            for entry in recovered:
                                accepted_probe=next(e for e in events if e['kind']=='stage.accepted' and e['data']['assignment_id']==entry['data']['assignment_id'])
                                following=next(e for e in events if e['kind']=='stage.assigned' and e['id']>entry['id']
                                               and e['data']['stage'] in ('probe','ground'))
                                # Recorded in the handoff's own transaction, right after it, never apart from it.
                                self.assertEqual(entry['id'],accepted_probe['id']+1)
                                self.assertEqual(following['data']['binding'],entry['data']['receipt']['binding'])
                            last=probes[-1]['notes']['inline']
                            self.assertIsNone(probes[-1].get('route'))
                            self.assertEqual(last['outcome'],{'kind':'advance','reason':'Repair budget exhausted; continuing with best revision'})
                            self.assertEqual({d['source'] for d in last['configurator']['device_diff']},{'interface_execute'})
                            self.assertIn('disarm',json.dumps(last['configurator']['device_diff']))
                        live={'recover-write':[False,True,True,False,False,True,True,True],'recover-variant':[False,True,True,False],
                              'recover-budget':[False,True,True,False],'recover-repeat':[False,True,True,False]*3,
                              'repair-decoder':[False,False,False,False,True,True],'invalid-model':[False,False,False,False,True,True],
                              'repair-ground':[False,True,True,False,False,True,True],
                              'concurrent-calls':[False,True,True,True,True]}.get(mode,[False,True,True])
                        self.assertEqual([entry[1] for entry in worker_live],live)
                        self.assertTrue(all(not entry[2] for entry in worker_live));self.assertFalse(peers[0].paused_requests)
                        reuse=assignments[-1];self.assertEqual(len(reuse['inputs']),1)
                        self.assertEqual(Path(next(iter(reuse['inputs']))).name,'package')
                        run_dir=root/'home/runs'/run['run_id'];state=json.loads((run_dir/'benchmark/state.json').read_text())
                        self.assertNotIn('stage_verdicts',state);self.assertNotIn('diagnostic',state)
                        # The harness holds the device: the run state records no session, and no startup reading.
                        self.assertNotIn('startup_observation',state)
                        self.assertNotIn('session',state);self.assertNotIn('previous_session',state)
                        sidecar=run_dir/'benchmark/run-evidence.enc'
                        sealed=unlock(sidecar,password.read_text(),state['final_evaluation']['evidence_sha256'])
                        self.assertEqual(state['final_evaluation']['verdict'],'unscored')
                        self.assertEqual(sealed['evaluations'],[])
                        self.assertEqual(len(sealed['accepted_gates']),len([h for h in handoffs if h['artifacts']]))
                        self.assertEqual(sealed['accepted_gates'][-1]['stage'],'reuse')
                        # Recovery proofs stay in the provider's private journal, never in the run directory.
                        self.assertNotIn('emulator_recoveries',sealed);self.assertFalse(list(run_dir.rglob('journal.jsonl')))
                        self.assertFalse(list((root/'home').rglob('journal.jsonl')))
                        journal=[json.loads(line) for line in (trial/'device/journal.jsonl').read_text().splitlines()]
                        self.assertEqual(journal[-1]['kind'],'stopped')
                        proofs=[entry['proof'] for entry in journal if entry['kind']=='replaced']
                        self.assertEqual(len(proofs),result['emulator_recovery_count'])
                        if mode in ('recover-write','recover-variant','recover-budget','recover-repeat'):
                            for index in range(result['emulator_recovery_count']):
                                proof=proofs[index]
                                receipt=result['emulator_recoveries'][index]['receipt']
                                self.assertEqual(hashlib.sha256(json.dumps(proof,sort_keys=True).encode()).hexdigest(),receipt['proof_sha256'])
                                self.assertEqual(proof['new_startup_observation']['values'],proof['old_startup_observation']['values'])
                                self.assertTrue(proof['unresolved_evidence_ids'])
                                self.assertEqual(proof['unresolved_dispatch_ids'],result['emulator_recoveries'][index]['unresolved_dispatch_ids'])
                                started=next(e for e in events if str(e['id'])==proof['unresolved_dispatch_ids'][0])
                                finished=next(e for e in events if str(e['id'])==proof['unresolved_evidence_ids'][0])
                                self.assertTrue(started['data']['effectful'])
                                self.assertEqual(finished['data']['dispatch_id'],str(started['id']))
                        self.assertTrue(all(Path(a['path']).resolve().is_relative_to((run_dir/'accepted').resolve())
                                            for a in sealed['accepted_artifacts']))
                        self.assertNotIn('maintenance',sealed)
                        from generative_driver.reporting import report_run
                        from generative_driver.benchmark_support.suite_reporting import _recorded_success
                        from generative_driver.benchmark import score
                        with patch('generative_driver.client.call',side_effect=lambda method,params,**kw:controller.call(method,params)):
                            report=report_run(run['run_id'],home=root/'home')
                        # A driver awaiting its independent grade is never counted as a success.
                        self.assertEqual(report['verdict'],'unscored')
                        self.assertFalse(_recorded_success(result,events,report))
                        # Nothing graded it: offline scoring of a pending assistive run reads unscored, never failed.
                        self.assertEqual(report['progress'].get('protocol'),'assistive/1')
                        scored=score(report,password,evidence_path=sidecar)
                        self.assertEqual((scored['verdict'],scored['final_evaluation']['verdict']),('unscored','unscored'))
                        self.assertNotIn('maintenance',report)
                        with self.assertRaisesRegex(ValueError,'inactive'):controller.call('tools',{**run,'assignment_id':assignments[0]['id']})
                        from generative_driver.benchmark_support.emulated_evidence import finalize
                        for kept in (handoffs[:1],handoffs):
                            finalize('tq9-v2',run_dir,kept,{'evaluator_password_file':str(password)})
                            latest=json.loads((run_dir/'benchmark/state.json').read_text())
                            updated=unlock(sidecar,password.read_text(),latest['final_evaluation']['evidence_sha256'])
                            self.assertEqual(len(updated['accepted_gates']),len([h for h in kept if h['artifacts']]))
                finally:
                    controller.close();server.shutdown();server.server_close()
                    if endpoint is not None:endpoint.close()
                    for peer in peers:
                        if not getattr(peer,'closed',False):peer.close()

    def test_measured_calibration_checks_code_and_complete_reference_mutant_coverage(self):
        from test_benchmark_family_calibration import AdmissionTests
        from generative_driver.benchmark_support.reference_calibration import validate_record
        import copy
        manifest,record=AdmissionTests().fixture()
        manifest.update(id='tq9-v2',family='tq9')
        record.update(case='tq9-v2')
        names=['wrong-scale','constant-output','wrong-state','unsigned-temperature','wrong-temperature-scale',
               'wrong-temperature-unit','wrong-temperature-byteorder','wrong-crc','wrong-duty-effect']
        template=copy.deepcopy(record['mutants'][0]); measured=copy.deepcopy(record['runs'][4])
        record['runs']=record['runs'][:4]; record['mutants']=[]
        for name in names:
            row=copy.deepcopy(template);row.update(id=name,run=name)
            run=copy.deepcopy(measured);run.update(id=name)
            record['mutants'].append(row);record['runs'].append(run)
        record['required_mutants']=names
        record['execution_inputs']={r['id']:{k:r[k] for k in ('model_sha256','capabilities_sha256')} for r in record['runs']}
        record['input_hashes'].pop('inventory');record['input_hashes']['analysis']='a'*64
        self.assertTrue(validate_record(manifest,record)['ok'])
        for change in ('code','reference','mutant','reason','backend','executed-model','executed-capabilities','diagnostic','uncovered-run'):
            bad=copy.deepcopy(record)
            if change=='code':bad['evaluator_identity']['sha256']='f'*64
            elif change=='reference':bad['references'].pop()
            elif change=='mutant':bad['mutants'].pop()
            elif change=='reason':bad['runs'][-1]['checks'][0]['reason']='host failure'
            elif change=='executed-model':bad['runs'][0]['model_sha256']='f'*64
            elif change=='executed-capabilities':bad['runs'][0]['capabilities_sha256']='f'*64
            elif change=='diagnostic':bad['runs'][0]['phase']='final'
            elif change=='uncovered-run':bad['runs'].append({**bad['runs'][0],'id':'extra'})
            else:bad['backend']='scripted TCP fixture'
            with self.subTest(change=change):self.assertFalse(validate_record(manifest,bad)['ok'])

    def tq9_admission_fixture(self):
        from test_benchmark_family_calibration import AdmissionTests
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import require_calibration
        from generative_driver.benchmark_support.reference_calibration import input_hashes, tq9_execution_inputs, CORE_MUTANTS
        from generative_driver.benchmark_support.snapshots import canonical_digest
        import copy
        manifest,record=AdmissionTests().fixture()
        manifest.update(id='tq9-v2',family='tq9',schema='benchmark-case/2')
        record['case']='tq9-v2'
        model={'operations':{'temp':{'outputs':{'value':{'scale':1,'unit':'degC'}}},'arm':{'toy':1},'disarm':{'toy':0}}}
        caps={'tasks':{'temperature':{'operation':'temp','outputs':{'temperature':{'output':'value','unit':'degC'}}},
                       'arm':{'operation':'arm','outputs':{}},'disarm':{'operation':'disarm','outputs':{}}}}
        names=['unsigned-temperature','wrong-temperature-scale','wrong-temperature-unit',
               'wrong-temperature-byteorder','wrong-crc','wrong-duty-effect']
        truth={key:{} for key in ('source_hashes','contracts','recipe','build','analysis')}
        truth['images']=record['images']
        truth['references']={name:{'model':{**copy.deepcopy(model),'toy_reference':name},'capabilities':copy.deepcopy(caps)}
                             for name in ('reference-a','reference-b')}
        truth['mutations']={'required':names,'mutations':[{'id':name,'patch':[
            {'op':'replace','path':'/operations/temp/outputs/value/scale','value':7}]} for name in names]}
        template=copy.deepcopy(record['runs'][4]);mutant=copy.deepcopy(record['mutants'][0])
        record['runs']=record['runs'][:4];record['mutants']=[]
        for ref in record['references']:
            old=ref['id'];ref['id']='reference-'+old
            ref['model_sha256']=canonical_digest(truth['references'][ref['id']]['model'])
            ref['runs']=['reference-'+name for name in ref['runs']]
        for run in record['runs']:run['id']='reference-'+run['id']
        for name in [*CORE_MUTANTS,*names]:
            run_id=name+'-original' if name in CORE_MUTANTS else name
            record['runs'].append({**copy.deepcopy(template),'id':run_id})
            record['mutants'].append({**copy.deepcopy(mutant),'id':name,'run':run_id})
        record['required_mutants']=[*CORE_MUTANTS,*names]
        record['execution_inputs']=tq9_execution_inputs(truth)
        for run in record['runs']:run.update(record['execution_inputs'][run['id']])
        for ref in record['references']:ref['execution_inputs']={name:copy.deepcopy(record['execution_inputs'][name]) for name in ref['runs']}
        for row in record['mutants']:row['execution_input']=copy.deepcopy(record['execution_inputs'][row['run']])
        from generative_driver.benchmark_support.tq9_v2 import contract
        phases={phase:{'temperature_vectors':[{'stimulus':value,'expected':value,'absolute_tolerance':0}],
            'effect_vectors':[{'task':'arm','inputs':{}},{'task':'set_duty','inputs':{'duty':200}},
                              {'task':'disarm','inputs':{}}]} for phase,value in [('diagnostic',-7),('final',29)]}
        truth['contracts']={'units':{'temperature':'degC','duty':'permille'},'scenarios':{'original':{'phases':phases}}}
        intended=['final/temperature-0/temperature']
        truth['mutations']['expected_failed_checks']={row['id']:list(intended) for row in record['mutants']}
        for row in record['mutants']:
            row['failed_checks']=list(intended);row['expected_failed_checks']=list(intended)
        for run in record['runs']:
            if not run['id'].startswith('reference-'):
                run.update(phase='final',package_sha256='c'*64)
            scoring=contract({'scenario_id':'original','case_seed':0,'revision':0},
                {**truth,'artifact_sha256':run['package_sha256'] or run['model_sha256']},run['phase'])
            run['contract_sha256']=canonical_digest(scoring)
            run['check_ids']=[check['id'] for check in scoring['checks']]
            run['checks']=[{'id':identifier,'passed':True} if run['passed'] or identifier not in intended
                else {'id':identifier,'passed':False,'reason':'value mismatch'} for identifier in run['check_ids']]
        record['input_hashes']=input_hashes(truth)
        manifest['calibration']={'status':'passed','input_hashes':record['input_hashes'],'evidence_sha256':canonical_digest(record)}
        truth['calibration']=record
        pin={'case_id':'tq9-v2','scenario_id':'original','case_seed':0,'manifest':manifest,'calibration':manifest['calibration']}
        return manifest,record,truth,pin

    def test_tq9_admission_binds_every_executed_input_to_authenticated_definitions(self):
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import require_calibration
        from generative_driver.benchmark_support.snapshots import canonical_digest
        from generative_driver.benchmark_support.tq9_v2 import contract
        manifest,record,truth,pin=self.tq9_admission_fixture()
        with patch('generative_driver.benchmark_support.registry.pin_case',return_value=pin),patch(
             'generative_driver.benchmark_support.emulator.truth_for_case',return_value=truth):
            self.assertTrue(require_calibration(pin,{})['ok'])
            for run in record['runs']:
                for field in ('model_sha256','capabilities_sha256'):
                    old_contract=run['contract_sha256']
                    old=run[field];run[field]='f'*64;record['execution_inputs'][run['id']][field]='f'*64
                    for ref in record['references']:
                        if run['id'] in ref['execution_inputs']:ref['execution_inputs'][run['id']][field]='f'*64
                    for row in record['mutants']:
                        if row['run']==run['id']:row['execution_input'][field]='f'*64
                    run['contract_sha256']=canonical_digest(contract({'scenario_id':'original','case_seed':0,'revision':0},
                        {**truth,'artifact_sha256':run['package_sha256'] or run['model_sha256']},run['phase']))
                    manifest['calibration']['evidence_sha256']=canonical_digest(record)
                    with self.subTest(run=run['id'],field=field),self.assertRaisesRegex(ValueError,'Calibration'):
                        require_calibration(pin,{})
                    run['contract_sha256']=old_contract
                    run[field]=old;record['execution_inputs'][run['id']][field]=old
                    for ref in record['references']:
                        if run['id'] in ref['execution_inputs']:ref['execution_inputs'][run['id']][field]=old
                    for row in record['mutants']:
                        if row['run']==run['id']:row['execution_input'][field]=old

    def test_tq9_admission_binds_check_coverage_to_authenticated_phase_contracts(self):
        import copy
        from unittest.mock import patch
        from generative_driver.benchmark_support.registry import require_calibration
        from generative_driver.benchmark_support.snapshots import canonical_digest
        from generative_driver.benchmark_support.tq9_v2 import contract
        for defect in ('inventory','contract','order','intended-failure'):
            manifest,record,truth,pin=self.tq9_admission_fixture()
            with patch('generative_driver.benchmark_support.registry.pin_case',return_value=pin),patch(
                 'generative_driver.benchmark_support.emulator.truth_for_case',return_value=truth):
                self.assertTrue(require_calibration(pin,{})['ok'])
                indices=range(len(record['runs'])) if defect in ('contract','order') else (0,1,2,3) if defect=='inventory' else (4,)
                for index in indices:
                    saved=copy.deepcopy(record);run=record['runs'][index]
                    if defect=='inventory':
                        run['checks']=[{'id':'unrelated/trivial','passed':True}];run['check_ids']=['unrelated/trivial']
                        run['contract_sha256']=canonical_digest({'schema':'benchmark-behavior/1',
                            'artifact_sha256':run['package_sha256'] or run['model_sha256'],'checks':[{'id':'unrelated/trivial'}]})
                    elif defect=='contract':
                        run['contract_sha256']=canonical_digest(contract({'scenario_id':'original','case_seed':0,'revision':0},
                            {**truth,'artifact_sha256':'f'*64},run['phase']))
                    elif defect=='order':run['check_ids'].reverse()
                    else:
                        row=next(row for row in record['mutants'] if row['run']==run['id'])
                        target='final/denied/duty'
                        row['expected_failed_checks']=[target];row['failed_checks']=[target]
                        run['checks']=[{'id':identifier,'passed':True} if identifier!=target else
                            {'id':identifier,'passed':False,'reason':'value mismatch'} for identifier in run['check_ids']]
                    run['evidence_sha256']=canonical_digest({k:v for k,v in run.items() if k!='evidence_sha256'})
                    manifest['calibration']['evidence_sha256']=canonical_digest(record)
                    with self.subTest(defect=defect,run=run['id']),self.assertRaisesRegex(ValueError,'Calibration'):
                        require_calibration(pin,{})
                    record.clear();record.update(saved)
                    manifest['calibration']['evidence_sha256']=canonical_digest(record)

    def test_reference_gate_refuses_structurally_invalid_mutant_before_native_start(self):
        # Catches recording a malformed model as a measured behavioral rejection.
        from generative_driver.benchmark_support.emulated import reference_execute
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError,'structurally valid'):
                reference_execute({'scenario_id':'original'}, {}, {'schema':'invalid'}, {}, renode='/absent',image='/absent',output_dir=Path(temp))

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
            _private(root,options,{'evaluations':[{'phase':'final','revision':0}]})
            with patch('generative_driver.benchmark_support.emulated._session',side_effect=AssertionError('device accessed')):
                with self.assertRaisesRegex(ValueError,'cannot be replayed'):
                    _final('tq9-v2',root,root/'absent',{},options)

    def test_fresh_reuse_acknowledgements_do_not_substitute_for_a_reading(self):
        # Public adapter seam: actuator ACK outputs alone do not finish the mission, which is a note, never a stop.
        from generative_driver.benchmark_support.cases import _write
        from tq9_workflow_fixture import capabilities
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);caps=root/'caps.json';caps.write_text(json.dumps(capabilities()))
            _write(root/'benchmark/state.json',{'package_attempts':{'reuse':'new'},
                'capabilities_path':str(caps),'package_copies':{'reuse':str(root/'absent')}})
            rows=[{'actor':'worker','stage':'reuse','attempt_id':'new','revision':0,
                   'operation':'set_duty' if duty else 'disarm','result':{'ok':True,'outputs':{'ack':1}},
                   'observation':{'duty':duty}} for duty in (370,0)]
            def mission():
                (root/'benchmark/package-events.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
                result=observe_stage('tq9-v2','reuse',root,root,{'status':'completed'})
                for key in ('ok','fault','route','stop','final_evaluation','evaluator'):self.assertNotIn(key,result)
                return next(c for c in result['checks'] if c['name']=='fresh_worker_mission')
            noted=mission()
            self.assertIs(noted['passed'],False)
            self.assertIn('reading',noted['reason'])
            self.assertEqual(noted.get('fault'),'model')
            for fault in ('host','operator'):
                rows[0]['result']={'ok':False,'error':{'fault':fault}}
                self.assertEqual(mission().get('fault'),fault)

    def test_reuse_failed_calls_need_trusted_attribution_before_model_blame(self):
        # The mission note attributes failed package calls; untyped or mixed evidence blames nobody.
        from unittest.mock import patch
        from generative_driver.benchmark_support import emulated
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
                        checked=emulated.observe_stage('tq9-v2','reuse',root,root,{'status':'completed'})
                    self.assertNotIn('stop',checked);self.assertNotIn('fault',checked)
                    noted=next(c for c in checked['checks'] if c['name']=='fresh_worker_mission')
                    self.assertIs(noted['passed'],False);self.assertEqual(noted.get('fault'),want)

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
                spec={'goal':'scripted no-effects fixture','case':'tq9-v2','case_options':{'scenario_id':'original'},
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

class ObservationTests(unittest.TestCase):
    """Truth-free stage observation: worker output is a note, only changed recorded bytes or the host stop."""
    def test_missing_candidate_artifacts_are_notes_and_an_empty_handoff(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(emulated,'_inputs',return_value=(root,{'images':{'firmware.bin':'a'*64}})):
                for stage,name in (('acquire','imported_firmware_hash'),('probe','capability_mapping'),('emit','package_integrity_and_replay')):
                    with self.subTest(stage=stage):
                        result=emulated.observe_stage('tq9-v2',stage,root,root,{'status':'completed'})
                        for key in ('ok','fault','route','stop'):self.assertNotIn(key,result)
                        self.assertEqual(result['artifacts'],[])
                        check=next(c for c in result['checks'] if c['name']==name)
                        self.assertFalse(check['passed']);self.assertTrue(check['reason'])

    def test_ground_observation_never_reads_an_evaluator_diagnostic(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);observed=[]
            for diagnostic in ({'passed':False},{'passed':True},{}):
                with self.subTest(diagnostic=diagnostic),patch.object(emulated,'_inputs',return_value=(root,{})), \
                     patch.object(emulated,'_state',return_value=(root/'state',{'diagnostic':diagnostic})):
                    result=emulated.observe_stage('tq9-v2','ground',root,root,{'status':'needs_revision',
                        'summary':'Decoded temperature contradicts the paired instrument reading'})
                    for key in ('ok','fault','route','stop','feedback'):self.assertNotIn(key,result)
                    observed.append(result)
            self.assertEqual(observed[0],observed[1]);self.assertEqual(observed[1],observed[2])

    def test_emit_failures_are_notes_and_only_a_host_fault_stops(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'manifest.json').write_text('{}')
            outcomes=[{'ok':False,'integrity_ok':False},
                {'ok':False,'integrity_ok':True,'runtime_current':True,'replay_status':'failed'},
                {'ok':False,'fault':'operator'},{'ok':False}]
            for result in outcomes:
                with self.subTest(result=result),patch.object(emulated,'_inputs',return_value=(root,{})), \
                     patch('generative_driver.toolkit.call_tool',return_value=result):
                    checked=emulated.observe_stage('tq9-v2','emit',root,root,{'status':'completed'})
                    for key in ('ok','fault','stop'):self.assertNotIn(key,checked)
                    self.assertEqual(checked['artifacts'],[])
                    check=next(c for c in checked['checks'] if c['name']=='package_integrity_and_replay')
                    self.assertFalse(check['passed']);self.assertEqual(len(check['rejected']),1)
            for result in ({'ok':False,'error':{'fault':'host'}},{'ok':False,'fault':'host','_exit':4},
                           {'ok':False,'error':'OSError: toy disk unavailable','_exit':1}):
                with self.subTest(result=result),patch.object(emulated,'_inputs',return_value=(root,{})), \
                     patch('generative_driver.toolkit.call_tool',return_value=result):
                    with self.assertRaisesRegex(RuntimeError,'could not run on this host'):
                        emulated.observe_stage('tq9-v2','emit',root,root,{'status':'completed'})

    def test_unreadable_acquisition_is_a_note_never_a_candidate_failure(self):
        from generative_driver.benchmark_support import emulated
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'provenance.json').write_text('{}');(root/'firmware.bin').write_bytes(b'toy')
            with patch.object(emulated,'_inputs',return_value=(root,{'images':{'firmware.bin':'a'*64}})), \
                 patch.object(Path,'read_bytes',side_effect=OSError('toy filesystem unavailable')):
                result=emulated.observe_stage('tq9-v2','acquire',root,root,{'status':'completed'})
            for key in ('ok','fault','stop'):self.assertNotIn(key,result)
            self.assertEqual(result['artifacts'],[])
            check=next(c for c in result['checks'] if c['name']=='imported_firmware_hash')
            self.assertFalse(check['passed']);self.assertIn('nreadable',check['reason'])

    def test_probe_hands_on_mapping_model_and_journal_and_notes_mapping_errors(self):
        # Structural notes only: the mapping, the adapted model's integrity and the live journal; no diagnostic.
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _write
        from generative_driver.benchmark_support.evaluate import adapt_model
        from generative_driver.configurator import IntegrityError
        from unittest.mock import patch
        from tq9_workflow_fixture import model, capabilities
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();ws=root/'worker';ws.mkdir();run=root/'run'
            (root/'original.json').write_text(json.dumps(model()))
            adaptation=adapt_model(root/'original.json',ws/'model')
            _write(run/'benchmark/state.json',{'adapted_model_dir':str(ws/'model'),'adaptation':adaptation,'revision':0})
            journal=run/'benchmark/live-observations-0.jsonl';journal.write_text('{"operation":"measure"}\n')
            def observed():
                with patch('generative_driver.benchmark_support.emulated_evidence._diagnose',side_effect=AssertionError('diagnostic')):
                    return emulated.observe_stage('tq9-v2','probe',run,ws,{'status':'completed'},[],{'observed_records':[]})
            mapping=capabilities();mapping['tasks'].pop('temperature')
            (ws/'capabilities.json').write_text(json.dumps(mapping))
            result=observed()
            for key in ('ok','fault','route','stop','feedback','evaluator'):self.assertNotIn(key,result)
            self.assertEqual({Path(p).name for p in result['artifacts']},
                             {'capabilities.json','model.json','adaptation.json','live-observations-0.jsonl'})
            check=next(c for c in result['checks'] if c['name']=='capability_mapping')
            self.assertFalse(check['passed']);self.assertIn('tasks.temperature',[e['path'] for e in check['errors']])
            for raw in ('null','[]'):
                (ws/'capabilities.json').write_text(raw)
                self.assertFalse(next(c for c in observed()['checks'] if c['name']=='capability_mapping')['passed'])
            (ws/'capabilities.json').write_text(json.dumps(capabilities()))
            self.assertTrue(next(c for c in observed()['checks'] if c['name']=='capability_mapping')['passed'])
            (ws/'model/model.json').write_text('{}')
            with self.assertRaisesRegex(IntegrityError,'changed'):observed()

    def test_a_mapping_against_a_wired_model_that_fails_validation_is_a_note_never_an_exception(self):
        # Catches the mapping check assuming a valid model: an interpreted model that fails validation still reaches
        # probe when the emulator can carry it, and the worker's mapping of it must stay a note, not a host stop.
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _write
        from generative_driver.benchmark_support.evaluate import adapt_model
        from generative_driver.toolkit import call_tool
        from tq9_workflow_fixture import model, capabilities
        def without_parameters(candidate):del candidate['operations']['measure']['parameters']
        def unstructured(candidate):candidate['operations']['arm']='not an operation'
        for name,damage,error in (('missing parameters',without_parameters,'KeyError'),('operation not an object',unstructured,'TypeError')):
            with self.subTest(model=name),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary).resolve();ws=root/'worker';ws.mkdir();run=root/'run'
                candidate=model();damage(candidate)
                (root/'original.json').write_text(json.dumps(candidate))
                adaptation=adapt_model(root/'original.json',ws/'model')
                self.assertIs(call_tool('model_validate',{'run_dir':str(root/'checks'),'model_dir':str(ws/'model')}).get('ok'),False)
                _write(run/'benchmark/state.json',{'adapted_model_dir':str(ws/'model'),'adaptation':adaptation,'revision':0})
                (ws/'capabilities.json').write_text(json.dumps(capabilities()))
                result=emulated.observe_stage('tq9-v2','probe',run,ws,{'status':'completed'},[],{'observed_records':[]})
                self.assertNotIn('stop',result)
                check=next(c for c in result['checks'] if c['name']=='capability_mapping')
                self.assertFalse(check['passed'])
                self.assertIn('The mapping cannot be checked against this model: '+error,check['reason'])
                self.assertIn('capabilities.json',{Path(p).name for p in result['artifacts']})

    def test_an_instrument_reading_after_a_refused_invalid_model_never_raises(self):
        # The runtime refuses a model that fails validation with a plain error string before opening the device; the
        # reading taken after that call is still device evidence and must not turn the refusal into a gateway error.
        from generative_driver.benchmark_support import tq9_v2
        raw={'values':{'compare':0,'reload':999}}
        refused={'ok':False,'error':"ValueError: invalid model: [{'where': 'operations.measure'}]",'transport_open_attempted':False}
        self.assertEqual(tq9_v2.observations(raw,refused,{}),{'refused_without_io':False,'duty':0})
        operator={'ok':False,'error':{'fault':'operator'}}
        self.assertIs(tq9_v2.observations(raw,operator,{})['refused_without_io'],True)

    def test_worker_facing_wording_names_device_evidence_and_promises_no_evaluator(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from generative_driver.agents import STAGE_BOUNDARY
        from generative_driver.configurator import BENCHMARK_PACKAGE_EXECUTE
        from generative_driver._toolchain.interpret import _prompt
        from generative_driver.benchmark_support.candidate_context import repair_sources
        from generative_driver.configurator import digest
        from tq9_workflow_fixture import model
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve();source=root/'accepted';source.mkdir()
            (source/'0-model.json').write_text(json.dumps(model()))
            package=root/'emitted/package';package.mkdir(parents=True);(package/'manifest.json').write_text('{}')
            accepted=[{'stage':'interpret','artifacts':[{'path':str(source/'0-model.json'),'sha256':digest(source/'0-model.json')}]},
                      {'stage':'emit','artifacts':[{'path':str(package),'sha256':digest(package)}]}]
            from generative_driver.benchmark_support.cases import _write
            _write(root/'run/benchmark/state.json',{'package_sha256':digest(package)})
            with patch('generative_driver.benchmark_support.emulated._session',return_value=SimpleNamespace(binding={'host':'127.0.0.1','port':1})):
                probe=prepare_stage('tq9-v2','probe',root/'run',root/'probe',accepted)
                reuse=prepare_stage('tq9-v2','reuse',root/'run',root/'reuse',accepted)
            context=root/'repair';context.mkdir()
            repair_sources(root/'run',context,[{'source':'probe_run','code':'output'}])
            policy=json.loads((context/'REPAIR_CONTEXT.json').read_text())['evidence_policy']
            texts={'boundary':STAGE_BOUNDARY,'interpret':_prompt(str(root),'agent'),'policy':policy,
                   'probe':probe['objective'],'reuse':reuse['objective'],'package':BENCHMARK_PACKAGE_EXECUTE['description']}
            for name,text in texts.items():
                with self.subTest(text=name):
                    self.assertNotRegex(text,r'(?i)diagnostic|evaluator observation|independent monitor|checks diagnostics')
                    if name!='reuse':self.assertIn('device evidence',text)
            self.assertIn('Do not read personal skills',STAGE_BOUNDARY)
            # The shared interpret prompt asserts no meaning for repair feedback: each repair context states its own.
            self.assertIn('says what that feedback is',texts['interpret'])
            self.assertNotRegex(texts['interpret'],r'(?i)feedback is what the device did|not a grade')

    def test_candidate_files_and_the_reuse_mapping_come_from_one_handoff(self):
        # Catches pairing one revision's replies, notes or mapping with another revision's files or model.
        from generative_driver.benchmark_support import emulated
        from generative_driver.benchmark_support.cases import _write
        from generative_driver.configurator import digest
        from types import SimpleNamespace
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary).resolve()
            def handoff(stage,revision,files):
                folder=root/'accepted'/str(revision)/stage;folder.mkdir(parents=True)
                artifacts=[]
                for index,(name,raw) in enumerate(files):
                    path=folder/f'{index}-{name}';path.write_text(raw)
                    artifacts.append({'path':str(path),'sha256':digest(path)})
                return {'stage':stage,'revision':revision,'artifacts':artifacts}
            first=handoff('interpret',0,[('model.json','{"a":0}'),('NOTES.md','first notes'),('replies.json','{"r":0}')])
            second=handoff('interpret',1,[('model.json','{"a":1}'),('replies.json','{"r":1}')])
            read=lambda files:{name:Path(path).read_text() for name,path in files.items()}
            self.assertEqual(read(emulated._candidate_files([first,second])),{'replies.json':'{"r":1}'})
            self.assertEqual(read(emulated._candidate_files([first,second],first['artifacts'][0]['sha256'])),
                             {'NOTES.md':'first notes','replies.json':'{"r":0}'})
            probed=handoff('probe',0,[('model.json','{"m":0}'),('capabilities.json','{"tasks":{}}')])
            reprobed=handoff('probe',1,[('model.json','{"m":1}')])
            self.assertIsNone(emulated._emitted_mapping([probed,reprobed]))
            self.assertEqual(emulated._emitted_mapping([probed,{'stage':'probe','revision':1,'artifacts':[]}])[0].read_text(),'{"tasks":{}}')
            # Reuse hands the mission note no mapping of a model other than the one emitted.
            package=root/'emitted/package';package.mkdir(parents=True);(package/'manifest.json').write_text('{}')
            emit={'stage':'emit','revision':1,'artifacts':[{'path':str(package),'sha256':digest(package)}]}
            _write(root/'run/benchmark/state.json',{'package_sha256':digest(package)})
            with patch.object(emulated,'_session',return_value=SimpleNamespace(binding={'host':'127.0.0.1','port':1})):
                emulated.prepare_stage('tq9-v2','reuse',root/'run',root/'reuse',[first,second,probed,reprobed,emit])
            self.assertIsNone(json.loads((root/'run/benchmark/state.json').read_text())['capabilities_path'])
