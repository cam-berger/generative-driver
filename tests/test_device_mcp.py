"""The tools of a package as an MCP client gets them from `generative-driver serve`, over standard input and output
(docs/features/package-format/03-package.md, Behaviours 03-B9 and 03-B10).

The command runs as a child process on a package emitted from an invented model. The device is a scripted one on a
local socket; a test that only lists tools gives a binding no call ever opens."""
import asyncio
import unittest

from test_device_sessions import demonstration_device, reachable
from test_driver_contract import GUARD, RANGE, STEP, controlled_model, described_model, emit_fixture, warned
from test_serve import ServeCase

# No call is made on this binding, so nothing opens it.
UNOPENED = {'port': 'never-opened'}
DRIVER = 'fictional_demonstration'


class DeviceMcpTests(ServeCase):
    def emitted(self, model=None):
        return emit_fixture(self.root, model)[0]

    def discover(self, package):
        async def scenario():
            async with self.serving(package, UNOPENED) as (client, _, _errors):
                return (await client.list_tools()).tools
        return asyncio.run(scenario())

    def test_02_B5_a_generated_tool_call_checks_the_value_and_observes_the_precondition(self):
        # The rules of a generated tool are the ones the package's interface model states.
        state = {'writes': 0, 'armed': 0, 'readings': [b'T:30.5\n']}
        package = self.emitted(reachable(described_model()))

        async def scenario(binding):
            async with self.serving(package, binding, 'write') as (client, _, _errors):
                hot = await client.call_tool(DRIVER + '_measure', {'_request_id': 'hot'})
                refused = await client.call_tool(DRIVER + '_set_level', {'level': 37, '_request_id': 'not-armed'})
                before = state['writes']
                state['armed'] = 1
                sent = await client.call_tool(DRIVER + '_set_level', {'level': 37, '_request_id': 'armed'})
                return hot, refused, before, sent
        with demonstration_device(state) as binding:
            hot, refused, before, sent = asyncio.run(scenario(binding))
        self.assertTrue(hot.is_error, hot)
        self.assertEqual((hot.structured_content['error']['code'], hot.structured_content['error']['fault']),
                         ('output_schema_violation', 'model'))
        self.assertEqual(hot.structured_content['invalid_outputs'], {'temperature': 30.5})
        self.assertEqual(hot.structured_content['warnings'], [warned(RANGE, 'operations.measure')])
        self.assertTrue(refused.is_error, refused)
        self.assertEqual(refused.structured_content['error']['code'], 'precondition_failed')
        self.assertEqual(refused.structured_content['error']['precondition']['id'], GUARD['id'])
        self.assertEqual(before, 0)
        self.assertFalse(sent.is_error, sent)
        self.assertEqual(state['writes'], 1)

    def test_unknown_tool_is_a_protocol_parameter_error(self):
        from mcp import MCPError
        package = self.emitted()

        async def scenario():
            async with self.serving(package, UNOPENED) as (client, _, _errors):
                try:
                    await client.call_tool(DRIVER + '_missing', {})
                except MCPError as error:
                    return error.code
        self.assertEqual(asyncio.run(scenario()), -32602)

    def test_bounded_byte_argument_has_a_valid_json_tool_schema(self):
        # Catches leaking the engine's non-JSON "bytes" type into MCP.
        model = controlled_model()
        model['operations']['send_block'] = {'effect': 'write',
            'parameters': {'payload': {'type': 'bytes', 'min_length': 1, 'max_length': 4}},
            'steps': [{'op': 'exchange', 'tx': [{'arg': 'payload', 'format': 'raw'}],
                'rx': {'mode': 'exact', 'length': 0}, 'timeout_ms': 500}], 'outputs': {}}
        model['provenance'].append({'item': 'operations.send_block', 'source': 'independent byte-payload fixture'})
        tool = next(tool for tool in self.discover(self.emitted(model)) if tool.name == DRIVER + '_send_block')
        value = tool.input_schema['properties']['payload']
        self.assertEqual(value['type'], 'string')
        self.assertEqual(value['contentEncoding'], 'hex')
        self.assertEqual((value['minLength'], value['maxLength']), (2, 8))

    def test_emitted_catalog_is_served_with_typed_scoped_descriptions(self):
        # Catches serving a generic executor or dropping parameter-specific caveats.
        tools = self.discover(self.emitted(described_model()))
        self.assertEqual([tool.name for tool in tools],
                         [DRIVER + '_' + name for name in ('identify', 'measure', 'read_armed', 'set_level')])
        level = next(tool for tool in tools if tool.name == DRIVER + '_set_level')
        self.assertEqual(level.input_schema['required'], ['_request_id', 'level'])
        self.assertFalse(level.input_schema['additionalProperties'])
        self.assertEqual(level.input_schema['properties']['level']['type'], 'integer')
        self.assertEqual(level.input_schema['properties']['level']['maximum'], 100)
        self.assertIn('Precondition ' + GUARD['id'] + ': ' + GUARD['description'], level.description)
        self.assertIn('Warning: ' + STEP['message'], level.input_schema['properties']['level']['description'])
        self.assertNotIn('Warning: ' + STEP['message'], level.description)
        measure = next(tool for tool in tools if tool.name == DRIVER + '_measure')
        self.assertIn('Warning: ' + RANGE['message'], measure.description)
        self.assertNotIn('binding', level.input_schema['properties'])
        self.assertFalse(level.annotations.read_only_hint)


if __name__ == '__main__':
    unittest.main()
