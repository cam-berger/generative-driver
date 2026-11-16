"""The two legacy benchmark cases, the old `tq9` and the physical `bme280`, are dropped.

Feature 04 of the untangle Run (docs/features/untangle/04-legacy-cases.md), 04-B1, 04-B2, 04-B3, 04-B5, 04-B6 and the
first, third and fourth points of 04-B7. This file is a benchmark item: it reads the benchmark package and the deletion
list, and the deletion removes it with them. The service's side, 04-B4, is in
`tests/test_service_without_legacy_cases.py`, which stays. It is also the one file that holds the file names of the
dropped cases' modules and answer files (04-B3).
"""
import ast
import contextlib
from dataclasses import replace
import io
import re
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

from deletion_marking import problems
from test_deletion_marking import LIST, REPO, require_working_tree, stripped_repository, table

SUPPORT = 'src/generative_driver/benchmark_support/'
# The stage code, which Feature 05 moved from the benchmark's folder to the tool's stage package.
STAGES = 'src/generative_driver/supplied/stages.py'
DROPPED_MODULES = ('cases', 'physical', 'recorded_grading', 'tq9_legacy')
DROPPED_FILES = tuple(name + '.py' for name in DROPPED_MODULES) + ('tq9.enc', 'bme280.enc')
THIS_FILE = 'tests/test_benchmark_legacy_cases_dropped.py'
RESOURCES = 'src/generative_driver/resources/bench/'
# The dropped cases' modules and resources: neither deleted nor edited, and ready to delete.
EIGHT_ITEMS = (*(SUPPORT + name for name in DROPPED_FILES[:4]), RESOURCES + 'cases/tq9/', RESOURCES + 'cases/bme280/',
               *(RESOURCES + 'groundtruth/' + name for name in DROPPED_FILES[4:]))
NOTE = "Dropped legacy case. Not carried to the benchmark's repository."
CLIENT_INTERFACE = 'docs/client-interface.md'
BEGIN, END = '<!-- legacy-benchmark:begin -->', '<!-- legacy-benchmark:end -->'
STAND_IN_MODULES = ('tests/test_suite_store.py', 'tests/test_suite_service.py', 'tests/test_suite_interfaces.py',
                    'tests/test_benchmark_snapshots.py', 'tests/test_suite_manifest.py', 'tests/suite_fixtures.py')
LEGACY_FIXTURES = ('legacy_manifest', 'legacy_case', 'LEGACY_DEVICE_FIXTURE', 'legacy_device_patches',
                   'legacy_devices')
DROPPED_CASES = ('tq9', 'bme280')
REGISTERED = ('setup-smoke', 'tq9-v2', 'tq9-v3', 'sampled-sensor-v1', 'parameter-store-v1', 'zephyr-modbus-v1',
              'generated')
# What `grader_closure()` names: every module it named before this Feature but the old tq9's family module.
GRADING_CODE = tuple('benchmark_support' + name for name in (
    '', '.behavior', '.calibration', '.capabilities', '.case_assets', '.device_provider', '.emulated_actions',
    '.emulator', '.evaluator', '.evidence', '.execution', '.families', '.family_support', '.generated',
    '.generated_device', '.generated_sets', '.grader_identity', '.instruments', '.integrity', '.labels', '.mcp_reuse',
    '.missions', '.native', '.native_comparison', '.native_firmware', '.parameter_store', '.registrations', '.registry',
    '.sampled_sensor', '.snapshots', '.tq9_v2', '.truth', '.upstream', '.zephyr_modbus'))


def code_that_stays():
    """Every Python file under `src/` and `tests/` other than the dropped cases' modules, as paths from the
    repository root written with `/`."""
    dropped = {SUPPORT + name for name in DROPPED_FILES}
    files = sorted(path.relative_to(REPO).as_posix() for folder in ('src', 'tests')
                   for path in (REPO / folder).rglob('*.py'))
    return [file for file in files if file not in dropped]


