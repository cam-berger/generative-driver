"""The plugin build `setup` writes, the plugin it replaces in place, and what `doctor` says about both.

Feature 02 of the kit fixes (docs/features/kit-fixes/02-plugin-freshness.md): Codex reinstalls a plugin from a local
marketplace only when its version changes, so the version names what was generated. Every test uses temporary homes
and output folders; none reads Codex's files or runs anything named `codex`.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from package_env import package_env

PLUGIN = 'plugins/generative-driver'
MANIFEST = PLUGIN + '/.codex-plugin/plugin.json'
CONNECTION = PLUGIN + '/.mcp.json'
MARKETPLACE = '.agents/plugins/marketplace.json'


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def server(output):
    """The `generative-driver` server entry of the `.mcp.json` generated in `output`."""
    return read_json(Path(output) / CONNECTION)['mcpServers']['generative-driver']


def snapshot(root):
    """Every folder and file below `root`, hidden ones too, by path relative to it: None for a folder, the bytes of a
    file."""
    root, entries = Path(root), {}
    for folder, folders, files in os.walk(root):
        for name in folders:
            entries[(Path(folder) / name).relative_to(root).as_posix()] = None
        for name in files:
            entries[(Path(folder) / name).relative_to(root).as_posix()] = (Path(folder) / name).read_bytes()
    return entries


def write_files(root, files):
    for path, content in files.items():
        (Path(root) / path).parent.mkdir(parents=True, exist_ok=True)
        (Path(root) / path).write_bytes(content)


# Files of someone else's in an output folder, which `setup` leaves as they were.
OTHERS = {'notes.txt': b'kept', 'plugins/another/plugin.json': b'{"name": "another"}',
          '.agents/plugins/another.json': b'{}', '.agents/skills/own/SKILL.md': b'kept'}


class PluginBuildTests(unittest.TestCase):
    def test_02_B1_setup_writes_the_plugin_build_as_the_version_and_in_the_server_environment(self):
        import generative_driver
        with tempfile.TemporaryDirectory(prefix='plugin build ') as temp:
            root = Path(temp).resolve()
            home, output = root / 'home', root / 'codex install'
            result = subprocess.run(
                [sys.executable, '-m', 'generative_driver', '--home', str(home), 'setup', '--host', 'codex',
                 '--output', str(output)], cwd=root, env={**os.environ, **package_env()}, capture_output=True,
                text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            build = read_json(output / MANIFEST)['version']
            self.assertIsNotNone(re.fullmatch(re.escape(generative_driver.__version__) + r'\+[0-9a-f]{12}', build),
                                 build)
            self.assertEqual(server(output)['env'], {'GENERATIVE_DRIVER_HOME': str(home),
                                                     'GENERATIVE_DRIVER_PLUGIN_BUILD': build})
            self.assertEqual(json.loads(result.stdout)['build'], build)

    def test_02_B2_the_build_stays_for_the_same_inputs_and_changes_with_any_generated_content(self):
        from generative_driver import setup
        with tempfile.TemporaryDirectory(prefix='plugin build inputs ') as temp:
            root = Path(temp).resolve()
            outputs = (root / f'output {number}' for number in range(100))

            def build(**changes):
                return setup.integration_assets('codex', next(outputs), **{'home': root / 'home', **changes})['build']

            first = build()
            self.assertEqual(build(), first)
            skills = root / 'skills'
            shutil.copytree(setup._SKILLS, skills)
            skill = skills / 'generative-driver/SKILL.md'
            skill.write_text(skill.read_text(encoding='utf-8') + '\nAnother line.\n', encoding='utf-8')
            changed = {}
            with patch.object(setup, '_SKILLS', skills):
                changed['skill text'] = build()
            with patch.dict(setup._MANIFEST, description='Another description.'):
                changed['manifest'] = build()
            with patch.dict(setup._MARKETPLACE, interface={'displayName': 'Another name'}):
                changed['marketplace'] = build()
            changed['interpreter'] = build(command=str(root / 'another python'))
            changed['server arguments'] = build(arguments=['-m', 'generative_driver.mcp', '--another'])
            changed['home'] = build(home=root / 'another home')
            for label, value in changed.items():
                with self.subTest(label):
                    self.assertNotEqual(value, first)


class PluginReplacementTests(unittest.TestCase):
    def test_02_B3_setup_replaces_its_own_plugin_and_marketplace_and_leaves_every_other_file(self):
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='plugin replacement ') as temp:
            root = Path(temp).resolve()
            output, expected = root / 'codex install', root / 'expected'
            first = integration_assets('codex', output, home=root / 'first home')
            self.assertEqual((first['replaced'], first['replaced_build']), (False, None))
            write_files(output, {**OTHERS, PLUGIN + '/left by hand.txt': b'goes with the folder'})
            second = integration_assets('codex', output, home=root / 'second home')
            self.assertEqual((second['replaced'], second['replaced_build']), (True, first['build']))
            self.assertNotEqual(second['build'], first['build'])
            integration_assets('codex', expected, home=root / 'second home')
            write_files(expected, OTHERS)
            self.assertEqual(snapshot(output), snapshot(expected))

    def test_02_B3_setup_replaces_a_plugin_generated_before_builds_were_recorded(self):
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='plugin replacement ') as temp:
            root = Path(temp).resolve()
            output, expected = root / 'codex install', root / 'expected'
            integration_assets('codex', output, home=root / 'home')
            # What setup wrote before this Feature: the fixed version, and no build in the server environment.
            manifest, connection = read_json(output / MANIFEST), read_json(output / CONNECTION)
            manifest['version'] = '0.1.0'
            del connection['mcpServers']['generative-driver']['env']['GENERATIVE_DRIVER_PLUGIN_BUILD']
            write_files(output, {MANIFEST: json.dumps(manifest, indent=2).encode(),
                                 CONNECTION: json.dumps(connection, indent=2).encode(), **OTHERS})
            result = integration_assets('codex', output, home=root / 'home')
            self.assertEqual((result['replaced'], result['replaced_build']), (True, None))
            integration_assets('codex', expected, home=root / 'home')
            write_files(expected, OTHERS)
            self.assertEqual(snapshot(output), snapshot(expected))

    def test_02_B4_setup_refuses_a_plugin_folder_or_marketplace_file_it_did_not_generate(self):
        from generative_driver.setup import integration_assets

        def rename(path, name):
            return lambda output: (output / path).write_text(json.dumps({**read_json(output / path), 'name': name}),
                                                             encoding='utf-8')

        def replace_plugin_with_a_file(output):
            shutil.rmtree(output / PLUGIN)
            (output / PLUGIN).write_text('not a folder', encoding='utf-8')

        cases = {'a plugin of another name': (rename(MANIFEST, 'another-plugin'), PLUGIN),
                 'a plugin folder without a manifest': (lambda output: (output / MANIFEST).unlink(), PLUGIN),
                 'a file where the plugin folder goes': (replace_plugin_with_a_file, PLUGIN),
                 'a plugin with no marketplace file beside it': (lambda output: (output / MARKETPLACE).unlink(),
                                                                 PLUGIN),
                 'a marketplace of another name': (rename(MARKETPLACE, 'another-marketplace'), MARKETPLACE),
                 'a marketplace file that is not JSON': (lambda output: (output / MARKETPLACE).write_text(
                     '{"name": ', encoding='utf-8'), MARKETPLACE),
                 'a marketplace file with no plugin folder': (lambda output: shutil.rmtree(output / PLUGIN),
                                                              MARKETPLACE)}
        with tempfile.TemporaryDirectory(prefix='plugin refusal ') as temp:
            root = Path(temp).resolve()
            for label, (change, named) in cases.items():
                with self.subTest(label):
                    output = root / label
                    integration_assets('codex', output, home=root / 'home')
                    write_files(output, OTHERS)
                    change(output)
                    before = snapshot(output)
                    with self.assertRaises(ValueError) as refusal:
                        integration_assets('codex', output, home=root / 'another home')
                    self.assertIn(str(output / named), str(refusal.exception))
                    self.assertEqual(snapshot(output), before)

    @unittest.skipIf(os.name == 'nt' or os.geteuid() == 0, 'a folder without write permission stops only a user '
                     'that is not the superuser, and only outside Windows')
    def test_02_B5_a_failure_to_move_the_plugin_into_place_leaves_the_output_as_it_was(self):
        from generative_driver.setup import integration_assets

        def earlier_plugin(output, home):
            integration_assets('codex', output, home=home)
            write_files(output, OTHERS)

        def only(folder):
            return lambda output, home: write_files(output, {'notes.txt': b'kept', folder + '/kept.txt': b'kept'})

        # Each locked folder makes one move fail: the plugin's or the marketplace file's, whichever comes first.
        cases = {'replacing, with the marketplace folder locked': (earlier_plugin, '.agents/plugins'),
                 'replacing, with the plugins folder locked': (earlier_plugin, 'plugins'),
                 'a first run, with the marketplace folder locked': (only('.agents/plugins'), '.agents/plugins'),
                 'a first run, with the plugins folder locked': (only('plugins'), 'plugins')}
        with tempfile.TemporaryDirectory(prefix='plugin failure ') as temp:
            root = Path(temp).resolve()
            for label, (prepare, locked) in cases.items():
                with self.subTest(label):
                    parent = root / label
                    output = parent / 'codex install'
                    prepare(output, root / 'home')
                    before = snapshot(parent)
                    (output / locked).chmod(0o555)
                    try:
                        with self.assertRaises(OSError):
                            integration_assets('codex', output, home=root / 'another home')
                    finally:
                        (output / locked).chmod(0o755)
                    after = snapshot(parent)
                    self.assertEqual(sorted(after), sorted(before))
                    self.assertEqual(after, before)

    def test_02_B5_a_failure_to_record_the_plugin_puts_back_what_was_in_the_output(self):
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='plugin failure ') as temp:
            root = Path(temp).resolve()
            unwritable_home = root / 'a file, not a folder'
            unwritable_home.write_text('setup cannot record anything in here', encoding='utf-8')
            for label in ('replacing', 'a first run'):
                with self.subTest(label):
                    parent = root / label
                    output = parent / 'codex install'
                    if label == 'replacing':
                        integration_assets('codex', output, home=root / 'home')
                        write_files(output, OTHERS)
                    else:
                        parent.mkdir()
                    before = snapshot(parent)
                    with self.assertRaises(OSError):
                        integration_assets('codex', output, home=unwritable_home)
                    after = snapshot(parent)
                    self.assertEqual(sorted(after), sorted(before))
                    self.assertEqual(after, before)


def plugin_report(home, launched_by=None):
    """The `plugin` entry of doctor's report for `home`, with `launched_by` as the build in its environment."""
    from generative_driver.setup import doctor
    with patch.dict(os.environ):
        os.environ.pop('GENERATIVE_DRIVER_PLUGIN_BUILD', None)
        if launched_by is not None:
            os.environ['GENERATIVE_DRIVER_PLUGIN_BUILD'] = launched_by
        return doctor(home)['plugin']


