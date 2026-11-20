"""The server and the tools of a package are named after its device, and one module renders the tools with the same
words on every path (docs/features/package-format/03-package.md, Behaviours 03-B8 and 03-B9).

Packages are emitted from invented models with replayed evidence. A reuse worker's server is the configurator's own
gateway, started as an MCP child over standard input and output against a configurator in this process; no model
runs and nothing here is physical evidence."""
import asyncio
import copy
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from package_env import package_env

from test_package_data import emitted, samp1

CLIENT = {'mcpServers': {'samp1': {'command': 'generative-driver',
                                   'args': ['serve', 'PACKAGE', '--binding', 'BINDING']}}}
# The names the rule of the package gives, by the device's name.
NAMES = {'SAMP1': 'samp1', 'stand-in device': 'stand_in_device', '9-axis IMU': 'device_9_axis_imu', None: 'device',
         '---': 'device', '  Probe/2 (rev B)  ': 'probe_2_rev_b', '_under_': 'under', 'Ωmeter': 'meter',
         'x' * 47 + '-yz': 'x' * 47, '0' * 60: 'device_' + '0' * 41, 'a' * 48: 'a' * 48, 'a' * 49: 'a' * 48}


def three_operations():
    """The invented model of SAMP1 with the operations `identify`, `measure` and `read`."""
    model = samp1()
    model['operations']['read'] = copy.deepcopy(model['operations']['measure'])
    model['provenance'].append({'item': 'operations.read', 'source': 'independent synthetic fixture'})
    return model


def mcp_of(package):
    return json.loads((Path(package) / 'mcp.json').read_text(encoding='utf-8'))


def served(tool):
    """A tool an MCP server listed, as the JSON it sent."""
    return tool.model_dump(mode='json', by_alias=True, exclude_none=True)


class ToolsCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def package(self, model, label='emitted', operation='measure'):
        return Path(emitted(self.root / label, model, operation)[0]['package_dir'])

    def reuse_assignment(self, owner, package, binding):
        """A fresh-reuse assignment of a generic run over `package`, as the configurator prepares one; nothing of the
        run is launched. Returns what its worker's gateway carries."""
        from generative_driver.configurator import digest
        run_dir = owner.home / 'runs/run'
        workspace = run_dir / 'reuse'
        workspace.mkdir(parents=True)
        spec = {'goal': 'Measure temperature', 'binding': binding, 'effects': ['read']}
        accepted = [{'stage': 'emit', 'artifacts': [{'path': str(package), 'sha256': digest(package)}]}]
        prepared = owner._prepare(spec, 'reuse', run_dir, workspace, accepted)
        assignment = {'id': 'reuse-1', 'stage': 'reuse', 'revision': 0, 'workspace': str(workspace),
                      'inputs': {path: digest(path) for path in prepared['inputs']}, 'binding': binding,
                      'effects': ['read'], 'allowed_tools': prepared['allowed_tools'],
                      'device_mcp': prepared['device_mcp']}
        with owner._db() as db:
            db.execute('INSERT INTO runs(id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?)',
                       ('run', json.dumps(spec), 'running', 'reuse', time.time(), time.time()))
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                       ('reuse-1', 'run', 'reuse', 'active', json.dumps(assignment), time.time()))
        return {'run_id': 'run', 'assignment_id': 'reuse-1'}

    async def reuse_server(self, home, identity):
        """What the server of a reuse worker announces and lists: (its name, its tools as JSON)."""
        parameters = StdioServerParameters(command=sys.executable, env=package_env(), cwd=self.root, args=[
            '-m', 'generative_driver.device_mcp', '--home', str(home), '--run', identity['run_id'],
            '--assignment', identity['assignment_id']])
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                announced = await client.initialize()
                return announced.server_info.name, [served(tool) for tool in (await client.list_tools()).tools]


