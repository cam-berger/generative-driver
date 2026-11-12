"""What `doctor` reports about the worker runtime and the optional packages.

Feature 01 of the kit fixes (docs/features/kit-fixes/01-doctor-identity.md): a start uses only the configured Codex
command, so `doctor` asks that command what it is. A script the test writes stands in for the runtime; no test runs a
real Codex or anything named `codex` on `PATH`.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from package_env import package_env

CONFIGURE = 'generative-driver configure --executor codex --command <path>'


def program(path, action):
    """The path of a new program at `path` that the operating system launches directly: a shell script, or on Windows
    a batch file. `action` is one line both shells understand."""
    path.parent.mkdir(exist_ok=True)
    if os.name == 'nt':
        path = path.with_suffix('.cmd')
        path.write_text('@' + action + '\r\n', encoding='utf-8')
    else:
        path.write_text('#!/bin/sh\n' + action + '\n', encoding='utf-8')
        path.chmod(0o755)
    return path


def codex_on_path(directory, marker):
    """The path of a new executable named `codex` in `directory`, which leaves `marker` behind if anything runs it."""
    return program(directory / 'codex', 'echo ran > "' + str(marker) + '"')


def stand_in(directory, body):
    """The command that runs a new script with `body` in `directory`, standing in for the worker runtime."""
    script = Path(directory) / 'stand_in.py'
    script.write_text(body, encoding='utf-8')
    return [sys.executable, str(script)]


def codex_entry(home):
    from generative_driver.setup import doctor
    return doctor(home)['dependencies']['codex']


class DoctorCodexTests(unittest.TestCase):
    def test_01_B1_codex_without_a_configured_command_is_unavailable_whatever_is_on_path(self):
        with tempfile.TemporaryDirectory(prefix='doctor unconfigured ') as temp:
            root = Path(temp)
            codex_on_path(root / 'with codex', root / 'ran')
            (root / 'without codex').mkdir()
            (root / 'other runtime').mkdir()
            (root / 'other runtime/config.json').write_text('{"executors": {"replay": {}}}', encoding='utf-8')
            for search_path in ('with codex', 'without codex'):
                for home in ('no home yet', 'other runtime'):
                    with self.subTest(PATH=search_path, home=home), \
                            patch.dict(os.environ, {'PATH': str(root / search_path)}):
                        entry = codex_entry(root / home)
                        expected = {'configured': False, 'available': False, 'version': None, 'path': None,
                                    'source': None}
                        self.assertEqual({key: entry.get(key, 'absent') for key in expected}, expected)
                        self.assertIn(CONFIGURE, entry['reason'])

    def test_01_B2_on_path_names_the_codex_on_path_and_does_not_run_it(self):
        with tempfile.TemporaryDirectory(prefix='doctor on path ') as temp:
            root = Path(temp)
            marker = root / 'ran'
            target = codex_on_path(root / 'with codex', marker)
            (root / 'without codex').mkdir()
            with patch.dict(os.environ, {'PATH': str(root / 'with codex')}):
                entry = codex_entry(root / 'home')
            self.assertIn('on_path', entry)
            self.assertEqual(Path(entry['on_path']), target)
            self.assertFalse(marker.exists(), 'the codex on PATH was run')
            with patch.dict(os.environ, {'PATH': str(root / 'without codex')}):
                entry = codex_entry(root / 'home')
            self.assertIn('on_path', entry)
            self.assertIsNone(entry['on_path'])

    def test_01_B3_a_configured_command_answering_as_the_codex_cli_is_available_with_its_version(self):
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='doctor configured ') as temp:
            root = Path(temp)
            command = stand_in(root, 'print("codex-cli 9.8.7")\nprint("a later line")\n')
            configure('codex', command, model='selected-model', provider='openai', home=root / 'home')
            self.assertEqual(codex_entry(root / 'home'), {
                'configured': True, 'available': True, 'version': '9.8.7', 'source': 'configuration',
                'path': sys.executable, 'reason': None, 'model': 'selected-model', 'provider': 'openai',
                'required_for': 'Codex workers', 'on_path': None})

    def test_01_B4_a_configured_command_that_resolves_to_no_executable_is_named_as_not_found(self):
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='doctor missing ') as temp:
            home = Path(temp) / 'home'
            configure('codex', ['no-such-codex-runtime', 'kept argument'], home=home)
            entry = codex_entry(home)
            expected = {'configured': True, 'available': False, 'path': None, 'version': None}
            self.assertEqual({key: entry[key] for key in expected}, expected)
            self.assertIn('not found', entry['reason'])
            self.assertIn('no-such-codex-runtime', entry['reason'])

    def test_01_B5_a_command_that_does_not_answer_as_the_codex_cli_has_its_first_line_quoted(self):
        from generative_driver.setup import configure
        answers = {
            'another first line': ('print("Python 3.13.0")', 'Python 3.13.0'),
            'a nonzero exit': ('print("codex-cli 1.0.0"); raise SystemExit(3)', 'codex-cli 1.0.0'),
            'standard error only': ('import sys; print("error: unexpected argument", file=sys.stderr); '
                                    'raise SystemExit(2)', 'error: unexpected argument'),
            'both streams': ('import sys; print("from standard error", file=sys.stderr); '
                             'print("from standard output")', 'from standard output'),
            'a line over 200 characters': ('print("x" * 300)', 'x' * 200),
            'no output': ('pass', 'nothing'),
        }
        with tempfile.TemporaryDirectory(prefix='doctor other answer ') as temp:
            home = Path(temp) / 'home'
            for label, (body, quoted) in answers.items():
                with self.subTest(label):
                    configure('codex', stand_in(temp, body + '\n'), home=home)
                    entry = codex_entry(home)
                    expected = {'configured': True, 'available': False, 'version': None, 'path': sys.executable}
                    self.assertEqual({key: entry[key] for key in expected}, expected)
                    self.assertIsInstance(entry['reason'], str)
                    self.assertIn(quoted, entry['reason'])
                    self.assertNotIn('x' * 201, entry['reason'])
                    self.assertNotIn('from standard error', entry['reason'])

    def test_01_B6_a_command_that_gives_no_answer_in_time_is_stopped_with_its_child_processes(self):
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='doctor no answer ') as temp:
            root = Path(temp)
            home, heartbeat = root / 'home', root / 'heartbeat'
            # Both processes end by themselves after 8 seconds, inside the real limit, so a limit the test failed to
            # shorten shows as another reason.
            child = ('import time\n'
                     'until = time.monotonic() + 8\n'
                     'while time.monotonic() < until:\n'
                     '    with open(' + repr(str(heartbeat)) + ', "a") as stream:\n'
                     '        stream.write("x")\n'
                     '    time.sleep(0.05)\n')
            configure('codex', stand_in(root, 'import subprocess, sys, time\n'
                                              'subprocess.Popen([sys.executable, "-c", ' + repr(child) + '])\n'
                                              'time.sleep(8)\n'), home=home)
            with patch('generative_driver.agents.VERSION_ANSWER_SECONDS', 3):
                entry = codex_entry(home)
            expected = {'configured': True, 'available': False, 'version': None}
            self.assertEqual({key: entry[key] for key in expected}, expected)
            self.assertIn('no answer', entry['reason'])
            self.assertTrue(heartbeat.is_file(), 'the child process of the stand-in never started')
            written = heartbeat.stat().st_size
            time.sleep(0.5)
            self.assertEqual(heartbeat.stat().st_size, written, 'a child process of the command is still running')

    def test_01_B9_a_config_no_start_could_use_is_named_and_the_rest_of_the_report_is_returned(self):
        from generative_driver.setup import doctor
        unusable = {
            'not valid JSON': '{"executors": ',
            'not a JSON object': '["executors"]',
            'executors that are not an object': '{"executors": ["codex"]}',
            'a Codex entry that is not an object': '{"executors": {"codex": "codex"}}',
            'no command': '{"executors": {"codex": {"model": "selected-model"}}}',
            'an empty string': '{"executors": {"codex": {"command": ""}}}',
            'an empty list': '{"executors": {"codex": {"command": []}}}',
            'a list holding an empty string': '{"executors": {"codex": {"command": ["codex", ""]}}}',
            'a list holding a number': '{"executors": {"codex": {"command": ["codex", 5]}}}',
            'a number': '{"executors": {"codex": {"command": 5}}}',
        }
        # What was found in place of an object, named by its JSON type and never quoted from the file.
        found = {'not a JSON object': 'JSON object: array', 'executors that are not an object': 'JSON object: array'}
        with tempfile.TemporaryDirectory(prefix='doctor unusable config ') as temp:
            home = Path(temp) / 'home'
            home.mkdir()
            config = home / 'config.json'
            for label, written in unusable.items():
                with self.subTest(label):
                    config.write_text(written, encoding='utf-8')
                    report = doctor(home)
                    entry = report['dependencies']['codex']
                    expected = {'configured': False, 'available': False, 'version': None, 'path': None}
                    self.assertEqual({key: entry[key] for key in expected}, expected)
                    self.assertIn(str(config.resolve()), entry['reason'])
                    if label in found:
                        self.assertIn(found[label], entry['reason'])
                    self.assertIn(CONFIGURE, entry['reason'])
                    self.assertEqual(sorted(report), ['core', 'dependencies', 'extras', 'home', 'serial_ports',
                                                      'serial_ports_available'])
                    self.assertEqual(sorted(report['dependencies']),
                                     ['arm-none-eabi-gcc', 'codex', 'java', 'podman', 'renode'])
                    self.assertTrue(report['core']['ok'])
            with self.subTest('a string is a command a start accepts'):
                config.write_text(json.dumps({'executors': {'codex': {'command': sys.executable}}}), encoding='utf-8')
                entry = doctor(home)['dependencies']['codex']
                self.assertEqual((entry['configured'], entry['path']), (True, sys.executable))

    def test_01_B12_the_stored_command_is_launched_and_a_launch_error_is_quoted(self):
        import shutil
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='doctor launch ') as temp:
            root = Path(temp)
            home = root / 'home'
            with self.subTest('the stored name is launched, not the path it resolved to'):
                # As on Windows, where a name resolves to a batch file that only a shell can launch by that name.
                resolved = str(program(root / 'resolved/codex', 'echo codex-cli 7.7.7'))
                configure('codex', ['codex-by-a-name-nothing-launches'], home=home)
                which = shutil.which
                with patch('shutil.which', side_effect=lambda name, *rest, **options: (
                        resolved if name == 'codex-by-a-name-nothing-launches' else which(name, *rest, **options))):
                    entry = codex_entry(home)
                with self.assertRaises(OSError) as refused:
                    subprocess.Popen(['codex-by-a-name-nothing-launches', '--version'])
                expected = {'configured': True, 'available': False, 'version': None, 'path': resolved}
                self.assertEqual({key: entry[key] for key in expected}, expected)
                self.assertIn(str(refused.exception), entry['reason'])
                self.assertNotIn('absolute', entry['reason'])
            with self.subTest('an executable file that is not a program'):
                broken = root / 'broken.exe'
                broken.write_text('not a program', encoding='utf-8')
                broken.chmod(0o755)
                configure('codex', [str(broken)], home=home)
                entry = codex_entry(home)
                with self.assertRaises(OSError) as refused:
                    subprocess.Popen([str(broken), '--version'])
                expected = {'configured': True, 'available': False, 'version': None, 'path': str(broken)}
                self.assertEqual({key: entry[key] for key in expected}, expected)
                self.assertIn(str(refused.exception), entry['reason'])
                self.assertNotIn('absolute', entry['reason'])
            with self.subTest('a relative path with a directory part that resolves nowhere'):
                configure('codex', ['tools/absent-codex'], home=home)
                entry = codex_entry(home)
                self.assertFalse(entry['available'])
                self.assertIn('tools/absent-codex', entry['reason'])
                self.assertIn('another directory', entry['reason'])
                self.assertIn('absolute', entry['reason'])


class DoctorExtrasTests(unittest.TestCase):
    def test_01_B8_extras_list_each_optional_package_without_importing_it(self):
        import site
        import tomllib
        from generative_driver.setup import doctor
        with tempfile.TemporaryDirectory(prefix='doctor extras ') as temp:
            packages = Path(temp) / 'packages'
            packages.mkdir()

            def install(name, module, version):
                """Record a distribution whose module cannot be imported, so only its metadata says it is there."""
                recorded = packages / (name + ('-' + version if version else '') + '.dist-info')
                recorded.mkdir()
                (recorded / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + name + '\n'
                                                   + ('Version: ' + version + '\n' if version else ''),
                                                   encoding='utf-8')
                (packages / (module + '.py')).write_text('raise ImportError("' + name + ' was imported")\n',
                                                         encoding='utf-8')

            install('pyusb', 'usb', '8.8.8')
            install('pypdf', 'pypdf', '7.7.7')
            install('esptool', 'esptool', None)
            elsewhere = {Path(entry) for entry in (*site.getsitepackages(), site.getusersitepackages())}
            with patch.object(sys, 'path', [str(packages), *(entry for entry in sys.path
                                                               if Path(entry) not in elsewhere)]):
                report = doctor(Path(temp) / 'home')
            self.assertIn('extras', report)
            extras = report['extras']
            self.assertEqual(
                {name: (entry['installed'], entry['version'], entry['extra']) for name, entry in extras.items()},
                {'pyserial': (False, None, 'hardware'), 'pyusb': (True, '8.8.8', 'hardware'),
                 'pyftdi': (False, None, 'hardware'), 'esptool': (True, None, 'hardware'),
                 'pypdf': (True, '7.7.7', 'docs')})
            declared = tomllib.loads((Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text(
                encoding='utf-8'))['project']['optional-dependencies']
            for name, entry in extras.items():
                with self.subTest(name):
                    self.assertEqual(sorted(entry), ['extra', 'installed', 'required_for', 'version'])
                    self.assertIsInstance(entry['required_for'], str)
                    self.assertTrue(entry['required_for'])
                    self.assertTrue(any(requirement.startswith(name) for requirement in declared[entry['extra']]))


class DoctorCommandTests(unittest.TestCase):
    def test_01_B7_the_check_runs_in_a_new_empty_directory_with_no_input_and_a_worker_environment(self):
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='doctor check ') as temp:
            root = Path(temp)
            caller, home, record = root / 'caller', root / 'home', root / 'record.json'
            caller.mkdir()
            configure('codex', stand_in(root, 'import json, os, sys\n'
                'seen = {"arguments": sys.argv[1:], "directory": os.getcwd(), "entries": os.listdir(),\n'
                '        "input": sys.stdin.read(), "environment": dict(os.environ)}\n'
                'with open(' + repr(str(record)) + ', "w", encoding="utf-8") as stream:\n'
                '    json.dump(seen, stream)\n'
                'with open("left behind.txt", "w", encoding="utf-8") as stream:\n'
                '    stream.write("written where the check ran")\n'
                'print("stand-in noise on standard error", file=sys.stderr)\n'
                'print("codex-cli 1.2.3")\n'), home=home)
            before = {path.name: path.read_bytes() for path in home.iterdir()}
            result = subprocess.run(
                [sys.executable, '-m', 'generative_driver', '--home', str(home), 'doctor', '--json'], cwd=caller,
                env={**os.environ, **package_env(DOCTOR_UNRELATED_FLAG='unrelated', BENCH_EVALUATOR_TOKEN='forbidden')},
                input='input meant for doctor, not for the runtime', capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['dependencies']['codex']['version'], '1.2.3')
            self.assertNotIn('stand-in noise', result.stderr)
            self.assertTrue(record.is_file(), 'the configured command was not run')
            seen = json.loads(record.read_text(encoding='utf-8'))
            self.assertEqual(seen['arguments'], ['--version'])
            self.assertEqual(seen['input'], '')
            self.assertEqual(seen['entries'], [])
            self.assertFalse(Path(seen['directory']).exists(), 'the directory the check ran in was not removed')
            self.assertEqual(seen['environment'].get('PATH'), os.environ.get('PATH'))
            for name in ('DOCTOR_UNRELATED_FLAG', 'BENCH_EVALUATOR_TOKEN', 'PYTHONPATH'):
                self.assertNotIn(name, seen['environment'])
            self.assertEqual(list(caller.iterdir()), [])
            self.assertEqual({path.name: path.read_bytes() for path in home.iterdir()}, before)

    def test_01_B10_doctor_exits_zero_whatever_the_codex_and_extras_entries_say(self):
        configurations = {
            'no configuration': None,
            'a config.json that is not JSON': '{"executors": ',
            'an executable that is not found': json.dumps({'executors': {'codex': {'command': ['no-such-runtime']}}}),
            'a command that answers as another program': json.dumps({'executors': {'codex': {
                'command': [sys.executable]}}}),
        }
        with tempfile.TemporaryDirectory(prefix='doctor exit status ') as temp:
            root = Path(temp)
            (root / 'caller').mkdir()
            for label, configuration in configurations.items():
                with self.subTest(label):
                    home = root / label
                    if configuration is not None:
                        home.mkdir()
                        (home / 'config.json').write_text(configuration, encoding='utf-8')
                    # -S leaves out the installed packages, so from a source checkout, or beside a wheel installed
                    # without its extras, this doctor finds none of the optional packages either.
                    result = subprocess.run(
                        [sys.executable, '-S', '-m', 'generative_driver', '--home', str(home), 'doctor', '--json'],
                        cwd=root / 'caller', env={**os.environ, **package_env()}, capture_output=True, text=True,
                        timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertTrue(report['core']['ok'])
                    self.assertFalse(report['dependencies']['codex']['available'])
                    self.assertEqual(sorted(report['extras']), ['esptool', 'pyftdi', 'pypdf', 'pyserial', 'pyusb'])

    @unittest.skipIf(os.name == 'nt', 'Windows resolves a relative command against the directory of doctor itself')
    def test_01_B12_a_relative_command_that_resolves_from_the_callers_directory_must_be_absolute(self):
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='doctor relative ') as temp:
            root = Path(temp)
            caller, home = root / 'caller', root / 'home'
            caller.mkdir()
            program(caller / 'tools/codex', 'echo codex-cli 7.7.7')
            configure('codex', ['tools/codex'], home=home)
            result = subprocess.run(
                [sys.executable, '-m', 'generative_driver', '--home', str(home), 'doctor', '--json'], cwd=caller,
                env={**os.environ, **package_env()}, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            entry = json.loads(result.stdout)['dependencies']['codex']
            expected = {'configured': True, 'available': False, 'version': None, 'path': 'tools/codex'}
            self.assertEqual({key: entry[key] for key in expected}, expected)
            self.assertIn('tools/codex', entry['reason'])
            self.assertIn('another directory', entry['reason'])
            self.assertIn('absolute', entry['reason'])


class DoctorOverMcpTests(unittest.TestCase):
    def test_01_B11_driver_doctor_returns_the_report_over_stdio_and_the_session_answers_again(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from generative_driver.setup import configure, doctor
        with tempfile.TemporaryDirectory(prefix='doctor over MCP ') as temp:
            root = Path(temp)
            home = root / 'home'
            configure('codex', stand_in(root, 'import sys\n'
                                              'print("stand-in diagnostics", file=sys.stderr, flush=True)\n'
                                              'print("codex-cli 4.5.6", flush=True)\n'
                                              'print("more on standard error", file=sys.stderr, flush=True)\n'
                                              'print("more on standard output")\n'), home=home)

            async def ask_twice():
                params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                                               cwd=str(root), env=package_env(GENERATIVE_DRIVER_HOME=str(home)))
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reports = []
                        for _ in range(2):
                            reply = await asyncio.wait_for(session.call_tool('driver_doctor', {}), 60)
                            self.assertFalse(reply.is_error, reply)
                            reports.append(json.loads(reply.content[0].text))
                        return reports

            first, second = asyncio.run(ask_twice())
            self.assertEqual(first['dependencies']['codex'], {
                'configured': True, 'available': True, 'version': '4.5.6', 'source': 'configuration',
                'path': sys.executable, 'reason': None, 'model': None, 'provider': None,
                'required_for': 'Codex workers', 'on_path': None})
            # `core.platform` is left out: the standard library caches it per process, from the PATH it first saw.
            direct = json.loads(json.dumps(doctor(home)))
            for report in (first, second, direct):
                del report['core']['platform']
            self.assertEqual(first, direct)
            self.assertEqual(second, first)


if __name__ == '__main__':
    unittest.main()
