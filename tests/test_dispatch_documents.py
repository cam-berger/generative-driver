"""The documents state what leaves an effect uncertain (Spec 01 of the dispatch-outcome Run, 01-B18).

The guides, the design, the client skill in both copies and the decision record ADR 0011 are read as UTF-8 text from
the checkout. The example observation of the setup guide is parsed as JSON, and the command line shown with it is
given to the parser that `generative_driver.cli.main` builds, stopped right after the parse, so nothing of it runs.

No model run and no device.
"""
import json
import re
import unittest

from test_guides import REPO, code_blocks, command_arguments, paragraphs, refusal, sections, text_of

SKILLS = ('plugins/generative-driver/skills/generative-driver/SKILL.md',
          'src/generative_driver/resources/client/skills/generative-driver/SKILL.md')
RECORD = 'docs/adr/0011-what-leaves-an-effect-uncertain.md'
# The documents 01-B18 names, and the sentences of the earlier rule that none of them may hold.
DOCUMENTS = ('docs/extending-drivers-and-mcp.md', 'docs/setup.md', 'docs/design.md', 'docs/codex.md',
             'docs/client-interface.md') + SKILLS
OLD_RULE = ('tracked as effectful by the owner',
            'an unacknowledged opening blocks another resetting read',
            'blocks another resetting read and session release until reconciliation',
            'If the owner restarts with an unfinished invocation, it blocks the session',
            'an effect it might have had stays uncertain until you confirm it',
            'sealing or cleanup failure',
            'Cleanup failed; operator reconciliation required')
FIELDS = ('effect_resolution', 'confirmed_safe', 'source', 'message', 'prior_execution_stopped')


def flat(text):
    """`text` with every run of white space turned into one space."""
    return ' '.join(text.split())


def section_of(name, heading):
    """The lines under the second-level `heading` of the document `name`."""
    return dict(sections(text_of(name)))[heading]


class SetupStatementTests(unittest.TestCase):
    """The setup guide gives the operator's statement field by field, with an observation `respond` can send."""

    def test_01_B18_setup_example_observation_is_a_complete_statement_and_its_command_line_parses(self):
        blocks = [lines for _, lines in code_blocks('\n'.join(section_of('docs/setup.md', 'Recovery')))]
        examples = [json.loads('\n'.join(lines)) for lines in blocks
                    if lines and lines[0].lstrip().startswith('{') and 'effect_resolution' in '\n'.join(lines)]
        self.assertEqual(len(examples), 1, 'The recovery section shows one observation that states a resolution')
        [example] = examples
        # The command line and the MCP server send the observation alone, so it holds `effect_resolution` itself.
        self.assertEqual(example['effect_resolution'], 'confirmed_safe')
        self.assertIs(example['prior_execution_stopped'], True)
        for field in ('source', 'message'):
            with self.subTest(field=field):
                self.assertIsInstance(example[field], str)
                self.assertTrue(example[field].strip())
        responds = [command_arguments(line) for lines in blocks for line in lines
                    if command_arguments(line) and command_arguments(line)[0] == 'respond']
        self.assertEqual(len(responds), 1, 'The recovery section shows one `respond` command line')
        [respond] = responds
        self.assertIsNone(refusal(respond))
        self.assertEqual(respond[respond.index('--observation') + 1], 'observation.json')

    def test_01_B18_setup_names_each_field_the_session_call_and_what_a_restart_marks(self):
        recovery = flat('\n'.join(section_of('docs/setup.md', 'Recovery')))
        for told in [f'`{field}`' for field in FIELDS] + ['`device.reconcile(', '`respond`']:
            with self.subTest(told=told):
                self.assertIn(told, recovery)
        restart = [part for part in paragraphs(section_of('docs/setup.md', 'Recovery')) if 'restart' in part]
        self.assertTrue(restart, 'The recovery section says what a restart leaves')
        self.assertTrue(any('pending' in part and 'write' in part and 'actuat' in part for part in restart),
                        'A restart leaves a run uncertain only for a pending write or actuation')


