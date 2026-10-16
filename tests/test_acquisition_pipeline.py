"""Configurator-to-toolchain contracts with scripted Codex workers and synthetic devices.

Only the external USB/I2C backends are replaced. These tests exercise real tool
dispatch and accepted evidence; they do not measure model or hardware capability.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from generative_driver.configurator import Controller


def eventually(read, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(.02)
    raise AssertionError('Scripted acquisition did not advance within the test deadline')


@contextmanager
def acquisition(binding, effects=None, **options):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        release = root / 'release'
        report = {'status': 'completed', 'summary': 'Scripted worker; no model',
                  'artifacts': [], 'checks': [], 'unresolved': []}
        program = ("import json,pathlib,sys,time\n"
                   "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(.02)\n"
                   "print(json.dumps({'type':'item.completed','item':"
                   "{'type':'agent_message','text':sys.argv[2]}}))\n")
        controller = Controller(root / 'state')
        try:
            run = controller.call('start', {'goal': 'Recover the interface from acquired evidence',
                'binding': binding, 'effects': effects or ['read'], **options,
                'executor_config': {'command': [sys.executable, '-c', program, str(release), json.dumps(report)]}})
            assignment = eventually(lambda: next((e['data'] for e in controller.call('events', run)['events']
                if e['kind'] == 'stage.assigned'), None)
                if controller.call('status', run)['status'] == 'running' else None)
            yield controller, run, assignment, release
        finally:
            controller.close()


@contextmanager
def usb_device():
    calls = {'usb': [], 'serial': []}
    descriptor = bytes.fromhex('120100020000004034127856000100000000')

    class Device:
        def ctrl_transfer(self, kind, request, value, index, length, timeout):
            if (kind, request, value, index) != (0x80, 6, 0x0100, 0):
                raise AssertionError('Unexpected USB request')
            return descriptor[:length]

    usb = types.ModuleType('usb')
    usb.core = types.ModuleType('usb.core')
    usb.util = types.ModuleType('usb.util')
    usb.core.USBError = usb.core.NoBackendError = type('USBError', (Exception,), {})

    def find(**selection):
        calls['usb'].append(selection)
        return [Device()]

    def comports():
        calls['serial'].append(True)
        return [types.SimpleNamespace(vid=0x1234, pid=0x5678, serial_number=None, device='synthetic-port')]

    usb.core.find = find
    usb.util.dispose_resources = lambda device: None
    from interface_runtime import transports
    dependency = transports._dependency
    with patch.dict(sys.modules, {'usb': usb, 'usb.core': usb.core, 'usb.util': usb.util}), \
            patch.object(transports, '_dependency', lambda name: types.SimpleNamespace(comports=comports)
                         if name == 'serial.tools.list_ports' else dependency(name)):
        yield calls


class AcquisitionPipelineTests(unittest.TestCase):
    def test_analysis_installations_require_explicit_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = Controller(Path(directory))
            try:
                for key in ('ghidra_home', 'java_home'):
                    for value in ('relative-installation', 123, ''):
                        with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, key + '.*absolute path'):
                            controller.call('start', {'goal': 'Recover firmware', key: value,
                                'executor_config': {'command': ['no-such-agent-runtime']}})
            finally:
                controller.close()

    def test_blind_firmware_worker_receives_a_sealed_executable_analysis_kit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            firmware = root / 'answer-revealing-source-name.bin'
            firmware.write_bytes(b'\x00\x01\x02\x03fixture firmware bytes')
            settings = {'ghidra_home': str(root / 'ghidra'), 'java_home': str(root / 'jdk')}
            with acquisition(None, inputs={'firmware': str(firmware)}, **settings) as active:
                controller, run, assignment, release = active
                result = controller.call('tool', {**run, 'assignment_id': assignment['id'],
                    'name': 'acquire_firmware_artifact', 'arguments': {
                        'source_path': next(iter(assignment['inputs'])), 'origin': 'provided_binary',
                        'expected_sha256': hashlib.sha256(firmware.read_bytes()).hexdigest()}})
                self.assertTrue(result['available'], result)
                release.touch()
                interpret = eventually(lambda: next((e['data'] for e in controller.call('events', run)['events']
                    if e['kind'] == 'stage.assigned' and e['data']['stage'] == 'interpret'), None))
                workspace = Path(interpret['workspace'])
                names = {Path(p).name for p in interpret['inputs']}
                self.assertTrue({'image.bin', 'ghidra_run.py', 'ExportDecomp.java', 'SeedCortexM.java',
                                 'ANALYSIS_TOOLS.json'} <= names, names)
                self.assertNotIn(firmware.name, names)
                self.assertNotIn('provenance.json', names)
                self.assertEqual((workspace / 'image.bin').read_bytes(), firmware.read_bytes())
                config = json.loads((workspace / 'ANALYSIS_TOOLS.json').read_text())
                self.assertEqual({key: config[key] for key in settings}, settings)
                for path, expected in interpret['inputs'].items():
                    if Path(path).is_file():
                        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), expected)
                helper = subprocess.run([sys.executable, str(workspace / 'ghidra_run.py'), '--help'],
                                        cwd=workspace, capture_output=True, text=True, timeout=10)
                self.assertEqual(helper.returncode, 0, helper.stderr)
                self.assertIn('--ghidra-path', helper.stdout)

    def test_usb_classification_needs_no_serial_listen_or_flash_backend(self):
        for effects in (['read'], ['read', 'reset']):
            with self.subTest(effects=effects), usb_device() as calls, \
                    acquisition({'vendor_id': 0x1234, 'product_id': 0x5678}, effects) as active:
                controller, run, assignment, _ = active
                result = controller.call('tool', {**run, 'assignment_id': assignment['id'],
                    'name': 'acquire_classify', 'arguments': {'transports': ['usb']}})
                self.assertEqual(result['_exit'], 0, result)
                self.assertEqual(result['device_class'], 3)
                self.assertEqual(calls['serial'], [])
                self.assertEqual(calls['usb'], [{'find_all': True, 'idVendor': 0x1234, 'idProduct': 0x5678}])

    def test_ftdi_scan_is_retained_in_the_acquisition_handoff(self):
        connections = []

        class Bus:
            def __init__(self, url, speed):
                connections.append((url, speed))

            def probe(self, address):
                return address == 0x42

            def close(self):
                pass

        with patch('generative_driver._toolchain.runner.FtdiI2c', Bus), \
                acquisition({'url': 'ftdi://ftdi:232h/1'}) as active:
            controller, run, assignment, release = active
            result = controller.call('tool', {**run, 'assignment_id': assignment['id'],
                'name': 'acquire_classify', 'arguments': {}})
            self.assertEqual(result['_exit'], 0, result)
            self.assertEqual(connections, [('ftdi://ftdi:232h/1', 100000)])
            release.touch()
            handoff = eventually(lambda: next((h for h in controller.call('result', run)['accepted_handoffs']
                                               if h['stage'] == 'acquire'), None))
            self.assertEqual(len(handoff['artifacts']), 1, handoff)
            artifact = handoff['artifacts'][0]
            scan = Path(artifact['path']).read_bytes()
            self.assertEqual(json.loads(scan), {'acks': ['0x42'], 'bus_url': 'ftdi://ftdi:232h/1'})
            self.assertEqual(artifact['sha256'], hashlib.sha256(scan).hexdigest())

    def test_usb_handoff_retains_descriptors_and_endpoint_context(self):
        with usb_device(), acquisition({'vendor_id': 0x1234, 'product_id': 0x5678}) as active:
            controller, run, assignment, release = active
            result = controller.call('tool', {**run, 'assignment_id': assignment['id'],
                'name': 'acquire_classify', 'arguments': {'transports': ['usb']}})
            self.assertEqual(result['_exit'], 0, result)
            expected = {Path(path).read_bytes() for path in result['inputs_for_interpret'].values()}
            self.assertEqual(len(expected), 2)
            release.touch()
            handoff = eventually(lambda: next((h for h in controller.call('result', run)['accepted_handoffs']
                                               if h['stage'] == 'acquire'), None))
            self.assertEqual({Path(a['path']).read_bytes() for a in handoff['artifacts']}, expected)
            for artifact in handoff['artifacts']:
                self.assertEqual(artifact['sha256'], hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest())


if __name__ == '__main__':
    unittest.main()
