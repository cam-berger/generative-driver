"""The tool's command line and its MCP server offer nothing of the benchmark, and no tool module imports it.

Feature 09 of the untangle Run (docs/features/untangle/09-commands.md): 09-B6, 09-B7, 09-B8, and of 09-B9 the hosted
workflow and `README.md`. The benchmark is driven from a command of its own, so the tool hands nothing over to it.
This module imports nothing from the benchmark package and names no registered case, so it also runs where the
benchmark is gone. The benchmark's side of the Feature is in the benchmark's tests.

Feature 11 (docs/features/untangle/11-tool-alone.md, 11-B9) took the benchmark's tests and the removal out of the
hosted step, so the step is pinned here as that Feature leaves it.

Temporary homes only; no model, no hardware and no password.
"""
import ast
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from generative_driver import cli, client_interface
from package_env import package_env
from test_client_calls import ServiceCase, service_source
from test_client_surface import IMPORTS
from test_shared_helpers import benchmark_imports, tool_modules

REPO = Path(__file__).resolve().parents[1]
# The commands of `generative-driver`, in the order its help gives them, each with the arguments its usage shows.
COMMANDS = {
    'doctor': '[--json]',
    'configure': '--executor {codex} --command EXECUTABLE [--argument ARGUMENT] [--model MODEL] [--provider PROVIDER] '
                 '[--reasoning-effort REASONING_EFFORT]',
    'setup': '--host {codex} --output OUTPUT',
    'service': '{start,status,stop}',
    'smoke': '',
    'dashboard': '[--port PORT] [--no-open] run_id',
    'run': '--spec SPEC',
    'tools': '',
    'tool': '--arguments ARGUMENTS name',
    'status': 'run_id',
    'events': '[--after AFTER] run_id',
    'result': 'run_id',
    'cancel': 'run_id',
    'resume': '[--budget-seconds BUDGET_SECONDS] [--budget-reason BUDGET_REASON] '
              '[--approve-emulator-tools | --approve-bound-device-tools] run_id',
    'respond': '--observation OBSERVATION run_id',
}

Ended = namedtuple('Ended', 'status printed errors home')


def typed(*words, environment=None):
    """What the tool's command line does for `words` in this process, with `environment` added to the variables it
    sees: its status, what it printed, what it wrote to its error stream, and what `GENERATIVE_DRIVER_HOME` was for
    the process when it ended. An exit of the parser counts as the status. The variables are then put back as they
    were, since the hosted tests run every test module in one process."""
    printed, errors = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, environment or {}), contextlib.redirect_stdout(printed), \
            contextlib.redirect_stderr(errors):
        try:
            status = cli.main(list(words))
        except SystemExit as stopped:
            status = stopped.code
        home = os.environ.get('GENERATIVE_DRIVER_HOME')
    return Ended(status, printed.getvalue(), errors.getvalue(), home)


def one_line(text):
    """`text` with every run of white space as one space: a usage text breaks its lines by the width of the
    terminal."""
    return ' '.join(text.split())


def usage(text):
    """The usage of a help text, as one line."""
    return one_line(text).partition(' positional arguments:')[0].partition(' options:')[0]


class TemporaryFolderCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='tool commands ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'


