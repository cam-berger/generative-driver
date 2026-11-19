"""The tool's documents describe the tool alone.

Feature 11 of the untangle Run (docs/features/untangle/11-tool-alone.md), 11-B4 and 11-B6: no line of a tool's
document holds a word of the benchmark's, every relative link of one resolves, and the client skill is the text the
Feature gives. The tool's documents are the front page, the project's rules and glossary, the notice, the guides and
decision records under `docs/`, and both copies of the client skill.

Feature 12 of the untangle Run parked the benchmark in a folder here; on 2026-11-19 the owner had it removed from
this repository. So the front page has no paragraph that points to it, no link of a document leads to it, and the
two lines of the rules that give the test command name the tool's tests alone. Those two lines are taken from
`tests/test_tool_alone.py`, the one tool test file that may hold the benchmark's names: 11-B8 holds them.

The Spec leaves 11-B5 (the design) and 11-B8 (the owner's three files) to the Reviewer. Each still has a test here,
because the Done check asks for one by name: the design by its sections, items, diagram and new sentences, and the
owner's files by the lines the Spec gives. Whether every other sentence stayed is read from the diff.

The documents are read as UTF-8 text. The MCP server is started once, over stdio, to list its tools.
"""
import hashlib
import re
import unittest
from pathlib import Path

import generative_driver
from test_tool_alone import REMOVED, TEST_COMMAND, TEST_FILES
from test_tool_commands import listed_tools

REPO = Path(__file__).resolve().parents[1]
ROOT_DOCUMENTS = ('README.md', 'CONTRIBUTING.md', 'NOTICE', 'AGENTS.md', 'CONTEXT.md', 'STANDARDS.md')
DOCUMENT_FOLDERS = ('docs', 'docs/adr', 'docs/agents')
# The client skill as the package ships it, and its copy in the plugin of the checkout.
SKILL = 'skills/generative-driver/SKILL.md'
PACKAGED_SKILL = Path(generative_driver.__file__).resolve().parent / 'resources/client' / SKILL
PLUGIN_SKILL = REPO / 'plugins/generative-driver' / SKILL
# The words no line of a tool's document holds, in any letter case.
WORDS = ('bench', 'harness', 'suite', 'trial', 'scorecard', 'scoring set', 'answer key', 'answer vault',
         'groundtruth', 'leak check', 'pass rate', 'mutant', 'renode')
# The one place the scan leaves out, by the document's name and its whole text: the sentence of the rules on the
# serial suite of tests.
SERIAL_SUITE = ('Each test module runs in its own process, eight at a time, because the serial suite takes about 13 '
                'minutes and the Done check stops at 9.')
LEFT_OUT = {'STANDARDS.md': (SERIAL_SUITE,)}
LINK = re.compile(r'\]\(([^)\s]+)\)')
# The client skill as the Feature gives it, by the SHA-256 of its text as UTF-8.
SKILL_SHA256 = 'b803f831bd4435176bc3c08d4ab7aa08437de6c8f73ae519fe6943d0487ea705'
# What the Feature leaves of `docs/design.md` (11-B5): its sections, the titles of the amendment's items, the three
# rows of its table of seams, the amendment's diagram, and the sentences the Feature words anew.
DESIGN_SECTIONS = ['Original design — 2026-10-03', 'Workflow', 'Device access and recovery',
                   'Repository and integrations', 'Driver contracts and generated MCP — 2026-10-28', 'Evaluation',
                   'Public test seams', 'Assistive protocol amendment — 2026-10-07']
DESIGN_ITEMS = ['Every stage hands off.', 'Only safety, integrity, operator and host faults stop a run.',
                'The device drives repair.', 'One final verdict.', 'Recovery.', 'The configurator holds no password.',
                'One posted verdict after the run.', 'Resume and readings.', 'No stage verdicts.',
                'Limits of this protocol.']
DESIGN_SEAMS = ['Installed CLI/MCP', 'Configurator/workers', 'Runtime/package']
DESIGN_DIAGRAM = [
    '```mermaid', 'flowchart LR',
    '    H["Client: owns the device, keeps the token"] --> P["Device provider"]',
    '    H -->|"device descriptor, firmware, mission, token sha256"| C["Configurator: no password, no case"]',
    '    C -->|"control endpoint: status, readings, call window, replacement, release"| P',
    '    C -->|"assignments, gateway tools, prior notes"| W["Stage workers"]',
    '    H -->|"after the run: equivalence/2 with record_evaluation"| C', '```']
