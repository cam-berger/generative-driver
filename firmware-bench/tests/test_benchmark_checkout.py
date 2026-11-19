"""A checkout that converts line endings keeps the bytes of the benchmark's package.

Each case's manifest names the SHA-256 of its answer file, and the bench harness refuses a case whose file differs. A
revision and a grader identity hash the package's code and resources. This checks out every file of the package
`firmware_bench` as Git for Windows does by default, with the `.gitattributes` of this folder's parent, and holds
each file to its bytes and each answer file to its hash. The tool's `tests/test_checkout.py` holds the same for the
tool (10-B1 of docs/features/untangle/10-tests.md; 12-B4 of docs/features/untangle/12-firmware-bench.md).

The package is found through the import, so the test also runs on a copy of this folder without its `src/`.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import firmware_bench

BENCH = Path(__file__).resolve().parents[1]
PACKAGE = Path(firmware_bench.__file__).resolve().parent


def package_files():
    """Every file of the package, code and resources, as {path from the package's parent: bytes}."""
    return {path.relative_to(PACKAGE.parent): path.read_bytes() for path in sorted(PACKAGE.rglob('*'))
            if path.is_file() and '__pycache__' not in path.parts
            and (path.suffix == '.py' or 'resources' in path.relative_to(PACKAGE).parts)}


@contextmanager
def windows_checkout(files):
    """A folder that holds `files` as a checkout that converts line endings leaves them, under the attributes file
    of this folder's parent."""
    with tempfile.TemporaryDirectory(prefix='benchmark checkout ') as temporary:
        checkout = Path(temporary)
        env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
        env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

        def git(*arguments):
            subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.attributesfile=' + os.devnull,
                            *arguments], cwd=checkout, env=env, check=True, capture_output=True)

        git('init', '--quiet', '--template=')
        attributes = BENCH / '.gitattributes'
        if attributes.exists():
            shutil.copyfile(attributes, checkout / '.gitattributes')
        for relative, data in files.items():
            target = checkout / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        git('add', '.')
        for relative in files:
            (checkout / relative).unlink()
        git('-c', 'core.autocrlf=true', 'checkout-index', '--all', '--force')
        yield checkout


@unittest.skipUnless(shutil.which('git'), 'Git is needed for the checkout contract')
class BenchmarkCheckoutTests(unittest.TestCase):
    def test_10_B1_a_windows_checkout_keeps_each_answer_file_at_the_hash_its_manifest_names(self):
        resources = Path(PACKAGE.name) / 'resources' / 'bench'
        inputs = {relative: data for relative, data in package_files().items() if relative.is_relative_to(resources)}
        self.assertTrue(inputs, 'The package holds no benchmark resource')
        with windows_checkout(inputs) as checkout:
            root, checked = checkout / resources, []
            for path in sorted((root / 'cases').glob('*/case.json')):
                manifest = json.loads(path.read_text(encoding='utf-8'))
                if truth := manifest.get('truth'):
                    checked.append(manifest['id'])
                    with self.subTest(case=manifest['id']):
                        self.assertEqual(hashlib.sha256((root / truth['path']).read_bytes()).hexdigest(),
                                         truth['sha256'])
            self.assertTrue(checked, 'No case manifest names an answer file')

    def test_12_B4_a_windows_checkout_keeps_every_file_of_the_package_byte_for_byte(self):
        files = package_files()
        names = {relative.as_posix() for relative in files}
        # Code and resources both: a module of the grading code, the registrations and an answer file.
        for name in ('firmware_bench/__init__.py', 'firmware_bench/benchmark_support/evaluator.py',
                     'firmware_bench/resources/registrations/cases.json',
                     'firmware_bench/resources/bench/groundtruth/tq9-v2.enc'):
            self.assertIn(name, names)
        self.assertTrue((BENCH / '.gitattributes').is_file(), 'firmware-bench/.gitattributes is missing')
        with windows_checkout(files) as checkout:
            changed = [relative.as_posix() for relative, data in files.items()
                       if (checkout / relative).read_bytes() != data]
        self.assertEqual(changed, [], 'The checkout changed bytes of the package')


if __name__ == '__main__':
    unittest.main()
