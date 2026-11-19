"""The operator saves the command of STM32CubeProgrammer's command-line program, and `doctor` reports it.

Feature 01 of the stm32-acquire Run (docs/features/stm32-acquire/01-stm32-flash-read.md), 01-B1 and 01-B2, at the
command line: `generative_driver.cli.main` and `generative_driver.setup.doctor`. The program is the operator's own
installation. No test here starts it, or anything else: a path that names no file is saved as it is given.

Temporary homes only; no model, no hardware and no program of ST's.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_tool_commands import typed


class ProgramCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='stm32 program ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        # An absolute path on this platform, of a file that is not there.
        self.program = str(self.root / 'STM32CubeProgrammer' / 'bin' / 'STM32_Programmer_CLI')

    def command_line(self, *words):
        """What the tool's command line does for `words` in the temporary home."""
        return typed('--home', str(self.home), *words)

    def configure_program(self, *words):
        return self.command_line('configure-program', '--program', 'stm32-programmer', *words)

    def config(self):
        """The saved configuration, or None when there is none."""
        path = self.home / 'config.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


class ConfigureProgramTests(ProgramCase):
    """`configure-program` saves the program's command and nothing else (01-B1)."""

    def test_01_B1_stm32_configure_program_saves_the_command_beside_the_executors(self):
        configured = self.command_line('configure', '--executor', 'codex', '--command', 'a-codex-runtime', '--model',
                                       'a-model')
        self.assertEqual(configured.status, 0, configured.errors)
        executors = self.config()['executors']
        # It starts nothing and does not ask whether the file exists.
        with patch('subprocess.Popen', side_effect=AssertionError('a process was started')), \
                patch('subprocess.run', side_effect=AssertionError('a process was started')):
            saved = self.configure_program('--command', self.program, '--argument', 'first', '--argument',
                                           'second one')
        self.assertEqual((saved.status, saved.errors), (0, ''))
        self.assertFalse(Path(self.program).exists())
        self.assertEqual(self.config(), {'executors': executors, 'programs': {
            'stm32-programmer': {'command': [self.program, 'first', 'second one']}}})
        self.assertEqual(json.loads(saved.printed), {
            'configured': 'stm32-programmer', 'configuration': {'command': [self.program, 'first', 'second one']},
            'path': str(self.home / 'config.json')})
        # Without an argument the command is the path alone, and the later command replaces the earlier.
        again = self.configure_program('--command', self.program)
        self.assertEqual(again.status, 0, again.errors)
        self.assertEqual(self.config()['programs'], {'stm32-programmer': {'command': [self.program]}})
        self.assertEqual(self.config()['executors'], executors)

    def test_01_B1_stm32_configure_leaves_the_programs_as_they_were(self):
        saved = self.configure_program('--command', self.program, '--argument', 'kept')
        self.assertEqual(saved.status, 0, saved.errors)
        programs = self.config()['programs']
        configured = self.command_line('configure', '--executor', 'codex', '--command', 'a-codex-runtime')
        self.assertEqual(configured.status, 0, configured.errors)
        self.assertEqual(self.config(), {'programs': programs,
                                         'executors': {'codex': {'command': ['a-codex-runtime']}}})

    def test_01_B1_stm32_configure_program_refuses_a_relative_path_a_batch_file_and_an_empty_argument(self):
        batch = str(self.root / 'bin' / 'STM32_Programmer_CLI')
        refused = {
            'a path that is not absolute': (['--command', 'bin/STM32_Programmer_CLI'], 'bin/STM32_Programmer_CLI'),
            'a bare name': (['--command', 'STM32_Programmer_CLI'], 'STM32_Programmer_CLI'),
            'a .bat file': (['--command', batch + '.bat'], batch + '.bat'),
            'a .cmd file': (['--command', batch + '.cmd'], batch + '.cmd'),
            'a .BAT file': (['--command', batch + '.BAT'], batch + '.BAT'),
            'a .Cmd file': (['--command', batch + '.Cmd'], batch + '.Cmd'),
            'an empty argument': (['--command', self.program, '--argument', 'kept', '--argument', ''], 'argument'),
        }
        for label, (words, named) in refused.items():
            with self.subTest(label):
                ended = self.configure_program(*words)
                self.assertEqual((ended.status, ended.printed), (2, ''))
                self.assertIn('generative-driver configure-program: error: ', ended.errors)
                self.assertIn(named, ended.errors)
                self.assertIsNone(self.config())
        # A refusal leaves a saved command as it was.
        self.assertEqual(self.configure_program('--command', self.program).status, 0)
        saved = self.config()
        self.assertEqual(self.configure_program('--command', 'bin/STM32_Programmer_CLI').status, 2)
        self.assertEqual(self.config(), saved)

    def test_01_B1_stm32_configure_program_takes_the_one_program(self):
        ended = self.command_line('configure-program', '--program', 'another-programmer', '--command', self.program)
        self.assertEqual((ended.status, ended.printed), (2, ''))
        self.assertIn("invalid choice: 'another-programmer'", ended.errors)
        self.assertIsNone(self.config())
        helped = self.command_line('configure-program', '--help')
        self.assertEqual(helped.status, 0, helped.errors)
        self.assertEqual(' '.join(helped.printed.split()).partition(' options:')[0],
                         'usage: generative-driver configure-program [-h] --program {stm32-programmer} --command PATH '
                         '[--argument ARG]')