DESIGN_SENTENCES = (
    'Confirmed 2026-10-03. Install Generative Driver,',
    'A fresh agent must use the emitted package successfully. Worker completion',
    '`docs/` and `tests/` hold guides and behavior tests.',
    'Follow [setup](setup.md) through runtime configuration and dependency checks before a device run.',
    'Fresh reuse uses that catalog through configurator-owned dispatch, with or without a supplied device.',
    'Earlier runs keep their recorded meaning; resuming one is refused.',
    'The configurator coordinates; for a run on a supplied device, a client of the interface plays the operator:',
    'The run releases the device when it ends, once no live call is inside its window; the client stops it.',
    'A client posts the verdict with `record_evaluation`.',
    'A posted record has the status `graded`, `no-driver`, `unknown` or `unscored`; an `unscored` one is recorded as '
    'an event and leaves the run awaiting its verdict.',
    'A verdict is taken only for a `completed` run.',
    "Readings leave the device's provider as `instrument-observation/1` values and units; the run records the "
    "device's session, binding and recovery receipts.",
    'The runs database stays readable. Nothing limits what a driver prints or a worker\'s disk or memory use. Effect')
# The texts the Feature gives for the owner's three files (11-B8), each a whole line of its file.
AGENTS_LINES = (
    '- Develop through three public seams: CLI/MCP, configurator/workers, runtime/package. For each code change, '
    'observe one behavior test fail, implement it, then observe it pass.',
    '- Fresh reuse receives only the package and objective. Under `assistive/1`, stages hand off with notes; device '
    'differences drive bounded repairs, then continue with the best revision. Record one final equivalence verdict: '
    "the configurator's own, labelled self-evidence, or the one posted through the client interface for a run on a "
    'supplied device. Never silently retry an uncertain write.',
    '- Keep worker claims, accepted handoffs and posted verdicts separate. Hashes establish provenance and '
    'integrity.',
    '- The configurator and workers receive neither passwords nor their paths or plaintext answers. Describe '
    'isolation accurately: workers are not read-isolated, and hashes are not a sandbox. Distinguish model runs, '
    'scripted tests, replay, emulation and physical evidence.',
    '- Keep docs concise, concrete and executable. Define terms on first use; remove repeated explanations. Preserve '
    'notices and private research artifacts. Launch model runs or physical operations only when explicitly assigned '
    'by the primary agent.')
# The two lines in the middle give the test command and the test files of the tool alone.
STANDARDS_LINES = (
    '- The configurator and workers never receive a password, its path or a plaintext answer.',
    TEST_COMMAND,
    TEST_FILES,
    '- Tests use temporary homes. They never touch a real password, device or model run.')
CONTEXT_LINES = (
    "| Equivalence | One final verdict: the configurator's own, with basis `self-evidence`, or the one posted through "
    'the client interface for a run on a supplied device, with basis `evaluator`. Completion alone does not mean '
    'success. |',
    '| Effect grant | Permission for a driver call to write, actuate or reset; reads need none. The operator grants a '
    "run's effects; a call needing an ungranted effect is refused before any device I/O. |",
    '| Instrument observation | Device-state readings visible to workers, never grades. |',
    '| Public mission | Required task meanings, inputs, output units and the objective for fresh reuse. |',
    'Functional compatibility compares required outputs/effects, not source code. Hashes prove identity/integrity, '
    'not physical truth or access control. Keep passwords and answers out of worker inputs; the configurator holds '
    'neither.',
    '| Supplied device | A device that a client of the [client interface](docs/client-interface.md) owns, starts and '
    'stops, and describes to the service. A run on one ends `completed` and waits for the one verdict that client '
    'posts. |')


