"""What the built archives must hold for the benchmark; CI supplies their paths.

The tool's `tests/test_distribution.py` held these requirements beside its own until the tests were pulled apart
(10-B1 and 10-B5 of docs/features/untangle/10-tests.md). The wheel carries the resources the benchmark reads on
import, and the source archive the published record and the helpers of the documented test commands.
"""
import os
from pathlib import Path
import posixpath
import re
import tarfile
import unittest
import zipfile

TESTS = Path(__file__).resolve().parent


class BenchmarkDistributionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('GD_TEST_WHEEL'), 'Set GD_TEST_WHEEL for the release archive gate')
    def test_10_B1_the_wheel_ships_the_benchmarks_smoke_case_and_its_registrations(self):
        with zipfile.ZipFile(os.environ['GD_TEST_WHEEL']) as archive:
            names = archive.namelist()
        for resource in ('generative_driver/resources/bench/cases/setup-smoke/model.json',
                         # Every import of the registrations reads it.
                         'generative_driver/resources/registrations/cases.json',
                         # Every load of a generated set that passes no table of its own reads it.
                         'generative_driver/resources/registrations/sets.json'):
            self.assertIn(resource, names)

    @unittest.skipUnless(os.environ.get('GD_TEST_SDIST'), 'Set GD_TEST_SDIST for the source archive gate')
    def test_source_archive_includes_linked_verification_record(self):
        with tarfile.open(os.environ['GD_TEST_SDIST']) as archive:
            names = archive.getnames()
            prefix = names[0].split('/')[0]
            document = archive.extractfile(prefix + '/docs/verification.md').read().decode('utf-8')
            self.assertIn('../bench/evidence/benchmark-final-verification.json', document)
            links = re.findall(r'\]\(([^)]+\.json)\)', document)
            self.assertTrue(links, 'Verification must link its machine-readable evidence')
            for link in links:
                with self.subTest(link=link):
                    target = posixpath.normpath(posixpath.join(prefix, 'docs', link))
                    self.assertTrue(target.startswith(prefix + '/bench/evidence/'))
                    self.assertIn(target, names)

    @unittest.skipUnless(os.environ.get('GD_TEST_SDIST'), 'Set GD_TEST_SDIST for the source archive gate')
    def test_source_archive_includes_helpers_for_documented_test_commands(self):
        with tarfile.open(os.environ['GD_TEST_SDIST']) as archive:
            names = archive.getnames()
            prefix = names[0].split('/')[0]
            for helper in ('benchmark_family_fixtures.py', 'tq9_workflow_fixture.py',
                           'suite_fixtures.py', 'native_benchmark_families.py'):
                self.assertIn(prefix + '/firmware-bench/tests/' + helper, names)

    @unittest.skipUnless(os.environ.get('GD_TEST_SDIST'), 'Set GD_TEST_SDIST for the source archive gate')
    def test_10_B5_the_source_archive_holds_every_python_file_of_the_benchmarks_test_folder(self):
        with tarfile.open(os.environ['GD_TEST_SDIST']) as archive:
            names = set(archive.getnames())
        prefix = sorted(names)[0].split('/')[0]
        files = sorted(path.name for path in TESTS.glob('*.py'))
        self.assertGreater(len(files), 70)
        missing = [name for name in files if f'{prefix}/firmware-bench/tests/{name}' not in names]
        self.assertEqual(len(missing), 0, ', '.join(missing))


if __name__ == '__main__':
    unittest.main()