class NameTests(ToolsCase):
    def test_03_B8_a_driver_is_named_after_its_device_by_the_rule_of_the_package(self):
        from interface_runtime.catalog import driver_name
        for device, name in NAMES.items():
            with self.subTest(device=device):
                self.assertEqual(driver_name({'device': {'name': device}}), name)

    def test_03_B8_the_package_names_its_driver_files_its_server_and_its_tools_by_that_rule(self):
        for index, device in enumerate(('SAMP1', 'stand-in device', '9-axis IMU', None)):
            name = NAMES[device]
            with self.subTest(device=device):
                model = samp1()
                model['device'] = {'name': device}
                package = self.package(model, 'emitted-' + str(index))
                manifest = json.loads((package / 'manifest.json').read_text(encoding='utf-8'))
                self.assertEqual((manifest['name'], manifest['drivers'][0]['name'], manifest['drivers'][0]['model'],
                                  manifest['drivers'][0]['evidence']),
                                 (name, name, 'drivers/' + name + '.json', 'evidence/' + name + '.probe.json'))
                document = mcp_of(package)
                self.assertEqual(document['server'], name)
                self.assertEqual([tool['name'] for tool in document['tools']], [name + '_identify', name + '_measure'])
                self.assertEqual(list(document['client']['mcpServers']), [name])

    def test_03_B8_mcp_json_names_the_server_samp1_and_its_three_tools(self):
        document = mcp_of(self.package(three_operations()))
        self.assertEqual(sorted(document), ['client', 'operations', 'schema', 'server', 'tools'])
        self.assertEqual((document['schema'], document['server']), ('driver-mcp/2', 'samp1'))
        self.assertEqual([tool['name'] for tool in document['tools']],
                         ['samp1_identify', 'samp1_measure', 'samp1_read'])
        self.assertEqual(document['operations'], {'samp1_' + operation: {'driver': 'samp1', 'operation': operation}
                                                  for operation in ('identify', 'measure', 'read')})
        self.assertEqual(document['client'], CLIENT)

    def test_03_B8_a_reuse_worker_is_served_the_same_tools_by_a_server_that_announces_itself_as_samp1(self):
        from generative_driver.configurator import Controller
        from owner_ipc_fixture import owner_ipc
        package = self.package(three_operations())
        owner = Controller(self.root / 'owner')
        self.addCleanup(owner.close)
        identity = self.reuse_assignment(owner, package, {'host': '127.0.0.1', 'port': 9})
        with owner_ipc(owner):
            name, tools = asyncio.run(self.reuse_server(owner.home, identity))
        self.assertEqual(name, 'samp1')
        self.assertEqual([tool['name'] for tool in tools], ['samp1_identify', 'samp1_measure', 'samp1_read'])
        self.assertEqual(tools, mcp_of(package)['tools'])


def texts(package):
    """What the tools of `mcp.json` in `package` say, as lines: by tool for the operation, and by tool and name for
    each parameter and output."""
    said = {}
    for tool in mcp_of(package)['tools']:
        said[tool['name']] = tool['description'].split('\n')
        for name, value in tool['inputSchema']['properties'].items():
            if name != '_request_id':
                said[tool['name'], 'parameter', name] = value['description'].split('\n')
        for name, value in tool['outputSchema']['properties']['outputs']['properties'].items():
            said[tool['name'], 'output', name] = value['description'].split('\n')
    return said


UNDOCUMENTED = 'Meaning not documented.'