def benchmark_words(name, text):
    """The words of the benchmark's that the document `name` with the text `text` holds, as pairs of a line number,
    counted from 1, and a word. The places the scan leaves out of that document are not read."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for place in LEFT_OUT.get(name, ()):
            line = line.replace(place, '')
        found += [(number, word) for word in WORDS if word in line.lower()]
    return found


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


class NoBenchmarkTextTests(DocumentsCase):
    """The tool's documents hold no benchmark text (11-B4)."""

    def test_11_B4_no_line_of_a_tools_document_holds_a_word_of_the_benchmarks(self):
        holding = [f'{name}:{number} holds {word}' for name, path in self.documents()
                   for number, word in benchmark_words(name, path.read_text(encoding='utf-8'))]
        self.assertEqual(len(holding), 0, '\n'.join(holding))

    def test_11_B4_the_scan_sees_each_word_in_any_letter_case(self):
        for word in WORDS:
            for written in (word, word.upper(), word.title(), f'a {word}s line'):
                with self.subTest(word=written):
                    self.assertEqual(benchmark_words('docs/setup.md', f'A line.\nIt says {written} here.\n'),
                                     [(2, word)])
        self.assertEqual(benchmark_words('docs/setup.md', '# Setup\n\nInstall the tool and run the smoke.\n'), [])
        # Every word of a line is named, and a word inside a longer one counts.
        self.assertEqual(benchmark_words('NOTICE', 'See bench/groundtruth/README.md and the Renode notice.'),
                         [(1, 'bench'), (1, 'groundtruth'), (1, 'renode')])
        self.assertEqual(benchmark_words('docs/codex.md', 'For emulated benchmarks, see the trials.'),
                         [(1, 'bench'), (1, 'trial')])

    def test_11_B4_the_scan_leaves_out_the_one_named_place_and_nothing_else(self):
        self.assertEqual(sum(len(places) for places in LEFT_OUT.values()), 1)
        self.assertEqual(benchmark_words('docs/setup.md', SERIAL_SUITE), [(1, 'suite')])
        self.assertEqual(benchmark_words('STANDARDS.md', f'{SERIAL_SUITE} `PYTHONPATH` is absolute.\n'), [])
        # The place is left out of its own document alone, and only by its whole text.
        self.assertEqual(benchmark_words('README.md', SERIAL_SUITE), [(1, 'suite')])
        self.assertEqual(benchmark_words('STANDARDS.md', SERIAL_SUITE.replace('13 minutes', '14 minutes')),
                         [(1, 'suite')])
        # A word beside the place that is left out is still seen.
        self.assertEqual(benchmark_words('STANDARDS.md', f'{SERIAL_SUITE} A trial runs.'), [(1, 'trial')])
        # A paragraph that points to the benchmark is seen in the front page as anywhere else.
        self.assertEqual(benchmark_words('README.md', 'Firmware bench evaluates this tool.'), [(1, 'bench')])
        # The two lines of the rules that give the test command hold no word of the benchmark's and need no place.
        for line in (TEST_COMMAND, TEST_FILES):
            with self.subTest(line=line[:14]):
                self.assertEqual(benchmark_words('STANDARDS.md', line), [])
                self.assertEqual(benchmark_words('docs/setup.md', line), [])


class LinkTests(DocumentsCase):
    """Every relative link of a tool's document resolves, and none leads to the benchmark's removed folder (11-B4)."""

    def test_11_B4_every_relative_link_resolves_and_none_leads_to_the_benchmark(self):
        broken, leading, count = [], [], 0
        for name, path in self.documents():
            text = path.read_text(encoding='utf-8')
            for number, target in relative_links(text):
                count += 1
                file, _, anchor = target.partition('#')
                aim = (path.parent / file).resolve() if file else path
                if not aim.exists() or anchor and aim.suffix == '.md' and anchor not in anchors(
                        aim.read_text(encoding='utf-8')):
                    broken.append(f'{name}:{number} links {target}')
                if aim.is_relative_to(REPO / REMOVED):
                    leading.append((name, text.splitlines()[number - 1]))
        self.assertGreater(count, 30)
        self.assertEqual(len(broken), 0, '\n'.join(broken))
        self.assertEqual(leading, [])

    def test_11_B4_the_scan_reads_links_to_files_and_anchors_and_leaves_addresses(self):
        text = ('# Run dashboard\n\nSee [setup](setup.md), [the smoke](codex.md#verify-the-openai-cli) and '
                '[below](#open-it).\nSee [uv](https://docs.astral.sh/uv/) and <mailto:someone@example.org>.\n'
                '## Assistive protocol amendment — 2026-10-07\n')
        self.assertEqual(relative_links(text), [(3, 'setup.md'), (3, 'codex.md#verify-the-openai-cli'),
                                                (3, '#open-it')])
        self.assertEqual(anchors(text), {'run-dashboard', 'assistive-protocol-amendment--2026-10-07'})


class ClientSkillTests(unittest.TestCase):
    """The client skill is the tool's alone (11-B6)."""

    def skill(self):
        """The text of the client skill as the package ships it."""
        self.assertTrue(PACKAGED_SKILL.is_file(), f'{PACKAGED_SKILL} is missing')
        return PACKAGED_SKILL.read_text(encoding='utf-8')

    def test_11_B6_both_copies_of_the_client_skill_hold_the_text_of_the_feature_and_are_equal(self):
        text = self.skill()
        lines = text.splitlines()
        self.assertEqual(lines[:5], [
            '---', 'name: generative-driver',
            'description: Generate a device interface from firmware, or reconnect to an existing driver workflow.',
            '---', ''])
        self.assertEqual([line[:3] for line in lines if re.match(r'\d\. ', line)], ['1. ', '2. ', '3. ', '4. ', '5. '])
        self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), SKILL_SHA256)
        if not PLUGIN_SKILL.is_file():
            self.skipTest(f'The plugin is read from a checkout, and {PLUGIN_SKILL} is not there')
        self.assertEqual(PLUGIN_SKILL.read_text(encoding='utf-8'), text)

    def test_11_B6_every_tool_the_skill_names_is_a_tool_of_the_mcp_server(self):
        offered = {name for name, _, _ in listed_tools()}
        self.assertEqual(len(offered), 8)
        named = sorted(set(re.findall(r'\bdriver_\w+', self.skill())))
        self.assertEqual(named, ['driver_doctor', 'driver_respond', 'driver_result', 'driver_start'])
        self.assertEqual([name for name in named if name not in offered], [])