def reaches(file, source):
    """Each way the Python `source` of `file` reaches a dropped module or an answer file of a dropped case, as a
    sentence that names the line: an import statement at any depth that names one of the four modules, and a string
    that is one of the six file names or ends with `/` and one of them. A relative import is named from the folders
    of `file` below `src`."""
    found, folders = [], file.split('/')[1:-1] if file.startswith('src/') else []
    for node in ast.walk(ast.parse(source)):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = folders[:len(folders) - node.level + 1] if node.level else []
            base = '.'.join([*base, *([node.module] if node.module else [])])
            names = [base, *(f'{base}.{alias.name}' for alias in node.names)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in DROPPED_FILES or node.value.endswith(tuple('/' + name for name in DROPPED_FILES)):
                found.append(f'{file}:{node.lineno} names {node.value}')
        for name in names:
            parts = name.split('.')
            if any(triple[:2] == ('generative_driver', 'benchmark_support') and triple[2] in DROPPED_MODULES
                   for triple in zip(parts, parts[1:], parts[2:])):
                found.append(f'{file}:{node.lineno} imports {name}')
    return found


def rows(heading):
    """The rows of the table under `heading` of the deletion list, each a list of its cells, read from the list's
    text: after the column names and the rule under them."""
    lines = [line for line in table(heading).splitlines() if line.startswith('|')]
    return [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in lines[2:]]


def freed_by(heading):
    """The `Freed by` of each row of the table under `heading`, keyed by its item without backticks. A file with
    several rows under `Passages` gives the sorted list of them."""
    found = {}
    for cells in rows(heading):
        found.setdefault(cells[0].strip('`'), []).append(cells[-1])
    return {item: sorted(values) for item, values in found.items()}


def strings(tree):
    """The string constants of a parsed module."""
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def identifiers(tree):
    """Every name a parsed module binds, reads or imports: names, attributes, imported names, definitions, arguments
    and keywords."""
    found = set()
    for node in ast.walk(tree):
        for field in ('id', 'attr', 'name', 'asname', 'arg'):
            value = getattr(node, field, None)
            if isinstance(value, str):
                found.update(value.split('.'))
    return found


class RegistrationTests(unittest.TestCase):
    def test_04_B1_the_registered_cases_are_the_smoke_and_the_six_current_cases_in_order(self):
        from generative_driver.benchmark_support import registry
        self.assertEqual(registry.case_ids(), REGISTERED)

    def test_04_B1_a_dropped_case_is_refused_as_a_name_that_was_never_registered(self):
        from generative_driver.benchmark_support import registry
        for case in (*DROPPED_CASES, 'never-registered'):
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, f'^Unknown benchmark case: {case}$'):
                registry.pin_case(case, None, 0)

    def test_04_B1_a_start_through_the_service_that_names_a_dropped_case_is_refused_with_that_reason(self):
        from generative_driver.configurator import Controller
        with tempfile.TemporaryDirectory() as home:
            controller = Controller(home)
            try:
                for case in (*DROPPED_CASES, 'never-registered'):
                    refusal = f'^Unknown benchmark case: {case}$'
                    with self.subTest(case=case), self.assertRaisesRegex(ValueError, refusal):
                        controller.call('start', {'goal': 'A run of a dropped case', 'case': case,
                                                  'executor_config': {'command': ['missing-runtime']}})
                with controller._db() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)
            finally:
                controller.close()

    def test_04_B1_the_stage_adapters_are_the_current_one_and_the_smokes(self):
        from generative_driver.benchmark_support import adapters, registrations
        adapter = adapters.adapter_for('tq9-v2')
        self.assertTrue(callable(adapter.supplied_inputs) and callable(adapter.score))
        with self.assertRaisesRegex(ValueError, '^Setup smoke has no agent stages$'):
            adapters.adapter_for('setup-smoke').supplied_inputs('setup-smoke', 'run')
        for key in ('legacy-emulator', 'legacy-physical'):
            # No registered case has such a key any more, so a row with it is registered for as long as this runs.
            row = replace(registrations.registration_for('tq9-v2'), id='with-a-dropped-adapter', adapter_key=key)
            registered = MappingProxyType({**registrations._REGISTRATIONS, row.id: row})
            with self.subTest(key=key), patch.object(registrations, '_REGISTRATIONS', registered), \
                    self.assertRaisesRegex(ValueError, f'^Benchmark adapter is pending: {key}$'):
                adapters.adapter_for(row.id)

    def test_04_B1_the_benchmark_module_no_longer_has_package_execute(self):
        from generative_driver import benchmark
        from generative_driver.supplied import stages
        self.assertFalse(hasattr(benchmark, 'package_execute'))
        # The device tool of every case stays, in the tool's stage package: its package call and the journal of its
        # calls.
        for name in ('prepare_stage', 'observe_stage', 'package_call', 'record_call'):
            self.assertTrue(callable(getattr(stages, name)), name)

    def test_04_B1_the_truth_command_has_no_default_case_and_requires_one(self):
        from generative_driver import benchmark
        with tempfile.TemporaryDirectory() as output:
            printed = io.StringIO()
            with contextlib.redirect_stderr(printed), contextlib.redirect_stdout(io.StringIO()), \
                    self.assertRaises(SystemExit) as refused:
                benchmark.main(['truth', 'unlock', '--output', output])
            self.assertEqual(refused.exception.code, 2)
            self.assertIn('the following arguments are required: --case', printed.getvalue())
            for case in DROPPED_CASES:
                printed = io.StringIO()
                with self.subTest(case=case), contextlib.redirect_stderr(printed), self.assertRaises(SystemExit):
                    benchmark.main(['truth', 'unlock', '--case', case, '--output', output])
                self.assertIn('invalid choice', printed.getvalue())

    def test_04_B1_the_benchmark_tool_no_longer_says_that_a_dropped_case_runs(self):
        from generative_driver import mcp
        description = ' '.join(mcp.driver_benchmark_run.__doc__.split())
        self.assertIn('the harness provides the emulator', description)
        self.assertEqual(re.findall(r'(?<![\w-])tq9(?![\w-])|bme280', description), [])


