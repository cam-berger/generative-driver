"""Firmware bench stands alone: a package of its own that uses the tool as an installed package.

Feature 12 of the untangle Run (docs/features/untangle/12-firmware-bench.md). The benchmark's code is the package
`firmware_bench`, found through the import and never through a path into a checkout, so these tests also pass on a
copy of this folder without its `src/`, against the two installed packages. This folder is `parents[1]` of this
file.

Temporary folders only; no model, no emulator, no hardware and no password.
"""
import argparse
import ast
import contextlib
import hashlib
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from urllib.parse import unquote

import firmware_bench
import generative_driver
import interface_runtime
from tool_fixtures import package_env

BENCH = Path(__file__).resolve().parents[1]
PACKAGE = Path(list(firmware_bench.__path__)[0]).resolve()
TOOL = Path(generative_driver.__file__).resolve().parent
# The module path the benchmark had inside the tool's package, built from its parts so that this file does not
# spell it.
OLD_PATH = 'generative_driver' + '.benchmark'
DOCSTRING = 'Firmware bench: runs Generative Driver on example firmware and evaluates the driver it emits.'
BUILD_FILE = '''\
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "firmware-bench"
version = "0.1.0"
description = "Run Generative Driver on example firmware and evaluate the driver it emits"
readme = "README.md"
requires-python = ">=3.11"
license = "Apache-2.0"
dependencies = ["generative-driver>=0.1.0", "cryptography>=43"]

[project.urls]
Repository = "https://github.com/cam-berger/generative-driver"

[project.scripts]
firmware-bench = "firmware_bench.benchmark:main"

[tool.setuptools]
include-package-data = false

[tool.setuptools.packages.find]
where = ["src"]
namespaces = false

[tool.setuptools.package-data]
firmware_bench = ["resources/**/*"]

[tool.setuptools.exclude-package-data]
"*" = ["**/__pycache__/*", "**/*.pyc", "**/*.pyo"]
'''
# The names that are data or the benchmark's own, which keep the tool's name: each with the module that holds it.
KEPT_NAMES = (
    ('truth.py', "'generative-driver-groundtruth/1'"),
    ('case_assets.py', 'GENERATIVE_DRIVER_BENCH_ROOT'),
    ('case_assets.py', 'GENERATIVE_DRIVER_CASE_ROOT'),
    ('harness.py', 'GD_TEST_HARNESS_FIXTURE'),
    ('suite_reporting.py', 'benchmark_support_tree_sha256'),
)


def texts(folder):
    """The text files under `folder` that a person or Python reads, sorted, without caches."""
    return sorted(path for path in folder.rglob('*')
                  if path.is_file() and path.suffix in ('.py', '.md', '.toml', '.json', '.txt', '.yml')
                  and '__pycache__' not in path.parts)


def child(*words, cwd):
    """A Python child with the two packages on its search path, run to its end."""
    return subprocess.run([sys.executable, *words], cwd=cwd, env={**os.environ, **package_env()},
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=300)


class PackageTests(unittest.TestCase):
    """The parked code is the package `firmware_bench` (12-B1)."""

    def test_12_B1_the_package_holds_one_docstring_and_its_modules_keep_their_names_and_places(self):
        self.assertEqual((PACKAGE / '__init__.py').read_text(encoding='utf-8'), '"""' + DOCSTRING + '"""\n')
        self.assertEqual(firmware_bench.__doc__, DOCSTRING)
        benchmark = importlib.import_module('firmware_bench.benchmark')
        self.assertEqual(Path(benchmark.__file__).resolve(), PACKAGE / 'benchmark.py')
        for name in ('harness', 'registry', 'evaluator', 'suite_book', 'snapshots', 'truth'):
            with self.subTest(module=name):
                module = importlib.import_module('firmware_bench.benchmark_support.' + name)
                self.assertEqual(Path(module.__file__).resolve(), PACKAGE / 'benchmark_support' / (name + '.py'))
        self.assertGreater(len(list((PACKAGE / 'benchmark_support').glob('*.py'))), 55)

    def test_12_B1_the_build_file_holds_the_text_the_spec_gives(self):
        self.assertEqual((BENCH / 'pyproject.toml').read_text(encoding='utf-8'), BUILD_FILE)

    def test_12_B1_no_text_of_the_folder_or_of_the_package_spells_the_old_module_path(self):
        files = [path for path in texts(BENCH) if 'src' not in path.relative_to(BENCH).parts[:1]] + texts(PACKAGE)
        self.assertGreater(len(files), 150)
        found = [f'{path}:{number}' for path in files
                 for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1) if OLD_PATH in line]
        self.assertEqual(len(found), 0, '\n'.join(found[:40]))

    def test_12_B1_the_names_that_are_data_or_the_benchmarks_own_stay(self):
        for module, name in KEPT_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, (PACKAGE / 'benchmark_support' / module).read_text(encoding='utf-8'))
        benchmark = (PACKAGE / 'benchmark.py').read_text(encoding='utf-8')
        self.assertIn("os.environ['GENERATIVE_DRIVER_HOME']", benchmark)
        self.assertIn("prefix='generative-driver-smoke-'", benchmark)

    def test_12_B1_the_benchmarks_own_resources_are_found_from_its_own_folder(self):
        from firmware_bench.benchmark_support import case_assets, generated_sets, registrations
        with patch.dict(os.environ):
            for name in ('GENERATIVE_DRIVER_BENCH_ROOT', 'GENERATIVE_DRIVER_CASE_ROOT'):
                os.environ.pop(name, None)
            self.assertEqual(Path(case_assets.case_root()), PACKAGE / 'resources' / 'bench')
        self.assertEqual(Path(generated_sets._SETS_FILE), PACKAGE / 'resources' / 'registrations' / 'sets.json')
        self.assertTrue((PACKAGE / 'resources' / 'registrations' / 'cases.json').is_file())
        self.assertEqual(len(registrations.registered_case_ids()), 7)
        self.assertFalse((TOOL / 'resources' / 'bench').exists())
        self.assertFalse((TOOL / 'resources' / 'registrations').exists())

    def test_12_B1_the_smoke_case_passes_through_the_module_with_the_two_packages_alone(self):
        with tempfile.TemporaryDirectory(prefix='firmware bench ') as work:
            output = Path(work) / 'smoke output'
            ran = child('-m', 'firmware_bench.benchmark', 'run', '--case', 'setup-smoke', '--output', str(output),
                        cwd=work)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual((report['case'], report['verdict'], report['execution']),
                             ('setup-smoke', 'passed', 'scripted-replay'))
            self.assertEqual(json.loads(ran.stdout)['verdict'], 'passed')


