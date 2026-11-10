"""The weekly tool-release check reports pinned GitHub releases that trail upstream."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".github" / "scripts" / "tool_releases.py"


def load_script():
    spec = importlib.util.spec_from_file_location("tool_releases", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_checkout(root):
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "bench").mkdir()
    (root / ".github" / "workflows" / "ci.yml").write_text(
        "TOOL: https://github.com/o/tool/releases/download/v1/tool.zip\n"
        "OLD: https://github.com/o/old/releases/download/v1/old.zip\n", encoding="utf-8")
    (root / "bench" / "README.md").write_text(
        "Uses [Fresh 9](https://github.com/o/fresh/releases/tag/v9).\n", encoding="utf-8")


class FakeGh:
    """Answers the GitHub CLI calls the check makes, from fixed release and issue lists."""

    def __init__(self, latest, titles=()):
        self.latest, self.titles = latest, list(titles)
        self.listed, self.created = [], []

    def __call__(self, args):
        if args[0] == "api":
            return self.latest[args[1].removeprefix("repos/").removesuffix("/releases/latest")] + "\n"
        if args[:2] == ["issue", "list"]:
            self.listed.append(args)
            return json.dumps([{"title": title} for title in self.titles])
        if args[:2] == ["issue", "create"]:
            self.created.append((args[args.index("--title") + 1], args[args.index("--body") + 1]))
            return ""
        raise AssertionError(f"unexpected gh call {args}")


@unittest.skipUnless(SCRIPT.is_file(), "The release check ships with the Git checkout, not the source archive")
class ToolReleaseTests(unittest.TestCase):
    def setUp(self):
        self.releases = load_script()

    def test_pins_are_read_from_release_download_and_tag_links(self):
        pins = self.releases.find_pins({
            "ci.yml": "URL: https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.3_build/"
                      "ghidra_12.1.3_PUBLIC_20260817.zip\n"
                      "JDK: https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1\n"
                      "GRADLE: https://services.gradle.org/distributions\n",
            "README.md": "Uses [Renode 1.16.1](https://github.com/renode/renode/releases/tag/v1.16.1), "
                         "listed on [releases](https://github.com/renode/renode/releases).",
        })
        self.assertEqual({(pin.repo, pin.tag, pin.source) for pin in pins}, {
            ("NationalSecurityAgency/ghidra", "Ghidra_12.1.3_build", "ci.yml"),
            ("adoptium/temurin21-binaries", "jdk-21.0.12.1+1", "ci.yml"),
            ("renode/renode", "v1.16.1", "README.md"),
        })

    def test_only_pins_behind_the_latest_release_raise_an_alert(self):
        pins = self.releases.find_pins({
            "a.yml": "https://github.com/o/tool/releases/download/v1/x.zip\n"
                     "https://github.com/o/fresh/releases/download/v9/y.zip\n",
            "b.md": "[tool](https://github.com/o/tool/releases/tag/v1)",
            "c.md": "[tool](https://github.com/o/tool/releases/tag/v2)",
        })
        alerts = self.releases.alerts(pins, {"o/tool": "v2", "o/fresh": "v9"}.__getitem__)
        self.assertEqual([(alert.repo, alert.latest, alert.stale) for alert in alerts],
                         [("o/tool", "v2", {"v1": ["a.yml", "b.md"]})])
        self.assertEqual(alerts[0].title, "New upstream release: o/tool v2")
        self.assertIn("https://github.com/o/tool/releases/tag/v2", alerts[0].body)
        self.assertIn("`a.yml`", alerts[0].body)
        self.assertNotIn("`c.md`", alerts[0].body)

    def test_open_issues_raises_each_new_release_once_even_after_it_is_closed(self):
        gh = FakeGh({"o/tool": "v2", "o/old": "v5", "o/fresh": "v9"},
                    titles=["New upstream release: o/old v5"])
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            write_checkout(Path(temporary))
            status = self.releases.main(["--open-issues"], root=Path(temporary), gh=gh)
        self.assertEqual(status, 0)
        self.assertEqual([title for title, _ in gh.created], ["New upstream release: o/tool v2"])
        self.assertIn("`.github/workflows/ci.yml`", gh.created[0][1])
        self.assertEqual([args[args.index("--state") + 1] for args in gh.listed], ["all"])

    def test_report_lists_each_pinned_tool_without_opening_issues(self):
        gh = FakeGh({"o/tool": "v2", "o/old": "v1", "o/fresh": "v9"})
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(output):
            write_checkout(Path(temporary))
            status = self.releases.main([], root=Path(temporary), gh=gh)
        self.assertEqual(status, 0)
        self.assertEqual(output.getvalue().splitlines(), [
            "o/fresh v9 current",
            "o/old v1 current",
            "o/tool v1 -> v2 (.github/workflows/ci.yml)",
        ])
        self.assertEqual((gh.listed, gh.created), ([], []))

    def test_the_repository_watches_its_native_reference_tools(self):
        repos = {pin.repo for pin in self.releases.find_pins(self.releases.watched_texts(REPO))}
        self.assertLessEqual({"NationalSecurityAgency/ghidra", "renode/renode", "adoptium/temurin21-binaries",
                              "espressif/crosstool-NG"}, repos)


if __name__ == "__main__":
    unittest.main()
