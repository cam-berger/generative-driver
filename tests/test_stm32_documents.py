"""What the STM32 flash read left as it was, and what its documents say.

Feature 01 of the stm32-acquire Run (docs/features/stm32-acquire/01-stm32-flash-read.md), 01-B16 and 01-B17. The
Feature adds one command, one acquire tool and one entry of `doctor`. Everything that existed keeps its shape: the
fifteen commands with their arguments, the client's tools, the client interface's document, the description of every
tool that existed, and the extras `doctor` lists.

The documents are read from the checkout. The MCP server is started once, over stdio, to list its tools. One test
starts a run in a temporary home, with stage workers that do nothing. Temporary homes only; no model, no hardware and
no program of ST's.
"""
import hashlib
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import generative_driver
from input_path_fixtures import RunCase
from test_tool_commands import TOOLS, listed_tools, one_line, taken, typed, usage
from test_tool_documents import benchmark_words

REPO = Path(__file__).resolve().parents[1]
# The fifteen commands `generative-driver` had before the Feature, in the order its help gave them, each with the
# arguments its usage showed.
EARLIER_COMMANDS = {
    'doctor': '[--json]',
    'configure': '--executor {codex} --command EXECUTABLE [--argument ARGUMENT] [--model MODEL] [--provider PROVIDER] '
                 '[--reasoning-effort REASONING_EFFORT]',
    'setup': '--host {codex} --output OUTPUT',
    'service': '{start,status,stop}',
    'smoke': '',
    'dashboard': '[--port PORT] [--no-open] run_id',
    'run': '--spec SPEC',
    'tools': '',
    'tool': '--arguments ARGUMENTS name',
    'status': 'run_id',
    'events': '[--after AFTER] run_id',
    'result': 'run_id',
    'cancel': 'run_id',
    'resume': '[--budget-seconds BUDGET_SECONDS] [--budget-reason BUDGET_REASON] '
              '[--approve-emulator-tools | --approve-bound-device-tools] run_id',
    'respond': '--observation OBSERVATION run_id',
}
NEW_COMMAND, NEW_TOOL = 'configure-program', 'acquire_stm32_flash'
# The SHA-256 of `docs/client-interface.md` as UTF-8, of the description of every tool but `acquire_document` as the
# base commit of Feature 07 of the kit fixes listed them, and of the description of `acquire_document`: each as it was
# at the base commit of this Feature.
CLIENT_INTERFACE_SHA256 = '01bde7ab829f83fa8eac430f04003366f3b5126d71f69bb3525417869ba5a634'
BASE_TOOLS_SHA256 = '16b20a898c1024e0c77601ab9c14eba3dd99f707eb7d111d0cce8531be02d7d6'
DOCUMENT_TOOL_SHA256 = '44a510588e47775810f87c04bcff425200ac4d33241bd20051331b46d8f1d74a'
EXTRAS = ['esptool', 'pyftdi', 'pypdf', 'pyserial', 'pyusb']


def canonical_sha256(value):
    """The SHA-256 of `value` as canonical JSON, as the pin of the earlier tool descriptions is taken."""
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


class ShapeKeptTests(unittest.TestCase):
    """What existed keeps its shape (01-B17)."""

    def test_01_B17_stm32_the_fifteen_commands_keep_their_arguments_and_the_new_one_follows_configure(self):
        self.assertEqual(len(EARLIER_COMMANDS), 15)
        listed = usage(typed('--help').printed).partition('{')[2].partition('}')[0].split(',')
        self.assertEqual([command for command in listed if command != NEW_COMMAND], list(EARLIER_COMMANDS))
        self.assertEqual(listed[listed.index('configure') + 1], NEW_COMMAND)
        self.assertEqual(len(listed), 16)
        for command, arguments in EARLIER_COMMANDS.items():
            with self.subTest(command=command):
                helped = typed(command, '--help')
                self.assertEqual(helped.status, 0, helped.errors)
                self.assertEqual(usage(helped.printed),
                                 one_line(f'usage: generative-driver {command} [-h] {arguments}'))

    def test_01_B17_stm32_the_clients_tools_keep_their_arguments_and_descriptions(self):
        listed = listed_tools()
        self.assertEqual([name for name, _, _ in listed], list(TOOLS))
        self.assertEqual(len(listed), 8)
        for name, description, schema in listed:
            said, parameters = TOOLS[name]
            with self.subTest(tool=name):
                self.assertEqual(one_line(description), said)
                self.assertEqual(taken(schema), parameters)
                self.assertEqual(list(schema['properties']), list(parameters))

    def test_01_B17_stm32_the_client_interface_document_keeps_its_hash(self):
        document = REPO / 'docs' / 'client-interface.md'
        if not document.is_file():
            self.skipTest(f'The document is read from a checkout, and {document} is not there')
        text = document.read_text(encoding='utf-8')
        self.assertEqual(hashlib.sha256(text.encode('utf-8')).hexdigest(), CLIENT_INTERFACE_SHA256)

    def test_01_B17_stm32_every_tool_that_existed_keeps_its_description_and_one_tool_is_new(self):
        from generative_driver.toolkit import list_tools
        tools = list_tools()
        earlier = [tool for tool in tools if tool['name'] not in ('acquire_document', NEW_TOOL)]
        self.assertEqual(canonical_sha256(earlier), BASE_TOOLS_SHA256)
        self.assertEqual(canonical_sha256([tool for tool in tools if tool['name'] == 'acquire_document']),
                         DOCUMENT_TOOL_SHA256)
        # The one new tool stands last, so the order of the others is the one they had.
        self.assertEqual([tool['name'] for tool in tools][-2:], ['acquire_document', NEW_TOOL])

    def test_01_B17_stm32_doctor_lists_the_extras_it_listed(self):
        from generative_driver.setup import doctor
        with tempfile.TemporaryDirectory(prefix='stm32 extras ') as temporary:
            report = doctor(Path(temporary) / 'home')
        self.assertEqual(sorted(report['extras']), EXTRAS)
        self.assertEqual(sorted(report), ['core', 'dependencies', 'extras', 'home', 'plugin', 'serial_ports',
                                          'serial_ports_available'])


