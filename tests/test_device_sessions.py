"""Real owner IPC and a scripted localhost device; no physical or model evidence."""
from contextlib import contextmanager
import json
import os
import signal
import socketserver
import tempfile
import threading
from pathlib import Path
import unittest

from test_driver_contract import emit_fixture, controlled_model, rewrite_document
from test_package import example_model, example_replay


@contextmanager
def demonstration_device(state=None):
    state = state if state is not None else {'writes': 0}
    class Device(socketserver.StreamRequestHandler):
        def handle(self):
            state['connections'] = state.get('connections', 0) + 1
            if state.get('reset_on_reconnect') and state['connections'] > 1:
                state['armed'] = 0
            for request in iter(self.rfile.readline, b''):
                if request == b'ID?\n':
                    self.wfile.write(b'DEMO-42\n')
                elif request == b'T\n':
                    state['measurements'] = state.get('measurements', 0) + 1
                    readings = state.get('readings')
                    self.wfile.write(readings.pop(0) if readings else b'T:21.5\n')
                    if state.get('on_measurement'):
                        state['on_measurement']()
                elif request == b'ARM?\n':
                    self.wfile.write(('ARM:'+str(state.get('armed', 0))+'\n').encode())
                elif request.startswith(b'LEVEL '):
                    state['writes'] += 1
                    if state.get('reset_on_reconnect') and not state.get('armed'):
                        state['unsafe_writes'] = state.get('unsafe_writes', 0) + 1
                    if state.get('drop_write_reply'):
                        return
                    self.wfile.write(b'OK\n')
                self.wfile.flush()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(('127.0.0.1', 0), Device) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield {'host': '127.0.0.1', 'port': server.server_address[1]}
        finally:
            server.shutdown()
            thread.join(5)


