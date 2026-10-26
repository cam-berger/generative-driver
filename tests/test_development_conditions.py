"""Public development inputs through the real device endpoint and worker gateway.

The sensor is a deterministic substitute. No evaluator password or final vectors
are needed to change an environmental input or observe a newly acquired sample.
"""
import json
import hashlib
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path

from test_device_provider import Raw, keys, MONITOR


RECIPE = {
    'stimuli': {'source_q4': {'command': 'private-source {value}', 'minimum': -8192, 'maximum': 8191}},
    'observations': {
        'latched_q4': {'command': 'private-latched', 'width': 16, 'count': 1},
        'acquisition_counter': {'command': 'private-counter', 'width': 32, 'count': 1}},
}
PANEL = {'low': -12.0, 'high': 32.0, 'zero': 0.0, 'fractional': 7.5, 'fine_fraction': 18.5625}


class Sensor(Raw):
    def __init__(self):
        super().__init__(RECIPE, {'latched_q4': 0, 'acquisition_counter': 0})
        self.source, self.stimuli = 0, []

    def stimulate(self, values):
        self.stimuli.append(dict(values))
        self.source = values['source_q4']
        return {**self.observe(), 'controls': [{'command': 'PRIVATE', 'response': 'PRIVATE'}]}

    def acquire(self):
        self.values['latched_q4'] = self.source & 65535
        self.values['acquisition_counter'] += 1


class DevelopmentConditionFixture(unittest.TestCase):
    def setUp(self):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        from generative_driver.benchmark_support.harness import DeviceServer
        from generative_driver.device_client import open_device
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        image = self.root/'firmware.bin'; image.write_bytes(b'synthetic sensor')
        self.sensor = Sensor()
        self.provider = DeviceProvider('sampled-sensor-v1', image, RECIPE, None, self.root/'private',
                                       start=lambda **kwargs: self.sensor)
        self.provider.start(); self.addCleanup(self.provider.stop)
        self.server = DeviceServer(self.provider, self.root/'endpoint'); self.addCleanup(self.server.close)
        self.client = open_device(self.server.descriptor)
        self.session = self.provider.session_id


class DevelopmentConditionsTests(DevelopmentConditionFixture):
    def test_fixed_panel_changes_source_but_never_acquires_or_claims_a_fresh_sample(self):
        catalog = self.server.descriptor['development_conditions']
        self.assertEqual({c['id']: c['reference']['value'] for c in catalog['conditions']}, PANEL)
        self.assertFalse(keys(catalog) & MONITOR)
        for epoch, (name, temperature) in enumerate(PANEL.items(), 1):
            before = self.client.observe()['values']
            receipt = self.client.select_condition(name, self.session)
            observed = self.client.observe()
            self.assertEqual(observed['values'], before)
            self.assertEqual(receipt['id'], name)
            self.assertEqual(receipt['epoch'], epoch)
            self.assertEqual(receipt['session_id'], self.session)
            self.assertEqual(observed['condition']['reference'], {'value': temperature, 'unit': 'degC'})
            self.assertIs(observed['condition']['sample_after_change'], False)
            with self.client.window():
                self.sensor.acquire()
            fresh = self.client.observe()
            self.assertEqual(fresh['values']['reference_temperature'], temperature)
            self.assertEqual(fresh['values']['monitor_sequence'], epoch)
            self.assertIs(fresh['condition']['sample_after_change'], True)
            self.assertFalse(keys(fresh) & MONITOR)
            self.assertNotIn('PRIVATE', json.dumps(fresh))
        private = (self.root/'private/journal.jsonl').read_text()
        self.assertIn('PRIVATE', private)

    def test_unknown_stale_active_and_released_condition_requests_make_no_changes(self):
        from generative_driver.device_client import DeviceUnavailable
        for name, session in [('unknown', self.session), ('low', 'stale')]:
            with self.assertRaises(DeviceUnavailable):
                self.client.select_condition(name, session)
        with self.client.window():
            with self.assertRaises(DeviceUnavailable):
                self.client.select_condition('low', self.session)
        self.client.release(self.session)
        with self.assertRaises(DeviceUnavailable):
            self.client.select_condition('low', self.session)
        self.assertEqual(self.sensor.stimuli, [])


