"""Public benchmark behavior; process fixtures are not authentic firmware evidence."""
import contextlib
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import shutil
import socket
import subprocess
import sys


class NativeBuildTests(unittest.TestCase):
    def test_authored_native_case_describes_its_actual_uart_bridge(self):
        # Authoring fixture only; no execution or validation claim is made for these bytes.
        from unittest.mock import patch
        from generative_driver.benchmark import main
        from generative_driver.benchmark_support.upstream_build import SOURCE_COMMIT
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build = root/'build'; build.mkdir()
            image = b'\x7fELF authoring fixture'
            (build/'firmware.elf').write_bytes(image)
            digest = hashlib.sha256(image).hexdigest()
            record = {'schema': 'benchmark-upstream-build/1', 'case': 'zephyr-modbus-v1',
                'execution': 'native-source-build', 'ok': True, 'source': {'commit': SOURCE_COMMIT},
                'images': {'firmware.elf': digest}, 'rebuild_hashes': [digest, digest], 'tool_versions': {},
                'execution_setup': {'method': 'native-sim', 'artifact': 'firmware.elf',
                    'substitutions': ['TTY UART'], 'validated_behavior': [], 'hardware_gaps': ['Test only']}}
            (build/'build-report.json').write_text(json.dumps(record))
            password = root/'test.password'; password.write_text('test authoring password')
            stream = io.StringIO()
            with patch('generative_driver.benchmark_support.upstream_build.native_recipe',
                       return_value={'backend': 'native-sim'}), \
                    patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value={}), \
                    contextlib.redirect_stdout(stream):
                status = main(['corpus', 'prepare-execution', '--build', str(build),
                    '--password-file', str(password), '--output', str(root/'resources')])
            self.assertEqual(status, 0, stream.getvalue())
            manifest = json.loads((root/'resources/cases/zephyr-modbus-v1/case.json').read_text())
            self.assertEqual(manifest['provenance']['uart_bridge'],
                'Zephyr native TTY UART via a host PTY and a protocol-independent TCP bridge.')

    def test_preparing_native_case_refuses_missing_build_evidence_before_output(self):
        from generative_driver.benchmark import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(['corpus', 'prepare-execution', '--build', str(root/'missing'),
                    '--password-file', str(root/'missing.password'), '--output', str(root/'resources')])
            self.assertEqual(status, 1)
            self.assertFalse(json.loads(stream.getvalue())['ok'])
            self.assertFalse((root/'resources').exists())

    @unittest.skipIf(sys.platform == 'linux', 'Linux runs the real native process test')
    def test_core_reports_the_optional_linux_requirement_without_importing_posix_drivers(self):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root/'firmware.elf'; image.write_bytes(b'\x7fELF fixture')
            provider = DeviceProvider('zephyr-modbus-v1', image, {'backend': 'native-sim'}, None, root/'private')
            with self.assertRaisesRegex(RuntimeError, 'optional Linux environment'):
                provider.start()
            self.assertFalse(provider.status()['healthy'])

    def test_native_build_refuses_missing_pinned_source_before_creating_output(self):
        from generative_driver.benchmark import main
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'build'
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(['corpus', 'build', '--case', 'zephyr-modbus-v1',
                    '--backend', 'native-sim', '--source', str(Path(directory)/'missing'),
                    '--compiler', '/missing/gcc', '--west', '/missing/west', '--output', str(output)])
            self.assertEqual(status, 1)
            self.assertIn('pinned Zephyr source', json.loads(stream.getvalue())['error'])
            self.assertFalse(output.exists())