class GradingCodeTests(unittest.TestCase):
    def test_04_B2_grading_code_is_the_34_modules_without_the_old_tq9_family(self):
        from generative_driver.benchmark_support.grader_identity import grader_closure
        self.assertEqual(len(GRADING_CODE), 34)
        self.assertEqual(grader_closure(), GRADING_CODE)
        self.assertNotIn('benchmark_support.' + DROPPED_MODULES[3], grader_closure())


class NothingReachesTheDroppedCasesTests(unittest.TestCase):
    def test_04_B3_the_scan_names_each_form_of_import_and_of_file_name(self):
        inside, outside = SUPPORT + 'emulated.py', 'tests/test_some_case.py'
        for file, line in ((inside, 'from .cases import _state'), (inside, 'from . import physical'),
                           (inside, 'def grade():\n    from . import evaluator, recorded_grading'),
                           ('src/generative_driver/benchmark.py', 'from .benchmark_support import tq9_legacy'),
                           (outside, 'import generative_driver.benchmark_support.cases'),
                           (outside, 'from generative_driver.benchmark_support import labels, physical'),
                           (outside, 'from generative_driver.benchmark_support.recorded_grading import grade'),
                           (outside, "PATH = ROOT / 'tq9_legacy.py'"), (outside, "PATH = ROOT / 'groundtruth/tq9.enc'"),
                           (outside, "NAMES = ['benchmark_support/cases.py', 'bme280.enc']")):
            with self.subTest(file=file, line=line):
                self.assertTrue(reaches(file, line))
        for file, line in ((inside, 'from .case_assets import write_json'), (inside, 'from . import tq9_v2'),
                           (outside, 'from generative_driver.benchmark_support import registry'),
                           (outside, 'import cases'), (outside, "PATH = ROOT / 'cases/tq9-v2/case.json'"),
                           (outside, "NAMES = ['test_cases.py', 'tq9-v2.enc', 'the old cases.py file']")):
            with self.subTest(file=file, line=line):
                self.assertEqual(reaches(file, line), [])

    def test_04_B3_no_code_that_stays_imports_a_dropped_module_or_names_it_or_an_answer_file(self):
        files = code_that_stays()
        for file in (STAGES, SUPPORT + 'harness.py', 'src/generative_driver/configurator.py',
                     'tests/test_benchmark_tq9_v2.py', 'tests/suite_fixtures.py'):
            self.assertIn(file, files)
        for name in DROPPED_FILES[:4]:
            self.assertNotIn(SUPPORT + name, files)
        found = [sentence for file in files if file != THIS_FILE
                 for sentence in reaches(file, (REPO / file).read_bytes())]
        self.assertEqual(len(found), 0, '\n'.join(found))

    def test_04_B3_the_stage_code_and_the_harness_keep_no_part_of_the_dropped_cases(self):
        from generative_driver.benchmark_support import harness
        from generative_driver.benchmark_support.truth import UnlockedTruth
        from generative_driver.supplied import stages
        self.assertFalse(hasattr(stages, 'RECORDED'), 'stages.py still names the cases graded from recorded reuse')
        self.assertFalse(hasattr(harness, 'legacy_recipe'), 'harness.py still projects the old tq9 recipe')
        # The device recipe is the answer key's own, whatever the case is called.
        recipe = {'observations': {'reading': {'command': 'read', 'width': 32, 'count': 1}}}
        for case in (*DROPPED_CASES, 'tq9-v2'):
            with self.subTest(case=case):
                self.assertEqual(harness.device_recipe(case, UnlockedTruth({'recipe': recipe, 'checks': []}, 'f' * 64)),
                                 recipe)


