"""The supported authoring interface validates application tools without hardware."""
import unittest
from pathlib import Path
import tempfile
import json

from generative_driver.sdk import Application, ToolSpec


def scalar_spec(name='sample'):
    return ToolSpec(name=name, description='Return a test value.', parameters={},
        outputs={'value': {'schema': {'type': 'number'}, 'description': 'Numeric result.',
                           'unit': None, 'uncertainties': []}}, dependencies={})


class ToolAuthoringTests(unittest.TestCase):
    def test_nonfinite_application_output_is_a_serializable_failure(self):
        application = Application('finite-output', drivers={})
        application.register(scalar_spec(), lambda context, arguments: {'value': float('nan')})
        result = application.invoke('sample', {})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'output_schema_violation')
        json.dumps(result, allow_nan=False)

    def test_application_results_retain_their_own_scoped_caveats(self):
        application = Application('caveat-check', drivers={})
        spec = scalar_spec()
        warning = {'code': 'unverified', 'message': 'Meaning needs independent validation.', 'evidence_refs': []}
        spec.uncertainties.append(warning)
        spec.parameters['factor'] = {'schema': {'type': 'number'}, 'description': 'Unverified factor.',
                                     'unit': None, 'uncertainties': [warning]}
        spec.outputs['value']['uncertainties'].append(warning)
        application.register(spec, lambda context, arguments: {'value': 1})
        result = application.invoke('sample', {'factor': 1})
        self.assertEqual({entry['scope'] for entry in result['warnings']},
            {'tools.sample', 'tools.sample.parameters.factor', 'tools.sample.outputs.value'})

    def test_registration_rejects_external_schema_references(self):
        application = Application('reference-check', drivers={})
        spec = scalar_spec()
        spec.outputs['value']['schema'] = {'$ref': 'https://example.invalid/schema.json'}
        with self.assertRaisesRegex(ValueError, 'reference'):
            application.register(spec, lambda context, arguments: {'value': 1})

    def test_duplicate_tool_identity_is_rejected_instead_of_replacing_a_handler(self):
        application = Application('duplicate-check', drivers={})
        application.register(scalar_spec(), lambda context, arguments: {'value': 1})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            application.register(scalar_spec(), lambda context, arguments: {'value': 2})

    def test_read_tool_cannot_hide_a_write_dependency(self):
        from generative_driver.sdk import ManagedDriver
        from test_driver_contract import emit_fixture, controlled_model
        with tempfile.TemporaryDirectory() as directory:
            package, _ = emit_fixture(Path(directory).resolve(), controlled_model())
            driver = ManagedDriver(package, 'not-opened')
            application = Application('effect-check', drivers={'sensor': driver})
            spec = scalar_spec()
            spec.dependencies['sensor'] = ['set_level']
            with self.assertRaisesRegex(ValueError, 'effect'):
                application.register(spec, lambda context, arguments: {'value': 1})

    def test_success_requires_every_declared_output(self):
        application = Application('missing-output', drivers={})
        application.register(scalar_spec(), lambda context, arguments: {})
        result = application.invoke('sample', {})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'output_schema_violation')

    def test_invalid_success_output_becomes_a_structured_failure(self):
        application = Application('schema-check', drivers={})
        application.register(scalar_spec(), lambda context, arguments: {'value': 'not a number'})
        result = application.invoke('sample', {})
        self.assertFalse(result['ok'], result)
        self.assertEqual(result['error']['code'], 'output_schema_violation')
        self.assertEqual(result['outputs'], {})
        self.assertEqual(result['invalid_outputs'], {'value': 'not a number'})
        self.assertEqual(result['calls'], [])
