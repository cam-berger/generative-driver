"""Report tools pinned to a GitHub release that trail their latest upstream release.

A pin is a link to https://github.com/<owner>/<repo>/releases/download/<tag>/... or
.../releases/tag/<tag> in a watched file: the workflows under .github/workflows and the native
reference setup in bench/README.md. Needs an authenticated GitHub CLI (gh).

    python .github/scripts/tool_releases.py                # print each pinned tool: current or behind
    python .github/scripts/tool_releases.py --open-issues  # also open one issue per new release

An issue is matched by title against open and closed issues, so each release raises one issue and
closing it acknowledges that release.
"""
import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote

ROOT = Path(__file__).resolve().parents[2]
WATCHED = (".github/workflows/*.yml", ".github/workflows/*.yaml", "bench/README.md")
PIN = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/(?:download|tag)/([^/\s)\"'<>#?]+)")


@dataclass(frozen=True)
class Pin:
    repo: str
    tag: str
    source: str


@dataclass
class Alert:
    repo: str
    latest: str
    stale: dict  # pinned tag -> sorted watched files that pin it

    @property
    def title(self):
        return f"New upstream release: {self.repo} {self.latest}"

    @property
    def body(self):
        pinned = "\n".join(f"- `{tag}` in " + ", ".join(f"`{source}`" for source in sources)
                           for tag, sources in self.stale.items())
        release = f"https://github.com/{self.repo}/releases/tag/{quote(self.latest, safe='')}"
        return (f"{self.repo} published [{self.latest}]({release}).\n\n"
                f"Pinned here:\n{pinned}\n\n"
                "Upgrading changes benchmark evidence. Update every pin above together with any SHA-256 beside it, "
                "search the repository for the old version to find unlinked mentions, run the affected workflows, "
                "and re-run native calibration before comparing results across versions.\n\n"
                "Close this issue once decided. The weekly check does not reopen it for this release.\n")


def watched_texts(root):
    """Map each watched file under root, as a POSIX relative path, to its text."""
    texts = {}
    for pattern in WATCHED:
        for path in sorted(root.glob(pattern)):
            texts[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return texts


def find_pins(texts):
    """Every distinct (repo, tag, file) release pin in the given texts."""
    pins = []
    for source, text in texts.items():
        for repo, tag in PIN.findall(text):
            pin = Pin(repo, unquote(tag), source)
            if pin not in pins:
                pins.append(pin)
    return pins


def check(pins, latest):
    """One Alert per pinned repo, sorted by repo; its stale map is empty when every pin is current."""
    files = defaultdict(lambda: defaultdict(set))
    for pin in pins:
        files[pin.repo][pin.tag].add(pin.source)
    checked = []
    for repo in sorted(files):
        newest = latest(repo)
        stale = {tag: sorted(sources) for tag, sources in sorted(files[repo].items()) if tag != newest}
        checked.append(Alert(repo, newest, stale))
    return checked


def alerts(pins, latest):
    """The Alerts for repos with at least one pin behind latest(repo)."""
    return [alert for alert in check(pins, latest) if alert.stale]


def run_gh(args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def main(argv=None, *, root=ROOT, gh=run_gh):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--open-issues", action="store_true", help="open one GitHub issue per new upstream release")
    options = parser.parse_args(argv)

    def latest(repo):
        return gh(["api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"]).strip()

    checked = check(find_pins(watched_texts(root)), latest)
    for alert in checked:
        for tag, sources in alert.stale.items():
            print(f"{alert.repo} {tag} -> {alert.latest} ({', '.join(sources)})")
        if not alert.stale:
            print(f"{alert.repo} {alert.latest} current")
    if options.open_issues:
        issues = json.loads(gh(["issue", "list", "--state", "all", "--limit", "1000", "--json", "title"]))
        raised = {issue["title"] for issue in issues}
        for alert in checked:
            if alert.stale and alert.title not in raised:
                gh(["issue", "create", "--title", alert.title, "--body", alert.body])
                print(f"opened issue: {alert.title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