NOT_CONFIGURED = {'configured': False, 'path': None, 'available': False, 'source': 'configuration',
                  'required_for': 'reading STM32 firmware through an ST-LINK probe'}


class DoctorProgramTests(ProgramCase):
    """`doctor` reports the program and never starts it (01-B2)."""

    def entry(self):
        """The `dependencies.stm32_programmer` entry of `doctor`, asked while the program may not be started. `doctor`
        itself starts `uname` for the platform's name, so only a launch that names the program fails."""
        import subprocess
        from generative_driver.setup import doctor
        launch = subprocess.Popen

        def guarded(command, *more, **named):
            if self.program in (command if isinstance(command, (list, tuple)) else [command]):
                raise AssertionError('the program was started')
            return launch(command, *more, **named)
        with patch('subprocess.Popen', guarded):
            report = doctor(self.home)
        self.assertEqual(sorted(report['dependencies']), ['codex', 'java', 'podman', 'stm32_programmer'])
        return report['dependencies']['stm32_programmer']

    def test_01_B2_stm32_doctor_reports_the_program_as_configured_and_whether_its_file_exists(self):
        self.assertEqual(self.entry(), NOT_CONFIGURED)
        self.assertEqual(self.configure_program('--command', self.program, '--argument', 'kept').status, 0)
        configured = {**NOT_CONFIGURED, 'configured': True, 'path': self.program}
        self.assertEqual(self.entry(), configured)
        # A folder at the path is no file.
        Path(self.program).mkdir(parents=True)
        self.assertEqual(self.entry(), configured)
        Path(self.program).rmdir()
        Path(self.program).write_text('never started\n', encoding='utf-8')
        self.assertEqual(self.entry(), {**configured, 'available': True})

    def test_01_B2_stm32_doctor_reports_a_command_no_start_would_take_as_not_configured(self):
        self.home.mkdir()
        Path(self.program).parent.mkdir(parents=True)
        Path(self.program).write_text('never started\n', encoding='utf-8')
        entry = lambda command: json.dumps({'programs': {'stm32-programmer': {'command': command}}})
        unusable = {
            'not valid JSON': '{"programs": ',
            'not a JSON object': '["programs"]',
            'programs that are not an object': '{"programs": ["stm32-programmer"]}',
            'an entry that is not an object': '{"programs": {"stm32-programmer": "a path"}}',
            'no command': '{"programs": {"stm32-programmer": {}}}',
            'another program': json.dumps({'programs': {'another-programmer': {'command': [self.program]}}}),
            'a string': entry(self.program),
            'an empty list': entry([]),
            'a path that is not absolute': entry(['bin/STM32_Programmer_CLI']),
            'a batch file': entry([self.program + '.cmd']),
            'a list holding a number': entry([self.program, 5]),
            'a list holding an empty string': entry([self.program, '']),
        }
        for label, written in unusable.items():
            with self.subTest(label):
                (self.home / 'config.json').write_text(written, encoding='utf-8')
                self.assertEqual(self.entry(), NOT_CONFIGURED)
        (self.home / 'config.json').write_text(entry([self.program]), encoding='utf-8')
        self.assertEqual(self.entry(), {**NOT_CONFIGURED, 'configured': True, 'path': self.program, 'available': True})


if __name__ == '__main__':
    unittest.main()
