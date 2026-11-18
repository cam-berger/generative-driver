"""The benchmark is driven from one command of its own, `firmware-bench`, and from nowhere else.

Feature 09 of the untangle Run (docs/features/untangle/09-commands.md): 09-B2 to 09-B5, and of 09-B9 that the smoke
case still runs through the benchmark's command. This file is a benchmark item: it imports the benchmark and runs a
registered case. The tool's side of the Feature is in `tests/test_tool_commands.py`, which stays with the tool.

The command is run as `python -m generative_driver.benchmark` or by calling `benchmark.main`; no test needs
`firmware-bench` on the search path. Temporary homes only; no model, no emulator and no real password.
"""
import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from generative_driver import benchmark, client_interface
from generative_driver.benchmark_support import harness
from tool_fixtures import package_env
from test_benchmark_surface import (BEFORE, FORMS, benchmark_sources, digests, exported, report_module,
                                    sent_methods)


def command(*words, cwd=None, timeout=60):
    """The finished child process of the benchmark's command for `words`, run as `python -m
    generative_driver.benchmark`, with its output as text. The child imports the package under test."""
    return subprocess.run([sys.executable, '-m', 'generative_driver.benchmark', *words], cwd=cwd,
                          env={**os.environ, **package_env()}, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding='utf-8', errors='replace', timeout=timeout)


Done = namedtuple('Done', 'status printed errors home')


def main(*words, environment=None):
    """What `benchmark.main` does for `words` in this process, with `environment` added to the variables it sees:
    its status, what it printed, what it wrote to its error stream, and what `GENERATIVE_DRIVER_HOME` was for the
    process when it ended. An exit of the parser counts as the status. The variables are then put back as they
    were: `--home` sets one for the process, and the hosted tests run every test module in one process."""
    printed, errors = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, environment or {}), contextlib.redirect_stdout(printed), \
            contextlib.redirect_stderr(errors):
        try:
            status = benchmark.main(list(words))
        except SystemExit as stopped:
            status = stopped.code
        home = os.environ.get('GENERATIVE_DRIVER_HOME')
    return Done(status, printed.getvalue(), errors.getvalue(), home)


def one_line(text):
    """`text` with every run of white space as one space: a usage text breaks its lines by the width of the
    terminal."""
    return ' '.join(text.split())


class TemporaryFolderCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='firmware bench ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'