class ExtensionGuideTests(unittest.TestCase):
    """The extension guide says what a resetting read needs, which write stays uncertain and what a restart leaves."""

    def test_01_B18_extension_a_read_over_a_resetting_channel_needs_the_grant_and_leaves_no_uncertain_effect(self):
        told = [part for part in paragraphs(text_of('docs/extending-drivers-and-mcp.md').splitlines())
                if '`resets_on_open`' in part]
        self.assertEqual(len(told), 1)
        self.assertIn('`reset` grant', told[0])
        self.assertIn('leaves no uncertain effect', told[0])

    def test_01_B18_extension_says_which_write_stays_uncertain(self):
        guide = flat(text_of('docs/extending-drivers-and-mcp.md'))
        for told in ('may have been sent', 'did not succeed', 'without a result', 'never sent'):
            with self.subTest(told=told):
                self.assertIn(told, guide)

    def test_01_B18_extension_a_blocked_session_without_an_uncertain_effect_is_closed_and_opened_again(self):
        told = [part for part in paragraphs(text_of('docs/extending-drivers-and-mcp.md').splitlines())
                if 'restart' in part and 'blocked' in part.lower()]
        self.assertTrue(told, 'A paragraph says what a session is after a restart')
        part = ' '.join(told)
        for named in ('`close()`', 'opened again', 'no statement', '`device.reconcile('):
            with self.subTest(named=named):
                self.assertIn(named, part)


class OtherDocumentTests(unittest.TestCase):
    """The design, the Codex guide and the client skill state the rule (01-B18)."""

    def test_01_B18_design_a_failed_cleanup_is_an_event_and_never_an_uncertain_effect(self):
        sentences = re.split(r'(?<=[.;])\s', flat(text_of('docs/design.md')))
        told = [sentence for sentence in sentences if '`cleanup.failed`' in sentence]
        self.assertEqual(len(told), 1, 'The design names the event of a failed cleanup once')
        self.assertIn('never', told[0])
        self.assertIn('uncertain', told[0])

    def test_01_B18_codex_an_abandoned_call_leaves_an_uncertain_effect_only_for_a_write_or_an_actuation(self):
        told = [part for part in paragraphs(section_of('docs/codex.md', 'When a run fails')) if 'abandoned' in part]
        self.assertEqual(len(told), 1)
        for named in ('uncertain', 'only', 'write', 'actuation'):
            with self.subTest(named=named):
                self.assertIn(named, told[0])

    def test_01_B18_skill_both_copies_name_the_fields_of_the_statement(self):
        for name in SKILLS:
            for field in FIELDS:
                with self.subTest(skill=name, field=field):
                    self.assertIn(field, text_of(name))

    def test_01_B18_no_document_holds_a_sentence_of_the_earlier_rule(self):
        held = [f'{name} holds "{old}"' for name in DOCUMENTS for old in OLD_RULE if old in flat(text_of(name))]
        self.assertEqual(held, [], '\n'.join(held))


class DecisionRecordTests(unittest.TestCase):
    """ADR 0011 records the five decisions with their context and their reason (01-B18)."""

    def test_01_B18_record_states_five_decisions_with_context_and_reason(self):
        self.assertTrue((REPO / RECORD).is_file(), RECORD + ' exists')
        text = text_of(RECORD)
        self.assertTrue(text.startswith('# 11. '))
        self.assertRegex(text, r'\nStatus: Accepted, \d{4}-\d{2}-\d{2}\.\n')
        decisions = re.findall(r'^(\d)\. ', text, re.MULTILINE)
        self.assertEqual(decisions, ['1', '2', '3', '4', '5'])
        body = flat(text)
        for told in ('twice', 'could not have happened', 'may really have gone out', 'silently', '`confirmed_safe`'):
            with self.subTest(told=told):
                self.assertIn(told, body)


if __name__ == '__main__':
    unittest.main()