class StandInModuleTests(unittest.TestCase):
    def test_04_B6_no_string_of_a_stand_in_module_is_a_dropped_case(self):
        for file in STAND_IN_MODULES:
            with self.subTest(file=file):
                found = strings(ast.parse((REPO / file).read_bytes()))
                self.assertIn('tq9-v2', found, 'The stand-in module names the current case it runs on')
                self.assertEqual([text for text in found if text in ('tq9', 'bme280')], [])

    def test_04_B6_the_five_legacy_fixtures_are_gone_and_no_test_file_uses_one(self):
        import suite_fixtures
        for name in LEGACY_FIXTURES:
            with self.subTest(name=name):
                self.assertFalse(hasattr(suite_fixtures, name), f'tests/suite_fixtures.py still has {name}')
        files = sorted(path.relative_to(REPO).as_posix() for path in (REPO / 'tests').rglob('*.py'))
        self.assertIn('tests/suite_fixtures.py', files)
        used = []
        for file in files:
            tree = ast.parse((REPO / file).read_bytes())
            # A fixture a spawned harness loads is code kept in a string, so strings are read too, but not this file's.
            texts = [] if file == THIS_FILE else strings(tree)
            used += [f'{file} uses {name}' for name in LEGACY_FIXTURES
                     if name in identifiers(tree) or any(re.search(rf'(?<!\w){name}(?!\w)', text) for text in texts)]
        self.assertEqual(len(used), 0, '\n'.join(used))