class DesignTests(unittest.TestCase):
    """`docs/design.md` is the tool's design (11-B5)."""

    def setUp(self):
        design = REPO / 'docs' / 'design.md'
        if not design.is_file():
            self.skipTest(f'The design is read from a checkout, and {design} is not there')
        self.text = design.read_text(encoding='utf-8')
        self.lines = self.text.splitlines()

    def test_11_B5_the_design_keeps_its_sections_and_the_amendment_the_items_of_the_tool(self):
        self.assertEqual([line[3:] for line in self.lines if line.startswith('## ')], DESIGN_SECTIONS)
        self.assertEqual(re.findall(r'^- \*\*([^*]+)\*\* ', self.text, flags=re.MULTILINE), DESIGN_ITEMS)
        seams = self.lines[self.lines.index('| Seam | Required behavior |') + 2:]
        self.assertEqual([row.split('|')[1].strip() for row in seams[:seams.index('')]], DESIGN_SEAMS)
        # The section on evaluation holds one paragraph and no table.
        section = self.lines[self.lines.index('## Evaluation') + 1:self.lines.index('## Public test seams')]
        self.assertEqual([line[:22] for line in section if line], ['Functional equivalence'])

    def test_11_B5_the_amendments_diagram_and_the_reworded_sentences_are_those_of_the_feature(self):
        start = self.lines.index('flowchart LR') - 1
        self.assertEqual(self.lines[start:start + len(DESIGN_DIAGRAM)], DESIGN_DIAGRAM)
        # The first diagram keeps the tool's five arrows.
        first = self.lines[self.lines.index('flowchart TD') + 1:]
        self.assertEqual(len(first[:first.index('```')]), 5)
        self.assertEqual([sentence for sentence in DESIGN_SENTENCES if sentence not in self.text], [])


class OwnerFilesTests(unittest.TestCase):
    """The owner's three files hold the texts the Feature gives (11-B8)."""

    def test_11_B8_the_owners_three_files_hold_the_lines_the_feature_gives(self):
        if not (REPO / 'STANDARDS.md').is_file():
            self.skipTest(f'The rules are read from a checkout, and {REPO / "STANDARDS.md"} is not there')
        for name, given in (('AGENTS.md', AGENTS_LINES), ('STANDARDS.md', STANDARDS_LINES),
                            ('CONTEXT.md', CONTEXT_LINES)):
            lines = (REPO / name).read_text(encoding='utf-8').splitlines()
            for line in given:
                with self.subTest(file=name, line=line[:60]):
                    self.assertIn(line, lines)

    def test_11_B8_the_glossary_keeps_the_tools_rows_and_the_rules_the_tools_test_folder(self):
        if not (REPO / 'STANDARDS.md').is_file():
            self.skipTest(f'The rules are read from a checkout, and {REPO / "STANDARDS.md"} is not there')
        rows = [line for line in (REPO / 'CONTEXT.md').read_text(encoding='utf-8').splitlines()
                if line.startswith('| ')]
        # The heading row and the 24 terms that are left: 19 kept, 4 reworded, 1 new, after 31 went.
        self.assertEqual(len(rows), 25)
        self.assertEqual(rows.index(CONTEXT_LINES[5]), [row.split('|')[1].strip() for row in rows].index(
            'Stand-in device') + 1)
        rules = (REPO / 'STANDARDS.md').read_text(encoding='utf-8').splitlines()
        self.assertEqual([line for line in rules if line.startswith(('Test command:', 'Test files:'))],
                         [STANDARDS_LINES[1], STANDARDS_LINES[2]])
        # Each of the two lines names one test folder, the tool's.
        for line in STANDARDS_LINES[1:3]:
            self.assertEqual(re.findall(r'[\w-]*/?tests/test_\*\.py', line), ['tests/test_*.py'])


if __name__ == '__main__':
    unittest.main()
