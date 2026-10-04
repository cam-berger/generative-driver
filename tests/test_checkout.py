"""Checkout must preserve the bytes used by benchmark integrity checks."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class CheckoutTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("git"), "Git is needed for the checkout contract")
    def test_windows_checkout_preserves_benchmark_and_evaluator_bytes(self):
        repo = Path(__file__).resolve().parents[1]
        inputs = [p for p in (repo / "src").rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts
                  and (p.suffix == ".py" or "resources" in p.parts)]
        self.assertTrue(inputs, "Source archive must include the benchmark inputs")
        expected = {p.relative_to(repo): p.read_bytes() for p in inputs}
        with tempfile.TemporaryDirectory(prefix="benchmark checkout ") as temporary:
            checkout = Path(temporary)
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

            def git(*args):
                subprocess.run(["git", "-c", "core.autocrlf=false", "-c",
                                "core.attributesfile=" + os.devnull, *args],
                               cwd=checkout, env=env, check=True, capture_output=True)

            git("init", "--quiet", "--template=")
            attributes = repo / ".gitattributes"
            if attributes.exists():
                shutil.copyfile(attributes, checkout / ".gitattributes")
            for relative, data in expected.items():
                target = checkout / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            git("add", ".")
            for relative in expected:
                (checkout / relative).unlink()
            git("-c", "core.autocrlf=true", "checkout-index", "--all", "--force")

            root = checkout / "src/generative_driver/resources/bench"
            for path in (root / "cases").glob("*/case.json"):
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if truth := manifest.get("truth"):
                    with self.subTest(case=manifest["id"]):
                        self.assertEqual(hashlib.sha256((root / truth["path"]).read_bytes()).hexdigest(),
                                         truth["sha256"])
            changed = [str(relative) for relative, data in expected.items()
                       if (checkout / relative).read_bytes() != data]
            self.assertEqual(changed, [], "Checkout changed pinned resource/evaluator bytes")
