"""Feature 05, vault gate: a suite holds its set in memory, says when it is ready, and starts trials only once the set's
folder is out of reach. Real temporary directories, removed or renamed, stand for a closed vault; fixture devices and
scripted workers only. Never a real volume, mount, password, Renode or model run."""
import contextlib
import copy
import dataclasses
import errno
import gc
import hashlib
import inspect
import json
import os
import shutil
import signal
import tempfile
import threading
import time
import unittest
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generative_driver.benchmark_support import evaluator, generated_sets, generated_trials, harness
from generative_driver.benchmark_support.evidence import public_pin
from generative_driver.benchmark_support.suites import resume_suite
from generative_driver.benchmark_support.truth import EvaluatorPassword, UnlockedTruth
from generative_driver.configurator import Controller
from generated_fixtures import FIXTURE_ANSWER_KEY, PASSWORD, REFERENCE_A
from suite_fixtures import drive_suite, settled_suite, symlinks_refused, wait_harnesses
from test_generated_trials import (HARNESS_FIXTURE, SET, START, GeneratedTrialFixture, SetSuiteFixture,
                                   SuiteStartFixture, as_scoring_set, handmade_manifest, no_native_process,
                                   qualified_set, recorded_digest, replay_candidate, resealed_set, set_manifest)
from test_replay_executor import serve

PREFIX = 'The generated set ' + SET + ' is not admitted: '
UNCERTAIN = 'Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation'


def copied_set(test, name='set'):
    """A copy of the qualified set that a test may remove, damage or rename: its root."""
    source, _, _ = qualified_set()
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    root = Path(directory.name).resolve() / name
    shutil.copytree(source, root)
    return root


class HoldingFixture(SuiteStartFixture):
    def supplied(self, root=None):
        source, handle, _ = qualified_set()
        return {'password_files': {SET: str(handle)}, 'set_root': str(root or source)}


class ReadingIntoMemoryTests(HoldingFixture):

    def test_05_B1_a_new_set_suite_reads_each_file_of_its_set_once_before_any_contact(self):
        root = copied_set(self)
        real, reads, at_contact = Path.read_bytes, [], []

        def read_bytes(path):
            reads.append(path)
            return real(path)

        def call(method, params=None):
            at_contact.append(sorted(str(path.relative_to(root)) for path in reads if path.is_relative_to(root)))
            return self.call(method, params)
        with patch.object(Path, 'read_bytes', read_bytes):
            harness_record = self.run_with(call, **self.supplied(root))
        self.assertEqual(harness_record['reason'], 'the configurator stops this test here')
        self.assertEqual(at_contact[0], ['devices/0000d001.answer.enc', 'devices/0000d001.inputs.enc',
                                         'devices/0000d002.answer.enc', 'devices/0000d002.inputs.enc',
                                         'manifest.json', 'qualification.enc'])

    def run_with(self, call, **request):
        return harness.run_suite(self.base / 'suite', {'home': str(self.base / 'home'), 'manifest': set_manifest(),
                                                       **request}, call=call)

    def test_05_B1_a_damaged_sealed_file_ends_it_in_the_not_admitted_form(self):
        for name in ('inputs', 'answer'):
            with self.subTest(name):
                self.calls.clear()
                root = copied_set(self)
                (root / 'devices' / ('0000d002.' + name + '.enc')).write_bytes(b'damaged')
                self.assertRefused(PREFIX + 'The ' + name + ' file of device 0000d002 does not match its manifest hash',
                                   **self.supplied(root))

    def test_05_B1_a_missing_or_unreadable_file_is_named_by_its_class_alone(self):
        for name, reason in (('devices/0000d001.inputs.enc', 'FileNotFoundError'),
                             ('devices/0000d002.answer.enc', 'FileNotFoundError'),
                             ('manifest.json', 'FileNotFoundError'),
                             ('qualification.enc', 'qualification unreadable: FileNotFoundError')):
            with self.subTest(name):
                self.calls.clear()
                root = copied_set(self)
                (root / name).unlink()
                self.assertRefused(PREFIX + reason, **self.supplied(root))
        self.calls.clear()
        root = copied_set(self)
        (root / 'devices/0000d001.answer.enc').unlink()
        (root / 'devices/0000d001.answer.enc').mkdir()
        self.assertRefused(PREFIX + 'IsADirectoryError', **self.supplied(root))