def imports(source):
    """The modules the Python `source` imports at any depth, as (level, dotted name) pairs: `from .a import b` gives
    (1, 'a') and (1, 'a.b'), `from . import b` gives (1, 'b')."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ''
            found.add((node.level, module))
            found.update((node.level, (module + '.' if module else '') + alias.name) for alias in node.names)
    return found


class ToolFolderTests(unittest.TestCase):
    """The benchmark finds the tool's folders through names the interface lists (12-B2)."""

    def test_12_B2_tool_install_gives_the_folder_of_the_tools_package_and_of_its_runtime(self):
        from firmware_bench.benchmark_support import tool_install
        from generative_driver.toolkit import resources_root
        self.assertEqual(tool_install.tool_folder(), resources_root().parent)
        self.assertEqual(tool_install.tool_folder(), TOOL)
        self.assertEqual(tool_install.runtime_folder(), Path(interface_runtime.__file__).resolve().parent)
        self.assertEqual(tool_install.folders(), {'tool': tool_install.tool_folder(),
                                                  'runtime': tool_install.runtime_folder()})
        # It follows the tool wherever the interface says the tool lies, not a path beside the benchmark.
        with patch('generative_driver.toolkit.resources_root', return_value=Path('/elsewhere/tool/resources')):
            self.assertEqual(tool_install.tool_folder(), Path('/elsewhere/tool'))

    def test_12_B2_tool_install_is_no_grading_code_and_snapshots_imports_nothing_of_the_tool(self):
        from firmware_bench.benchmark_support import grader_identity
        closure = grader_identity.grader_closure()
        self.assertGreater(len(closure), 25)
        self.assertNotIn('benchmark_support.tool_install', closure)
        taken = imports((PACKAGE / 'benchmark_support' / 'snapshots.py').read_text(encoding='utf-8'))
        self.assertGreater(len(taken), 8)
        outside = sorted(name for level, name in taken
                         if (not level and name.split('.')[0] in ('generative_driver', 'interface_runtime'))
                         or name.split('.')[-1] == 'tool_install')
        self.assertEqual(outside, [])


class RevisionTests(unittest.TestCase):
    """The revisions cover the tool, its runtime and the benchmark, each folder given or found, never guessed from
    where the benchmark lies (12-B2)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tool, self.runtime = self.root / 'tool', self.root / 'runtime'
        self.skills = self.tool / 'resources' / 'toolchain' / 'skills'
        self.skills.mkdir(parents=True)
        (self.skills / 'skill.md').write_text('a skill', encoding='utf-8')
        (self.tool / 'module.py').write_text('TOOL = 1\n', encoding='utf-8')
        self.runtime.mkdir()
        (self.runtime / 'engine.py').write_text('RUNTIME = 1\n', encoding='utf-8')

    def revisions(self, **folders):
        """The toolchain and the skills revision of a provenance record over the made-up tool and runtime."""
        from firmware_bench.benchmark_support.snapshots import execution_provenance
        record = execution_provenance({}, case_ids=('tq9-v2',),
                                      **{'tool': self.tool, 'runtime': self.runtime, **folders})
        return record['toolchain_revision'], record['skills_revision']

    def test_12_B2_execution_provenance_takes_the_two_folders_from_its_caller_and_has_no_default_for_them(self):
        from firmware_bench.benchmark_support.snapshots import execution_provenance
        parameters = inspect.signature(execution_provenance).parameters
        for name in ('tool', 'runtime'):
            with self.subTest(keyword=name):
                self.assertIs(parameters[name].kind, inspect.Parameter.KEYWORD_ONLY)
                self.assertIs(parameters[name].default, inspect.Parameter.empty)
        with self.assertRaises(TypeError):
            execution_provenance({}, case_ids=('tq9-v2',), tool=self.tool)
        with self.assertRaises(TypeError):
            execution_provenance({}, case_ids=('tq9-v2',), runtime=self.runtime)

    def test_12_B2_the_toolchain_revision_is_the_hash_of_three_tree_hashes_joined(self):
        hashed = []

        def tree(folder, *, exclude=()):
            hashed.append((Path(folder), tuple(exclude)))
            return 'tree of ' + Path(folder).name + ';'
        with patch('firmware_bench.benchmark_support.integrity.tree_hash', tree):
            toolchain, skills = self.revisions()
        self.assertEqual(hashed, [(self.tool, ()), (self.runtime, ()),
                                  (PACKAGE, ('resources/registrations/sets.json',)), (self.skills, ())])
        self.assertEqual(toolchain,
                         hashlib.sha256(b'tree of tool;tree of runtime;tree of firmware_bench;').hexdigest())
        self.assertEqual(skills, 'tree of skills;')

    def test_12_B2_each_revision_covers_the_files_it_names_and_no_others(self):
        before = self.revisions()
        (self.tool / 'module.py').write_text('TOOL = 2\n', encoding='utf-8')
        tool_changed = self.revisions()
        self.assertNotEqual(tool_changed[0], before[0])
        self.assertEqual(tool_changed[1], before[1])
        (self.runtime / 'engine.py').write_text('RUNTIME = 2\n', encoding='utf-8')
        runtime_changed = self.revisions()
        self.assertNotEqual(runtime_changed[0], tool_changed[0])
        self.assertEqual(runtime_changed[1], before[1])
        (self.skills / 'skill.md').write_text('another skill', encoding='utf-8')
        skills_changed = self.revisions()
        self.assertNotEqual(skills_changed[0], runtime_changed[0])
        self.assertNotEqual(skills_changed[1], before[1])
        # The benchmark's own folder is the third tree: a copy of it, changed, moves the revision too.
        from firmware_bench.benchmark_support import snapshots
        copy = self.root / 'firmware_bench'
        shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns('__pycache__'))
        with patch.object(snapshots, '__file__', str(copy / 'benchmark_support' / 'snapshots.py')):
            self.assertEqual(self.revisions(), skills_changed)
            sets = copy / 'resources' / 'registrations' / 'sets.json'
            sets.write_text(sets.read_text(encoding='utf-8') + '\n', encoding='utf-8')
            self.assertEqual(self.revisions(), skills_changed)
            (copy / 'benchmark.py').write_text((copy / 'benchmark.py').read_text(encoding='utf-8') + '\n',
                                               encoding='utf-8')
            self.assertNotEqual(self.revisions()[0], skills_changed[0])

    def test_12_B2_a_revision_is_never_the_hash_of_nothing(self):
        from firmware_bench.benchmark_support import snapshots
        # The name holds characters a pattern reads, as a Windows path holds backslashes: the path is held as text.
        missing = self.root / 'not there (1)+'
        for folders, named in (({'tool': missing}, missing), ({'runtime': missing}, missing)):
            with self.subTest(folders=sorted(folders)):
                with self.assertRaisesRegex(ValueError, '^Revision folder is missing: ' + re.escape(str(named)) + '$'):
                    self.revisions(**folders)
        shutil.rmtree(self.skills)
        with self.assertRaisesRegex(ValueError, '^Revision folder is missing: ' + re.escape(str(self.skills)) + '$'):
            self.revisions()
        self.skills.mkdir()
        with patch.object(snapshots, '__file__', str(missing / 'benchmark_support' / 'snapshots.py')):
            with self.assertRaisesRegex(ValueError, '^Revision folder is missing: ' + re.escape(str(missing)) + '$'):
                self.revisions()


class HarnessChildTests(unittest.TestCase):
    """The harness child is the benchmark's module, with both packages on its search path (12-B2)."""

    def started(self, tool):
        """How `_spawn` starts its child when the tool's package lies at `tool`: the command and the environment."""
        from firmware_bench.benchmark_support import harness
        seen = {}

        class Process:
            def __init__(self, command, **options):
                seen.update(command=command, env=options['env'])
                self.stdin = io.BytesIO()

            def wait(self):
                return 0
        with tempfile.TemporaryDirectory() as directory, patch.object(harness.subprocess, 'Popen', Process), \
                patch('firmware_bench.benchmark_support.tool_install.tool_folder', return_value=tool), \
                patch.dict(os.environ, {'PYTHONPATH': 'an inherited value'}):
            harness._spawn(directory, ['--trial-dir', directory], {'trial_id': 'trial-1'})
            seen['directory'] = directory
        return seen

    def test_12_B2_the_harness_child_is_the_benchmarks_module_with_both_package_folders_on_its_path(self):
        elsewhere = Path(tempfile.gettempdir()).resolve() / 'another place' / 'generative_driver'
        seen = self.started(elsewhere)
        self.assertEqual(seen['command'], [sys.executable, '-m', 'firmware_bench.benchmark_support.harness',
                                           '--trial-dir', seen['directory']])
        # The inherited value is replaced, not added to.
        self.assertEqual(seen['env']['PYTHONPATH'], str(PACKAGE.parent) + os.pathsep + str(elsewhere.parent))

    def test_12_B2_the_folder_stands_once_where_both_packages_lie_in_it(self):
        seen = self.started(PACKAGE.parent / 'generative_driver')
        self.assertEqual(seen['env']['PYTHONPATH'], str(PACKAGE.parent))