class OwnCommandTests(TemporaryFolderCase):
    """The benchmark has a command of its own, `firmware-bench` (09-B2)."""

    def test_09_B2_python_m_generative_driver_benchmark_runs_the_same_function(self):
        # Two new processes, so that nothing this process holds stands between them: the module run as a program,
        # and `benchmark.main` called by name.
        called = subprocess.run(
            [sys.executable, '-c', 'import sys; from generative_driver import benchmark; '
                                   'raise SystemExit(benchmark.main(sys.argv[1:]))', 'cases'],
            cwd=self.root, env={**os.environ, **package_env()}, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, encoding='utf-8', errors='replace', timeout=60)
        self.assertEqual(called.returncode, 0, called.stderr)
        self.assertGreater(len(json.loads(called.stdout)), 3)
        child = command('cases', cwd=self.root)
        self.assertEqual((child.returncode, child.stdout), (0, called.stdout), child.stderr)
        # And in this process it is the function the declared command names.
        self.assertEqual(main('cases').status, 0)

    def test_09_B2_the_program_name_is_firmware_bench_in_help_usage_and_errors(self):
        for words in ((), ('run',), ('suite',), ('suite', 'resume'), ('corpus', 'plan'), ('set', 'qualify'),
                      ('truth',)):
            with self.subTest(words=words):
                helped = main(*words, '--help')
                self.assertEqual(helped.status, 0, helped.errors)
                self.assertTrue(one_line(helped.printed).startswith(one_line('usage: firmware-bench ' + ' '.join(words))
                                                                    + ' [-h]'), helped.printed[:200])
                self.assertNotIn('gd benchmark', helped.printed)
        refused = main('suite', 'start')
        self.assertEqual((refused.status, refused.printed), (2, ''))
        self.assertIn('usage: firmware-bench suite start ', one_line(refused.errors))
        self.assertIn('firmware-bench suite start: error: the following arguments are required: --manifest',
                      refused.errors)
        # Run as a module, it names itself the same.
        child = command('--help', cwd=self.root)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(child.stdout.startswith('usage: firmware-bench '), child.stdout[:200])

    def test_09_B2_with_no_command_or_one_it_does_not_know_it_ends_with_the_usage_error_and_status_2(self):
        bare = main()
        self.assertEqual((bare.status, bare.printed), (2, ''))
        self.assertIn('usage: firmware-bench ', bare.errors)
        self.assertIn('firmware-bench: error: the following arguments are required: command', bare.errors)
        for word in ('benchmark', 'no-such-command'):
            with self.subTest(word=word):
                unknown = main(word, 'cases')
                self.assertEqual((unknown.status, unknown.printed), (2, ''))
                self.assertIn(f"firmware-bench: error: argument command: invalid choice: '{word}'", unknown.errors)
        child = command('no-such-command', cwd=self.root)
        self.assertEqual((child.returncode, child.stdout), (2, ''))
        self.assertIn("firmware-bench: error: argument command: invalid choice: 'no-such-command'", child.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_09_B2_home_before_the_command_is_the_home_of_the_process_in_both_spellings(self):
        other = {'GENERATIVE_DRIVER_HOME': str(self.root / 'named by the environment')}
        for spelling in (('--home', str(self.home)), ('--home=' + str(self.home),)):
            with self.subTest(spelling=spelling[0]):
                done = main(*spelling, 'cases', environment=other)
                self.assertEqual(done.status, 0, done.errors)
                self.assertEqual(done.home, str(self.home))
        # Without it the process keeps the home it had.
        self.assertEqual(main('cases', environment=other).home, other['GENERATIVE_DRIVER_HOME'])
        # It is given as it was typed, as the tool's command line gave it.
        self.assertEqual(main('--home', 'state', 'cases', environment=other).home, 'state')

    def test_09_B2_a_command_that_finds_the_home_itself_works_in_the_home_given_before_the_command(self):
        asked = []

        def service(method, params=None, home=None, **options):
            asked.append((method, Path(home)))
            return {'ok': True, 'pid': os.getpid()}
        other = {'GENERATIVE_DRIVER_HOME': str(self.root / 'named by the environment')}
        with patch('generative_driver.client.call', service):
            done = main('--home', str(self.home), 'suite', 'status', 'suite-unknown', environment=other)
        self.assertEqual(done.status, 1, done.errors)
        self.assertEqual(json.loads(done.printed), {'ok': False, 'error': 'ValueError', 'reason': 'Unknown suite_id'})
        # The service it asked is that home's, and the suite book it read lies beside that home.
        self.assertEqual(asked, [('ping', self.home)])
        self.assertTrue((self.root / 'home-bench' / 'suites.sqlite3').is_file())
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['home-bench'])

    def test_09_B2_run_keeps_its_own_home_beside_the_one_before_the_command(self):
        first, second = self.root / 'first', self.root / 'second'
        launched = []

        def spawn(folder, arguments, payload):
            launched.append((Path(folder), payload, os.environ.get('GENERATIVE_DRIVER_HOME')))
        followed = {'ok': True, 'trial_id': 'followed'}
        with patch.object(harness, '_spawn', spawn), patch.object(harness, '_follow', return_value=followed):
            done = main('--home', str(first), 'run', '--home', str(second), '--case', 'tq9-v2')
            alone = main('run', '--home', str(second), '--case', 'tq9-v2',
                         environment={'GENERATIVE_DRIVER_HOME': str(first)})
        for ended in (done, alone):
            self.assertEqual((ended.status, json.loads(ended.printed) if ended.printed else None), (0, followed),
                             ended.errors)
        # The process has the home given before the command, and the trial is launched with `run`'s own.
        self.assertEqual([(payload['home'], home) for _, payload, home in launched], [(str(second), str(first))] * 2)
        self.assertEqual(done.home, str(first))
        for folder, _, _ in launched:
            self.assertEqual(folder.parent, self.root / 'second-bench' / 'trials')
        self.assertFalse((self.root / 'first-bench').exists())


COMMANDS_BEFORE = ('cases', 'corpus', 'suite', 'set', 'run', 'trial-resume', 'compare', 'score', 'truth')


