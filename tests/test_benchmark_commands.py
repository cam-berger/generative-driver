"""The benchmark is driven from one command of its own, `firmware-bench`, and from nowhere else.

Feature 09 of the untangle Run (docs/features/untangle/09-commands.md): 09-B1 to 09-B5, 09-B10, 09-B11, and of 09-B9
that the smoke case still runs through the benchmark's command. This file is a benchmark item: it imports the
benchmark, runs a registered case and reads the deletion list, and the deletion removes it with them. The tool's side
of the Feature is in `tests/test_tool_commands.py`, which stays.

The command is run as `python -m generative_driver.benchmark` or by calling `benchmark.main`; no test needs
`firmware-bench` on the search path. Temporary homes only; no model, no emulator and no real password.
"""
import ast
import contextlib
import hashlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import types
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

import generative_driver
import interface_runtime
from deletion_marking import problems, tool_test_modules
from generative_driver import benchmark, client_interface
from generative_driver.benchmark_support import harness
from package_env import package_env
from test_benchmark_surface import (BEFORE, FORMS, benchmark_sources, digests, exported, report_module,
                                    sent_methods)
from test_deletion_marking import (SmallTreeCase, deletion_list, require_working_tree, stripped_repository,
                                   tool_package)
from test_shared_helpers import benchmark_imports, tool_modules

REPO = Path(__file__).resolve().parents[1]
LIST = REPO / 'docs' / 'benchmark-deletion-list.md'
READY, STILL_USED, KEPT = 'Ready to delete', 'Still used by the tool', 'Kept'
MODULES, COMMANDS, PASSAGES = 'Tool modules that import the benchmark', 'Commands', 'Passages'


def table(heading):
    """The text of the deletion list from `heading` to the next heading."""
    return LIST.read_text(encoding='utf-8').partition(f'\n## {heading}\n')[2].partition('\n## ')[0]


def rows(heading):
    """The rows of the deletion list's table under `heading`, each as its cells, the first without its backticks."""
    lines = [line.strip() for line in table(heading).splitlines() if line.startswith('|')]
    cells = [[cell.strip() for cell in line.strip('|').split('|')] for line in lines]
    # The first two lines of a table are its column names and the rule under them.
    return [[row[0].strip('`'), *row[1:]] for row in cells[2:]]


def covering(path):
    """The (heading, item) of every row of the list whose item is `path` or a folder that holds it."""
    return [(heading, row[0]) for heading in (READY, STILL_USED, KEPT) for row in rows(heading)
            if row[0] == path or row[0].endswith('/') and path.startswith(row[0])]


# The rows that named what the merge of `main` at fcd078c removed, by table.
MERGED_AWAY = {
    READY: ('docs/features/bench-harness/', 'docs/superpowers/plans/2026-10-28-tq9-replacement.md',
            'docs/superpowers/plans/2026-10-30-grader-identity.md', 'docs/superpowers/plans/2026-10-30-scorecard.md',
            'docs/superpowers/plans/2026-11-01-generated-grader.md'),
    KEPT: ('docs/superpowers/plans/2026-10-28-driver-contract-mcp.md',
           'docs/superpowers/specs/2026-10-28-driver-contract-mcp-design.md')}
# The documents and records `Ready to delete` names, and every row under `Kept`, as the merge left them. The merge
# touched no source and no test.
READY_DOCUMENTS = [
    'docs/benchmark-deletion-list.md', 'docs/generated-device-interface.md',
    'docs/adr/0001-benchmark-registration-and-selection.md', 'docs/adr/0003-generated-devices-register-by-set.md',
    'docs/adr/0004-compare-systems-through-a-scoring-set.md', 'docs/adr/0005-keep-the-device-generator-private.md',
    'docs/adr/0006-seal-graded-scoring-trials.md', 'docs/adr/0007-one-validator-defines-a-generated-device.md',
    '.scratch/benchmark-baseline/', '.scratch/benchmark-review/']