class PlacementTests(unittest.TestCase):
    """Evaluator files stay out of the folders that hold the two packages and out of a checkout that holds either
    (12-B2). The layout is made up in a temporary folder, so the test says the same in a checkout and installed."""

    AUTHORING = 'Evaluator directory must be outside repository, installed package and known candidate roots'
    OUTPUT = 'Evaluator output must be outside the repository, installed package and candidate resources'

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        # The benchmark installed in one folder, the tool in a checkout of its own.
        self.bench_holder = self.root / 'site packages'
        self.checkout = self.root / 'tool checkout'
        self.tool_holder = self.checkout / 'src'
        for folder in (self.bench_holder / 'firmware_bench' / 'benchmark_support',
                       self.tool_holder / 'generative_driver', self.checkout / '.git', self.root / 'home'):
            folder.mkdir(parents=True)
        self.inside = (self.bench_holder / 'beside the benchmark', self.bench_holder / 'firmware_bench' / 'out',
                       self.tool_holder / 'beside the tool', self.tool_holder / 'generative_driver' / 'out',
                       self.checkout / 'docs' / 'out')
        self.outside = self.root / 'private evaluator'

    def laid_out(self, module):
        """Patches under which `module` lies in the made-up layout, with the tool beside it in its checkout."""
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(
            module, '__file__', str(self.bench_holder / 'firmware_bench' / 'benchmark_support' / 'module.py')))
        stack.enter_context(patch('firmware_bench.benchmark_support.tool_install.tool_folder',
                                  return_value=self.tool_holder / 'generative_driver'))
        stack.enter_context(patch.dict(os.environ, {'GENERATIVE_DRIVER_HOME': str(self.root / 'home')}))
        return stack

    def test_12_B2_an_evaluator_directory_is_refused_inside_either_packages_folder_or_checkout(self):
        from firmware_bench.benchmark_support import authoring
        with self.laid_out(authoring):
            for folder in self.inside:
                with self.subTest(folder=folder.relative_to(self.root).as_posix()):
                    with self.assertRaises(ValueError) as refusal:
                        authoring.evaluator_directory(str(folder))
                    self.assertEqual(str(refusal.exception), self.AUTHORING)
            self.assertEqual(authoring.evaluator_directory(str(self.outside)), self.outside)

    def test_12_B2_evaluator_output_is_refused_inside_either_packages_folder_or_checkout(self):
        from firmware_bench.benchmark_support import authoring, evaluator_cli
        truth = {'source_files': {}}
        with self.laid_out(evaluator_cli), self.laid_out(authoring), \
                patch('firmware_bench.benchmark_support.emulator.truth_for_case', return_value=truth):
            for folder in self.inside:
                with self.subTest(folder=folder.relative_to(self.root).as_posix()):
                    with self.assertRaises(ValueError) as refusal:
                        evaluator_cli.manage('unlock', 'tq9-v2', None, str(folder))
                    self.assertEqual(str(refusal.exception), self.OUTPUT)
                    self.assertFalse(folder.exists())
            unlocked = evaluator_cli.manage('unlock', 'tq9-v2', None, str(self.outside))
        self.assertEqual(unlocked['evidence'], str(self.outside / 'evidence.json'))


class TestFolderTests(unittest.TestCase):
    """The benchmark's tests run from their own folder against installed packages (12-B4)."""

    CASES = ['setup-smoke', 'tq9-v2', 'tq9-v3', 'sampled-sensor-v1', 'parameter-store-v1', 'zephyr-modbus-v1',
             'generated']

    def test_12_B4_a_child_with_the_environment_of_package_env_alone_imports_both_packages_and_lists_the_cases(self):
        script = ('import json, firmware_bench, generative_driver\n'
                  'from firmware_bench.benchmark_support.registrations import registered_case_ids\n'
                  'print(json.dumps({"benchmark": firmware_bench.__file__, "tool": generative_driver.__file__,\n'
                  '                  "cases": list(registered_case_ids())}))\n')
        with tempfile.TemporaryDirectory() as work, patch.dict(os.environ):
            # Nothing else is on the child's search path: no inherited value, and a working folder that is empty.
            os.environ.pop('PYTHONPATH', None)
            ran = child('-c', script, cwd=work)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        found = json.loads(ran.stdout)
        self.assertEqual(Path(found['benchmark']).resolve(), Path(firmware_bench.__file__).resolve())
        self.assertEqual(Path(found['tool']).resolve(), Path(generative_driver.__file__).resolve())
        self.assertEqual(found['cases'], self.CASES)