class CommandLineTests(TemporaryFolderCase):
    """The tool's command line offers nothing of the benchmark (09-B6)."""

    def test_09_B6_benchmark_and_report_end_as_any_command_the_tool_does_not_know(self):
        unknown = typed('no-such-command')
        self.assertEqual((unknown.status, unknown.printed), (2, ''))
        self.assertIn("generative-driver: error: argument command: invalid choice: 'no-such-command'", unknown.errors)
        process = {'GENERATIVE_DRIVER_HOME': str(self.root / 'named by the environment')}
        lines = (('benchmark',), ('benchmark', 'cases'), ('benchmark', 'run', '--case', 'any', '--output', 'folder'),
                 ('benchmark', '--help'), ('report', 'run-1'), ('report', 'run-1', '--output', 'report.json'))
        # Nothing is handed over and nothing is asked of a service.
        with patch('generative_driver.client.call', side_effect=AssertionError('the service was asked')):
            for line in lines:
                for home in ((), ('--home', str(self.home)), ('--home=' + str(self.home),)):
                    with self.subTest(line=' '.join(line), home=home[:1]):
                        ended = typed(*home, *line, environment=process)
                        self.assertEqual((ended.status, ended.printed), (2, ''))
                        self.assertEqual(ended.errors, unknown.errors.replace('no-such-command', line[0]))
                        # The process keeps the home it had: no `--home` is handed on through the environment.
                        self.assertEqual(ended.home, process['GENERATIVE_DRIVER_HOME'])
        self.assertEqual(list(self.root.iterdir()), [])

    def stop_service(self):
        """Stop the service of the home, if a command that should have been refused started one."""
        from generative_driver import client
        if (self.home / 'service.json').exists():
            client.call('shutdown', {}, home=self.home, autostart=False)

    def test_09_B6_the_installed_command_line_refuses_both_with_status_2(self):
        self.addCleanup(self.stop_service)
        for line in (('benchmark', 'cases'), ('--home', str(self.home), 'benchmark', 'cases'),
                     ('report', 'run-1'), ('--home', str(self.home), 'report', 'run-1')):
            with self.subTest(line=' '.join(line[-2:]), home=line[0] == '--home'):
                done = subprocess.run([sys.executable, '-m', 'generative_driver', *line], cwd=self.root,
                                      env={**os.environ, **package_env(GENERATIVE_DRIVER_HOME=str(self.home))},
                                      stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8',
                                      errors='replace', timeout=60)
                self.assertEqual((done.returncode, done.stdout), (2, ''), done.stderr)
                self.assertIn(f"generative-driver: error: argument command: invalid choice: '{line[-2]}'", done.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_09_B6_help_names_neither(self):
        helped = typed('--help')
        self.assertEqual(helped.status, 0, helped.errors)
        self.assertIn('doctor', helped.printed)
        self.assertEqual([line for line in helped.printed.splitlines()
                          if 'benchmark' in line.lower() or 'report' in line.lower()], [])

    def test_09_B6_the_commands_are_exactly_the_fifteen_each_with_the_arguments_it_had(self):
        self.assertEqual(len(COMMANDS), 15)
        self.assertEqual(usage(typed('--help').printed),
                         'usage: generative-driver [-h] [--home HOME] {' + ','.join(COMMANDS) + '} ...')
        for command, arguments in COMMANDS.items():
            with self.subTest(command=command):
                helped = typed(command, '--help')
                self.assertEqual(helped.status, 0, helped.errors)
                self.assertEqual(usage(helped.printed),
                                 one_line(f'usage: generative-driver {command} [-h] {arguments}'))


REQUIRED = 'no default'
# The tools of `generative-driver-mcp`, in the order the server lists them. Each has its description on one line and
# its parameters, each with the JSON types its input schema takes, the values of a choice, and its default.
TOOLS = {
    'driver_doctor': (
        'Check the configured worker runtime, the optional packages and the plugin build, with core Python and the '
        'optional analysis and emulator tools. plugin.state is stale when the plugin in use is not what setup writes '
        'now; plugin.reason then gives the setup command to run again.', {}),
    'driver_start': (
        'Start a durable six-stage run using a configured runtime. Save the returned run_id. Configure the Codex '
        'executor with `generative-driver configure` before starting a run. Supply explicit inputs and operator '
        'binding; effects default to read. request_id deduplicates starts. Effects are read, write, actuate and '
        'reset; grant reset only for devices that restart when their port opens. ghidra_home and java_home optionally '
        'select native analysis installations; the configurator then analyzes the acquired firmware once for every '
        'interpret worker. interpretation is single (the default: one interpret worker) or tasks: an inventory, one '
        'fresh worker per command and a cross-check, which needs that analysis. Task mode is opt-in and its effect on '
        'cost and quality is unmeasured. Set bound-device approval only with the user\'s explicit consent to assigned '
        'tools for that binding/effect scope.',
        {'goal': (['string'], [], REQUIRED), 'executor': (['null', 'string'], ['codex'], None),
         'inputs': (['null', 'object'], [], None), 'binding': (['null', 'object'], [], None),
         'effects': (['array', 'null'], [], None), 'budget_seconds': (['integer'], [], 10800),
         'request_id': (['null', 'string'], [], None),
         'scoped_tool_approval': (['null', 'string'], ['bound-device'], None),
         'ghidra_home': (['null', 'string'], [], None), 'java_home': (['null', 'string'], [], None),
         'interpretation': (['null', 'string'], ['single', 'tasks'], None)}),
    'driver_status': (
        'Reconnect to the current stage, status, blockers, uncertain effects, latest stage notes and final verdict of '
        'a durable run. It also gives `deadline`, when the run\'s budget runs out (seconds since the epoch, like '
        '`created`); `repairs`, the repairs `used` and the repair `budget`; `worker`, null unless a stage worker is '
        'running, else its `stage`, when it `started`, its `last_activity` and whether that was `output`, a `tool` '
        'call or its start, and the interpret `task` it runs ({kind, command}) when the run interprets as tasks; and '
        '`log`, the path of the service\'s log, which holds the traceback behind a failed run.',
        {'run_id': (['string'], [], REQUIRED)}),
    'driver_events': (
        'Read ordered progress and evidence events after the previous cursor.',
        {'run_id': (['string'], [], REQUIRED), 'after': (['integer'], [], 0)}),
    'driver_cancel': (
        'Cancel further work and preserve any uncertainty about an operation interrupted in progress.',
        {'run_id': (['string'], [], REQUIRED)}),
    'driver_result': (
        'Read worker reports, accepted evidence with its notes, evaluator verdicts and the final equivalence verdict '
        'separately.', {'run_id': (['string'], [], REQUIRED)}),
    'driver_resume': (
        'Resume after correcting a blocker; recheck accepted evidence. Set scoped_tool_approval only after explicit '
        'user approval. emulator covers an emulated case\'s assigned tools; bound-device requires the saved explicit '
        'device binding and allowed effects. Neither permits unrelated tools. Set budget_seconds only after explicit '
        'user approval to increase the total wall-clock budget from the original start. Supply a nonblank '
        'budget_reason; the increase is recorded without resetting time or prior attempts.',
        {'run_id': (['string'], [], REQUIRED),
         'scoped_tool_approval': (['null', 'string'], ['bound-device', 'emulator'], None),
         'budget_seconds': (['null', 'number'], [], None), 'budget_reason': (['null', 'string'], [], None)}),
    'driver_respond': (
        'Supply a requested operator observation with its source and evidence. Never invent observations.',
        {'run_id': (['string'], [], REQUIRED), 'observation': (['object'], [], REQUIRED)}),
}


def taken(schema):
    """What an input schema takes for each parameter, however the SDK writes a choice or an optional value: its
    JSON types, the values of a choice, and its default."""
    def parameter(node):
        branches = node.get('anyOf') or [node]
        types = sorted({branch['type'] for branch in branches if 'type' in branch})
        values = sorted({value for branch in branches for value in branch.get('enum', [])}
                        | {branch['const'] for branch in branches if 'const' in branch})
        return types, values, node.get('default') if 'default' in node else REQUIRED
    return {name: parameter(node) for name, node in schema['properties'].items()}


def listed_tools():
    """The tools the MCP server lists over stdio, each as (its name, its description, its input schema)."""
    import asyncio
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def listing():
        with tempfile.TemporaryDirectory(prefix='tool server ') as temporary:
            server = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                                           cwd=temporary, env=package_env(GENERATIVE_DRIVER_HOME=temporary))
            async with stdio_client(server) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    return [(tool.name, tool.description, tool.input_schema)
                            for tool in (await session.list_tools()).tools]
    return asyncio.run(listing())


