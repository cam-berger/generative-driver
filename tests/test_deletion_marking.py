"""The deletion list, the markers and the removal hold to the rules of the deletion marking.

Feature 01 of the untangle Run (docs/features/untangle/01-deletion-marking.md). Each rule is tested through
`problems`, `strip` and `tool_test_modules` on a small git repository built in a temporary directory. The repository
itself is tested for 01-B9 and 01-B10.
"""
import ast
import atexit
from concurrent.futures import ThreadPoolExecutor
import functools
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock

from deletion_marking import problems, strip, tool_test_modules

REPO = Path(__file__).resolve().parents[1]
# The slowest tool module takes about a minute here. On the hosted Windows machines three modules did not finish in
# 300 seconds with eight at a time (08-B17 of docs/features/untangle/08-reports.md). A module that hangs is reported
# instead of waited for.
MODULE_SECONDS = 900
LIST = 'docs/benchmark-deletion-list.md'
TABLES = (('Ready to delete', 'Item | Note'),
          ('Still used by the tool', 'Item | Used by | Freed by'),
          ('Tool modules that import the benchmark', 'Module | Freed by'),
          ('Commands', 'Command | Where | Freed by'),
          ('Passages', 'File | Unmarked lines | Why | Freed by'),
          ('Kept', 'Item | Why'))


def deletion_list(ready=(), still_used=(), modules=(), commands=(), passages=(), kept=(), lists_itself=True):
    """The text of a deletion list whose six tables hold the given rows, each row a tuple of cells. The list names
    itself as ready unless `lists_itself` is false."""
    ready = [*ready, *([(f'`{LIST}`', 'The list itself')] if lists_itself else [])]
    text = ['# Benchmark deletion list', '']
    for (heading, columns), rows in zip(TABLES, (ready, still_used, modules, commands, passages, kept)):
        text += [f'## {heading}', '', f'| {columns} |', '|' + '---|' * (columns.count('|') + 1)]
        text += ['| ' + ' | '.join(str(cell) for cell in row) + ' |' for row in rows]
        text.append('')
    return '\n'.join(text)


def git(root, *arguments):
    """Run git in `root` without the caller's own git configuration."""
    environment = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(['git', *arguments], cwd=root, env=environment, check=True, capture_output=True)


def removal_environment(copy):
    """The environment the removal gives a child that runs in the tree at `copy`: this process's own, but its
    `PYTHONPATH` holds only the `src` of `copy`, so the child cannot import from the tree the copy was made from
    (01-B7)."""
    return {**os.environ, 'PYTHONPATH': str(Path(copy) / 'src')}


def failed_modules(copy, modules):
    """A sentence for each of the test `modules` that fails in the tree at `copy`, with the end of its output. Each
    module runs in its own process. No more run at a time than the machine has processors, never more than eight,
    and one at a time where the number of processors is not known (08-B17 of
    docs/features/untangle/08-reports.md). A child gets the `removal_environment` of `copy`."""
    environment = removal_environment(copy)

    def end(output):
        return '\n'.join((output or b'').decode('utf-8', 'replace').splitlines()[-40:])

    def failure(module):
        command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', Path(module).name]
        try:
            done = subprocess.run(command, cwd=copy, env=environment, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=MODULE_SECONDS)
        except subprocess.TimeoutExpired as error:
            return f'{module} did not finish in {MODULE_SECONDS} seconds in the copy:\n{end(error.output)}'
        return None if done.returncode == 0 else f'{module} failed in the copy:\n{end(done.stdout)}'

    with ThreadPoolExecutor(max_workers=min(os.cpu_count() or 1, 8)) as pool:
        return [sentence for sentence in pool.map(failure, modules) if sentence]


def require_working_tree(test):
    """Skip `test` unless the repository is the root of a git working tree, which an unpacked source archive is
    not."""
    try:
        top = subprocess.run(['git', '-C', str(REPO), 'rev-parse', '--show-toplevel'], capture_output=True,
                             text=True, check=True).stdout.strip()
        is_root = os.path.samefile(top, REPO)
    except (OSError, subprocess.CalledProcessError):
        is_root = False
    if not is_root:
        test.skipTest(f'The tests of the repository read its files from git, and {REPO} is not a git working tree')


@unittest.skipUnless(shutil.which('git'), 'The deletion marking reads its files from git')
class SmallTreeCase(unittest.TestCase):
    def tree(self, files):
        """The root of a new git working tree that holds `files`, a mapping of path to text or bytes."""
        temporary = tempfile.TemporaryDirectory(prefix='deletion marking ', ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve() / 'tree'
        root.mkdir()
        git(root, 'init', '--quiet', '--template=')
        for path, content in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode('utf-8'))
        return root

    def assert_one_problem(self, found, *named):
        """`found` holds one sentence, and it names every string of `named`."""
        self.assertEqual(len(found), 1, found)
        for name in named:
            self.assertIn(name, found[0])


