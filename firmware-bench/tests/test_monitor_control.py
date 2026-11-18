"""Scripted TCP monitor controls; no native firmware, device or evaluator truth."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from generative_driver.benchmark_support.native import NativeSession


class ScriptedMonitor:
    def __init__(self, response, *, greeting=True, delayed_prompt=False):
        self.response, self.greeting, self.delayed_prompt = response, greeting, delayed_prompt
        self.commands = []
        self.processed = threading.Event()
        self.initial_prompt_sent = threading.Event()
        self.release = threading.Event()
        self.done = threading.Event()
        self.socket = socket.socket()
        self.socket.bind(('127.0.0.1', 0))
        self.socket.listen(1)
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.done.set()
        self.thread.join(2)
        self.socket.close()

    def serve(self):
        channel, _ = self.socket.accept()
        with channel:
            channel.settimeout(2)
            if self.greeting:
                channel.sendall(self.greeting if isinstance(self.greeting, bytes) else b'(device) ')
            negotiation = b''
            while len(negotiation) < 9:
                negotiation += channel.recv(9 - len(negotiation))
            data = b''
            while not data.endswith(b'\n'):
                chunk = channel.recv(4096)
                if not chunk:
                    return
                data += chunk
            command = data.decode().strip()
            self.commands.append(command)
            if self.delayed_prompt:
                # Receiving the command proves the client's greeting timeout elapsed.
                channel.sendall(b'(device) ')
                self.initial_prompt_sent.set()
                self.release.wait(2)
            self.processed.set()
            for chunk in self.response(command):
                try:
                    channel.sendall(chunk)
                except OSError:
                    return
                time.sleep(.003)
            # Keep an uncorrelated response open until the client's deadline.
            self.done.wait(.5)


class MonitorControlTests(unittest.TestCase):
    def session(self, peer, *, observation=None, private=None):
        return NativeSession.attach({'monitor_port': peer.port, 'monitor_timeout_seconds': .3,
            **({'private_dir': str(private)} if private else {})},
            {'stimuli': {}, 'source_files': {},
             'observations': {'value': observation} if observation else {}})

    def test_delayed_initial_prompt_cannot_complete_an_unprocessed_start(self):
        response = lambda command: [(command + '\r\n(device) ').encode()]
        with ScriptedMonitor(response, greeting=False, delayed_prompt=True) as peer:
            session = self.session(peer)
            returned = threading.Event()
            result = {}
            def start():
                try:
                    session.set_running(True)
                    result['processed_at_return'] = peer.processed.is_set()
                except BaseException as error:
                    result['error'] = error
                finally:
                    returned.set()
            caller = threading.Thread(target=start)
            caller.start()
            try:
                self.assertTrue(peer.initial_prompt_sent.wait(1))
                self.assertFalse(returned.wait(.05), result)
                self.assertFalse(peer.processed.is_set())
            finally:
                peer.release.set()
                caller.join(1)
            self.assertNotIn('error', result)
            self.assertTrue(returned.is_set())
            self.assertTrue(result['processed_at_return'])
            self.assertEqual(peer.commands, ['start'])

    def test_greetingless_reconnect_accepts_the_command_echo_and_following_prompt(self):
        response = lambda command: [(command + '\r\n(device) ').encode()]
        with ScriptedMonitor(response, greeting=False) as peer:
            self.session(peer).set_running(False)
            self.assertTrue(peer.processed.is_set())
            self.assertEqual(peer.commands, ['pause'])

    def test_missing_or_unrelated_echo_cannot_complete_controls_or_observations(self):
        cases = [
            ('start', None, b'(device) '),
            ('start', None, b'banner: start enabled\r\n(device) '),
            ('start', None, b'other_start\r\n(device) '),
            ('read', {'kind': 'scalar', 'command': 'read', 'unit': 'count'}, b'27\r\n(device) '),
            ('read', {'kind': 'scalar', 'command': 'read', 'unit': 'count'}, b'other_read\r\n27\r\n(device) '),
            ('read', {'command': 'read', 'width': 32, 'count': 3}, b'65535\r\n-2\r\n0x0010\r\n(device) '),
        ]
        for command, observation, reply in cases:
            with self.subTest(command=command, reply=reply), ScriptedMonitor(lambda _: [reply]) as peer:
                session = self.session(peer, observation=observation)
                with self.assertRaisesRegex(RuntimeError, 'Host fault: monitor response timed out'):
                    session.observe() if observation else session.set_running(True)
                self.assertEqual(peer.commands, [command])

    def test_fragmented_ansi_echo_binds_observation_and_preserves_raw_response(self):
        fragments = [b'(device) \x1b[32mre', b'ad\x1b[0m\r', b'\n27\r\n(dev', b'ice) ']
        with ScriptedMonitor(lambda _: fragments) as peer:
            observed = self.session(peer, observation={
                'kind': 'scalar', 'command': 'read', 'unit': 'count'}).observe()
            self.assertEqual(observed['values'], {'value': 27.0})
            self.assertIn('\x1b[32mread\x1b[0m', observed['reads'][0]['response'])
            self.assertEqual(peer.commands, ['read'])

    def test_observation_uses_current_echo_instead_of_an_earlier_banner_mention(self):
        response = lambda command: [('notice previous read 999\r\n(device) '
            + command + '\r\n27\r\n(device) ').encode()]
        for observation in ({'kind': 'scalar', 'command': 'read', 'unit': 'count'},
                            {'command': 'read', 'width': 32, 'count': 1}):
            with self.subTest(observation=observation), ScriptedMonitor(response) as peer:
                observed = self.session(peer, observation=observation).observe()
                self.assertEqual(observed['values'], {'value': 27})
                self.assertIn('notice previous read 999', observed['reads'][0]['response'])
                self.assertEqual(peer.commands, ['read'])

    def test_unsolicited_error_before_current_echo_does_not_reject_the_command(self):
        response = lambda command: [('Error from an earlier connection\r\n(device) '
            + command + '\r\n(device) ').encode()]
        with ScriptedMonitor(response) as peer:
            self.session(peer).set_running(True)
            self.assertEqual(peer.commands, ['start'])

    def test_correlated_rejection_keeps_private_evidence_without_redispatch(self):
        response = lambda command: [(command + '\r\nError: refused\r\n(device) ').encode()]
        with tempfile.TemporaryDirectory() as private, ScriptedMonitor(response) as peer:
            session = self.session(peer, private=private)
            with self.assertRaisesRegex(RuntimeError, 'monitor rejected evaluator control'):
                session.set_running(False)
            evidence = [json.loads(line) for line in (Path(private)/'monitor-errors.jsonl').read_text().splitlines()]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]['command'], 'pause')
            self.assertIn('Error: refused', evidence[0]['response'])
            self.assertEqual(peer.commands, ['pause'])

    def test_telnet_negotiation_before_lfcr_echo_preserves_wire_and_observation(self):
        command = 'sysbus ReadDoubleWord 0x1234'
        negotiation = b'\xff\xfd\x00\xff\xfe\x01\xff\xfb\x03\xff\xfc\x22'
        wire = negotiation + command.encode() + b'\n\r0x1234\r\r\n\x1b[33;1m(device) \x1b[0m'
        with ScriptedMonitor(lambda _: [wire], greeting=False) as peer:
            observed = self.session(peer, observation={
                'command': command, 'width': 32, 'count': 1}).observe()
            self.assertEqual(observed['values'], {'value': 0x1234})
            raw = observed['reads'][0]['response']
            self.assertIsInstance(raw, str)
            self.assertEqual(raw.encode('utf-8', errors='surrogateescape'), wire)
            # The existing JSON evidence writers use ensure_ascii=True by default.
            saved = json.loads(json.dumps(observed).encode('utf-8'))
            self.assertEqual(saved['reads'][0]['response'].encode('utf-8', errors='surrogateescape'), wire)
            self.assertEqual(peer.commands, [command])

    def test_fragmented_telnet_subnegotiation_is_not_a_command_response(self):
        fragments = [b'\xff', b'\xfd', b'\x00\xff\xfa\x22\nread\n\r999\r\n(device) ',
            b'\xff\xff', b'\xff', b'\xf0', b'\x1b[32mre', b'ad\x1b[0m\n',
            b'\r27\r\r\n\x1b[33;1m(device) \x1b[0m']
        with ScriptedMonitor(lambda _: fragments, greeting=False) as peer:
            observed = self.session(peer, observation={
                'kind': 'scalar', 'command': 'read', 'unit': 'count'}).observe()
            self.assertEqual(observed['values'], {'value': 27.0})
            self.assertEqual(observed['reads'][0]['response'].encode('utf-8', errors='surrogateescape'),
                             b''.join(fragments))
            self.assertEqual(peer.commands, ['read'])

    def test_response_timeout_records_lossless_greeting_and_response_once(self):
        greeting = b'\xff\xfb\x01\x1b[33;1m(device) \x1b[0m'
        wire = b'\xff\xfd\x00unrelated\r\n\x1b[33;1m(device) \x1b[0m'
        with tempfile.TemporaryDirectory() as private, ScriptedMonitor(lambda _: [wire], greeting=greeting) as peer:
            with self.assertRaisesRegex(RuntimeError, 'Host fault: monitor response timed out'):
                self.session(peer, private=private).set_running(True)
            evidence = [json.loads(line) for line in (Path(private)/'monitor-errors.jsonl').read_text().splitlines()]
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]['command'], 'start')
            self.assertEqual(evidence[0]['reason'], 'response-timeout')
            self.assertEqual(evidence[0]['greeting'].encode('utf-8', errors='surrogateescape'), greeting)
            self.assertEqual(evidence[0]['response'].encode('utf-8', errors='surrogateescape'), wire)
            self.assertIsInstance(evidence[0]['time'], float)
            self.assertEqual(peer.commands, ['start'])

    def test_padded_pause_echo_after_a_greeting_tail_preserves_the_complete_response(self):
        wire = b' \x1b[0mpause\n\rPausing emulation...\r\n\r\x1b[33;1m(device) \x1b[0m'
        with ScriptedMonitor(lambda _: [wire], greeting=False) as peer:
            response = self.session(peer)._monitor('pause')
            self.assertEqual(response.encode('utf-8', errors='surrogateescape'), wire)
            self.assertTrue(peer.processed.is_set())
            self.assertEqual(peer.commands, ['pause'])
