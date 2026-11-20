"""The tool's own helpers: the hash of an artifact, the error that stops a run for integrity, and the words of a
run's final verdict.

The helpers are tested by what they return, and the service through its public calls.

Temporary directories and homes only; no model and no hardware.
"""
import inspect
import sys
import tempfile
import time
import unittest
from pathlib import Path

from generative_driver import integrity, verdict_labels

ABC = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'
FIXED_TREE = '1dac59c765a528370eaa52da6b3e4dfc8127e8a08a9acd81e7cfd7d2ee3184ab'
EMPTY_DIRECTORY = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'


def own_names(module):
    """The names `module` holds without a leading underscore, leaving out the modules and names it imported."""
    return sorted(name for name, value in vars(module).items()
                  if not name.startswith('_') and not inspect.ismodule(value)
                  and getattr(value, '__module__', module.__name__) == module.__name__)


def fixed_tree(root):
    """The fixed tree at `root`: `a.txt` with the bytes `abc`, `sub/b.bin` with the bytes 00 01, and the empty
    folder `empty`."""
    (root / 'sub').mkdir(parents=True)
    (root / 'empty').mkdir()
    (root / 'a.txt').write_bytes(b'abc')
    (root / 'sub' / 'b.bin').write_bytes(b'\x00\x01')
    return root


def link(test, path, target):
    """Make `path` a symlink to `target`, or skip `test` with the reason where the platform cannot make one."""
    try:
        path.symlink_to(target)
    except (OSError, NotImplementedError) as refused:
        test.skipTest(f'This platform cannot make a symlink: {refused}')


class TemporaryDirectoryCase(unittest.TestCase):
    def directory(self):
        """A new empty directory, removed when the test ends."""
        temporary = tempfile.TemporaryDirectory(prefix='shared helpers ')
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name).resolve()


class IntegrityTests(TemporaryDirectoryCase):
    def test_02_B1_integrity_offers_digest_and_integrity_error_and_nothing_else(self):
        self.assertEqual(sorted(integrity.__all__), ['IntegrityError', 'digest'])
        self.assertEqual(own_names(integrity), ['IntegrityError', 'digest'])

    def test_02_B1_the_digest_of_a_file_is_the_hash_the_tool_records_today(self):
        path = self.directory() / 'a.txt'
        path.write_bytes(b'abc')
        self.assertEqual(integrity.digest(path), ABC)

    def test_02_B1_the_digest_of_a_directory_is_the_hash_the_tool_records_today(self):
        root = self.directory()
        self.assertEqual(integrity.digest(fixed_tree(root / 'tree')), FIXED_TREE)
        (root / 'nothing').mkdir()
        self.assertEqual(integrity.digest(root / 'nothing'), EMPTY_DIRECTORY)

    def test_02_B1_digest_refuses_a_symlink_at_the_path_and_under_a_directory(self):
        root = self.directory()
        tree = fixed_tree(root / 'tree')
        link(self, root / 'link.txt', tree / 'a.txt')
        with self.assertRaises(ValueError) as refused:
            integrity.digest(root / 'link.txt')
        self.assertEqual(str(refused.exception), 'Artifact symlinks are not admissible')
        link(self, tree / 'sub' / 'link.bin', tree / 'sub' / 'b.bin')
        with self.assertRaises(ValueError) as refused:
            integrity.digest(tree)
        self.assertEqual(str(refused.exception), 'Artifact symlinks are not admissible')

    def test_02_B1_digest_refuses_a_path_that_does_not_exist(self):
        missing = self.directory() / 'absent.bin'
        with self.assertRaises(ValueError) as refused:
            integrity.digest(missing)
        self.assertEqual(str(refused.exception), 'Artifact does not exist: ' + str(missing))

    def test_02_B1_integrity_error_is_a_value_error(self):
        self.assertTrue(issubclass(integrity.IntegrityError, ValueError))


class VerdictLabelTests(unittest.TestCase):
    def test_02_B2_verdict_labels_offers_equivalence_label_and_verdict_reason_and_nothing_else(self):
        self.assertEqual(sorted(verdict_labels.__all__), ['equivalence_label', 'verdict_reason'])
        self.assertEqual(own_names(verdict_labels), ['equivalence_label', 'verdict_reason'])

    def test_02_B2_equivalence_label_words_a_verdict_and_a_record_without_one(self):
        for record, label in (({'equivalent': True}, 'equivalent'), ({'equivalent': False}, 'not equivalent'),
                              ({'status': 'unknown'}, 'unknown'), ({}, 'unscored'), (None, 'unscored')):
            with self.subTest(record=record):
                self.assertEqual(verdict_labels.equivalence_label(record), label)

    def test_02_B2_verdict_reason_gives_the_verdict_and_the_first_three_of_five_differences(self):
        self.assertEqual(verdict_labels.verdict_reason({'equivalent': True, 'diff': []}), 'Final verdict: equivalent')
        differing = {'equivalent': False,
                     'diff': [{'id': f'check-{number}', 'reason': f'r{number}'} for number in range(1, 6)]}
        self.assertEqual(verdict_labels.verdict_reason(differing),
                         'Final verdict: not equivalent: r1; r2; r3; 2 more')


class IntegrityStopTests(TemporaryDirectoryCase):
    def stopped(self, controller, run, seconds=30):
        """The run's status once it has stopped and nothing of it is left stopping."""
        until = time.monotonic() + seconds
        while True:
            status = controller.call('status', run)
            if status['status'] not in ('queued', 'running') and not status['stopping']:
                return status
            self.assertLess(time.monotonic(), until, status)
            time.sleep(.02)

    def test_02_B6_a_resume_after_accepted_notes_changed_raises_the_tools_error_and_blocks_the_run(self):
        from generative_driver.configurator import Controller
        controller = Controller(self.directory() / 'home')
        self.addCleanup(controller.close)
        # A worker that ends at once in acquire and fails in every later stage, so the run has an accepted handoff
        # when it stops.
        worker = ('import json,sys; stage=json.loads(sys.stdin.read().partition(chr(10))[2])["stage"]; '
                  'sys.exit(0 if stage == "acquire" else 1)')
        run = controller.call('start', {'goal': 'A run whose accepted notes change while it is stopped',
                                        'executor_config': {'command': [sys.executable, '-c', worker]}})
        stopped = self.stopped(controller, run)
        self.assertEqual((stopped['status'], stopped['stage']), ('failed', 'probe'), stopped)
        notes = Path(controller.call('result', run)['accepted_handoffs'][0]['notes']['path'])
        notes.write_text(notes.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        with self.assertRaises(ValueError) as refused:
            controller.call('resume', run)
        self.assertIs(type(refused.exception), integrity.IntegrityError)
        self.assertIn('Stale handoff', str(refused.exception))
        status = controller.call('status', run)
        self.assertEqual((status['status'], status['outcome_category']), ('blocked', 'integrity'), status)


if __name__ == '__main__':
    unittest.main()