class DeletionListTests(unittest.TestCase):
    def test_04_B5_each_of_the_eight_items_is_ready_to_delete_as_a_dropped_legacy_case(self):
        ready = {cells[0].strip('`'): cells[1] for cells in rows('Ready to delete')}
        for item in EIGHT_ITEMS:
            with self.subTest(item=item):
                self.assertIn(item, ready)
                self.assertTrue(ready[item].startswith(NOTE), ready[item])
        self.assertEqual(len([note for note in ready.values() if note.startswith(NOTE)]), 8)
        # The folders they lie in are still used by the tool, each with a row of its own.
        still_used = [cells[0].strip('`') for cells in rows('Still used by the tool')]
        for folder in (SUPPORT, RESOURCES):
            self.assertIn(folder, still_used)

    def test_04_B5_the_eight_items_are_neither_deleted_nor_gone_from_the_repository(self):
        for item in EIGHT_ITEMS:
            with self.subTest(item=item):
                path = REPO / item
                self.assertTrue(path.is_dir() and any(path.iterdir()) if item.endswith('/') else path.is_file())

    def test_04_B5_the_check_reports_no_problem_and_the_removal_takes_the_eight_items_out(self):
        require_working_tree(self)
        found = problems(REPO)
        self.assertEqual(len(found), 0, '\n'.join(found))
        copy, failures = stripped_repository()
        self.assertEqual(len(failures), 0, '\n'.join(failures))
        for item in EIGHT_ITEMS:
            with self.subTest(item=item):
                self.assertFalse((copy / item).exists(), 'The stripped copy still holds ' + item)
        # The removal takes only them out of the two folders: what the tool still uses stays, and so does the stage
        # code, which is the tool's.
        for path in (STAGES, SUPPORT + 'harness.py', RESOURCES + 'cases/tq9-v2/case.json',
                     RESOURCES + 'cases/setup-smoke/model.json', RESOURCES + 'groundtruth/tq9-v2.enc'):
            self.assertEqual((copy / path).read_bytes(), (REPO / path).read_bytes(), path)

    def test_04_B5_this_file_is_ready_to_delete_and_the_services_test_is_not_on_the_list(self):
        ready = [cells[0].strip('`') for cells in rows('Ready to delete')]
        self.assertIn(THIS_FILE, ready)
        for heading in ('Ready to delete', 'Still used by the tool', 'Kept'):
            listed = [cells[0].strip('`') for cells in rows(heading)]
            self.assertNotIn('tests/test_service_without_legacy_cases.py', listed)
            # The rows of the two test files this Feature removed went with them.
            self.assertNotIn('tests/test_legacy_cases.py', listed)
            self.assertNotIn('tests/test_benchmark_physical.py', listed)

    def test_04_B7_every_freed_by_names_feature_08_09_or_10_or_stays(self):
        modules = freed_by('Tool modules that import the benchmark')
        source = 'src/generative_driver/'
        self.assertEqual(modules, {source + 'cli.py': ['09'], source + 'configurator.py': ['08'],
                                   source + 'mcp.py': ['09'], source + 'reporting.py': ['08']})
        commands = freed_by('Commands')
        self.assertEqual(len(commands), 8)
        self.assertEqual({value for values in commands.values() for value in values}, {'09'})
        still_used = freed_by('Still used by the tool')
        self.assertGreater(len(still_used), 60)
        self.assertEqual({value for values in still_used.values() for value in values}, {'10'})
        passages = freed_by('Passages')
        # What named Feature 08 names 09, what named 09 names 10, and the three rows that stay still do.
        expected = {'.github/workflows/test.yml': ['09'], 'README.md': ['09', '10', 'stays'],
                    'MANIFEST.in': ['10', 'stays'], 'docs/design.md': ['10', 'stays']}
        self.assertEqual({file: values for file, values in passages.items() if values != ['10']}, expected)
        self.assertGreater(len(passages), 15)
        every = [value for heading in ('Still used by the tool', 'Tool modules that import the benchmark', 'Commands',
                                       'Passages') for values in freed_by(heading).values() for value in values]
        self.assertEqual(set(every), {'08', '09', '10', 'stays'})
        self.assertEqual(every.count('stays'), 3)

    def test_04_B7_the_list_names_feature_09_for_the_entry_point_and_feature_10_for_what_moves(self):
        commands = table('Commands')
        self.assertIn('Feature 09 gives the benchmark its own entry point.', commands)
        acceptance = [cells for cells in rows('Passages') if cells[0] == '`docs/codex-acceptance.md`']
        self.assertEqual(len(acceptance), 1)
        self.assertIn('Feature 10 settles what moves', acceptance[0][2])
        text = (REPO / LIST).read_text(encoding='utf-8')
        self.assertEqual(re.findall(r'Feature \d\d', text), ['Feature 09', 'Feature 10'])

    def test_04_B7_the_marked_passage_of_the_client_interface_names_features_05_and_06(self):
        lines = (REPO / CLIENT_INTERFACE).read_text(encoding='utf-8').splitlines()
        passage = ' '.join(lines[lines.index(BEGIN) + 1:lines.index(END)])
        self.assertIn('Features 05 and 06 of the [untangle Run](features/untangle/breakdown.md) replace the case with '
                      'inputs and a mission the client supplies.', passage)
        self.assertEqual(re.findall(r'Features? \d\d(?: and \d\d)?', passage), ['Features 05 and 06'])


if __name__ == '__main__':
    unittest.main()