class ServerTests(unittest.TestCase):
    """The tool's MCP server offers nothing of the benchmark (09-B7)."""

    @classmethod
    def setUpClass(cls):
        cls.listed = listed_tools()

    def test_09_B7_the_server_offers_exactly_the_eight_tools_of_the_tool(self):
        names = [name for name, _, _ in self.listed]
        self.assertEqual(names, list(TOOLS))
        self.assertEqual(len(names), 8)
        self.assertEqual([name for name in names if 'bench' in name or 'suite' in name], [])

    def test_09_B7_each_tool_has_the_parameters_the_input_schema_and_the_description_it_had(self):
        for name, description, schema in self.listed:
            if name not in TOOLS:
                continue
            said, parameters = TOOLS[name]
            with self.subTest(tool=name):
                self.assertEqual(one_line(description), said)
                self.assertEqual(schema['type'], 'object')
                self.assertEqual(taken(schema), parameters)
                self.assertEqual(list(schema['properties']), list(parameters))
                self.assertEqual(schema.get('required', []),
                                 [parameter for parameter, (_, _, default) in parameters.items()
                                  if default is REQUIRED])
        self.assertLessEqual(set(TOOLS), {name for name, _, _ in self.listed})

    def test_09_B7_each_tool_sends_the_service_the_call_it_sent(self):
        from generative_driver import mcp as server
        sent = []

        def service(method, params=None, home=None, **options):
            sent.append((method, params, home, options))
            return {'ok': True, 'answered': method}
        observation = {'observation': 'seen', 'source': 'operator'}
        calls = (
            (server.driver_start, ('Read the gauge',), {},
             ('start', {'goal': 'Read the gauge', 'executor': None, 'inputs': {}, 'binding': None,
                        'effects': ['read'], 'budget_seconds': 10800, 'request_id': None,
                        'scoped_tool_approval': None, 'ghidra_home': None, 'java_home': None})),
            (server.driver_start, ('Read the gauge',), {'effects': [], 'interpretation': 'tasks', 'budget_seconds': 60},
             ('start', {'goal': 'Read the gauge', 'executor': None, 'inputs': {}, 'binding': None, 'effects': [],
                        'budget_seconds': 60, 'request_id': None, 'scoped_tool_approval': None, 'ghidra_home': None,
                        'java_home': None, 'interpretation': 'tasks'})),
            (server.driver_status, ('run-1',), {}, ('status', {'run_id': 'run-1'})),
            (server.driver_events, ('run-1',), {}, ('events', {'run_id': 'run-1', 'after': 0})),
            (server.driver_events, ('run-1',), {'after': 7}, ('events', {'run_id': 'run-1', 'after': 7})),
            (server.driver_cancel, ('run-1',), {}, ('cancel', {'run_id': 'run-1'})),
            (server.driver_result, ('run-1',), {}, ('result', {'run_id': 'run-1'})),
            (server.driver_resume, ('run-1',), {}, ('resume', {'run_id': 'run-1'})),
            (server.driver_resume, ('run-1',),
             {'scoped_tool_approval': 'emulator', 'budget_seconds': 90.5, 'budget_reason': 'A longer look'},
             ('resume', {'run_id': 'run-1', 'scoped_tool_approval': 'emulator', 'budget_seconds': 90.5,
                         'budget_reason': 'A longer look'})),
            (server.driver_respond, ('run-1', observation), {},
             ('respond', {'run_id': 'run-1', 'observation': observation})))
        # A client on Windows starts no service of its own, as before.
        for system, autostart in (('Darwin', True), ('Linux', True), ('Windows', False)):
            with patch('generative_driver.client.call', service), patch('platform.system', return_value=system):
                for tool, given, named, (method, params) in calls:
                    with self.subTest(tool=tool.__name__, named=sorted(named), system=system):
                        del sent[:]
                        answer = tool(*given, **named)
                        self.assertEqual(json.loads(answer), {'ok': True, 'answered': method})
                        self.assertEqual(sent, [(method, params, None, {'autostart': autostart})])