SKILL = 'skills/generative-driver/SKILL.md'
# The section of `docs/setup.md` on the read, and what the Feature has it say, each as a whole line of the section.
SETUP_SECTION = '## Read STM32 firmware through a debug probe'
PROGRAM_PLACE = '/absolute/path/to/STM32_Programmer_CLI'
CONFIGURE_LINE = ('python -m generative_driver configure-program --program stm32-programmer --command "'
                  + PROGRAM_PLACE + '"')
BINDING_LINE = '{"binding": {"debug_probe": {"kind": "stlink", "serial_number": "..."}}}'
SETUP_LINES = {
    'the program is installed separately': (
        'A run can read the flash of an STM32 through an ST-LINK debug probe. The read starts `STM32_Programmer_CLI`, '
        'the command-line program of STM32CubeProgrammer, which is a separate installation from ST. Save its command '
        'once:'),
    'configure-program': CONFIGURE_LINE,
    'a binding with debug_probe': BINDING_LINE,
    'what the reset grant changes': (
        '- Without the `reset` grant the tool neither resets nor halts the chip. With it the chip is reset to connect '
        'and again after the read.'),
    'a stopped read can leave the chip halted': (
        '- A read that the configurator stops under the `reset` grant (a cancel, a shutdown, a spent budget or the '
        'limit of a tool call) can leave the chip halted until it is reset.'),
    'one such run runs at a time': (
        '- One run with a declared probe runs at a time. A second is refused until the first has ended.'),
    'a read-protected chip is not read': '- A read-protected chip is not read. The run goes on without an image.',
    'a probe on a read-protected chip, not checked on a chip': (
        '- On many STM32 families, attaching a probe to a read-protected chip stops its application until the next '
        'power cycle. This has not been checked on a chip.'),
}
# The sentence of `docs/support.md` on the read, in the row on firmware.
SUPPORT_SENTENCE = ('STM32 flash is read through an ST-LINK probe with STM32CubeProgrammer; that read has scripted '
                    'evidence only.')
# The one sentence the skill's step on binding gains, and the SHA-256 of the skill's text before it.
SKILL_SENTENCE = ('For an STM32 with an ST-LINK probe attached, add `"debug_probe": {"kind": "stlink"}` to the binding '
                  'so that acquisition reads its firmware; that read resets the chip only under the `reset` grant.')
EARLIER_SKILL_SHA256 = 'b803f831bd4435176bc3c08d4ab7aa08437de6c8f73ae519fe6943d0487ea705'


