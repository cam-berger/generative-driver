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


def unstarted_provider(test, image, root, peers, recipe=RECIPE):
    """A recipe-only provider of Silicon peers that has not started its device yet."""
    from tq9_workflow_fixture import Silicon
    from generative_driver.benchmark_support.device_provider import DeviceProvider
    def start(**kwargs):
        peer = Silicon(kwargs['image']); peers.append(peer); test.addCleanup(peer.close)
        session = peer.session(kwargs['recipe'], Path(root) / ('native-' + str(len(peers))))
        session.process = Process(peer)
        return session
    return DeviceProvider('tq9-v2', image, recipe, None, Path(root) / 'device', image_sha256=sha(image), start=start)


class Device:
    """A provided device for a test: Silicon peers behind a recipe-only provider, served on private endpoints."""

    def __init__(self, test, image, root, recipe=RECIPE):
        from generative_driver.benchmark_support.harness import DeviceServer
        self.peers = []
        self.provider = unstarted_provider(test, image, root, self.peers, recipe)
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

    def test_a_provider_that_never_answers_is_device_unavailable_by_the_deadline(self):
        # Catches a client that waits forever to connect or authenticate while the provider is stuck on another
        # request: the harness serves one connection at a time and accepts nothing meanwhile.
        import secrets
        import shutil
        import threading
        from multiprocessing.connection import Listener
        from generative_driver.device_client import DeviceClient, DeviceUnavailable
        if os.name == 'nt':
            endpoint = {'family': 'AF_PIPE', 'address': r'\\.\pipe\gd-test-stuck-' + secrets.token_hex(8)}
        else:
            sockets = Path(tempfile.mkdtemp(prefix='gd-s-'))
            self.addCleanup(shutil.rmtree, sockets, True)
            endpoint = {'family': 'AF_UNIX', 'address': str(sockets / 'stuck.sock')}
        endpoint['authkey'] = secrets.token_hex(32)
        listener = Listener(endpoint['address'], family=endpoint['family'], authkey=bytes.fromhex(endpoint['authkey']))
        self.addCleanup(listener.close)
        (self.root / 'stuck.json').write_text(json.dumps(endpoint))
        client = DeviceClient(self.root / 'stuck.json', timeout=1)
        for name, request in (('status', client.status), ('observe', client.observe),
                              ('release', lambda: client.release('session'))):
            outcome = {}
            def attempt():
                try:
                    request()
                except Exception as error:
                    outcome['error'] = error
            caller = threading.Thread(target=attempt, daemon=True)
            began = time.monotonic()
            caller.start()
            caller.join(8)
            self.assertFalse(caller.is_alive(), name + ' waited past its deadline')
            self.assertIsInstance(outcome.get('error'), DeviceUnavailable, name)
            self.assertLess(time.monotonic() - began, 5, name)

    def test_the_device_client_imports_nothing_from_the_benchmark(self):
        tree = ast.parse((SOURCE / 'device_client.py').read_text(encoding='utf-8'))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imported |= {('.' * node.level) + (node.module or '') for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual(imported, {'contextlib', 'json', 'multiprocessing', 'multiprocessing.connection', 'pathlib',
                                        'threading'})


class TrialFinishTests(unittest.TestCase):
    """What the harness does once the run is over: stop the device, confirmed, and seal only what it read."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'firmware.bin').write_bytes(b'synthetic image')

    def test_the_harness_seals_only_a_run_it_saw_finish_and_whose_handoffs_it_read(self):
        # Catches sealing an empty or partial set of handoffs, and reporting that seal as good, or grading the run, when
        # the run's result could not be read (a transport failure, a reply over the read limit) or the run had not finished.
        from generative_driver.benchmark_support import evaluator, harness
        from generative_driver.device_client import DeviceClient
        (self.root / 'password-handle').write_text('synthetic fixture credential')
        handoffs = [{'stage': 'acquire', 'artifacts': []}]
        completed = {'ok': True, 'status': 'completed', 'stopping': False}
        running = {'ok': True, 'status': 'running', 'stopping': False}
        for case, observed, read, reason in (
                ('result unavailable', completed, {'ok': False, 'reason': 'Reply exceeds the read limit'},
                 'run result unavailable: Reply exceeds the read limit'),
                ('run resumed', completed, {**running, 'accepted_handoffs': handoffs}, 'run not terminal: running'),
                ('owner shutdown', running, {**running, 'accepted_handoffs': handoffs},
                 'run not terminal: the harness stopped waiting (owner shutdown)')):
            with self.subTest(case=case):
                trial = self.root / case.replace(' ', '-')
                peers, starts = [], []
                provider = unstarted_provider(self, self.root / 'firmware.bin', trial, peers)
                def configurator(method, params=None, home=None, **kwargs):
                    if method == 'ping':
                        return {'ok': True}
                    if method == 'start':
                        starts.append(params)
                        return {'ok': True, 'run_id': 'run-fixture'}
                    if method == 'status':
                        if case == 'owner shutdown':
                            DeviceClient(trial / 'endpoint/owner.json')._call('shutdown')
                        return observed
                    if method == 'result':
                        return read
                    raise AssertionError(method)
                with patch.object(harness, 'admit', return_value=(provider, {'calibration_evidence_sha256': EVIDENCE})), \
                        patch.object(evaluator, 'seal', side_effect=AssertionError('sealed what the harness did not read')) as sealing, \
                        patch.object(evaluator, 'grade', side_effect=AssertionError('graded what the harness did not read')) as grading, \
                        patch('generative_driver.client.call', side_effect=configurator):
                    record = harness.run_trial(trial, {'trial_id': trial.name, 'home': str(self.root / 'home'),
                                                       'case': 'tq9-v2', 'case_options': {'scenario_id': 'original'},
                                                       'password_file': str(self.root / 'password-handle')})
                sealing.assert_not_called(); grading.assert_not_called()
                self.assertIs(record['sealed'], False)
                self.assertEqual(record['sealing_failed'], reason)
                self.assertEqual(record['device_stop'], {'stopped': True, 'confirmed': True})
                self.assertTrue(peers[0].closed)
                self.assertEqual(harness.read_trial(trial), record)
                # With no request_id of its own, the run starts under the trial's, so a retried start reconnects.
                self.assertEqual((starts[0]['request_id'], record['request_id']), (trial.name, trial.name))

    def test_an_unconfirmed_device_stop_names_the_session_left_running(self):
        # Catches a trial record that names only the session the run started with, which a committed recovery
        # replaced, when the device the harness could not stop is the replacement.
        from generative_driver.benchmark_support.harness import finish
        peers = []
        provider = unstarted_provider(self, self.root / 'firmware.bin', self.root, peers)
        started = provider.start()['session_id']
        receipt = provider.replace(started, ['7'], ['6'])
        provider.session.stop = lambda: None  # The replacement never confirms its stop.
        done = finish(provider, 'tq9-v2', None, [], None, {'status': 'completed'})
        self.assertEqual(done['status'], 'stop-unconfirmed')
        self.assertIs(done['device_stop']['confirmed'], False)
        self.assertEqual((done['device']['session_id'], done['device']['binding']),
                         (receipt['new_session_id'], receipt['binding']))
        self.assertNotEqual(done['device']['session_id'], started)
        self.assertIs(done['device']['healthy'], True)
        self.assertFalse(getattr(peers[1], 'closed', False))


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
                     {'goal': 'g', 'case_options': {'evaluator_password_file': str(secret)}},
                     # Top-level request keys too: the whole request is kept as the run's spec.
                     {'goal': 'g', 'case': 'tq9-v2', 'effects': ['write'], 'evaluator_password_file': str(secret)},
                     {'goal': 'g', 'Renode': '/opt/renode'},
                     {'goal': 'g', 'password': 'plaintext'}):
            with self.subTest(spec=spec), self.assertRaisesRegex(ValueError, 'password|emulator executable'):
                self.controller.start(spec)
        self.assertEqual(self.runs(), 0)

    def test_a_suite_holding_a_password_file_or_the_emulator_executable_is_refused_before_anything_is_stored(self):
        # Catches the configurator freezing and keeping evaluator password file and emulator paths for a suite, whose
        # emulated children the bench harness provides and grades with them; and a suite no harness drives.
        from suite_fixtures import legacy_manifest
        secret = self.root / 'private-password-handle'
        (self.root / 'home/config.json').write_text(json.dumps({'executors': {'codex': {'command': ['scripted-runtime']}}}))
        manifest = legacy_manifest()
        manifest['entries'] = [{'case': 'tq9-v2', 'scenario': 'original', 'case_seed': 0}]
        token = hashlib.sha256(b'harness token').hexdigest()
        for options in ({'evaluator_password_files': {'tq9-v2': str(secret)}, 'renode': '/opt/renode-handle'},
                        {'renode': '/opt/renode-handle'}, {'Evaluator_Password_File': str(secret)}, {'password': 'plaintext'}):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'bench harness'):
                self.controller.call('suite_start', {'manifest': manifest, 'executor': 'codex', 'options': options,
                                                     'request_id': 'emulated-suite', 'harness_token_sha256': token})
        # The configurator schedules nothing itself: a suite no bench harness drives is refused.
        with self.assertRaisesRegex(ValueError, 'benchmark suite start'):
            self.controller.call('suite_start', {'manifest': manifest, 'executor': 'codex', 'options': {},
                                                 'request_id': 'emulated-suite'})
        with self.controller._db() as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'suite%'")]
            self.assertTrue(tables)
            for table in tables:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0], 0, table)
        for path in (self.root / 'home').rglob('*'):
            if path.is_file():
                self.assertNotIn(str(secret).encode(), path.read_bytes(), path)
                self.assertNotIn(b'renode-handle', path.read_bytes(), path)

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


LAUNCH_FIXTURE = """
# Tests only: a harness whose configurator refuses the run, or whose device cannot be verified nor stopped. The fake
# admission and session factory accept only absolute paths to existing files, as the real ones do from the trial dir.
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, TESTS_DIR)
from tq9_workflow_fixture import Silicon
from test_device_provider import RECIPE
peers = []
def start(**kwargs):
    if not Path(kwargs['renode']).is_absolute() or not Path(kwargs['renode']).is_file():
        raise ValueError('emulator executable not found: ' + str(kwargs['renode']))
    peer = Silicon(kwargs['image']); peers.append(peer)
    session = peer.session(kwargs['recipe'], Path(PRIVATE_DIR) / ('native-' + str(len(peers))))
    class Process:
        def poll(self):
            return 0 if getattr(peer, 'closed', False) else None
    session.process = Process()
    if FIXTURE_MODE == 'stop-unconfirmed':
        session.observe = lambda: {'values': {}, 'reads': []}
        session.stop = lambda: None
    return session