class NoLaunchTests(ServiceCase):
    """The service does not launch the bench harness (09-B8)."""

    def launch(self, **more):
        """The params a `benchmark_spawn` had while the service served it: a folder beside the home, the kind of
        harness and the channel to fetch its request through."""
        folder = self.home.parent / (self.home.name + '-bench') / 'trials' / 'trial-test'
        folder.mkdir(parents=True, exist_ok=True)
        return {'directory': str(folder), 'kind': 'trial',
                'channel': {'schema': 'bench-launch-channel/1', 'port': 12345, 'token': 'a' * 64}, **more}

    def test_09_B8_benchmark_spawn_is_refused_as_any_method_the_service_does_not_have(self):
        known = self.held(self.supplied())['run_id']
        with patch('subprocess.Popen', side_effect=AssertionError('a process was started')):
            for method in ('benchmark_spawn', 'no_such_method'):
                with self.subTest(method=method):
                    # The service looks for the run first, and at the method only once it has found the run.
                    self.assertEqual(self.refused(method, **self.launch(run_id=known)), 'Unknown method: ' + method)
                    self.assertEqual(self.refused(method, run_id=known), 'Unknown method: ' + method)
                    for params in (self.launch(), self.launch(run_id='run-unknown'), {}):
                        self.assertEqual(self.refused(method, **params), 'Unknown run_id')
        # Nothing was written in the folder a harness would have been launched in.
        self.assertEqual(list(Path(self.launch()['directory']).iterdir()), [])

    def test_09_B8_the_service_holds_neither_the_text_benchmark_spawn_nor_the_name_bench_home(self):
        text = service_source().read_text(encoding='utf-8')
        self.assertIn('class Controller', text)
        for gone in ('benchmark_spawn', 'bench_home', 'bench-launch-channel'):
            with self.subTest(text=gone):
                self.assertNotIn(gone, text)