class DroppedCasesTests(unittest.TestCase):
    """12-B3: the code, resources and texts of the two dropped cases, the old `tq9` and the physical one, are gone."""

    def said(self, *words):
        """What `firmware-bench` prints for `words`, with every run of white space as one space."""
        from firmware_bench.benchmark import main
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), contextlib.suppress(SystemExit):
            main(list(words))
        return ' '.join(printed.getvalue().split())

    def test_12_B3_the_modules_and_resources_of_the_two_dropped_cases_are_gone(self):
        support, bench = PACKAGE / 'benchmark_support', PACKAGE / 'resources' / 'bench'
        dropped = [support / 'cases.py', support / 'physical.py', support / 'recorded_grading.py',
                   support / 'tq9_legacy.py', bench / 'cases' / 'tq9', bench / 'cases' / 'bme280',
                   bench / 'groundtruth' / 'tq9.enc', bench / 'groundtruth' / 'bme280.enc']
        self.assertEqual([path.relative_to(PACKAGE).as_posix() for path in dropped if path.exists()], [])

    def test_12_B3_the_modules_that_stay_lost_what_only_the_dropped_cases_used(self):
        from firmware_bench.benchmark_support import case_assets, mcp_reuse
        left = [module.__name__.rpartition('.')[2] + '.' + name
                for module, name in ((case_assets, 'REUSE_EVENTS'), (case_assets, 'TASKS'),
                                     (case_assets, 'mapping_errors'), (mcp_reuse, 'collect'),
                                     (mcp_reuse, 'PROOF_FIELDS'))
                if hasattr(module, name)]
        self.assertEqual(left, [])

    def test_12_B3_the_benchmarks_code_names_no_dropped_case(self):
        """The old case's name stands only where `suite_reporting.py` reads an earlier report format."""
        named = []
        for path in sorted(PACKAGE.rglob('*.py')):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if ('bme280' in line.lower() or 'legacy tq9' in line.lower() or "'tq9'" in line or '"tq9"' in line
                        or 'tq9/emulator' in line or 'physical_grounding' in line or 'physical_final' in line):
                    named.append(path.relative_to(PACKAGE).as_posix() + ': ' + line.strip())
        self.assertEqual(named, [
            "benchmark_support/suite_reporting.py: if report.get('schema')=='benchmark-report/1':"
            "return report.get('case')=='tq9'",
            "benchmark_support/suite_reporting.py: if named.get('case')!='tq9' or (named.get('case_pin') or {})"
            ".get('evaluator_version')!='2':return False"])

    def test_12_B3_the_help_of_run_and_of_score_names_no_dropped_case(self):
        run = self.said('run', '--help')
        self.assertIn('--approve-bound-device-tools Explicitly authorize assigned tools for the selected physical '
                      'binding and effects', run)
        self.assertIn('--approve-emulator-tools Explicitly authorize scoped emulator MCP tools; no physical device '
                      'approval', run)
        self.assertIn('--binding-json', run)
        self.assertIn('--evidence EVIDENCE V2 sealed evidence (digest checked)', self.said('score', '--help'))

    def test_12_B3_a_scoped_approval_of_a_case_without_that_scope_is_refused_by_the_scope_alone(self):
        from firmware_bench.benchmark import run
        for approval in ('emulator', 'bound-device'):
            with self.subTest(approval=approval), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError) as refusal:
                    run('setup-smoke', folder, options={'scoped_tool_approval': approval})
                self.assertEqual(str(refusal.exception), 'Scoped approval requires a case with that approval scope')

    def test_12_B3_a_suite_takes_only_a_passed_reference_calibration_whatever_the_case_is_called(self):
        from types import SimpleNamespace
        from firmware_bench.benchmark_support.suites import preflight_selection
        manifest = {'schema': 'benchmark-suite/1', 'id': 'toy', 'version': '1', 'repetitions': 1,
                    'child_budget_seconds': 60, 'suite_budget_seconds': 60, 'max_active_children': 1,
                    'entries': [{'case': 'tq9', 'scenario': 'original', 'case_seed': 0}]}
        definition = SimpleNamespace(id='tq9', execution='actual-agent-emulation', evidence_track='emulation')
        for status in ('legacy-not-required', 'pending'):
            pin = {'calibration': {'status': status}, 'scope': 'full-workflow'}
            with self.subTest(status=status), \
                    patch('firmware_bench.benchmark_support.registry.resolve_case', return_value=definition), \
                    patch('firmware_bench.benchmark_support.registry.pin_case', return_value=pin):
                with self.assertRaises(ValueError) as refusal:
                    preflight_selection(manifest)
                self.assertEqual(str(refusal.exception),
                                 'Suite contains a case without required reference calibration')
        pin = {'calibration': {'status': 'passed'}, 'scope': 'full-workflow'}
        with patch('firmware_bench.benchmark_support.registry.resolve_case', return_value=definition), \
                patch('firmware_bench.benchmark_support.registry.pin_case', return_value=pin):
            self.assertEqual(preflight_selection(manifest)[2], [pin])

    def test_12_B3_the_smokes_regrade_is_what_the_legacy_module_keeps(self):
        from firmware_bench.benchmark_support import legacy
        self.assertEqual(legacy.score({'case': 'setup-smoke', 'observations': {'temperature': 21.5}})['verdict'],
                         'passed')
        self.assertEqual({'case_root', 'json', 'Path'} & set(vars(legacy)), set())


# What a document of the tool is linked by: its path in the tool's repository follows.
TOOL_FILES = 'https://github.com/cam-berger/generative-driver/blob/main/'
# The given texts below are held word for word. Where a source line ends in a backslash, the text goes on in the next
# line without a break: a line of a document is longer than a line of this file may be.
OPENING = '''\
# Firmware bench

Firmware bench measures how well [Generative Driver](https://github.com/cam-berger/generative-driver) recovers a \
working driver from a device's firmware. It supplies a device, a firmware image and a mission to the tool through the \
tool's client interface, and grades the frozen driver once, after the run. The tool holds nothing of the benchmark.

| To | Read |
|---|---|
| Hold the answer keys and qualify the graders | [Evaluator's guide](docs/evaluator.md) |
| Add a case | [Case authoring](docs/case-authoring.md) · [Upstream corpus](docs/corpus.md) · [Generated-device \
interface](docs/generated-device-interface.md) |
| Accept a model lane | [Codex acceptance](docs/codex-acceptance.md) |
| Look up a term | [Glossary](CONTEXT.md) |
| Follow a decision | [Decision records](docs/adr/) |
'''
NEW_ROWS = (
    '| `tq9-v3` | Registered with its public mission. Its firmware and answer key are not in this repository yet, '
    'so it lists as `pending` |',
    '| `generated` | A device of a generated set. No set is registered, so it lists as `pending` |')
BELOW_THE_CASES = (
    'Only `setup-smoke` runs with nothing but the two packages. It checks an installation: it validates a model, '
    'emits a package and replays a recorded reply. It starts no service, worker or grader. Every other case needs '
    'its evaluator password, the emulator, a qualification under the current grader, and a model or a saved '
    'candidate.')
