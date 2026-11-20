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


class ManagedDriverTests(unittest.TestCase):
    """The SDK on a package of format 3 (docs/features/package-format/03-package.md, Behaviour 03-B14)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_03_B14_the_sdk_opens_describes_invokes_and_closes_a_session_on_a_package_of_format_3(self):
        from generative_driver import stand_in_device
        from generative_driver.configurator import Controller
        from generative_driver.sdk import ManagedDriver
        from interface_runtime.contracts import contract_view
        from owner_ipc_fixture import owner_ipc
        from test_package_data import FILES, emitted, files_of, samp1
        result, model, _ = emitted(self.root)
        package = Path(result['package_dir'])
        self.assertEqual(files_of(package), FILES)
        owner = Controller(self.root / 'owner')
        self.addCleanup(owner.close)
        with owner_ipc(owner), stand_in_device.serving() as binding:
            driver = ManagedDriver.open(package, binding=binding, home=owner.home)
            # The contract is the view of the package's driver, and the session describes the same one.
            self.assertEqual(driver.contract, contract_view(json.loads(model.read_text(encoding='utf-8'))))
            self.assertEqual(driver.contract, contract_view(samp1()))
            described = driver.describe()
            self.assertEqual((described['ok'], described['contract'], described['manifest_sha256']),
                             (True, driver.contract, result['manifest_sha256']), described)
            measured = driver.invoke('measure', request_id='measure-once')
            self.assertEqual((measured['schema'], measured['ok'], measured['outputs']),
                             ('driver-result/1', True, {'temperature': 21.5}), measured)
            # A second client reaches the same session by its id, and reads the saved result of that call.
            again = ManagedDriver(package, driver.session_id, home=owner.home)
            status = again.status('measure-once')
            self.assertEqual((status['status'], status['invocation_status'], status['result']['outputs']),
                             ('open', 'finished', {'temperature': 21.5}), status)
            self.assertEqual(driver.close()['status'], 'closed')
            self.assertEqual(again.status()['status'], 'closed')

    def test_03_B14_the_sdk_refuses_a_package_of_an_earlier_format_by_its_name(self):
        from generative_driver.sdk import ManagedDriver
        from test_package_data import emitted, remanifest
        package = Path(emitted(self.root)[0]['package_dir'])
        remanifest(package, lambda manifest: manifest.update(schema='interface-package/2'))
        with self.assertRaisesRegex(ValueError, "package format is not one this kit knows: 'interface-package/2'; "
                                                'it reads interface-package/3'):
            ManagedDriver(package, 'not-opened')