class GatewayConditionFixture(DevelopmentConditionFixture):
    def setUp(self):
        super().setUp()
        from benchmark_family_fixtures import sample_reply, toy_sample_model
        from generative_driver.configurator import Controller
        sensor = self.sensor
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for request in self.rfile:
                    if request == b'ID\n':
                        reply = b'TOY-SAMPLE\n'
                    elif request == b'ACQUIRE\n':
                        sensor.acquire(); reply = b'OK\n'
                    elif request == b'SAMPLE\n':
                        value = sensor.values['latched_q4']
                        reply = sample_reply(value if value < 32768 else value - 65536,
                                             sensor.values['acquisition_counter'])
                    else:
                        raise AssertionError(request)
                    self.wfile.write(reply); self.wfile.flush()
        server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(close)
        sensor.info['binding'] = {'host': '127.0.0.1', 'port': server.server_address[1]}
        self.controller = Controller(self.root/'home'); self.addCleanup(self.controller.close)
        self.workspace = self.root/'worker'; self.workspace.mkdir()
        self.model_dir = self.workspace/'model'; self.model_dir.mkdir()
        model = toy_sample_model()
        model['operations']['read_current'] = {**model['operations']['sample'], 'effect': 'read',
                                               'steps': model['operations']['sample']['steps'][1:]}
        model['provenance'].append({'item': 'operations.read_current', 'source': 'public toy retained-read fixture'})
        (self.model_dir/'model.json').write_text(json.dumps(model))
        self.assignment = {'id': 'assignment', 'stage': 'probe', 'revision': 0, 'workspace': str(self.workspace),
            'inputs': {}, 'allowed_tools': ['interface_execute', 'probe_run'], 'effects': ['write'],
            'binding': sensor.binding}
        self.spec = {'device': self.server.descriptor, 'effects': ['write'],
                      '_case_pin': {'case_id': 'sampled-sensor-v1', 'execution': 'actual-agent-emulation'}}
        now = time.time()
        with self.controller._db() as db:
            db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                       ('run-fixture', 'request', json.dumps(self.spec), 'running', 'probe', now, now))
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                       ('assignment', 'run-fixture', 'probe', 'active', json.dumps(self.assignment), now))
            db.execute('INSERT INTO progress VALUES(?,?)', ('run-fixture', json.dumps({'revision': 0, 'device': {
                'session_id': self.session, 'binding': sensor.binding}})))

    def call(self, name='interface_execute', **arguments):
        return self.controller.call('tool', {'run_id': 'run-fixture', 'assignment_id': 'assignment', 'name': name,
            'arguments': {'model_dir': str(self.model_dir), 'operation': 'sample', **arguments}})


