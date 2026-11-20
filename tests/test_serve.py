"""One command serves a package to an agent: `generative-driver serve PACKAGE --binding JSON`
(docs/features/package-format/03-package.md, Behaviours 03-B8 to 03-B13).

The command runs as a child process and is spoken to over MCP on its standard input and output. The device is a
scripted one on a local socket, or a stand-in serial module on the child's import path; packages are emitted from
invented models with replayed evidence. No model runs and nothing here is physical evidence."""
import asyncio
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from package_env import package_env

from test_device_sessions import demonstration_device, reachable
from test_driver_contract import controlled_model
from test_package_child import STAND_IN, serial_model
from test_package_data import emitted, remanifest, samp1
from test_package_tools import mcp_of, served, three_operations

SESSION = re.compile(r'session ([0-9a-f]{32})')


def arguments(home, package, binding, *effects):
    """The arguments that start the command on `package`, with the state directory `home`."""
    given = ['-m', 'generative_driver', '--home', str(home), 'serve', str(package), '--binding',
             binding if isinstance(binding, str) else json.dumps(binding)]
    for effect in effects:
        given += ['--allow-effect', effect]
    return given


def writing_model():
    """The invented model of SAMP1 on TCP with the write `set_level`, which waits for the device's acknowledgement,
    and the actuate `pulse`, which sends the same request."""
    model = reachable(controlled_model())
    model['device'] = {'name': 'SAMP1'}
    model['operations']['pulse'] = {**json.loads(json.dumps(model['operations']['set_level'])), 'effect': 'actuate'}
    model['provenance'].append({'item': 'operations.pulse', 'source': 'independent invented device'})
    return model


class HeldReadings(list):
    """Readings of the scripted device, each of which it gives only once `release` is set: until then the device
    has the request and the call is still running."""

    def __init__(self, release, readings):
        super().__init__(readings)
        self.release = release

    def pop(self, index=-1):
        self.release.wait(30)
        return super().pop(index)


class RawClient:
    """The command as a child process, spoken to in MCP's messages, one JSON document a line, so that a test can
    close its standard input, signal it and read its exit status. With `replies` false nothing reads the command's
    standard output, so that a test can close that end too, as a client that ends does."""

    def __init__(self, given, replies=True):
        self.process = subprocess.Popen([sys.executable, *given], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env={**os.environ, **package_env()},
                                        text=True, encoding='utf-8')
        self.errors, self.replies = [], []
        self._readers = [threading.Thread(target=self._read, args=(self.process.stderr, self.errors), daemon=True)]
        if replies:
            self._readers.append(
                threading.Thread(target=self._read, args=(self.process.stdout, self.replies), daemon=True))
        for reader in self._readers:
            reader.start()

    @staticmethod
    def _read(stream, lines):
        for line in stream:
            lines.append(line)

    def send(self, message):
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', **message}) + '\n')
        self.process.stdin.flush()

    def reply(self, identifier, timeout=60):
        """The reply to the request `identifier`, once the command wrote it."""
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            for line in list(self.replies):
                message = json.loads(line)
                if message.get('id') == identifier:
                    return message
            time.sleep(.02)
        raise AssertionError('no reply to request ' + str(identifier) + '; standard error: ' + ''.join(self.errors))

    def session(self, timeout=60):
        """The id of the session the command opened, as it wrote it to standard error."""
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            found = SESSION.search(''.join(self.errors))
            if found:
                return found.group(1)
            if self.process.poll() is not None:
                break
            time.sleep(.02)
        raise AssertionError('the command wrote no session id; standard error: ' + ''.join(self.errors))

    def initialize(self):
        from mcp.types import LATEST_PROTOCOL_VERSION
        self.send({'id': 1, 'method': 'initialize', 'params': {
            'protocolVersion': LATEST_PROTOCOL_VERSION, 'capabilities': {},
            'clientInfo': {'name': 'test of serve', 'version': '0'}}})
        # A client that reads no replies sends on: the server answers in the order it was asked.
        announced = self.reply(1) if len(self._readers) == 2 else None
        self.send({'method': 'notifications/initialized'})
        return announced and announced['result']

    def call(self, identifier, name, **given):
        self.send({'id': identifier, 'method': 'tools/call', 'params': {'name': name, 'arguments': given}})

    def ended(self, timeout=60):
        """The exit status and the standard error of the command once it ended."""
        status = self.process.wait(timeout)
        for reader in self._readers:
            reader.join(10)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            with contextlib.suppress(OSError, ValueError):
                stream.close()
        return status, ''.join(self.errors)

    def close_input(self):
        self.process.stdin.close()

    def leave(self):
        """Close both ends, as a client that ends does. Only a client that reads no replies can."""
        self.process.stdout.close()
        self.process.stdin.close()


class ServeCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'state'
        self.addCleanup(self.stop_service)

    def stop_service(self):
        from generative_driver.client import call
        if self.home.is_dir():
            call('shutdown', home=self.home, autostart=False)

    def package(self, model, label='emitted', **options):
        return Path(emitted(self.root / label, model, **options)[0]['package_dir'])

    def service(self, method, **params):
        """The reply of the service the command started, or of none: this never starts one."""
        from generative_driver.client import call
        return call(method, params, self.home, autostart=False)

    def raw(self, package, binding, *effects, replies=True):
        client = RawClient(arguments(self.home, package, binding, *effects), replies)
        self.addCleanup(lambda: client.process.poll() is None and client.process.kill())
        return client

    @contextlib.asynccontextmanager
    async def serving(self, package, binding, *effects):
        """An MCP client of the command serving `package`: (the client, what the server announced, the file its
        standard error goes to)."""
        errors = self.root / ('errors-' + str(len(list(self.root.glob('errors-*')))) + '.txt')
        parameters = StdioServerParameters(command=sys.executable, env=package_env(), cwd=self.root,
                                           args=arguments(self.home, package, binding, *effects))
        with errors.open('w', encoding='utf-8') as log:
            async with stdio_client(parameters, errlog=log) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    yield client, await client.initialize(), errors


