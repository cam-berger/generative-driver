"""The bench harness provides the device and holds the password; the configurator coordinates over a narrow device
handle. Synthetic TCP peers, scripted executors and a toy sealed truth only; never Renode or a real password."""
import ast
import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from test_device_provider import MONITOR, Process, RECIPE, keys

EVIDENCE = 'e' * 64
SOURCE = Path(__file__).resolve().parents[1] / 'src/generative_driver'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class Device:
    """A provided device for a test: Silicon peers behind a recipe-only provider, served on private endpoints."""

    def __init__(self, test, image, root, recipe=RECIPE):
        from tq9_workflow_fixture import Silicon
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        from generative_driver.benchmark_support.harness import DeviceServer
        self.peers = []
        def start(**kwargs):
            peer = Silicon(kwargs['image']); self.peers.append(peer); test.addCleanup(peer.close)
            session = peer.session(kwargs['recipe'], Path(root) / ('native-' + str(len(self.peers))))
            session.process = Process(peer)
            return session
        self.provider = DeviceProvider('tq9-v2', image, recipe, None, Path(root) / 'device', image_sha256=sha(image), start=start)
        self.provider.start()
        self.server = DeviceServer(self.provider, Path(root) / 'endpoint')
        test.addCleanup(self.server.close)


class DeviceEndpointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'firmware.bin').write_bytes(b'synthetic image')
        # Nothing the harness serves to the configurator unlocks the case truth.
        for target in ('generative_driver.benchmark_support.emulator.truth_for_case',
                       'generative_driver.benchmark_support.truth.unlock'):
            guard = patch(target, side_effect=AssertionError('truth unlocked while serving the device'))
            guard.start(); self.addCleanup(guard.stop)

    def test_the_control_endpoint_serves_the_device_and_withholds_owner_methods(self):
        from generative_driver.device_client import DeviceClient, DeviceUnavailable, open_device, reading
        device = Device(self, self.root / 'firmware.bin', self.root)
        descriptor = device.server.descriptor
        self.assertEqual(set(descriptor), {'schema', 'kind', 'provider_id', 'session_id', 'binding', 'image_sha256',
                                           'instrument', 'time_policy', 'control'})
        self.assertEqual(descriptor['control'], {'endpoint_file': str(self.root / 'endpoint/control.json')})
        for name in ('control', 'owner'):
            endpoint = json.loads((self.root / 'endpoint' / (name + '.json')).read_text())
            self.assertEqual(set(endpoint), {'family', 'address', 'authkey'})
            if os.name != 'nt':
                self.assertEqual((self.root / 'endpoint' / (name + '.json')).stat().st_mode & 0o777, 0o600)
        client = open_device(descriptor)
        self.assertEqual(client.status(), device.provider.status())
        observed = client.observe()
        self.assertEqual((observed['schema'], observed['session_id']), ('instrument-observation/1', descriptor['session_id']))
        self.assertFalse(keys(observed) & MONITOR)
        self.assertEqual(reading(observed), {'duty': 0.0, 'temperature_reference': 20.0, 'source': 'instrument',
                                             'units': {'duty': 'permille', 'temperature_reference': 'degC'}})
        # The device runs only inside the window, which closes when the call inside it raises too.
        with client.window():
            self.assertTrue(device.peers[0].running)
        self.assertFalse(device.peers[0].running)
        with self.assertRaisesRegex(RuntimeError, 'inside'):
            with client.window():
                raise RuntimeError('inside')
        self.assertFalse(device.peers[0].running)
        # Stop and shutdown are the harness's alone.
        for method in ('stop', 'shutdown'):
            with self.assertRaisesRegex(DeviceUnavailable, 'not served'):
                client._call(method)
        self.assertFalse(getattr(device.peers[0], 'closed', False))
        # A replacement is refused for a stale session before anything stops, and otherwise returns only the receipt.
        with self.assertRaisesRegex(DeviceUnavailable, 'inactive'):
            client.replace('stale', ['7'], ['6'], deadline_seconds=30)
        self.assertEqual(len(device.peers), 1)
        receipt = client.replace(descriptor['session_id'], ['7'], ['6'], deadline_seconds=30)
        self.assertEqual(set(receipt), {'schema', 'old_session_id', 'new_session_id', 'binding', 'old_owner_stopped',
                                        'initial_state_verified', 'observation_sha256', 'proof_sha256'})
        self.assertNotEqual(receipt['binding'], descriptor['binding'])
        self.assertTrue(device.peers[0].closed)
        proof = [json.loads(line) for line in (self.root / 'device/journal.jsonl').read_text().splitlines()][-1]['proof']
        self.assertEqual(hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest(), receipt['proof_sha256'])
        # Release gives up control of that session; the device stays up for the harness to stop.
        with self.assertRaisesRegex(DeviceUnavailable, 'does not hold'):
            client.release(descriptor['session_id'])
        client.release(receipt['new_session_id'])
        with self.assertRaisesRegex(DeviceUnavailable, 'released'):
            client.observe()
        self.assertFalse(getattr(device.peers[1], 'closed', False))
        owner = DeviceClient(device.server.endpoint_file('owner'))
        self.assertEqual(owner._call('stop'), {'stopped': True, 'confirmed': True})
        self.assertTrue(device.peers[1].closed)

    def test_an_unreachable_or_malformed_provider_is_device_unavailable(self):
        from generative_driver.device_client import DeviceClient, DeviceUnavailable, open_device
        device = Device(self, self.root / 'firmware.bin', self.root)
        descriptor = device.server.descriptor
        with self.assertRaisesRegex(ValueError, 'control endpoint'):
            open_device({**descriptor, 'control': {}})
        with self.assertRaisesRegex(ValueError, 'descriptor'):
            open_device({**descriptor, 'kind': 'physical'})
        # A wrong authkey is refused like an absent provider.
        forged = self.root / 'forged.json'
        forged.write_text(json.dumps({**json.loads((self.root / 'endpoint/control.json').read_text()), 'authkey': '00' * 32}))
        with self.assertRaises(DeviceUnavailable):
            DeviceClient(forged, timeout=5).status()
        client = open_device(descriptor)
        device.server.close()
        self.assertFalse(list((self.root / 'endpoint').glob('*.json')))
        for call in (client.status, client.observe, lambda: client.replace(descriptor['session_id'], deadline_seconds=1),
                     lambda: client.release(descriptor['session_id'])):
            with self.assertRaisesRegex(DeviceUnavailable, 'Device provider unavailable'):
                call()
        with self.assertRaises(DeviceUnavailable):
            with client.window():
                self.fail('The window opened without a provider')
        # Withdrawing the endpoint left the device itself alone.
        self.assertFalse(getattr(device.peers[0], 'closed', False))
        self.assertEqual(device.provider.stop(), {'stopped': True, 'confirmed': True})

    def test_the_device_client_imports_nothing_from_the_benchmark(self):
        tree = ast.parse((SOURCE / 'device_client.py').read_text(encoding='utf-8'))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {('.' * node.level) + (node.module or '') for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual(imported, {'contextlib', 'json', 'multiprocessing', 'multiprocessing.connection', 'pathlib'})


class StartAdmissionTests(unittest.TestCase):
    """The configurator admits an emulated run only with what the bench harness hands over, and never a password."""

    def setUp(self):
        from suite_fixtures import public_case_fixture
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manifest = public_case_fixture(self.root / 'resources', calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
        guard = patch('generative_driver.benchmark.case_root', return_value=self.root / 'resources')
        guard.start(); self.addCleanup(guard.stop)
        # The configurator no longer admits a case by unlocking truth: the harness does, before the run.
        for target in ('generative_driver.benchmark_support.registry.require_calibration',
                       'generative_driver.benchmark_support.emulator.truth_for_case'):
            refused = patch(target, side_effect=AssertionError('the configurator unlocked case truth'))
            refused.start(); self.addCleanup(refused.stop)
        from generative_driver.configurator import Controller
        self.controller = Controller(self.root / 'home')
        self.addCleanup(self.controller.close)

    def runs(self):
        with self.controller._db() as db:
            return db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]

    def test_the_configurator_refuses_a_password_or_the_emulator_executable(self):
        secret = self.root / 'private-password-handle'
        for spec in ({'goal': 'g', 'case': 'tq9-v2', 'effects': ['write'],
                      'case_options': {'scenario_id': 'original', 'evaluator_password_file': str(secret)}},
                     {'goal': 'g', 'case': {'id': 'tq9-v2', 'options': {'password': 'plaintext'}}, 'effects': ['write']},
                     {'goal': 'g', 'case': 'tq9-v2', 'effects': ['write'], 'case_options': {'Evaluator_Password_Files': {}}},
                     {'goal': 'g', 'case': 'tq9-v2', 'effects': ['write'], 'case_options': {'renode': '/opt/renode'}},
                     {'goal': 'g', 'case_options': {'evaluator_password_file': str(secret)}}):
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, 'password|emulator executable'):
                self.controller.start(spec)
        self.assertEqual(self.runs(), 0)

    def test_an_emulated_case_starts_only_with_the_harness_admission_token_and_device(self):
        from generative_driver.configurator import IntegrityError
        from test_configurator import wait_stopped
        device = Device(self, self.root / 'resources/cases/tq9-v2/firmware.bin', self.root)
        descriptor = device.server.descriptor
        good = {'goal': 'g', 'case': 'tq9-v2', 'effects': ['write', 'actuate'], 'case_options': {'scenario_id': 'original'},
                'executor_config': {'command': ['missing-runtime-b3']}, 'device': descriptor,
                'admission': {'calibration_evidence_sha256': EVIDENCE},
                'evaluation_token_sha256': hashlib.sha256(b'harness token').hexdigest()}
        without = lambda key: {k: v for k, v in good.items() if k != key}
        for spec, error, message in (
                (without('device'), ValueError, 'device descriptor'),
                ({**good, 'device': {**descriptor, 'control': {}}}, ValueError, 'control endpoint'),
                ({**good, 'admission': {'calibration_evidence_sha256': 'f' * 64}}, ValueError, 'admission'),
                (without('admission'), ValueError, 'admission'),
                (without('evaluation_token_sha256'), ValueError, 'evaluation token'),
                ({**good, 'evaluation_token_sha256': 'harness token'}, ValueError, 'evaluation token'),
                ({**good, 'device': {**descriptor, 'image_sha256': '0' * 64}}, IntegrityError, 'image'),
                ({'goal': 'g', 'device': descriptor}, ValueError, 'emulated benchmark case')):
            with self.subTest(message=message), self.assertRaisesRegex(error, message):
                self.controller.start(spec)
        self.assertEqual(self.runs(), 0)
        with self.controller._db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0], 0)
        run = self.controller.start(good)
        result = wait_stopped(self.controller, run)
        # The configured runtime is missing: an operator stop at acquire, before any device use.
        self.assertEqual((result['status'], result['outcome_category']), ('blocked', 'operator'))
        self.assertEqual(result['progress']['device'], {'session_id': descriptor['session_id'],
                                                         'binding': descriptor['binding'], 'receipts': []})
        self.assertEqual(result['progress']['protocol'], 'assistive/1')
        # The run released its control of the device and left it running for the harness to stop.
        self.assertTrue(device.provider.released)
        self.assertFalse(getattr(device.peers[0], 'closed', False))
        with self.controller._db() as db:
            spec = db.execute('SELECT spec FROM runs WHERE id=?', (run['run_id'],)).fetchone()['spec']
            self.assertEqual(db.execute('SELECT COUNT(*) FROM bindings').fetchone()[0], 0)
        self.assertNotIn('password', spec.lower())
        self.assertNotIn('renode', spec.lower())
        self.assertNotIn(json.loads((self.root / 'endpoint/control.json').read_text())['authkey'], spec)