class NativeIdentityTests(unittest.TestCase):
    def test_native_qualification_requires_measured_comparison_before_running_tools(self):
        from upstream_fixture import payload
        from generative_driver.benchmark_support.upstream_qualification import qualify
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root/'firmware.elf'; image.write_bytes(b'\x7fELF qualification fixture')
            truth = payload()
            truth['images'] = {'firmware.elf': hashlib.sha256(image.read_bytes()).hexdigest()}
            truth['recipe'] = {'backend': 'native-sim'}
            manifest = {'id': 'zephyr-modbus-v1', 'family': 'zephyr-modbus', 'evaluator_version': '1',
                'images': truth['images'], 'execution_setup': {'method': 'native-sim', 'artifact': 'firmware.elf',
                    'substitutions': ['TTY'], 'validated_behavior': [], 'hardware_gaps': ['Physical UART']}}
            with self.assertRaisesRegex(ValueError, 'comparison'):
                qualify(truth, manifest, image, root/'output', renode=None, ghidra_home=None, java_home=None)
            self.assertFalse((root/'output').exists())

    def test_native_reference_qualification_is_bound_to_its_backend_and_executable_inventory(self):
        from test_benchmark_family_calibration import AdmissionTests
        from generative_driver.benchmark_support.calibration import validate_calibration
        manifest, record = AdmissionTests().fixture()
        manifest['images'] = {'firmware.elf': manifest['images']['firmware.bin']}
        manifest['execution_setup'] = {'method': 'native-sim', 'artifact': 'firmware.elf',
            'substitutions': ['TTY and GPIO emulator'], 'validated_behavior': ['public RTU episodes'],
            'hardware_gaps': ['Physical UART timing']}
        record['backend'] = 'native-sim'
        record['images'] = dict(manifest['images'])
        record['analysis']['firmware.elf'] = record['analysis'].pop('firmware.bin')
        record['tools'] = {key: record['tools'][key] for key in ('compiler', 'ghidra', 'java')}
        for run in record['runs']:
            run['backend'] = 'native-sim'
            run['process']['backend'] = 'native-sim'
        checked = validate_calibration(manifest, record)
        self.assertTrue(checked['ok'], checked)
        record['runs'][0]['process']['backend'] = 'renode'
        self.assertFalse(validate_calibration(manifest, record)['ok'])
        record['runs'][0]['process']['backend'] = 'native-sim'
        record['backend'] = 'native-renode'
        self.assertFalse(validate_calibration(manifest, record)['ok'])

    def test_acquisition_and_snapshot_supply_the_pinned_native_elf_and_disclose_substitutions(self):
        import os
        from unittest.mock import patch
        from generative_driver.benchmark import case_root, prepare_stage
        from generative_driver.benchmark_support.registry import pin_case
        from generative_driver.benchmark_support.snapshots import snapshot_case
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resources = root/'resources'
            shutil.copytree(case_root(), resources)
            case = resources/'cases/zephyr-modbus-v1'
            manifest = json.loads((case/'case.json').read_text())
            # Artifact-identity fixture only: deliberately not an executable firmware target.
            image = b'\x7fELF native identity fixture'
            (case/'firmware.elf').write_bytes(image)
            manifest['images'] = {'firmware.elf': hashlib.sha256(image).hexdigest()}
            manifest['execution_setup'] = {'method': 'native-sim', 'artifact': 'firmware.elf',
                'substitutions': ['TTY UART and emulated GPIO'], 'validated_behavior': [],
                'hardware_gaps': ['Physical RS-485 timing']}
            manifest['calibration'] = {'status': 'pending'}
            (case/'case.json').write_text(json.dumps(manifest))
            with patch.dict(os.environ, GENERATIVE_DRIVER_BENCH_ROOT=str(resources)):
                pin = pin_case('zephyr-modbus-v1', None, 0)
                saved = snapshot_case(root/'run', pin, {})
                assignment = prepare_stage('zephyr-modbus-v1', 'acquire', root/'run', root/'worker')
            self.assertEqual(Path(assignment['context']['source_path']).read_bytes(), image)
            self.assertEqual(assignment['context']['expected_sha256'], hashlib.sha256(image).hexdigest())
            self.assertEqual(saved['case_pin']['manifest']['execution_setup'], manifest['execution_setup'])
            self.assertEqual(set(saved['public_files']), {'firmware.elf'})


class ExecutionComparisonTests(unittest.TestCase):
    def test_complete_looking_traffic_cannot_hide_wrong_firmware_or_unconfirmed_execution(self):
        import copy
        from generative_driver.benchmark import case_root, main
        from generative_driver.benchmark_support.snapshots import canonical_digest
        plan = json.loads((case_root()/'qualification/zephyr-modbus-rtu.json').read_text())
        good = {'schema': 'firmware-execution-observation/1', 'case': plan['case'],
            'execution': 'firmware-reference-exercise', 'method': 'native-sim',
            'plan_sha256': canonical_digest(plan), 'source_commit': plan['source_commit'],
            'complete': True, 'stop_confirmed': True, 'image_sha256': '1'*64,
            'process': {'pid': 123, 'executable': '/fixture/native.elf', 'image_sha256': '1'*64},
            'records': [{'id': s['id'], 'request_hex': s.get('request_hex'),
                'reply_hex': s.get('reply_hex'), 'state': s['state']} for s in plan['steps']]}
        reference = {**copy.deepcopy(good), 'method': 'renode', 'image_sha256': '2'*64,
                     'process': {**good['process'], 'image_sha256': '2'*64}}
        for field, value in (('source_commit', 'wrong'), ('complete', False), ('stop_confirmed', False),
                             ('failure', 'lost observation'), ('process', {**good['process'], 'image_sha256': '3'*64})):
            native = {**copy.deepcopy(good), field: value}
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                a, b = Path(directory)/'native.json', Path(directory)/'reference.json'
                a.write_text(json.dumps(native)); b.write_text(json.dumps(reference))
                stream = io.StringIO()
                with contextlib.redirect_stdout(stream):
                    status = main(['corpus', 'compare-execution', str(a), str(b)])
                self.assertEqual(status, 1)
                self.assertEqual(json.loads(stream.getvalue())['verdict'], 'failed')

    def test_execution_exercise_requires_authenticated_setup_before_creating_output(self):
        from generative_driver.benchmark import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(['corpus', 'exercise-execution', '--case', 'zephyr-modbus-v1',
                    '--password-file', str(root/'missing.password'), '--output', str(root/'output')])
            self.assertEqual(status, 1)
            self.assertFalse(json.loads(stream.getvalue())['ok'])
            self.assertFalse((root/'output').exists())

    def test_comparison_rejects_changed_effect_even_when_both_records_claim_success(self):
        from generative_driver.benchmark import case_root, main
        from generative_driver.benchmark_support.snapshots import canonical_digest
        import copy
        plan = json.loads((case_root()/'qualification/zephyr-modbus-rtu.json').read_text())
        # Scripted observations of the published qualification plan, with a planted wrong effect.
        native = {'schema': 'firmware-execution-observation/1', 'case': plan['case'],
            'execution': 'scripted-test', 'model_benchmark': False, 'method': 'native-sim',
            'plan_sha256': canonical_digest(plan), 'image_sha256': '1'*64, 'passed': True,
            'records': [{'id': step['id'], 'request_hex': step.get('request_hex'),
                'reply_hex': step.get('reply_hex'), 'state': step['state']} for step in plan['steps']]}
        reference = {**copy.deepcopy(native), 'method': 'renode', 'image_sha256': '2'*64}
        native['records'][2]['state'] = {**native['records'][2]['state'], 'register3': 999}
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'native.json', Path(directory)/'reference.json'
            a.write_text(json.dumps(native)); b.write_text(json.dumps(reference))
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                status = main(['corpus', 'compare-execution', str(a), str(b)])
            result = json.loads(stream.getvalue())
        self.assertEqual(status, 1)
        self.assertEqual(result['verdict'], 'failed')
        self.assertIn('native-sim/write-register/state/register3', result['failed_checks'])
        self.assertFalse(result['model_benchmark'])