def admitted(pin, secret):
    path = Path(secret['evaluator_password_file'])
    if not path.is_absolute() or not path.is_file():
        raise ValueError('password file not found: ' + str(path))
    return {'ok': True, 'evidence_sha256': EVIDENCE_SHA}
def configurator(method, params=None, home=None, **kwargs):
    if method == 'ping':
        return {'ok': True}
    if method == 'start':
        return {'ok': False, 'reason': 'configurator refused the fixture run'}
    raise AssertionError(method)
patch('generative_driver.benchmark.case_root', return_value=Path(RESOURCES_DIR)).start()
patch('generative_driver.benchmark_support.registry.require_calibration', side_effect=admitted).start()
patch('generative_driver.benchmark_support.evaluator.case_truth', return_value={'recipe': RECIPE}).start()
patch('generative_driver.benchmark_support.native.NativeSession.start', side_effect=start).start()
patch('generative_driver.client.call', side_effect=configurator).start()
"""


class LaunchTests(unittest.TestCase):
    """`benchmark run` for an emulated case, up to the configurator start; a spawned harness with a fixture device."""

    def setUp(self):
        from suite_fixtures import public_case_fixture
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        public_case_fixture(self.root / 'resources', calibration={'status': 'passed', 'evidence_sha256': EVIDENCE})
        (self.root / 'password-handle').write_text('synthetic fixture credential')
        (self.root / 'renode-handle').write_text('')

    def launch(self, mode, **options):
        from generative_driver.benchmark_support.harness import launch
        fixture = self.root / 'launch_fixture.py'
        fixture.write_text(LAUNCH_FIXTURE.replace('TESTS_DIR', repr(str(Path(__file__).resolve().parent)))
                           .replace('PRIVATE_DIR', repr(str(self.root / 'native')))
                           .replace('RESOURCES_DIR', repr(str(self.root / 'resources')))
                           .replace('EVIDENCE_SHA', repr(EVIDENCE)).replace('FIXTURE_MODE', repr(mode)))
        with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
            return launch('tq9-v2', executor='codex', autostart=False, timeout=60,
                          options={'home': str(self.root / 'home'), 'effects': ['write', 'actuate'],
                                   'scenario_id': 'original', **options})

    def trial(self):
        from generative_driver.benchmark_support.harness import read_trial
        trials = list((self.root / 'home-bench/trials').iterdir())
        self.assertEqual(len(trials), 1)
        return trials[0], read_trial(trials[0])

    def test_relative_password_and_emulator_paths_resolve_against_the_caller(self):
        # Catches paths forwarded as given and then resolved in the trial directory, where the harness runs: the
        # case would be refused admission, or the emulator would not start.
        directory = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, directory)
        with self.assertRaisesRegex(ValueError, 'configurator refused the fixture run'):
            self.launch('refused', evaluator_password_file='password-handle', renode='renode-handle')
        trial, record = self.trial()
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['device_stop'], {'stopped': True, 'confirmed': True})
        journal = [json.loads(line)['kind'] for line in (trial / 'device/journal.jsonl').read_text().splitlines()]
        self.assertEqual(journal, ['started', 'stopped'])
        self.assertNotIn('password-handle', json.dumps(record))

    def test_a_claimed_request_whose_harness_never_started_reports_that_failure(self):
        # Catches a request_id claimed by a launch whose harness process could not start: a retry would otherwise wait
        # for a trial that never runs.
        from generative_driver.benchmark_support.harness import launch
        options = {'home': str(self.root / 'home'), 'effects': ['write', 'actuate'], 'scenario_id': 'original',
                   'request_id': 'unstarted-harness', 'evaluator_password_file': str(self.root / 'password-handle')}
        with patch('subprocess.Popen', side_effect=OSError('no interpreter')):
            with self.assertRaisesRegex(OSError, 'no interpreter'):
                launch('tq9-v2', executor='codex', autostart=False, timeout=5, options=dict(options))
        with self.assertRaisesRegex(ValueError, 'The bench harness did not start: no interpreter'):
            launch('tq9-v2', executor='codex', autostart=False, timeout=5, options=dict(options))
        trial, record = self.trial()
        self.assertEqual((record['status'], record['request_id']), ('failed', 'unstarted-harness'))

    def test_a_device_stop_left_unconfirmed_is_named_where_the_launch_fails(self):
        # Catches a launch that reports only the original failure while an emulator it could not stop may still run.
        with self.assertRaises(ValueError) as raised:
            self.launch('stop-unconfirmed', evaluator_password_file=str(self.root / 'password-handle'),
                        renode=str(self.root / 'renode-handle'))
        trial, record = self.trial()
        self.assertEqual(record['status'], 'stop-unconfirmed')
        message = str(raised.exception)
        self.assertIn('device stop was not confirmed', message)
        self.assertIn(str(trial), message)
        self.assertIn(record['device']['session_id'], message)
        self.assertIn(str(record['device']['binding']['port']), message)
        self.assertIn('emulator cleanup unverified', message)


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
        from generative_driver.benchmark_support.truth import seal, unlock
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
            # The final phase the harness grades the frozen driver with after the run, on a fresh emulator of its own.
            final = {'temperature_vectors': [{'stimulus': v, 'expected': v, 'absolute_tolerance': .01} for v in (-7, 29)],
                     'effect_vectors': [{'task': 'arm', 'inputs': {}}, {'task': 'set_duty', 'inputs': {'duty': 400}},
                                        {'task': 'disarm', 'inputs': {}}]}
            truth = {'private_sentinel': 'EVALUATOR SENTINEL', 'images': manifest['images'], 'recipe': recipe,
                     'contracts': {'units': {'temperature': 'degC', 'duty': 'permille'},
                                   'scenarios': {'original': {'phases': {'final': final}}},
                                   'time_policy': {'sample_settle_seconds': 0}}}
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
                request = {'home': str(home), 'evaluator_password_file': str(password), 'renode': str(renode),
                           'effects': ['write', 'actuate'], 'budget_seconds': 120, 'scenario_id': 'original',
                           'request_id': 'trial-request-fixture'}
                with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)}):
                    launched = run('tq9-v2', executor='codex', wait=True, options=dict(request))
                    # The same request reconnects to its trial: no second harness, device or run. Different inputs
                    # under that request_id are refused before anything starts.
                    again = run('tq9-v2', executor='codex', wait=True, options=dict(request))
                    with self.assertRaisesRegex(ValueError, 'request_id was already used with different inputs'):
                        run('tq9-v2', executor='codex', options={**request, 'budget_seconds': 121})
                self.assertEqual((again['trial_id'], again['run_id'], again['duplicate'], launched['duplicate']),
                                 (launched['trial_id'], launched['run_id'], True, False))
                self.assertEqual(again['status'], launched['status'])
                self.assertEqual([path.name for path in (root / 'home-bench/trials').iterdir()], [launched['trial_id']])
                trial = Path(launched['trial_dir'])
                self.assertEqual(read_trial(trial)['request_id'], 'trial-request-fixture')
                log = (trial / 'harness.log').read_text(errors='replace') if (trial / 'harness.log').is_file() else ''
                self.assertEqual(launched['status'], 'evaluated', (read_trial(trial), log))
                self.assertTrue(trial.is_relative_to(root / 'home-bench'))
                self.assertTrue(launched['run_id'] and launched['trial_id'] == trial.name)
                # After the run, the harness graded the frozen driver once and the configurator recorded its verdict.
                result = client.call('result', {'run_id': launched['run_id']}, home=home, autostart=False)
                self.assertEqual((result['status'], result['reason']), ('completed', 'Final verdict: equivalent'), result)
                self.assertEqual(result['progress']['equivalence'], 'graded')
                record = read_trial(trial)
                self.assertEqual((launched['equivalence'], record['equivalence']), (result['equivalence'], result['equivalence']))
                self.assertEqual((result['equivalence']['equivalent'], result['equivalence']['basis']), (True, 'evaluator'))
                self.assertEqual(result['equivalence']['passed'], result['equivalence']['total'])
                self.assertNotIn('EVALUATOR SENTINEL', json.dumps(result))
                self.assertEqual(result['progress']['device']['session_id'], record['device']['session_id'])
                self.assertEqual(record['device_stop'], {'stopped': True, 'confirmed': True})
                self.assertIs(record['sealed'], True)
                # The sealed evidence and the evaluator's draft live in the trial directory, never in the run's.
                sealed = unlock(trial / 'run-evidence.enc', password.read_text(), result['equivalence']['evidence_sha256'])
                self.assertEqual(sealed['final']['frozen_artifact_sha256'], result['equivalence']['frozen_artifact_sha256'])
                self.assertTrue((trial / 'evaluator-draft.enc').is_file())
                self.assertFalse([p for p in home.rglob('*.enc')])
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
