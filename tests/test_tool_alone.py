"""The tool stands alone: its package, its build files and its folders hold nothing of the benchmark.

Feature 11 of the untangle Run (docs/features/untangle/11-tool-alone.md): 11-B1 (the folders that are gone from the
tool), 11-B2 (the build file, the doctor, and with `GD_TEST_WHEEL` the built wheel), 11-B3 (no marker line), 11-B7
(the records and notes that are gone), the release check's part of 11-B9, and 11-B10 (the few places that name the
benchmark's folder). This is the one tool test file that may hold the benchmark's names as text.

The Spec leaves 11-B7 to the Reviewer. It still has a test here, because the Done check asks for one by name.

The files are read from the checkout and from the installed package, and `doctor` is asked in a temporary home.
Nothing else is started.
"""
import importlib.util
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import generative_driver

REPO = Path(__file__).resolve().parents[1]
RELEASE_CHECK = REPO / '.github' / 'scripts' / 'tool_releases.py'
# The tool's package where it is imported from, and in the checkout where the tests run from one.
PACKAGES = sorted({Path(generative_driver.__file__).resolve().parent,
                   *(path for path in [REPO / 'src' / 'generative_driver'] if path.is_dir())})
# What the benchmark had inside the tool's package: its command, its code and its two resource folders.
PACKAGE_PARTS = ('benchmark.py', 'benchmark_support', 'resources/bench', 'resources/registrations')
# The benchmark's documents, records and notes as the checkout held them, each a file or a folder.
CHECKOUT_PARTS = (
    'bench', 'docs/benchmark-case-authoring.md', 'docs/benchmark-corpus.md', 'docs/generated-device-interface.md',
    'docs/codex-acceptance.md', 'docs/adr/0001-benchmark-registration-and-selection.md',
    'docs/adr/0003-generated-devices-register-by-set.md', 'docs/adr/0004-compare-systems-through-a-scoring-set.md',
    'docs/adr/0005-keep-the-device-generator-private.md', 'docs/adr/0006-seal-graded-scoring-trials.md',
    'docs/adr/0007-one-validator-defines-a-generated-device.md', '.scratch/benchmark-baseline',
    '.scratch/benchmark-lifecycle', '.scratch/benchmark-review')


class ParkedTests(unittest.TestCase):
    """Everything of the benchmark has left the tool's folders (11-B1)."""

    def test_11_B1_the_tools_package_holds_no_benchmark_module_and_no_benchmark_resource(self):
        self.assertTrue(PACKAGES)
        left = [str(package / part) for package in PACKAGES for part in PACKAGE_PARTS if (package / part).exists()]
        self.assertEqual(left, [])

    def test_11_B1_neither_benchmark_name_imports_from_the_tools_package(self):
        # A folder that holds only compiled files would still import, as an empty package.
        for name in ('generative_driver.benchmark', 'generative_driver.benchmark_support'):
            with self.subTest(name=name):
                self.assertIsNone(importlib.util.find_spec(name))

    def test_11_B1_the_checkout_holds_no_benchmark_folder_document_or_note(self):
        if not (REPO / 'docs').is_dir():
            self.skipTest(f'The documents are read from a checkout, and {REPO / "docs"} is not there')
        left = [part for part in CHECKOUT_PARTS if (REPO / part).exists()]
        self.assertEqual(left, [])
        # No other document or note is named for the benchmark. A Run's own folder under `docs/features/` is a
        # record and is left out.
        named = [path.relative_to(REPO).as_posix() for folder in ('docs', '.scratch') if (REPO / folder).is_dir()
                 for path in sorted((REPO / folder).rglob('*'))
                 if 'benchmark' in path.name.lower() and 'features' not in path.relative_to(REPO).parts]
        self.assertEqual(named, [])


def requirement(text, extra=None):
    """The requirement `text` of a build file as (the package's name, its version specifier, the extra it belongs
    to)."""
    name, specifier = re.fullmatch(r'([A-Za-z0-9_.-]+)\s*(.*)', text).groups()
    return name, specifier, extra