@unittest.skipUnless(sys.platform == 'linux' and shutil.which('gcc'), 'optional Linux native backend')
class NativeProviderTests(unittest.TestCase):
    def test_native_target_keeps_state_observes_memory_and_replaces_through_the_provider(self):
        from generative_driver.benchmark_support.device_provider import DeviceProvider
        with tempfile.TemporaryDirectory(prefix='native process ') as directory:
            root = Path(directory)
            source, image = root/'target.c', root/'target.elf'
            source.write_text('''#include <fcntl.h>
#include <stdint.h>
#include <unistd.h>
volatile uint32_t registers[4], gpio;
int main(int argc, char **argv) {
    int fd = open(argv[1], O_RDWR); unsigned char command, value;
    if (fd < 0) return 2;
    while (read(fd, &command, 1) == 1) {
        if (read(fd, &value, 1) != 1) return 3;
        registers[1] = (uint32_t)value << 16;
        gpio = value ? (1 << 12) : 0;
        if (write(fd, &value, 1) != 1) return 4;
    }
    return 0;
}
''', encoding='utf-8')
            subprocess.run(['gcc', '-no-pie', '-g', str(source), '-o', str(image)], check=True,
                           capture_output=True, timeout=30)
            symbols = {parts[0]: int(parts[2], 16) for line in subprocess.check_output(
                ['nm', '-P', str(image)], text=True).splitlines() if len(parts := line.split()) >= 3
                and parts[0] in ('registers', 'gpio')}
            recipe = {'backend': 'native-sim', 'uart_args': ['{tty}'], 'stimuli': {},
                'observations': {**{'register_pair'+str(i): {'address': symbols['registers'] + 4*i,
                    'width': 32, 'count': 1} for i in range(4)},
                    'gpio_d': {'address': symbols['gpio'], 'width': 32, 'count': 1}}}
            # Case snapshots contain the pinned bytes without executable permissions.
            image.chmod(0o444)
            provider = DeviceProvider('zephyr-modbus-v1', image, recipe, None, root/'private')
            try:
                descriptor = provider.start()
                self.assertEqual(image.stat().st_mode & 0o777, 0o444)
                self.assertEqual(hashlib.sha256(Path('/proc', str(provider.session.process.pid), 'exe').read_bytes()).hexdigest(),
                                 hashlib.sha256(image.read_bytes()).hexdigest())
                self.assertEqual(descriptor['time_policy'], 'host-clock-with-process-pauses')
                self.assertEqual(provider.observe()['values']['register3'], 0)
                first = provider.session.process
                with provider.window(), socket.create_connection(tuple(provider.binding[k] for k in ('host', 'port')), 2) as peer:
                    peer.sendall(b'w\x1b')
                    self.assertEqual(peer.recv(1), b'\x1b')
                observed = provider.observe()['values']
                self.assertEqual((observed['register3'], observed['coil0']), (27, 1))
                receipt = provider.replace(descriptor['session_id'])
                self.assertTrue(receipt['initial_state_verified'])
                self.assertIsNotNone(first.poll())
                self.assertEqual(provider.observe()['values']['register3'], 0)
                self.assertNotEqual(provider.binding, descriptor['binding'])
                changed = image.with_suffix('.changed')
                changed.write_bytes(image.read_bytes() + b'changed after admission')
                changed.chmod(0o755)
                changed.replace(image)
                before_refusal = provider.session.process
                with self.assertRaisesRegex(ValueError, 'image.*changed|hash'):
                    provider.session.reset({})
                self.assertIsNone(before_refusal.poll(), 'Reject a changed image before stopping the current target')
            finally:
                provider.stop()
            self.assertFalse(provider.status()['healthy'])