class ServingTests(ServeCase):
    def test_03_B10_serve_opens_a_session_serves_the_tools_and_a_read_returns_its_driver_result(self):
        state = {'writes': 0}
        package = self.package(three_operations())

        async def scenario(binding):
            async with self.serving(package, binding) as (client, announced, errors):
                tools = (await client.list_tools()).tools
                reply = await client.call_tool('samp1_measure', {})
                [session] = SESSION.findall(errors.read_text(encoding='utf-8'))
                return announced, tools, reply, session, self.service('device_status', session_id=session)
        self.assertFalse((self.home / 'service.json').exists())
        with demonstration_device(state) as binding:
            announced, tools, reply, session, status = asyncio.run(scenario(binding))
        # The command started the service, which holds the session it opened.
        self.assertEqual((status['ok'], status.get('status'), status.get('effects')), (True, 'open', ['read']),
                         status)
        self.assertFalse(reply.is_error, reply)
        result = reply.structured_content
        self.assertEqual((result['schema'], result['operation'], result['ok'], result['outputs']),
                         ('driver-result/1', 'measure', True, {'temperature': 21.5}), result)
        self.assertEqual(result['session_id'], session)
        self.assertEqual(state.get('measurements'), 1)
        self.assertEqual([tool.name for tool in tools], ['samp1_identify', 'samp1_measure', 'samp1_read'])

    def test_03_B8_serve_announces_the_server_samp1_and_lists_the_tools_of_mcp_json(self):
        package = self.package(three_operations())

        async def scenario(binding):
            async with self.serving(package, binding) as (client, announced, _):
                return announced.server_info.name, [served(tool) for tool in (await client.list_tools()).tools]
        with demonstration_device() as binding:
            name, tools = asyncio.run(scenario(binding))
        self.assertEqual(name, 'samp1')
        # 03-B9: what `serve` lists is the text of `mcp.json`.
        self.assertEqual(tools, mcp_of(package)['tools'])

    def test_03_B10_a_write_or_an_actuation_is_refused_naming_the_missing_grant_and_nothing_is_sent(self):
        state = {'writes': 0}
        package = self.package(writing_model())

        async def scenario(binding, *effects):
            async with self.serving(package, binding, *effects) as (client, _, errors):
                replies = [await client.call_tool('samp1_' + operation, {'level': 37, '_request_id': operation})
                           for operation in ('set_level', 'pulse')]
                [session] = SESSION.findall(errors.read_text(encoding='utf-8'))
                return replies, [self.service('device_status', session_id=session, request_id=operation)
                                 for operation in ('set_level', 'pulse')]
        with demonstration_device(state) as binding:
            replies, saved = asyncio.run(scenario(binding))
            for reply, effect, status in zip(replies, ('write', 'actuate'), saved):
                with self.subTest(effect):
                    self.assertTrue(reply.is_error, reply)
                    result = reply.structured_content
                    self.assertEqual((result['schema'], result['ok'], result['error']['fault']),
                                     ('driver-result/1', False, 'operator'), result)
                    self.assertIn('--allow-effect ' + effect, result['error']['message'])
                    self.assertEqual((result['requested_command_dispatched'], result['transport_open_attempted']),
                                     (False, False))
                    # The session never got the call.
                    self.assertEqual(status.get('invocation_status'), 'unknown', status)
            self.assertEqual((state['writes'], state.get('connections', 0)), (0, 0))
            # Started with one of the two effects, the command sends that one and still refuses the other.
            replies, _ = asyncio.run(scenario(binding, 'write'))
            self.assertFalse(replies[0].is_error, replies[0])
            self.assertIn('--allow-effect actuate', replies[1].structured_content['error']['message'])
            self.assertEqual(state['writes'], 1)

    def test_03_B10_the_flag_is_repeated_once_for_each_effect(self):
        state = {'writes': 0}
        package = self.package(writing_model())

        async def scenario(binding):
            async with self.serving(package, binding, 'write', 'actuate') as (client, _, errors):
                replies = [await client.call_tool('samp1_' + operation, {'level': 37, '_request_id': operation})
                           for operation in ('set_level', 'pulse')]
                [session] = SESSION.findall(errors.read_text(encoding='utf-8'))
                return replies, self.service('device_status', session_id=session)
        with demonstration_device(state) as binding:
            replies, status = asyncio.run(scenario(binding))
        self.assertEqual([reply.is_error for reply in replies], [False, False], replies)
        self.assertEqual(state['writes'], 2)
        self.assertEqual(status.get('effects'), ['read', 'write', 'actuate'], status)

    def test_03_B10_a_call_on_a_resetting_channel_is_refused_naming_the_reset_grant_until_it_is_given(self):
        from generative_driver.configurator import Controller
        from owner_ipc_fixture import owner_ipc
        model = serial_model()
        model['channel']['resets_on_open'] = True
        package = self.package(model, grants=['reset'])
        # The service is this process, so the child it starts for a call finds the stand-in serial module here.
        folder, marker = self.root / 'stand-in', self.root / 'imported.txt'
        (folder / 'serial').mkdir(parents=True)
        (folder / 'serial' / '__init__.py').write_text(STAND_IN.replace('MARKER', repr(str(marker))), encoding='utf-8')
        sys.path.insert(0, str(folder))
        self.addCleanup(sys.path.remove, str(folder))
        owner = Controller(self.home)
        self.addCleanup(owner.close)

        async def scenario(*effects):
            async with self.serving(package, {'port': 'stand-in-port'}, *effects) as (client, _, _errors):
                return await client.call_tool('samp1_measure', {})
        with owner_ipc(owner):
            refused = asyncio.run(scenario())
            self.assertTrue(refused.is_error, refused)
            self.assertIn('--allow-effect reset', refused.structured_content['error']['message'])
            self.assertEqual(refused.structured_content['transport_open_attempted'], False)
            self.assertFalse(marker.exists(), 'the refused call started a child that opened the port')
            granted = asyncio.run(scenario('reset'))
        self.assertFalse(granted.is_error, granted)
        self.assertEqual(granted.structured_content['outputs'], {'temperature': 21.5})
        self.assertTrue(marker.exists())

    def test_03_B10_a_call_the_client_cancels_is_cancelled_in_the_session_so_a_waiting_write_is_never_sent(self):
        from unittest.mock import patch
        from anyio.abc import ObjectSendStream
        from generative_driver.configurator import Controller
        from generative_driver.sdk import ManagedDriver
        from mcp.types import CancelledNotification, CancelledNotificationParams
        from owner_ipc_fixture import owner_ipc
        arrived, cancelled, finished = (threading.Event() for _ in range(3))
        state = {'writes': 0}
        package = self.package(writing_model())
        owner = Controller(self.home)
        self.addCleanup(owner.close)
        execute, event = owner.device_access.execute, owner._event

        def arriving(params):
            arrived.set()
            try:
                return execute(params)
            finally:
                finished.set()

        def observed(owner_id, kind, payload, *args, **kwargs):
            result = event(owner_id, kind, payload, *args, **kwargs)
            if kind == 'device.invocation_cancelled':
                cancelled.set()
            return result

        async def scenario(binding):
            parameters = StdioServerParameters(command=sys.executable, env=package_env(), cwd=self.root,
                                               args=arguments(self.home, package, binding, 'write'))
            request_ids = []
            log = stack.enter_context((self.root / 'errors.txt').open('w', encoding='utf-8'))
            async with stdio_client(parameters, errlog=log) as (reader, writer):
                class CaptureToolRequest(ObjectSendStream):
                    async def send(self, value):
                        if getattr(value.message, 'method', None) == 'tools/call':
                            request_ids.append(value.message.id)
                        await writer.send(value)

                    async def aclose(self):
                        await writer.aclose()
                async with ClientSession(reader, CaptureToolRequest()) as client:
                    await client.initialize()
                    with owner._db() as db:
                        [session] = [row['id'] for row in db.execute('SELECT id FROM device_sessions')]
                    # The session's queue is held, so the write waits in it.
                    lock = owner._operations.setdefault(session, threading.RLock())
                    lock.acquire()
                    released = False
                    try:
                        pending = asyncio.create_task(client.call_tool(
                            'samp1_set_level', {'level': 37, '_request_id': 'cancelled-write'}))
                        self.assertTrue(await asyncio.to_thread(arrived.wait, 20))
                        await client.send_notification(CancelledNotification(params=CancelledNotificationParams(
                            requestId=request_ids[-1], reason='Cancel the waiting write.')))
                        pending.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await pending
                        self.assertTrue(await asyncio.to_thread(cancelled.wait, 20))
                        lock.release()
                        released = True
                        self.assertTrue(await asyncio.to_thread(finished.wait, 20))
                    finally:
                        if not released:
                            lock.release()
                    return session
        with owner_ipc(owner), demonstration_device(state) as binding, contextlib.ExitStack() as stack, \
                patch.object(owner.device_access, 'execute', side_effect=arriving), \
                patch.object(owner, '_event', side_effect=observed):
            session = asyncio.run(scenario(binding))
            self.assertEqual(state['writes'], 0, 'the cancelled call was sent later')
            saved = ManagedDriver(package, session, home=self.home).status('cancelled-write')
        self.assertEqual(saved['result']['error']['code'], 'cancelled')
        self.assertFalse(saved['uncertain_effect'])

    def test_03_B10_a_call_repeated_with_the_same_request_id_returns_the_saved_result_and_sends_nothing_again(self):
        from generative_driver.sdk import ManagedDriver
        state = {'writes': 0}
        package = self.package(writing_model())

        async def scenario(binding):
            async with self.serving(package, binding, 'write') as (client, _, errors):
                replies = [await client.call_tool('samp1_set_level', {'level': 37, '_request_id': 'known-write'})
                           for _ in range(2)]
                [session] = SESSION.findall(errors.read_text(encoding='utf-8'))
                # The id the client gave is the session's request id: the saved result is read by it.
                return replies, ManagedDriver(package, session, home=self.home).status('known-write')
        with demonstration_device(state) as binding:
            replies, saved = asyncio.run(scenario(binding))
        self.assertFalse(replies[0].is_error, replies[0])
        first, second = (reply.structured_content for reply in replies)
        self.assertEqual((first['request_id'], second['request_id']), ('known-write', 'known-write'))
        self.assertEqual(first['dispatch_id'], second['dispatch_id'])
        self.assertEqual(state['writes'], 1)
        self.assertEqual((saved['invocation_status'], saved['result']['dispatch_id']),
                         ('finished', first['dispatch_id']), saved)