HARNESS_FIXTURE = """
# Tests only: the bench harness's case resources, admission and device session factory. The device is a synthetic TCP
# peer in this harness process; the toy truth is unlocked with the password file the harness received on stdin.
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, TESTS)
from tq9_workflow_fixture import Silicon
peers = []
def start(**kwargs):
    peer = Silicon(kwargs['image']); peers.append(peer)
    session = peer.session(kwargs['recipe'], Path(PRIVATE) / ('native-' + str(len(peers))))
    class Process:
        def poll(self):
            return 0 if getattr(peer, 'closed', False) else None
    session.process = Process()
    return session
patch('generative_driver.benchmark.case_root', return_value=Path(RESOURCES)).start()
patch('generative_driver.benchmark_support.registry.require_calibration',
      return_value={'ok': True, 'evidence_sha256': EVIDENCE}).start()
patch('generative_driver.benchmark_support.native.NativeSession.start', side_effect=start).start()
"""


class HarnessTrialTests(unittest.TestCase):
    def test_the_harness_provides_the_device_and_the_configurator_holds_no_password(self):
        # A real detached harness process with a fake device and a scripted executor, against a configurator daemon:
        # the password file path reaches only the harness, which provides the device the run's live tools use.
        import http.server
        import secrets
        import shutil
        import threading
        import generative_driver
        from generative_driver import client, daemon
        from generative_driver.benchmark import run
        from generative_driver.benchmark_support.harness import read_trial
        from generative_driver.benchmark_support.truth import seal
        from generative_driver.configurator import PENDING
        from suite_fixtures import public_case_fixture
        from tq9_workflow_fixture import WORKER
        if os.name == 'nt':
            self.skipTest('The daemon endpoint fixture uses a Unix socket')
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            resources, home = root / 'resources', root / 'home'
            manifest = public_case_fixture(resources, calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
            password = root / 'password-handle'
            password.write_text('synthetic fixture credential')
            recipe = {'stimuli': {'temperature': {'minimum': -100, 'maximum': 100, 'command': 'temp {value}'}},
                      **RECIPE, 'reset_commands': ['reset']}
            truth = {'private_sentinel': 'EVALUATOR SENTINEL', 'images': manifest['images'], 'recipe': recipe,
                     'contracts': {'units': {'temperature': 'degC', 'duty': 'permille'}}}
            manifest['truth']['sha256'] = seal(truth, resources / manifest['truth']['path'], password.read_text())
            (resources / 'cases/tq9-v2/case.json').write_text(json.dumps(manifest))
            fixture = root / 'harness_fixture.py'
            fixture.write_text(HARNESS_FIXTURE.replace('TESTS', repr(str(Path(__file__).resolve().parent)))
                               .replace('PRIVATE', repr(str(root / 'native'))).replace('RESOURCES', repr(str(resources)))
                               .replace('EVIDENCE', repr(EVIDENCE)))
            # The configurator daemon, in this process: it never unlocks the case truth or admits the case itself.
            home.mkdir(mode=0o700)
            sockets = Path(tempfile.mkdtemp(prefix='gd-t-'))
            self.addCleanup(shutil.rmtree, sockets, True)
            (home / 'service.json').write_text(json.dumps({'family': 'AF_UNIX', 'address': str(sockets / 'owner.sock'),
                                                           'authkey': secrets.token_hex(32), 'pid': os.getpid()}))
            for target, value in (('generative_driver.benchmark.case_root', {'return_value': resources}),
                                  ('generative_driver.benchmark_support.registry.require_calibration',
                                   {'side_effect': AssertionError('the configurator admitted the case')}),
                                  ('generative_driver.benchmark_support.emulator.truth_for_case',
                                   {'side_effect': AssertionError('the configurator unlocked the case truth')})):
                guard = patch(target, **value); guard.start(); self.addCleanup(guard.stop)
            serving = threading.Thread(target=daemon.serve, args=(home,), daemon=True)
            serving.start()
            gateway = None
            try:
                until = time.monotonic() + 10
                while not client.call('ping', home=home, autostart=False).get('ok'):
                    self.assertLess(time.monotonic(), until); time.sleep(.05)
                # The scripted executor reaches the worker gateway through the daemon.
                class Gateway(http.server.BaseHTTPRequestHandler):
                    def do_POST(self):
                        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        response = client.call(request['method'], request['params'], home=home, autostart=False)
                        body = json.dumps(response).encode(); self.send_response(200); self.end_headers(); self.wfile.write(body)
                    def log_message(self, *args):
                        pass
                gateway = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Gateway)
                threading.Thread(target=gateway.serve_forever, daemon=True).start()
                script = root / 'scripted_worker.py'
                script.write_text(WORKER.replace('PACKAGE_PARENT', repr(str(Path(generative_driver.__file__).resolve().parents[1])))
                                  .replace('FIXTURE_PARENT', repr(str(Path(__file__).parent)))
                                  .replace('SERVICE_URL', repr('http://127.0.0.1:' + str(gateway.server_port))))
                (home / 'config.json').write_text(json.dumps({'executors': {'codex': {'command': [sys.executable, str(script)]}}}))
                renode = root / 'renode-executable-handle'
                with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
                    launched = run('tq9-v2', executor='codex', wait=True, options={
                        'home': str(home), 'evaluator_password_file': str(password), 'renode': str(renode),
                        'effects': ['write', 'actuate'], 'budget_seconds': 120, 'scenario_id': 'original'})
                trial = Path(launched['trial_dir'])
                log = (trial / 'harness.log').read_text(errors='replace') if (trial / 'harness.log').is_file() else ''
                self.assertEqual(launched['status'], 'awaiting-evaluation', (read_trial(trial), log))
                self.assertTrue(trial.is_relative_to(root / 'home-bench'))
                self.assertTrue(launched['run_id'] and launched['trial_id'] == trial.name)
                result = client.call('result', {'run_id': launched['run_id']}, home=home, autostart=False)
                self.assertEqual((result['status'], result['reason']), ('completed', PENDING), result)
                self.assertEqual(result['progress']['equivalence'], 'pending')
                record = read_trial(trial)
                self.assertEqual(result['progress']['device']['session_id'], record['device']['session_id'])
                self.assertEqual(record['device_stop'], {'stopped': True, 'confirmed': True})
                self.assertIs(record['sealed'], True)
                self.assertTrue((home / 'runs' / launched['run_id'] / 'benchmark/run-evidence.enc').is_file())
                # The device ran in the harness: its journal there records the start and the confirmed stop.
                journal = [json.loads(line) for line in (trial / 'device/journal.jsonl').read_text().splitlines()]
                self.assertEqual([entry['kind'] for entry in journal], ['started', 'stopped'])
                self.assertIs(journal[-1]['confirmed'], True)
                # Every live call of the run carried the instrument readings the harness served around it.
                events = client.call('events', {'run_id': launched['run_id']}, home=home, autostart=False)['events']
                live = [e['data'] for e in events if e['kind'] == 'tool.finished' and e['data'].get('connected')
                        and not e['data'].get('refused_before_open')]
                self.assertEqual({e['name'] for e in live}, {'interface_execute', 'probe_run', 'benchmark_package_execute'})
                for event in live:
                    self.assertEqual((event['result']['before_observation']['source'], event['result']['observation']['source']),
                                     ('instrument', 'instrument'))
                # Neither the password, its file path nor the emulator executable reached the configurator: not in
                # runs.sqlite3 nor in any other file under its home. The bench home never holds the password either.
                files = [path for path in home.rglob('*') if path.is_file()]
                self.assertIn(home / 'runs.sqlite3', files)
                for path in files:
                    content = path.read_bytes()
                    for secret in (str(password).encode(), password.read_bytes(), str(renode).encode()):
                        self.assertNotIn(secret, content, path)
                for path in (root / 'home-bench').rglob('*'):
                    if path.is_file():
                        self.assertNotIn(str(password).encode(), path.read_bytes(), path)
                        self.assertNotIn(password.read_bytes(), path.read_bytes(), path)
                self.assertNotIn('renode', json.dumps(record))
            finally:
                client._request(home, 'shutdown', {})
                serving.join(20)
                if gateway is not None:
                    gateway.shutdown(); gateway.server_close()


if __name__ == '__main__':
    unittest.main()