class WordsTests(ToolsCase):
    def test_03_B9_the_tool_of_the_measure_that_the_driver_format_shows_reads_as_the_spec_says(self):
        from test_driver_contract import spec_model
        model = spec_model()
        model['device'] = {'name': 'SAMP1'}
        [tool] = [tool for tool in mcp_of(self.package(model, operation='identify'))['tools']
                  if tool['name'] == 'samp1_measure']
        self.assertEqual(tool['description'], 'Read the temperature of the probe.')
        self.assertEqual(tool['inputSchema']['properties']['channel']['description'], 'Meaning not documented.')
        self.assertEqual(tool['outputSchema']['properties']['outputs']['properties']['temperature']['description'],
                         'Temperature of the probe.\nUnit: Cel.')

    def test_03_B9_a_missing_description_reads_meaning_not_documented_and_no_warning_line_repeats_it(self):
        said = texts(self.package(samp1()))
        self.assertEqual(said['samp1_identify'], [UNDOCUMENTED])
        self.assertEqual(said['samp1_measure'], [UNDOCUMENTED])
        self.assertEqual(said['samp1_measure', 'output', 'temperature'], [UNDOCUMENTED, 'Unit: degC.'])
        # An uncertainty of that code which the model states itself is one of the model's, and is shown.
        stated = samp1()
        stated['operations']['measure']['outputs']['temperature']['uncertainties'] = [{
            'code': 'semantics_unknown', 'message': 'The handler names this value only as T.',
            'evidence_refs': ['invented handler listing']}]
        said = texts(self.package(stated, 'stated'))
        self.assertEqual(said['samp1_measure', 'output', 'temperature'],
                         [UNDOCUMENTED, 'Unit: degC.', 'Warning: The handler names this value only as T.'])

    def test_03_B9_every_uncertainty_is_one_warning_line_and_every_precondition_one_precondition_line(self):
        from test_driver_contract import ARMED, GUARD, RANGE, STEP, described_model
        model = described_model()
        model['device'] = {'name': 'SAMP1'}
        second = {'code': 'settling_unknown', 'message': 'How long the probe takes to settle is not stated.',
                  'evidence_refs': ['invented probe record']}
        model['operations']['measure']['uncertainties'].append(second)
        said = texts(self.package(model))
        self.assertEqual(said['samp1_measure'], ['Read the temperature of the probe.', 'Warning: ' + RANGE['message'],
                                                 'Warning: ' + second['message']])
        self.assertEqual(said['samp1_set_level'],
                         ['Set the output level.', 'Precondition ' + GUARD['id'] + ': ' + GUARD['description']])
        self.assertEqual(said['samp1_set_level', 'parameter', 'level'],
                         ['Level of the output.', 'Unit: %.', 'Warning: ' + STEP['message']])
        self.assertEqual(said['samp1_read_armed', 'output', 'armed'],
                         [UNDOCUMENTED, 'Unit: unknown.', 'Warning: ' + ARMED['message']])

    def test_03_B9_a_number_output_always_has_a_unit_line_and_a_parameter_or_a_text_output_only_a_stated_unit(self):
        from test_driver_contract import controlled_model
        model = controlled_model()
        model['device'] = {'name': 'SAMP1'}
        outputs = model['operations']['measure']['outputs']
        outputs['temperature'] = {**outputs['temperature'], 'description': 'Temperature of the probe.'}
        outputs['raw'] = {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T:', 'description': 'The same reading.'}
        outputs['counts'] = {'from': 'reply', 'kind': 'ascii_number', 'prefix': 'T:', 'description': 'Raw counts.',
                             'uncertainties': [{'code': 'unit_not_stated', 'message': 'The reply states no unit.',
                                                'evidence_refs': ['invented reply']}]}
        outputs['line'] = {'from': 'reply', 'kind': 'utf8', 'strip': True, 'description': 'The reply as text.'}
        outputs['label'] = {'from': 'reply', 'kind': 'utf8', 'strip': True, 'description': 'The reply as a label.',
                            'unit': 'label'}
        said = texts(self.package(model))
        self.assertEqual(said['samp1_measure', 'output', 'temperature'], ['Temperature of the probe.', 'Unit: degC.'])
        self.assertEqual(said['samp1_measure', 'output', 'raw'], ['The same reading.', 'Unit: unknown.'])
        self.assertEqual(said['samp1_measure', 'output', 'counts'],
                         ['Raw counts.', 'Unit: not stated by the device.', 'Warning: The reply states no unit.'])
        self.assertEqual(said['samp1_measure', 'output', 'line'], ['The reply as text.'])
        self.assertEqual(said['samp1_measure', 'output', 'label'], ['The reply as a label.', 'Unit: label.'])
        # A parameter states its unit or has no unit line.
        self.assertEqual(said['samp1_set_level', 'parameter', 'level'], [UNDOCUMENTED])
        model['operations']['set_level']['parameters']['level']['unit'] = '%'
        said = texts(self.package(model, 'with-unit'))
        self.assertEqual(said['samp1_set_level', 'parameter', 'level'], [UNDOCUMENTED, 'Unit: %.'])

    def test_03_B9_fresh_reuse_and_a_sessions_description_give_the_text_of_mcp_json(self):
        from generative_driver.configurator import Controller
        from test_driver_contract import described_model
        model = described_model()
        model['device'], model['channel'] = {'name': 'SAMP1'}, {'type': 'tcp'}
        package = self.package(model)
        tools = mcp_of(package)['tools']
        owner = Controller(self.root / 'owner')
        self.addCleanup(owner.close)
        binding = {'host': '127.0.0.1', 'port': 9}
        identity = self.reuse_assignment(owner, package, binding)
        self.assertEqual(owner.call('mcp_tools', identity)['tools'], tools)
        self.assertEqual(owner.call('tools', identity)['tools'], tools)
        other = Controller(self.root / 'sessions')
        self.addCleanup(other.close)
        opened = other.call('device_open', {'package_dir': str(package), 'binding': binding})
        described = other.call('device_describe', {'session_id': opened['session_id']})
        self.assertEqual(described['catalog'], mcp_of(package))
        self.assertIn('Warning: Readings above 25 were never observed.', json.dumps(tools))

    def test_03_B9_a_results_warnings_keep_the_semantics_unknown_ones(self):
        from interface_runtime.driver import execute_package
        from test_package import example_replay
        result = execute_package(self.package(samp1()), 'measure', replay=example_replay())
        self.assertTrue(result['ok'], result)
        unknown = lambda subject, scope: {
            'code': 'semantics_unknown', 'message': subject + ' meaning and operating assumptions are not documented.',
            'evidence_refs': [], 'scope': scope}
        self.assertEqual(result['warnings'], [unknown('Operation measure', 'operations.measure'),
                                              unknown('Output temperature', 'operations.measure.outputs.temperature')])


if __name__ == '__main__':
    unittest.main()
