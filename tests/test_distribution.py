"""Release archive contract; CI supplies the built wheel path."""
import os
from pathlib import Path
import unittest
import zipfile


class DistributionTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("GD_TEST_WHEEL"), "Set GD_TEST_WHEEL for the release archive gate")
    def test_wheel_contains_portable_resources_without_local_bytecode(self):
        with zipfile.ZipFile(os.environ["GD_TEST_WHEEL"]) as archive:
            names = archive.namelist()
            self.assertFalse([n for n in names if n.endswith((".pyc", ".pyo")) or "__pycache__" in n])
            for resource in ("interface_runtime/engine.py", "generative_driver/resources/toolchain/tools.json",
                             "generative_driver/resources/bench/cases/setup-smoke/model.json",
                             "generative_driver/resources/client/skills/generative-driver/SKILL.md"):
                self.assertIn(resource, names)