WORKFLOW = REPO / '.github' / 'workflows' / 'test.yml'
# The step of the hosted workflow that runs the tests against the installed wheel, to the end of the file.
STEP = '\n'.join((
    "      - name: Test outside the checkout",
    "        shell: python",
    "        run: |",
    "          from pathlib import Path",
    "          import glob, os, subprocess, sys, tempfile",
    "          repo = Path.cwd()",
    "          with tempfile.TemporaryDirectory(prefix='installed package ') as work:",
    "              env = dict(os.environ)",
    "              env.pop('PYTHONPATH', None)",
    "              env['GD_TEST_WHEEL'] = str(Path(glob.glob('dist/*.whl')[0]).resolve())",
    "              env['GD_TEST_SDIST'] = str(Path(glob.glob('dist/*.tar.gz')[0]).resolve())",
    "              subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(repo / 'tests'), '-v'], "
    "cwd=work, env=env, check=True)",
    "              subprocess.run([sys.executable, '-m', 'generative_driver', 'smoke'], cwd=work, env=env, check=True)",
    ''))
# The SHA-256 of the workflow's text before that step, as UTF-8: the lines this Feature leaves as they were.
BEFORE_THE_STEP = 'eba2de7d9a9912d166930987ca0edfb4cf4735deeb0a359ad3d3d4f60e325f99'
# The three install blocks of `README.md`, each with the language of its fence and its lines.
INSTALL_BLOCKS = (
    ('sh', ['python3 -m venv .venv', '.venv/bin/python -m pip install -e ".[dev]"',
            '.venv/bin/python -m generative_driver doctor', '.venv/bin/python -m generative_driver smoke']),
    ('powershell', ['py -3.13 -m venv .venv', '.\\.venv\\Scripts\\python.exe -m pip install -e ".[dev]"',
                    '.\\.venv\\Scripts\\python.exe -m generative_driver doctor',
                    '.\\.venv\\Scripts\\python.exe -m generative_driver smoke',
                    '.\\.venv\\Scripts\\python.exe -m generative_driver service start']),
    ('sh', ['uv sync --extra dev', 'uv run generative-driver doctor', 'uv run generative-driver smoke']))
