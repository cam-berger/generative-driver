"""Wording of the assistive protocol: worker-facing text, the synced client skill and the public docs."""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'src/generative_driver'
SKILL = 'src/generative_driver/resources/client/skills/generative-driver/SKILL.md'
INTERPRET = 'src/generative_driver/resources/toolchain/skills/interpret-interface/SKILL.md'
# Phrases of the gated protocol, in which the evaluator's diagnostic grades steered repair and stopped runs.
GRADED = ('diagnostic feedback', 'diagnostic observation', 'evaluator observation', 'evaluator feedback',
          'graded feedback', 'checks diagnostics', 'final failures are terminal', 'independent verdict',
          'diagnostic evaluation', 'diagnostic and final evaluation')
# The gated protocol's stage checks, which the current docs no longer describe as a gate.
GATED = ('checked stage progression', 'checked handoff', 'until its handoff is accepted')
# Docs that describe current behaviour; design.md, verification.md and the handoff keep dated historical sections.
CURRENT = ('README.md', 'bench/README.md', 'docs/support.md', 'docs/goose.md', 'docs/codex.md', 'docs/setup.md',
           'docs/dashboard.md', SKILL)
DOCS = (*CURRENT, 'docs/design.md', 'docs/verification.md', 'docs/implementation/benchmark-handoff.md')
CHANGE = (r'assistive/1', r'new series', r'not read-isolated', r'password-based')
LIMITS = (r'fresh native calibration', r'preserved checkout', r'time_policy\.probe', r'paused between',
          r'configurator[^.]*(holds no|never receives?)[^.]*password', r'instrument readings')
REQUIRED = {'bench/README.md': CHANGE + LIMITS, 'docs/design.md': CHANGE + LIMITS,
            'docs/verification.md': CHANGE[:3] + LIMITS[:2],
            'docs/implementation/benchmark-handoff.md': CHANGE[:2] + LIMITS[:1],
            'docs/goose.md': CHANGE + LIMITS[4:], 'docs/codex.md': CHANGE + LIMITS[4:],
            'README.md': CHANGE[2:], SKILL: CHANGE + (r'self-evidence', r'bench harness', r'notes',
                                                  r'[Nn]ever relay[^.]*`driver_respond`',
                                                  r'[Oo]nly a suite.s harness attaches one',
                                                  r'stopped single trial is not continued[^.]*start a new trial',
                                                  r'verdict pending until the bench harness posts',
                                                  r'`benchmark trial-resume` completes an evaluation that was unscored or not posted'),
            # A repair's feedback also carries the configurator's own validation defects, named by their source.
            INTERPRET: (r'model_contract', r'repair/DEFECTS\.json', r'never a grade')}


def _text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class WorkerFacingTextTests(unittest.TestCase):
    def test_worker_facing_text_names_no_evaluator_grade_as_feedback(self):
        from generative_driver.agents import STAGE_BOUNDARY
        from generative_driver.configurator import GROUND_OBJECTIVE
        from generative_driver.benchmark_support.emulated import INSTRUMENT
        texts = {'STAGE_BOUNDARY': STAGE_BOUNDARY, 'GROUND_OBJECTIVE': GROUND_OBJECTIVE, 'INSTRUMENT': INSTRUMENT}
        resources = PACKAGE / 'resources/toolchain'
        for path in (*sorted((resources / 'skills').rglob('*.md')), resources / 'tools.json'):
            texts[str(path.relative_to(ROOT))] = path.read_text(encoding='utf-8')
        for module in ('agents.py', 'benchmark_support/emulated.py', 'benchmark_support/cases.py',
                       'benchmark_support/physical.py', 'benchmark_support/candidate_context.py',
                       '_toolchain/interpret.py'):
            texts[module] = (PACKAGE / module).read_text(encoding='utf-8')
        # The configurator writes repair contexts and objectives that workers read; its comments are not read by them.
        tree = ast.parse((PACKAGE / 'configurator.py').read_text(encoding='utf-8'))
        texts['configurator.py strings'] = '\n'.join(' '.join(node.value.split()) for node in ast.walk(tree)
                                                     if isinstance(node, ast.Constant) and isinstance(node.value, str))
        for name, text in texts.items():
            for phrase in GRADED:
                with self.subTest(name=name, phrase=phrase):
                    self.assertNotIn(phrase, text.lower())


class PublicWordingTests(unittest.TestCase):
    def test_docs_and_skill_state_the_protocol_change_and_its_limits(self):
        for path, patterns in REQUIRED.items():
            text = ' '.join(_text(path).split())
            for pattern in patterns:
                with self.subTest(path=path, pattern=pattern):
                    self.assertRegex(text, pattern)

    def test_current_docs_promise_no_gated_feedback_or_terminal_final(self):
        for path in CURRENT:
            text = ' '.join(_text(path).lower().split())
            for phrase in GRADED + GATED:
                with self.subTest(path=path, phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_emulator_approval_is_described_for_any_emulated_case(self):
        for module in ('mcp.py', 'cli.py'):
            with self.subTest(module=module):
                self.assertNotRegex((PACKAGE / module).read_text(encoding='utf-8'), r'TQ9 (emulator )?tools')

    def test_reports_claim_only_what_they_carry(self):
        from generative_driver.benchmark_support.evidence import public_v3_report
        handoff = {'stage': 'probe', 'revision': 0, 'artifacts': [{'path': 'model', 'sha256': 'a' * 64}],
                   'notes': {'path': 'notes.json', 'sha256': 'b' * 64, 'inline': {'open_questions': ['why']}}}
        report = public_v3_report({'run_id': 'run', 'accepted_handoffs': [handoff], 'stages': {'probe': {
            'attempts': [{'accepted': True, 'notes': handoff['notes']}]}}})
        self.assertNotIn('notes', str(report))
        self.assertNotIn('b' * 64, str(report))
        text = ' '.join(_text('bench/README.md').split())
        retained = re.search(r'Reports retain[^.]*\.', text).group(0)
        self.assertNotIn('notes', retained)
        self.assertRegex(text, r'notes[^.]*sealed in the trial.s run evidence')
        self.assertRegex(text, r'`driver_result` returns the notes')

    def test_no_doc_claims_a_sandbox_or_isolation(self):
        for path in DOCS:
            for sentence in re.split(r'(?<=[.!?])\s+', ' '.join(_text(path).split())):
                if re.search(r'sandbox|(?<!bank )isolat', sentence, re.I):
                    with self.subTest(path=path, sentence=sentence[:120]):
                        self.assertRegex(sentence, r'(?i)\b(not|no|never|unverified|until)\b')


if __name__ == '__main__':
    unittest.main()