class ReportCommandTests(TemporaryFolderCase):
    """The run report's command is `firmware-bench report` (09-B3). The canned runs and the stand-in service are
    those of `test_benchmark_surface.py`, whose tests of the command show that it gives what
    `generative-driver report` gave."""

    def report(self, *words):
        """What `firmware-bench --home <home> report <words>` does, in a process whose own home is another."""
        return main('--home', str(self.home), 'report', *words,
                    environment={'GENERATIVE_DRIVER_HOME': str(self.root / 'named by the environment')})

    def test_09_B3_report_is_a_command_of_the_parser_beside_the_nine_it_had(self):
        usage = one_line(main('--help').printed)
        offered = usage[usage.index('{') + 1:usage.index('}')].split(',')
        self.assertEqual(sorted(offered), sorted([*COMMANDS_BEFORE, 'report']))
        self.assertEqual(one_line(main('report', '--help').printed).split(' options:')[0].split(' positional')[0],
                         'usage: firmware-bench report [-h] [--output OUTPUT] run_id')

    def test_09_B3_it_reads_the_run_in_the_home_before_it_prints_the_report_and_writes_both_files_there(self):
        ended = []

        def typed(run_id):
            ended.append(self.report(run_id))
            self.assertEqual((ended[-1].status, ended[-1].errors), (0, ''))
            return json.loads(ended[-1].printed)
        report, saved, text, _ = exported(report_module(), '3', self.home, typed)
        [done] = ended
        self.assertEqual(done.home, str(self.home))
        self.assertEqual(report['schema'], 'benchmark-report/3')
        self.assertEqual(done.printed, json.dumps(report, indent=2) + '\n')
        self.assertEqual(json.loads(saved), report)
        self.assertEqual(digests(saved, text), BEFORE['3'])
        # The run's snapshot was read in that home, and the two files lie beside it.
        self.assertEqual(sorted(path.name for path in (self.home / 'runs' / 'run-form-3').iterdir()),
                         ['benchmark', 'report.json', 'report.md', 'supplied'])
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ['home'])

    def test_09_B3_it_ends_with_status_0_whenever_it_wrote_the_report_whatever_the_report_says(self):
        module, said = report_module(), {}
        for form in FORMS:
            output = self.root / 'reports' / (form + '.json')

            def typed(run_id):
                done = self.report(run_id, '--output', str(output))
                self.assertEqual((done.status, done.errors), (0, ''), form)
                # With `--output` nothing is written in the run's folder. The files there are for the comparison.
                self.assertFalse((self.home / 'runs' / run_id / 'report.json').exists())
                self.assertEqual(json.loads(done.printed), module.report_run(run_id, home=self.home))
                return json.loads(done.printed)
            with self.subTest(form=form):
                report, saved, text, _ = exported(module, form, self.home, typed)
                self.assertEqual((output.read_text(encoding='utf-8'),
                                  output.with_suffix('.md').read_text(encoding='utf-8')), (saved, text))
                said[form] = (report.get('verdict'), report.get('workflow_status'))
        # Among them are reports of a run that failed and of one that stopped, which end the command the same.
        self.assertIn('failed', [verdict for verdict, _ in said.values()], said)
        self.assertIn('blocked', [status for _, status in said.values()], said)

    def test_09_B3_for_a_run_that_cannot_be_read_it_prints_the_reports_refusal_and_ends_with_status_1(self):
        for answer, refusal in (({'ok': False, 'error': 'ValueError', 'reason': 'Unknown run_id'}, 'Unknown run_id'),
                                ({'ok': False}, 'Cannot read run')):
            asked = []

            def service(method, params=None, home=None, **options):
                asked.append((method, params, Path(home)))
                return dict(answer)
            with self.subTest(refusal=refusal), patch('generative_driver.client.call', service):
                done = self.report('run-unknown')
                self.assertEqual((done.status, done.errors), (1, ''))
                self.assertEqual(json.loads(done.printed), {'ok': False, 'error': refusal})
                self.assertEqual(asked, [('result', {'run_id': 'run-unknown'}, self.home)])
        self.assertEqual(list(self.root.iterdir()), [])