def wait_until(predicate, what, seconds=120):
    """What `predicate` returns once it is true, watched rather than slept on, however loaded the machine is."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        found = predicate()
        if found:
            return found
        time.sleep(.005)
    raise AssertionError('Never saw ' + what)


def waiting(directory):
    """The suite record of the harness in `directory` once it says `awaiting-unmount`, or None; a harness that finished
    without saying so fails the test at once."""
    record = harness.read_suite(directory) or {}
    if record.get('finished') is not None:
        raise AssertionError('The harness finished without waiting: ' + str(record))
    return record if record.get('status') == 'awaiting-unmount' else None


class GateFixture(HoldingFixture):
    """`run_suite` in a thread, whose gate is real and polls at once, against the recording configurator."""
    gated = True

    def setUp(self):
        super().setUp()
        guard = patch.object(harness, 'UNMOUNT_POLL', .005)
        guard.start()
        self.addCleanup(guard.stop)
        self.stop = threading.Event()
        self.threads = []
        self.addCleanup(self.join)
        self.directory = self.base / 'gated-suite'

    def join(self):
        self.stop.set()
        for thread in self.threads:
            thread.join(60)
            self.assertFalse(thread.is_alive(), 'A harness still runs')

    def start(self, call=None, **request):
        """The thread of a harness on a copy of the set unless `request` names a root, and the record it returns."""
        done = {}
        request = {'home': str(self.base / 'home'), 'manifest': set_manifest(), **self.supplied(), **request}
        thread = threading.Thread(target=lambda: done.update(record=harness.run_suite(
            self.directory, request, call=call or self.call, stop=self.stop)), daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread, done

    def finished(self, thread, done):
        thread.join(120)
        self.assertFalse(thread.is_alive(), 'The harness did not finish')
        return done['record']


class GateOrderTests(GateFixture):
    def test_05_B5_it_holds_checks_pings_then_waits_and_starts_nothing_until_the_folder_is_out_of_reach(self):
        root = copied_set(self)
        order = []
        real_hold, real_admitted, real_unreachable = (generated_trials.hold_set, generated_sets.admitted_held,
                                                      generated_trials.set_unreachable)

        def noted(name, real):
            def wrapped(*args, **kwargs):
                if not order or order[-1] != name:
                    order.append(name)
                return real(*args, **kwargs)
            return wrapped

        def call(method, params=None):
            order.append(method)
            return self.call(method, params)
        checks = []

        def unreachable(path):
            checks.append(path)
            return real_unreachable(path)
        with patch.object(generated_trials, 'hold_set', noted('hold', real_hold)), \
                patch.object(generated_sets, 'admitted_held', noted('admit', real_admitted)), \
                patch.object(generated_trials, 'set_unreachable', noted('gate', unreachable)):
            thread, done = self.start(call, set_root=str(root))
            record = wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
            self.assertEqual((record['status'], record['accepted'], record['set']), ('awaiting-unmount', False, SET))
            polls = len(checks)
            wait_until(lambda: len(checks) > polls + 5, 'more checks of the folder')
            self.assertEqual([method for method, _ in self.calls], ['ping'])
            shutil.rmtree(root)
            ended = self.finished(thread, done)
        self.assertEqual(order, ['hold', 'admit', 'ping', 'gate', 'suite_start'])
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])
        self.assertEqual((ended['status'], ended['accepted']), ('failed', False))

    def test_05_B5_a_folder_already_out_of_reach_is_checked_once_and_never_shows_waiting(self):
        root = copied_set(self)
        seen = []

        def call(method, params=None):
            if method == 'ping':
                shutil.rmtree(root)
            else:
                seen.append(harness.read_suite(self.directory)['status'])
            return self.call(method, params)
        with patch.object(generated_trials, 'set_unreachable', wraps=generated_trials.set_unreachable) as checked:
            self.finished(*self.start(call, set_root=str(root)))
        self.assertEqual(checked.call_count, 1)
        self.assertEqual(seen, ['starting'])

    def test_05_B5_a_check_that_ends_the_suite_makes_no_call_and_no_gate_runs(self):
        root = copied_set(self)
        (root / 'devices/0000d001.answer.enc').write_bytes(b'damaged')
        with patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            record = self.finished(*self.start(set_root=str(root)))
        self.assertEqual((record['status'], self.calls), ('failed', []), record)
        self.assertEqual(record['reason'],
                         PREFIX + 'The answer file of device 0000d001 does not match its manifest hash')

    def test_05_B5_a_configurator_it_cannot_reach_ends_it_before_the_gate(self):
        # No RED of its own: the ping's refusal stands as it did; this pins that the gate comes after it.
        with patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            record = self.finished(*self.start(lambda method, params=None: {'ok': False, 'reason': 'no configurator'}))
        self.assertEqual((record['status'], record['reason']), ('failed', 'no configurator'))

    def test_05_B5_a_stopped_harness_ends_its_wait_and_starts_nothing(self):
        thread, done = self.start(set_root=str(copied_set(self)))
        wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
        self.stop.set()
        record = self.finished(thread, done)
        self.assertEqual((record['status'], record['accepted']), ('failed', False))
        self.assertEqual(record['reason'],
                         'The bench harness stopped before the vault holding set ' + SET + ' was closed')
        self.assertEqual([method for method, _ in self.calls], ['ping'])

    def test_05_B11_a_scoring_suite_and_a_development_suite_both_wait(self):
        for name, scoring in (('development', False), ('scoring', True)):
            with self.subTest(name):
                self.calls.clear()
                self.directory = self.base / name
                root = copied_set(self)
                if scoring:
                    as_scoring_set(self)
                thread, done = self.start(set_root=str(root))
                wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
                self.assertEqual([method for method, _ in self.calls], ['ping'])
                shutil.rmtree(root)
                self.finished(thread, done)
                self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])

    def test_05_B11_a_suite_without_a_set_makes_no_check(self):
        # No RED of its own: a suite without a set never reached a gate; this pins that it never will.
        with patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            self.finished(*self.start(manifest=handmade_manifest(), set_root=str(copied_set(self))))
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])


class UnderTheRootTests(GateFixture):
    def assertUnderRoot(self, reason, root, **request):
        self.calls.clear()
        self.directory = self.base / ('suite-' + str(len(list(self.base.iterdir()))))
        with patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            record = self.finished(*self.start(set_root=str(root), **request))
        self.assertEqual(self.calls, [])
        self.assertEqual((record['status'], record['accepted'], record['reason']), ('failed', False, PREFIX + reason))

    def test_05_B10_a_new_suite_refuses_what_it_would_need_from_under_the_root(self):
        root = copied_set(self, 'h-bench')
        self.assertUnderRoot('the configurator home lies under the set root; the bench home lies under the set root',
                             root, home=str(root / 'home'))
        self.assertUnderRoot('the bench home lies under the set root', root, home=str(root.parent / 'h'))
        self.assertUnderRoot('the emulator executable lies under the set root', root, renode=str(root / 'tools/renode'))
        self.assertUnderRoot('a replay candidate lies under the set root', root,
                             options={'replay_candidates': {'generated': str(root / 'candidate')}})

    def test_05_B10_both_sides_are_resolved(self):
        root = copied_set(self)
        refused = symlinks_refused(self.base)
        if refused:
            self.skipTest('This system refuses symlinks: ' + refused)
        link = self.base / 'link-to-set'
        link.symlink_to(root, target_is_directory=True)
        self.assertUnderRoot('the emulator executable lies under the set root', root, renode=str(link / 'renode'))
        self.assertUnderRoot('the emulator executable lies under the set root', link, renode=str(root / 'renode'))

    def test_05_B10_every_reason_is_given_with_the_admission_checks(self):
        root = copied_set(self, 'h-bench')
        self.assertUnderRoot('the configurator home lies under the set root; the bench home lies under the set root; '
                             'the emulator executable lies under the set root', root, home=str(root / 'home'),
                             renode=str(root / 'renode'))

    def test_05_B10_what_lies_beside_the_root_is_not_under_it(self):
        root = copied_set(self)
        thread, done = self.start(set_root=str(root), renode=str(root.parent / 'set-tools/renode'), options={
            'replay_candidates': {'generated': str(root.parent / 'set-candidate')}})
        wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
        shutil.rmtree(root)
        self.finished(thread, done)
        self.assertEqual([method for method, _ in self.calls], ['ping', 'suite_start'])


class TimeLimitTests(GateFixture):
    REASON = 'The vault holding set ' + SET + ' was not closed in time'

    def test_05_B7_a_folder_still_reachable_after_the_limit_ends_the_harness(self):
        root = copied_set(self)
        with patch.object(harness, 'UNMOUNT_WAIT', .05):
            record = self.finished(*self.start(set_root=str(root)))
        self.assertEqual((record['status'], record['accepted'], record['reason']), ('failed', False, self.REASON))
        written = harness.read_suite(self.directory)
        self.assertEqual((written['status'], written['accepted'], written['reason']), ('failed', False, self.REASON))
        self.assertEqual([method for method, _ in self.calls], ['ping'])
        self.assertTrue((root / 'manifest.json').exists())


DETACHED_FIXTURE = HARNESS_FIXTURE.replace('generated_trials.set_unreachable = lambda root: True\n', '')


class LaunchFixture(unittest.TestCase):
    """`launch_suite` with a real configurator and a real detached harness, whose gate is real and polls once a
    second."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.source, self.handle, _ = qualified_set()
        self.root = copied_set(self)
        self.home = self.base / 'home'
        self.configurator = serve(self, self.home)
        self.addCleanup(self.end_harnesses)
        self.candidate = replay_candidate(self.base / 'candidate', REFERENCE_A)
        self.environment(DETACHED_FIXTURE)

    def environment(self, source, extra=''):
        """Have the next harness load the fixture file of `source`, with `extra` lines at its end."""
        fixture = self.base / ('harness_fixture_' + str(len(list(self.base.iterdir()))) + '.py')
        tests = repr(str(Path(__file__).resolve().parent))
        fixture.write_text(source.replace('TESTS', tests).replace('TABLE', repr(str(generated_sets._SETS_FILE)))
                           .replace('START', repr(START)) + extra)
        guard = patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': str(fixture)})
        guard.start()
        self.addCleanup(guard.stop)

    def end_harnesses(self):
        """End every harness of the test, whichever way it got: closing the set's folder ends a wait, and a suite it
        took on is cancelled."""
        shutil.rmtree(self.root, ignore_errors=True)
        for path in (self.home.parent / 'home-bench/suites').glob('*/suite.json'):
            record = harness.read_suite(path.parent) or {}
            if record.get('finished') is None and record.get('pid') and record.get('status') == 'awaiting-unmount':
                with contextlib.suppress(OSError):
                    os.kill(record['pid'], signal.SIGTERM)
            if record.get('suite_id'):
                self.configurator('suite_cancel', {'suite_id': record['suite_id']})
        wait_harnesses(self.home)

    def request(self, **changes):
        return {'manifest': set_manifest(), 'executor': 'replay', 'options': {'replay_candidates': {
            'generated': str(self.candidate)}}, **changes}

    def launch(self, **changes):
        return harness.launch_suite(self.request(**changes), home=self.home, password_files={SET: str(self.handle)},
                                    set_root=str(self.root), renode=str(self.base / 'renode-handle'), timeout=120)


class LaunchTests(LaunchFixture):
    def test_05_B6_it_returns_at_once_on_awaiting_unmount_mints_a_request_and_follows_the_same_harness(self):
        first = self.launch()
        self.assertEqual(first, {'ok': True, 'status': 'awaiting-unmount', 'set': SET, 'duplicate': False,
                                 'request_id': first['request_id'], 'suite_dir': first['suite_dir']})
        self.assertTrue(first['request_id'].strip())
        self.assertIsNone(harness.read_suite(first['suite_dir']).get('finished'))
        again = self.launch(request_id=first['request_id'])
        self.assertEqual(again, {**first, 'duplicate': True})
        pid = harness.read_suite(first['suite_dir'])['pid']
        self.assertEqual(self.configurator('suite_result', {'suite_id': 'none'})['ok'], False)
        shutil.rmtree(self.root)
        accepted = wait_until(lambda: (lambda reply: reply if 'suite_id' in reply else None)(
            self.launch(request_id=first['request_id'])), 'the accepted suite')
        self.assertEqual((accepted['ok'], accepted['duplicate'], accepted['suite_dir']),
                         (True, True, first['suite_dir']))
        self.assertEqual(self.configurator('suite_status', {'suite_id': accepted['suite_id']})['ok'], True)
        self.assertEqual(harness.read_suite(first['suite_dir'])['pid'], pid)
        self.assertEqual(len(list((self.home.parent / 'home-bench/suites').iterdir())), 1)

    def test_05_B6_a_stopped_harness_leaves_the_request_free_to_start_afresh(self):
        first = self.launch(request_id='stopped')
        pid = harness.read_suite(first['suite_dir'])['pid']
        os.kill(pid, signal.SIGTERM)
        from generative_driver.client import _wait_for_exit
        self.assertTrue(_wait_for_exit(pid, 60))
        again = self.launch(request_id='stopped')
        self.assertEqual((again['status'], again['duplicate']), ('awaiting-unmount', False))
        self.assertNotEqual(again['suite_dir'], first['suite_dir'])

    def test_05_B6_a_suite_without_a_set_takes_no_request_and_no_gate(self):
        # No RED of its own: a suite without a set is as it was.
        from generative_driver.client import _wait_for_exit
        refused = harness.launch_suite({'manifest': handmade_manifest(), 'executor': 'replay', 'options': {}},
                                       home=self.home, timeout=120)
        self.assertEqual(refused['ok'], False)
        self.assertFalse((self.home.parent / 'home-bench/suite-requests').exists())
        self.assertIsNone(harness.read_suite(refused['suite_dir']).get('set'))
        self.assertTrue(_wait_for_exit(harness.read_suite(refused['suite_dir'])['pid'], 60))

    def test_05_B7_the_same_request_starts_afresh_after_the_limit(self):
        self.environment(DETACHED_FIXTURE, 'import __main__\n__main__.UNMOUNT_WAIT = .1\n')
        first = self.launch(request_id='in-time')
        ended = wait_until(lambda: (lambda record: record if record.get('finished') else None)(
            harness.read_suite(first['suite_dir'])), 'the harness ending')
        self.assertEqual(ended['reason'], 'The vault holding set ' + SET + ' was not closed in time')
        again = self.launch(request_id='in-time')
        self.assertNotEqual(again['suite_dir'], first['suite_dir'])
        self.assertEqual(again.get('duplicate', False), False)


