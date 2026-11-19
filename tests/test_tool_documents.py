"""Every relative link of the tool's documents resolves.

The tool's documents are the front page, the project's rules and glossary, the notice, the guides and decision
records under `docs/`, and both copies of the client skill. They are read as UTF-8 text from a Git checkout.
"""
import re
import unittest
from pathlib import Path

import generative_driver

REPO = Path(__file__).resolve().parents[1]
ROOT_DOCUMENTS = ('README.md', 'CONTRIBUTING.md', 'NOTICE', 'AGENTS.md', 'CONTEXT.md', 'STANDARDS.md')
DOCUMENT_FOLDERS = ('docs', 'docs/adr', 'docs/agents')
# The client skill as the package ships it, and its copy in the plugin of the checkout.
SKILL = 'skills/generative-driver/SKILL.md'
PACKAGED_SKILL = Path(generative_driver.__file__).resolve().parent / 'resources/client' / SKILL
PLUGIN_SKILL = REPO / 'plugins/generative-driver' / SKILL
LINK = re.compile(r'\]\(([^)\s]+)\)')


def anchors(text):
    """The anchors of the headings of the Markdown `text`, as a hosted page gives them: lower case, without
    punctuation, each space a hyphen."""
    headings = [line.lstrip('#').strip() for line in text.splitlines() if line.startswith('#')]
    return {re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-') for heading in headings}


def relative_links(text):
    """The targets of the Markdown links of `text` that name a file or an anchor of the checkout, as pairs of a line
    number and a target."""
    return [(number, target) for number, line in enumerate(text.splitlines(), 1) for target in LINK.findall(line)
            if not re.match(r'[a-z][a-z0-9+.-]*:', target)]


class DocumentsCase(unittest.TestCase):
    def documents(self):
        """The tool's documents, each as (its name, its file). A document of the checkout is named by its path from
        the root of the checkout, and the packaged client skill by its path in the package."""
        if not (REPO / '.git').exists():
            self.skipTest(f'The documents are read from a Git checkout, and {REPO} is none')
        names = [*ROOT_DOCUMENTS, *(path.relative_to(REPO).as_posix() for folder in DOCUMENT_FOLDERS
                                    for path in sorted((REPO / folder).glob('*.md')))]
        found = [(name, REPO / name) for name in names]
        found += [('plugins/generative-driver/' + SKILL, PLUGIN_SKILL),
                  ('generative_driver/resources/client/' + SKILL, PACKAGED_SKILL)]
        self.assertEqual([name for name, path in found if not path.is_file()], [])
        self.assertGreater(len(found), 15)
        return found


class LinkTests(DocumentsCase):
    """Every relative link of a tool's document resolves (11-B4)."""

    def test_11_B4_every_relative_link_resolves(self):
        broken, count = [], 0
        for name, path in self.documents():
            text = path.read_text(encoding='utf-8')
            for number, target in relative_links(text):
                count += 1
                file, _, anchor = target.partition('#')
                aim = (path.parent / file).resolve() if file else path
                if not aim.exists() or anchor and aim.suffix == '.md' and anchor not in anchors(
                        aim.read_text(encoding='utf-8')):
                    broken.append(f'{name}:{number} links {target}')
        self.assertGreater(count, 30)
        self.assertEqual(len(broken), 0, '\n'.join(broken))

    def test_11_B4_the_scan_reads_links_to_files_and_anchors_and_leaves_addresses(self):
        text = ('# Run dashboard\n\nSee [setup](setup.md), [the smoke](codex.md#verify-the-openai-cli) and '
                '[below](#open-it).\nSee [uv](https://docs.astral.sh/uv/) and <mailto:someone@example.org>.\n'
                '## Assistive protocol amendment — 2026-10-07\n')
        self.assertEqual(relative_links(text), [(3, 'setup.md'), (3, 'codex.md#verify-the-openai-cli'),
                                                (3, '#open-it')])
        self.assertEqual(anchors(text), {'run-dashboard', 'assistive-protocol-amendment--2026-10-07'})


if __name__ == '__main__':
    unittest.main()