class DeviceSessionTests(unittest.TestCase):
    def test_operator_reconciliation_preserves_the_failed_invocation(self):
        from generative_driver.sdk import ManagedDriver
        from generative_driver.client import call
        state = {'writes': 0, 'drop_write_reply': True}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            model['operations']['set_level']['steps'][0]['rx'] = {
                'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                device = ManagedDriver.open(package, binding=binding, home=home, effects=('write',))
                failed = device.invoke('set_level', {'level': 37}, request_id='missing-ack')
                self.assertFalse(failed['ok'], failed)
                try:
                    reconciled = device.reconcile({'source': 'scripted independent fixture inspection',
                        'message': 'Prior execution has stopped; observed level 37.',
                        'prior_execution_stopped': True}, effect_resolution='confirmed_safe')
                except Exception as error:
                    self.fail('operators need explicit recorded session reconciliation: '+str(error))
                self.assertFalse(reconciled['uncertain_effect'], reconciled)
                self.assertEqual(device.status('missing-ack')['result'], failed)
                self.assertEqual(state['writes'], 1)
                state['drop_write_reply'] = False
                self.assertTrue(device.invoke('set_level', {'level': 38}, request_id='new-intent')['ok'])
                self.assertEqual(state['writes'], 2)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_owner_loss_blocks_a_session_while_its_old_child_may_still_run(self):
        from generative_driver.sdk import ManagedDriver
        from generative_driver.client import call
        started, release = threading.Event(), threading.Event()
        def stalled_read():
            started.set()
            release.wait(10)
        state = {'writes': 0, 'on_measurement': stalled_read}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            model['operations']['measure']['steps'][0]['timeout_ms'] = 5000
            replay = example_replay(); replay['transactions'][1]['timeout_ms'] = 5000
            package, _ = emit_fixture(root, model, replay_data=replay)
            home = root/'owner'
            pending = None
            try:
                device = ManagedDriver.open(package, binding=binding, home=home)
                pending = threading.Thread(target=lambda: device.invoke('measure', request_id='interrupted'), daemon=True)
                pending.start()
                self.assertTrue(started.wait(5))
                endpoint = json.loads((home/'service.json').read_text())
                os.kill(endpoint['pid'], signal.SIGTERM)
                pending.join(5)
                call('ping', home=home)
                status = device.status('interrupted')
                self.assertEqual(status['status'], 'blocked', status)
                self.assertTrue(status['uncertain_effect'], status)
                self.assertEqual(status['invocation_status'], 'interrupted')
                refused = device.invoke('measure', request_id='must-not-overlap')
                self.assertFalse(refused['ok'], refused)
                self.assertEqual(state['measurements'], 1)
            finally:
                release.set()
                if pending:
                    pending.join(5)
                call('shutdown', home=home, autostart=False)

    def test_guard_and_requested_command_keep_one_connection(self):
        from generative_driver.sdk import ManagedDriver
        from generative_driver.client import call
        state = {'writes': 0, 'armed': 1, 'reset_on_reconnect': True}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            contract = json.loads((package/'driver/contract.json').read_text())
            contract['operations']['set_level']['preconditions'] = [{
                'id': 'armed_required', 'description': 'Device must be armed before changing level.',
                'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
                'operator': 'eq', 'expected': 1}]
            rewrite_document(package, 'driver/contract.json', contract)
            home = root/'owner'
            try:
                device = ManagedDriver.open(package, binding=binding, home=home, effects=('write',))
                result = device.invoke('set_level', {'level': 37})
                self.assertTrue(result['ok'], result)
                self.assertEqual(state.get('unsafe_writes', 0), 0,
                                 'reopening after the guard resets the fixture and invalidates the check')
                self.assertEqual(state['connections'], 1)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_public_sdk_opens_inspects_and_closes_the_same_session(self):
        from generative_driver.client import call
        from generative_driver.sdk import ManagedDriver
        with tempfile.TemporaryDirectory() as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                try:
                    driver = ManagedDriver.open(package, binding=binding, home=home)
                except Exception as error:
                    self.fail('the public SDK must configure operator-owned sessions: '+str(error))
                self.assertIn('measure', driver.describe()['contract']['operations'])
                result = driver.invoke('measure', request_id='sdk-read')
                self.assertTrue(result['ok'], result)
                self.assertEqual(driver.status('sdk-read')['result'], result)
                reconnected = ManagedDriver(package, driver.session_id, home=home)
                self.assertEqual(reconnected.status('sdk-read')['result'], result)
                self.assertTrue(reconnected.cancel()['cancelled'])
                self.assertEqual(reconnected.close()['status'], 'closed')
                reopened = reconnected.reconcile({'source': 'scripted operator', 'message': 'Already settled.',
                    'prior_execution_stopped': True}, effect_resolution='confirmed_safe')
                self.assertFalse(reopened['ok'], 'reconciliation must not resurrect a released session')
            finally:
                call('shutdown', home=home, autostart=False)

    def test_uncertain_write_survives_owner_restart_without_reexecution(self):
        from generative_driver.client import call
        state = {'writes': 0, 'drop_write_reply': True}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            model['operations']['set_level']['steps'][0]['rx'] = {
                'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding,
                                             'effects': ['write']}, home)
                request = {'session_id': opened['session_id'], 'operation': 'set_level',
                           'parameters': {'level': 37}, 'request_id': 'lost-ack'}
                failed = call('device_execute', request, home, autostart=False)
                self.assertFalse(failed['ok'], failed)
                self.assertTrue(failed['requested_command_dispatched'])
                self.assertTrue(call('shutdown', home=home, autostart=False)['stopped'])
                saved = call('device_status', {**opened, 'request_id': 'lost-ack'}, home)
                self.assertTrue(saved['uncertain_effect'], saved)
                self.assertEqual(saved['result'], failed)
                again = call('device_execute', request, home, autostart=False)
                self.assertEqual(again, failed)
                blocked = call('device_execute', {**request, 'request_id': 'another'}, home, autostart=False)
                self.assertFalse(blocked['ok'], blocked)
                self.assertFalse(call('device_close', opened, home, autostart=False)['ok'])
                self.assertEqual(state['writes'], 1)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_session_can_be_inspected_cancelled_and_closed_without_losing_its_result(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding}, home)
                session = {'session_id': opened['session_id']}
                described = call('device_describe', session, home, autostart=False)
                self.assertIn('measure', described.get('contract', {}).get('operations', {}))
                invocation = {**session, 'operation': 'measure', 'request_id': 'retained'}
                result = call('device_execute', invocation, home, autostart=False)
                status = call('device_status', {**session, 'request_id': 'retained'}, home, autostart=False)
                self.assertEqual(status.get('result'), result)
                self.assertTrue(call('device_cancel', session, home, autostart=False)['ok'])
                refused = call('device_execute', {**invocation, 'request_id': 'after-cancel'}, home, autostart=False)
                self.assertFalse(refused['ok'], refused)
                self.assertTrue(call('device_close', session, home, autostart=False)['ok'])
                status = call('device_status', session, home, autostart=False)
                self.assertEqual(status['status'], 'closed')
                replacement = call('device_open', {'package_dir': str(package), 'binding': binding}, home)
                self.assertTrue(replacement['ok'], replacement)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_guard_only_rejection_does_not_leave_a_write_uncertain(self):
        from generative_driver.client import call
        state = {'writes': 0, 'armed': 0}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            contract = json.loads((package/'driver/contract.json').read_text())
            contract['operations']['set_level']['preconditions'] = [{
                'id': 'armed_required', 'description': 'Device must be armed before changing level.',
                'observation': {'operation': 'read_armed', 'parameters': {}, 'output': 'armed'},
                'operator': 'eq', 'expected': 1}]
            rewrite_document(package, 'driver/contract.json', contract)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding,
                                             'effects': ['write']}, home)
                request = {'session_id': opened['session_id'], 'operation': 'set_level',
                           'parameters': {'level': 37}, 'request_id': 'not-armed'}
                refused = call('device_execute', request, home, autostart=False)
                self.assertEqual(refused['error']['code'], 'precondition_failed')
                self.assertFalse(refused['requested_command_dispatched'])
                self.assertEqual(state['writes'], 0)
                state['armed'] = 1
                allowed = call('device_execute', {**request, 'request_id': 'armed'}, home, autostart=False)
                self.assertTrue(allowed['ok'], allowed)
                self.assertEqual(state['writes'], 1)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_client_package_pin_must_match_the_open_session(self):
        from generative_driver.client import call
        state = {'writes': 0}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root / 'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding,
                                             'effects': ['write']}, home)
                reply = call('device_execute', {'session_id': opened['session_id'],
                    'operation': 'set_level', 'parameters': {'level': 37}, 'request_id': 'wrong-package',
                    'expected_manifest_sha256': '0'*64}, home, autostart=False)
                self.assertFalse(reply['ok'], reply)
                self.assertIn('package', reply.get('reason', '').lower())
                self.assertEqual(state['writes'], 0)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_repeated_invocation_id_returns_saved_result_without_another_write(self):
        # Catches resending an acknowledged device action after a lost client reply.
        from generative_driver.client import call
        state = {'writes': 0}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            model['operations']['set_level']['steps'][0]['rx'] = {
                'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}
            package, _ = emit_fixture(root, model)
            home = root / 'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding,
                    'effects': ['write']}, home)
                self.assertTrue(opened['ok'], opened)
                request = {'session_id': opened['session_id'], 'operation': 'set_level',
                           'parameters': {'level': 37}, 'request_id': 'write-once'}
                first = call('device_execute', request, home, autostart=False)
                second = call('device_execute', request, home, autostart=False)
                self.assertTrue(first['ok'], first)
                self.assertTrue(second['ok'], second)
                self.assertEqual(state['writes'], 1, 'the same invocation must not write twice')
                self.assertEqual(first['dispatch_id'], second['dispatch_id'])
                conflict = call('device_execute', {**request, 'parameters': {'level': 38}}, home, autostart=False)
                self.assertFalse(conflict.get('ok'), conflict)
                self.assertIn('conflict', conflict.get('reason', '').lower())
            finally:
                call('shutdown', home=home, autostart=False)

    def test_package_session_executes_under_exclusive_configurator_ownership(self):
        # Catches a finalized package needing a worker assignment or taking a second owner.
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix='managed package ') as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root / 'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding,
                    'effects': ['read'], 'request_id': 'owner-once'}, home)
                self.assertTrue(opened.get('ok'), opened)
                self.assertIn('session_id', opened)
                result = call('device_execute', {'session_id': opened['session_id'],
                    'operation': 'measure', 'parameters': {}, 'request_id': 'measurement-once'},
                    home, autostart=False)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['outputs'], {'temperature': 21.5})
                self.assertEqual(result['units'], {'temperature': 'degC'})
                competing = call('device_open', {'package_dir': str(package), 'binding': binding,
                    'effects': ['read'], 'request_id': 'another-owner'}, home, autostart=False)
                self.assertFalse(competing.get('ok'), competing)
                self.assertIn('owned', competing.get('reason', '').lower())
            finally:
                call('shutdown', home=home, autostart=False)


if __name__ == '__main__':
    unittest.main()
