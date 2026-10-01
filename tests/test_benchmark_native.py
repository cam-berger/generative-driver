"""Synthetic recipes exercise evaluator boundaries, never scored-device answers."""
import copy
import socket
import threading
import unittest
from unittest.mock import patch

from generative_driver.benchmark_support.native import NativeSession, render_stimuli


def recipe():
    return {'stimuli': {'sample': {'minimum': -128, 'maximum': 127,
            'command': 'sysbus WriteDoubleWord 0x50000000 {value}'}},
            'observations': {}, 'reset_commands': []}


class NativeTests(unittest.TestCase):
    def test_out_of_range_stimulus_never_reaches_monitor(self):
        session = NativeSession.attach({'monitor_port': 1,
            'binding': {'host': '127.0.0.1', 'port': 2}}, recipe())
        with patch.object(session, '_monitor') as monitor:
            with self.assertRaises(ValueError):
                session.stimulate({'sample': 128})
            monitor.assert_not_called()

    def test_complete_batch_validated_before_any_effect(self):
        session = NativeSession.attach({'monitor_port': 1,
            'binding': {'host': '127.0.0.1', 'port': 2}}, recipe())
        for values in ({'sample': 12, 'unknown': 3}, {'sample': True}):
            with self.subTest(values=values), patch.object(session, '_monitor') as monitor:
                with self.assertRaises(ValueError):
                    session.reset(values)
                monitor.assert_not_called()

    def test_only_integer_value_template_is_allowed(self):
        self.assertEqual(render_stimuli(recipe(), {'sample': -128}),
                         ['sysbus WriteDoubleWord 0x50000000 -128'])
        for command in ('write {other}', 'write {value.x}', 'write {value}\nquit',
                        'write {value}; quit', 'write {value} {value}'):
            bad = recipe()
            bad['stimuli']['sample']['command'] = command
            with self.subTest(command=command), self.assertRaises(ValueError):
                render_stimuli(bad, {'sample': 1})

    def test_recipe_rejects_sources_and_invalid_observation_sizes_before_io(self):
        for extra in ({'source_files': {'../secret.cs': 'text'}},
                      {'observations': {'x': {'command': 'read', 'width': True, 'count': 1}}},
                      {'observations': {'x': {'command': 'read', 'width': 32, 'count': 0}}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                NativeSession.attach({'monitor_port': 1,
                    'binding': {'host': '127.0.0.1', 'port': 2}}, {**recipe(), **extra})


class MonitorFixture:
    """A local TCP peer, keeping command effects and transport failures observable."""
    def __init__(self, mode='normal'):
        self.mode, self.commands, self.value = mode, [], 0
        self.sock = socket.socket()
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen()
        self.sock.settimeout(.1)
        self.port = self.sock.getsockname()[1]
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.done.set()
        self.thread.join(timeout=2)
        self.sock.close()

    def serve(self):
        while not self.done.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            with conn:
                conn.settimeout(.1)
                conn.sendall(b'(device) ')
                data = b''
                while not self.done.is_set():
                    try:
                        block = conn.recv(4096)
                    except socket.timeout:
                        continue
                    if not block:
                        break
                    data += block
                    if b'\n' not in data:
                        continue
                    command = data.decode(errors='ignore').split('\n')[0].lstrip('\x00\x01\x03')
                    self.commands.append(command)
                    if command.startswith('write '):
                        self.value = int(command.split()[-1])
                    if command == 'reset':
                        self.value = 0
                    if self.mode == 'timeout':
                        self.done.wait(.5)
                        break
                    if self.mode == 'closed':
                        break
                    reply = 'no value' if self.mode == 'incomplete' else str(self.value)
                    if self.mode == 'ansi': reply = '\x1b[32m' + reply + '\x1b[0m'
                    conn.sendall((command + '\r\n' + reply + '\r\n(device) ').encode())
                    break


class MonitorTests(unittest.TestCase):
    def session(self, peer):
        return NativeSession.attach({'monitor_port': peer.port, 'monitor_timeout_seconds': .2,
                'binding': {'host': '127.0.0.1', 'port': 2}},
            {'stimuli': {'sample': {'minimum': -128, 'maximum': 127, 'command': 'write {value}'}},
             'observations': {'sample': {'command': 'read', 'width': 32, 'count': 1}},
             'reset_commands': ['reset']})

    def test_stimulus_reset_and_observation_preserve_raw_evidence(self):
        with MonitorFixture() as peer:
            session = self.session(peer)
            observed = session.stimulate({'sample': 27})
            self.assertEqual(observed['values'], {'sample': 27})
            self.assertFalse(observed['physical'])
            self.assertIn('27', observed['reads'][0]['response'])
            self.assertEqual(session.reset({'sample': 13})['values'], {'sample': 13})
            self.assertEqual(session.binding, {'host': '127.0.0.1', 'port': 2})
            session.set_running(True)
            session.set_running(False)
            self.assertEqual(peer.commands, ['write 27', 'read', 'pause', 'reset', 'write 13', 'read', 'start', 'pause'])

    def test_raw_monitor_responses_survive_color_codes(self):
        with MonitorFixture('ansi') as peer:
            observed = self.session(peer).stimulate({'sample': 27})
            self.assertEqual(observed['values'], {'sample': 27})
            self.assertIn('\x1b[32m', observed['reads'][0]['response'])
            self.assertIn('write 27', observed['controls'][0]['response'])

    def test_incomplete_observation_is_host_failure(self):
        with MonitorFixture('incomplete') as peer:
            with self.assertRaisesRegex(RuntimeError, 'Host fault'):
                self.session(peer).observe()

    def test_monitor_timeout_and_disconnect_are_host_failures(self):
        for mode in ('timeout', 'closed'):
            with self.subTest(mode=mode), MonitorFixture(mode) as peer:
                with self.assertRaisesRegex(RuntimeError, 'Host fault'):
                    self.session(peer).observe()


class LifecycleValidationTests(unittest.TestCase):
    def test_missing_image_and_unsafe_setup_fail_before_launch(self):
        from pathlib import Path
        with self.assertRaises(ValueError):
            NativeSession.start(renode=Path('/missing/tool'), image=Path('/missing/image'), recipe=recipe())

    def test_attached_session_stop_does_not_terminate_unowned_process(self):
        with MonitorFixture() as peer:
            session = MonitorTests().session(peer)
            session.stop()
            self.assertEqual(peer.commands, [])


class BuildTests(unittest.TestCase):
    def authoring(self, root, *, fail=False):
        import hashlib
        import json
        import subprocess
        import sys
        author = root / 'author with spaces'
        author.mkdir()
        script = author / 'compile.py'
        script.write_text('import pathlib,sys\n' + ('sys.exit(7)\n' if fail else
                          'pathlib.Path(sys.argv[1]).write_bytes(b"built image")\n'))
        version = subprocess.check_output([sys.executable, '--version'], text=True).strip()
        manifest = {'schema': 'benchmark-build/1', 'tools': {
            'compiler': {'version': version},
            'objcopy': {'filename': __import__('pathlib').Path(sys.executable).name, 'version': version}},
            'source_hashes': {'compile.py': hashlib.sha256(script.read_bytes()).hexdigest()},
            'steps': [['{compiler}', '{authoring}/compile.py', '{output}/firmware.bin']],
            'outputs': ['firmware.bin']}
        (author / 'build.json').write_text(json.dumps(manifest))
        return author, manifest

    def test_build_with_spaces_runs_external_argv_and_hashes_actual_output(self):
        import hashlib
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, _ = self.authoring(root)
            output = root / 'output with spaces'
            result = build_case(author, Path(sys.executable), output)
            self.assertEqual((output / 'firmware.bin').read_bytes(), b'built image')
            self.assertEqual(result['images']['firmware.bin'], hashlib.sha256(b'built image').hexdigest())
            self.assertEqual(result['execution'], 'reference-build')
            self.assertFalse(result['model_benchmark'])

    def test_nonzero_compiler_writes_no_success_report(self):
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, _ = self.authoring(root, fail=True)
            output = root / 'output'
            with self.assertRaises(RuntimeError):
                build_case(author, Path(sys.executable), output)
            self.assertFalse((output / 'build-report.json').exists())
            self.assertFalse((output / 'calibration.json').exists())

    def test_build_rejects_escaping_paths_and_tool_version_before_process(self):
        import json
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        for change in ('output', 'source', 'step', 'version'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                author, manifest = self.authoring(root)
                if change == 'output': manifest['outputs'] = ['../escaped.bin']
                if change == 'source': manifest['source_hashes'] = {'../outside.py': '0' * 64}
                if change == 'step': manifest['steps'][0][-1] = '{output}/../escaped.bin'
                if change == 'version': manifest['tools']['compiler']['version'] = 'wrong version'
                (author / 'build.json').write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    build_case(author, Path(sys.executable), root / 'output')
                self.assertFalse((root / 'escaped.bin').exists())
                self.assertFalse((root / 'output' / 'build-report.json').exists())

    def test_authoring_and_output_exclude_known_candidate_roots(self):
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, _ = self.authoring(root)
            home = root / 'home'
            marker = root / 'candidate'
            marker.mkdir()
            (marker / '_report_schema.json').write_text('{}')
            with patch('generative_driver.setup.home_path', return_value=home), patch('generative_driver.client.default_home', return_value=home):
                for output in (home / 'runs' / 'r' / 'out', marker / 'out', root / 'runs' / 'r' / 'work' / 'interpret' / 'out', author / 'out'):
                    with self.subTest(output=output), self.assertRaises(ValueError):
                        build_case(author, Path(sys.executable), output)
                    self.assertFalse(output.exists())
                (author / '_report_schema.json').write_text('{}')
                with self.assertRaises(ValueError):
                    build_case(author, Path(sys.executable), root / 'good-output')

    def test_cli_builds_pending_v2_from_private_authoring_without_unlocking(self):
        import contextlib
        import io
        import json
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, _ = self.authoring(root)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(['truth', 'rebuild', '--case', 'tq9-v2', '--authoring-dir', str(author),
                             '--compiler', sys.executable, '--output', str(root / 'output')])
            self.assertEqual(code, 0)
            result = json.loads(out.getvalue())
            self.assertEqual(result['execution'], 'reference-build')
            self.assertFalse('calibration' in result)

    def test_build_rejects_source_argument_missing_from_hash_inventory(self):
        import json
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, manifest = self.authoring(root)
            (author / 'unhashed.py').write_text((author / 'compile.py').read_text())
            manifest['steps'][0][1] = '{authoring}/unhashed.py'
            (author / 'build.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                build_case(author, Path(sys.executable), root / 'output')
            self.assertFalse((root / 'output' / 'build-report.json').exists())

    def test_build_rejects_generated_output_symlink_outside_output_root(self):
        import hashlib
        import json
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, manifest = self.authoring(root)
            probe = root / 'symlink-probe'
            try:
                probe.symlink_to(author / 'compile.py')
            except OSError:
                self.skipTest('Host does not permit test symlinks')
            probe.unlink()
            script = author / 'compile.py'
            script.write_text('import pathlib,sys\npathlib.Path(sys.argv[1]).symlink_to(sys.argv[2])\n')
            manifest['source_hashes']['compile.py'] = hashlib.sha256(script.read_bytes()).hexdigest()
            manifest['steps'][0].append('{authoring}/compile.py')
            (author / 'build.json').write_text(json.dumps(manifest))
            output = root / 'output'
            with self.assertRaises(ValueError):
                build_case(author, Path(sys.executable), output)
            self.assertFalse((output / 'build-report.json').exists())

    def test_build_manifest_cannot_escape_authoring_through_symlink(self):
        import sys
        import tempfile
        from pathlib import Path
        from generative_driver.benchmark_support.authoring import build_case
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            author, _ = self.authoring(root)
            manifest = author / 'build.json'
            external = root / 'external-build.json'
            manifest.rename(external)
            try:
                manifest.symlink_to(external)
            except OSError:
                self.skipTest('Host does not permit test symlinks')
            with self.assertRaises(ValueError):
                build_case(author, Path(sys.executable), root / 'output')
            self.assertFalse((root / 'output').exists())