class ClosingTests(ServeCase):
    def test_03_B11_when_the_client_closes_standard_input_serve_closes_the_session_and_frees_the_binding(self):
        package = self.package(samp1())
        with demonstration_device() as binding:
            client = self.raw(package, binding)
            session = client.session()
            client.initialize()
            self.assertEqual(self.service('device_status', session_id=session)['status'], 'open')
            client.close_input()
            status, errors = client.ended()
            self.assertEqual(status, 0, errors)
            self.assertEqual(self.service('device_status', session_id=session)['status'], 'closed')
            # The binding is free: another session opens on it.
            again = self.service('device_open', package_dir=str(package), binding=binding)
            self.assertTrue(again.get('ok'), again)

    def test_03_B11_serve_waits_for_a_call_that_is_still_running_before_it_closes(self):
        release = threading.Event()
        state = {'writes': 0, 'readings': HeldReadings(release, [b'T:21.5\n'])}
        # The measurement may take longer than this test holds its reply; the replayed evidence is of the identity.
        model = samp1()
        model['operations']['measure']['steps'][0]['timeout_ms'] = 20000
        package = self.package(model, operation='identify')
        with demonstration_device(state) as binding:
            client = self.raw(package, binding)
            session = client.session()
            client.initialize()
            client.call(2, 'samp1_measure', _request_id='still-running')
            until = time.monotonic() + 30
            while not state.get('measurements') and time.monotonic() < until:
                time.sleep(.02)
            self.assertEqual(state.get('measurements'), 1, 'the call never reached the device')
            client.close_input()
            time.sleep(.5)
            # The call is still running, so the command has not ended and the session is still open.
            self.assertIsNone(client.process.poll())
            held = self.service('device_status', session_id=session)
            self.assertEqual((held['status'], held['in_flight']), ('open', True), held)
            release.set()
            status, errors = client.ended()
            self.assertEqual(status, 0, errors)
            saved = self.service('device_status', session_id=session, request_id='still-running')
            self.assertEqual((saved['status'], saved['invocation_status'], saved['result']['ok']),
                             ('closed', 'finished', True), saved)
            self.assertEqual(state['measurements'], 1)

    def test_03_B11_when_the_client_is_gone_during_a_call_serve_waits_for_it_and_still_closes_the_session(self):
        # A client that ends takes both ends with it, so the reply to the call that still runs cannot be written.
        release = threading.Event()
        state = {'writes': 0, 'readings': HeldReadings(release, [b'T:21.5\n'])}
        model = samp1()
        model['operations']['measure']['steps'][0]['timeout_ms'] = 20000
        package = self.package(model, operation='identify')
        with demonstration_device(state) as binding:
            client = self.raw(package, binding, replies=False)
            session = client.session()
            client.initialize()
            client.call(2, 'samp1_measure', _request_id='client-gone')
            until = time.monotonic() + 30
            while not state.get('measurements') and time.monotonic() < until:
                time.sleep(.02)
            self.assertEqual(state.get('measurements'), 1, 'the call never reached the device')
            client.leave()
            time.sleep(.5)
            self.assertIsNone(client.process.poll())
            release.set()
            status, errors = client.ended()
            saved = self.service('device_status', session_id=session, request_id='client-gone')
            self.assertEqual((saved['status'], saved['invocation_status'], saved['result']['ok']),
                             ('closed', 'finished', True), (saved, errors))
            # The reply was lost, which the exit status says; the session keeps the result.
            self.assertNotEqual(status, 0, errors)
            again = self.raw(package, binding)
            self.assertNotEqual(again.session(), session)
            again.close_input()
            self.assertEqual(again.ended()[0], 0)

    @unittest.skipIf(os.name == 'nt', 'On Windows only the closed standard input is claimed')
    def test_03_B11_on_sigint_or_sigterm_serve_tries_the_close_once_and_exits(self):
        package = self.package(samp1())
        for index, name in enumerate(('SIGINT', 'SIGTERM')):
            with self.subTest(name), demonstration_device() as binding:
                client = self.raw(package, binding)
                session = client.session()
                client.initialize()
                client.process.send_signal(getattr(signal, name))
                status, errors = client.ended()
                self.assertEqual(status, 0, errors)
                self.assertEqual(self.service('device_status', session_id=session)['status'], 'closed')

    def test_03_B11_a_close_refused_for_an_uncertain_effect_leaves_the_session_and_exits_non_zero(self):
        state = {'writes': 0, 'drop_write_reply': True}
        package = self.package(writing_model())
        with demonstration_device(state) as binding:
            client = self.raw(package, binding, 'write')
            session = client.session()
            client.initialize()
            client.call(2, 'samp1_set_level', level=37, _request_id='lost-reply')
            failed = client.reply(2)['result']
            self.assertTrue(failed['isError'], failed)
            client.close_input()
            status, errors = client.ended()
            self.assertNotEqual(status, 0)
            [reason] = [line for line in errors.splitlines() if 'stays open' in line]
            self.assertIn(session, reason)
            self.assertIn('an effect is uncertain, which the operator resolves', reason)
            self.assertNotIn('invocation is still active', reason)
            # The session is as it was: open, uncertain, holding its binding. Nothing was sent again.
            left = self.service('device_status', session_id=session)
            self.assertEqual((left['status'], left['uncertain_effect'], left['in_flight']), ('open', True, False), left)
            refused = self.service('device_open', package_dir=str(package), binding=binding)
            self.assertFalse(refused.get('ok'), refused)
            self.assertEqual(state['writes'], 1)

    def test_03_B11_a_close_refused_for_an_active_invocation_says_so_and_leaves_the_session(self):
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        from generative_driver.sdk import ManagedDriver
        from owner_ipc_fixture import owner_ipc
        package = self.package(samp1())
        owner = Controller(self.home)
        self.addCleanup(owner.close)
        started, release = threading.Event(), threading.Event()
        invoke = owner.device_access.invoke_package

        def held(*given):
            started.set()
            release.wait(30)
            return invoke(*given)
        with owner_ipc(owner), demonstration_device() as binding, \
                patch.object(owner.device_access, 'invoke_package', side_effect=held):
            client = self.raw(package, binding)
            session = client.session()
            client.initialize()
            # Another client of the same session has a call running when the command ends.
            other = threading.Thread(target=lambda: ManagedDriver(package, session, home=self.home).invoke(
                'measure', request_id='from-another-client'), daemon=True)
            other.start()
            self.assertTrue(started.wait(30))
            client.close_input()
            status, errors = client.ended()
            try:
                self.assertNotEqual(status, 0)
                [reason] = [line for line in errors.splitlines() if 'stays open' in line]
                self.assertIn(session, reason)
                self.assertIn('an invocation is still active', reason)
                self.assertNotIn('uncertain', reason)
                self.assertEqual(owner.call('device_status', {'session_id': session})['status'], 'open')
            finally:
                release.set()
                other.join(30)
            self.assertEqual(owner.call('device_close', {'session_id': session})['status'], 'closed')

    def test_03_B11_a_failure_after_the_session_is_open_ends_in_the_one_close_and_names_the_session(self):
        """Once its session is open, whatever stops the command ends in the one attempt to close it, and the session's
        id is on standard error. Here the service does not give the command the session's tools: it refuses to
        describe the session because its package changed, it describes another package, which the session's client
        refuses, or its answer names no tools. The command writes the reason, exits with a non-zero status, and
        leaves the session closed and its binding free."""
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        from owner_ipc_fixture import owner_ipc
        package = self.package(samp1())
        owner = Controller(self.home)
        self.addCleanup(owner.close)
        describe = owner.device_access.describe

        def sessions():
            with owner._db() as db:
                return {row['id']: row['status'] for row in db.execute('SELECT id,status FROM device_sessions')}

        def changed(params):
            raise ValueError('Managed session package has changed')

        def another(params):
            return {**describe(params), 'manifest_sha256': '0' * 64}

        def bare(params):
            return {key: value for key, value in describe(params).items() if key != 'catalog'}
        cases = (('the service refuses to describe the session', changed, 'Managed session package has changed'),
                 ('the service describes another package', another,
                  'Client package identity differs from the managed session package'),
                 ('the answer of the service names no tools', bare, 'its answer names no tools'))
        with owner_ipc(owner):
            for case, stand_in, reason in cases:
                with self.subTest(case), demonstration_device() as binding, \
                        patch.object(owner.device_access, 'describe', side_effect=stand_in):
                    before = sessions()
                    status, errors = self.raw(package, binding).ended()
                    after = sessions()
                    [session] = set(after) - set(before)
                    self.assertEqual((after[session], session in errors, 'Traceback' in errors),
                                     ('closed', True, False), errors)
                    self.assertNotEqual(status, 0, errors)
                    self.assertIn(reason, errors)
                    # The binding is free: another session opens on it.
                    again = owner.call('device_open', {'package_dir': str(package), 'binding': binding})
                    self.assertTrue(again.get('ok'), again)
                    owner.call('device_close', {'session_id': again['session_id']})


