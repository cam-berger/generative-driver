"""Fresh reuse gets the generated tools of the package and nothing else, and what it leaves behind names the model
and the manifest (docs/features/package-format/03-package.md, Behaviours 03-B5 and 03-B7).

The generic runs skip the stages before emit and reuse a package emitted from an invented model, on the smoke's
stand-in device. The run on a supplied device is the scripted one of `tests/test_supplied_runs.py`, on its
hand-written device. No model runs and nothing here is physical evidence."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from test_package import example_replay
from test_package_child import fresh_reuse
from test_package_data import emitted, samp1, sha256
from test_supplied_runs import FullRunCase

# The tools a reuse stage could name before a package was data.
GONE = ('package_execute', 'emit_test_live', 'interface_execute', 'interface_describe', 'emit_check')


def rendered_sha256(tools):
    """The hash of `tools` as they were rendered: of their JSON with sorted keys."""
    return hashlib.sha256(json.dumps(tools, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def tools_of(package):
    """The names of the tools `mcp.json` of `package` lists."""
    return [tool['name'] for tool in json.loads((Path(package) / 'mcp.json').read_text(encoding='utf-8'))['tools']]


class ReuseCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def package(self, model=None):
        return Path(emitted(self.root / 'emitted', model or samp1())[0]['package_dir'])


class GenericReuseTests(ReuseCase):
    def test_03_B5_fresh_reuse_of_a_generic_run_is_assigned_the_generated_tools_and_nothing_else(self):
        from generative_driver import stand_in_device
        from generative_driver.configurator import STAGE_TOOLS
        package = self.package()
        with stand_in_device.serving() as binding:
            seen = fresh_reuse(self.root, package, binding)
        self.assertTrue(seen['result'].get('ok'), seen['result'])
        generated = tools_of(package)
        self.assertEqual(len(generated), 2)
        [assignment] = [event['data'] for event in seen['events']
                        if event['kind'] == 'stage.assigned' and event['data']['stage'] == 'reuse']
        self.assertEqual(assignment['allowed_tools'], generated)
        self.assertEqual(seen['task']['allowed_tools'], generated)
        self.assertEqual([tool['name'] for tool in seen['tools']], generated)
        # No stage names the tool that ran a package's own command line, and reuse names no tool of the kit.
        self.assertEqual(STAGE_TOOLS['reuse'], [])
        self.assertEqual([stage for stage, tools in STAGE_TOOLS.items() if 'package_execute' in tools], [])

    def test_03_B5_the_objective_of_a_generic_run_names_the_generated_tools_and_the_readme(self):
        from generative_driver import stand_in_device
        package = self.package()
        with stand_in_device.serving() as binding:
            seen = fresh_reuse(self.root, package, binding)
        objective = seen['task']['objective']
        self.assertTrue(objective.startswith('Read the temperature through the emitted driver'), objective)
        for name in tools_of(package):
            self.assertIn(name, objective)
        self.assertIn('README.md', objective)
        for gone in GONE:
            self.assertNotIn(gone, objective)

    def test_03_B5_the_gateway_refuses_package_execute_as_unknown_even_for_an_assignment_that_names_it(self):
        from generative_driver.configurator import Controller
        from generative_driver.toolkit import call_tool, list_tools
        owner = Controller(self.root / 'owner')
        self.addCleanup(owner.close)
        workspace = owner.home / 'runs/run/reuse'
        workspace.mkdir(parents=True)
        spec = {'goal': 'Measure temperature', 'binding': {'host': '127.0.0.1', 'port': 9}, 'effects': ['read']}
        assignment = {'id': 'reuse-1', 'stage': 'reuse', 'revision': 0, 'workspace': str(workspace), 'inputs': {},
                      'binding': spec['binding'], 'effects': ['read'], 'allowed_tools': ['package_execute']}
        with owner._db() as db:
            db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
                       ('run', json.dumps(spec), 'running', 'reuse', time.time(), time.time()))
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                       ('reuse-1', 'run', 'reuse', 'active', json.dumps(assignment), time.time()))
        identity = {'run_id': 'run', 'assignment_id': 'reuse-1'}
        self.assertEqual(owner.call('tools', identity)['tools'], [])
        with self.assertRaisesRegex(ValueError, 'Tool is not assigned to this worker'):
            owner.call('tool', {**identity, 'name': 'package_execute',
                                'arguments': {'package_dir': str(workspace), 'operation': 'measure'}})
        with owner._db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='tool.started'").fetchone()[0], 0)
        self.assertNotIn('package_execute', [tool['name'] for tool in list_tools()])
        unknown = call_tool('package_execute', {'run_dir': str(self.root / 'run')})
        self.assertIn('Unknown tool: package_execute', json.dumps(unknown))


class SuppliedReuseTests(FullRunCase):
    """Fresh reuse of the scripted run on a supplied device."""

    def package(self):
        [assignment] = self.fixture.assigned('reuse')
        [package] = [Path(path) for path in assignment['inputs'] if (Path(path) / 'manifest.json').is_file()]
        return assignment, package

    def test_03_B5_fresh_reuse_on_a_supplied_device_is_assigned_the_generated_tools_and_nothing_else(self):
        assignment, package = self.package()
        generated = tools_of(package)
        self.assertEqual(len(generated), 3)
        self.assertEqual(assignment['allowed_tools'], generated)
        [worker] = self.fixture.workers('reuse')
        self.assertEqual(worker.task['allowed_tools'], generated)
        # The stage package names no tool of its own for fresh reuse.
        prepared = next(call for call in self.prepared if call['stage'] == 'reuse')['prepared']
        self.assertEqual(prepared['allowed_tools'], [])

    def test_03_B5_the_objective_on_a_supplied_device_names_the_generated_tools_and_the_readme(self):
        _, package = self.package()
        [worker] = self.fixture.workers('reuse')
        objective = worker.task['objective']
        self.assertTrue(objective.startswith('Use only the emitted package and this objective: Read the level twice '
                                             'and report both readings.'), objective)
        for name in tools_of(package):
            self.assertIn(name, objective)
        self.assertIn('README.md', objective)
        for gone in (*GONE, 'package reference'):
            self.assertNotIn(gone, objective)

    def test_03_B5_a_generated_call_on_a_supplied_device_is_recorded_in_package_events_with_its_mcp_proof(self):
        assignment, package = self.package()
        recorded = self.fixture.run_dir() / 'supplied' / 'package-events.jsonl'
        rows = [json.loads(line) for line in recorded.read_text(encoding='utf-8').splitlines()]
        self.assertEqual([(row['stage'], row['operation'], row['result']['ok']) for row in rows],
                         [('reuse', 'level', True), ('reuse', 'status', True)])
        level, status = (name for name in tools_of(package) if not name.endswith('identify'))
        self.assertEqual([(row['mcp']['schema'], row['mcp']['tool_name'], row['mcp']['assignment_id'],
                           row['mcp']['manifest_sha256']) for row in rows],
                         [('driver-mcp-call/1', name, assignment['id'], sha256(package / 'manifest.json'))
                          for name in (level, status)])
        # The service's own events carry the generated tool's name, never `package_execute`.
        names = {event['data']['name'] for event in self.fixture.events('tool.started', 'tool.finished')
                 if event['data']['stage'] == 'reuse'}
        self.assertEqual(names, {level, status})


class NamedHashesTests(ReuseCase):
    """Results, sessions and assignments name the model and the manifest, and no contract file."""

    def test_03_B7_a_driver_result_names_the_model_and_no_contract(self):
        from interface_runtime.driver import execute_package
        package = self.package()
        result = execute_package(package, 'measure', replay=example_replay())
        self.assertEqual((result['schema'], result['ok'], result['model_sha256']),
                         ('driver-result/1', True, sha256(package / 'drivers/samp1.json')), result)
        self.assertNotIn('contract_sha256', result)

    def test_03_B7_the_replies_of_a_managed_session_name_the_manifest_and_no_contract(self):
        from generative_driver import stand_in_device
        from generative_driver.configurator import Controller
        package = self.package()
        pin = sha256(package / 'manifest.json')
        controller = Controller(self.root / 'owner')
        self.addCleanup(controller.close)
        with stand_in_device.serving() as binding:
            opened = controller.call('device_open', {'package_dir': str(package), 'binding': binding})
            session = {'session_id': opened['session_id']}
            executed = controller.call('device_execute', {**session, 'operation': 'measure', 'parameters': {},
                                                          'request_id': 'measure-once'})
        replies = {'device_open': opened, 'device_status': controller.call('device_status', session),
                   'device_describe': controller.call('device_describe', session)}
        for method, reply in replies.items():
            with self.subTest(method):
                self.assertEqual(reply['manifest_sha256'], pin)
                self.assertNotIn('contract_sha256', reply)
        self.assertEqual((executed['schema'], executed['ok'], executed['model_sha256']),
                         ('driver-result/1', True, sha256(package / 'drivers/samp1.json')), executed)
        self.assertNotIn('contract_sha256', executed)

    def test_03_B7_a_reuse_assignment_and_the_proof_of_its_calls_name_the_manifest_and_the_rendered_tools(self):
        from generative_driver import stand_in_device
        package = self.package()
        pin = sha256(package / 'manifest.json')
        with stand_in_device.serving() as binding:
            seen = fresh_reuse(self.root, package, binding)
        rendered = rendered_sha256(seen['tools'])
        [assignment] = [event['data'] for event in seen['events']
                        if event['kind'] == 'stage.assigned' and event['data']['stage'] == 'reuse']
        [copy] = [path for path in assignment['inputs'] if (Path(path) / 'manifest.json').is_file()]
        self.assertEqual(assignment['device_mcp'],
                         {'package_dir': copy, 'manifest_sha256': pin, 'catalog_sha256': rendered})
        proof = seen['result']['mcp']
        self.assertEqual(sorted(proof), ['assignment_id', 'catalog_sha256', 'dispatch_id', 'manifest_sha256',
                                         'package_sha256', 'request_id', 'schema', 'session_id', 'tool_name'])
        self.assertEqual((proof['schema'], proof['manifest_sha256'], proof['catalog_sha256'], proof['assignment_id']),
                         ('driver-mcp-call/1', pin, rendered, assignment['id']))
        [served] = [event['data'] for event in seen['events'] if event['kind'] == 'mcp.catalog_served']
        self.assertEqual(served, {'assignment_id': assignment['id'], **assignment['device_mcp']})


class SuppliedNamedHashesTests(FullRunCase):
    def test_03_B7_the_proof_of_a_generated_call_on_a_supplied_device_names_the_manifest_and_the_rendered_tools(self):
        [assignment] = self.fixture.assigned('reuse')
        [package] = [Path(path) for path in assignment['inputs'] if (Path(path) / 'manifest.json').is_file()]
        tools = json.loads((package / 'mcp.json').read_text(encoding='utf-8'))['tools']
        self.assertEqual(assignment['device_mcp'],
                         {'package_dir': str(package), 'manifest_sha256': sha256(package / 'manifest.json'),
                          'catalog_sha256': rendered_sha256(tools)})
        recorded = self.fixture.run_dir() / 'supplied' / 'package-events.jsonl'
        rows = [json.loads(line) for line in recorded.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual((row['mcp']['manifest_sha256'], row['mcp']['catalog_sha256']),
                             (assignment['device_mcp']['manifest_sha256'], rendered_sha256(tools)))
            self.assertNotIn('contract_sha256', row['mcp'])
            self.assertEqual(row['result']['schema'], 'driver-result/1')
            self.assertNotIn('contract_sha256', row['result'])


if __name__ == '__main__':
    unittest.main()