INSTALL = '''\
## Install and check

```sh
python -m pip install "generative-driver @ git+https://github.com/cam-berger/generative-driver"
python -m pip install -e .
firmware-bench cases
firmware-bench run --case setup-smoke --output "smoke output"
```

While this folder lies in the tool's repository, install the tool from the checkout instead: `python -m pip install -e \
..`. Set up the tool's service and Codex as its \
[setup](https://github.com/cam-berger/generative-driver/blob/main/docs/setup.md) and \
[Codex](https://github.com/cam-berger/generative-driver/blob/main/docs/codex.md) guides say. Run the tests with \
`python -m unittest discover -s tests`. A graded run also needs Renode, at the release this page pins, and for \
building firmware an Arm compiler.
'''
GLOSSARY_HEAD = '''\
# Domain of the benchmark

The terms of firmware bench. The terms of the tool it evaluates, such as configurator, stage, handoff, package and \
fresh reuse, are in the tool's [CONTEXT.md](https://github.com/cam-berger/generative-driver/blob/main/CONTEXT.md).

| Term | Meaning |
|---|---|
'''
GLOSSARY_TAIL = '''\

What the benchmark adds to four of the tool's terms:

| Term | The benchmark's part |
|---|---|
| Equivalence | For a benchmark run the final verdict is the evaluator's grade. |
| Effect grant | The grader grants each hidden step's effects. |
| Instrument observation | Generated devices offer none for now: workers see only their UART. |
| Public mission | Hidden vectors remain evaluator-owned. A generated mission states only each task's effect; ranges, \
refusal conditions, framing and the mapping onto firmware commands stay hidden. |

The pass rate scores safety and integrity stops, like a spent budget, as failures, and other operator stops and host \
stops as faults.
'''
# The thirty-one terms of the first table, in order, and the SHA-256 of its rows as UTF-8: the rows of the glossary
# this one was written from, unchanged but for the second sentence of `Device pin`.
GLOSSARY_TERMS = (
    'Groundtruth', 'Bench harness', 'Evaluator password', 'Unlocked truth', 'Lineage', 'Case', 'Device pin',
    'Boot reading', 'Rerun', 'Execution setup', 'Trial', 'Pass / failure', 'Unknown', 'Pass rate', 'Scorecard',
    'Scoring set / development set', 'Pinned system', 'Baseline', 'Hidden sequence / step', 'Framing',
    'Answer vault / generator volume', 'Device generator', 'Generated-device interface', 'Family hook',
    'Difficulty level', 'Comparison', 'Anchor', 'Leak check', 'Selection', 'Availability / admission',
    'Reference driver / mutant')
GLOSSARY_ROWS_SHA256 = '117595c73af932db04918b8ece9fdaa10da8bd6d8ea4053cc6e79a274ca27da6'
RULES = '''\
# Firmware bench

Before implementation, read [CONTEXT.md](CONTEXT.md) and the [front page](README.md).

- Develop through one public seam, the command `firmware-bench`: run, score, compare. For each code change, observe \
one behavior test fail, implement it, then observe it pass.
- Use the tool only as an installed package, through its client interface. The benchmark's code imports no other name \
of it.
- The bench harness holds evaluator passwords; the configurator and workers receive neither passwords nor their paths \
or plaintext answers. Describe password separation accurately; hashes are not a sandbox.
- Keep worker claims, accepted handoffs and evaluator verdicts separate. Score observable outputs/effects, not \
candidate source-code equality.
- Distinguish model runs, scripted tests, replay, emulation and physical evidence. Launch model baselines or physical \
operations only when explicitly assigned by the primary agent.
- Keep docs concise, concrete and executable. Preserve notices.
- Track open issues as local Markdown under `.scratch/<feature>/`.
'''
NOTICE = '''\
Firmware bench
Copyright 2026 Generative Driver contributors

Third-party materials retain their original notices and licenses. See
docs/evaluator.md and the notices beside each case for the provenance of
emulator models. Renode, Zephyr and compilers are separately distributed
under their respective licenses.
'''
ATTRIBUTES = '''\
# Hashes of source and resource bytes must be the same on every host.
* text=auto eol=lf
*.bin -text
*.enc -text
'''
IGNORED = '''\
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
*.egg-info/
dist/
build/
.DS_Store
.env
*.password
*.secret
runs/
groundtruth/plaintext/
'''
# The lines of the benchmark's standards that differ from the tool's, each with its number, and the SHA-256 of the
# whole file as UTF-8.
STANDARDS_LINES = {
    12: '- Grading code imports nothing from the system under test, the bench harness or the reporting code, so a '
        'grader identity covers grading code only and changing anything else keeps a qualification. '
        '`tests/test_grader_identity.py` checks the boundary.',
    13: '- Evaluator passwords stay in the bench harness. The configurator and workers never receive a password, its '
        'path or a plaintext answer.',
    27: 'Each test module runs in its own process, eight at a time, because the serial suite takes about 13 minutes '
        'and the Done check stops at 9. A worktree links `.venv` to the main checkout\'s environment.',
    29: 'Test command: `ls tests/test_*.py | xargs -P 8 -n 1 sh -c \'out=$(.venv/bin/python -m unittest discover -v '
        '-s "$(dirname "$0")" -p "$(basename "$0")" 2>&1); s=$?; printf "%s\\n" "$out"; exit $s\'`',
    31: 'Test files: `tests/test_*.py`',
    38: '- A test that starts a Python child gives it `tests/tool_fixtures.py`\'s environment. MCP stdio children '
        'drop `PYTHONPATH`, so without it the child silently runs another checkout\'s code.',
    39: '- Tests use temporary homes. They never touch a real password, vault, device or model run.',
}
STANDARDS_SHA256 = '66eb0d236425748dc9f0d463f4b9ccd7fa76136ebedfca08e257dfb71d129d43'
# What no document outside the notes holds, in any letter case (12-B5), and the places documents had before this
# folder was a package of its own, which nothing under this folder names any more (rule 1 of the Spec's documents).
DROPPED_WORDS = ('bme280', 'legacy tq9', 'docs/verification.md', 'bench/evidence', 'benchmark-deletion-list')
OLD_PLACES = re.compile(r'benchmark-case-authoring\.md|benchmark-corpus\.md|(?<!firmware-)bench/README'
                        r'|groundtruth/README'
                        r'|src/generative_driver/(?:benchmark|resources/bench|resources/registrations)')
FROM_THE_ROOT = re.compile(r'firmware-bench/(?:tests|src|docs|\.scratch|README|CONTEXT|AGENTS|STANDARDS)')
LINK = re.compile(r'\[[^\]]*\]\(([^()\s]+)\)')
HEADING_LINK = re.compile(r'\[([^\]]*)\]\([^)]*\)')
WORKFLOW = '''\
name: Firmware bench
on:
  push:
  pull_request:
permissions:
  contents: read
jobs:
  installed:
    timeout-minutes: 150
    strategy:
      fail-fast: false
      matrix:
        os: [macos-latest, windows-latest]
        python: ['3.11', '3.13']
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-python@v7
        with:
          python-version: ${{ matrix.python }}
      - name: Build both distributions
        run: |
          python -m pip install build
          python -m build
          python -m build firmware-bench
      - name: Install both wheels
        shell: python
        run: |
          import glob, subprocess, sys
          tool, = glob.glob('dist/*.whl')
          bench, = glob.glob('firmware-bench/dist/*.whl')
          subprocess.run([sys.executable, '-m', 'pip', 'install', tool, bench], check=True)
      - name: Test on a copy outside the checkout
        shell: python
        run: |
          from pathlib import Path
          import glob, os, shutil, subprocess, sys, tempfile
          repo = Path.cwd()
          with tempfile.TemporaryDirectory(prefix='firmware bench ') as work:
              copy = Path(work) / 'firmware-bench'
              shutil.copytree(repo / 'firmware-bench', copy, ignore=shutil.ignore_patterns('src', 'dist', 'build', \
'*.egg-info', '__pycache__'))
              env = dict(os.environ)
              env.pop('PYTHONPATH', None)
              env['FB_TEST_WHEEL'] = str(Path(glob.glob('firmware-bench/dist/*.whl')[0]).resolve())
              subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(copy / 'tests'), '-v'], \
cwd=work, env=env, check=True)
              subprocess.run([sys.executable, '-m', 'firmware_bench.benchmark', 'run', '--case', 'setup-smoke', \
'--output', str(Path(work) / 'smoke output')], cwd=work, env=env, check=True)
'''


