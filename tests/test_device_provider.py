"""The device provider: an emulated device started, observed, recovered and stopped from its recipe alone. Synthetic TCP
peers and process substitutes only; never Renode, truth or a password."""
import ast
import hashlib
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RECIPE = {'observations': {'compare': {'command': 'compare', 'width': 32, 'count': 1},
                           'reload': {'command': 'reload', 'width': 32, 'count': 1},
                           'temperature': {'kind': 'scalar', 'command': 'temperature', 'unit': 'degC'}}}
MONITOR = {'reads', 'command', 'commands', 'response', 'responses', 'monitor_port', 'private_dir', 'argv', 'pid',
           'compare', 'reload', 'temperature', 'controls'}
SOURCE = Path(__file__).resolve().parents[1]/'src/generative_driver/benchmark_support'


def keys(value):
    """Every key anywhere in a JSON value."""
    if isinstance(value, dict):
        return set(value) | {k for item in value.values() for k in keys(item)}
    return {k for item in value for k in keys(item)} if isinstance(value, list) else set()


class Process:
    """A process handle that reports the peer's state: alive until the peer is closed."""
    def __init__(self, peer):
        self.peer = peer
    def poll(self):
        return 0 if getattr(self.peer, 'closed', False) else None


class Raw:
    """A monitor substitute for a family whose raw values are given; each read carries its own response."""
    def __init__(self, recipe, values):
        self.recipe, self.values, self.closed, self.running = recipe, values, False, []
        self.info, self.process = {'binding': {'host': '127.0.0.1', 'port': id(self) % 60000}}, Process(self)
    @property
    def binding(self):
        return self.info['binding']
    def observe(self):
        return {'values': dict(self.values), 'time': 1.0, 'reads': [
            {'name': name, 'command': spec['command'], 'response': spec['command'] + ' ' + str(self.values[name]),
             'value': self.values[name]} for name, spec in self.recipe['observations'].items()]}
    def set_running(self, running):
        self.running.append(running)
    def stop(self):
        self.closed = True


class ProviderFixture(unittest.TestCase):
    def setUp(self):
        from tq9_workflow_fixture import Silicon
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.image = self.root/'firmware.bin'
        self.image.write_bytes(b'synthetic image')
        self.sha = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.peers, self.tweak, Peer = [], (lambda peer, session: None), Silicon
        def start(**kwargs):
            self.assertEqual(set(kwargs), {'renode', 'image', 'recipe'})
            self.assertEqual(kwargs['recipe'], RECIPE)
            peer = Peer(kwargs['image']); self.peers.append(peer); self.addCleanup(peer.close)
            session = peer.session(kwargs['recipe'], self.root/('native-' + str(len(self.peers))))
            session.process = Process(peer)
            self.tweak(peer, session)
            return session
        self.start = start
        # The provider takes the recipe only: no stage of its life may unlock the case truth.
        for target in ('generative_driver.benchmark_support.emulator.truth_for_case',
                       'generative_driver.benchmark_support.truth.unlock',
                       'generative_driver.benchmark_support.calibration.hydrate_truth'):
            guard = patch(target, side_effect=AssertionError('truth unlocked by the device provider'))
            guard.start(); self.addCleanup(guard.stop)

    def provider(self, case_id='tq9-v2', recipe=RECIPE, start=None, **options):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        return DeviceProvider(case_id, self.image, recipe, None, self.root/'device', image_sha256=self.sha,
                              start=start or self.start, **options)

    def journal(self, root=None):
        path = (root or self.root)/'device/journal.jsonl'
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()] if path.is_file() else []


