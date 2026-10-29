"""Run the shipped application template against managed scripted readings."""
import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from package_env import package_env

from test_device_sessions import demonstration_device
from test_driver_contract import emit_fixture
from test_package import example_model


class ApplicationMcpTests(unittest.TestCase):
    def test_cancellation_while_queued_prevents_the_child_write(self):
        # Without propagation to the owner, a cancelled tool still writes once its lease becomes free.
        from unittest.mock import patch
        from generative_driver.configurator import Controller
        from generative_driver.sdk import Application, ManagedDriver, ToolSpec
        from owner_ipc_fixture import owner_ipc
        from test_driver_contract import controlled_model
        arrived, cancellation_seen, cancelled = (threading.Event() for _ in range(3))
        state, outcomes = {'writes': 0}, []
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
                    application = Application('queued-write', drivers={'output': driver})
                    application.register(ToolSpec(name='adjust', description='Set the fixture level.',
                        parameters={}, outputs={}, dependencies={'output': ['set_level']}, effect='write'),
                        lambda context, args: context.invoke('output', 'set_level', {'level': 37})['outputs'])
                    execute, event = owner.device_access.execute, owner._event
                    def arriving(params):
                        arrived.set()
                        return execute(params)
                    def observed(owner_id, kind, payload, *args, **kwargs):
                        result = event(owner_id, kind, payload, *args, **kwargs)
                        if kind == 'device.invocation_cancelled':
                            cancellation_seen.set()
                        return result
                    with patch.object(owner.device_access, 'execute', side_effect=arriving), \
                            patch.object(owner, '_event', side_effect=observed):
                        with owner.device_access.lease(driver.session_id):
                            worker = threading.Thread(target=lambda: outcomes.append(application.invoke(
                                'adjust', {'_request_id': 'queued-parent'}, cancel=cancelled)), daemon=True)
                            worker.start()
                            self.assertTrue(arrived.wait(5), 'The real owner must receive the queued operation')
                            cancelled.set()
                            cancellation_seen.wait(5)
                        worker.join(10)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(state['writes'], 0, 'Cancellation must reach the owner before the lease is released')
                    self.assertFalse(outcomes[0]['ok'], outcomes)
                    self.assertEqual(outcomes[0]['error']['code'], 'cancelled')
                    self.assertTrue(driver.status()['status'] == 'open', 'Invocation cancellation must not cancel unrelated session intent')
            finally:
                owner.close()

    def test_handler_exception_retains_completed_child_evidence(self):
        from generative_driver.client import call
        from generative_driver.sdk import ManagedDriver, Application
        from test_tool_authoring import scalar_spec
        with tempfile.TemporaryDirectory() as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                device = ManagedDriver.open(package, binding=binding, home=home)
                application = Application('handler-failure', drivers={'sensor': device})
                spec = scalar_spec(); spec.dependencies['sensor'] = ['measure']
                def broken(context, arguments):
                    context.invoke('sensor', 'measure')
                    raise RuntimeError('calculation failed')
                application.register(spec, broken)
                try:
                    result = application.invoke('sample', {})
                except Exception as error:
                    self.fail('handler errors must preserve the partial driver trace: '+str(error))
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error']['code'], 'application_error')
                self.assertEqual(len(result['calls']), 1)
                self.assertEqual(result['calls'][0]['outputs'], {'temperature': 21.5})
                self.assertIn('dispatch_id', result['calls'][0])
            finally:
                call('shutdown', home=home, autostart=False)

    def test_application_cancellation_stops_before_the_next_managed_call(self):
        from generative_driver.client import call
        from generative_driver.sdk import ManagedDriver
        try:
            from generative_driver.examples.measurement_average import average_application
        except ImportError as error:
            self.fail(str(error))
        cancelled = threading.Event()
        state = {'writes': 0, 'on_measurement': cancelled.set}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding}, home)
                application = average_application(ManagedDriver(package, opened['session_id'], home=home))
                try:
                    result = application.invoke('sensor_average', {'count': 3}, cancel=cancelled)
                except Exception as error:
                    self.fail('application invocation must support bounded cancellation: '+str(error))
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['error']['code'], 'cancelled')
                self.assertEqual(len(result['calls']), 1)
                self.assertEqual(result['calls'][0]['outputs'], {'temperature': 21.5})
                self.assertEqual(state['measurements'], 1)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_failed_constituent_call_preserves_partial_trace_and_original_fault(self):
        from generative_driver.client import call
        state = {'writes': 0, 'readings': [b'T:21.5\n', b'broken\n']}
        with tempfile.TemporaryDirectory() as directory, demonstration_device(state) as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding}, home)
                try:
                    _, result = asyncio.run(self.average(package, home, opened['session_id'], {'count': 3}))
                except Exception as error:
                    self.fail('child failure must retain a structured partial trace: '+str(error))
                self.assertTrue(result.is_error)
                value = result.structured_content
                self.assertEqual(value['outputs'], {})
                self.assertEqual(value['error']['fault'], 'model')
                self.assertEqual(value['error'], value['calls'][1]['error'])
                self.assertEqual(value['calls'][0]['outputs'], {'temperature': 21.5})
                self.assertEqual(len(value['calls']), 2)
                self.assertEqual(state['measurements'], 2)
            finally:
                call('shutdown', home=home, autostart=False)

    def test_invalid_application_arguments_are_structured_errors_before_device_access(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory() as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding}, home)
                try:
                    _, result = asyncio.run(self.average(package, home, opened['session_id'], {'count': 0}))
                except Exception as error:
                    self.fail('invalid arguments must produce a structured tool error: '+str(error))
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content['error']['code'], 'invalid_arguments')
                self.assertEqual(result.structured_content.get('calls', []), [])
            finally:
                call('shutdown', home=home, autostart=False)

    def test_measurement_average_uses_managed_calls_and_retains_caveats(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix='application MCP ') as directory, demonstration_device() as binding:
            root = Path(directory).resolve()
            model = example_model(); model['channel'] = {'type': 'tcp'}
            package, _ = emit_fixture(root, model)
            home = root/'owner'
            try:
                opened = call('device_open', {'package_dir': str(package), 'binding': binding}, home)
                try:
                    tools, result = asyncio.run(self.average(package, home, opened['session_id'], {'count': 3}))
                except Exception as error:
                    self.fail('the shipped application template must serve MCP: '+str(error))
                self.assertEqual([tool.name for tool in tools], ['sensor_average'])
                self.assertEqual(tools[0].input_schema['properties']['count']['maximum'], 16)
                self.assertIn('Operation measure meaning', tools[0].description)
                self.assertFalse(result.is_error, result)
                value = result.structured_content
                self.assertEqual(value['outputs'], {'mean': 21.5, 'count': 3, 'unit': 'degC'})
                self.assertEqual(len(value['calls']), 3)
                self.assertEqual(len({child['dispatch_id'] for child in value['calls']}), 3)
                self.assertTrue(any(warning['scope'] == 'operations.measure' for warning in value['warnings']))
                self.assertTrue(all(child['session_id'] == opened['session_id'] for child in value['calls']))
            finally:
                call('shutdown', home=home, autostart=False)

    async def average(self, package, home, session, arguments):
        parameters = StdioServerParameters(command=sys.executable, env=package_env(),
            args=['-m', 'generative_driver.examples.measurement_average',
                  '--package', str(package), '--home', str(home), '--session', session], cwd=package.parent)
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                return tools, await client.call_tool('sensor_average', arguments)