class PackageTests(unittest.TestCase):
    """The tool's package holds nothing of the benchmark, and `doctor` looks for no program of the benchmark's
    (11-B2)."""

    def read(self, name):
        """The TOML file `name` of the checkout."""
        if not (REPO / name).is_file():
            self.skipTest(f'{name} is read from a checkout, and {REPO / name} is not there')
        return tomllib.loads((REPO / name).read_text(encoding='utf-8'))

    def test_11_B2_the_build_file_has_the_tools_two_scripts_and_two_dependencies(self):
        project = self.read('pyproject.toml')['project']
        self.assertEqual(project['scripts'], {'generative-driver': 'generative_driver.cli:main',
                                              'generative-driver-mcp': 'generative_driver.mcp:main'})
        self.assertEqual(project['dependencies'], ['mcp>=2.2,<3', 'jsonschema>=4.20,<5'])

    def test_11_B2_the_lock_agrees_with_the_build_file(self):
        project = self.read('pyproject.toml')['project']
        declared = {requirement(text) for text in project['dependencies']}
        declared |= {requirement(text, extra) for extra, texts in project['optional-dependencies'].items()
                     for text in texts}
        root, = [package for package in self.read('uv.lock')['package'] if package.get('source') == {'editable': '.'}]
        self.assertEqual(root['name'], project['name'])
        locked = {(entry['name'], entry['specifier'],
                   re.fullmatch(r"extra == '(.+)'", entry['marker']).group(1) if 'marker' in entry else None)
                  for entry in root['metadata']['requires-dist']}
        self.assertEqual(locked, declared)
        self.assertEqual(sorted(entry['name'] for entry in root['dependencies']), ['jsonschema', 'mcp'])

    def test_11_B2_doctor_reports_codex_java_and_podman_and_no_program_of_the_benchmarks(self):
        from generative_driver.setup import doctor
        found = {'java': '/somewhere/bin/java'}
        with tempfile.TemporaryDirectory(prefix='tool alone ') as temporary, \
                patch('shutil.which', lambda name, *more, **named: found.get(name)):
            report = doctor(Path(temporary) / 'home')
        programs = report['dependencies']
        self.assertEqual(sorted(programs), ['codex', 'java', 'podman'])
        self.assertEqual(programs['java'], {'available': True, 'path': '/somewhere/bin/java',
                                            'required_for': 'firmware analysis', 'source': 'PATH'})
        self.assertEqual(programs['podman'], {'available': False, 'path': None,
                                              'required_for': 'optional analysis containers', 'source': 'PATH'})
        self.assertEqual((programs['codex']['configured'], programs['codex']['available']), (False, False))

    @unittest.skipUnless(os.environ.get('GD_TEST_WHEEL'), 'Set GD_TEST_WHEEL to the built wheel')
    def test_11_B2_the_wheel_holds_no_benchmark_path_script_or_requirement(self):
        with zipfile.ZipFile(os.environ['GD_TEST_WHEEL']) as archive:
            names = archive.namelist()
            metadata, = [name for name in names if name.endswith('.dist-info/METADATA')]
            entry_points, = [name for name in names if name.endswith('.dist-info/entry_points.txt')]
            required = [re.match(r'Requires-Dist:\s*([A-Za-z0-9_.-]+)', line).group(1)
                        for line in archive.read(metadata).decode('utf-8').splitlines()
                        if line.startswith('Requires-Dist:') and 'extra ==' not in line]
            scripts = [line.partition('=')[0].strip()
                       for line in archive.read(entry_points).decode('utf-8').splitlines() if '=' in line]
        self.assertIn('generative_driver/cli.py', names)
        self.assertEqual([name for name in names if 'benchmark' in name or 'resources/bench' in name
                          or 'resources/registrations' in name], [])
        self.assertEqual(sorted(scripts), ['generative-driver', 'generative-driver-mcp'])
        self.assertEqual(sorted(required), ['jsonschema', 'mcp'])