class RefusalTests(ServeCase):
    def refused(self, package, binding, *effects):
        """The exit status and the standard error of the command when it refuses before it serves. It writes nothing
        to its standard output."""
        done = subprocess.run([sys.executable, *arguments(self.home, package, binding, *effects)], input='',
                              capture_output=True, text=True, encoding='utf-8', timeout=120,
                              env={**os.environ, **package_env()})
        self.assertEqual(done.stdout, '')
        return done.returncode, done.stderr

    def sessions(self):
        """The sessions the service at this home holds; none when no service ever ran there."""
        folder = self.home / 'devices'
        return sorted(path.name for path in folder.iterdir()) if folder.is_dir() else []

    def test_03_B12_serve_refuses_a_package_the_check_refuses_a_bad_binding_and_an_unknown_effect(self):
        state = {'writes': 0}
        good = self.package(samp1(), 'good')
        earlier = remanifest(self.package(samp1(), 'earlier'), lambda manifest: manifest.update(
            schema='interface-package/2'))
        with demonstration_device(state) as binding:
            cases = {
                'a package of an earlier format': (
                    (earlier, binding), "package format is not one this kit knows: 'interface-package/2'; "
                                        'it reads interface-package/3'),
                'a folder that is no package': ((self.root, binding), 'package has no manifest.json'),
                'a binding that is not JSON': ((good, '{host: 127.0.0.1}'), 'the binding is not JSON'),
                'a binding that does not fit the channel': (
                    (good, {'port': '/dev/a-serial-port'}), 'the binding does not fit the channel of this driver'),
                'an effect it does not know': ((good, binding, 'burn'), "invalid choice: 'burn'"),
            }
            for case, (given, reason) in cases.items():
                with self.subTest(case):
                    status, errors = self.refused(*given)
                    self.assertNotEqual(status, 0)
                    self.assertIn(reason, errors)
                    self.assertNotRegex(errors, SESSION)
                    self.assertEqual(self.sessions(), [])
        self.assertEqual(state.get('connections', 0), 0, 'a refused command opened a transport')

    def test_03_B12_serve_refuses_a_binding_that_another_session_holds_and_leaves_no_session_behind(self):
        from generative_driver.client import call
        state = {'writes': 0}
        package = self.package(samp1())
        with demonstration_device(state) as binding:
            held = call('device_open', {'package_dir': str(package), 'binding': binding}, self.home)
            self.assertTrue(held.get('ok'), held)
            status, errors = self.refused(package, binding)
            self.assertNotEqual(status, 0)
            self.assertIn('Device binding is already owned by another run or session', errors)
            self.assertNotRegex(errors, SESSION)
            self.assertEqual(self.sessions(), [held['session_id']])
            self.assertEqual(self.service('device_status', session_id=held['session_id'])['status'], 'open')
        self.assertEqual(state.get('connections', 0), 0)