SEVEN_TOOLS = ('driver_benchmark_run', 'driver_benchmark_suite_start', 'driver_benchmark_suite_status',
               'driver_benchmark_suite_events', 'driver_benchmark_suite_result', 'driver_benchmark_suite_cancel',
               'driver_benchmark_suite_resume')
# The commands that do from a terminal what the seven tools did, each with the arguments its usage shows. `run`
# follows below, since its usage names the registered cases.
IN_PLACE_OF_THE_TOOLS = {
    ('suite', 'start'): '--manifest MANIFEST [--executor {codex,replay}] [--options OPTIONS] [--request-id REQUEST_ID]',
    ('suite', 'status'): 'suite_id',
    ('suite', 'events'): '[--after AFTER] suite_id',
    ('suite', 'result'): '[--offset OFFSET] [--limit LIMIT] suite_id',
    ('suite', 'cancel'): 'suite_id',
    ('suite', 'resume'): '[--suite-budget-seconds SUITE_BUDGET_SECONDS] [--budget-reason BUDGET_REASON] '
                         '[--child-budget-seconds CHILD_BUDGET_SECONDS] [--child-budget-reason CHILD_BUDGET_REASON] '
                         '[--options OPTIONS] suite_id',
}
RUN = ('[--case {CASES}] [--scenario SCENARIO] [--case-seed CASE_SEED] [--output OUTPUT] [--executor {codex,replay}] '
       '[--replay-candidate REPLAY_CANDIDATE] [--budget-seconds BUDGET_SECONDS] [--password-file PASSWORD_FILE] '
       '[--renode RENODE] [--ghidra-home GHIDRA_HOME] [--java-home JAVA_HOME] [--home HOME] [--request-id REQUEST_ID] '
       '[--approve-bound-device-tools | --approve-emulator-tools] [--binding-json BINDING_JSON] [--wait]')


def sdk_imports(source):
    """The lines of the Python `source` that import the `mcp` package, the SDK the tool's server is built on, in
    import statements at any depth. A relative import names a module of this repository, never the SDK."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                 [node.module] if isinstance(node, ast.ImportFrom) and not node.level and node.module else [])
        found += [node.lineno for name in names if name.split('.')[0] == 'mcp']
    return found


def usage(*words):
    """The usage the benchmark's command shows for the command `words`, as one line."""
    helped = one_line(main(*words, '--help').printed)
    return helped.partition(' positional arguments:')[0].partition(' options:')[0]


class NoToolTests(unittest.TestCase):
    """The benchmark has no MCP tools (09-B4)."""

    def test_09_B4_no_module_of_the_benchmarks_code_imports_the_mcp_package(self):
        sources = benchmark_sources()
        self.assertGreater(len(sources), 60)
        importing = [f'{path.name}:{line}' for path, _ in sources for line in sdk_imports(path.read_bytes())]
        self.assertEqual(importing, [])
        # The scan sees each form of the import, and a module of the repository is no SDK.
        for source, lines in (('import mcp', [1]), ('import json, mcp.types as types', [1]),
                              ('from mcp import ClientSession', [1]),
                              ('def reuse():\n    from mcp.client.stdio import stdio_client', [2]),
                              ('from .. import mcp', []), ('from .mcp_reuse import owner_evidence', []),
                              ('import mcp_reuse', []), ('from ..device_mcp import main', [])):
            with self.subTest(source=source):
                self.assertEqual(sdk_imports(source), lines)

    def test_09_B4_the_command_does_from_a_terminal_what_the_seven_tools_did(self):
        from generative_driver.benchmark_support.registry import case_ids
        self.assertEqual(usage('run'), 'usage: firmware-bench run [-h] ' + RUN.replace('CASES', ','.join(case_ids())))
        for words, arguments in IN_PLACE_OF_THE_TOOLS.items():
            with self.subTest(command=' '.join(words)):
                self.assertEqual(usage(*words), 'usage: firmware-bench ' + ' '.join(words) + ' [-h] ' + arguments)
        # One command for each tool: a trial's start, and a suite's start, status, events, result, cancel and resume.
        self.assertEqual(len(IN_PLACE_OF_THE_TOOLS) + 1, len(SEVEN_TOOLS))