class GatewayConditionTests(GatewayConditionFixture):
    def test_gateway_live_calls_expose_fixed_conditions_and_keep_measurements_in_handoff_journal(self):
        contracts = self.controller.call('tools', {'run_id': 'run-fixture', 'assignment_id': 'assignment'})['tools']
        for tool in contracts:
            self.assertEqual(tool['inputSchema']['properties']['condition_id']['enum'], list(PANEL))
        for name, temperature in PANEL.items():
            result = self.call(condition_id=name)
            self.assertTrue(result['ok'], result)
            self.assertEqual(result['outputs']['temperature'], temperature)
            self.assertEqual(result['observation']['reference_temperature'], temperature)
            self.assertEqual(result['observation']['condition']['id'], name)
            self.assertIs(result['before_observation']['condition']['sample_after_change'], False)
            self.assertIs(result['observation']['condition']['sample_after_change'], True)
        rows = [json.loads(line) for line in (self.root/'home/runs/run-fixture/benchmark/live-observations-0.jsonl')
                .read_text().splitlines()]
        self.assertEqual([r['observation']['condition']['id'] for r in rows], list(PANEL))
        self.assertFalse(keys([r['observation'] for r in rows]) & MONITOR)
        self.assertFalse(self.controller.call('status', {'run_id': 'run-fixture'})['uncertain_effect'])

    def test_condition_selection_also_works_for_probe_run_and_retained_reads(self):
        first = self.call('probe_run', condition_id='low', n=1)
        self.assertTrue(first['ok'], first)
        self.assertEqual(first['results'][0]['outputs']['temperature'], -12)
        retained = self.call(operation='read_current', condition_id='high')
        self.assertEqual(retained['outputs']['temperature'], -12)
        self.assertEqual(retained['observation']['condition']['reference']['value'], 32)
        self.assertIs(retained['observation']['condition']['sample_after_change'], False)
        fresh = self.call()
        self.assertEqual(fresh['outputs']['temperature'], 32)
        self.assertIs(fresh['observation']['condition']['sample_after_change'], True)

    def test_unknown_replay_and_outstanding_effect_refuse_selection_before_source_changes(self):
        for arguments in ({'condition_id': 'arbitrary'}, {'condition_id': 'low', 'replay': 'not-a-live-call'}):
            with self.assertRaises(ValueError):
                self.call(**arguments)
        with self.controller._db() as db:
            db.execute('UPDATE runs SET uncertain=1 WHERE id=?', ('run-fixture',))
        with self.assertRaises(ValueError):
            self.call(operation='read_current', condition_id='low')
        self.assertEqual(self.sensor.stimuli, [])

    def test_reuse_and_other_cases_have_no_condition_argument_or_control(self):
        for stage, case_id in [('reuse', 'sampled-sensor-v1'), ('probe', 'tq9-v2')]:
            self.assignment['stage'] = stage
            self.spec['_case_pin']['case_id'] = case_id
            with self.controller._db() as db:
                db.execute('UPDATE runs SET stage=?,spec=? WHERE id=?', (stage, json.dumps(self.spec), 'run-fixture'))
                db.execute('UPDATE assignments SET stage=?,payload=? WHERE id=?',
                           (stage, json.dumps(self.assignment), 'assignment'))
            contracts = self.controller.call('tools', {'run_id': 'run-fixture', 'assignment_id': 'assignment'})['tools']
            self.assertTrue(all('condition_id' not in t['inputSchema']['properties'] for t in contracts))
            with self.assertRaises(ValueError):
                self.call(condition_id='low')
        self.assertEqual(self.sensor.stimuli, [])

    def test_stage_guidance_carries_catalog_without_equipment_coordinates(self):
        from unittest.mock import patch
        from generative_driver.configurator import case_options
        from generative_driver.benchmark_support.emulated import prepare_stage
        model = self.model_dir/'model.json'
        accepted = [{'stage': 'probe', 'artifacts': [{'path': str(model),
            'sha256': hashlib.sha256(model.read_bytes()).hexdigest()}]}]
        options = case_options({**self.spec, '_device': {'session_id': self.session, 'binding': self.sensor.binding}})
        with patch('generative_driver.benchmark_support.emulated._inputs', return_value=(self.root, {})):
            prepared = prepare_stage('sampled-sensor-v1', 'ground', self.root/'run', self.root/'ground',
                                     accepted=accepted, options=options)
        catalog = json.loads(Path(prepared['context']['development_conditions']).read_text())
        self.assertEqual({c['id']: c['reference']['value'] for c in catalog['conditions']}, PANEL)
        self.assertIn('condition_id', prepared['objective'])
        self.assertIn('acquisition', prepared['objective'])
        tasks = json.loads((self.root/'ground/TASKS.json').read_text())['tasks']
        self.assertEqual(tasks['measure']['outputs']['temperature']['instrument'], 'reference_temperature')
        self.assertEqual(tasks['measure']['outputs']['temperature']['absolute_tolerance'], 1e-9)
        self.assertIn(str(self.root/'ground/TASKS.json'), prepared['inputs'])
        self.assertNotIn('source_q4', json.dumps(prepared) + json.dumps(catalog))


if __name__ == '__main__':
    unittest.main()