class DeviceProviderTests(ProviderFixture):
    def test_provider_starts_observes_replaces_and_stops_from_the_recipe_alone(self):
        provider = self.provider()
        descriptor = provider.start()
        self.assertEqual(set(descriptor), {'schema', 'kind', 'provider_id', 'session_id', 'binding', 'image_sha256',
                                           'instrument', 'time_policy'})
        self.assertEqual((descriptor['schema'], descriptor['kind'], descriptor['time_policy']),
                         ('device-descriptor/1', 'provided-emulator', 'paused-between-calls'))
        self.assertEqual(descriptor['binding'], {'host': '127.0.0.1', 'port': self.peers[0].servers[0].server_address[1]})
        self.assertEqual(descriptor['image_sha256'], self.sha)
        self.assertEqual(descriptor['instrument'], {'units': {'duty': 'permille', 'temperature_reference': 'degC'},
                                                    'label': 'instrument_observation'})
        self.assertFalse(keys(descriptor) & MONITOR)
        self.assertEqual(provider.start(), descriptor)
        self.assertEqual(len(self.peers), 1)
        self.assertEqual(provider.status(), {'provider_id': descriptor['provider_id'], 'session_id': descriptor['session_id'],
                                             'binding': descriptor['binding'], 'running': False, 'healthy': True})
        # An instrument reading: projected values and units of this session, with no monitor command or raw read.
        self.peers[0].duty = 370
        observed = provider.observe()
        self.assertEqual(set(observed), {'schema', 'session_id', 'values', 'units', 'time'})
        self.assertEqual((observed['schema'], observed['session_id']), ('instrument-observation/1', descriptor['session_id']))
        self.assertEqual(observed['values'], {'duty': 370.0, 'temperature_reference': 20.0})
        self.assertEqual(observed['units'], descriptor['instrument']['units'])
        self.assertFalse(keys(observed) & MONITOR)
        # The device runs only inside a window, paused between calls.
        self.assertFalse(self.peers[0].running)
        with provider.window():
            self.assertTrue(self.peers[0].running)
            self.assertTrue(provider.status()['running'])
        self.assertFalse(self.peers[0].running); self.assertFalse(provider.status()['running'])
        # A replacement is refused for a stale session, before anything stops.
        for stale in ('another-session', None):
            with self.assertRaisesRegex(RuntimeError, 'inactive'):
                provider.replace(stale, ['7'], ['6'])
        self.assertFalse(getattr(self.peers[0], 'closed', False)); self.assertEqual(len(self.peers), 1)
        self.peers[0].duty = 0
        receipt = provider.replace(descriptor['session_id'], ['7'], ['6'])
        self.assertEqual(set(receipt), {'schema', 'old_session_id', 'new_session_id', 'binding', 'old_owner_stopped',
                                        'initial_state_verified', 'observation_sha256', 'proof_sha256'})
        self.assertEqual(receipt['schema'], 'emulator-recovery/2')
        self.assertEqual(receipt['old_session_id'], descriptor['session_id'])
        self.assertNotEqual(receipt['new_session_id'], descriptor['session_id'])
        self.assertEqual(receipt['binding'], {'host': '127.0.0.1', 'port': self.peers[1].servers[0].server_address[1]})
        self.assertNotEqual(receipt['binding'], descriptor['binding'])
        self.assertIs(receipt['initial_state_verified'], True); self.assertIs(receipt['old_owner_stopped'], True)
        self.assertTrue(self.peers[0].closed); self.assertFalse(getattr(self.peers[1], 'closed', False))
        self.assertEqual(provider.status()['session_id'], receipt['new_session_id'])
        self.assertEqual(provider.status()['binding'], receipt['binding'])
        self.assertEqual(provider.observe()['session_id'], receipt['new_session_id'])
        # The proof body stays in the provider's private journal; the receipt carries only its hash.
        journal = self.journal()
        self.assertEqual([entry['kind'] for entry in journal], ['started', 'replaced'])
        self.assertEqual(journal[0]['session_id'], descriptor['session_id'])
        proof = journal[1]['proof']
        self.assertEqual(hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest(), receipt['proof_sha256'])
        self.assertEqual(proof['new_startup_observation']['values'], proof['old_startup_observation']['values'])
        self.assertEqual(proof['old_startup_observation'], journal[0]['startup_observation'])
        self.assertEqual((proof['unresolved_evidence_ids'], proof['unresolved_dispatch_ids']), (['7'], ['6']))
        self.assertEqual({k: v for k, v in proof['receipt'].items()}, {k: v for k, v in receipt.items() if k != 'proof_sha256'})
        self.assertEqual({p.name for p in (self.root/'device').iterdir()}, {'journal.jsonl'})
        self.assertEqual(provider.stop(), {'stopped': True, 'confirmed': True})
        self.assertTrue(self.peers[1].closed)
        self.assertEqual(self.journal()[-1]['kind'], 'stopped')
        with self.assertRaisesRegex(RuntimeError, 'No live device session'):
            provider.observe()
        self.assertEqual(provider.stop(), {'stopped': True, 'confirmed': True})

    def test_every_family_reading_is_projected_with_units_and_nothing_else(self):
        from generative_driver.benchmark_support import instruments
        families = {'sampled-sensor-v1': ({'latched_q4': {'command': 'latched', 'width': 16, 'count': 1},
                                           'acquisition_counter': {'command': 'counter', 'width': 32, 'count': 1}},
                                          {'latched_q4': 65456, 'acquisition_counter': 3},
                                          {'reference_temperature': -5.0, 'monitor_sequence': 3}),
                    'parameter-store-v1': ({'committed': {'command': 'committed', 'width': 16, 'count': 8},
                                            'pending': {'command': 'pending', 'width': 16, 'count': 8},
                                            'generation': {'command': 'generation', 'width': 32, 'count': 1},
                                            'pending_active': {'command': 'active', 'width': 8, 'count': 1}},
                                           {'committed': [1, 2, 3, 4, 5, 6, 65529, 8], 'pending': [0] * 8,
                                            'generation': 2, 'pending_active': 0}, None)}
        for case_id, (observations, values, expected) in families.items():
            with self.subTest(case_id=case_id):
                recipe = {'observations': observations}
                provider = self.provider(case_id, recipe, start=lambda **kwargs: Raw(kwargs['recipe'], values))
                provider.start()
                observed = provider.observe()
                named = dict(instruments.family_for(case_id).observations.monitor_units)
                self.assertEqual(set(observed), {'schema', 'session_id', 'values', 'units', 'time'})
                self.assertEqual((set(observed['values']), observed['units']), (set(named), named))
                self.assertEqual(provider.start()['instrument']['units'], named)
                if expected:
                    self.assertEqual(observed['values'], expected)
                else:
                    self.assertEqual((observed['values']['cell_B_2'], observed['values']['generation'],
                                      observed['values']['pending_active']), (-7, 2, False))
                self.assertFalse(keys(observed) & MONITOR)
                # A worker sees the same values beside its result, labelled as an instrument's, never as a phase.
                shown = instruments.reading(observed)
                self.assertEqual(shown, {**observed['values'], 'units': named, 'source': 'instrument'})
                self.assertNotIn('phase', shown)
                provider.stop()
        tq9 = instruments.reading(instruments.observation('tq9-v2', {'values': {'compare': 370, 'reload': 999,
                                                                                'temperature': 21.5}}, 's'))
        self.assertEqual(tq9, {'duty': 370.0, 'temperature_reference': 21.5, 'source': 'instrument',
                               'units': {'duty': 'permille', 'temperature_reference': 'degC'}})

    def test_constructed_from_the_recipe_alone_and_only_for_the_pinned_image(self):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        parameters = set(inspect.signature(DeviceProvider).parameters)
        self.assertEqual(parameters, {'case_id', 'image', 'recipe', 'renode', 'private_dir', 'image_sha256', 'start'})
        # A truth object or anything carrying a password is refused, whatever else it holds.
        for refused in ({'recipe': RECIPE, 'contracts': {}}, {**RECIPE, 'contracts': {}}, {**RECIPE, 'phases': {}},
                        {**RECIPE, 'evaluator_password_file': 'p'}, 'recipe'):
            with self.subTest(refused=refused), self.assertRaisesRegex(ValueError, 'recipe'):
                self.provider(recipe=refused)
        with self.assertRaisesRegex(ValueError, 'Unknown native family'):
            self.provider('tq9')
        # The pinned image only: a changed image starts nothing.
        self.image.write_bytes(b'another image')
        with self.assertRaisesRegex(ValueError, 'image'):
            self.provider().start()
        self.assertFalse(self.peers); self.assertFalse(self.journal())

    def test_an_unconfirmed_old_stop_raises_and_retains_the_handle(self):
        provider = self.provider()
        session_id = provider.start()['session_id']
        old = provider.session; old.stop = lambda: None
        with self.assertRaisesRegex(RuntimeError, 'Old emulator stop could not be confirmed'):
            provider.replace(session_id, ['7'], ['6'])
        self.assertIs(provider.session, old); self.assertEqual(provider.status()['session_id'], session_id)
        self.assertEqual(len(self.peers), 1)
        with self.assertRaisesRegex(RuntimeError, 'stop was not confirmed'):
            provider.stop()
        self.assertIs(provider.session, old)
        self.assertEqual([entry['kind'] for entry in self.journal()], ['started'])

    def test_a_baseline_mismatch_raises_and_retains_a_live_replacement_for_stop(self):
        for stop_raises in (True, False):
            with self.subTest(stop_raises=stop_raises):
                self.peers.clear()
                def tweak(peer, session):
                    if len(self.peers) == 2:
                        peer.temperature = 21
                        if stop_raises:
                            def stop():raise RuntimeError('stop unavailable')
                            session.stop = stop
                        else:
                            session.stop = lambda: None
                self.tweak = tweak
                provider = self.provider()
                session_id = provider.start()['session_id']
                with self.assertRaises(RuntimeError) as raised:
                    provider.replace(session_id, ['7'], ['6'])
                self.assertIn('stop unavailable' if stop_raises else 'Replacement initial state is unverified', str(raised.exception))
                self.assertTrue(self.peers[0].closed)
                replacement = provider.session
                self.assertIsNotNone(replacement); self.assertIsNone(replacement.process.poll())
                self.assertEqual(replacement.binding['port'], self.peers[1].servers[0].server_address[1])
                self.assertNotEqual(provider.status()['session_id'], session_id)
                self.assertNotIn('replaced', [entry['kind'] for entry in self.journal()])
                with self.assertRaises(RuntimeError):
                    provider.stop()
                self.assertIs(provider.session, replacement)
                replacement.stop = self.peers[1].close
                self.assertEqual(provider.stop(), {'stopped': True, 'confirmed': True})
        # A mismatched replacement that does stop is released: nothing live stays owned.
        self.peers.clear()
        self.tweak = lambda peer, session: setattr(peer, 'temperature', 21) if len(self.peers) == 2 else None
        provider = self.provider()
        session_id = provider.start()['session_id']
        with self.assertRaisesRegex(RuntimeError, 'Replacement initial state is unverified'):
            provider.replace(session_id, [], [])
        self.assertIsNone(provider.session); self.assertTrue(self.peers[1].closed)
        self.assertFalse(provider.status()['healthy'])

    def test_a_replacement_must_be_a_distinct_live_process(self):
        # A launcher returning a formerly stopped handle is not trusted ownership.
        launches = []
        def launcher(**kwargs):
            return launches.pop(0)(**kwargs) if launches else self.start(**kwargs)
        provider = self.provider(start=launcher)
        session_id = provider.start()['session_id']
        old = provider.session
        def reused(**kwargs):
            self.peers[0].closed = False
            return old
        launches.append(reused)
        with self.assertRaisesRegex(RuntimeError, 'distinct'):
            provider.replace(session_id, [], [])
        self.assertTrue(self.peers[0].closed); self.assertIsNone(provider.session)
        # A replacement on the old binding is refused too.
        provider = self.provider(start=launcher)
        session_id = provider.start()['session_id']
        binding = dict(provider.session.binding)
        def same_binding(**kwargs):
            session = self.start(**kwargs)
            session.info['binding'] = binding
            return session
        launches.append(same_binding)
        with self.assertRaisesRegex(RuntimeError, 'binding was reused'):
            provider.replace(session_id, [], [])
        self.assertTrue(self.peers[-1].closed); self.assertIsNone(provider.session)

    def test_cancellation_stops_a_replacement_before_it_is_handed_over(self):
        provider = self.provider()
        session_id = provider.start()['session_id']
        calls = []
        def cancelled():
            calls.append(len(self.peers))
            return len(self.peers) == 2
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            provider.replace(session_id, [], [], cancelled=cancelled)
        self.assertTrue(self.peers[0].closed); self.assertTrue(self.peers[1].closed)
        self.assertIsNone(provider.session)
        provider = self.provider()
        session_id = provider.start()['session_id']
        with self.assertRaisesRegex(RuntimeError, 'inactive'):
            provider.replace(session_id, [], [], cancelled=lambda: True)
        self.assertFalse(getattr(self.peers[-1], 'closed', False))

    def test_initial_observation_failure_retains_a_live_owner_when_its_stop_fails(self):
        for raises in (True, False):
            with self.subTest(stop_raises=raises):
                self.peers.clear()
                def tweak(peer, session):
                    session.observe = lambda: {'values': {}, 'reads': []}
                    def stop():
                        if raises:raise RuntimeError('stop unavailable')
                    session.stop = stop
                self.tweak = tweak
                provider = self.provider()
                with self.assertRaisesRegex(RuntimeError, 'observation'):
                    provider.start()
                owner = provider.session
                self.assertIsNotNone(owner); self.assertIsNone(owner.process.poll())
                self.assertTrue(provider.status()['session_id'])
                with self.assertRaisesRegex(RuntimeError, 'stop'):
                    provider.stop()
                self.assertIs(provider.session, owner)
        # When the stop is confirmed nothing stays owned, and the failure is still raised.
        self.peers.clear()
        def broken(peer, session):
            session.observe = lambda: {'values': {}, 'reads': []}
        self.tweak = broken
        provider = self.provider()
        with self.assertRaisesRegex(RuntimeError, 'observation'):
            provider.start()
        self.assertIsNone(provider.session); self.assertTrue(self.peers[-1].closed)

    def test_release_pauses_and_refuses_further_control_of_that_session(self):
        provider = self.provider()
        session_id = provider.start()['session_id']
        with self.assertRaisesRegex(ValueError, 'session'):
            provider.release('another-session')
        provider.release(session_id)
        self.assertEqual(self.peers[0].commands[-1], 'pause'); self.assertFalse(self.peers[0].running)
        for control in (provider.observe, lambda: provider.window().__enter__(),
                        lambda: provider.replace(session_id, [], [])):
            with self.assertRaisesRegex(RuntimeError, 'released'):
                control()
        self.assertFalse(provider.status()['healthy'])
        self.assertFalse(getattr(self.peers[0], 'closed', False))
        self.assertEqual(provider.stop(), {'stopped': True, 'confirmed': True})

    def test_the_provider_imports_nothing_that_decrypts_truth(self):
        forbidden = {'truth', 'emulator', 'calibration', 'reference_calibration', 'evidence', 'emulated_evidence', 'evaluator',
                     'emulated', 'emulated_recovery', 'registry', 'cases', 'configurator'}
        for name in ('device_provider.py', 'instruments.py'):
            tree = ast.parse((SOURCE/name).read_text(encoding='utf-8'))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imported |= {(node.module or '').split('.')[-1]} | {alias.name for alias in node.names}
                elif isinstance(node, ast.Import):
                    imported |= {alias.name.split('.')[-1] for alias in node.names}
            with self.subTest(module=name):
                self.assertFalse(imported & forbidden, imported)
        loaded = subprocess.run([sys.executable, '-c', 'import sys,json\n'
            'import generative_driver.benchmark_support.device_provider\n'
            'print(json.dumps(sorted(m for m in sys.modules if m.startswith("generative_driver"))))'],
            capture_output=True, text=True, check=True)
        modules = {m.rsplit('.', 1)[-1] for m in json.loads(loaded.stdout)}
        self.assertFalse(modules & forbidden, modules)