class DoctorPluginTests(unittest.TestCase):
    def test_02_B7_doctor_reports_the_build_setup_would_write_and_the_build_in_its_environment(self):
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='doctor plugin ') as temp:
            root = Path(temp).resolve()
            written = integration_assets('codex', root / 'output', home=root / 'home')['build']
            report = plugin_report(root / 'home')
            self.assertEqual(set(report), {'current', 'launched_by', 'generated', 'state', 'reason'})
            self.assertEqual(report['current'], written)
            self.assertIsNone(report['launched_by'])
            self.assertEqual(plugin_report(root / 'home', launched_by='0.1.0+0123456789ab')['launched_by'],
                             '0.1.0+0123456789ab')
            self.assertIsNone(plugin_report(root / 'home without a record')['generated'])

    def test_02_B6_setup_records_what_it_generated_and_replaces_an_earlier_record(self):
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='setup record ') as temp:
            root = Path(temp).resolve()
            home = root / 'home'
            for label in ('first', 'second'):
                with self.subTest(label):
                    output = root / f'{label} output'
                    # Another interpreter and other arguments than doctor's: the record keeps the ones setup used.
                    written = integration_assets('codex', output, home=home, command=str(root / f'{label} python'),
                                                 arguments=['-m', 'generative_driver.mcp', f'--{label}'])['build']
                    self.assertEqual(plugin_report(home)['generated'],
                                     {'output': str(output), 'build': written, 'up_to_date': True})

    def test_02_B7_the_generated_plugin_is_out_of_date_when_setup_would_now_write_another_build(self):
        from generative_driver import setup
        with tempfile.TemporaryDirectory(prefix='doctor plugin ') as temp:
            root = Path(temp).resolve()
            skills = root / 'earlier skills'
            shutil.copytree(setup._SKILLS, skills)
            (skills / 'generative-driver/SKILL.md').write_text('An earlier skill.\n', encoding='utf-8')
            with patch.object(setup, '_SKILLS', skills):
                earlier = setup.integration_assets('codex', root / 'output', home=root / 'home')['build']
            self.assertEqual(plugin_report(root / 'home')['generated'],
                             {'output': str(root / 'output'), 'build': earlier, 'up_to_date': False})
            setup.integration_assets('codex', root / 'output', home=root / 'home')
            shutil.copytree(root / 'home', root / 'moved home')
            self.assertFalse(plugin_report(root / 'moved home')['generated']['up_to_date'])

    def test_02_B8_state_and_reason_follow_the_launching_build_and_the_generated_plugin(self):
        from generative_driver import setup
        with tempfile.TemporaryDirectory(prefix='doctor state ') as temp:
            root = Path(temp).resolve()
            fresh, recorded, outdated = root / 'no record', root / 'up-to-date record', root / 'outdated record'
            setup.integration_assets('codex', root / 'up-to-date output', home=recorded)
            skills = root / 'earlier skills'
            shutil.copytree(setup._SKILLS, skills)
            (skills / 'generative-driver/SKILL.md').write_text('An earlier skill.\n', encoding='utf-8')
            with patch.object(setup, '_SKILLS', skills):
                setup.integration_assets('codex', root / 'outdated output', home=outdated)
            other = '0.1.0+0123456789ab'
            # The home, the build in doctor's environment ('current' for the build setup would write there now), the
            # state, and the output folder the command names (None for a placeholder).
            cases = {'launched by the current build': (fresh, 'current', 'current', None),
                     'launched by the current build, beside an outdated record': (outdated, 'current', 'current', None),
                     'launched by another build, with no record': (fresh, other, 'stale', None),
                     'launched by another build, with a record': (recorded, other, 'stale', root / 'up-to-date output'),
                     'launched by no build, with an outdated record': (outdated, None, 'stale',
                                                                       root / 'outdated output'),
                     'launched by no build, with no record': (fresh, None, 'unchecked', None),
                     'launched by no build, with an up-to-date record': (recorded, None, 'unchecked', None)}
            for label, (home, launched_by, state, output) in cases.items():
                with self.subTest(label):
                    if launched_by == 'current':
                        launched_by = plugin_report(home)['current']
                    report = plugin_report(home, launched_by)
                    self.assertEqual(report['state'], state, report)
                    if state == 'current':
                        self.assertIsNone(report['reason'])
                    elif state == 'stale':
                        self.assertIn('reinstall or refresh', report['reason'])
                        self.assertIn('new chat', report['reason'])
                        command = re.search('`([^`]+)`', report['reason']).group(1)
                        if os.name == 'nt':
                            self.assertTrue(command.startswith('& '), command)
                            command = command[2:]
                        arguments = shlex.split(command)
                        self.assertEqual(arguments[:-1], [sys.executable, '-m', 'generative_driver', '--home',
                                                          str(home), 'setup', '--host', 'codex', '--output'])
                        if output is None:
                            self.assertTrue(arguments[-1].startswith('<'), arguments[-1])
                        else:
                            self.assertEqual(arguments[-1], str(output))
                    else:
                        for words in ('No plugin build reached this process', 'command line', 'MCP-only setup',
                                      'generated before builds were recorded', 'driver_doctor', 'from Codex'):
                            self.assertIn(words, report['reason'])

    def test_02_B10_a_record_doctor_cannot_read_is_named_and_the_rest_of_the_report_is_returned(self):
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='doctor record ') as temp:
            root = Path(temp).resolve()
            source = root / 'source home'
            source.mkdir()
            integration_assets('codex', root / 'output', home=source)
            [record] = source.iterdir()  # setup writes nothing else in the home
            damaged = {'not JSON': '{"output": ', 'JSON that is not an object': '[]', 'an empty object': '{}',
                       'values of the wrong type': json.dumps({key: 7 for key in read_json(record)}),
                       'a folder': None}
            environment = {name: value for name, value in os.environ.items()
                           if name != 'GENERATIVE_DRIVER_PLUGIN_BUILD'}
            for label, content in damaged.items():
                home = root / label
                home.mkdir()
                if content is None:
                    (home / record.name).mkdir()
                else:
                    (home / record.name).write_text(content, encoding='utf-8')
                cannot_read = str(home / record.name)
                for launched_by, state in (('current', 'current'), ('0.1.0+0123456789ab', 'stale'),
                                           (None, 'unchecked')):
                    with self.subTest(label, state=state):
                        if launched_by == 'current':
                            launched_by = plugin_report(home)['current']
                        report = plugin_report(home, launched_by)
                        self.assertIsNone(report['generated'])
                        self.assertEqual(report['state'], state, report)
                        self.assertIn('cannot be read', report['reason'])
                        self.assertIn(cannot_read, report['reason'])
                        usual = {'current': None, 'stale': 'reinstall or refresh',
                                 'unchecked': 'No plugin build reached this process'}[state]
                        if usual is None:
                            self.assertTrue(report['reason'].startswith('The record'), report['reason'])
                        else:
                            self.assertLess(report['reason'].index(usual), report['reason'].index(cannot_read))
                with self.subTest(label, command='doctor'):
                    result = subprocess.run(
                        [sys.executable, '-m', 'generative_driver', '--home', str(home), 'doctor', '--json'], cwd=root,
                        env={**environment, **package_env()}, capture_output=True, text=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    report = json.loads(result.stdout)
                    self.assertIsNone(report['plugin']['generated'])
                    self.assertIn(cannot_read, report['plugin']['reason'])
                    self.assertIn('codex', report['dependencies'])


class DoctorOverMcpTests(unittest.TestCase):
    def test_02_B9_driver_doctor_started_as_a_generated_plugin_starts_it_checks_that_plugin(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from generative_driver.setup import integration_assets
        with tempfile.TemporaryDirectory(prefix='plugin over MCP ') as temp:
            root = Path(temp).resolve()
            integration_assets('codex', root / 'output', home=root / 'home')
            connection = server(root / 'output')

            async def plugin(environment):
                params = StdioServerParameters(command=connection['command'], args=connection['args'],
                                               cwd=str(root), env=package_env(**environment))
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reply = await asyncio.wait_for(session.call_tool('driver_doctor', {}), 60)
                        self.assertFalse(reply.is_error, reply)
                        return json.loads(reply.content[0].text)['plugin']

            launched = asyncio.run(plugin(connection['env']))
            self.assertEqual(launched['state'], 'current', launched)
            other = asyncio.run(plugin({**connection['env'], 'GENERATIVE_DRIVER_PLUGIN_BUILD': '0.1.0+0123456789ab'}))
            self.assertEqual(other['state'], 'stale', other)


if __name__ == '__main__':
    unittest.main()