def documents(notes=True):
    """The Markdown documents under this folder, sorted: the front page, the guides, the records, the notes under
    `.scratch/` unless `notes` is false, and those among the package's resources where `src/` lies here."""
    left_out = {'dist', 'build', '__pycache__', '.venv', 'runs'} | (set() if notes else {'.scratch'})
    return sorted(path for path in BENCH.rglob('*.md')
                  if not left_out & set(path.relative_to(BENCH).parts)
                  and not any(part.endswith('.egg-info') for part in path.parts))


def prose(text):
    """(line number, line) of each line of the Markdown `text` outside its fenced code blocks."""
    fenced = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        elif not fenced:
            yield number, line


def anchors(text):
    """The anchors of the headings of the Markdown `text`, as GitHub names them."""
    found = set()
    for _, line in prose(text):
        if line.startswith('#'):
            title = HEADING_LINK.sub(r'\1', line.lstrip('#').strip()).lower()
            found.add(re.sub(r'[^\w\- ]', '', title).replace(' ', '-'))
    return found


def link_problems(document, text):
    """What is wrong with the links of the document at `document`, whose text is `text`: a relative link that leaves
    this folder or leads to nothing, and an anchor that its target does not have. A link under `src/firmware_bench/`
    is resolved against the imported package where `src/` is not beside the documents."""
    problems = []
    for number, line in prose(text):
        for target in LINK.findall(line):
            if re.match(r'[a-z][a-z0-9+.-]*:', target):
                continue
            where, _, anchor = target.partition('#')
            path = Path(os.path.normpath(document.parent / unquote(where))) if where else document
            if not path.is_relative_to(BENCH):
                problems.append(f'{number}: {target} leaves the folder')
                continue
            parts = path.relative_to(BENCH).parts
            if parts[:2] == ('src', 'firmware_bench') and not (BENCH / 'src').is_dir():
                path = PACKAGE.joinpath(*parts[2:])
            if not path.exists():
                problems.append(f'{number}: {target} leads to nothing')
            elif anchor and path.suffix == '.md':
                if anchor not in anchors(path.read_text(encoding='utf-8') if where else text):
                    problems.append(f'{number}: {target} names no heading')
    return problems


def commands(text):
    """(line number, command) of each command line in the fenced code blocks of the Markdown `text`, with its
    continuation lines joined."""
    fenced, joined, first = False, '', 0
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith('```'):
            fenced, joined = not fenced, ''
            continue
        if not fenced:
            continue
        first = first if joined else number
        joined += line.strip()
        if joined.endswith('\\'):
            joined = joined[:-1] + ' '
            continue
        if joined:
            yield first, joined
        joined = ''


def arguments(command):
    """The arguments of `command` where its first word is `firmware-bench`, else None: a leading variable assignment
    is left out, and so is each part in square brackets with the dots after it."""
    command = re.sub(r'^(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)+', '', command)
    if command.split()[:1] != ['firmware-bench']:
        return None
    return shlex.split(re.sub(r'\s*\[[^\]]*\](?:\.\.\.)?', '', command))[1:]


class _Parsed(Exception):
    """The parser of `firmware-bench` accepted its arguments; nothing was run."""


def refusal(argv):
    """What the parser of `firmware-bench` says against `argv`, or None where it accepts them. Nothing is run: the
    command stops right after its arguments are parsed."""
    from firmware_bench import benchmark
    parse = argparse.ArgumentParser.parse_args

    def stop(self, args=None, namespace=None):
        parse(self, args, namespace)
        raise _Parsed()
    said = io.StringIO()
    with patch.object(argparse.ArgumentParser, 'parse_args', stop), contextlib.redirect_stderr(said), \
            contextlib.redirect_stdout(io.StringIO()):
        try:
            benchmark.main(list(argv))
        except _Parsed:
            return None
        except SystemExit:
            return (said.getvalue().strip().splitlines() or ['refused'])[-1]
    raise AssertionError('The command ran: ' + ' '.join(argv))