SMOKE_SENTENCE = ('The smoke runs one whole run through a service of its own, with a scripted worker and a stand-in '
                  'device. It needs no AI account or hardware and measures no model performance.')


def fenced_blocks(lines):
    """The fenced blocks of the Markdown `lines`, each as (the language of its fence, its lines)."""
    blocks, opened = [], None
    for line in lines:
        if line.startswith('```'):
            if opened is None:
                opened = (line[3:].strip(), [])
            else:
                blocks.append(opened)
                opened = None
        elif opened is not None:
            opened[1].append(line)
    return blocks


class SmokeTests(unittest.TestCase):
    """The hosted tests and `README.md` use the tool's own smoke (09-B9)."""

    def install_section(self):
        """The lines of the section `Install and try it` of `README.md`."""
        lines = (REPO / 'README.md').read_text(encoding='utf-8').splitlines()
        start = lines.index('## Install and try it') + 1
        return lines[start:next(number for number in range(start, len(lines)) if lines[number].startswith('## '))]

    def workflow(self):
        if not WORKFLOW.is_file():
            self.skipTest(f'The hosted workflow is read from a checkout, and {WORKFLOW} is not there')
        return WORKFLOW.read_text(encoding='utf-8')

    def test_09_B9_the_hosted_step_runs_the_tests_and_then_the_tools_smoke_each_with_check(self):
        before, name, step = self.workflow().partition('      - name: Test outside the checkout\n')
        self.assertEqual(name + step, STEP)
        ran = [line.strip() for line in step.splitlines() if 'subprocess.run(' in line]
        self.assertEqual(len(ran), 2)
        self.assertIn("'-m', 'unittest', 'discover'", ran[0])
        self.assertIn("[sys.executable, '-m', 'generative_driver', 'smoke']", ran[1])
        for line in ran:
            self.assertTrue(line.endswith('check=True)'), line)

    def test_09_B9_no_other_line_of_the_workflow_changed_and_none_says_bench(self):
        text = self.workflow()
        before = text.partition('      - name: Test outside the checkout\n')[0]
        self.assertEqual(hashlib.sha256(before.encode('utf-8')).hexdigest(), BEFORE_THE_STEP)
        self.assertEqual([line for line in text.splitlines() if 'bench' in line.lower()], [])

    def test_09_B9_each_install_block_of_the_readme_shows_the_tools_smoke(self):
        blocks = fenced_blocks(self.install_section())
        # The first block clones the repository; the three after it install and try the tool, one for each way.
        self.assertEqual(len(blocks), 4)
        self.assertEqual(tuple(blocks[1:]), INSTALL_BLOCKS)
        for _, lines in blocks[1:]:
            self.assertEqual(len([line for line in lines if line.endswith(' smoke')]), 1)
        text = (REPO / 'README.md').read_text(encoding='utf-8')
        self.assertEqual([line for line in text.splitlines() if 'benchmark run' in line or '--case' in line], [])

    def test_09_B9_the_sentence_after_the_blocks_says_what_the_smoke_is(self):
        section = self.install_section()
        self.assertEqual([line for line in section if line.strip()][-1], SMOKE_SENTENCE)
        text = (REPO / 'README.md').read_text(encoding='utf-8')
        for gone in ('Use a new output directory each time.', 'Smoke tests installation and recorded replay'):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, text)


def indent(line):
    return len(line) - len(line.lstrip())


def under(lines, key):
    """The lines of the YAML `lines` under the line `key`, to the next line indented no deeper, without the blank
    ones. The workflow is read as text, so the tests need no YAML reader."""
    found = []
    for line in lines[lines.index(key) + 1:]:
        if line.strip() and indent(line) <= indent(key):
            break
        found += [line] if line.strip() else []
    return found