class Started:
    """What stands in for the process a launch starts: its command, its options and what its input was given."""

    def __init__(self, command, **options):
        self.command, self.options, self.given, self.closed, self.stdin = command, options, b'', False, self

    def write(self, data):
        self.given += data

    def close(self):
        self.closed = True

    def wait(self):
        return 0


class OneLaunchTests(TemporaryFolderCase):
    """The bench harness is started in one way (09-B5)."""

    def test_09_B5_a_trial_and_a_suite_are_launched_through_spawn(self):
        spawned = []

        def spawn(folder, arguments, payload):
            spawned.append((Path(folder), arguments, payload))
        suite = {'manifest': {'entries': [{'case': 'tq9-v2'}]}, 'executor': 'codex', 'options': {}, 'request_id': None}
        with patch.object(harness, '_spawn', spawn), patch.object(harness, '_follow', return_value={'ok': True}), \
                patch.object(harness, '_follow_suite', return_value={'ok': True}), \
                patch('generative_driver.client.call', side_effect=AssertionError('the service was asked')):
            harness.launch('tq9-v2', options={'home': str(self.home)})
            harness.launch_suite(suite, home=self.home)
            harness.launch_suite({'suite_id': 'suite-to-resume'}, home=self.home)
        bench = self.root / 'home-bench'
        self.assertEqual([arguments[0] for _, arguments, _ in spawned], ['--trial-dir', '--suite-dir', '--suite-dir'])
        self.assertEqual([(folder.parent, arguments) for folder, arguments, _ in spawned],
                         [(bench / 'trials', ['--trial-dir', str(spawned[0][0])]),
                          (bench / 'suites', ['--suite-dir', str(spawned[1][0])]),
                          (bench / 'suites', ['--suite-dir', str(spawned[2][0])])])
        self.assertEqual([payload['home'] for _, _, payload in spawned], [str(self.home)] * 3)
        # `_spawn` alone starts a harness process, and the two launches alone call it.
        tree = ast.parse(Path(harness.__file__).read_bytes())
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        calling = sorted(function.name for function in functions for node in ast.walk(function)
                         if isinstance(node, ast.Call) and ast.unparse(node.func) == '_spawn')
        self.assertEqual(calling, ['launch', 'launch_suite'])
        starting = [function.name for function in functions
                    if any(isinstance(node, ast.Call) and ast.unparse(node.func) == 'subprocess.Popen'
                           for node in ast.walk(function))]
        self.assertEqual(starting, ['_spawn'])

    def test_09_B5_spawn_starts_a_detached_harness_in_the_folder_and_hands_it_the_request_on_its_input(self):
        folder = self.root / 'trial'
        folder.mkdir()
        payload = {'trial_id': 'trial-1', 'password_file': str(self.root / 'a secret handle'), 'renode': None}
        with patch('subprocess.Popen', Started):
            process = harness._spawn(folder, ['--trial-dir', str(folder)], payload)
        self.assertEqual(process.command, [sys.executable, '-m', 'generative_driver.benchmark_support.harness',
                                           '--trial-dir', str(folder)])
        self.assertEqual(Path(process.options['cwd']), folder)
        # The request goes to the harness on its input, never on its command line.
        self.assertEqual((json.loads(process.given), process.closed), (payload, True))
        self.assertIs(process.options['stdin'], subprocess.PIPE)
        self.assertNotIn('a secret handle', ' '.join(process.command))
        # It outlives the command that started it, on either system, and writes its output to the log in its folder.
        detached = ({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                    if os.name == 'nt' else {'start_new_session': True})
        self.assertEqual({key: process.options.get(key) for key in detached}, detached)
        self.assertEqual(sorted(path.name for path in folder.iterdir()), ['harness.log'])

    def test_09_B5_the_harness_takes_the_request_it_reads_from_its_input_as_it_is(self):
        folder = self.root / 'folder of the harness'
        requests = ({'trial_id': 'trial-1', 'home': str(self.home), 'case': 'tq9-v2'},
                    # What named the launch channel is a request like any other: nothing is fetched through it.
                    {'schema': 'bench-launch-channel/1', 'port': 9, 'token': 'a' * 64})
        for kind, runner in (('trial', 'run_trial'), ('suite', 'run_suite')):
            for request in requests:
                taken = []
                given = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(request).encode('utf-8')))
                with self.subTest(kind=kind, request=sorted(request)), patch.dict(os.environ), \
                        patch.object(sys, 'stdin', given), \
                        patch.object(harness, runner, lambda directory, request: taken.append((directory, request))), \
                        patch('socket.create_connection', side_effect=AssertionError('a connection was opened')):
                    os.environ.pop('GD_TEST_HARNESS_FIXTURE', None)
                    harness.main(['--' + kind + '-dir', str(folder)])
                    self.assertEqual(taken, [(folder, request)])

    def test_09_B5_every_method_the_benchmarks_code_sends_the_service_is_a_listed_call(self):
        sent = {method for path, _ in benchmark_sources()
                for _, method in sent_methods(path.read_text(encoding='utf-8')) if method is not None}
        # A suite call is served by the suite book in the benchmark's own process, and never reaches the service.
        to_service = {method for method in sent if not method.startswith('suite_')}
        self.assertEqual(sorted(to_service - set(client_interface.CALLS)), [])
        self.assertGreater(len(to_service), 5)
        naming = [path.name for path, _ in benchmark_sources()
                  if 'benchmark_spawn' in path.read_text(encoding='utf-8')]
        self.assertEqual(naming, [])

    def test_09_B5_a_run_that_may_start_no_service_is_refused_where_none_runs(self):
        # The assertion of `test_windows_agent_benchmarks_require_an_owner_but_replay_does_not`
        # (`tests/test_mcp.py`) that called a benchmark function. The test's subject was a tool, and it went.
        from generative_driver import client
        from generative_driver.setup import configure
        self.addCleanup(lambda: (self.home / 'service.json').exists()
                        and client.call('shutdown', {}, home=self.home, autostart=False))
        configure('codex', ['no-such-agent-runtime'], home=self.home)
        refused = benchmark.run('tq9-v2', options={'home': str(self.home)}, autostart=False)
        self.assertFalse(refused.get('ok'), refused)
        self.assertIn('service start', refused.get('reason', ''))
        self.assertFalse((self.home / 'service.json').exists())