class ResumeFixture(GateFixture):
    """A resume of a stopped set suite, against a configurator that answers from the suite's frozen pins and stops the
    test at `suite_resume`."""

    def setUp(self):
        super().setUp()
        self.source, self.handle, self.manifest = qualified_set()
        digest = recorded_digest(self.source)
        self.frozen = {device: generated_trials.device_pin(self.manifest, device, digest)
                       for device in ('0000d001', '0000d002')}
        self.status = {'status': 'blocked', 'stopping': False, 'active_child_id': None}
        self.child = {'ok': True, 'status': 'blocked', 'uncertain_effect': False, 'stopping': False}
        self.suite_manifest = set_manifest()
        self.resume_reply = {'ok': False, 'reason': 'the configurator stops this test here'}

    def call(self, method, params=None):
        self.calls.append((method, params))
        identity = {'manifest': self.suite_manifest, 'entry_pins': [public_pin(pin) for pin in self.frozen.values()]}
        return {'ping': {'ok': True}, 'status': self.child, 'suite_resume': self.resume_reply,
                'suite_result': {'ok': True, 'suite_id': 'suite-1', **self.status,
                                 'experiment': {'comparison_identity': identity}}}.get(method, {'ok': True})

    def resume(self, root=None, **request):
        """The thread of a resuming harness, and the record it returns."""
        given = {'manifest': None, 'suite_id': 'suite-1', 'set_root': str(root or copied_set(self)), **request}
        return self.start(**given)

    def methods(self):
        return [method for method, _ in self.calls]


class ResumeOrderTests(ResumeFixture):
    def test_05_B13_it_asks_the_configurator_then_holds_and_checks_then_waits_and_only_then_resumes(self):
        root = copied_set(self)
        order = []

        def noted(name, real):
            def wrapped(*args, **kwargs):
                if not order or order[-1] != name:
                    order.append(name)
                return real(*args, **kwargs)
            return wrapped
        real_call = self.call

        def call(method, params=None):
            order.append(method)
            return real_call(method, params)
        with patch.object(generated_trials, 'hold_set', noted('hold', generated_trials.hold_set)), \
                patch.object(generated_sets, 'admitted_held', noted('admit', generated_sets.admitted_held)), \
                patch.object(generated_trials, 'set_unreachable', noted('gate', generated_trials.set_unreachable)):
            thread, done = self.start(call, manifest=None, suite_id='suite-1', set_root=str(root))
            record = wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
            self.assertEqual((record['accepted'], record['set'], record['suite_id']), (False, SET, 'suite-1'))
            self.assertNotIn('suite_resume', self.methods())
            shutil.rmtree(root)
            ended = self.finished(thread, done)
        self.assertEqual(order, ['ping', 'suite_result', 'hold', 'admit', 'gate', 'suite_resume'])
        self.assertEqual(ended['reason'], 'the configurator stops this test here')

    def assertRefused(self, reason, root=None, **request):
        self.calls.clear()
        self.directory = self.base / ('suite-' + str(len(list(self.base.iterdir()))))
        with patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            record = self.finished(*self.resume(root, **request))
        self.assertEqual((record['status'], record['accepted'], record['reason']), ('failed', False, reason))
        self.assertNotIn('suite_resume', self.methods())
        return record

    def test_05_B13_a_suite_that_cannot_resume_refuses_with_the_configurators_text_and_holds_nothing(self):
        with patch.object(generated_trials, 'hold_set', side_effect=AssertionError('the set was held')):
            for status in ('queued', 'running', 'completed'):
                self.status['status'] = status
                with self.subTest(status):
                    self.assertRefused('Only a stopped incomplete suite can resume')
            self.status.update(status='blocked', stopping=True)
            self.assertRefused('Previous suite work is still stopping')
            self.status.update(stopping=False, active_child_id='run-1')
            self.child['uncertain_effect'] = True
            self.assertRefused(UNCERTAIN)
            self.assertEqual(self.methods(), ['ping', 'suite_result', 'status'])

    def test_05_B13_what_it_cannot_read_from_the_configurator_refuses_too(self):
        with patch.object(generated_trials, 'hold_set', side_effect=AssertionError('the set was held')):
            self.status.update(active_child_id='run-1')
            self.child = {'ok': False, 'reason': 'no such run'}
            self.assertRefused('The configurator did not report the suite\'s active child: no such run')
            real_call = self.call

            def call(method, params=None):
                if method == 'suite_result':
                    return {'ok': False, 'reason': 'no such suite'}
                return real_call(method, params)
            with patch.object(self, 'call', call):
                self.assertRefused('The configurator did not report the suite: no such suite')

    def test_05_B13_every_stopped_status_goes_on(self):
        for status in ('blocked', 'failed', 'cancelled'):
            self.status['status'] = status
            with self.subTest(status):
                self.calls.clear()
                self.directory = self.base / status
                root = copied_set(self)
                thread, done = self.resume(root)
                wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
                shutil.rmtree(root)
                self.finished(thread, done)
                self.assertEqual(self.methods(), ['ping', 'suite_result', 'suite_resume'])

    def test_05_B13_a_child_with_nothing_uncertain_goes_on(self):
        self.status.update(active_child_id='run-1')
        root = copied_set(self)
        thread, done = self.resume(root)
        wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
        shutil.rmtree(root)
        self.finished(thread, done)
        self.assertEqual(self.methods(), ['ping', 'suite_result', 'status', 'suite_resume'])

    def test_05_B13_a_set_it_cannot_admit_refuses_in_the_not_admitted_form_with_the_vault_still_open(self):
        (self.base / 'another-handle').write_text('another credential')
        self.assertRefused(PREFIX + 'qualification unreadable: ValueError',
                           password_files={SET: str(self.base / 'another-handle')})
        self.assertRefused(PREFIX + 'no evaluator password file was supplied', password_files={})
        self.assertEqual(self.methods(), ['ping', 'suite_result'])
        with patch.object(generated_sets, 'grader_identity', return_value={'another': 'identity'}):
            self.assertRefused(PREFIX + 'qualification under another grader identity')
        with patch.dict(os.environ, {'GD_TEST_HARNESS_FIXTURE': 'a fixture file'}), \
                patch.object(generated_trials, '_SHIPPED_SETS', generated_sets._SETS_FILE):
            self.assertRefused(PREFIX + 'GD_TEST_HARNESS_FIXTURE is set, and this is a shipped set')

    def test_05_B13_a_requalified_record_differs_from_the_frozen_pins(self):
        def later(record):
            record['seconds'] = 1234
        reason = 'qualification differs from the frozen pin:'
        self.assertRefused(PREFIX + reason + '0000d001; ' + reason + '0000d002', resealed_set(self, later))

    def test_05_B13_an_unreadable_password_file_ends_it_before_any_contact(self):
        record = self.finished(*self.resume(password_files={SET: str(self.base / 'absent')}))
        self.assertEqual(record['reason'],
                         'The evaluator password file of ' + SET + ' cannot be read: FileNotFoundError')
        self.assertEqual(self.calls, [])

    def test_05_B13_a_folder_still_reachable_after_the_limit_ends_it_as_a_new_suites_does(self):
        with patch.object(harness, 'UNMOUNT_WAIT', .05):
            record = self.finished(*self.resume())
        self.assertEqual((record['accepted'], record['reason']),
                         (False, 'The vault holding set ' + SET + ' was not closed in time'))
        self.assertEqual(self.methods(), ['ping', 'suite_result'])

    def test_05_B13_a_stopped_harness_ends_its_wait_with_no_resume(self):
        thread, done = self.resume()
        wait_until(lambda: waiting(self.directory), 'awaiting-unmount')
        self.stop.set()
        self.assertEqual(self.finished(thread, done)['accepted'], False)
        self.assertEqual(self.methods(), ['ping', 'suite_result'])

    def test_05_B13_a_handmade_suite_given_a_root_holds_nothing_and_never_waits(self):
        self.suite_manifest = handmade_manifest()
        with patch.object(generated_trials, 'hold_set', side_effect=AssertionError('the set was held')), \
                patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            self.finished(*self.resume())
        self.assertEqual(self.methods(), ['ping', 'suite_result', 'suite_resume'])

    def test_05_B10_a_resume_refuses_what_it_would_need_from_under_the_root(self):
        root = copied_set(self, 'h-bench')
        under = ' lies under the set root'
        self.assertRefused(PREFIX + 'the configurator home' + under + '; the bench home' + under, root,
                           home=str(root / 'home'))
        self.assertRefused(PREFIX + 'the bench home' + under, root, home=str(root.parent / 'h'))
        self.assertRefused(PREFIX + 'the emulator executable' + under, root, renode=str(root / 'renode'))