class DocumentTests(unittest.TestCase):
    """The documents say how to use the read (01-B16)."""

    def document(self, name):
        """The text of the document `name` of the checkout."""
        path = REPO / name
        if not path.is_file():
            self.skipTest(f'The documents are read from a checkout, and {path} is not there')
        return path.read_text(encoding='utf-8')

    def setup_section(self):
        """The lines of the section of `docs/setup.md` on the read."""
        lines = self.document('docs/setup.md').splitlines()
        self.assertEqual(lines.count(SETUP_SECTION), 1)
        section = lines[lines.index(SETUP_SECTION) + 1:]
        return section[:next(index for index, line in enumerate(section) if line.startswith('## '))]

    def test_01_B16_stm32_setup_says_how_to_configure_the_program_declare_the_probe_and_what_a_read_does(self):
        section = self.setup_section()
        self.assertEqual({said: line in section for said, line in SETUP_LINES.items()},
                         {said: True for said in SETUP_LINES})
        # The section follows the one on optional hardware tools.
        headings = [line for line in self.document('docs/setup.md').splitlines() if line.startswith('## ')]
        self.assertEqual(headings[headings.index(SETUP_SECTION) - 1:][:3],
                         ['## Add optional hardware tools', SETUP_SECTION, '## Recovery'])

    def test_01_B16_stm32_the_command_and_the_binding_setup_shows_are_ones_the_tool_takes(self):
        from generative_driver.configurator import Controller
        self.assertIn(CONFIGURE_LINE, self.setup_section())
        temporary = tempfile.TemporaryDirectory(prefix='stm32 setup ')
        self.addCleanup(temporary.cleanup)
        program = Path(temporary.name).resolve() / 'STM32_Programmer_CLI'
        program.write_text('never started\n', encoding='utf-8')
        words = [str(program) if word == PROGRAM_PLACE else word for word in shlex.split(CONFIGURE_LINE)]
        self.assertEqual(words[:3], ['python', '-m', 'generative_driver'])
        home = Path(temporary.name) / 'home'
        saved = typed('--home', str(home), *words[3:])
        self.assertEqual((saved.status, saved.errors), (0, ''))
        self.assertEqual(json.loads(saved.printed)['configuration'], {'command': [str(program)]})
        # The binding the section shows declares a probe whose serial number is a place to fill in. A start in the
        # home that command configured refuses it as shown, and takes it once the number is filled in.
        binding = json.loads(BINDING_LINE)['binding']
        self.assertEqual(binding, {'debug_probe': {'kind': 'stlink', 'serial_number': '...'}})
        controller = Controller(home)
        self.addCleanup(controller.close)
        start = {'goal': 'Read the firmware of the device', 'executor_config': {'command': ['never-launched']}}
        with self.assertRaises(ValueError) as refused:
            controller.call('start', {**start, 'binding': binding})
        self.assertEqual(str(refused.exception), 'binding.debug_probe.serial_number must be 1 to 64 letters and digits')
        binding['debug_probe']['serial_number'] = '066DFF485550755187121723'

        def execute(request, config, cancel):
            """A stage worker that does nothing and reports completed: no model runs."""
            return {'runtime': 'codex', 'status': 'completed', 'reason': None, 'elapsed_seconds': 0, 'usage': None,
                    'report': {'status': 'completed', 'summary': 'scripted ' + request.stage, 'artifacts': [],
                               'checks': [], 'unresolved': []}}
        with patch('generative_driver.configurator.execute', execute):
            started = controller.call('start', {**start, 'binding': binding})
            self.assertEqual((started['ok'], started['duplicate']), (True, False))
            RunCase.stopped(controller, started)

    def test_01_B16_stm32_support_names_the_read_in_the_firmware_row_with_scripted_evidence_only(self):
        rows = [line for line in self.document('docs/support.md').splitlines() if line.startswith('| Firmware |')]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0].endswith(' ' + SUPPORT_SENTENCE + ' |'), rows[0])

    def test_01_B16_stm32_the_skills_step_on_binding_gains_one_sentence_and_both_copies_stay_identical(self):
        packaged = Path(generative_driver.__file__).resolve().parent / 'resources/client' / SKILL
        text = packaged.read_text(encoding='utf-8')
        self.assertEqual(text.count(SKILL_SENTENCE), 1)
        [step] = [line for line in text.splitlines() if SKILL_SENTENCE in line]
        self.assertTrue(step.startswith('2. Establish objective, inputs, binding and effects. '), step)
        # Without the sentence the skill is the text it was.
        earlier = text.replace(SKILL_SENTENCE + ' ', '')
        self.assertEqual(hashlib.sha256(earlier.encode('utf-8')).hexdigest(), EARLIER_SKILL_SHA256)
        plugin = REPO / 'plugins/generative-driver' / SKILL
        if not plugin.is_file():
            self.skipTest(f'The plugin is read from a checkout, and {plugin} is not there')
        self.assertEqual(plugin.read_text(encoding='utf-8'), text)

    def test_01_B16_stm32_the_new_text_holds_no_word_the_document_scan_bans(self):
        section = self.setup_section()
        self.assertGreater(len(section), 15)
        self.assertEqual(benchmark_words('docs/setup.md', '\n'.join(section)), [])
        self.assertEqual(benchmark_words('docs/support.md', SUPPORT_SENTENCE), [])
        self.assertEqual(benchmark_words('plugins/generative-driver/' + SKILL, SKILL_SENTENCE), [])
        from generative_driver.toolkit import list_tools
        [described] = [tool for tool in list_tools() if tool['name'] == NEW_TOOL]
        self.assertEqual(benchmark_words('tools.json', json.dumps(described)), [])


if __name__ == '__main__':
    unittest.main()