class OneWayTests(ServeCase):
    def test_03_B13_the_gateway_module_refuses_package_as_an_unknown_option(self):
        package = self.package(samp1())
        done = subprocess.run([sys.executable, '-m', 'generative_driver.device_mcp', '--package', str(package)],
                              input='', capture_output=True, text=True, encoding='utf-8', timeout=120,
                              env={**os.environ, **package_env()})
        self.assertEqual((done.returncode, done.stdout), (2, ''), done.stderr)
        self.assertIn('unrecognized arguments: --package', done.stderr)
        with_session = subprocess.run(
            [sys.executable, '-m', 'generative_driver.device_mcp', '--package', str(package), '--session', 'a' * 32],
            input='', capture_output=True, text=True, encoding='utf-8', timeout=120,
            env={**os.environ, **package_env()})
        self.assertEqual(with_session.returncode, 2)
        self.assertIn('unrecognized arguments: --package', with_session.stderr)

    def test_03_B13_the_gateway_module_still_serves_a_reuse_worker_through_run_and_assignment(self):
        from generative_driver.configurator import Controller
        from owner_ipc_fixture import owner_ipc
        from test_package_tools import ToolsCase
        package = self.package(samp1())
        owner = Controller(self.home)
        self.addCleanup(owner.close)

        async def scenario(identity):
            parameters = StdioServerParameters(command=sys.executable, env=package_env(), cwd=self.root, args=[
                '-m', 'generative_driver.device_mcp', '--home', str(self.home), '--run', identity['run_id'],
                '--assignment', identity['assignment_id']])
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as client:
                    await client.initialize()
                    return await client.call_tool('samp1_measure', {})
        with owner_ipc(owner), demonstration_device() as binding:
            identity = ToolsCase.reuse_assignment(self, owner, package, binding)
            reply = asyncio.run(scenario(identity))
        self.assertFalse(reply.is_error, reply)
        self.assertEqual(reply.structured_content['outputs'], {'temperature': 21.5})
        self.assertEqual(reply.structured_content['mcp']['tool_name'], 'samp1_measure')


if __name__ == '__main__':
    unittest.main()