def listed(line):
    """The values of the one-line YAML list that ends `line`, without their quotes."""
    return [value.strip().strip('\'"') for value in line[line.index('[') + 1:line.rindex(']')].split(',')]


class HostedJobTests(unittest.TestCase):
    """Which jobs the hosted workflow `Installed package` has (09-B12): the four on which the repository's tests
    must pass. That they pass there is shown by a hosted run alone. No test on one machine shows it, and these do
    not: they hold the four jobs in place, so that what the Behaviour asks cannot shrink unseen."""

    def setUp(self):
        if not WORKFLOW.is_file():
            self.skipTest(f'The hosted workflow is read from a checkout, and {WORKFLOW} is not there')
        self.lines = WORKFLOW.read_text(encoding='utf-8').splitlines()

    def test_09_B12_the_workflow_installed_package_runs_on_every_push_and_on_pull_requests(self):
        self.assertEqual(self.lines[0], 'name: Installed package')
        # Neither event is narrowed to some branches or paths.
        self.assertEqual(under(self.lines, 'on:'), ['  push:', '  pull_request:'])

    def test_09_B12_its_one_job_is_four_macos_and_windows_each_with_python_3_11_and_3_13(self):
        jobs = under(self.lines, 'jobs:')
        with self.subTest('the workflow has one job'):
            self.assertEqual([line for line in jobs if indent(line) == 2], ['  native:'])
        matrix = under(self.lines, '      matrix:')
        with self.subTest('a job that fails does not stop the others'):
            self.assertEqual([line.strip() for line in under(self.lines, '    strategy:') if line not in matrix],
                             ['fail-fast: false', 'matrix:'])
        with self.subTest('the matrix is two systems by two Python versions, with nothing added or left out'):
            self.assertEqual([line.strip() for line in matrix],
                             ['os: [macos-latest, windows-latest]', "python: ['3.11', '3.13']"])
            self.assertEqual([(system, python) for system in listed(matrix[0]) for python in listed(matrix[1])],
                             [('macos-latest', '3.11'), ('macos-latest', '3.13'),
                              ('windows-latest', '3.11'), ('windows-latest', '3.13')])
        with self.subTest('each job runs on the system and with the Python its place in the matrix names'):
            self.assertEqual([line.strip() for line in jobs if 'runs-on:' in line], ['runs-on: ${{ matrix.os }}'])
            versions = [number for number, line in enumerate(jobs) if 'python-version:' in line]
            self.assertEqual([jobs[number].strip() for number in versions], ['python-version: ${{ matrix.python }}'])
            # It is the version the step that sets up Python takes.
            step = [line for line in jobs[:versions[0]] if line.lstrip().startswith('- ')][-1]
            self.assertIn('actions/setup-python@', step)


PACKAGE = 'generative_driver'
# Each way a source could reach the benchmark's code, for a module of the package or of one of its folders, with
# whether the scan below must name it.
REACHES = (
    ('from .benchmark import run', PACKAGE, True),
    ('from . import benchmark', PACKAGE, True),
    ('def call():\n    from .benchmark_support.harness import _spawn', PACKAGE, True),
    ('from .benchmark_support import trial_store', PACKAGE, True),
    ('from .. import benchmark_support', PACKAGE + '.supplied', True),
    ('from ..benchmark_support.labels import equivalence_label', PACKAGE + '.supplied', True),
    ('import generative_driver.benchmark', PACKAGE, True),
    ('import json, generative_driver.benchmark_support.harness as harness', PACKAGE, True),
    ('from generative_driver import benchmark', 'interface_runtime', True),
    ('from generative_driver.benchmark_support.suites import start_suite', 'interface_runtime', True),
    ("importlib.import_module('generative_driver.benchmark')", PACKAGE, True),
    ("def late():\n    return import_module('generative_driver.benchmark_support.suites')", PACKAGE, True),
    ("__import__('generative_driver.benchmark', fromlist=['run'])", PACKAGE, True),
    ("import_module('.benchmark_support', __package__)", PACKAGE, True),
    # What stays: the tool's own modules, a name made at run time, and a text that is given to no import.
    ('from .client import call', PACKAGE, False),
    ('from . import client_interface', PACKAGE, False),
    ('from .benchmarks import run', PACKAGE, False),
    ("importlib.import_module(f'generative_driver._toolchain.{name}')", PACKAGE, False),
    ("__import__('generative_driver.agents', fromlist=['REPORT_SCHEMA'])", PACKAGE, False),
    ("named = 'generative_driver.benchmark'", PACKAGE, False),
)


