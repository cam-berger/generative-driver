"""Generated device tools exercise the actual stdio MCP Interface."""
import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from package_env import package_env

from test_driver_contract import emit_fixture, controlled_model
from test_package import example_model
from test_device_sessions import demonstration_device


class DeviceMcpTests(unittest.TestCase):
    def test_mcp_cancellation_prevents_a_queued_device_write(self):
        # Cancelling the real stdio request must reach the owner before its queue opens.
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        from generative_driver.sdk import ManagedDriver
        from owner_ipc_fixture import owner_ipc
        arrived, cancelled, finished = (threading.Event() for _ in range(3))
        state = {'writes': 0}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = controlled_model(); model['channel'] = {'type': 'tcp'}
            model['operations']['set_level']['steps'][0]['rx'] = {
                'mode': 'until', 'delimiter_hex': '0a', 'max_bytes': 128}
            package, _ = emit_fixture(root, model)
            owner = Controller(root / 'owner')
            try:
                with owner_ipc(owner):
                    driver = ManagedDriver.open(package, binding=binding, home=owner.home, effects=('write',))
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
                    lock = owner._operations.setdefault(driver.session_id, threading.RLock())
                    with patch.object(owner.device_access, 'execute', side_effect=arriving), \
                            patch.object(owner, '_event', side_effect=observed):
                        lock.acquire()
                        asyncio.run(self.cancel_queued(package, owner.home, driver.session_id,
                            arrived, cancelled, finished, lock.release))
                    self.assertEqual(state['writes'], 0, 'The cancelled MCP invocation must not dispatch later')
                    saved = driver.status('cancelled-stdio-write')
                    self.assertEqual(saved['result']['error']['code'], 'cancelled')
                    self.assertFalse(saved['uncertain_effect'])
            finally:
                owner.close()

    def test_unknown_tool_is_a_protocol_parameter_error(self):
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve())
            self.assertEqual(asyncio.run(self.protocol_error(package)), -32602)

    def test_caller_known_mcp_invocation_reconnects_without_another_write(self):
        from generative_driver.client import call
        state = {'writes': 0}
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
                replies = [asyncio.run(self.call_connected(package, home, opened['session_id'],
                    'device_set_level', {'level': 37, '_request_id': 'caller-known-write'})) for _ in range(2)]
                self.assertFalse(replies[0].is_error, replies[0])
                first, second = [reply.structured_content for reply in replies]
                self.assertEqual(first['request_id'], 'caller-known-write')
                self.assertEqual(first['dispatch_id'], second['dispatch_id'])
                self.assertEqual(state['writes'], 1)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_generated_server_reconnects_to_the_managed_package_session(self):
        # Catches MCP processes opening their own device or being tied to one connection.
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root / 'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding,
                                              'effects': ['read']}, home)
                self.assertTrue(opened['ok'], opened)
                for _ in range(2):
                    try:
                        reply = asyncio.run(self.call_connected(package, home, opened['session_id']))
                    except Exception as error:
                        self.fail('generated MCP must connect to a managed session: '+str(error))
                    self.assertFalse(reply.is_error, reply)
                    self.assertEqual(reply.structured_content['outputs'], {'temperature': 21.5})
                    self.assertEqual(reply.structured_content['session_id'], opened['session_id'])
            finally:
                call('shutdown', home=home, autostart=False)

    def test_bounded_byte_argument_has_a_valid_json_tool_schema(self):
        # Catches leaking the engine's non-JSON "bytes" type into MCP.
        model = controlled_model()
        model['schema'] = 'interface-model/6'
        model['operations']['send_block'] = {'effect': 'write',
            'parameters': {'payload': {'type': 'bytes', 'min_length': 1, 'max_length': 4}},
            'steps': [{'op': 'exchange', 'tx': [{'arg': 'payload', 'format': 'raw'}],
                'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}], 'outputs': {}}
        model['provenance'].append({'item': 'operations.send_block', 'source': 'independent byte-payload fixture'})
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), model)
            tool = next(tool for tool in asyncio.run(self.discover(package)) if tool.name == 'device_send_block')
            value = tool.input_schema['properties']['payload']
            self.assertEqual(value['type'], 'string')
            self.assertEqual(value['contentEncoding'], 'hex')
            self.assertEqual((value['minLength'], value['maxLength']), (2, 8))

    def test_unconfigured_execution_is_a_structured_tool_error(self):
        # Catches a discoverable tool failing as an opaque protocol error.
        with tempfile.TemporaryDirectory(prefix='device MCP ') as directory:
            package, _ = emit_fixture(Path(directory).resolve())
            reply = asyncio.run(self.call_unconfigured(package))
            self.assertIsNotNone(reply, 'missing configuration must be an actionable tool execution error')
            self.assertTrue(reply.is_error)
            self.assertIsInstance(reply.structured_content, dict)
            self.assertEqual(reply.structured_content['error']['code'], 'managed_session_required')
            self.assertIn('session', reply.structured_content['error']['message'])
            self.assertEqual(reply.structured_content['operation'], 'measure')

    def test_emitted_catalog_is_served_with_typed_scoped_descriptions(self):
        # Catches serving a generic executor or dropping parameter-specific caveats.
        with tempfile.TemporaryDirectory(prefix='device MCP ') as directory:
            package, _ = emit_fixture(Path(directory).resolve(), controlled_model())
            self.assertTrue((package / 'mcp-tools.json').is_file(),
                            'emit must freeze the generated MCP catalog')
            tools = asyncio.run(self.discover(package))
            self.assertEqual([tool.name for tool in tools],
                ['device_identify', 'device_measure', 'device_read_armed', 'device_set_level'])
            level = next(tool for tool in tools if tool.name == 'device_set_level')
            self.assertEqual(level.input_schema['required'], ['_request_id', 'level'])
            self.assertFalse(level.input_schema['additionalProperties'])
            self.assertEqual(level.input_schema['properties']['level']['type'], 'integer')
            self.assertEqual(level.input_schema['properties']['level']['maximum'], 100)
            self.assertIn('Warning:', level.description)
            self.assertIn('Warning:', level.input_schema['properties']['level']['description'])
            self.assertNotIn('binding', level.input_schema['properties'])
            self.assertFalse(level.annotations.read_only_hint)

    async def cancel_queued(self, package, home, session, arrived, cancelled, finished, release):
        from anyio.abc import ObjectSendStream
        from mcp.types import CancelledNotification, CancelledNotificationParams
        parameters = StdioServerParameters(command=sys.executable, env=package_env(),
            args=['-m', 'generative_driver.device_mcp', '--package', str(package),
                  '--home', str(home), '--session', session], cwd=package.parent)
        released, request_ids = False, []
        try:
            async with stdio_client(parameters) as (reader, writer):
                class CaptureToolRequest(ObjectSendStream):
                    async def send(self, value):
                        if getattr(value.message, 'method', None) == 'tools/call':
                            request_ids.append(value.message.id)
                        await writer.send(value)
                    async def aclose(self):
                        await writer.aclose()
                async with ClientSession(reader, CaptureToolRequest()) as client:
                    await client.initialize()
                    pending = asyncio.create_task(client.call_tool('device_set_level',
                        {'level': 37, '_request_id': 'cancelled-stdio-write'}))
                    self.assertTrue(await asyncio.to_thread(arrived.wait, 5))
                    await client.send_notification(CancelledNotification(params=CancelledNotificationParams(
                        requestId=request_ids[-1], reason='Cancel the queued write.')))
                    pending.cancel()
                    try:
                        await pending
                    except asyncio.CancelledError:
                        pass
                    await asyncio.to_thread(cancelled.wait, 5)
                    release(); released = True
                    self.assertTrue(await asyncio.to_thread(finished.wait, 5))
        finally:
            if not released:
                release()

    async def discover(self, package):
        parameters = StdioServerParameters(command=sys.executable, env=package_env(),
            args=['-m', 'generative_driver.device_mcp', '--package', str(package)], cwd=package.parent)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                return (await client.list_tools()).tools

    async def protocol_error(self, package):
        from mcp import MCPError
        parameters = StdioServerParameters(command=sys.executable, env=package_env(),
            args=['-m', 'generative_driver.device_mcp', '--package', str(package)], cwd=package.parent)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                try:
                    await client.call_tool('device_missing', {})
                except MCPError as error:
                    return error.code

    async def call_unconfigured(self, package):
        from mcp import MCPError
        parameters = StdioServerParameters(command=sys.executable, env=package_env(),
            args=['-m', 'generative_driver.device_mcp', '--package', str(package)], cwd=package.parent)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                try:
                    return await client.call_tool('device_measure', {})
                except MCPError:
                    return None

    async def call_connected(self, package, home, session, name='device_measure', arguments=None):
        parameters = StdioServerParameters(command=sys.executable, env=package_env(),
            args=['-m', 'generative_driver.device_mcp', '--package', str(package),
                  '--home', str(home), '--session', session], cwd=package.parent)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                return await client.call_tool(name, arguments or {})


if __name__ == '__main__':
    unittest.main()
