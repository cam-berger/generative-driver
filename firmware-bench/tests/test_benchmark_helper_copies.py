"""The grader's own helpers beside the tool's: they give the same answers, and grading code stays in the benchmark's
folder.

Feature 02 of the untangle Run (docs/features/untangle/02-shared-helpers.md), 02-B3, 02-B5 and 02-B7. Grading
code keeps `integrity.py` and `labels.py` in `benchmark_support`, and the tool has its own `integrity` and
`verdict_labels`. The grader checks again the hashes the tool recorded, so its helpers must agree with the tool's.
This module is a benchmark item: it goes where the benchmark goes.

Temporary directories and homes only; no model, no hardware, no real password.
"""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_evaluation import PASS, TOKEN, HarnessTrialFixture, reference
import generative_driver
from generative_driver import integrity, replay, verdict_labels
from generative_driver.benchmark_support import integrity as grader_integrity, labels as grader_labels
from generative_driver.benchmark_support.grader_identity import grader_closure


def answer(helper, argument):
    """What `helper` answers for `argument`: the value it returns, or the type and the message of what it raises."""
    try:
        return 'returns', helper(argument)
    except Exception as error:
        return 'raises', type(error), str(error)


def differing(*reasons):
    """A record that is not equivalent, with one difference for each of `reasons`."""
    return {'equivalent': False, 'diff': [{'id': f'check-{index}', 'reason': reason}
                                         for index, reason in enumerate(reasons)]}


RECORDS = {
    'empty': {},
    'unknown': {'status': 'unknown'},
    'equivalent': {'equivalent': True, 'diff': []},
    'no reason': differing(),
    'one reason': differing('r1'),
    'three reasons': differing('r1', 'r2', 'r3'),
    'five reasons': differing('r1', 'r2', 'r3', 'r4', 'r5'),
    'an entry that is not a dict and a reason that is not a string': {
        'equivalent': False, 'diff': ['not a dict', {'id': 'check-1', 'reason': 7}, {'id': 'check-2', 'reason': 'r3'}]},
}


class HelperAgreementTests(unittest.TestCase):
    def directory(self):
        """A new empty directory, removed when the test ends."""
        temporary = tempfile.TemporaryDirectory(prefix='helper copies ')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name).resolve()

    def fixed_tree(self, root):
        """The fixed tree at `root`: `a.txt` with the bytes `abc`, `sub/b.bin` with the bytes 00 01, and the empty
        folder `empty`."""
        (root / 'sub').mkdir(parents=True)
        (root / 'empty').mkdir()
        (root / 'a.txt').write_bytes(b'abc')
        (root / 'sub' / 'b.bin').write_bytes(b'\x00\x01')
        return root

    def link(self, path, target):
        """Make `path` a symlink to `target`, or skip the test with the reason where the platform cannot make one."""
        try:
            path.symlink_to(target)
        except (OSError, NotImplementedError) as refused:
            self.skipTest(f'This platform cannot make a symlink: {refused}')

    def assert_same_answer(self, name, argument, kind):
        """The grader's helper `name` and the tool's answer `argument` alike, and the answer is of `kind`."""
        tool = integrity if name == 'digest' else verdict_labels
        grader = grader_integrity if name == 'digest' else grader_labels
        expected = answer(getattr(grader, name), argument)
        self.assertEqual(expected[0], kind, expected)
        self.assertEqual(answer(getattr(tool, name), argument), expected)

    def test_02_B3_the_graders_digest_gives_the_tools_answer_for_files_a_tree_and_a_missing_path(self):
        root = self.directory()
        (root / 'empty.bin').write_bytes(b'')
        # Three blocks of the size the helpers read at a time, so the order of the blocks counts.
        (root / 'large.bin').write_bytes(bytes(range(256)) * (3 * 4096))
        self.assertEqual((root / 'large.bin').stat().st_size, 3 * 1024 * 1024)
        self.fixed_tree(root / 'tree')
        for name in ('empty.bin', 'large.bin', 'tree'):
            with self.subTest(artifact=name):
                self.assert_same_answer('digest', root / name, 'returns')
        with self.subTest(artifact='a path that does not exist'):
            self.assert_same_answer('digest', root / 'absent.bin', 'raises')

    def test_02_B3_the_graders_digest_refuses_a_symlink_as_the_tools_does(self):
        root = self.directory()
        tree = self.fixed_tree(root / 'tree')
        self.link(root / 'link.txt', tree / 'a.txt')
        self.assert_same_answer('digest', root / 'link.txt', 'raises')
        self.link(tree / 'sub' / 'link.bin', tree / 'sub' / 'b.bin')
        self.assert_same_answer('digest', tree, 'raises')

    def test_02_B3_the_graders_verdict_words_are_the_tools(self):
        for name in ('equivalence_label', 'verdict_reason'):
            for case, record in RECORDS.items():
                with self.subTest(helper=name, record=case):
                    self.assert_same_answer(name, record, 'returns')

    def test_02_B3_the_runtime_has_one_name_and_the_integrity_errors_are_two_classes_of_one_name(self):
        self.assertEqual(grader_labels.RUNTIME, replay.RUNTIME)
        tool, grader = integrity.IntegrityError, grader_integrity.IntegrityError
        self.assertEqual((tool.__name__, grader.__name__), ('IntegrityError', 'IntegrityError'))
        self.assertTrue(issubclass(tool, ValueError) and issubclass(grader, ValueError))
        self.assertIsNot(tool, grader)
        self.assertFalse(issubclass(tool, grader) or issubclass(grader, tool))