class CommandLineTests(unittest.TestCase):
    """The tests of the benchmark's command that were in `tests/test_cli.py`, which is a tool test. They run the
    benchmark's own command (09-B2), and the smoke case with them (09-B9)."""

    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory(prefix='benchmark caller ')
        cls.addClassCleanup(temporary.cleanup)
        cls.caller = Path(temporary.name).resolve()
        cls.home = cls.caller / 'state'
        cls.words = ('--home', str(cls.home), 'run', '--case', 'setup-smoke', '--output', str(cls.caller / 'run'))
        cls.smoke = command(*cls.words, cwd=cls.caller)

    def test_benchmark_smoke_runs_via_installed_cli_and_reports_replay(self):
        result = self.smoke
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["execution"], "scripted-replay")
        self.assertFalse(report["model_benchmark"])
        self.assertEqual(report["verdict"], "passed")
        # It runs without a service and leaves none in the home, which the MCP tool's test of the smoke case showed.
        self.assertFalse((self.home / 'service.json').exists())

    def test_09_B9_the_smoke_case_still_runs_in_the_tests_through_the_benchmarks_command(self):
        self.assertEqual(self.words[2:5], ('run', '--case', 'setup-smoke'))
        self.assertEqual(self.smoke.returncode, 0, self.smoke.stderr)
        report = json.loads(self.smoke.stdout)
        self.assertEqual((report['case'], report['schema']), ('setup-smoke', 'benchmark-report/1'))
        saved = self.caller / 'run' / 'report.json'
        self.assertEqual(Path(report['report']), saved)
        self.assertEqual(json.loads(saved.read_text(encoding='utf-8'))['verdict'], 'passed')
        self.assertFalse(self.home.exists(), 'the smoke case needs no home')

    def test_benchmark_executor_options_refuse_unsupported_choices(self):
        with tempfile.TemporaryDirectory() as temp:
            for args in (("run", "--case", "setup-smoke", "--output", "smoke"),
                         ("suite", "start", "--manifest", "suite.json"),
                         ("corpus", "start", "--plan", "plan.json")):
                with self.subTest(command=args[0]):
                    result = command(*args, "--executor", "unknown-runtime", cwd=temp, timeout=15)
                    self.assertIn("invalid choice", result.stderr)
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
