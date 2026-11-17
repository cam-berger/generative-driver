"""The deletion list, the markers and the removal hold to the rules of the deletion marking.

Feature 01 of the untangle Run (docs/features/untangle/01-deletion-marking.md). Each rule is tested through
`problems`, `strip` and `tool_test_modules` on a small git repository built in a temporary directory. The repository
itself is tested for 01-B9 and 01-B10.
"""
import ast
import atexit
from concurrent.futures import ThreadPoolExecutor
import functools
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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


def failed_modules(copy, modules):
    """A sentence for each of the test `modules` that fails in the tree at `copy`, with the end of its output. Each
    module runs in its own process. No more run at a time than the machine has processors, never more than eight,
    and one at a time where the number of processors is not known (08-B17 of
    docs/features/untangle/08-reports.md). A child's `PYTHONPATH` holds only the `src` of `copy`, so it cannot import
    from the tree the copy was made from (01-B7)."""
    environment = {**os.environ, 'PYTHONPATH': str(Path(copy) / 'src')}

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
                                modules=[('`src/tool.py`', '12'), ('`src/other.py`', '02')],
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

    def test_04_B7_takes_02_to_11_as_a_freed_by_and_reports_12_as_no_later_feature(self):
        numbers = [f'{number:02d}' for number in range(1, 13)]
        root = self.tree({
            **{f'docs/{number}.md': 'Text.\n' for number in numbers},
            LIST: deletion_list(still_used=[(f'`docs/{number}.md`', '`README.md`', number) for number in numbers])})
        found = sorted(problems(root))
        self.assertEqual(len(found), 2, found)
        for sentence, number in zip(found, ('01', '12')):
            self.assertIn(f'`docs/{number}.md` under Still used by the tool', sentence)
            self.assertIn('02, 03, 04, 05, 06, 07, 08, 09, 10, 11: ' + number, sentence)

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
        self.assertLess(setting.start(), steps[0].index("'-m', 'unittest', 'discover'"))

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
        self.assertIn('| `src/generative_driver/benchmark_support/` |', table('Still used by the tool'))


if __name__ == '__main__':
    unittest.main()