class HarnessDelegationTests(ProviderFixture):
    """The bench harness holds the emulator through a provider built from the device recipe alone, and the configurator
    reaches it only through its control endpoint: the stage adapter holds, starts and stops no device."""
    def setUp(self):
        super().setUp()
        from generative_driver.benchmark_support.harness import DeviceServer
        from generative_driver.configurator import Controller
        self.trial, self.home = self.root/'trial', self.root/'home'
        self.truth = {'recipe': RECIPE, 'contracts': {'sentinel': 'EVALUATOR SENTINEL'}, 'private_sentinel': 'EVALUATOR SENTINEL'}
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        self.device = DeviceProvider('tq9-v2', self.image, RECIPE, None, self.trial/'device', image_sha256=self.sha,
                                     start=self.start)
        self.server_type = DeviceServer
        self.controller = Controller(self.home); self.addCleanup(self.controller.close)
        self.run = {'id': 'run-fixture', 'spec': json.dumps({'_case_pin': {'case_id': 'tq9-v2'}})}
        self.run_dir = self.home/'runs/run-fixture'

    def serve(self):
        server = self.server_type(self.device, self.trial/'endpoint'); self.addCleanup(server.close)
        return server

    def test_the_harness_provides_recovers_and_stops_the_device_from_the_recipe_alone(self):
        from generative_driver.benchmark_support import emulated, harness
        from generative_driver.device_client import DeviceUnavailable, open_device
        from generative_driver.toolkit import call_tool
        with patch('generative_driver.benchmark_support.evaluator.case_truth', return_value=self.truth) as unlocked:
            self.assertEqual(harness.device_recipe('tq9-v2', self.trial, {}), RECIPE)
        unlocked.assert_called_once()
        self.assertFalse(hasattr(emulated, '_session'))
        self.device.start()
        server = self.serve()
        self.assertEqual(len(self.peers), 1)
        self.assertEqual(self.device.recipe, RECIPE)
        # The configurator receives the public descriptor only: no startup reading, monitor command or private path.
        descriptor = server.descriptor
        self.assertFalse(keys(descriptor) & MONITOR)
        self.assertNotIn('EVALUATOR SENTINEL', json.dumps(descriptor))
        self.assertTrue(Path(self.device.private_dir).is_relative_to(self.trial))
        # Live tools run inside the paused-between-calls window, between instrument readings.
        model = self.root/'model'; model.mkdir()
        from tq9_workflow_fixture import model as fixture_model
        candidate = fixture_model(); candidate['channel'] = {'type': 'tcp'}
        (model/'model.json').write_text(json.dumps(candidate))
        client = open_device(descriptor)
        arguments = {'model_dir': str(model), 'run_dir': str(self.root/'tools'), 'operation': 'measure',
                     'binding': descriptor['binding']}
        result = self.controller._device_call(self.run, {'id': 'probe-attempt', 'stage': 'probe'}, 'interface_execute',
                                              arguments, client, lambda: call_tool('interface_execute', arguments))
        self.assertTrue(result['ok'], result)
        for reading in (result['observation'], result['before_observation']):
            self.assertEqual(reading, {'duty': 0.0, 'temperature_reference': 20.0, 'source': 'instrument',
                                       'units': {'duty': 'permille', 'temperature_reference': 'degC'}})
        self.assertFalse(self.peers[0].running); self.assertFalse(self.peers[0].paused_requests)
        self.assertEqual(self.peers[0].commands.count('start'), 1)
        # Recovery replaces the emulator through the handle; the proof stays in the harness's journal.
        old = descriptor['session_id']
        receipt = client.replace(old, ['9'], ['8'])
        self.assertEqual((receipt['schema'], receipt['old_session_id']), ('emulator-recovery/2', old))
        self.assertEqual(self.device.status()['session_id'], receipt['new_session_id'])
        self.assertEqual(server.descriptor['binding'], receipt['binding'])
        proofs = [entry['proof'] for entry in self.journal(self.trial) if entry['kind'] == 'replaced']
        self.assertEqual([hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest() for p in proofs],
                         [receipt['proof_sha256']])
        self.assertEqual((proofs[0]['unresolved_evidence_ids'], proofs[0]['unresolved_dispatch_ids']), (['9'], ['8']))
        self.assertFalse(list(self.home.rglob('journal.jsonl')))
        self.assertFalse(keys(receipt) & {'proof', 'startup_observation', 'old_startup_observation', 'new_startup_observation'})
        # A stale session cannot steer the replacement.
        with self.assertRaisesRegex(DeviceUnavailable, 'inactive'):
            client.replace(old, [], [])
        self.assertEqual(len(self.peers), 2)
        self.assertEqual(self.device.status()['session_id'], receipt['new_session_id'])
        # The harness stops the device, confirmed; the configurator only releases it.
        done = harness.finish(self.device, 'tq9-v2', None, [], None, {'status': 'completed'})
        self.assertEqual(done['device_stop'], {'stopped': True, 'confirmed': True})
        self.assertTrue(self.peers[1].closed)
        self.assertEqual(self.journal(self.trial)[-1]['kind'], 'stopped')

    def test_package_calls_run_in_the_window_and_carry_instrument_readings(self):
        from generative_driver.benchmark_support.cases import _write
        from generative_driver.benchmark_support.emulated import package_call
        from generative_driver.configurator import digest
        from generative_driver.device_client import open_device
        package = self.root/'package'; package.mkdir(); (package/'toy').write_text('toy')
        _write(self.run_dir/'benchmark/state.json', {'package_copies': {'reuse': str(package.resolve())},
            'package_sha256': digest(package), 'package_attempts': {'reuse': 'attempt'}})
        self.device.start()
        client = open_device(self.serve().descriptor)
        running = []
        def invoker(*args):
            return lambda operation, parameters: running.append(self.peers[0].running) or {'ok': True, 'outputs': {}}
        arguments = {'package_dir': str(package), 'operation': 'measure', 'parameters': {}, 'binding': client.status()['binding'],
                     'allow_effects': []}
        with patch('generative_driver.benchmark_support.emulated_actions.package_invoker', side_effect=invoker):
            invoke = package_call('tq9-v2', self.run_dir, 'reuse', arguments)
            result = self.controller._device_call(self.run, {'id': 'reuse-attempt', 'stage': 'reuse'},
                                                  'benchmark_package_execute', arguments, client, invoke)
            # Only the unchanged stage-assigned copy may run; anything else is refused before dispatch.
            with self.assertRaisesRegex(ValueError, 'stage-assigned package'):
                package_call('tq9-v2', self.run_dir, 'reuse', {**arguments, 'package_dir': str(self.root)})
        self.assertEqual(running, [True]); self.assertFalse(self.peers[0].running)
        self.assertEqual(result['observation']['source'], 'instrument')
        row = json.loads((self.run_dir/'benchmark/package-events.jsonl').read_text())
        self.assertEqual(row['observation'], result['observation'])
        self.assertFalse(keys(row) & MONITOR)

    def test_a_failed_start_keeps_a_live_owner_and_the_harness_record(self):
        # The harness holds the provider before anything starts, so a start that fails mid-way leaves a live owner.
        from generative_driver.benchmark_support import harness
        def tweak(peer, session):
            session.observe = lambda: {'values': {}, 'reads': []}
            def stop():raise RuntimeError('stop unavailable')
            session.stop = stop
        self.tweak = tweak
        with self.assertRaisesRegex(RuntimeError, 'emulator cleanup unverified'):
            self.device.start()
        self.assertIsNone(self.device.session.process.poll())
        held = self.device.status()['session_id']
        self.assertTrue(held)
        with self.assertRaisesRegex(RuntimeError, 'stop'):
            self.device.stop()
        done = harness.finish(self.device, 'tq9-v2', None, [], None)
        self.assertEqual((done['status'], done['device']['session_id']), ('stop-unconfirmed', held))
        # The owner keeps its session: no second start launches another emulator, and the configurator starts none.
        self.assertEqual(self.device.start()['session_id'], held)
        self.assertEqual(len(self.peers), 1)
        self.peers[0].close()

if __name__ == '__main__':
    unittest.main()