KEPT_ROWS = [
    ['docs/verification.md', 'The published record of past results.'],
    ['bench/evidence/', 'The evidence files of the published record.'],
    ['docs/features/untangle/', 'This Run\'s own folder.'],
    ['docs/features/kit-fixes/', 'Record of the kit-fixes Run.'],
    ['docs/adr/0002-driver-contract-and-generated-mcp.md', 'Decision record of the tool.'],
    ['docs/adr/0008-run-the-installed-runtime-over-package-data.md', 'Decision record of the tool.'],
    ['docs/adr/0009-device-first-interpretation.md', 'Decision record of the tool.'],
    ['docs/research/', 'Research notes, a record.'],
    ['.scratch/effect-labels/', 'Spec and issues of the tool, a record.'],
    ['.scratch/learning-loop/', 'Parked spec of the tool, a record.']]


class MergeTests(unittest.TestCase):
    """The deletion list names only what the merge left (09-B1)."""

    def test_09_B1_the_seven_rows_that_name_what_the_merge_removed_are_gone(self):
        self.assertEqual(sum(map(len, MERGED_AWAY.values())), 7)
        for heading, items in MERGED_AWAY.items():
            listed = [row[0] for row in rows(heading)]
            for item in items:
                with self.subTest(item=item):
                    self.assertFalse((REPO / item).exists(), 'the merge removed it')
                    self.assertNotIn(item, listed)
        text = LIST.read_text(encoding='utf-8')
        for folder in ('docs/superpowers/', 'docs/features/bench-harness/'):
            self.assertNotIn(folder, text)

    def test_09_B1_every_row_of_the_list_names_a_file_or_folder_that_exists(self):
        # A row under `Commands` names a command, so that table is left out here.
        named = [(heading, row[0]) for heading in (READY, STILL_USED, MODULES, PASSAGES, KEPT)
                 for row in rows(heading)]
        self.assertGreater(len(named), 140)
        missing = [f'`{item}` under {heading}' for heading, item in named
                   if not ((REPO / item).is_dir() and any(path.is_file() for path in (REPO / item).rglob('*'))
                           if item.endswith('/') else (REPO / item).is_file())]
        self.assertEqual(missing, [])

    def test_09_B1_the_file_the_merge_added_is_covered_by_the_row_of_its_folder(self):
        added = '.scratch/benchmark-review/bench-harness-noticed.md'
        self.assertTrue((REPO / added).is_file())
        self.assertEqual(covering(added), [(READY, '.scratch/benchmark-review/')])

    def test_09_B1_no_other_row_changed_for_the_merge(self):
        ready = [row[0] for row in rows(READY)]
        self.assertEqual([item for item in ready if not item.startswith(('tests/', 'src/'))], READY_DOCUMENTS)
        self.assertEqual(rows(KEPT), KEPT_ROWS)


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

    def test_09_B2_pyproject_declares_firmware_bench_as_the_benchmarks_main(self):
        project = tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        self.assertEqual(project['scripts'].get('firmware-bench'), 'generative_driver.benchmark:main')
        self.assertTrue(callable(benchmark.main))

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
    those of `tests/test_benchmark_surface.py`, whose tests of the command show that it gives what
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