class ResumeWithoutARootTests(ResumeFixture):
    def resume_without(self, reservations, child):
        self.child = {'ok': True, 'stopping': False, **child}
        replies = iter(reservations)
        real_call = self.call
        self.resume_reply = {'ok': True, 'suite_id': 'suite-1', 'attached': True}

        def call(method, params=None):
            if method == 'suite_reserve':
                self.calls.append((method, params))
                return next(replies)
            return ({**real_call(method, params), 'decision': 'advance'} if method == 'suite_settle'
                    else real_call(method, params))
        with patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            return self.finished(*self.start(call, manifest=None, suite_id='suite-1', set_root=None,
                                             password_files=None))

    def slot(self):
        return {'ok': True, 'status': 'running', 'request': {'budget_seconds': 1, 'case_pin': self.frozen['0000d001']},
                'slot': {'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': 'generated',
                         'device_id': '0000d001', 'child_request_id': 'child', 'run_id': 'run-1'}}

    def test_05_B15_slots_that_need_no_device_settle_with_no_gate_and_no_read_of_the_suite(self):
        # No RED of its own: this pins what stays.
        for name, child in (('a recorded verdict', {'status': 'completed', 'equivalence': {'status': 'graded'}}),
                            ('an uncertain effect', {'status': 'blocked', 'uncertain_effect': True})):
            with self.subTest(name):
                self.calls.clear()
                self.directory = self.base / name.replace(' ', '-')
                record = self.resume_without([self.slot(), {'ok': True, 'status': 'completed', 'slot': None}], child)
                self.assertEqual((record['status'], record['accepted']), ('finished', True), record)
                self.assertNotIn('suite_result', self.methods())

    def test_05_B15_a_slot_that_needs_its_device_fails_there(self):
        # No RED of its own: Feature 03's B5 stands.
        record = self.resume_without([self.slot()], {'status': 'completed', 'equivalence': None,
                                                     'uncertain_effect': False})
        self.assertEqual((record['status'], record['accepted']), ('failed', True))
        self.assertEqual(record['reason'],
                         'The evaluator password file of ' + SET + ' is required to provide and grade its device')
        self.directory = self.base / 'with-password'
        self.calls.clear()
        with patch.object(harness, '_supplied_passwords', return_value={SET: PASSWORD}):
            record = self.resume_without([self.slot()], {'status': 'completed', 'equivalence': None,
                                                         'uncertain_effect': False})
        self.assertEqual(record['reason'], 'The set root of ' + SET + ' is required to provide and grade its device')


class ResumeReplyTests(ResumeFixture):
    """`launch_suite` and `resume_suite` with the harness in a thread of this process, under the fake configurator."""

    def setUp(self):
        super().setUp()
        self.spawned = []

        def spawn(where, _arguments, payload):
            self.directory = Path(where)
            self.spawned.append(payload)
            thread, done = self.start(**payload)
            return type('Harness', (), {'poll': lambda _self: None if thread.is_alive() else 0})()
        guard = patch.object(harness, '_spawn', spawn)
        guard.start()
        self.addCleanup(guard.stop)

    def options(self, root):
        return {'evaluator_password_files': {SET: str(self.handle)}, 'set_root': str(root)}

    def test_05_B14_a_resumes_awaiting_unmount_reply_names_its_suite_and_no_request(self):
        root = copied_set(self)
        reply = resume_suite('suite-1', self.options(root), home=self.base / 'home', autostart=False)
        self.assertEqual(reply, {'ok': True, 'suite_id': 'suite-1', 'suite_dir': str(self.directory),
                                 'status': 'awaiting-unmount', 'set': SET})
        self.assertFalse((self.base / 'home-bench/suite-requests').exists())
        shutil.rmtree(root)

    def test_05_B14_an_accepted_resumes_reply_is_as_it_was(self):
        # No RED of its own: this pins what stays.
        self.resume_reply = {'ok': True, 'suite_id': 'suite-1', 'status': 'running', 'attached': True}
        with patch.object(generated_trials, 'set_unreachable', return_value=True):
            reply = resume_suite('suite-1', self.options(copied_set(self)), home=self.base / 'home', autostart=False)
        self.assertEqual(reply, {'ok': True, 'suite_id': 'suite-1', 'status': 'running', 'attached': True,
                                 'duplicate': False, 'suite_dir': str(self.directory)})


class LiveSuiteResumeTests(unittest.TestCase):
    """`resume_suite` over a recording configurator client, before any harness."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.manifest = set_manifest()
        self.suite = {'ok': True, 'status': 'blocked', 'stopping': False, 'active_child_id': 'run-1'}
        self.child = {'ok': True, 'status': 'blocked'}
        self.spawned = []

        def spawn(where, _arguments, payload):
            self.spawned.append(payload)
            (Path(where) / 'suite.json').write_text(json.dumps({'status': 'failed', 'accepted': False, 'finished': 1,
                                                                'reason': 'stopped here'}))
            return type('Ended', (), {'poll': lambda _self: 0})()
        guard = patch.object(harness, '_spawn', spawn)
        guard.start()
        self.addCleanup(guard.stop)

    def call(self, method, params=None, home=None, **_options):
        self.assertEqual(_options.get('autostart'), False)
        return {'suite_result': {**self.suite, 'experiment': {'comparison_identity': {'manifest': self.manifest}}},
                'suite_status': self.suite, 'status': self.child}.get(method, {'ok': False, 'reason': 'unknown'})

    def resume(self, options=None):
        with patch('generative_driver.client.call', self.call):
            return resume_suite('suite-1', options if options is not None else {'set_root': str(self.base / 'set')},
                                home=self.base / 'home', autostart=False)

    def test_05_B12_it_refuses_before_any_harness_while_the_active_child_runs_or_anything_stops(self):
        for name, suite, child in (('queued', {}, {'status': 'queued'}), ('running', {}, {'status': 'running'}),
                                   ('stopping', {'stopping': True, 'active_child_id': None}, {})):
            self.suite.update(suite)
            self.child.update(child)
            for options in ({'set_root': str(self.base / 'set')}, {}):
                with self.subTest(name, options=options), \
                        self.assertRaisesRegex(ValueError, 'stop it or let it finish'):
                    self.resume(options)
            self.assertEqual(self.spawned, [])

    def test_05_B12_a_stopped_suite_goes_on_to_the_harness(self):
        # No RED of its own: this pins what stays.
        self.assertEqual(self.resume()['ok'], False)
        self.assertEqual(len(self.spawned), 1)

    def test_05_B12_a_handmade_suite_is_as_it_was(self):
        # No RED of its own: this pins what stays.
        self.manifest = handmade_manifest()
        self.child['status'] = 'running'
        self.resume()
        self.assertEqual(len(self.spawned), 1)

    def test_05_B12_a_configurator_it_cannot_reach_goes_on_to_the_harness(self):
        # No RED of its own: this pins what stays.
        resume_suite('suite-1', {'set_root': str(self.base / 'set')}, home=self.base / 'home', autostart=False)
        self.assertEqual(len(self.spawned), 1)


class GatedTrialFixture(GeneratedTrialFixture):
    """An in-process configurator and a suite's harness, whose gate is real, on copies of the qualified set."""
    gated = True

    def setUp(self):
        super().setUp()
        guard = patch.object(harness, 'UNMOUNT_POLL', .005)
        guard.start()
        self.addCleanup(guard.stop)

    def suite_records(self):
        return [harness.read_suite(path.parent) or {}
                for path in (self.root / 'home-bench/suites').glob('*/suite.json')]

    def closing(self, root):
        """Remove `root` as soon as a harness of this test says `awaiting-unmount`, as an operator closes the vault. The
        context's end says it happened."""
        closed = threading.Event()

        def close():
            until = time.monotonic() + 240
            while time.monotonic() < until:
                if any(record.get('status') == 'awaiting-unmount' for record in self.suite_records()):
                    shutil.rmtree(root)
                    closed.set()
                    return
                time.sleep(.005)
        thread = threading.Thread(target=close, daemon=True)
        thread.start()
        return thread, closed

    def drive_closing(self, *devices, **request):
        """A harness on a new copy of the set, removed once it is waiting: its record."""
        root = copied_set(self)
        thread, closed = self.closing(root)
        record = self.drive(*devices, set_root=str(root), **request)
        thread.join(240)
        self.assertTrue(closed.is_set(), record)
        self.assertFalse(root.exists())
        return record

    def assertGradedAndSealed(self, suite_id, count):
        rows, results = self.row_results(suite_id)
        self.assertEqual(len(rows), count)
        for result in results:
            self.assertEqual(result['status'], 'completed', result['reason'])
            self.assertEqual(result['equivalence']['equivalent'], True, result['equivalence'])
        sealed = list((self.root / 'home-bench/suites').glob('*/trials/*/run-evidence.enc'))
        self.assertEqual(len({path.parent for path in sealed}), count, sealed)

    def stops_at_probe(self):
        stopped = []

        def stop_probe(request):
            if request.stage == 'probe' and not stopped:
                stopped.append(request.stage)
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                        'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: scripted'}
        return stop_probe

    def stopped_suite(self):
        """A suite whose one child stopped at probe, on a copy of the set removed once it was waiting: its record."""
        with self.staged(REFERENCE_A, runtime=self.stops_at_probe()):
            return self.drive_closing('0000d001', request_id='stopped')

    def ungraded_suite(self):
        """A suite whose one child completed and could not be graded, on a copy removed once it was waiting: its
        record."""
        with self.staged(REFERENCE_A), patch.object(evaluator, 'reference_execute', side_effect=RuntimeError('host')):
            record = self.drive_closing('0000d001', request_id='ungraded')
        self.assertEqual(self.row_results(record['suite_id'])[1][0]['equivalence'], None)
        return record