class GradingBoundaryTests(unittest.TestCase):
    """Grading code is what `grader_closure` names: all of it lies in `benchmark_support`."""

    def assert_inside_the_benchmark_folder(self, closure):
        """Every module of `closure`, named from the `generative_driver` package, is `benchmark_support` or under
        it."""
        outside = [name for name in closure if name.split('.')[0] != 'benchmark_support']
        self.assertEqual(outside, [], 'Grading code imports modules outside the benchmark folder')

    def test_02_B5_every_grading_module_is_in_the_benchmark_folder(self):
        closure = grader_closure()
        for name in ('benchmark_support.evaluator', 'benchmark_support.integrity', 'benchmark_support.labels'):
            self.assertIn(name, closure)
        self.assert_inside_the_benchmark_folder(closure)

    def test_02_B5_the_same_check_finds_a_grading_module_that_imports_the_tools_integrity(self):
        temporary = tempfile.TemporaryDirectory(prefix='helper copies ')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'generative_driver'
        shutil.copytree(Path(generative_driver.__file__).parent, root, ignore=shutil.ignore_patterns('__pycache__'))
        # In the copy alone, the grader's helper takes the tool's `digest` in place of its own.
        helper = root / 'benchmark_support' / 'integrity.py'
        helper.write_text(helper.read_text(encoding='utf-8') + '\nfrom ..integrity import digest\n', encoding='utf-8')
        closure = grader_closure(root)
        self.assertIn('integrity', closure)
        with self.assertRaisesRegex(AssertionError, r"\['integrity'\] != \[\]"):
            self.assert_inside_the_benchmark_folder(closure)


class HarnessIntegrityTests(HarnessTrialFixture):
    """What `harness.conclude` records when grading code stops for integrity with the grader's own class."""

    def conclude(self, result, *, truth_source):
        """The trial record `harness.conclude` returns for the completed run of `result`, in the trial folder its
        case was frozen in."""
        from generative_driver.benchmark_support import harness
        return harness.conclude(self.frozen, 'tq9-v2', self.home / 'runs' / result['run_id'], result,
                                password=self.password_object(), truth_source=truth_source, token=TOKEN,
                                call=self.configurator)

    def test_02_B7_a_package_changed_before_grading_is_recorded_unscored_for_integrity(self):
        from generative_driver.benchmark_support import evaluator
        _, result = self.pending(tamper=True)
        with patch.object(evaluator, 'reference_execute', side_effect=AssertionError('device started')):
            done = self.conclude(result, truth_source=self.refused_truth)
        equivalence = done['equivalence']
        self.assertEqual((done['status'], equivalence['status'], equivalence['equivalent']),
                         ('unscored', 'unscored', None), done)
        self.assertEqual([entry['reason'] for entry in equivalence['diff']],
                         ['integrity: Accepted package bytes changed'])

    def test_02_B7_a_seal_that_finds_accepted_bytes_changed_leaves_an_unscored_record_for_integrity(self):
        from generative_driver.benchmark_support import evaluator
        _, result = self.pending(tamper_notes=True)
        with patch.object(evaluator, 'reference_execute', side_effect=reference(PASS)):
            done = self.conclude(result, truth_source=self.unlocked_truth)
        equivalence = done['equivalence']
        self.assertEqual((done['status'], done['sealed'], equivalence['status'], equivalence['equivalent']),
                         ('unscored', False, 'unscored', None), done)
        self.assertEqual(len(equivalence['diff']), 1, equivalence['diff'])
        reason = equivalence['diff'][0]['reason']
        self.assertTrue(reason.startswith('integrity: Accepted artifact bytes changed'), reason)
        self.assertEqual(done['sealing_failed'], reason)


if __name__ == '__main__':
    unittest.main()
