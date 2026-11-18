"""The benchmark's reference setup pins the emulator release its cases were calibrated on.

The weekly release check reads the pins of `bench/README.md`, and the tool's `tests/test_tool_releases.py` held the
Renode pin among them until the tests were pulled apart (10-B1 of docs/features/untangle/10-tests.md). This reads
the document itself.
"""
from pathlib import Path
import re
import unittest

REPO = Path(__file__).resolve().parents[2]
# A link to one release of Renode on GitHub, as the release check reads a pin: the tag follows.
RENODE_PIN = re.compile(r'https://github\.com/renode/renode/releases/(?:download|tag)/([^/\s)"\'<>#?]+)')


class ReferenceToolTests(unittest.TestCase):
    def test_10_B1_the_bench_readme_pins_a_release_of_renode(self):
        text = (REPO / 'bench' / 'README.md').read_text(encoding='utf-8')
        self.assertTrue(RENODE_PIN.findall(text), 'bench/README.md links to no release of renode/renode by its tag')


if __name__ == '__main__':
    unittest.main()