def python_sources():
    """The Python modules under `src/`: those of the two packages as they are installed and, where the tests run
    from a checkout, those of its `src/` folder."""
    roots = [Path(package.__file__).resolve().parent for package in (generative_driver, interface_runtime)]
    roots += [REPO / 'src'] if (REPO / 'src').is_dir() else []
    return sorted({path.resolve() for root in roots for path in root.rglob('*.py')})


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

    def test_09_B4_no_python_module_under_src_holds_the_name_of_any_of_the_seven_tools(self):
        sources = python_sources()
        self.assertGreater(len(sources), 150)
        self.assertIn(Path(benchmark.__file__).resolve(), sources)
        holding = sorted({f'{path.name} holds {name}' for path in sources
                          for name in re.findall(r'driver_benchmark\w*', path.read_text(encoding='utf-8'))})
        self.assertEqual(holding, [])
        for tool in SEVEN_TOOLS:
            self.assertRegex(tool, r'^driver_benchmark\w*$')

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

    def test_09_B4_pyproject_declares_three_commands_and_no_other(self):
        project = tomllib.loads((REPO / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        self.assertEqual(project['scripts'], {'generative-driver': 'generative_driver.cli:main',
                                              'generative-driver-mcp': 'generative_driver.mcp:main',
                                              'firmware-bench': 'generative_driver.benchmark:main'})
        for table in ('gui-scripts', 'entry-points'):
            self.assertNotIn(table, project)

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

    def test_09_B5_the_launch_through_the_service_and_its_channel_are_gone(self):
        for name in ('_spawn_via_owner', '_launch_payload'):
            with self.subTest(name=name):
                self.assertFalse(hasattr(harness, name))
        gone = ('_spawn_via_owner', '_launch_payload', 'bench-launch-channel', 'via_owner')
        holding = [f'{path.name} holds {text}' for path, _ in benchmark_sources() for text in gone
                   if text in path.read_text(encoding='utf-8')]
        self.assertEqual(holding, [])

    def test_09_B5_no_function_that_launches_has_a_parameter_via_owner_and_each_keeps_autostart(self):
        from generative_driver.benchmark_support import suite_book, suites
        launching = {'benchmark.run': benchmark.run, 'harness.launch': harness.launch,
                     'harness.launch_suite': harness.launch_suite, 'suites.start_suite': suites.start_suite,
                     'suites.resume_suite': suites.resume_suite}
        for name, function in {**launching, 'suite_book.suite_call': suite_book.suite_call}.items():
            parameters = inspect.signature(function).parameters
            with self.subTest(function=name):
                self.assertNotIn('via_owner', parameters)
                self.assertEqual((parameters['autostart'].kind, parameters['autostart'].default),
                                 (inspect.Parameter.KEYWORD_ONLY, True))

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


# The benchmark's documents that show its command lines. Each has the SHA-256 of its text after this Feature, as
# UTF-8: its text at the base (af775e3) with the command lines rewritten by the rules of 09-B10, the three passages
# of `bench/README.md` taken out, and nothing else changed. Then the lines in fenced blocks that show the benchmark's
# command, the code spans in prose that name it, and the fenced lines that show a command of the tool.
DOCUMENTS = {
    'bench/README.md': ('4541d75fbf99fb64012ddbd5ff303dcf3de99e582ef31afcf09eab16002261a3', 20, 2, (
        'python -m generative_driver doctor',
        'generative-driver --home /operator/state/benchmark configure --executor codex --command '
        '/absolute/path/to/codex --provider openai --model YOUR_MODEL',
        'generative-driver --home /operator/state/benchmark status RUN_ID',
        'generative-driver --home /operator/state/benchmark result RUN_ID',
        'generative-driver --home /operator/state/suite configure --executor codex --command /absolute/path/to/codex '
        '--model YOUR_MODEL',
        'generative-driver --home /operator/state/suite service start')),
    'bench/groundtruth/README.md': ('8405693d0934aedb59db408847bd9d2af9ff6fbf44988694126b13e2aa363573', 6, 0, ()),
    'docs/benchmark-case-authoring.md': ('4e58b1f536ca3234cf5ad49c7d139ce366c74c79d7c1b566f115bd34673f8a9e', 9, 11,
                                         ()),
    'docs/benchmark-corpus.md': ('9147c1c9724e84d8071db3c9dc7aeb51f3bcfbaed0649b5044dddb1b0db065dc', 14, 0, (
        'generative-driver --home /operator/state/corpus configure --executor codex --command /absolute/path/to/codex '
        '--provider openai --model YOUR_MODEL',
        'generative-driver --home /operator/state/corpus service start')),
    'docs/generated-device-interface.md': ('2c62aa1e5baf604020a96c780010285b03ad6598267049db497151dfd6515850', 0, 3,
                                           ()),
    'docs/verification.md': ('a299a250a42ca811619520e9db0aef04195ac8bcd8694c73b556da3f3f67b75b', 2, 1, (
        'python -m generative_driver setup --host codex --output "codex setup"',
        'python -m generative_driver smoke')),
}
# The tool's command line with a benchmark command, as a document showed it before this Feature.
OLD_FORMS = re.compile(r'generative_driver benchmark|\bgd (report|benchmark)\b'
                       r'|generative-driver( --home[= ]\S+)? (benchmark|report)\b')
RECORDS = ('docs/features/', 'docs/adr/', 'docs/research/', '.scratch/')


def shown(document):
    """What the document at `document` under the repository shows: the lines of its fenced blocks without their
    indentation, and the code spans of its prose."""
    fenced, spans, inside = [], [], False
    for line in (REPO / document).read_text(encoding='utf-8').splitlines():
        if line.lstrip().startswith('```'):
            inside = not inside
        elif inside:
            fenced.append(line.strip())
        else:
            spans += re.findall(r'`([^`]+)`', line)
    return fenced, spans


class DocumentTests(unittest.TestCase):
    """The benchmark's documents show the command that exists, and no MCP tool (09-B10)."""

    def test_09_B10_every_command_line_of_the_benchmark_is_shown_with_firmware_bench(self):
        commands = {*COMMANDS_BEFORE, 'report'}
        for document, (_, lines, _, _) in DOCUMENTS.items():
            fenced, _ = shown(document)
            typed = [line for line in fenced if line.startswith('firmware-bench ')]
            with self.subTest(document=document):
                self.assertEqual(len(typed), lines)
                self.assertEqual([line for line in fenced if OLD_FORMS.search(line)], [])
                # Each names a command of the benchmark's parser, after the home where it gives one.
                words = [re.sub(r'^firmware-bench( --home \S+)? ', '', line).split()[0] for line in typed]
                self.assertEqual(sorted(set(words) - commands), [])
        # The report's command, which the tool's command line had, is the benchmark's.
        self.assertIn('firmware-bench --home /operator/state/benchmark report RUN_ID --output reports/trial.json',
                      shown('bench/README.md')[0])

    def test_09_B10_a_code_span_in_prose_that_names_a_command_starts_with_firmware_bench(self):
        named = re.compile(r'(benchmark|firmware-bench|gd) (' + '|'.join({*COMMANDS_BEFORE, 'report'}) + r')\b')
        for document, (_, _, spans, _) in DOCUMENTS.items():
            naming = [span for span in shown(document)[1] if named.match(span)]
            with self.subTest(document=document):
                self.assertEqual(len(naming), spans)
                self.assertEqual([span for span in naming if not span.startswith('firmware-bench ')], [])
        # The 16 spans that started with `benchmark`, and the one that named the report's command.
        self.assertEqual(sum(spans for _, _, spans, _ in DOCUMENTS.values()), 16 + 1)
        self.assertIn('firmware-bench report <run>', shown('docs/benchmark-case-authoring.md')[1])

    def test_09_B10_a_line_that_shows_a_command_of_the_tool_is_unchanged(self):
        for document, (_, _, _, tools) in DOCUMENTS.items():
            fenced, _ = shown(document)
            with self.subTest(document=document):
                self.assertEqual(tuple(line for line in fenced
                                       if re.match(r'(generative-driver|python -m generative_driver) ', line)), tools)

    def test_09_B10_the_three_passages_of_the_bench_readme_are_gone_and_it_names_no_mcp_tool(self):
        text = (REPO / 'bench' / 'README.md').read_text(encoding='utf-8')
        for passage in ('MCP uses `scoped_tool_approval: "emulator"`.', 'MCP: `driver_benchmark_run` takes',
                        'MCP offers suite start/status/events/result/cancel/resume; CLI exports/compares.'):
            with self.subTest(passage=passage):
                self.assertNotIn(passage, text)
        self.assertEqual([number for number, line in enumerate(text.splitlines(), 1) if re.search(r'\bMCP\b', line)
                          or any(tool in line for tool in SEVEN_TOOLS)], [])
        # Nothing was put in their place: what stood around them is as it was.
        for kept in ('Emulator approval covers assigned emulator tools only. Use `original` for V2 families.',
                     '\n\nUse the same owner for cancel and resume:\n', 'which `suite compare` pairs device by device.'
                     '\n\n## Replay a saved candidate\n'):
            with self.subTest(kept=kept):
                self.assertIn(kept, text)
        # The two tools of the tool that the document names stay.
        for tool in ('driver_respond', 'driver_result'):
            self.assertEqual(text.count('`' + tool + '`'), 1)

    def test_09_B10_no_document_outside_the_records_shows_the_tools_command_line_with_a_benchmark_command(self):
        documents = sorted(path.relative_to(REPO).as_posix() for path in REPO.rglob('*.md')
                           if not any(part.startswith('.') for part in path.relative_to(REPO).parts[:-1]))
        documents = [document for document in documents if not document.startswith(RECORDS)]
        for document in (*DOCUMENTS, 'README.md', 'docs/codex.md', 'docs/setup.md', 'docs/design.md'):
            self.assertIn(document, documents)
        showing = [f'{document}:{number}: {line[:120]}' for document in documents
                   for number, line in enumerate((REPO / document).read_text(encoding='utf-8').splitlines(), 1)
                   if OLD_FORMS.search(line)]
        self.assertEqual(showing, [])
        # The pattern sees each form a document had, and no command of the tool.
        for line, seen in (('python -m generative_driver benchmark cases', True),
                           ('generative-driver benchmark run --case tq9-v2', True),
                           ('generative-driver --home /operator/state/suite benchmark suite status SUITE_ID', True),
                           ('generative-driver --home /operator/state/benchmark report RUN_ID', True),
                           ('`gd report <run>` on a sealed run', True),
                           ('generative-driver --home /operator/state/benchmark status RUN_ID', False),
                           ('python -m generative_driver smoke', False),
                           ('firmware-bench --home /operator/state/benchmark report RUN_ID', False)):
            with self.subTest(line=line):
                self.assertEqual(bool(OLD_FORMS.search(line)), seen)

    def test_09_B10_nothing_else_in_these_documents_changed(self):
        for document, (sha256, _, _, _) in DOCUMENTS.items():
            text = (REPO / document).read_text(encoding='utf-8')
            with self.subTest(document=document):
                self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), sha256)


