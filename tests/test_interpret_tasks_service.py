"""Interpretation as tasks through the service and its MCP seam. Temporary homes, a stand-in analyzer and scripted
workers; no Ghidra, Java, model or device."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from package_env import package_env


class McpTests(unittest.TestCase):
    def test_06_B5_driver_start_takes_interpretation_single_or_tasks(self):
        asyncio.run(self.start_with_interpretation())

    async def start_with_interpretation(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            configure('codex', ['no-such-agent-runtime'], home=root)
            call('ping', {}, home=root)
            params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                                           cwd=root, env=package_env(GENERATIVE_DRIVER_HOME=str(root)))
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        catalog = {t.name: t for t in (await session.list_tools()).tools}
                        tool = catalog['driver_start']
                        self.assertIn('interpretation', tool.input_schema['properties'])
                        self.assertIn('tasks', tool.description)
                        spec = {'goal': 'Interpret supplied firmware', 'request_id': 'as-tasks',
                                'interpretation': 'tasks'}
                        reply = await session.call_tool('driver_start', spec)
                        self.assertFalse(reply.is_error, reply)
                        self.assertTrue(json.loads(reply.content[0].text)['ok'])
                        # The value reached the run's saved start: the same request with the default differs.
                        reply = await session.call_tool('driver_start', {**spec, 'interpretation': 'single'})
                        self.assertIn('different inputs', reply.content[0].text)
                        reply = await session.call_tool('driver_start', {**spec, 'request_id': 'other',
                                                                         'interpretation': 'parallel'})
                        self.assertTrue(reply.is_error, reply)
            finally:
                call('shutdown', {}, home=root)


TASKS = [('inventory', None), ('handler', 'identify'), ('handler', 'measure'), ('cross-check', None)]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_06_B10_task_mode_run_in_the_service_names_its_task_stops_at_a_cancel_and_resumes_from_the_inventory(self):
        from interpret_task_fixtures import (FRAGMENTS, INVENTORY, cortex_m_image, events, gone, launches,
                                             scripted_service_stages, scripted_worker, seen, stand_in_ghidra, stopped,
                                             until)
        from service_fixtures import serve
        for scripted in scripted_service_stages(cortex_m_image()):
            scripted.start()
            self.addCleanup(scripted.stop)
        ghidra, java = stand_in_ghidra(self.root)
        folder = self.root / 'worker'
        command = scripted_worker(folder, inventory=INVENTORY, fragments=FRAGMENTS, hang='handler:measure')
        service = _Service(serve(self, self.root / 'home'))
        run = service.call('start', {'goal': 'Recover the interface of the canned firmware', 'interpretation': 'tasks',
                                     'executor_config': {'command': command}, 'ghidra_home': ghidra, 'java_home': java})
        waiting = folder / 'waiting-handler-measure'
        pid = int(until(lambda: waiting.is_file() and waiting.read_text(encoding='utf-8')))
        worker = service.call('status', run)['worker']
        self.assertEqual((worker['stage'], worker['task']), ('interpret', {'kind': 'handler', 'command': 'measure'}))
        # A cancel stops the running task and starts no other.
        service.call('cancel', run)
        self.assertTrue(gone(pid), 'the running task outlived the cancel')
        result = stopped(service, run)
        self.assertEqual(result['status'], 'cancelled', result)
        self.assertIsNone(service.call('status', run)['worker'])
        self.assertEqual([(record['task'], record['command']) for record in seen(folder)][1:], TASKS[:3])
        finished = [event['data'] for event in events(service, run, 'task.finished')]
        self.assertEqual((finished[-1]['command'], finished[-1]['status']), ('measure', 'cancelled'))
        # A resume runs the interrupted attempt's tasks again from the inventory, with the run's one kit analysis.
        waiting.unlink()
        scripted_worker(folder, inventory=INVENTORY, fragments=FRAGMENTS)
        service.call('resume', run)
        result = stopped(service, run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual([(record['task'], record['command']) for record in seen(folder)][4:], TASKS)
        self.assertEqual(len(launches(ghidra)), 1)
        assigned = [e for e in events(service, run, 'stage.assigned') if e['data']['stage'] == 'interpret']
        self.assertEqual(len(assigned), 2)


class _Service:
    """The service's `call` as the fixtures call a controller."""

    def __init__(self, call):
        self.call = call


if __name__ == '__main__':
    unittest.main()