def tracked_files():
    """The files Git tracks in the checkout that are there, as POSIX paths from its root, or None where the tests do
    not run from a Git checkout."""
    if not (REPO / '.git').exists():
        return None
    try:
        listed = subprocess.run(['git', 'ls-files', '-z'], cwd=REPO, capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return [name for name in listed.stdout.decode('utf-8').split('\0') if name and (REPO / name).is_file()]


# The text that both marker lines of Feature 01 hold, before `:begin` and `:end`. It is put together here so that
# this file holds no marker line.
MARKER = 'legacy-' + 'benchmark'
# Where a marker line may still stand: in this Run's own records and in the parked benchmark.
RECORDS, PARKED = 'docs/features/untangle/', 'firmware-bench/'
# The one paragraph of the front page that points to the benchmark (11-B10). `tests/test_tool_documents.py` takes it
# from here.
README_PARAGRAPH = ('Firmware bench, the benchmark that evaluates this tool, lies in '
                    '[firmware-bench/](firmware-bench/README.md) until it moves to a repository of its own.')


def marker_lines(text):
    """The numbers of the lines of `text` that hold the marker text, counted from 1."""
    return [number for number, line in enumerate(text.splitlines(), 1) if MARKER in line]


class TrackedFilesCase(unittest.TestCase):
    def tracked(self):
        """The tracked files of the checkout, each with its text; a file that is no UTF-8 text is read leniently."""
        names = tracked_files()
        if names is None:
            self.skipTest(f'The tracked files are listed by Git, and {REPO} is no Git checkout')
        self.assertGreater(len(names), 300)
        return [(name, (REPO / name).read_text(encoding='utf-8', errors='replace')) for name in names]


class NoMarkerTests(TrackedFilesCase):
    """The deletion list, its marker lines and its check are gone (11-B3)."""

    def test_11_B3_the_deletion_list_its_check_and_the_test_of_the_dropped_cases_are_gone(self):
        gone = ('docs/benchmark-deletion-list.md', 'tests/deletion_marking.py', 'tests/test_deletion_marking.py',
                'tests/test_service_without_legacy_cases.py')
        self.assertEqual([name for name in gone if (REPO / name).exists()], [])

    def test_11_B3_no_tracked_file_of_the_tool_holds_a_marker_line(self):
        holding = [f'{name}:{number}' for name, text in self.tracked() if not name.startswith((RECORDS, PARKED))
                   for number in marker_lines(text)]
        self.assertEqual(holding, [])

    def test_11_B3_the_scan_sees_a_marker_in_each_comment_form_and_leaves_other_text(self):
        begin, end = MARKER + ':begin', MARKER + ':end'
        self.assertEqual(marker_lines(f'kept\n# {begin}\nmarked\n# {end}\nkept'), [2, 4])
        self.assertEqual(marker_lines(f'<!-- {begin} -->\nmarked\n    <!-- {end} -->'), [1, 3])
        self.assertEqual(marker_lines(f"found = [line for line in lines if '{MARKER}' in line]"), [1])
        self.assertEqual(marker_lines('the legacy benchmark begins\n# legacy: begin\n'), [])


class RecordsTests(unittest.TestCase):
    """The tool's outdated records and notes are deleted, and the notes that stay name what is there (11-B7)."""

    def setUp(self):
        if not (REPO / '.git').exists():
            self.skipTest(f'The records and notes are read from a Git checkout, and {REPO} is none')

    def test_11_B7_the_past_results_and_the_finished_notes_are_gone_and_the_open_ones_stay(self):
        gone = ('docs/verification.md', 'bench/evidence', 'docs/research/2026-10-28-mhs-interface-review.md',
                '.scratch/effect-labels/spec.md')
        kept = ('.scratch/effect-labels/issues/01-observe-read-state-changes.md', '.scratch/learning-loop/spec.md',
                'docs/research/2026-10-30-firmware-learning-practice.md', 'docs/features/kit-fixes')
        self.assertEqual([name for name in gone if (REPO / name).exists()], [])
        self.assertEqual([name for name in kept if not (REPO / name).exists()], [])

    def test_11_B7_the_notes_that_stay_link_nothing_that_moved_or_went(self):
        issue = (REPO / '.scratch/effect-labels/issues/01-observe-read-state-changes.md').read_text(encoding='utf-8')
        place = 'src/generative_driver/supplied/instrument_evidence.py'
        self.assertTrue((REPO / place).is_file())
        self.assertIn(f'`{place}`', issue)
        self.assertNotIn('benchmark_support', issue)
        self.assertEqual(re.findall(r'\]\(([^)]+)\)', issue), [])
        spec = (REPO / '.scratch/learning-loop/spec.md').read_text(encoding='utf-8')
        targets = [target.partition('#')[0] for target in re.findall(r'\]\(([^)]+)\)', spec)]
        self.assertEqual(sorted(set(targets)), ['../../docs/design.md',
                                               '../../docs/research/2026-10-30-firmware-learning-practice.md'])
        self.assertEqual([target for target in targets if not (REPO / '.scratch/learning-loop' / target).is_file()], [])
        self.assertIn('became pull request 24', spec.splitlines()[-1])


@unittest.skipUnless(RELEASE_CHECK.is_file(), 'The release check ships with the Git checkout, not the source archive')
class ReleaseCheckTests(unittest.TestCase):
    """The weekly release check watches the workflows and the parked front page, and its issue says nothing of the
    benchmark (11-B9)."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('tool_releases', RELEASE_CHECK)
        self.releases = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.releases)

    def test_11_B9_the_release_check_reads_the_workflows_and_the_parked_front_page_and_nothing_else(self):
        self.assertEqual(self.releases.WATCHED, ('.github/workflows/*.yml', '.github/workflows/*.yaml',
                                                 'firmware-bench/README.md'))
        pins = {'.github/workflows/ci.yml': 'o/tool', '.github/workflows/lane.yaml': 'o/lane',
                'firmware-bench/README.md': 'o/emulator', 'bench/README.md': 'o/moved', 'README.md': 'o/front',
                'firmware-bench/docs/corpus.md': 'o/guide'}
        with tempfile.TemporaryDirectory(prefix='tool alone ') as temporary:
            for name, repo in pins.items():
                (Path(temporary) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(temporary) / name).write_text(f'Uses https://github.com/{repo}/releases/tag/v1.\n',
                                                    encoding='utf-8')
            texts = self.releases.watched_texts(Path(temporary))
        self.assertEqual(sorted(texts), ['.github/workflows/ci.yml', '.github/workflows/lane.yaml',
                                         'firmware-bench/README.md'])
        self.assertEqual(sorted(pin.repo for pin in self.releases.find_pins(texts)),
                         ['o/emulator', 'o/lane', 'o/tool'])

    def test_11_B9_an_issue_asks_for_the_pins_and_the_affected_workflows_and_names_no_benchmark(self):
        pins = self.releases.find_pins({'ci.yml': 'https://github.com/o/tool/releases/download/v1/tool.zip'})
        alert, = self.releases.alerts(pins, {'o/tool': 'v2'}.__getitem__)
        self.assertEqual(alert.body.split('\n\n')[2:], [
            'Update every pin above together with any SHA-256 beside it, search the repository for the old version '
            'to find unlinked mentions, and run the affected workflows.',
            'Close this issue once decided. The weekly check does not reopen it for this release.\n'])
        for gone in ('benchmark', 'calibration', 'comparing results'):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, alert.body)


# The two spellings of the benchmark's folder and package.
FOLDER_NAMES = ('firmware-bench', 'firmware_bench')
# The files outside the parked folder, the Runs' records and the tests that name it, each with the number of its
# lines that do: the release check's module doc and its watched files, the comment of its workflow, and the one
# paragraph of the front page.
NAMED_PLACES = {'.github/scripts/tool_releases.py': 2, '.github/workflows/tool-releases.yml': 1, 'README.md': 1}
# The tool test files that name it: the two that forbid the import of the benchmark, and this one, which holds the
# list.
NAMING_TESTS = ['tests/test_shared_helpers.py', 'tests/test_tool_alone.py', 'tests/test_tool_boundary.py']


def naming_lines(text):
    """The lines of `text` that name the benchmark's folder or package."""
    return [line for line in text.splitlines() if any(name in line for name in FOLDER_NAMES)]


class NamedPlacesTests(TrackedFilesCase):
    """Outside the parked folder, only named places speak of it (11-B10)."""

    def test_11_B10_outside_the_parked_folder_the_records_and_the_tests_only_four_places_name_it(self):
        naming = {name: naming_lines(text) for name, text in self.tracked()
                  if not name.startswith((PARKED, 'docs/features/', 'tests/'))}
        naming = {name: lines for name, lines in naming.items() if lines}
        self.assertEqual({name: len(lines) for name, lines in naming.items()}, NAMED_PLACES)
        self.assertEqual(naming['README.md'], [README_PARAGRAPH])
        # Each place of the release check names the parked front page, where the emulator's pin is kept.
        for name in ('.github/scripts/tool_releases.py', '.github/workflows/tool-releases.yml'):
            for line in naming[name]:
                with self.subTest(file=name, line=line):
                    self.assertEqual(line.count('firmware-bench/README.md'), 1)
                    self.assertEqual(sum(line.count(folder) for folder in FOLDER_NAMES), 1)

    def test_11_B10_among_the_tests_only_those_that_forbid_its_import_or_hold_this_list_name_it(self):
        naming = [name for name, text in self.tracked() if name.startswith('tests/') and naming_lines(text)]
        self.assertEqual(naming, NAMING_TESTS)
        # The scan sees both spellings, and leaves words that only resemble them.
        self.assertEqual(naming_lines(f'A line.\n{README_PARAGRAPH}\nimport firmware_bench.harness\n'),
                         [README_PARAGRAPH, 'import firmware_bench.harness'])
        self.assertEqual(naming_lines('The firmware bench and the bench of a firmware.\n'), [])


if __name__ == '__main__':
    unittest.main()
