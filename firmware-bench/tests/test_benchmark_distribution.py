"""What the benchmark's wheel must hold; the hosted tests name the wheel.

Feature 12 of the untangle Run (docs/features/untangle/12-firmware-bench.md), 12-B1 and 12-B4. `FB_TEST_WHEEL` names
the wheel built from this folder's parent. Where it is not set the tests are skipped. The tool's
`tests/test_distribution.py` holds the same for the tool's wheel.
"""
import os
from pathlib import Path
import unittest
import zipfile

import firmware_bench

WHEEL = os.environ.get('FB_TEST_WHEEL')
# The resources beside the tests, where `src/` lies here: on a copy of this folder without it they are not read.
SOURCE = Path(__file__).resolve().parents[1] / 'src' / 'firmware_bench'
SMOKE = 'firmware_bench/resources/bench/cases/setup-smoke/'


@unittest.skipUnless(WHEEL, 'Set FB_TEST_WHEEL to the wheel built from the benchmark\'s folder')
class BenchmarkWheelTests(unittest.TestCase):
    def names(self):
        with zipfile.ZipFile(WHEEL) as archive:
            return set(archive.namelist())

    def test_12_B1_the_wheel_holds_the_registrations_and_every_file_of_the_smoke_case(self):
        names = self.names()
        # Every import of the registrations reads the first; every load of a generated set that passes no table of
        # its own reads the second.
        for resource in ('firmware_bench/resources/registrations/cases.json',
                         'firmware_bench/resources/registrations/sets.json',
                         SMOKE + 'model.json', SMOKE + 'replay.json'):
            self.assertIn(resource, names)
        # The installed package came from this wheel where the hosted tests run: each file of its smoke case is one
        # of the wheel's.
        installed = Path(firmware_bench.__file__).resolve().parent
        smoke = sorted(path.relative_to(installed.parent).as_posix()
                       for path in (installed / 'resources/bench/cases/setup-smoke').rglob('*')
                       if path.is_file() and '__pycache__' not in path.parts)
        self.assertGreaterEqual(len(smoke), 2)
        self.assertEqual([name for name in smoke if name not in names], [])

    def test_12_B1_the_wheel_holds_every_resource_where_the_source_lies_beside_the_tests(self):
        if not SOURCE.is_dir():
            self.skipTest(f'The package\'s source is not beside the tests: {SOURCE}')
        resources = sorted(path.relative_to(SOURCE.parent).as_posix() for path in (SOURCE / 'resources').rglob('*')
                           if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc')
        self.assertGreater(len(resources), 30)
        names = self.names()
        self.assertEqual([name for name in resources if name not in names], [])

    def test_12_B1_the_wheel_gives_the_command_and_holds_no_test_and_no_cache(self):
        names = self.names()
        entry_points = [name for name in names if name.endswith('.dist-info/entry_points.txt')]
        self.assertEqual(len(entry_points), 1, sorted(names)[:20])
        with zipfile.ZipFile(WHEEL) as archive:
            text = archive.read(entry_points[0]).decode('utf-8')
        self.assertIn('firmware-bench = firmware_bench.benchmark:main', text.splitlines())
        self.assertEqual(sorted({name.split('/')[0] for name in names if '.dist-info/' not in name}),
                         ['firmware_bench'])
        self.assertEqual([name for name in names if '__pycache__' in name or name.endswith(('.pyc', '.pyo'))], [])


if __name__ == '__main__':
    unittest.main()
