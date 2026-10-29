"""Scripted IPC peer exercising the public client after a lost reply."""
import json
from multiprocessing.connection import Listener
import os
from pathlib import Path
import secrets
import tempfile
import threading
import unittest


class DeviceClientTests(unittest.TestCase):
    def test_lost_reply_does_not_automatically_resubmit_a_device_invocation(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            family = 'AF_PIPE' if os.name == 'nt' else 'AF_UNIX'
            address = (r'\\.\pipe\gd-test-'+secrets.token_hex(8)) if os.name == 'nt' else str(home/'peer.sock')
            key = secrets.token_bytes(32)
            received = []
            stop = threading.Event()
            with Listener(address, family=family, authkey=key) as listener:
                (home/'service.json').write_text(json.dumps({
                    'address': address, 'family': family, 'authkey': key.hex()}))

                def peer():
                    while not stop.is_set():
                        with listener.accept() as connection:
                            request = json.loads(connection.recv_bytes())
                            if request['method'] == 'device_execute':
                                received.append(request)
                                if len(received) == 1:
                                    continue  # Request accepted, response lost.
                            connection.send_bytes(b'{"ok":true}')

                thread = threading.Thread(target=peer, daemon=True)
                thread.start()
                try:
                    result = call('device_execute', {'session_id': 'saved-session',
                        'operation': 'write', 'request_id': 'one-dispatch'}, home)
                    self.assertFalse(result['ok'], result)
                    self.assertEqual(result.get('error', {}).get('code'), 'invocation_outcome_unknown')
                    self.assertEqual(result.get('request_id'), 'one-dispatch')
                    self.assertEqual(len(received), 1)
                finally:
                    stop.set()
                    call('ping', home=home, autostart=False)
                    thread.join(5)