def reaches(source, package):
    """The lines of the Python `source`, a module of the package `package`, that reach the benchmark's code: an
    import statement at any depth that names `generative_driver.benchmark` or `generative_driver.benchmark_support`,
    as an absolute or a relative import, and a call of `import_module` or `__import__` that is given either name as
    a literal text."""
    def benchmarks(name):
        return name.split('.')[:2] in ([PACKAGE, 'benchmark'], [PACKAGE, 'benchmark_support'])

    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [node.lineno for alias in node.names if benchmarks(alias.name)]
        elif isinstance(node, ast.ImportFrom):
            parts = package.split('.')
            base = parts[:len(parts) - node.level + 1] if node.level else []
            module = '.'.join([*base, *([node.module] if node.module else [])])
            found += [node.lineno for alias in node.names
                      if benchmarks(module) or benchmarks(module + '.' + alias.name)]
        elif isinstance(node, ast.Call) and ast.unparse(node.func).split('.')[-1] in ('import_module', '__import__'):
            given = [argument.value for argument in node.args[:1]
                     if isinstance(argument, ast.Constant) and isinstance(argument.value, str)]
            # A text that starts with a dot names a module from the package of the caller.
            found += [node.lineno for name in given
                      if benchmarks(name) or benchmarks(PACKAGE + name if name.startswith('.') else name)]
    return sorted(set(found))


class NoImportTests(unittest.TestCase):
    """No tool module imports the benchmark, and the interface is as it was (09-B8)."""

    def test_09_B8_no_tool_module_imports_the_benchmarks_code(self):
        modules = tool_modules()
        scanned = {'/'.join([*package.split('.'), path.name]) for path, package in modules}
        self.assertGreater(len(modules), 80)
        for name in ('cli.py', 'mcp.py', 'configurator.py', 'toolkit.py', 'supplied/stages.py'):
            self.assertIn(PACKAGE + '/' + name, scanned)
        reaching = [f'{path.name}:{line}' for path, package in modules
                    for line in reaches(path.read_text(encoding='utf-8'), package)]
        self.assertEqual(reaching, [])
        # The helper every Feature of this Run uses for import statements agrees.
        self.assertEqual([path.name for path, package in modules if benchmark_imports(path, package)], [])

    def test_09_B8_the_scan_sees_each_form_of_an_import_and_leaves_the_tools_own(self):
        for source, package, reached in REACHES:
            with self.subTest(source=source):
                self.assertEqual(bool(reaches(source, package)), reached)

    def test_09_B8_the_interface_is_unchanged(self):
        self.assertEqual(client_interface.VERSION, 4)
        self.assertEqual(client_interface.CALLS, ('ping', 'start', 'resume', 'status', 'events', 'result',
                                                  'attach_device', 'record_evaluation', 'cancel', 'seal'))
        self.assertEqual(dict(client_interface.IMPORTS), IMPORTS)
        # The document is the one of version 4, by the SHA-256 of its text as UTF-8.
        document = (REPO / 'docs' / 'client-interface.md').read_text(encoding='utf-8')
        self.assertEqual(document.splitlines()[0], '# Client interface, version 4')
        self.assertEqual(hashlib.sha256(document.encode('utf-8')).hexdigest(),
                         '01bde7ab829f83fa8eac430f04003366f3b5126d71f69bb3525417869ba5a634')


if __name__ == '__main__':
    unittest.main()