class DocumentTests(unittest.TestCase):
    """The benchmark's documents describe the benchmark as it is, from its own folder (12-B5)."""

    def read(self, name):
        return (BENCH / name).read_text(encoding='utf-8')

    def test_12_B5_every_relative_link_of_a_document_resolves_inside_this_folder(self):
        found = documents()
        for name in ('README.md', 'CONTEXT.md', 'AGENTS.md', 'STANDARDS.md', 'docs/evaluator.md',
                     'docs/case-authoring.md', 'docs/corpus.md', 'docs/generated-device-interface.md',
                     'docs/codex-acceptance.md', 'docs/adr/0001-benchmark-registration-and-selection.md',
                     '.scratch/benchmark-review/spec.md'):
            self.assertIn(BENCH / name, found)
        problems = {path.relative_to(BENCH).as_posix(): link_problems(path, path.read_text(encoding='utf-8'))
                    for path in found}
        self.assertEqual({name: wrong for name, wrong in problems.items() if wrong}, {})

    def test_12_B5_the_link_check_sees_a_link_that_leaves_the_folder_a_dead_one_and_a_missing_heading(self):
        page = BENCH / 'docs' / 'evaluator.md'
        for text, expected in (
                ('See [design](../../docs/design.md).', ['1: ../../docs/design.md leaves the folder']),
                ('See [it](no-such-file.md) and [that](../README.md#no-such-heading).',
                 ['1: no-such-file.md leads to nothing', '1: ../README.md#no-such-heading names no heading']),
                ('See [the page](../README.md#install-and-check), [here](#x) and [the tool](' + TOOL_FILES
                 + 'docs/design.md).\n\n# X\n', []),
                ('```sh\necho "[not a link](../../outside.md)"\n```\n', [])):
            with self.subTest(text=text):
                self.assertEqual(link_problems(page, text), expected)
        self.assertEqual(anchors('# Native Modbus pilot — 2026-10-27\n## The `tq9-v2` [case](x.md)\n'),
                         {'native-modbus-pilot--2026-10-27', 'the-tq9-v2-case'})

    def test_12_B5_a_link_to_a_file_of_the_tool_names_a_file_of_the_tools_repository(self):
        tool = BENCH.parent
        if not (tool / 'src' / 'generative_driver').is_dir():
            self.skipTest(f'This folder does not lie in the tool\'s repository: {tool}')
        missing, linked = [], 0
        for path in documents():
            for number, line in prose(path.read_text(encoding='utf-8')):
                for target in LINK.findall(line):
                    if target.startswith(TOOL_FILES):
                        linked += 1
                        if not (tool / unquote(target[len(TOOL_FILES):].partition('#')[0])).exists():
                            missing.append(f'{path.relative_to(BENCH).as_posix()}:{number}: {target}')
        self.assertGreater(linked, 3)
        self.assertEqual(missing, [])

    def test_12_B5_every_firmware_bench_command_of_a_code_block_is_accepted_by_the_parser(self):
        refused, seen = [], 0
        for path in documents():
            for number, command in commands(path.read_text(encoding='utf-8')):
                argv = arguments(command)
                if argv is not None:
                    seen += 1
                    said = refusal(argv)
                    if said:
                        refused.append(f'{path.relative_to(BENCH).as_posix()}:{number}: {command}: {said}')
        self.assertGreater(seen, 30)
        self.assertEqual(refused, [])

    def test_12_B5_the_command_check_reads_a_command_as_the_spec_says_and_sees_a_refused_one(self):
        text = ('Run `firmware-bench no-such-command` in prose.\n```sh\n'
                'GENERATIVE_DRIVER_HOME=/state firmware-bench cases\n'
                'firmware-bench --home /operator/state suite report SUITE_ID \\\n'
                '    --output "reports/pilot 1" [--audit-path PATH]...\n'
                'generative-driver doctor\n'
                'firmware-bench truth unlock --case tq9 --output out\n```\n')
        found = [(number, arguments(command)) for number, command in commands(text)]
        self.assertEqual(found, [
            (3, ['cases']),
            (4, ['--home', '/operator/state', 'suite', 'report', 'SUITE_ID', '--output', 'reports/pilot 1']),
            (6, None),
            (7, ['truth', 'unlock', '--case', 'tq9', '--output', 'out'])])
        self.assertEqual([bool(refusal(argv)) for _, argv in found if argv is not None], [False, False, True])
        self.assertIn("invalid choice: 'tq9'", refusal(found[-1][1]))

    def test_12_B5_no_document_outside_the_notes_names_a_dropped_case_or_a_deleted_record(self):
        found = []
        for path in documents(notes=False):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                low = line.lower()
                found += [f'{path.relative_to(BENCH).as_posix()}:{number}: {word}' for word in DROPPED_WORDS
                          if word in low]
                if re.search(r'--case tq9(?!-v)', low):
                    found.append(f'{path.relative_to(BENCH).as_posix()}:{number}: --case tq9')
        self.assertEqual(found, [])

    def test_12_B5_a_document_is_named_by_its_place_of_today_and_a_path_is_written_from_this_folder(self):
        found = []
        for path in texts(BENCH) + texts(PACKAGE):
            if path == Path(__file__).resolve():
                continue
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                found += [f'{path.name}:{number}: {place}' for place in OLD_PLACES.findall(line)]
                if path.suffix == '.md' and FROM_THE_ROOT.search(line.replace(TOOL_FILES, '')):
                    found.append(f'{path.name}:{number}: a path written from the repository')
        self.assertEqual(sorted(set(found)), [])

    def test_12_B5_the_front_page_opens_with_the_text_the_spec_gives(self):
        from firmware_bench.benchmark_support.registrations import registered_case_ids
        page = self.read('README.md')
        self.assertTrue(page.startswith(OPENING), page[:400])
        profiles = page[page.index('\n## Profiles\n'):page.index('\n## Install and check\n')]
        rows = [line for line in profiles.splitlines() if line.startswith('| `')]
        self.assertEqual(profiles.splitlines()[3:5], ['| Case | Purpose |', '|---|---|'])
        self.assertEqual([row.split('`')[1] for row in rows], list(registered_case_ids()))
        for row in NEW_ROWS:
            self.assertIn(row, rows)
        self.assertIn('\n\n' + BELOW_THE_CASES + '\n\n', profiles)
        self.assertLess(profiles.index(rows[-1]), profiles.index(BELOW_THE_CASES))
        self.assertIn('\n' + INSTALL + '\n', page)

    def test_12_B5_the_glossary_holds_the_benchmarks_terms_in_the_frame_the_spec_gives(self):
        glossary = self.read('CONTEXT.md')
        self.assertTrue(glossary.startswith(GLOSSARY_HEAD), glossary[:300])
        self.assertTrue(glossary.endswith(GLOSSARY_TAIL), glossary[-900:])
        rows = glossary[len(GLOSSARY_HEAD):len(glossary) - len(GLOSSARY_TAIL)]
        self.assertEqual(tuple(row.split(' | ')[0][2:] for row in rows.splitlines()), GLOSSARY_TERMS)
        self.assertIn('. The bench harness builds it and freezes it with the trial\'s snapshot. ', rows)
        self.assertEqual(hashlib.sha256(rows.encode()).hexdigest(), GLOSSARY_ROWS_SHA256)

    def test_12_B5_the_rules_the_standards_and_the_notice_hold_what_the_spec_gives(self):
        self.assertEqual(self.read('AGENTS.md'), RULES)
        self.assertEqual(self.read('NOTICE'), NOTICE)
        standards = self.read('STANDARDS.md')
        lines = standards.splitlines()
        self.assertEqual({number: lines[number - 1] for number in STANDARDS_LINES}, STANDARDS_LINES)
        self.assertEqual(len(lines), 45)
        self.assertNotIn('firmware-bench', standards)
        self.assertEqual(standards.count('PYTHONPATH'), 1)
        self.assertEqual(hashlib.sha256(standards.encode()).hexdigest(), STANDARDS_SHA256)

    def test_12_B5_the_licence_the_attributes_and_the_ignored_names_are_the_folders_own(self):
        licence = (BENCH / 'LICENSE').read_bytes()
        self.assertIn(b'Apache License', licence[:120])
        self.assertIn(b'Version 2.0, January 2004', licence[:200])
        self.assertEqual(self.read('.gitattributes'), ATTRIBUTES)
        self.assertEqual(self.read('.gitignore'), IGNORED)
        for name in ('LICENSE', '.gitattributes'):
            original = BENCH.parent / name
            if (BENCH.parent / 'src' / 'generative_driver').is_dir():
                with self.subTest(copy=name):
                    self.assertEqual((BENCH / name).read_bytes(), original.read_bytes())