def above(heading):
    """What the deletion list says under `heading` before its table, as one line, and the table's column names."""
    lines = table(heading).splitlines()
    first = next(number for number, line in enumerate(lines) if line.startswith('|'))
    return one_line(' '.join(lines[:first])), [cell.strip() for cell in lines[first].strip().strip('|').split('|')]


THIS_FILE, TOOLS_FILE = 'tests/test_benchmark_commands.py', 'tests/test_tool_commands.py'
RESOURCES = 'src/generative_driver/resources/bench/'


class ListTests(SmallTreeCase):
    """The deletion list is true after this Feature (09-B11)."""

    def test_09_B11_no_tool_module_is_listed_and_its_table_keeps_its_heading_and_columns(self):
        self.assertEqual(rows(MODULES), [])
        self.assertEqual(above(MODULES), ('No tool module imports the benchmark package.', ['Module', 'Freed by']))
        # It is true: no module of the tool imports the benchmark's code.
        self.assertEqual([path.name for path, package in tool_modules() if benchmark_imports(path, package)], [])

    def test_09_B11_no_command_is_listed_and_the_sentence_names_the_benchmarks_own_command(self):
        self.assertEqual(rows(COMMANDS), [])
        self.assertEqual(above(COMMANDS), ('The tool offers no benchmark command. The benchmark\'s own command is '
                                           '`firmware-bench`.', ['Command', 'Where', 'Freed by']))

    def test_09_B11_the_check_still_reports_a_benchmark_command_the_tool_offers(self):
        offering = tool_package(('doctor', 'benchmark'), ('driver_start', 'driver_benchmark_run'))
        found = sorted(problems(self.tree({**offering, 'docs/benchmark-deletion-list.md': deletion_list()})))
        self.assertEqual(len(found), 2, found)
        for sentence, command in zip(found, ('`driver_benchmark_run`', '`generative-driver benchmark`')):
            self.assertIn(command + ' is a benchmark command the tool offers', sentence)
            self.assertIn('does not name', sentence)
        # A tool that offers none, as this repository's, has no such problem.
        without = tool_package(('doctor',), ('driver_start',))
        self.assertEqual(problems(self.tree({**without, 'docs/benchmark-deletion-list.md': deletion_list()})), [])

    def test_09_B11_the_passage_rows_this_feature_freed_are_gone_and_the_other_two_of_the_readme_are_true(self):
        passages = rows(PASSAGES)
        self.assertEqual([row for row in passages if row[0] == '.github/workflows/test.yml'], [])
        self.assertEqual([row[1:] for row in passages if row[0] == 'README.md'],
                         [['3', 'Two rows of a table that also holds tool rows, and one mixed paragraph', '10'],
                          ['1', 'Links to the published record under `bench/evidence/`, which is kept', 'stays']])
        saying = [line for line in (REPO / 'README.md').read_text(encoding='utf-8').splitlines()
                  if 'bench' in line.lower()]
        self.assertEqual(len(saying), 3 + 1)
        self.assertEqual([len([line for line in saying if told(line)]) for told in (
            lambda line: line.startswith('|'), lambda line: 'bench/evidence/' in line)], [2, 1])

    def test_09_B11_the_benchmarks_command_sits_in_a_marked_passage_of_pyproject_which_has_no_row(self):
        lines = (REPO / 'pyproject.toml').read_text(encoding='utf-8').splitlines()
        [declared] = [number for number, line in enumerate(lines) if 'bench' in line.lower()
                      and 'legacy-benchmark' not in line]
        self.assertEqual(lines[declared - 1:declared + 2],
                         ['# legacy-benchmark:begin', 'firmware-bench = "generative_driver.benchmark:main"',
                          '# legacy-benchmark:end'])
        self.assertNotIn('pyproject.toml', LIST.read_text(encoding='utf-8'))

    def test_09_B11_the_stripped_copys_pyproject_declares_only_the_tools_two_commands(self):
        require_working_tree(self)
        copy, failures = stripped_repository()
        self.assertEqual(failures, [])
        project = tomllib.loads((copy / 'pyproject.toml').read_text(encoding='utf-8'))['project']
        self.assertEqual(project['scripts'], {'generative-driver': 'generative_driver.cli:main',
                                              'generative-driver-mcp': 'generative_driver.mcp:main'})
        self.assertNotIn('bench', (copy / 'pyproject.toml').read_text(encoding='utf-8').lower())
        # The removal leaves the tool's tests of this Feature and takes this file.
        self.assertTrue((copy / TOOLS_FILE).is_file())
        self.assertFalse((copy / THIS_FILE).exists())

    def test_09_B11_no_row_of_the_list_names_feature_09(self):
        freed = {heading: sorted({row[-1] for row in rows(heading)})
                 for heading in (STILL_USED, MODULES, COMMANDS, PASSAGES)}
        self.assertEqual(freed, {STILL_USED: ['10'], MODULES: [], COMMANDS: [], PASSAGES: ['10', 'stays']})
        text = LIST.read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'Feature \d\d', text), ['Feature 10'])
        self.assertEqual([line for line in text.splitlines() if re.search(r'\|\s*09\s*\|', line)], [])

    def test_09_B11_each_reason_under_still_used_is_true(self):
        used = {row[0]: row[1] for row in rows(STILL_USED)}
        # The benchmark's code is used by the still-used tests, and by no tool module or command.
        for item in ('src/generative_driver/benchmark.py', 'src/generative_driver/benchmark_support/'):
            with self.subTest(item=item):
                self.assertEqual(used[item], 'The still-used tests')
        tests = [item for item in used if item.startswith('tests/test_')]
        importing = [item for item in tests if 'generative_driver.benchmark' in
                     (REPO / item).read_text(encoding='utf-8')]
        self.assertGreater(len(importing), 40)
        # The smoke case's resources: each test file the reason names for the smoke case names that case, and the
        # hosted workflow, which ran it, is not named.
        reason = used[RESOURCES]
        self.assertEqual(reason, '`benchmark.py` and `benchmark_support/` read it; `tests/test_service.py` and '
                                 '`tests/test_toolkit.py` use its smoke case; `tests/test_distribution.py` requires '
                                 'it in the wheel')
        for file in re.findall(r'`(tests/test_\w+\.py)`', reason):
            with self.subTest(file=file):
                self.assertIn('bench/cases/setup-smoke', (REPO / file).read_text(encoding='utf-8'))
        self.assertNotIn('setup-smoke', (REPO / 'tests' / 'test_cli.py').read_text(encoding='utf-8'))
        self.assertNotIn('bench', (REPO / '.github' / 'workflows' / 'test.yml').read_text(encoding='utf-8').lower())

    def test_09_B11_the_row_of_the_mcp_tests_is_gone_and_they_are_a_tool_test(self):
        require_working_tree(self)
        self.assertEqual(covering('tests/test_mcp.py'), [])
        self.assertNotIn('tests/test_mcp.py', LIST.read_text(encoding='utf-8'))
        modules = tool_test_modules(REPO)
        for module in ('tests/test_mcp.py', TOOLS_FILE, 'tests/test_cli.py'):
            self.assertIn(module, modules)
        self.assertNotIn(THIS_FILE, modules)
        # It imports nothing of the benchmark and names none of the seven tools.
        source = REPO / 'tests' / 'test_mcp.py'
        self.assertEqual(benchmark_imports(source, ''), [])
        self.assertEqual(re.findall(r'driver_benchmark\w*', source.read_text(encoding='utf-8')), [])

    def test_09_B11_this_module_is_ready_to_delete_and_the_tools_module_is_not_on_the_list(self):
        self.assertEqual(covering(THIS_FILE), [(READY, THIS_FILE)])
        text = LIST.read_text(encoding='utf-8')
        self.assertEqual(text.count('`' + THIS_FILE + '`'), 1)
        self.assertTrue((REPO / TOOLS_FILE).is_file())
        self.assertNotIn('test_tool_commands', text)

    def test_09_B11_every_count_in_the_list_is_true_and_its_check_reports_no_problem(self):
        require_working_tree(self)
        self.assertEqual(problems(REPO), [])
        # The counts the check does not make: the test files that import each shared helper.
        importers = {}
        for path in sorted((REPO / 'tests').glob('*.py')):
            for node in ast.walk(ast.parse(path.read_bytes())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import) else
                         [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                for name in names:
                    importers.setdefault(name.split('.')[0], set()).add(path.name)
        counted = 0
        for item, used_by, _ in rows(STILL_USED):
            said = re.fullmatch(r'`tests/([a-z0-9_]+\.py)` and (\d+) other test files? imports? it', used_by)
            if said:
                counted += 1
                with self.subTest(item=item):
                    self.assertIn(said.group(1), importers[Path(item).stem])
                    self.assertEqual(int(said.group(2)), len(importers[Path(item).stem]) - 1)
        self.assertEqual(counted, 5)


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
