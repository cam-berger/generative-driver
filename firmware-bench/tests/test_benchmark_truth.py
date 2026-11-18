import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark_support.truth import EvaluatorPassword


class EncryptedTruthTests(unittest.TestCase):
    def test_password_and_pinned_hash_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'truth.enc'
            evidence = {'schema': 'benchmark-truth/1', 'expected': {'reading': 21.5}}
            pin = EvaluatorPassword('test-only-passphrase').seal(evidence, path)
            self.assertNotIn(b'21.5', path.read_bytes())
            self.assertEqual(EvaluatorPassword('test-only-passphrase').unlock(path, pin), evidence)
            with self.assertRaises(ValueError):
                EvaluatorPassword('incorrect').unlock(path, pin)
            with self.assertRaises(ValueError):
                EvaluatorPassword('test-only-passphrase').unlock(path, '0' * 64)


if __name__ == '__main__':
    unittest.main()