class RecordTests(unittest.TestCase):
    """The benchmark's outdated records and notes are deleted, and its open issues stay (12-B6)."""

    GONE = ('docs/adr/0004-compare-systems-through-a-scoring-set.md', '.scratch/benchmark-baseline',
            '.scratch/benchmark-review/bench-harness-noticed.md',
            '.scratch/benchmark-review/issues/12-one-case-gets-extra-help.md')
    STAY = ('.scratch/baseline/issues/01-open-steps-of-the-baseline.md', '.scratch/benchmark-review/spec.md',
            '.scratch/benchmark-review/issues/01-no-held-out-result.md',
            '.scratch/benchmark-review/issues/02-pass-rate-leaves-out-crashes.md',
            '.scratch/benchmark-review/issues/03-no-intervals-or-variance.md',
            '.scratch/benchmark-review/issues/04-emulator-monitor-port.md',
            '.scratch/benchmark-review/issues/05-audit-reads-nothing-as-clean.md',
            '.scratch/benchmark-review/issues/06-earlier-probes-stay-readable.md',
            '.scratch/benchmark-review/issues/07-any-byte-voids-qualification.md',
            '.scratch/benchmark-review/issues/08-no-mutant-tests-refusals.md',
            '.scratch/benchmark-review/issues/09-fresh-reuse-unjudged.md',
            '.scratch/benchmark-review/issues/10-harness-size-and-operation.md',
            '.scratch/benchmark-review/issues/11-calibration-staleness-is-hidden.md',
            '.scratch/benchmark-review/issues/13-kit-changes-not-adopted.md',
            '.scratch/benchmark-lifecycle/issues/02-structured-native-monitor.md',
            '.scratch/benchmark-lifecycle/issues/03-expected-refusal-repair.md',
            '.scratch/test-noise/issues/01-quiet-test-noise.md',
            'docs/adr/0001-benchmark-registration-and-selection.md',
            'docs/adr/0003-generated-devices-register-by-set.md',
            'docs/adr/0005-keep-the-device-generator-private.md', 'docs/adr/0006-seal-graded-scoring-trials.md',
            'docs/adr/0007-one-validator-defines-a-generated-device.md')
    # The open steps of the baseline's plan, word for word, as its deleted spec numbered them.
    OPEN_STEPS = (
        '6. Private generator in its own encrypted volume (16, 22): three framings, five levels, effects-only '
        'missions, hidden sequences, two references and about five mutants per device, built through the existing '
        'Arm GNU Toolchain and Renode path.',
        '7. Development set: generate it; the user qualifies it; the pilot\'s 20 trials run once the user assigns '
        'them; adjust the ladder (23).',
        '8. Pin the system (6); the user requalifies the anchor.',
        '9. Scoring set: generate it; the user qualifies it; the baseline\'s about 150 scoring and 12 anchor trials '
        'run once the user assigns them, within the 350M-token ceiling (23).',
        '10. Store the baseline and publish its aggregate scorecard.')

    def test_12_B6_the_outdated_records_and_notes_are_gone_and_the_open_issues_stay(self):
        self.assertEqual([name for name in self.GONE if (BENCH / name).exists()], [])
        self.assertEqual([name for name in self.STAY if not (BENCH / name).is_file()], [])

    def test_12_B6_the_open_steps_of_the_baseline_are_kept_word_for_word_as_an_issue_to_triage(self):
        lines = (BENCH / self.STAY[0]).read_text(encoding='utf-8').splitlines()
        self.assertTrue(lines[0].startswith('# '), lines[0])
        self.assertEqual(lines[1:4], ['', 'Status: needs-triage', ''])
        self.assertEqual(tuple(line for line in lines if re.match(r'\d+\. ', line)), self.OPEN_STEPS)

    def test_12_B6_the_review_lost_its_section_on_the_seam_and_its_row_for_the_deleted_issue(self):
        review = (BENCH / '.scratch/benchmark-review/spec.md').read_text(encoding='utf-8')
        self.assertNotIn('The seam with the kit', review)
        self.assertEqual(re.findall(r'^\| \[(\d\d)\]', review, flags=re.M),
                         ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11', '13'])
        for kept in ('## Considerations', '## What the benchmark showed about the kit',
                     '## Already recorded elsewhere'):
            self.assertIn(kept, review)

    def test_12_B6_the_benchmarks_note_on_test_noise_holds_the_item_on_its_own_code(self):
        note = (BENCH / '.scratch/test-noise/issues/01-quiet-test-noise.md').read_text(encoding='utf-8')
        self.assertEqual(note.splitlines()[:3], ['# Quiet the test suite\'s recurring noise', '',
                                                'Status: needs-triage'])
        self.assertIn('`upstream_qualification.qualify`', note)
        for of_the_tool in ('probe.py', 'test_device_sessions.py', 'ConnectionResetError'):
            self.assertNotIn(of_the_tool, note)


class HostedWorkflowTests(unittest.TestCase):
    """The hosted tests run the benchmark (12-B8), and their job ends after 150 minutes (12-B10)."""

    def setUp(self):
        self.path = BENCH.parent / '.github' / 'workflows' / 'firmware-bench.yml'
        if not (BENCH.parent / 'src' / 'generative_driver').is_dir():
            self.skipTest(f'The workflow file is not two folders above the tests: {self.path}')

    def last_step(self):
        """The Python script of the workflow's last step."""
        text = self.path.read_text(encoding='utf-8')
        step = text[text.rindex('      - name: '):]
        return textwrap.dedent(step[step.index('        run: |\n') + len('        run: |\n'):])

    def test_12_B8_the_workflow_holds_the_text_the_spec_gives(self):
        self.assertEqual(self.path.read_text(encoding='utf-8'), WORKFLOW)

    def test_12_B8_the_matrix_is_macos_and_windows_each_with_python_3_11_and_3_13(self):
        lines = self.path.read_text(encoding='utf-8').splitlines()
        matrix = lines.index('      matrix:')
        self.assertEqual(lines[matrix + 1:matrix + 4], ['        os: [macos-latest, windows-latest]',
                                                      "        python: ['3.11', '3.13']",
                                                      '    runs-on: ${{ matrix.os }}'])
        self.assertIn('          python-version: ${{ matrix.python }}', lines)
        self.assertEqual(lines[lines.index('  installed:') + 1], '    timeout-minutes: 150')

    def test_12_B8_the_last_step_runs_the_tests_on_the_copy_and_then_the_smoke_case_each_with_check_true(self):
        tree = ast.parse(self.last_step())
        runs = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                and ast.unparse(node.func) == 'subprocess.run']
        self.assertEqual([ast.unparse(run.args[0]) for run in runs], [
            "[sys.executable, '-m', 'unittest', 'discover', '-s', str(copy / 'tests'), '-v']",
            "[sys.executable, '-m', 'firmware_bench.benchmark', 'run', '--case', 'setup-smoke', '--output', "
            "str(Path(work) / 'smoke output')]"])
        for run in runs:
            keywords = {keyword.arg: ast.unparse(keyword.value) for keyword in run.keywords}
            self.assertEqual(keywords, {'cwd': 'work', 'env': 'env', 'check': 'True'})
        script = self.last_step()
        self.assertIn("ignore=shutil.ignore_patterns('src', 'dist', 'build', '*.egg-info', '__pycache__')", script)
        self.assertIn("env.pop('PYTHONPATH', None)", script)


if __name__ == '__main__':
    unittest.main()