class DeletionListTests(SmallTreeCase):
    def test_01_B1_a_list_whose_entries_match_the_files_has_no_problem(self):
        root = self.tree({
            'docs/guide.md': 'A guide.\n', 'docs/old/record.md': 'A record.\n', 'src/tool.py': 'VALUE = 1\n',
            LIST: deletion_list(still_used=[('`docs/guide.md`', '`README.md`', '09')],
                                kept=[('`docs/old/`', 'A record of another Run')])})
        self.assertEqual(problems(root), [])

    def test_01_B1_names_a_missing_list_a_missing_table_and_wrong_columns(self):
        self.assert_one_problem(problems(self.tree({'docs/guide.md': 'A guide.\n'})), LIST)
        without_kept = deletion_list().split('## Kept')[0]
        self.assert_one_problem(problems(self.tree({LIST: without_kept})), LIST, 'Kept')
        renamed = deletion_list().replace('| Item | Note |', '| Item | Remark |')
        self.assert_one_problem(problems(self.tree({LIST: renamed})), LIST, 'Ready to delete', 'Item, Note')

    def test_01_B1_names_a_row_with_a_missing_cell(self):
        short = deletion_list(kept=[('`docs/guide.md`',)])
        found = problems(self.tree({'docs/guide.md': 'A guide.\n', LIST: short}))
        self.assert_one_problem(found, LIST, '`docs/guide.md`', 'Kept', '1 cells, not 2')

    def test_01_B1_names_an_entry_whose_path_matches_no_file(self):
        root = self.tree({
            'docs/guide.md': 'A guide.\n',
            LIST: deletion_list(ready=[('`docs/gone.md`', 'Nothing links to it')],
                                still_used=[('`src/gone/`', 'The service', '05')],
                                modules=[('`src/absent.py`', '07')],
                                passages=[('`docs/missing.md`', 0, 'Mixed lines', '09')],
                                kept=[('`docs/guide.md/`', 'A file, not a folder')])})
        found = problems(root)
        self.assertEqual(len(found), 5, found)
        for sentence, path in zip(sorted(found), ('docs/gone.md', 'docs/guide.md/', 'docs/missing.md',
                                                  'src/absent.py', 'src/gone/')):
            self.assertIn(f'`{path}`', sentence)
            self.assertIn('matches no file', sentence)

    def test_01_B1_names_two_entries_that_cover_the_same_file(self):
        root = self.tree({
            'docs/old/record.md': 'A record.\n', 'docs/old/notes.md': 'Notes.\n', 'docs/guide.md': 'A guide.\n',
            LIST: deletion_list(ready=[('`docs/old/`', 'Records')],
                                still_used=[('`docs/guide.md`', '`README.md`', '09')],
                                kept=[('`docs/old/record.md`', 'A record'), ('`docs/guide.md`', 'A guide')])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 2, found)
        for name in ('`docs/guide.md` under Still used by the tool', '`docs/guide.md` under Kept'):
            self.assertIn(name, found[0])
        for name in ('`docs/old/` under Ready to delete', '`docs/old/record.md` under Kept', 'docs/old/record.md'):
            self.assertIn(name, found[1])

    def test_01_B1_names_two_passage_rows_with_the_same_file_and_feature(self):
        root = self.tree({
            'docs/guide.md': 'A guide.\n',
            LIST: deletion_list(passages=[('`docs/guide.md`', 0, 'Command lines', '08'),
                                          ('`docs/guide.md`', 0, 'Mixed lines', '09'),
                                          ('`docs/guide.md`', 0, 'More mixed lines', '09')])})
        self.assert_one_problem(problems(root), '`docs/guide.md`', 'Passages', '09')

    def test_01_B1_names_an_entry_whose_freed_by_is_missing_or_names_no_later_feature(self):
        root = self.tree({
            'docs/a.md': 'A.\n', 'docs/b.md': 'B.\n', 'docs/c.md': 'C.\n', 'docs/d.md': 'D.\n',
            'src/tool.py': 'import generative_driver.benchmark\n', 'src/other.py': 'import firmware_bench\n',
            LIST: deletion_list(still_used=[('`docs/a.md`', '`README.md`', ''), ('`docs/b.md`', '`README.md`', '01'),
                                            ('`docs/c.md`', '`README.md`', 'stays'),
                                            ('`docs/d.md`', '`README.md`', '10')],
                                modules=[('`src/tool.py`', '13'), ('`src/other.py`', '02')],
                                passages=[('`src/tool.py`', 0, 'Not about the benchmark', 'stays'),
                                          ('`docs/d.md`', 0, 'Mixed lines', '9')])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 5, found)
        for sentence, entry in zip(found, ('`docs/a.md` under Still used by the tool',
                                           '`docs/b.md` under Still used by the tool',
                                           '`docs/c.md` under Still used by the tool',
                                           '`docs/d.md` under Passages',
                                           '`src/tool.py` under Tool modules that import the benchmark')):
            self.assertIn(entry, sentence)
            self.assertIn('Freed by', sentence)

    def test_04_B7_takes_02_to_12_as_a_freed_by_and_reports_13_as_no_later_feature(self):
        numbers = [f'{number:02d}' for number in range(1, 14)]
        root = self.tree({
            **{f'docs/{number}.md': 'Text.\n' for number in numbers},
            LIST: deletion_list(still_used=[(f'`docs/{number}.md`', '`README.md`', number) for number in numbers])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 2, found)
        for sentence, number in zip(found, ('01', '13')):
            self.assertIn(f'`docs/{number}.md` under Still used by the tool', sentence)
            self.assertIn('02, 03, 04, 05, 06, 07, 08, 09, 10, 11, 12: ' + number, sentence)

    def test_04_B8_a_ready_row_inside_a_still_used_folder_is_no_problem_and_the_removal_takes_it_out(self):
        root = self.tree({
            'src/support/old.py': 'VALUE = 1\n', 'src/support/live.py': 'VALUE = 2\n',
            'src/support/cases/old/case.json': '{}\n', 'src/support/cases/old/image.bin': b'\x00\xff',
            'src/support/cases/new/case.json': '{}\n',
            LIST: deletion_list(ready=[('`src/support/old.py`', 'A dropped case'),
                                       ('`src/support/cases/old/`', 'A dropped case')],
                                still_used=[('`src/support/`', 'The tool', '10')])})
        self.assertEqual(problems(root), [])
        destination = root.parent / 'copy'
        self.assertEqual(strip(root, destination), [])
        self.assertEqual(snapshot(destination), {'src/support/live.py': b'VALUE = 2\n',
                                                 'src/support/cases/new/case.json': b'{}\n'})

    def test_04_B8_every_other_pair_of_rows_that_cover_one_file_is_still_reported(self):
        files = {'src/support/old.py': 'VALUE = 1\n', 'src/support/deep/old.py': 'VALUE = 2\n'}
        ready, still_used, kept = 'Ready to delete', 'Still used by the tool', 'Kept'
        folder, deep, file = '`src/support/`', '`src/support/deep/`', '`src/support/deep/old.py`'
        pairs = {
            'two ready rows': ({ready: [folder, file]}, (f'{folder} under {ready}', f'{file} under {ready}')),
            'two still-used rows': ({still_used: [folder, file]},
                                    (f'{folder} under {still_used}', f'{file} under {still_used}')),
            'two kept rows': ({kept: [folder, file]}, (f'{folder} under {kept}', f'{file} under {kept}')),
            'a kept row inside a still-used folder': ({still_used: [folder], kept: [file]},
                                                      (f'{folder} under {still_used}', f'{file} under {kept}')),
            'a ready row inside a kept folder': ({ready: [file], kept: [folder]},
                                                 (f'{file} under {ready}', f'{folder} under {kept}')),
            'a kept row inside a ready folder': ({ready: [folder], kept: [file]},
                                                 (f'{folder} under {ready}', f'{file} under {kept}')),
            'a still-used row inside a ready folder': ({ready: [folder], still_used: [file]},
                                                       (f'{folder} under {ready}', f'{file} under {still_used}')),
            'one item under ready and still used': ({ready: [deep], still_used: [deep]},
                                                    (f'{deep} under {ready}', f'{deep} under {still_used}')),
            'two still-used folders around one ready row': ({ready: [file], still_used: [folder, deep]},
                                                            (f'{folder} under {still_used}',
                                                             f'{deep} under {still_used}')),
        }
        for name, (rows, named) in pairs.items():
            with self.subTest(name):
                text = deletion_list(ready=[(item, 'A note') for item in rows.get(ready, ())],
                                     still_used=[(item, 'The tool', '10') for item in rows.get(still_used, ())],
                                     kept=[(item, 'A record') for item in rows.get(kept, ())])
                self.assert_one_problem(problems(self.tree({**files, LIST: text})), *named, 'both cover')

    def test_01_B2_names_each_file_with_a_benchmark_name_that_no_entry_covers(self):
        named = ('bench/README.md', 'src/tool/resources/registrations/cases.json', 'docs/bench-notes.md',
                 'tests/bench_fixture.txt', 'docs/benchmark-corpus.md', 'tests/test_benchmark_run.txt',
                 'src/firmware_bench/cases.json', 'docs/firmware-bench.md', 'src/tool/benchmark_support/notes.txt')
        plain = ('src/tool/_toolchain/bench.txt', 'docs/workbench.md', 'docs/registration.md', 'docs/abench-x.md')
        root = self.tree({**{path: 'Text.\n' for path in (*named, *plain)}, LIST: deletion_list()})
        found = sorted(problems(root))
        self.assertEqual(len(found), len(named), found)
        for sentence, path in zip(found, sorted(named)):
            self.assertIn(path, sentence)
            self.assertIn('benchmark name', sentence)

    def test_01_B2_a_file_with_a_benchmark_name_is_covered_by_any_of_the_three_tables(self):
        files = {path: 'Text.\n' for path in ('bench/README.md', 'bench/evidence/result.json',
                                              'src/firmware_bench/cases.json', 'docs/benchmark-corpus.md')}
        covered = deletion_list(ready=[('`docs/benchmark-corpus.md`', 'Nothing links to it')],
                                still_used=[('`bench/README.md`', '`README.md`', '09'),
                                            ('`src/firmware_bench/`', 'The tool', '10')],
                                kept=[('`bench/evidence/`', 'The published record')])
        self.assertEqual(problems(self.tree({**files, LIST: covered})), [])
        uncovered = deletion_list(passages=[('`bench/README.md`', 0, 'Mixed lines', '09')],
                                  kept=[('`bench/evidence/`', 'The published record')])
        found = sorted(problems(self.tree({**files, LIST: uncovered})))
        self.assertEqual(len(found), 3, found)
        for sentence, path in zip(found, ('bench/README.md', 'docs/benchmark-corpus.md',
                                          'src/firmware_bench/cases.json')):
            self.assertIn(path, sentence)


TEST_FILES = {
    'tests/fixtures.py': 'def device():\n    import generative_driver.benchmark\n',
    'tests/test_direct.py': 'import generative_driver.benchmark\n',
    'tests/test_from.py': 'from generative_driver.benchmark_support.registry import resolve_case\n',
    'tests/test_member.py': 'import json\nfrom generative_driver import benchmark_support\n',
    'tests/test_nested.py': 'class Case:\n    def run(self):\n        from generative_driver.benchmark import run\n',
    'tests/test_renamed.py': 'import firmware_bench.cases\n',
    'tests/test_through.py': 'from fixtures import device\n',
    'tests/test_twice.py': 'import test_through\n',
    'tests/test_tool.py': 'import generative_driver.benchmarks\nfrom generative_driver import reporting\n'
                          'from line_device import serve\n',
    'tests/line_device.py': 'import socket\n'}
BENCHMARK_TESTS = sorted(set(TEST_FILES) - {'tests/test_tool.py', 'tests/line_device.py'})
SOURCE_FILES = {
    'src/generative_driver/__init__.py': '',
    'src/generative_driver/benchmark.py': 'from .benchmark_support import labels\n',
    'src/generative_driver/benchmark_support/__init__.py': '',
    'src/generative_driver/benchmark_support/labels.py': 'from . import integrity\n',
    'src/generative_driver/service.py': 'from .benchmark_support.suites import start_suite\n',
    'src/generative_driver/cli.py': 'def main():\n    from .benchmark import main\n',
    'src/generative_driver/stages/emit.py': 'from ..benchmark_support import labels\n',
    'src/generative_driver/setup.py': 'from .service import start\nfrom . import benchmarks\n',
    'scripts/report.py': 'import generative_driver.benchmark\n'}
BENCHMARK_SOURCE = [('`src/generative_driver/benchmark.py`', 'The tool', '08'),
                    ('`src/generative_driver/benchmark_support/`', 'The tool', '09')]
IMPORTING_MODULES = ('scripts/report.py', 'src/generative_driver/cli.py', 'src/generative_driver/service.py',
                     'src/generative_driver/stages/emit.py')


class ImportTests(SmallTreeCase):
    def test_01_B3_names_each_test_file_that_imports_the_benchmark_and_is_not_covered(self):
        found = sorted(problems(self.tree({**TEST_FILES, LIST: deletion_list()})))
        self.assertEqual(len(found), len(BENCHMARK_TESTS), found)
        for sentence, path in zip(found, BENCHMARK_TESTS):
            self.assertIn(path, sentence)
            self.assertIn('imports the benchmark package', sentence)

    def test_01_B3_a_benchmark_test_file_is_covered_by_ready_or_still_used_and_not_by_kept(self):
        ready, still_used, kept = BENCHMARK_TESTS[:3], BENCHMARK_TESTS[3:6], BENCHMARK_TESTS[6:]
        listed = deletion_list(ready=[(f'`{path}`', 'Benchmark only') for path in ready],
                               still_used=[(f'`{path}`', 'Its tool tests', '09') for path in still_used],
                               kept=[(f'`{path}`', 'A record') for path in kept])
        found = sorted(problems(self.tree({**TEST_FILES, LIST: listed})))
        self.assertEqual(len(found), len(kept), found)
        for sentence, path in zip(found, kept):
            self.assertIn(path, sentence)

    def test_01_B3_names_each_module_outside_tests_that_imports_the_benchmark_and_is_not_listed(self):
        listed = deletion_list(still_used=BENCHMARK_SOURCE)
        found = sorted(problems(self.tree({**SOURCE_FILES, LIST: listed})))
        self.assertEqual(len(found), len(IMPORTING_MODULES), found)
        for sentence, path in zip(found, IMPORTING_MODULES):
            self.assertIn(path, sentence)
            self.assertIn('Tool modules that import the benchmark', sentence)

    def test_01_B3_names_a_listed_module_that_no_longer_imports_the_benchmark(self):
        modules = [(f'`{path}`', '07') for path in (*IMPORTING_MODULES, 'src/generative_driver/setup.py')]
        listed = deletion_list(still_used=BENCHMARK_SOURCE, modules=modules)
        found = problems(self.tree({**SOURCE_FILES, LIST: listed}))
        self.assert_one_problem(found, '`src/generative_driver/setup.py` under Tool modules that import the benchmark',
                                'no longer imports')

    def test_01_B3_names_a_python_file_whose_imports_cannot_be_read(self):
        found = problems(self.tree({'src/broken.py': 'import generative_driver.benchmark\ndef (:\n',
                                    LIST: deletion_list()}))
        self.assert_one_problem(found, 'src/broken.py', 'imports')


BEGIN, END = '<!-- legacy-benchmark:begin -->', '<!-- legacy-benchmark:end -->'
HASH_BEGIN, HASH_END = '# legacy-benchmark:begin', '# legacy-benchmark:end'


def lines(*text):
    return '\n'.join(text) + '\n'


class MarkerTests(SmallTreeCase):
    def test_01_B4_whole_passages_in_each_comment_form_have_no_problem(self):
        root = self.tree({
            'docs/guide.md': lines('# Guide', '', BEGIN, 'Score a run.', END, '', 'Start a run.', '', BEGIN,
                                   '- one', '- two', END),
            '.github/workflows/test.yml': lines('steps:', '  ' + HASH_BEGIN, '  - run: score', '  ' + HASH_END,
                                                '  - run: test'),
            'pyproject.toml': lines('[project]', 'name = "tool"', HASH_BEGIN, 'scores = true', HASH_END),
            LIST: deletion_list()})
        self.assertEqual(problems(root), [])

    def test_01_B4_names_the_line_of_a_begin_with_no_end(self):
        root = self.tree({'docs/guide.md': lines('# Guide', '', BEGIN, 'Score a run.', END, '', BEGIN, 'More.'),
                          LIST: deletion_list()})
        self.assert_one_problem(problems(root), 'docs/guide.md:7', 'no end')

    def test_01_B4_names_the_line_of_an_end_with_no_begin(self):
        root = self.tree({'MANIFEST.in': lines('include NOTICE', HASH_END, 'include README.md'),
                          LIST: deletion_list()})
        self.assert_one_problem(problems(root), 'MANIFEST.in:2', 'no begin')

    def test_01_B4_names_the_line_of_a_passage_that_starts_inside_another(self):
        root = self.tree({'docs/guide.md': lines('# Guide', BEGIN, 'Score a run.', BEGIN, 'Compare runs.', END),
                          LIST: deletion_list()})
        self.assert_one_problem(problems(root), 'docs/guide.md:4', 'inside the passage that begins at line 2')

    def test_01_B4_names_each_marker_in_a_file_that_an_entry_covers(self):
        root = self.tree({
            'docs/ready.md': lines('# Ready', BEGIN, 'Text.', END),
            'docs/used.md': lines('# Used', '', BEGIN),
            'records/kept.yml': lines('kept: true', HASH_BEGIN, 'more: true', HASH_END),
            LIST: deletion_list(ready=[('`docs/ready.md`', 'Nothing links to it')],
                                still_used=[('`docs/used.md`', '`README.md`', '09')],
                                kept=[('`records/`', 'Records of another Run')])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 5, found)
        expected = (('docs/ready.md:2', '`docs/ready.md` under Ready to delete'),
                    ('docs/ready.md:4', '`docs/ready.md` under Ready to delete'),
                    ('docs/used.md:3', '`docs/used.md` under Still used by the tool'),
                    ('records/kept.yml:2', '`records/` under Kept'), ('records/kept.yml:4', '`records/` under Kept'))
        for sentence, (line, entry) in zip(found, expected):
            self.assertIn(line, sentence)
            self.assertIn(entry, sentence)

    def test_01_B4_a_marker_is_a_whole_line_in_the_comment_form_of_its_file(self):
        # None of these is a marker, so none begins a passage that would need an end.
        root = self.tree({
            'docs/guide.md': lines('# Guide', HASH_BEGIN, 'Write ' + BEGIN + ' before the passage.', BEGIN + ' here'),
            'setup.cfg': lines('[metadata]', BEGIN, 'name = tool  ' + HASH_BEGIN),
            LIST: deletion_list(passages=[('`docs/guide.md`', 3, 'Explains the marker', 'stays'),
                                          ('`setup.cfg`', 2, 'Explains the marker', 'stays')])})
        self.assertEqual(problems(root), [])

    def test_01_B5_names_each_file_whose_unmarked_lines_differ_from_its_passage_rows(self):
        root = self.tree({
            'README.md': lines('Run the Benchmark.', 'Ask the BENCH harness.', 'Start a run.', BEGIN,
                               'Score a benchmark run.', END, 'Tidy the workbench.'),
            'docs/guide.md': lines('# Guide', 'See the benchmark.'),
            'NOTICE': lines('Copyright the contributors.'),
            'docs/setup.md': lines('Install it.', 'Then run `tool benchmark`.', 'The bench is a desk.'),
            LIST: deletion_list(passages=[('`README.md`', 1, 'Command lines', '08'),
                                          ('`README.md`', 1, 'Mixed lines', '09'),
                                          ('`NOTICE`', 2, 'Names the evidence', '09'),
                                          ('`docs/setup.md`', 1, 'Command lines', '08'),
                                          ('`docs/setup.md`', 1, 'The hardware bench', 'stays')])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 3, found)
        for sentence, (path, counted, listed) in zip(found, (('NOTICE', '0 ', ' 2'), ('README.md', '3 ', ' 2'),
                                                             ('docs/guide.md', '1 ', ' 0'))):
            self.assertTrue(sentence.startswith(path), sentence)
            self.assertIn(counted, sentence)
            self.assertIn(listed, sentence)

    def test_01_B5_counts_no_line_of_python_source_a_covered_file_a_marked_passage_or_a_file_that_is_not_text(self):
        files = {'src/tool.py': '# The bench harness starts here.\n',
                 'records/plan.md': lines('# Plan', 'Build the benchmark.'),
                 'data/image.bin': b'\xff\xfe bench \n',
                 'docs/guide.md': lines('# Guide', BEGIN, 'The benchmark.', 'Its bench harness.', END)}
        kept = [('`records/`', 'Records of another Run')]
        self.assertEqual(problems(self.tree({**files, LIST: deletion_list(kept=kept)})), [])
        rows = [(f'`{path}`', 1, 'Mixed lines', '09') for path in sorted(files)]
        found = sorted(problems(self.tree({**files, LIST: deletion_list(kept=kept, passages=rows)})))
        self.assertEqual(len(found), 4, found)
        for sentence, path in zip(found, sorted(files)):
            self.assertTrue(sentence.startswith(path), sentence)
            self.assertIn('0 ', sentence)

    def test_01_B5_names_a_passage_row_whose_figure_is_not_a_whole_number(self):
        root = self.tree({'README.md': lines('Run the benchmark.'),
                          LIST: deletion_list(passages=[('`README.md`', 'one', 'Mixed lines', '09')])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 2, found)
        self.assertIn('1 ', found[0])
        self.assertIn('`README.md` under Passages', found[1])
        self.assertIn('one', found[1])


def snapshot(root):
    """The bytes of every file below `root`, keyed by its path from there, without git's own folder."""
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(Path(root).rglob('*'))
            if path.is_file() and '.git' not in path.relative_to(root).parts}


class RemovalTests(SmallTreeCase):
    def test_01_B6_writes_a_copy_without_the_ready_items_and_the_marked_passages(self):
        stay = {'.gitignore': b'outputs/\n', 'docs/guide.md': b'A guide.\n',
                'data/image.bin': b'\x00\xff\xfe# legacy-benchmark:begin\n\x01',
                'records/kept.md': lines('# Kept', 'The benchmark record.').encode(),
                'bench/README.md': lines('# Benchmark', '', 'Score a run.').encode()}
        files = {**stay, 'outputs/run.json': '{}\n',
                 'README.md': lines('# Tool', '', BEGIN, 'Score a benchmark run.', END, '', 'Start a run.'),
                 'ci.yml': lines('steps:', '  ' + HASH_BEGIN, '  - run: score', '  ' + HASH_END, '  - run: test'),
                 'windows.md': f'First.\r\n{BEGIN}\r\nScore.\r\n{END}\r\nLast.\r\n'.encode(),
                 'docs/old/record.md': 'A record.\n', 'docs/old/more/notes.md': 'Notes.\n',
                 'tests/test_gone.py': 'import unittest\n',
                 LIST: deletion_list(ready=[('`docs/old/`', 'Records'), ('`tests/test_gone.py`', 'Benchmark only')],
                                     still_used=[('`bench/README.md`', '`README.md`', '09')],
                                     kept=[('`records/`', 'Records of another Run')])}
        root = self.tree(files)
        git(root, 'add', 'README.md', 'docs/old/record.md')
        self.assertEqual(problems(root), [])
        before, destination = snapshot(root), root.parent / 'copy'
        self.assertEqual(strip(root, destination), [])
        self.assertEqual(snapshot(destination), {
            **stay, 'README.md': lines('# Tool', '', '', 'Start a run.').encode(),
            'ci.yml': lines('steps:', '  - run: test').encode(), 'windows.md': b'First.\r\nLast.\r\n'})
        self.assertEqual(snapshot(root), before)

    def test_01_B6_refuses_a_destination_inside_the_tree_or_one_that_holds_files(self):
        root = self.tree({'docs/guide.md': 'A guide.\n', LIST: deletion_list()})
        before = snapshot(root)
        for destination in (root, root / 'copy', root / 'docs' / 'deep' / 'copy'):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, 'inside the tree'):
                strip(root, destination)
        self.assertEqual(snapshot(root), before)
        self.assertFalse((root / 'copy').exists())
        used = root.parent / 'used'
        used.mkdir()
        (used / 'earlier.txt').write_bytes(b'From an earlier copy.\n')
        with self.assertRaisesRegex(ValueError, 'not an empty directory'):
            strip(root, used)
        self.assertEqual(snapshot(used), {'earlier.txt': b'From an earlier copy.\n'})

    def test_01_B6_names_each_relative_link_to_a_file_the_copy_lacks(self):
        root = self.tree({
            'README.md': lines(
                '# Tool', 'Read the [guide](docs/guide.md), its [run section](docs/guide.md#run) and [docs](docs/).',
                'See the [old record](docs/old/record.md) and ![its diagram](docs/old/diagram.png "A diagram").',
                'Visit the [site](https://example.org/docs/old/record.md), [install](#install) and',
                '[write](mailto:owner@example.org).', '', BEGIN, 'The [scores](docs/old/record.md).', END, '',
                'Write `[label](docs/old/record.md)` for a link.', '', '```md', '[label](docs/old/record.md)', '```',
                '', '[reference]: docs/old/notes.md'),
            'docs/guide.md': lines('# Guide', '## Run', 'Back to the [top](../README.md).'),
            'docs/deep/page.md': lines('The [guide](../guide.md) and a [lost page](../lost.md).'),
            'docs/used.md': lines('The [old record](old/record.md).'),
            'docs/old/record.md': 'A record.\n', 'docs/old/notes.md': 'Notes.\n', 'docs/old/diagram.png': b'\x89PNG',
            LIST: deletion_list(ready=[('`docs/old/`', 'Records')],
                                still_used=[('`docs/used.md`', '`README.md`', '09')])})
        found = sorted(strip(root, root.parent / 'copy'))
        self.assertEqual(len(found), 4, found)
        for sentence, (path, link) in zip(found, (('README.md', 'docs/old/diagram.png'),
                                                  ('README.md', 'docs/old/notes.md'),
                                                  ('README.md', 'docs/old/record.md'),
                                                  ('docs/deep/page.md', '../lost.md'))):
            self.assertTrue(sentence.startswith(path), sentence)
            self.assertIn(link, sentence)

    def test_01_B6_names_the_parse_error_of_pyproject_toml_in_the_copy(self):
        whole = lines('[project]', 'name = "tool"', HASH_BEGIN, 'keywords = ["scores"]', HASH_END)
        root = self.tree({'pyproject.toml': whole, LIST: deletion_list()})
        self.assertEqual(strip(root, root.parent / 'copy'), [])
        torn = lines('[project]', 'name = "tool"', 'keywords = [', HASH_BEGIN, '    "scores",', ']', HASH_END)
        root = self.tree({'pyproject.toml': torn, LIST: deletion_list()})
        found = strip(root, root.parent / 'copy')
        self.assert_one_problem(found, 'pyproject.toml')
        # The wording of the error is the TOML reader's own; the sentence only has to carry it.
        self.assertRegex(found[0], r'does not parse in the copy: \w+')

    def test_01_B6_a_list_that_cannot_be_read_is_a_failure_of_the_removal(self):
        root = self.tree({'docs/guide.md': 'A guide.\n'})
        self.assert_one_problem(strip(root, root.parent / 'copy'), LIST)


def test_module(*body):
    """The source of a test module with one test, whose body is the lines of `body`."""
    return lines('import os', 'from pathlib import Path', 'import unittest', '', '',
                 'class Tests(unittest.TestCase):', '    def test_it(self):', *('        ' + line for line in body))


class ToolTestTests(SmallTreeCase):
    def test_01_B7_the_tool_test_modules_are_those_the_removal_leaves_that_are_not_benchmark_items(self):
        files = {**TEST_FILES, 'tests/test_ready.py': 'import json\n', 'tests/test_benchmark_named.py': 'import json\n',
                 'tests/deeper/test_below.py': 'import json\n', 'tests/test_notes.txt': 'Notes.\n'}
        listed = deletion_list(
            ready=[('`tests/test_ready.py`', 'Tests the marking'), ('`tests/test_direct.py`', 'Benchmark only')],
            still_used=[(f'`{path}`', 'Its tool tests', '09')
                        for path in (*BENCHMARK_TESTS[:1], *BENCHMARK_TESTS[2:], 'tests/test_benchmark_named.py')])
        root = self.tree({**files, LIST: listed})
        self.assertEqual(problems(root), [])
        self.assertEqual(tool_test_modules(root), ['tests/test_benchmark_named.py', 'tests/test_tool.py'])

    def test_01_B7_names_a_tool_test_that_needs_a_ready_item_with_the_end_of_its_output(self):
        root = self.tree({
            'src/tool/__init__.py': '', 'src/tool/cases.json': '{}\n',
            'tests/test_reads.py': test_module('import tool',
                                               "self.assertTrue((Path(tool.__file__).parent / 'cases.json').is_file(),"
                                               " 'the cases are gone')"),
            'tests/test_passes.py': test_module('import tool', 'self.assertTrue(tool.__file__)'),
            LIST: deletion_list(ready=[('`src/tool/cases.json`', 'Benchmark data')])})
        modules = tool_test_modules(root)
        self.assertEqual(modules, ['tests/test_passes.py', 'tests/test_reads.py'])
        self.assertEqual(failed_modules(root, modules), [])
        copy = root.parent / 'copy'
        self.assertEqual(strip(root, copy), [])
        found = failed_modules(copy, modules)
        self.assert_one_problem(found, 'tests/test_reads.py', 'the cases are gone', 'FAILED (failures=1)')

    def test_01_B7_a_child_imports_from_the_src_of_the_copy_alone(self):
        root = self.tree({
            'src/tool/__init__.py': '', 'src/scores/__init__.py': '',
            'tests/test_where.py': test_module(
                'import tool', 'self.assertEqual(Path(tool.__file__).resolve().parents[2], Path.cwd().resolve())'),
            'tests/test_scores.py': 'import scores\n' + test_module('pass'),
            LIST: deletion_list(ready=[('`src/scores/`', 'Benchmark code')])})
        copy = root.parent / 'copy'
        self.assertEqual(strip(root, copy), [])
        # Even when this process imports from the tree the copy was made from, a child does not.
        with unittest.mock.patch.dict(os.environ, {'PYTHONPATH': str(root / 'src')}):
            found = failed_modules(copy, tool_test_modules(root))
        self.assert_one_problem(found, 'tests/test_scores.py', "No module named 'scores'")


def tool_package(commands, tools):
    """The files of a small `generative_driver` whose command line has the subcommands `commands` and whose MCP
    server offers `tools`."""
    server = ['from mcp.server.mcpserver import MCPServer', '', "server = MCPServer('generative-driver')"]
    for tool in tools:
        server += ['', '', '@server.tool()', f'def {tool}() -> str:', f'    """Answer as {tool}."""', "    return ''"]
    return {'src/generative_driver/__init__.py': '',
            'src/generative_driver/__main__.py': lines(
                'import argparse', '', "parser = argparse.ArgumentParser(prog='generative-driver')",
                "commands = parser.add_subparsers(dest='command', required=True)",
                f'for name in {tuple(commands)!r}:', '    commands.add_parser(name)', 'parser.parse_args()'),
            'src/generative_driver/mcp.py': lines(*server, '', '', "if __name__ == '__main__':",
                                                  "    server.run(transport='stdio')")}


def command_rows(*commands):
    return [(f'`{command}`', '`src/generative_driver/`', '08') for command in commands]


class CommandTests(SmallTreeCase):
    def test_01_B8_names_a_benchmark_tool_the_list_leaves_out_and_one_the_server_does_not_offer(self):
        package = tool_package(('doctor', 'benchmark'), ('driver_start', 'driver_benchmark_run',
                                                         'driver_benchmark_suite_start'))
        whole = command_rows('generative-driver benchmark', 'driver_benchmark_run', 'driver_benchmark_suite_start')
        self.assertEqual(problems(self.tree({**package, LIST: deletion_list(commands=whole)})), [])
        stale = command_rows('generative-driver benchmark', 'driver_benchmark_run', 'driver_benchmark_suite_stop')
        found = sorted(problems(self.tree({**package, LIST: deletion_list(commands=stale)})))
        self.assertEqual(len(found), 2, found)
        self.assertIn('`driver_benchmark_suite_start`', found[0])
        self.assertIn('does not name', found[0])
        self.assertIn('`driver_benchmark_suite_stop` under Commands', found[1])
        self.assertIn('does not offer', found[1])

    def test_01_B8_names_a_list_and_a_command_line_that_disagree_on_generative_driver_benchmark(self):
        offered = {**tool_package(('doctor', 'benchmark'), ('driver_start',)), LIST: deletion_list()}
        self.assert_one_problem(problems(self.tree(offered)), '`generative-driver benchmark`', 'does not name')
        moved = {**tool_package(('doctor',), ('driver_start',)),
                 LIST: deletion_list(commands=command_rows('generative-driver benchmark'))}
        self.assert_one_problem(problems(self.tree(moved)), '`generative-driver benchmark` under Commands',
                                'does not offer')

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

    def test_01_B8_a_tree_without_the_tool_offers_no_command(self):
        rows = [*command_rows('generative-driver benchmark'), ('`driver_benchmark_run`', '`src/`', 'later')]
        found = sorted(problems(self.tree({LIST: deletion_list(commands=rows)})))
        self.assertEqual(len(found), 3, found)
        for sentence, (command, fault) in zip(found, (('driver_benchmark_run', 'Freed by'),
                                                      ('driver_benchmark_run', 'does not offer'),
                                                      ('generative-driver benchmark', 'does not offer'))):
            self.assertIn(f'`{command}` under Commands', sentence)
            self.assertIn(fault, sentence)


NOTICE = ('**This is the legacy copy of the benchmark.** The benchmark is moving to a repository of its own. This copy '
          'will be deleted once the move is complete. The [deletion list]({link}) says what goes.')


def table(heading):
    """The text of the deletion list of the repository from `heading` to the next heading."""
    text = (REPO / LIST).read_text(encoding='utf-8') if (REPO / LIST).is_file() else ''
    return text.partition(f'\n## {heading}\n')[2].partition('\n## ')[0]


@functools.cache
def stripped_repository():
    """The stripped copy of the repository and the failures of its removal, made once for the tests that read it,
    whichever test module asks for it first. The copy stays until the process ends and is removed then, with errors
    ignored: the hosted tests run every test module in one process, so no one module may take the copy away
    (08-B17 of docs/features/untangle/08-reports.md)."""
    folder = tempfile.mkdtemp(prefix='stripped copy ')
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    copy = Path(folder).resolve() / 'copy'
    return copy, strip(REPO, copy)


class RepositoryTests(unittest.TestCase):
    def test_01_B7_is_skipped_without_the_variable_and_says_why(self):
        result = unittest.TestResult()
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop('GD_TEST_REMOVAL', None)
            RepositoryTests('test_01_B7_the_tool_tests_pass_in_the_stripped_copy').run(result)
        self.assertEqual((result.testsRun, result.errors, result.failures), (1, [], []))
        (_, reason), = result.skipped
        self.assertIn('GD_TEST_REMOVAL=1', reason)

    def test_01_B7_the_hosted_test_step_sets_the_variable(self):
        require_working_tree(self)
        workflow = REPO / '.github' / 'workflows' / 'test.yml'
        steps = [step for step in workflow.read_text(encoding='utf-8').split('\n      - ')
                 if "'-m', 'unittest', 'discover'" in step]
        self.assertEqual(len(steps), 1, 'One step of the hosted workflow runs the tests')
        setting = re.search(r"^\s*env\['GD_TEST_REMOVAL'\] = '1'$", steps[0], re.MULTILINE)
        self.assertIsNotNone(setting, 'The test step sets GD_TEST_REMOVAL=1 in the environment of its tests')
        runs = [found.start() for found in re.finditer(re.escape("'-m', 'unittest', 'discover'"), steps[0])]
        self.assertEqual(len(runs), 2, 'The step runs the tests of the two folders')
        for run in runs:
            self.assertLess(setting.start(), run)

    def test_01_B7_the_tool_tests_pass_in_the_stripped_copy(self):
        if os.environ.get('GD_TEST_REMOVAL') != '1':
            self.skipTest('Set GD_TEST_REMOVAL=1 to run the tool tests in the stripped copy; it takes minutes')
        require_working_tree(self)
        with tempfile.TemporaryDirectory(prefix='stripped copy ', ignore_cleanup_errors=True) as temporary:
            copy = Path(temporary).resolve() / 'copy'
            self.assertEqual(strip(REPO, copy), [])
            modules = tool_test_modules(REPO)
            self.assertTrue(modules, 'The removal leaves no tool test to run')
            failed = failed_modules(copy, modules)
            self.assertEqual(len(failed), 0, '\n\n'.join(failed))

    def test_01_B9_the_check_reports_no_problem_on_the_repository(self):
        require_working_tree(self)
        found = problems(REPO)
        self.assertEqual(len(found), 0, '\n'.join(found))

    def test_01_B9_the_removal_passes_on_the_repository(self):
        require_working_tree(self)
        _, failures = stripped_repository()
        self.assertEqual(len(failures), 0, '\n'.join(failures))

    def test_01_B9_the_marking_is_ready_and_the_published_record_and_this_run_are_kept(self):
        require_working_tree(self)
        copy, _ = stripped_repository()
        for path in (LIST, 'tests/deletion_marking.py', 'tests/test_deletion_marking.py'):
            self.assertIn(f'| `{path}` |', table('Ready to delete'))
            self.assertFalse((copy / path).exists(), path)
        for path in ('docs/verification.md', 'bench/evidence/', 'docs/features/untangle/'):
            self.assertIn(f'| `{path}` |', table('Kept'))
        for path in ('docs/verification.md', 'bench/evidence/benchmark-final-verification.json',
                     'docs/features/untangle/breakdown.md'):
            self.assertEqual((copy / path).read_bytes(), (REPO / path).read_bytes(), path)

    def test_01_B9_every_test_file_the_removal_leaves_imports_only_test_files_it_leaves(self):
        require_working_tree(self)
        copy, _ = stripped_repository()
        left = sorted((copy / 'tests').glob('*.py'))
        self.assertTrue(left, 'The removal leaves no test file')
        lost = []
        for path in left:
            for node in ast.walk(ast.parse(path.read_bytes())):
                names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                lost += [f'{path.name} imports {name}' for name in names
                         if (REPO / 'tests' / f'{name}.py').is_file() and not (copy / 'tests' / f'{name}.py').is_file()]
        self.assertEqual(lost, [])

    def test_01_B10_the_benchmark_readme_carries_the_notice_under_its_title(self):
        require_working_tree(self)
        text = (REPO / 'bench' / 'README.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('# Benchmark\n\n> ' + NOTICE.format(link='../docs/benchmark-deletion-list.md')
                                        + '\n\n'), text[:400])

    def test_01_B10_benchmark_support_has_a_readme_with_its_title_and_the_notice(self):
        require_working_tree(self)
        readme = REPO / 'src' / 'generative_driver' / 'benchmark_support' / 'README.md'
        self.assertTrue(readme.is_file(), 'src/generative_driver/benchmark_support/README.md is missing')
        self.assertEqual(readme.read_text(encoding='utf-8'), '# Benchmark support\n\n> '
                         + NOTICE.format(link='../../../docs/benchmark-deletion-list.md') + '\n')

    def test_01_B10_both_notices_link_to_the_list_and_both_files_are_on_it(self):
        require_working_tree(self)
        for readme in ('bench/README.md', 'src/generative_driver/benchmark_support/README.md'):
            text = (REPO / readme).read_text(encoding='utf-8') if (REPO / readme).is_file() else ''
            links = re.findall(r'\[deletion list\]\(([^)]+)\)', text)
            self.assertEqual(len(links), 1, f'{readme} links to the deletion list once')
            self.assertEqual(os.path.normpath(REPO / readme / '..' / links[0]), str(REPO / LIST), readme)
            self.assertTrue((REPO / LIST).is_file(), LIST)
        self.assertIn('| `bench/README.md` |', table('Still used by the tool'))
        self.assertIn('| `src/generative_driver/benchmark_support/` |', table('Ready to delete'))


class FailedModulesTests(unittest.TestCase):
    """How `failed_modules` runs the tool's test modules in the copy (08-B17 of
    docs/features/untangle/08-reports.md)."""

    def at_a_time(self, processors, expected):
        """The most test modules `failed_modules` had running at once on a machine that reports `processors`
        processors, with the time limits it gave them. Each module is held until `expected` of them run together,
        so fewer at a time than `expected` is a failure here. It then stays a moment longer, so that one module
        too many is seen running beside it, also where `expected` is one. No process is started."""
        running, most, limits = [], [], set()
        together, lock = threading.Barrier(expected), threading.Lock()

        def run(command, **options):
            with lock:
                running.append(command)
                most.append(len(running))
                limits.add(options.get('timeout'))
            try:
                together.wait(timeout=30)
                time.sleep(.02)
            finally:
                with lock:
                    running.remove(command)
            return subprocess.CompletedProcess(command, 0, b'', None)
        modules = [f'tests/test_{number}.py' for number in range(24)]
        with unittest.mock.patch('os.cpu_count', return_value=processors), unittest.mock.patch('subprocess.run', run):
            self.assertEqual(failed_modules(REPO / 'no copy is read', modules), [])
        return max(most), limits

    def test_08_B17_failed_modules_runs_no_more_at_a_time_than_the_machine_has_processors_and_never_over_eight(self):
        for processors, expected in ((1, 1), (2, 2), (3, 3), (4, 4), (8, 8), (16, 8), (64, 8), (None, 1)):
            with self.subTest(processors=processors):
                self.assertEqual(self.at_a_time(processors, expected)[0], expected)

    def test_08_B17_failed_modules_gives_each_module_900_seconds(self):
        self.assertEqual(MODULE_SECONDS, 900)
        self.assertEqual(self.at_a_time(4, 4)[1], {900})

        def never_ends(command, **options):
            raise subprocess.TimeoutExpired(command, options['timeout'], output=b'the last line it printed')
        with unittest.mock.patch('subprocess.run', never_ends):
            found = failed_modules(REPO / 'no copy is read', ['tests/test_slow.py'])
        self.assertEqual(found, ['tests/test_slow.py did not finish in 900 seconds in the copy:\n'
                                 'the last line it printed'])


# The 41 tests of the tool's own behaviour that ran on the benchmark's fixtures, by the tool test module each is in
# (10-B2 of docs/features/untangle/10-tests.md).
JOINED_TOOL_TESTS = {
    'tests/test_engine_protocols.py': (
        'test_negative_framed_sample_and_sequence', 'test_corrupt_frame_fails_before_decoding',
        'test_store_fixed_transaction_preserves_step_order', 'test_error_before_completion_refuses_commit',
        'test_denied_write_never_opens_transport', 'test_request_crc_covers_varying_argument',
        'test_toy_device_refuses_put_without_begin', 'test_toy_device_refuses_commit_without_staged_put',
        'test_commit_and_abort_across_separate_execute_connections'),
    'tests/test_rejection_framing.py': (
        'test_qualification_refuses_a_bound_that_cannot_capture_a_complete_rejection',
        'test_candidate_contract_refusal_preserves_structural_runtime_validity',
        'test_a_rejection_that_is_its_own_completion_can_use_one_line',
        'test_multiline_rejection_is_captured_completely_without_retry_or_later_steps',
        'test_missing_completion_and_receive_overflow_remain_incomplete_and_are_not_retried',
        'test_generic_deployment_repairs_the_contract_without_contacting_a_device',
        'test_qualification_uses_a_minimal_error_reply_within_the_declared_byte_bound',
        'test_qualification_completes_a_whitespace_prefixed_single_line_error',
        'test_managed_write_with_an_impossible_reply_is_refused_before_contact_and_repairs',
        'test_qualifier_reports_delay_only_operations_without_a_transfer_crash'),
    'tests/test_reuse_controller.py': (
        'test_accepting_fresh_reuse_completes_six_stage_run_with_only_emitted_package',
        'test_a_stage_check_in_the_gated_final_shape_is_no_verdict_and_never_dispatches_another_stage',
        'test_unsupported_saved_progress_refuses_resume_before_worker_dispatch',
        'test_reuse_requires_emitted_package_and_excludes_other_emission_evidence',
        'test_recovering_obsolete_active_progress_explains_incompatibility_without_dispatch'),
    'tests/test_service_repair.py': (
        'test_old_gateway_is_invalidated_when_a_model_defect_starts_a_new_revision',),
    'tests/test_owner_generated_integration.py': (
        'test_generic_reuse_serves_generated_catalog_and_records_frozen_execution',
        'test_reset_grant_and_open_failure_keep_uncertainty',
        'test_finish_distinguishes_a_preopen_refusal_from_live_guard_reads',
        'test_invocation_cancellation_before_dispatch_is_durable_and_does_not_cancel_session',
        'test_benchmark_journal_records_complete_generated_arguments_and_proof',
        'test_authored_contract_pair_survives_benchmark_adaptation_and_rejects_projection_mismatch',
        'test_device_rpc_waits_for_long_operation_and_never_resends_lost_reply',
        'test_recovery_accepts_only_owner_proven_generated_completed_model_failure',
        'test_resetting_session_read_keeps_failed_open_uncertain_and_retains_binding',
        'test_resetting_assigned_read_marks_pending_before_open_and_blocks_another_open'),
    'tests/test_emitted_package_calls.py': (
        'test_package_execute_treats_read_as_implicit_and_preserves_write_and_actuate',
        'test_emit_uses_accepted_bytes_and_requires_matching_current_executable_package'),
    'tests/test_supplied_stage_files.py': (
        'test_acquire_hands_on_only_actual_imported_bytes',
        'test_interpret_receives_neutral_sealed_inputs_without_evaluator_answers',
        'test_interpret_repair_preserves_firmware_and_seals_observed_defects',
        'test_a_valid_model_without_predicted_reply_qualification_is_handed_on_with_a_note')}


def test_names(path):
    """The names of the test methods the Python file at `path` defines, or none where there is no such file."""
    if not Path(path).is_file():
        return set()
    return {node.name for owner in ast.walk(ast.parse(Path(path).read_bytes())) if isinstance(owner, ast.ClassDef)
            for node in owner.body if isinstance(node, ast.FunctionDef) and node.name.startswith('test')}


def list_text():
    """The text of the deletion list of the repository."""
    return (REPO / LIST).read_text(encoding='utf-8')


class JoinedToolTestTests(unittest.TestCase):
    """The tests of the tool's own behaviour that ran on the benchmark's fixtures are tool tests (10-B2)."""

    def test_10_B2_the_41_tests_are_in_seven_tool_test_modules_that_have_no_row(self):
        require_working_tree(self)
        self.assertEqual(sum(map(len, JOINED_TOOL_TESTS.values())), 41)
        modules = tool_test_modules(REPO)
        for module, names in JOINED_TOOL_TESTS.items():
            with self.subTest(module=module):
                self.assertEqual(test_names(REPO / module), set(names))
                self.assertIn(module, modules)
                self.assertFalse(f'`{module}`' in list_text(), f'The list names {module}')

    def test_10_B2_the_three_modules_whose_tests_took_a_new_file_are_gone(self):
        require_working_tree(self)
        for name in ('test_benchmark_family_runtime.py', 'test_package_benchmark.py', 'test_benchmark_stages.py'):
            for folder in ('tests', 'firmware-bench/tests'):
                self.assertFalse((REPO / folder / name).exists(), f'{folder}/{name}')
            self.assertFalse(name in list_text(), f'The list names {name}')


# The test modules, without `test_`, and the helpers that moved to the benchmark's test folder (10-B5).
MOVED_MODULES = (
    'assistive_reports', 'assistive_wording', 'audit', 'benchmark_asset_build', 'benchmark_behavior',
    'benchmark_client_interface', 'benchmark_commands', 'benchmark_family_calibration', 'benchmark_ground',
    'benchmark_helper_copies', 'benchmark_legacy_cases_dropped', 'benchmark_mcp_reuse', 'benchmark_native',
    'benchmark_run', 'benchmark_runs', 'benchmark_scenarios', 'benchmark_score', 'benchmark_snapshots',
    'benchmark_suite_book', 'benchmark_supplied_stages', 'benchmark_surface', 'benchmark_tq9_v2', 'benchmark_truth',
    'benchmark_v2_evidence', 'case_registration', 'case_registry', 'corpus', 'corpus_preflight', 'corpus_workflow',
    'development_conditions', 'device_provider', 'device_recovery', 'emulator_recovery', 'evaluation', 'family_seam',
    'generated_device', 'generated_family', 'generated_sets', 'generated_trials', 'grader_identity', 'harness',
    'monitor_control', 'native_rehosting', 'outcomes', 'output_measure', 'pairing', 'parameter_store_family',
    'pipeline_repair', 'public_missions', 'quantity_repair', 'replay_executor', 'reporting', 'sampled_sensor_family',
    'scorecard', 'set_qualification', 'slot_policy', 'slot_policy_flags', 'suite_harness', 'suite_interfaces',
    'suite_manifest', 'suite_reporting', 'suite_service', 'suite_store', 'tq9_replacement', 'trial_sealing',
    'unlocked_keys', 'upstream_authoring', 'upstream_modbus', 'vault_gate')
MOVED_HELPERS = (
    'benchmark_case_stages', 'benchmark_family_fixtures', 'generated_fixtures', 'native_benchmark_families',
    'native_codex_pipeline', 'suite_fixtures', 'tq9_workflow_fixture', 'upstream_fixture')
BENCHMARK_FOLDER = 'firmware-bench/tests'
WORKFLOW = REPO / '.github' / 'workflows' / 'test.yml'
# The three lines of `STANDARDS.md` that the owner put in for this Feature (10-B6).
GRADER_BOUNDARY = '`firmware-bench/tests/test_grader_identity.py` checks the boundary.'
TEST_COMMAND_LINE = (
    'Test command: `ls tests/test_*.py firmware-bench/tests/test_*.py 2>/dev/null | PYTHONPATH="$PWD/src" xargs -P 8 '
    '-n 1 sh -c \'out=$(.venv/bin/python -m unittest discover -v -s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); '
    's=$?; printf "%s\\n" "$out"; exit $s\' && node tests/dashboard-client.test.js && GD_TEST_REMOVAL=1 '
    'PYTHONPATH="$PWD/src" .venv/bin/python -m unittest discover -v -s tests -p test_deletion_marking.py`')
TEST_FILES_LINE = 'Test files: `tests/test_*.py`, `firmware-bench/tests/test_*.py`'
# The five lines of the benchmark's documents that name a moved file or the folder, each as it reads now (10-B5).
DOCUMENT_LINES = (
    ('bench/README.md', 35, 'python -m unittest discover -s firmware-bench/tests -v'),
    ('bench/groundtruth/README.md', 73,
     'Explicit native gate: `python -m unittest discover -s firmware-bench/tests -p native_benchmark_families.py -v`.'),
    ('docs/benchmark-case-authoring.md', 30, '| `firmware-bench/tests/` | Cover public case resolution,'),
    ('docs/codex-acceptance.md', 37, 'python firmware-bench/tests/native_codex_pipeline.py -v'),
    ('docs/generated-device-interface.md', 186,
     '[firmware-bench/tests/generated_fixtures.py](../firmware-bench/tests/generated_fixtures.py)'))
READY_ITEMS = ('src/generative_driver/benchmark.py', 'src/generative_driver/benchmark_support/',
               'src/generative_driver/resources/bench/', 'src/generative_driver/resources/registrations/',
               'firmware-bench/tests/')
STILL_USED_DOCUMENTS = ('bench/README.md', 'bench/groundtruth/README.md', 'docs/benchmark-case-authoring.md',
                        'docs/benchmark-corpus.md', '.scratch/benchmark-lifecycle/')
# The files of the two dropped legacy cases, by the ready row whose note names them (10-B8).
DROPPED = {'src/generative_driver/benchmark_support/': ('cases.py', 'physical.py', 'recorded_grading.py',
                                                        'tq9_legacy.py'),
           'src/generative_driver/resources/bench/': ('cases/tq9/', 'cases/bme280/', 'groundtruth/tq9.enc',
                                                      'groundtruth/bme280.enc')}


def rows(heading):
    """The rows of the table under `heading` of the deletion list of the repository, each as its cells, the first
    without its backticks."""
    lines = [line.strip() for line in table(heading).splitlines() if line.startswith('|')]
    cells = [[cell.strip() for cell in re.split(r'(?<!\\)\|', line.strip('|'))] for line in lines]
    # The first two lines of a table are its column names and the rule under them.
    return [[row[0].strip('`'), *row[1:]] for row in cells[2:]]


def marked(text):
    """The lines of `text` inside its marked passages, without the markers."""
    kept, inside = [], False
    for line in text.splitlines():
        if line.strip() in (HASH_BEGIN, HASH_END):
            inside = line.strip() == HASH_BEGIN
        elif inside:
            kept.append(line)
    return kept


class TwoFoldersTests(unittest.TestCase):
    """The benchmark's tests lie in a folder of their own, and the test command, the hosted tests and the source
    archive know both folders (10-B5 to 10-B7)."""

    def test_10_B5_the_69_modules_and_the_8_helpers_lie_in_the_benchmarks_folder_and_not_in_the_tools(self):
        require_working_tree(self)
        self.assertEqual((len(MOVED_MODULES), len(MOVED_HELPERS)), (69, 8))
        for name in (*(f'test_{module}.py' for module in MOVED_MODULES), *(f'{helper}.py' for helper in MOVED_HELPERS)):
            with self.subTest(file=name):
                self.assertTrue((REPO / BENCHMARK_FOLDER / name).is_file(), f'{BENCHMARK_FOLDER}/{name} is missing')
                self.assertFalse((REPO / 'tests' / name).exists(), f'tests/{name} is still there')

    def test_10_B5_discovery_from_the_benchmarks_folder_alone_finds_the_tests_of_every_moved_module(self):
        require_working_tree(self)
        from package_env import package_env
        script = ('import sys, unittest\n'
                  'loader = unittest.TestLoader()\n'
                  'found, pending = set(), [loader.discover(sys.argv[1])]\n'
                  'while pending:\n'
                  '    for test in pending.pop():\n'
                  '        if isinstance(test, unittest.TestSuite):\n'
                  '            pending.append(test)\n'
                  '        else:\n'
                  '            found.add(type(test).__module__)\n'
                  'print("\\n".join(sorted(found)))\n'
                  'print("\\n".join("ERROR " + error.splitlines()[-1] for error in loader.errors))\n')
        with tempfile.TemporaryDirectory(prefix='discovery ') as elsewhere:
            done = subprocess.run([sys.executable, '-c', script, str(REPO / BENCHMARK_FOLDER)], cwd=elsewhere,
                                  env={**os.environ, **package_env()}, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, timeout=MODULE_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        found = done.stdout.split('\n')
        self.assertEqual([line for line in found if line.startswith('ERROR')], [])
        self.assertEqual(sorted(set(f'test_{module}' for module in MOVED_MODULES) - set(found)), [])

    def test_10_B5_the_manifest_puts_the_benchmarks_test_files_in_the_source_archive_in_a_marked_passage(self):
        require_working_tree(self)
        text = (REPO / 'MANIFEST.in').read_text(encoding='utf-8')
        self.assertIn('recursive-include firmware-bench/tests *.py', marked(text))
        copy, _ = stripped_repository()
        left = (copy / 'MANIFEST.in').read_text(encoding='utf-8')
        self.assertEqual([line for line in left.splitlines() if 'firmware-bench' in line], [])
        self.assertIn('recursive-include tests *.py *.js', left.splitlines())

    def test_10_B5_the_five_document_lines_that_named_a_moved_file_or_the_folder_name_the_new_place(self):
        require_working_tree(self)
        for document, number, text in DOCUMENT_LINES:
            with self.subTest(document=document):
                line = (REPO / document).read_text(encoding='utf-8').splitlines()[number - 1]
                self.assertIn(text, line)
                self.assertIsNone(re.search(r'(?<![\w/-])tests/|-s tests\b', line), line)

    def test_10_B6_standards_holds_the_three_lines_for_the_two_folders(self):
        require_working_tree(self)
        lines = (REPO / 'STANDARDS.md').read_text(encoding='utf-8').splitlines()
        self.assertTrue(lines[11].endswith(' ' + GRADER_BOUNDARY), lines[11])
        self.assertEqual(lines[28], TEST_COMMAND_LINE)
        self.assertEqual(lines[30], TEST_FILES_LINE)
        self.assertTrue((REPO / 'firmware-bench' / 'tests' / 'test_grader_identity.py').is_file())

    @unittest.skipIf(os.name == 'nt', 'The test command is a line of sh, and the kit runs it where sh is')
    def test_10_B6_the_test_command_runs_each_module_of_both_folders_in_its_own_process_and_fails_when_one_fails(self):
        require_working_tree(self)
        line = next(line for line in (REPO / 'STANDARDS.md').read_text(encoding='utf-8').splitlines()
                    if line.startswith('Test command: `'))
        # The part before the dashboard test and the removal: the modules of the two folders.
        modules = line.removeprefix('Test command: `').split(' && node ')[0]

        def ran(files):
            """The exit status of that part in a new tree that holds `files`, and the process each module ran in."""
            with tempfile.TemporaryDirectory(prefix='test command ') as temporary:
                root = Path(temporary).resolve()
                for path, body in files.items():
                    (root / path).parent.mkdir(parents=True, exist_ok=True)
                    (root / path).write_text(test_module(*body), encoding='utf-8')
                python = root / '.venv' / 'bin' / 'python'
                python.parent.mkdir(parents=True)
                python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding='utf-8')
                python.chmod(0o755)
                done = subprocess.run(['sh', '-c', modules], cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, timeout=MODULE_SECONDS)
                return done.returncode, dict(re.findall(r'^ran (\S+) in (\d+)$', done.stdout, re.MULTILINE))

        says = ("print('ran', Path(__file__).resolve().relative_to(Path.cwd().resolve()).as_posix(), 'in', "
                "os.getpid())",)
        passing = {'tests/test_tool.py': says, 'tests/test_more.py': says, 'tests/helper.py': ("raise SystemExit(3)",),
                   'firmware-bench/tests/test_bench.py': says, 'firmware-bench/tests/fixture.py': says}
        status, processes = ran(passing)
        self.assertEqual(status, 0)
        self.assertEqual(sorted(processes), ['firmware-bench/tests/test_bench.py', 'tests/test_more.py',
                                             'tests/test_tool.py'])
        self.assertEqual(len(set(processes.values())), 3, processes)
        for failing in ('tests/test_tool.py', 'firmware-bench/tests/test_bench.py'):
            with self.subTest(failing=failing):
                status, processes = ran({**passing, failing: (*says, "self.fail('it fails')")})
                self.assertNotEqual(status, 0)
                self.assertEqual(len(processes), 3, processes)
        # Where the benchmark's folder is gone, the command runs the modules of the tool's folder alone.
        status, processes = ran({path: body for path, body in passing.items() if path.startswith('tests/')})
        self.assertEqual((status, sorted(processes)), (0, ['tests/test_more.py', 'tests/test_tool.py']))

    def test_10_B6_the_command_then_runs_the_dashboard_test_and_the_removal_as_before(self):
        require_working_tree(self)
        self.assertTrue(TEST_COMMAND_LINE.endswith(
            ' && node tests/dashboard-client.test.js && GD_TEST_REMOVAL=1 PYTHONPATH="$PWD/src" .venv/bin/python '
            '-m unittest discover -v -s tests -p test_deletion_marking.py`'))
        self.assertIn(TEST_COMMAND_LINE, (REPO / 'STANDARDS.md').read_text(encoding='utf-8').splitlines())

    def test_10_B7_the_marked_passage_of_the_step_runs_the_benchmarks_tests_between_the_tools_and_the_smoke(self):
        require_working_tree(self)
        step = WORKFLOW.read_text(encoding='utf-8').partition('      - name: Test outside the checkout\n')[2]
        commands = [line.strip() for line in step.splitlines() if 'subprocess.run(' in line]
        benchmark = ("subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', "
                     "str(repo / 'firmware-bench' / 'tests'), '-v'], cwd=work, env=env, check=True)")
        self.assertEqual([line.strip() for line in marked(step)], [benchmark])
        self.assertEqual(len(commands), 3, commands)
        self.assertIn("str(repo / 'tests')", commands[0])
        self.assertEqual(commands[1], benchmark)
        self.assertIn("'-m', 'generative_driver', 'smoke'", commands[2])
        for command in commands:
            self.assertTrue(command.endswith('cwd=work, env=env, check=True)'), command)

    def test_10_B7_the_workflow_of_the_stripped_copy_runs_the_tools_tests_and_the_tools_smoke_only(self):
        require_working_tree(self)
        copy, _ = stripped_repository()
        left = (copy / '.github' / 'workflows' / 'test.yml').read_text(encoding='utf-8')
        commands = [line.strip() for line in left.splitlines() if 'subprocess.run(' in line and 'pip' not in line]
        self.assertEqual(len(commands), 2, commands)
        self.assertIn("'-m', 'unittest', 'discover', '-s', str(repo / 'tests'), '-v'", commands[0])
        self.assertIn("'-m', 'generative_driver', 'smoke'", commands[1])
        self.assertEqual([line for line in left.splitlines() if 'bench' in line.lower()], [])
        # Nothing else of the workflow is marked: the copy is the workflow without that one command.
        whole = WORKFLOW.read_text(encoding='utf-8')
        gone = [line.strip() for line in whole.splitlines() if line not in left.splitlines()]
        self.assertEqual(gone, [HASH_BEGIN, *(line.strip() for line in marked(whole)), HASH_END])
        self.assertEqual(len(gone), 3, gone)


class ReadyBenchmarkTests(unittest.TestCase):
    """The benchmark's code, resources and tests are ready to delete, and the removal takes them out (10-B8)."""

    def test_10_B8_the_benchmarks_code_its_resources_and_its_tests_stand_under_ready_to_delete(self):
        require_working_tree(self)
        ready = [row[0] for row in rows('Ready to delete')]
        for item in READY_ITEMS:
            with self.subTest(item=item):
                self.assertEqual(ready.count(item), 1)
        self.assertEqual([item for item in ready if item.startswith('firmware-bench/')], ['firmware-bench/tests/'])

    def test_10_B8_the_eight_rows_of_the_dropped_legacy_cases_are_gone_and_two_notes_name_their_files(self):
        require_working_tree(self)
        notes = {row[0]: row[1] for row in rows('Ready to delete')}
        for folder, files in DROPPED.items():
            with self.subTest(folder=folder):
                self.assertEqual([item for item in notes if item in (folder + name for name in files)], [])
                sentence = next((part for part in re.split(r'(?<=\.)\s', notes.get(folder, ''))
                                 if 'dropped legacy case' in part), '')
                self.assertEqual(re.findall(r'`([^`]+)`', sentence), list(files), notes.get(folder))
                self.assertIn('not carried to the benchmark\'s repository', sentence)
                for name in files:
                    self.assertTrue((REPO / folder / name).exists(), folder + name)

    def test_10_B8_no_row_names_a_file_under_tests_but_the_check_and_the_lists_tests(self):
        require_working_tree(self)
        named = [row[0] for heading, _ in TABLES for row in rows(heading) if row[0].startswith('tests/')]
        self.assertEqual(named, ['tests/deletion_marking.py', 'tests/test_deletion_marking.py'])

    def test_10_B8_the_stripped_copy_holds_none_of_the_ready_items(self):
        require_working_tree(self)
        copy, failures = stripped_repository()
        self.assertEqual(len(failures), 0, '\n'.join(failures))
        for item in READY_ITEMS:
            with self.subTest(item=item):
                self.assertTrue((REPO / item).exists(), item)
                self.assertFalse((copy / item).exists(), item)
        self.assertFalse((copy / 'firmware-bench').exists())
        self.assertTrue((copy / 'src' / 'generative_driver' / 'configurator.py').is_file())
        self.assertTrue((copy / 'src' / 'generative_driver' / 'resources' / 'smoke' / 'model.json').is_file())

    def test_10_B8_a_python_child_in_the_stripped_copy_cannot_import_the_benchmarks_code(self):
        require_working_tree(self)
        copy, _ = stripped_repository()
        script = ('import importlib, json\n'
                  'import generative_driver\n'
                  'answers = {"generative_driver": generative_driver.__file__}\n'
                  'for name in ("generative_driver.benchmark", "generative_driver.benchmark_support"):\n'
                  '    try:\n'
                  '        answers[name] = importlib.import_module(name).__file__\n'
                  '    except ModuleNotFoundError as error:\n'
                  '        answers[name] = "missing: " + str(error.name)\n'
                  'print(json.dumps(answers))\n')
        # The tree the copy was made from still has both, and this process may import from it.
        self.assertTrue((REPO / 'src' / 'generative_driver' / 'benchmark.py').is_file())
        with unittest.mock.patch.dict(os.environ, {'PYTHONPATH': str(REPO / 'src')}):
            done = subprocess.run([sys.executable, '-c', script], cwd=copy, env=removal_environment(copy),
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=MODULE_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr[-2000:])
        answers = json.loads(done.stdout)
        self.assertEqual(Path(answers.pop('generative_driver')).resolve(),
                         (copy / 'src' / 'generative_driver' / '__init__.py').resolve())
        self.assertEqual(answers, {'generative_driver.benchmark': 'missing: generative_driver.benchmark',
                                   'generative_driver.benchmark_support':
                                       'missing: generative_driver.benchmark_support'})


class ListAfterTheTestsTests(unittest.TestCase):
    """The list is true once the tests are pulled apart (10-B10)."""

    def test_10_B10_only_the_five_documents_are_still_used_each_freed_by_11(self):
        require_working_tree(self)
        still_used = rows('Still used by the tool')
        self.assertEqual([row[0] for row in still_used], list(STILL_USED_DOCUMENTS))
        self.assertEqual({row[2] for row in still_used}, {'11'})

    def test_10_B10_each_reason_under_still_used_names_files_that_are_there_and_that_name_the_item(self):
        require_working_tree(self)
        for item, reason, _ in rows('Still used by the tool'):
            users = re.findall(r'`([^`]+)`', reason)
            self.assertTrue(users, f'The reason of {item} names no file')
            for user in users:
                with self.subTest(item=item, user=user):
                    self.assertTrue((REPO / user).is_file(), f'{user} is not a file')
                    text = (REPO / user).read_text(encoding='utf-8')
                    link = Path(os.path.relpath(REPO / item, (REPO / user).parent)).as_posix()
                    self.assertTrue(item.rstrip('/') in text or link in text, f'{user} does not name {item}')

    def test_10_B10_no_reason_and_no_note_names_a_test_file_at_a_place_it_no_longer_has(self):
        require_working_tree(self)
        every = [row for heading, _ in TABLES for row in rows(heading)]
        # The item of a row stands without its backticks; a reason or a note names a file between backticks.
        named = {row[0] for row in every} | {path for row in every for cell in row[1:]
                                             for path in re.findall(r'`([^`]+)`', cell)}
        tests = sorted(path for path in named if re.match(r'(firmware-bench/)?tests/', path))
        self.assertLessEqual({'firmware-bench/tests/', 'tests/deletion_marking.py', 'tests/test_deletion_marking.py'},
                             set(tests))
        self.assertEqual([path for path in tests if not (REPO / path).exists()], [])

    def test_10_B10_every_freed_by_under_passages_is_11_or_stays_and_nothing_names_feature_10(self):
        require_working_tree(self)
        passages = rows('Passages')
        self.assertGreater(len(passages), 15)
        self.assertEqual({row[3] for row in passages}, {'11', 'stays'})
        acceptance, = [row for row in passages if row[0] == 'docs/codex-acceptance.md']
        self.assertIn('Feature 11 settles what moves', acceptance[2])
        self.assertIsNone(re.search(r'Feature\s+10\b', list_text()))
        freed = [row[-1] for heading in ('Still used by the tool', 'Tool modules that import the benchmark',
                                         'Commands', 'Passages') for row in rows(heading)]
        self.assertNotIn('10', freed)

    def test_10_B10_every_count_under_passages_is_true_and_the_row_of_standards_says_4(self):
        require_working_tree(self)
        standards, = [row for row in rows('Passages') if row[0] == 'STANDARDS.md']
        self.assertEqual(standards[1], '4')
        counted = [sentence for sentence in problems(REPO) if 'unmarked lines that say bench' in sentence]
        self.assertEqual(len(counted), 0, '\n'.join(counted))


if __name__ == '__main__':
    unittest.main()