class WithTheRootGoneTests(GatedTrialFixture):
    def test_05_B2_a_fresh_suite_provides_runs_grades_and_seals_both_trials(self):
        with self.staged(REFERENCE_A):
            record = self.drive_closing(request_id='fresh')
        self.assertEqual(record['status'], 'finished', record)
        self.assertGradedAndSealed(record['suite_id'], 2)

    def test_05_B2_a_resume_grades_a_completed_ungraded_slot(self):
        first = self.ungraded_suite()
        with self.staged(REFERENCE_A):
            second = self.drive_closing('0000d001', suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertGradedAndSealed(first['suite_id'], 1)

    def test_05_B2_a_resume_continues_a_stopped_slot(self):
        first = self.stopped_suite()
        with self.staged(REFERENCE_A):
            second = self.drive_closing('0000d001', suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertGradedAndSealed(first['suite_id'], 1)


class DecryptionTests(GatedTrialFixture):
    def setUp(self):
        super().setUp()
        source, _, _ = qualified_set()
        self.names = {(source / 'qualification.enc').read_bytes(): 'record'}
        for path in (source / 'devices').iterdir():
            self.names[path.read_bytes()] = path.name.removesuffix('.enc')
        self.opened = []
        real = EvaluatorPassword.unlock_bytes

        def unlock_bytes(password, data, expected):
            if data in self.names:
                self.opened.append(self.names[data])
            return real(password, data, expected)
        guard = patch.object(EvaluatorPassword, 'unlock_bytes', unlock_bytes)
        guard.start()
        self.addCleanup(guard.stop)

    def test_05_B3_the_record_once_at_the_start_and_each_devices_files_once_at_its_slot(self):
        with self.staged(REFERENCE_A):
            record = self.drive_closing(request_id='counted')
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(self.opened, ['record', '0000d001.inputs', '0000d001.answer', '0000d002.inputs',
                                       '0000d002.answer'])

    def test_05_B3_a_grading_resume_decrypts_the_answer_alone(self):
        first = self.ungraded_suite()
        self.opened.clear()
        with self.staged(REFERENCE_A):
            second = self.drive_closing('0000d001', suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertEqual(self.opened, ['record', '0000d001.answer'])

    def test_05_B3_a_continued_slot_decrypts_its_inputs_and_answer_once_each(self):
        first = self.stopped_suite()
        self.opened.clear()
        with self.staged(REFERENCE_A):
            second = self.drive_closing('0000d001', suite_id=first['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertEqual(self.opened, ['record', '0000d001.inputs', '0000d001.answer'])


class ReachableAgainFixture(GatedTrialFixture):
    """A suite whose folder comes back while it runs. `operator` runs beside the harness as the human does: it closes
    the vault at the start, and what it does next is the test's."""

    def setUp(self):
        super().setUp()
        self.vault = copied_set(self)
        self.calls = []
        self.error = []

    def count(self, method):
        return sum(called == method for called, _ in self.calls)

    def reopen(self):
        shutil.copytree(qualified_set()[0], self.vault)

    def await_status(self, *, accepted):
        """The record of the harness once it waits with `accepted`. Once one was accepted, a harness that has finished
        without waiting fails at once."""
        def found():
            records = self.suite_records()
            if accepted and records and all(record.get('finished') is not None for record in records):
                raise AssertionError('The harness finished without waiting: ' + str(records[-1].get('reason')))
            return next((record for record in records if record.get('status') == 'awaiting-unmount'
                         and record.get('accepted') is accepted), None)
        return wait_until(found, 'awaiting-unmount with accepted ' + str(accepted), seconds=240)

    def operator(self, then):
        """A thread that closes the vault when the harness first waits, then runs `then()`."""
        def run():
            try:
                self.await_status(accepted=False)
                shutil.rmtree(self.vault)
                then()
            except BaseException as error:
                self.error.append(error)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def reopened_after(self, opening):
        """An `intercept` that reopens the vault once, right after the harness's first `opening` request."""
        reopened = []

        def intercept(method, params, forward):
            reply = forward(method, params)
            if method == opening and not reopened:
                reopened.append(method)
                self.reopen()
            return reply
        return intercept

    def finish(self, thread):
        thread.join(240)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.error, [])


class ReachableAgainTests(ReachableAgainFixture):
    def test_05_B9_the_next_trial_waits_and_no_worker_starts_meanwhile(self):
        seen = {}

        def then():
            seen['record'] = self.await_status(accepted=True)
            started, polls = self.count('suite_start_child'), self.count('suite_reserve')
            wait_until(lambda: self.count('suite_reserve') >= polls + 3, 'polls of the reservation')
            seen['started'] = (started, self.count('suite_start_child'))
            shutil.rmtree(self.vault)
        thread = self.operator(then)
        with self.staged(REFERENCE_A):
            record = self.drive(calls=self.calls, intercept=self.reopened_after('suite_settle'),
                                set_root=str(self.vault), request_id='again')
        self.finish(thread)
        self.assertEqual(seen['started'], (1, 1))
        self.assertEqual((seen['record']['set'], seen['record']['accepted']), (SET, True))
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(self.count('suite_start_child'), 2)
        self.assertGradedAndSealed(record['suite_id'], 2)
        status = self.controller.call('suite_status', {'suite_id': record['suite_id']})
        self.assertNotIn('awaiting', json.dumps(status))

    def test_05_B9_a_continued_slot_waits_before_its_device_is_provided(self):
        first = self.stopped_suite()
        self.vault = copied_set(self, 'another')
        seen = {}

        def then():
            self.await_status(accepted=True)
            polls = self.count('suite_reserve')
            wait_until(lambda: self.count('suite_reserve') >= polls + 3, 'polls of the reservation')
            seen['attached'] = self.count('attach_device') + self.count('suite_resume_child')
            shutil.rmtree(self.vault)
        thread = self.operator(then)
        with self.staged(REFERENCE_A):
            second = self.drive('0000d001', suite_id=first['suite_id'], calls=self.calls, set_root=str(self.vault),
                                intercept=self.reopened_after('suite_resume'))
        self.finish(thread)
        self.assertEqual(seen['attached'], 0)
        self.assertEqual(second['status'], 'finished', second)
        self.assertGradedAndSealed(first['suite_id'], 1)

    def test_05_B9_a_grading_resume_checks_nothing(self):
        first = self.ungraded_suite()
        self.vault = copied_set(self, 'another')
        thread = self.operator(lambda: None)
        with self.staged(REFERENCE_A), patch.object(harness, 'UNMOUNT_WAIT', 240):
            second = self.drive('0000d001', suite_id=first['suite_id'], calls=self.calls, set_root=str(self.vault),
                                intercept=self.reopened_after('suite_resume'))
        self.finish(thread)
        self.assertTrue(self.vault.exists(), 'The vault stayed open through the grade')
        self.assertEqual(second['status'], 'finished', second)
        self.assertGradedAndSealed(first['suite_id'], 1)
        self.assertFalse([record for record in self.suite_records() if record.get('accepted') is True
                          and record.get('status') == 'awaiting-unmount'])

    def test_05_B9_a_cancelled_suite_ends_the_wait_with_no_worker_and_no_settlement(self):
        def then():
            record = self.await_status(accepted=True)
            self.controller.call('suite_cancel', {'suite_id': record['suite_id']})
        thread = self.operator(then)
        with self.staged(REFERENCE_A):
            record = self.drive(calls=self.calls, intercept=self.reopened_after('suite_settle'),
                                set_root=str(self.vault), request_id='cancelled')
        self.finish(thread)
        self.assertEqual((record['status'], record.get('reason')), ('finished', None), record)
        self.assertEqual((self.count('suite_start_child'), self.count('suite_settle')), (1, 1))
        status = settled_suite(lambda method, params, _home: self.controller.call(method, params), record['suite_id'])
        self.assertEqual(status['status'], 'cancelled', status)

    def test_05_B9_a_set_stop_ends_the_wait_and_the_suite_can_resume(self):
        stop = threading.Event()

        def then():
            self.await_status(accepted=True)
            stop.set()
        thread = self.operator(then)
        with self.staged(REFERENCE_A):
            record = self.drive(calls=self.calls, intercept=self.reopened_after('suite_settle'), stop=stop,
                                set_root=str(self.vault), request_id='stopped-wait')
        self.finish(thread)
        self.assertEqual((record['status'], record.get('reason')), ('finished', None), record)
        self.assertEqual((self.count('suite_start_child'), self.count('suite_settle')), (1, 1))
        status = settled_suite(lambda method, params, _home: self.controller.call(method, params), record['suite_id'])
        self.assertEqual(status['status'], 'blocked', status)

    def test_05_B9_a_reply_that_is_not_ok_keeps_waiting(self):
        outage, refused = threading.Event(), []
        reopen = self.reopened_after('suite_settle')

        def intercept(method, params, forward):
            if method == 'suite_reserve' and outage.is_set():
                refused.append(method)
                return {'ok': False, 'reason': 'a brief outage'}
            return reopen(method, params, forward)

        def then():
            self.await_status(accepted=True)
            outage.set()
            wait_until(lambda: len(refused) >= 3, 'refused polls')
            outage.clear()
            shutil.rmtree(self.vault)
        thread = self.operator(then)
        with self.staged(REFERENCE_A):
            record = self.drive(calls=self.calls, intercept=intercept, set_root=str(self.vault), request_id='outage')
        self.finish(thread)
        self.assertEqual(record['status'], 'finished', record)
        self.assertGradedAndSealed(record['suite_id'], 2)

    def test_05_B9_the_limit_ends_the_harness_blames_no_trial_and_the_suite_can_resume(self):
        reopen = self.reopened_after('suite_settle')
        real_limit = harness.UNMOUNT_WAIT
        self.addCleanup(setattr, harness, 'UNMOUNT_WAIT', real_limit)

        def intercept(method, params, forward):
            if method == 'suite_settle':
                harness.UNMOUNT_WAIT = .05
            return reopen(method, params, forward)
        thread = self.operator(lambda: None)
        with self.staged(REFERENCE_A):
            record = self.drive(calls=self.calls, intercept=intercept, set_root=str(self.vault), request_id='limit')
        harness.UNMOUNT_WAIT = real_limit
        self.finish(thread)
        self.assertEqual((record['status'], record['accepted'], record['reason']),
                         ('failed', True, 'The vault holding set ' + SET + ' was not closed in time'))
        self.assertEqual((self.count('suite_start_child'), self.count('suite_settle')), (1, 1))
        status = settled_suite(lambda method, params, _home: self.controller.call(method, params), record['suite_id'])
        self.assertEqual(status['status'], 'blocked', status)
        self.assertIn('the harness failed', status['reason'])
        self.assertNotIn('trial entry', status['reason'])
        shutil.rmtree(self.vault)
        with self.staged(REFERENCE_A):
            second = self.drive_closing(suite_id=record['suite_id'])
        self.assertEqual(second['status'], 'finished', second)
        self.assertGradedAndSealed(record['suite_id'], 2)


class SlotGateTests(GateFixture):
    """The wait before a slot's worker, on a scripted configurator and a clock of the test's, with no trial run. Each
    sleep of the harness advances the clock one second and runs what `self.at` holds for that second."""

    def setUp(self):
        super().setUp()
        source, _, manifest = qualified_set()
        digest = recorded_digest(source)
        self.slots = [{'ok': True, 'status': 'running', 'slot': {
            'ordinal': ordinal, 'trial_key': 'entry-00' + str(ordinal) + '-repeat-000', 'case': 'generated',
            'device_id': device, 'child_request_id': 'child-' + str(ordinal), 'run_id': None},
            'request': {'budget_seconds': 1, 'case_pin': generated_trials.device_pin(manifest, device, digest)}}
            for ordinal, device in enumerate(('0000d001', '0000d002'))]
        self.none = {'ok': True, 'status': 'completed', 'slot': None}
        self.reply = self.slots[0]
        self.vault = copied_set(self)
        self.trials, self.at, self.now = [], {}, 0
        self.reopen_at_start = False

    def reopen(self):
        shutil.copytree(qualified_set()[0], self.vault)

    def close(self):
        shutil.rmtree(self.vault)

    def next_reply(self, reply):
        def change():
            self.reply = reply
        return change

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == 'suite_reserve':
            return self.reply
        if method == 'suite_settle':
            self.reply = self.slots[1] if self.reply is self.slots[0] else self.none
            return {'ok': True, 'decision': 'advance'}
        if method == 'suite_start' and self.reopen_at_start:
            self.reopen()
        return {'ok': True, 'suite_id': 'suite-1'}

    def methods(self):
        return [method for method, _ in self.calls]

    def run_trial(self, _trial_dir, request, **_options):
        self.trials.append(request['start']['case_pin']['device_id'])
        if len(self.trials) == 1 and not self.vault.exists():
            self.reopen()
        return {'status': 'evaluated'}

    def run_clocked(self):
        def sleep(_seconds):
            self.now += 1
            self.at.get(self.now, lambda: None)()
        clock = SimpleNamespace(monotonic=lambda: self.now, sleep=sleep, time=time.time)
        with patch.object(harness, 'time', clock), patch.object(harness, 'run_trial', self.run_trial), \
                patch.object(harness, 'UNMOUNT_WAIT', 5):
            return harness.run_suite(self.directory, {
                'home': str(self.base / 'home'), 'manifest': set_manifest(), **self.supplied(), 'renode': 'handle',
                'set_root': str(self.vault)}, call=self.call)

    def test_05_B9_each_wait_has_a_fresh_limit(self):
        # Four seconds at the start, then four before the second trial: eight in all, under a limit of five.
        self.at = {4: self.close, 8: self.close}
        record = self.run_clocked()
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(self.trials, ['0000d001', '0000d002'])

    def test_05_B9_the_limit_of_a_wait_ends_it_and_blames_no_trial(self):
        self.at = {4: self.close}
        record = self.run_clocked()
        self.assertEqual((record['status'], record['accepted'], record['reason']),
                         ('failed', True, 'The vault holding set ' + SET + ' was not closed in time'))
        self.assertEqual(self.trials, ['0000d001'])
        detached = next(params for method, params in self.calls if method == 'suite_detach')
        self.assertTrue(detached['reason'].startswith('the harness failed; see '), detached)

    def test_05_B9_each_poll_reserves_and_the_status_goes_back_to_running(self):
        self.at = {4: self.close, 7: self.close}
        statuses = []
        real = self.run_trial

        def run_trial(*args, **kwargs):
            statuses.append(harness.read_suite(self.directory)['status'])
            return real(*args, **kwargs)
        self.run_trial = run_trial
        self.assertEqual(self.run_clocked()['status'], 'finished')
        self.assertEqual(statuses, ['running', 'running'])
        # Before the second trial: one poll for each of its three seconds. The rest are the loop's own reservations.
        between = self.methods()
        first, second = between.index('suite_settle'), len(between) - 1 - between[::-1].index('suite_settle')
        self.assertEqual(between[first + 1:second].count('suite_reserve'), 1 + 3)

    def test_05_B9_a_different_slot_ends_the_wait_with_no_settlement_and_the_loop_takes_it(self):
        self.reopen_at_start = True
        self.at = {1: self.close, 2: self.next_reply(self.slots[1]), 4: self.close}
        record = self.run_clocked()
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(self.trials, ['0000d002'])
        self.assertEqual(self.methods().count('suite_settle'), 1)

    def test_05_B9_a_missing_slot_ends_the_wait_with_no_worker_or_settlement(self):
        self.reopen_at_start = True
        self.at = {1: self.close, 2: self.next_reply(self.none)}
        record = self.run_clocked()
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual((self.trials, self.methods().count('suite_settle')), ([], 0))

    def test_05_B11_a_suite_without_a_set_checks_nothing_before_its_trials(self):
        # No RED of its own: this pins what stays.
        self.reply = {'ok': True, 'status': 'running', 'request': {'budget_seconds': 1}, 'slot': {
            'ordinal': 0, 'trial_key': 'entry-000-repeat-000', 'case': 'tq9-v2', 'child_request_id': 'child',
            'run_id': None}}
        ran = []

        def run_trial(_directory, request, **_options):
            ran.append(request['case'])
            return {'status': 'evaluated'}

        def call(method, params=None):
            if method == 'suite_settle':
                self.reply = self.none
                return {'ok': True, 'decision': 'advance'}
            return self.call(method, params)
        with patch.object(harness, 'run_trial', run_trial), \
                patch.object(generated_trials, 'set_unreachable', side_effect=AssertionError('the gate ran')):
            record = harness.run_suite(self.directory, {
                'home': str(self.base / 'home'), 'manifest': handmade_manifest(),
                'password_files': {'tq9-v2': str(qualified_set()[1])}}, call=call)
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(ran, ['tq9-v2'])


class KeptNothingTests(GatedTrialFixture):
    def test_05_B3_nothing_decrypted_is_kept_past_its_trial(self):
        # A dict that can be watched without keeping it alive: gone once nothing in the harness holds it.
        class Watched(dict):
            pass
        real, watched = EvaluatorPassword.unlock_bytes, {}
        source, _, _ = qualified_set()
        files = {(source / 'devices' / (device + '.' + name + '.enc')).read_bytes(): device + '.' + name
                 for device in ('0000d001', '0000d002') for name in ('inputs', 'answer')}

        def unlock_bytes(password, data, expected):
            opened = real(password, data, expected)
            if data not in files:
                return opened
            opened = Watched(opened)
            watched[files[data]] = weakref.ref(opened)
            return opened
        kept_at_second_slot = []
        real_admit = generated_trials.admit_device

        def admit_device(device, *args, **kwargs):
            if device.device_id == '0000d002':
                gc.collect()
                kept_at_second_slot.extend(name for name, ref in watched.items() if ref() is not None)
            return real_admit(device, *args, **kwargs)
        with patch.object(EvaluatorPassword, 'unlock_bytes', unlock_bytes), \
                patch.object(generated_trials, 'admit_device', admit_device), self.staged(REFERENCE_A):
            record = self.drive_closing(request_id='kept')
        self.assertEqual(record['status'], 'finished', record)
        self.assertEqual(sorted(watched), ['0000d001.answer', '0000d001.inputs', '0000d002.answer', '0000d002.inputs'])
        self.assertEqual(kept_at_second_slot, [])
        gc.collect()
        self.assertEqual([name for name, ref in watched.items() if ref() is not None], [])


class ConfiguratorWordsTests(SetSuiteFixture):
    def resume_refused(self, suite_id, **request):
        with self.assertRaises(ValueError) as refused:
            drive_suite(self.controller, self.root / 'home-bench/suites', {
                'suite_id': suite_id, 'password_files': {SET: str(self.handle)}, 'set_root': str(self.set_root),
                **request})
        return str(refused.exception)

    def own_words(self, suite_id):
        with self.assertRaises(ValueError) as refused:
            self.controller.call('suite_resume', {'suite_id': suite_id, 'harness_token_sha256': 'b' * 64,
                                                  'harness_pid': 1})
        return str(refused.exception)

    def test_05_B13_its_refusals_are_the_configurators_own_words(self):
        with patch.object(generated_trials, 'hold_set', side_effect=AssertionError('the set was held')):
            started = self.suite_start(self.pins)
            self.assertTrue(started['ok'], started)
            self.assertEqual(self.resume_refused(started['suite_id']), self.own_words(started['suite_id']))
            self.assertEqual(self.own_words(started['suite_id']), 'Only a stopped incomplete suite can resume')
            # Its harness, this process, is still alive.
            self.controller._suites.set_state(started['suite_id'], 'blocked', 'for this test')
            self.assertEqual(self.resume_refused(started['suite_id']), self.own_words(started['suite_id']))
            self.assertEqual(self.own_words(started['suite_id']), 'Previous suite work is still stopping')
        self.assertIn(UNCERTAIN, inspect.getsource(Controller._suite_resume))

    def test_05_B13_a_real_stopped_suite_reads_as_stopped_and_its_entry_pins_compare(self):
        digest = recorded_digest(self.set_root)
        pins = {device: generated_trials.device_pin(self.manifest, device, digest) for device in self.pins}
        started = self.suite_start(pins)
        self.controller._suites.set_state(started['suite_id'], 'blocked', 'for this test')
        self.controller._suites.detach_harness(started['suite_id'])
        calls = []

        def call(method, params=None):
            calls.append(method)
            return {'ok': True} if method == 'ping' else self.controller.call(method, params or {})
        root = copied_set(self)
        directory = self.root / 'resumed'
        with patch.object(harness, 'UNMOUNT_WAIT', 0):
            record = harness.run_suite(directory, {'home': str(self.home), 'suite_id': started['suite_id'],
                                                   'password_files': {SET: str(self.handle)}, 'set_root': str(root)},
                                       call=call)
        self.assertEqual(record['reason'], 'The vault holding set ' + SET + ' was not closed in time')
        self.assertEqual(calls, ['ping', 'suite_result'])


class HeldAdmissionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.source, _, self.manifest = qualified_set()
        self.trial = self.base / 'trial'
        self.trial.mkdir()

    def held(self, root=None, devices=('0000d001', '0000d002')):
        return generated_trials.hold_set(root or self.source, SET, devices, PASSWORD)

    def admit(self, held):
        pin = generated_trials.device_pin(self.manifest, '0000d001', held.evidence_sha256)
        return harness.admit('generated', {}, self.trial, password=PASSWORD, renode='emulator-handle',
                             device=generated_trials.SetDevice(held, '0000d001'), pin=pin)

    def test_05_B4_it_refuses_what_the_path_form_refuses_with_the_same_reasons(self):
        def changing(**fields):
            return lambda record: record.update(fields)

        def forgetting(record):
            del record['status']

        def no_references(record):
            record['devices']['0000d001'] = {'a': True, 'b': False}
        for name, change in (('pending', changing(status='pending')),
                             ('another registration', changing(registration='x')),
                             ('another manifest', changing(manifest_sha256='f' * 64)),
                             ('another identity', changing(grader_identity={'another': 'identity'})),
                             ('failures', changing(failures=['/private/path'])),
                             ('no native process', no_native_process),
                             ('a field missing', forgetting), ('references', no_references)):
            with self.subTest(name):
                root = resealed_set(self, change)
                reasons = generated_sets.admitted(root, SET, '0000d001', PASSWORD)
                self.assertTrue(reasons)
                self.assertEqual(generated_sets.admitted_held(self.held(root), '0000d001'), reasons)
                with self.assertRaises(ValueError) as refused:
                    self.admit(self.held(root))
                self.assertEqual(str(refused.exception), PREFIX + '; '.join(reasons))
        self.assertEqual(generated_sets.admitted_held(self.held(), '0000d001'), [])
        self.assertEqual(generated_sets.admitted(self.source, SET, '0000d001', PASSWORD), [])

    def test_05_B4_a_record_that_is_not_an_object_is_malformed(self):
        root = copied_set(self)
        generated_sets.seal_qualification(root, ['not', 'a', 'record'], PASSWORD)
        self.assertEqual(generated_sets.admitted_held(self.held(root), '0000d001'), ['qualification malformed'])

    def test_05_B4_each_slot_asks_afresh_and_reads_no_file_of_the_set(self):
        root = copied_set(self)
        held = self.held(root)
        shutil.rmtree(root)
        for name, patched, reason in (
                ('no longer registered', {'return_value': {}}, 'manifest refused: ValueError'),
                ('registered under another hash', {'return_value': {SET: 'f' * 64}}, 'manifest refused: ValueError'),
                ('a table it cannot read', {'side_effect': FileNotFoundError('a path')},
                 'manifest refused: FileNotFoundError')):
            with self.subTest(name), patch.object(generated_sets, 'set_registrations', **patched):
                self.assertIn(reason, generated_sets.admitted_held(held, '0000d001'))
                with self.assertRaisesRegex(ValueError, 'is not admitted: ' + reason), \
                        patch.object(generated_sets, 'open_held', side_effect=AssertionError('opened')):
                    self.admit(held)
        with patch.object(generated_sets, 'grader_identity', return_value={'another': 'identity'}), \
                self.assertRaisesRegex(ValueError, 'is not admitted: qualification under another grader identity'):
            self.admit(held)
        with patch.object(generated_sets, 'grader_identity', side_effect=OSError('a path')):
            self.assertEqual(generated_sets.admitted_held(held, '0000d001'), ['grader identity unavailable: OSError'])
        self.assertEqual(list(self.trial.iterdir()), [])
        provider, admission, truth = self.admit(held)
        self.assertEqual(admission, {'calibration_evidence_sha256': held.evidence_sha256})
        self.assertEqual(truth.payload, FIXTURE_ANSWER_KEY)
        self.assertEqual(provider.recipe, FIXTURE_ANSWER_KEY['recipe'])

    def test_05_B4_the_path_forms_stay_for_qualification(self):
        # No RED of its own: this pins what stays.
        self.assertEqual(generated_sets.load_set(self.source, SET), self.manifest)
        self.assertEqual(generated_sets.open_device(self.source, self.manifest, '0000d001', PASSWORD, answer=True)[
            'answer_key'], FIXTURE_ANSWER_KEY)
        self.assertEqual(generated_sets.admitted(self.source, SET, '0000d001', PASSWORD), [])


class HoldSetTests(unittest.TestCase):
    def setUp(self):
        self.source, _, self.manifest = qualified_set()

    def test_05_B1_it_holds_the_manifest_the_record_and_the_named_devices_sealed_files(self):
        held = generated_trials.hold_set(self.source, SET, ['0000d002'], PASSWORD)
        path = self.source / 'qualification.enc'
        record = PASSWORD.unlock(path, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual((held.registration, held.manifest, held.record, held.evidence_sha256),
                         (SET, self.manifest, record, recorded_digest(self.source)))
        devices = self.source / 'devices'
        self.assertEqual(held.sealed, {'0000d002': {'inputs': (devices / '0000d002.inputs.enc').read_bytes(),
                                                    'answer': (devices / '0000d002.answer.enc').read_bytes()}})

    def test_05_B1_it_names_every_unknown_device(self):
        with self.assertRaisesRegex(ValueError,
                                    '^Unknown generated device: 0000dffe; Unknown generated device: 0000dfff$'):
            generated_trials.hold_set(self.source, SET, ['0000dffe', '0000d001', '0000dfff'], PASSWORD)

    def test_05_B1_a_record_it_cannot_decrypt_or_digest_is_unreadable(self):
        with self.assertRaisesRegex(ValueError, '^qualification unreadable: ValueError$'):
            generated_trials.hold_set(self.source, SET, ['0000d001'], EvaluatorPassword('another credential'))
        with patch.object(generated_trials, 'canonical_digest', side_effect=RecursionError('nested')), \
                self.assertRaisesRegex(ValueError, '^qualification unreadable: RecursionError$'):
            generated_trials.hold_set(self.source, SET, ['0000d001'], PASSWORD)

    def test_05_B1_a_sealed_file_outside_the_set_is_refused(self):
        # No RED of its own: `load_set` refused it before this Feature; this pins that it still does.
        root = copied_set(self)
        refused = symlinks_refused(root)
        if refused:
            self.skipTest('This system refuses symlinks: ' + refused)
        outside = root.parent / 'outside'
        outside.write_bytes((root / 'devices/0000d001.answer.enc').read_bytes())
        (root / 'devices/0000d001.answer.enc').unlink()
        (root / 'devices/0000d001.answer.enc').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'A set path leaves the set: devices/0000d001.answer.enc'):
            generated_trials.hold_set(root, SET, ['0000d001'], PASSWORD)


class OpeningHeldFilesTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.source, _, self.manifest = qualified_set()
        self.held = generated_trials.hold_set(self.source, SET, ['0000d001'], PASSWORD)
        self.sealed = self.held.sealed['0000d001']

    def resealed(self, name, content):
        """(a manifest whose entry names the hash of `content` sealed as the file `name`, the sealed files with it)."""
        path = self.base / 'resealed.enc'
        sha = PASSWORD.seal(content, path)
        manifest = copy.deepcopy(self.manifest)
        manifest['devices'][0][name]['sha256'] = sha
        return manifest, {**self.sealed, name: path.read_bytes()}

    def test_05_B3_unlock_bytes_opens_what_unlock_opens_under_the_same_checks(self):
        path = self.source / 'devices/0000d001.answer.enc'
        data, sha = path.read_bytes(), self.manifest['devices'][0]['answer']['sha256']
        self.assertEqual(PASSWORD.unlock_bytes(data, sha), PASSWORD.unlock(path, sha))
        for name, attempt, message in (
                ('another hash', lambda: PASSWORD.unlock_bytes(data, 'f' * 64), 'does not match its pinned hash'),
                ('another password', lambda: EvaluatorPassword('another credential').unlock_bytes(data, sha),
                 'invalid password or envelope'),
                ('no envelope', lambda: PASSWORD.unlock_bytes(b'{}', hashlib.sha256(b'{}').hexdigest()),
                 'invalid password or envelope')):
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                attempt()

    def test_05_B3_open_held_opens_what_open_device_opens(self):
        for answer in (False, True):
            with self.subTest(answer=answer):
                opened = generated_sets.open_held(self.manifest, '0000d001', self.sealed, PASSWORD, answer=answer)
                self.assertEqual(opened, generated_sets.open_device(self.source, self.manifest, '0000d001', PASSWORD,
                                                                    answer=answer))

    def test_05_B3_without_answer_the_answer_is_never_decrypted(self):
        opened = generated_sets.open_held(self.manifest, '0000d001', {**self.sealed, 'answer': b'not ciphertext'},
                                          PASSWORD)
        self.assertEqual(sorted(opened), ['image', 'mission'])

    def test_05_B3_open_held_refuses_what_open_device_refuses(self):
        inputs = PASSWORD.unlock_bytes(self.sealed['inputs'], self.manifest['devices'][0]['inputs']['sha256'])
        for name, field, content, message in (
                ('malformed inputs', 'inputs', {'image_base64': 7}, 'Malformed inputs of device 0000d001'),
                ('another image', 'inputs', {**inputs, 'image_base64': 'AAAA'}, 'The image of device 0000d001'),
                ('another mission', 'inputs', {**inputs, 'mission': {}}, 'The mission of device 0000d001'),
                ('another key', 'answer', {'another': 'key'}, 'The answer key of device 0000d001')):
            manifest, sealed = self.resealed(field, content)
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                generated_sets.open_held(manifest, '0000d001', sealed, PASSWORD, answer=True)
        with self.assertRaisesRegex(ValueError, 'does not match its pinned hash'):
            generated_sets.open_held(self.manifest, '0000d001', {**self.sealed, 'inputs': b'damaged'}, PASSWORD)
        with self.assertRaisesRegex(ValueError, 'Unknown generated device: 0000dfff'):
            generated_sets.open_held(self.manifest, '0000dfff', self.sealed, PASSWORD)

    def device(self, manifest, sealed):
        held = generated_trials.HeldSet(SET, manifest, self.held.record, self.held.evidence_sha256,
                                        {'0000d001': sealed})
        return generated_trials.SetDevice(held, '0000d001')

    def test_05_B3_a_grades_answer_key_is_the_answer_alone_checked_against_its_hash(self):
        with patch.object(generated_sets, 'validate_device', side_effect=AssertionError('checked against the mission')):
            device = self.device(self.manifest, {**self.sealed, 'inputs': b'not ciphertext'})
            unlocked = generated_trials.answer_key(device, PASSWORD)
        self.assertIsInstance(unlocked, UnlockedTruth)
        self.assertEqual((unlocked.payload, unlocked.sha256),
                         (FIXTURE_ANSWER_KEY, self.manifest['devices'][0]['answer']['sha256']))

    def test_05_B3_a_key_the_manifest_does_not_bind_is_refused(self):
        for name, content in (('another key', {'another': 'key'}), ('no object', ['a', 'list'])):
            manifest, sealed = self.resealed('answer', content)
            with self.subTest(name), self.assertRaisesRegex(
                    ValueError, 'The answer key of device 0000d001 does not match its manifest hash'):
                generated_trials.answer_key(self.device(manifest, sealed), PASSWORD)


class WhatTheHarnessKeepsTests(GatedTrialFixture):
    def assertNoRoot(self, root, *texts):
        for text in texts:
            self.assertNotIn(str(root), text)

    def test_05_B16_a_successful_suite_leaves_the_root_nowhere(self):
        root = copied_set(self)
        calls = []
        thread, closed = self.closing(root)
        with self.staged(REFERENCE_A):
            record = self.drive(calls=calls, set_root=str(root), request_id='no-root')
        thread.join(240)
        self.assertTrue(closed.is_set())
        self.assertEqual(record['status'], 'finished', record)
        rows, _ = self.row_results(record['suite_id'])
        self.assertEqual(len(rows), 2)
        self.assertNoRoot(root, json.dumps(calls, default=str), json.dumps(record),
                          *[self.controller._row(row['run_id'])['spec'] for row in rows])
        for path in [*self.home.rglob('*'), *(self.root / 'home-bench').rglob('*.json')]:
            if path.is_file():
                self.assertNotIn(str(root).encode(), path.read_bytes(), path)

    def test_05_B16_a_set_device_holds_no_path(self):
        self.assertEqual([field.name for field in dataclasses.fields(generated_trials.SetDevice)],
                         ['held', 'device_id'])
        root = copied_set(self)
        held = generated_trials.hold_set(root, SET, ['0000d001'], PASSWORD)
        device = generated_trials.SetDevice(held, '0000d001')
        self.assertEqual([field.name for field in dataclasses.fields(held)],
                         ['registration', 'manifest', 'record', 'evidence_sha256', 'sealed'])
        self.assertNoRoot(root, repr(device), repr(held), json.dumps([held.manifest, held.record]), held.registration,
                          held.evidence_sha256)
        for files in held.sealed.values():
            for data in files.values():
                self.assertNotIn(str(root).encode(), data)


class FailuresKeepTheRootTests(GateFixture):
    def assertRootNowhere(self, root, record):
        self.assertNotIn(str(root), json.dumps(record))
        self.assertNotIn(str(root), (self.directory / 'suite.json').read_text())
        self.assertNotIn(str(root), json.dumps(self.calls, default=str))
        self.assertEqual(record['accepted'], False)

    def test_05_B16_the_failures_of_B1_B7_and_B10_name_no_root(self):
        root = copied_set(self)
        (root / 'devices/0000d001.inputs.enc').unlink()
        record = self.finished(*self.start(set_root=str(root)))
        self.assertEqual(record['reason'], PREFIX + 'FileNotFoundError')
        self.assertRootNowhere(root, record)
        self.calls.clear()
        self.directory = self.base / 'limit'
        root = copied_set(self)
        with patch.object(harness, 'UNMOUNT_WAIT', .01):
            record = self.finished(*self.start(set_root=str(root)))
        self.assertEqual(record['reason'], 'The vault holding set ' + SET + ' was not closed in time')
        self.assertRootNowhere(root, record)
        self.calls.clear()
        self.directory = self.base / 'under'
        record = self.finished(*self.start(set_root=str(root), renode=str(root / 'renode')))
        self.assertEqual(record['reason'], PREFIX + 'the emulator executable lies under the set root')
        self.assertRootNowhere(root, record)


class UnreachableTests(unittest.TestCase):
    def test_05_B7_the_default_limit_is_an_hour_watched_once_a_second(self):
        # No RED of its own: these constants came with the wait.
        self.assertEqual((harness.UNMOUNT_WAIT, harness.UNMOUNT_POLL), (3600, 1))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name).resolve()
        self.root = self.base / 'set'
        self.root.mkdir()
        (self.root / 'manifest.json').write_text('{}')

    def test_05_B8_what_counts_as_out_of_reach(self):
        self.assertFalse(generated_trials.set_unreachable(self.root))
        (self.root / 'manifest.json').unlink()
        self.assertTrue(generated_trials.set_unreachable(self.root), 'an empty directory, as a closed volume leaves')
        self.root.rmdir()
        self.assertTrue(generated_trials.set_unreachable(self.root), 'removed')
        (self.base / 'file').write_text('no directory')
        self.assertTrue(generated_trials.set_unreachable(self.base / 'file/set'), 'a parent that is not a directory')

    def test_05_B8_what_keeps_it_reachable(self):
        (self.root / 'manifest.json').unlink()
        (self.root / 'another').write_text('a file')
        self.assertFalse(generated_trials.set_unreachable(self.root), 'a directory that is not empty')
        (self.base / 'file').write_text('no directory')
        self.assertFalse(generated_trials.set_unreachable(self.base / 'file'), 'a root that is a file')

    def stat_raising(self, error, at):
        real = os.stat

        def stat(path, *args, **kwargs):
            if Path(path) == at:
                raise error
            return real(path, *args, **kwargs)
        return patch.object(os, 'stat', stat)

    def test_05_B8_any_other_error_keeps_it_reachable(self):
        manifest = self.root / 'manifest.json'
        for error in (PermissionError('locked'), OSError(errno.EIO, 'no answer')):
            with self.subTest(str(error)):
                with self.stat_raising(error, manifest):
                    self.assertFalse(generated_trials.set_unreachable(self.root), 'the manifest')
                manifest.unlink()
                with self.stat_raising(error, self.root):
                    self.assertFalse(generated_trials.set_unreachable(self.root), 'the root')
                with patch.object(os, 'scandir', side_effect=error):
                    self.assertFalse(generated_trials.set_unreachable(self.root), 'listing the root')
                manifest.write_text('{}')


if __name__ == '__main__':
    unittest.main()
