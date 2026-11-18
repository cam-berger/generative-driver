"""A checkout that converts line endings keeps the bytes the benchmark's manifests pin.

Each case's manifest names the SHA-256 of its answer file, and the bench harness refuses a case whose file differs.
This checks out the files under `resources/bench/` as Git for Windows does by default and holds each answer file to
that hash. The tool's `tests/test_checkout.py` held this beside its own assertion until the tests were pulled apart
(10-B1 of docs/features/untangle/10-tests.md).
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
RESOURCES = Path('src/generative_driver/resources/bench')


class BenchmarkCheckoutTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('git'), 'Git is needed for the checkout contract')
    def test_10_B1_a_windows_checkout_keeps_each_answer_file_at_the_hash_its_manifest_names(self):
        inputs = [path for path in (REPO / RESOURCES).rglob('*') if path.is_file() and '__pycache__' not in path.parts]
        self.assertTrue(inputs, 'The checkout holds no benchmark resource')
        expected = {path.relative_to(REPO): path.read_bytes() for path in inputs}
        with tempfile.TemporaryDirectory(prefix='benchmark checkout ') as temporary:
            checkout = Path(temporary)
            env = {name: value for name, value in os.environ.items() if not name.startswith('GIT_')}
            env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)

            def git(*arguments):
                subprocess.run(['git', '-c', 'core.autocrlf=false', '-c', 'core.attributesfile=' + os.devnull,
                                *arguments], cwd=checkout, env=env, check=True, capture_output=True)

            git('init', '--quiet', '--template=')
            attributes = REPO / '.gitattributes'
            if attributes.exists():
                shutil.copyfile(attributes, checkout / '.gitattributes')
            for relative, data in expected.items():
                target = checkout / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            git('add', '.')
            for relative in expected:
                (checkout / relative).unlink()
            git('-c', 'core.autocrlf=true', 'checkout-index', '--all', '--force')

            root, checked = checkout / RESOURCES, []
            for path in sorted((root / 'cases').glob('*/case.json')):
                manifest = json.loads(path.read_text(encoding='utf-8'))
                if truth := manifest.get('truth'):
                    checked.append(manifest['id'])
                    with self.subTest(case=manifest['id']):
                        self.assertEqual(hashlib.sha256((root / truth['path']).read_bytes()).hexdigest(),
                                         truth['sha256'])
            self.assertTrue(checked, 'No case manifest names an answer file')


if __name__ == '__main__':
    unittest.main()
