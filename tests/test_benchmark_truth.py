import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark_support.truth import seal, unlock


class EncryptedTruthTests(unittest.TestCase):
    def test_password_and_pinned_hash_are_both_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'truth.enc'
            evidence = {'schema': 'benchmark-truth/1', 'expected': {'reading': 21.5}}
            pin = seal(evidence, path, 'test-only-passphrase')
            self.assertNotIn(b'21.5', path.read_bytes())
            self.assertEqual(unlock(path, 'test-only-passphrase', pin), evidence)
            with self.assertRaises(ValueError):
                unlock(path, 'incorrect', pin)
            with self.assertRaises(ValueError):
                unlock(path, 'test-only-passphrase', '0' * 64)


if __name__ == '__main__':
    unittest.main()
