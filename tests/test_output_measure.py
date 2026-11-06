"""Feature 08, output measure: what a graded trial records of the output its driver printed, at the package invoker on a
temporary folder holding a small driver and at the grader and harness on scripted trials. Finite prints and synthetic
credentials only; never Renode, a real set, a vault or a model run."""
import json
import locale
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark_support import emulated_actions, evaluator, harness
from generative_driver.benchmark_support.emulated_actions import (OutputMeter, canonical_package_invoker,
                                                                  package_invoker)
from generative_driver.benchmark_support.evidence import regrade_v3
from generative_driver.benchmark_support.truth import read_password
from test_evaluation import CASE_TRUTH, EXPECTED, FAIL, HarnessTrialFixture, PASS, files, reference
from tq9_workflow_fixture import capabilities, model

BINDING = {'host': '127.0.0.1', 'port': 1}
RUNTIME_CEILING = 2 * 1024 * 1024
# A driver that does what its call's parameters say: wait, print raw bytes, print a result holding a long text or print
# a long object no runtime writes. A call bound from a capability mapping names none of these and prints DEFAULT.
DRIVER = '''
import json
import sys
import time
parameters = json.loads(sys.argv[sys.argv.index('--parameters') + 1])
time.sleep(parameters.get('sleep', 0))
if 'result_size' in parameters:
    print(json.dumps({'ok': True, 'outputs': {'text': 'x' * parameters['result_size']}, 'units': {}, 'transcript': []}))
elif 'malformed_size' in parameters:
    print('{"outputs": 3, "pad": "' + 'x' * parameters['malformed_size'] + '"}', end='')
else:
    sys.stdout.buffer.write(bytes.fromhex(parameters.get('stdout_hex', b'{"ok": true}'.hex())))
sys.stdout.flush()
sys.stderr.buffer.write(b'e' * parameters.get('stderr_size', 0))
sys.stderr.flush()
sys.exit(parameters.get('exit_code', 0))
'''
DEFAULT = len('{"ok": true}')


def hex_of(text):
    return text.encode('ascii').hex()


class InvokerFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package = Path(temporary.name).resolve() / 'package'
        (self.package / 'driver').mkdir(parents=True)
        (self.package / 'driver/__init__.py').write_text('')
        (self.package / 'driver/__main__.py').write_text(DRIVER)
        self.meter = OutputMeter()

    def call(self, *, meter='own', package=None, **parameters):
        """One call of the driver, as the grader makes one, under this test's meter or `meter`."""
        meter = self.meter if meter == 'own' else meter
        return package_invoker(package or self.package, BINDING, [], meter=meter)('operation', parameters)

    def figures(self):
        return self.meter.largest_bytes, self.meter.calls


class CountingTests(InvokerFixture):
    def test_08_B1_a_call_counts_its_stdout_and_stderr_together(self):
        self.call(stdout_hex=hex_of('{"ok": true}'), stderr_size=30)
        self.assertEqual(self.figures(), (42, 1))

    def test_08_B1_the_largest_call_is_kept_and_each_returned_call_counted(self):
        for size in (10, 500, 20):
            self.call(stderr_size=size)
        self.assertEqual(self.figures(), (DEFAULT + 500, 3))

    def test_08_B1_a_print_that_is_no_result_counts_before_any_parsing(self):
        malformed = self.call(stdout_hex=hex_of('{"outputs": 3}'))
        self.assertEqual(malformed['error']['code'], 'malformed_result')
        self.assertEqual(self.figures(), (14, 1))
        no_result = self.call(stdout_hex=hex_of('not a result at all'))
        self.assertEqual(no_result['error']['code'], 'no_result')
        self.assertEqual(self.figures(), (19, 2))

    def test_08_B1_a_call_that_crashed_after_printing_returned_and_counts(self):
        crashed = self.call(stdout_hex=hex_of('partial'), exit_code=3)
        self.assertEqual(crashed['error']['code'], 'no_result')
        self.assertEqual(self.figures(), (7, 1))

    def test_08_B1_a_timed_out_call_is_not_counted(self):
        with patch.object(emulated_actions, 'PACKAGE_CALL_SECONDS', .2):
            timed_out = self.call(sleep=30)
        self.assertEqual(timed_out['error']['code'], 'timeout')
        self.assertEqual(self.figures(), (0, 0))

    def test_08_B1_a_call_that_could_not_start_is_not_counted(self):
        unstarted = self.call(package=self.package.parent / 'no-such-folder' / 'package')
        self.assertEqual(unstarted['error']['code'], 'spawn')
        self.assertEqual(self.figures(), (0, 0))

    def test_08_B1_the_figure_is_the_utf8_bytes_of_the_text(self):
        self.meter.add('é€', '😀a')
        self.assertEqual(self.figures(), (2 + 3 + 4 + 1, 1))

    def test_08_B1_a_print_that_does_not_decode_counts_as_the_text_it_was_read_as(self):
        if locale.getpreferredencoding(False).lower().replace('-', '') != 'utf8':
            self.skipTest('The replacement text of an undecodable byte depends on the locale encoding')
        self.call(stdout_hex='ff')
        self.assertEqual(self.figures(), (len('�'.encode('utf-8')), 1))

    def test_08_B1_the_canonical_invoker_passes_the_meter_through(self):
        (self.package / 'driver/model.json').write_text(json.dumps(model()))
        invoke = canonical_package_invoker(self.package, capabilities(), BINDING, meter=self.meter)
        invoke('set_duty', {'duty': 5}, ['actuate'])
        self.assertEqual(self.figures(), (DEFAULT, 1))
        # The model's own bounds refuse this before any package process starts: no call returned.
        refused = invoke('set_duty', {'duty': 1001}, ['actuate'])
        self.assertEqual(refused['error']['code'], 'capability_mapping')
        self.assertEqual(self.figures(), (DEFAULT, 1))


class NothingLimitedTests(InvokerFixture):
    def test_08_B2_a_result_several_times_the_runtimes_ceiling_is_read_whole(self):
        size = 3 * RUNTIME_CEILING
        result = self.call(result_size=size)
        self.assertEqual(result['outputs']['text'], 'x' * size)
        # The result's own envelope, around the text the driver printed.
        self.assertGreater(self.meter.largest_bytes, size)
        self.assertEqual(self.meter.calls, 1)

    def test_08_B2_a_flood_that_no_runtime_writes_is_read_whole(self):
        size = 3 * RUNTIME_CEILING
        malformed = self.call(malformed_size=size)
        self.assertEqual(malformed['error']['code'], 'malformed_result')
        printed = size + len('{"outputs": 3, "pad": ""}')
        self.assertEqual(malformed['printed']['characters'], printed)
        self.assertEqual(self.figures(), (printed, 1))

    def test_08_B2_a_flood_on_stderr_is_read_whole(self):
        size = 3 * RUNTIME_CEILING
        self.call(stderr_size=size)
        self.assertEqual(self.figures(), (DEFAULT + size, 1))


class SameResultTests(InvokerFixture):
    def test_08_B3_a_call_gives_the_same_result_with_and_without_a_meter(self):
        missing = self.package.parent / 'no-such-folder' / 'package'
        for name, parameters in (('result', {'result_size': 10}), ('default', {}),
                                 ('stderr', {'stderr_size': 100}),
                                 ('malformed', {'stdout_hex': hex_of('{"outputs": 3}')}),
                                 ('no result', {'stdout_hex': hex_of('not a result')}),
                                 ('spawn', {'package': missing})):
            with self.subTest(name):
                self.assertEqual(self.call(meter=OutputMeter(), **parameters), self.call(meter=None, **parameters))
        with self.subTest('timeout'), patch.object(emulated_actions, 'PACKAGE_CALL_SECONDS', .2):
            self.assertEqual(self.call(meter=OutputMeter(), sleep=30), self.call(meter=None, sleep=30))

    def test_08_B3_the_canonical_invoker_gives_the_same_result_with_and_without_a_meter(self):
        (self.package / 'driver/model.json').write_text(json.dumps(model()))
        with_meter = canonical_package_invoker(self.package, capabilities(), BINDING, meter=self.meter)
        without = canonical_package_invoker(self.package, capabilities(), BINDING)
        request = 'set_duty', {'duty': 5}, ['actuate']
        self.assertEqual(with_meter(*request), without(*request))
        self.assertEqual(self.meter.calls, 1)


def keys_in(value):
    """Every key in a nested record."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys_in(item)}
    return {key for item in value for key in keys_in(item)} if isinstance(value, list) else set()


MEASURED = {'package_output', 'largest_bytes'}
# What the scripted grading's three package calls printed: the middle one is the largest.
PRINTED = (('a' * 100, 'b' * 20), ('c' * 400, 'd' * 100), ('e', ''))


class MeasuredTrialFixture(HarnessTrialFixture):
    def setUp(self):
        super().setUp()
        self.sent = []

    def configurator(self, method, params=None, home=None, **kwargs):
        self.sent.append((method, params))
        return super().configurator(method, params, home, **kwargs)

    def graded(self, name='trial', values=PASS, printed=PRINTED, after=None, **options):
        """One trial through the harness, with stages as `options` say, graded by the scripted reference, whose calls
        printed `printed` and whose end runs `after`."""
        grading = reference(values, printed=printed, after=after)
        with patch(CASE_TRUTH, side_effect=self.truth([])), \
                patch.object(evaluator, 'reference_execute', side_effect=grading):
            return self.trial(name, **options)

    def resumed(self, trial, grading=None):
        """A resume of the trial, graded by `grading`, and refusing any grading when there is none."""
        execute = grading or AssertionError('graded again')
        with patch(CASE_TRUTH, side_effect=self.truth([])), \
                patch.object(evaluator, 'reference_execute', side_effect=execute):
            return self.resume(trial, renode='emulator-handle')


def host_fault():
    raise RuntimeError('Scripted host')


class TrialRecordTests(MeasuredTrialFixture):
    def test_08_B1_a_graded_trial_records_its_largest_output_and_its_calls(self):
        trial, record = self.graded()
        self.assertEqual(record['status'], 'evaluated')
        self.assertEqual(record['package_output'], {'largest_bytes': 500, 'calls': 3})
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 500, 'calls': 3})

    def test_08_B1_a_grading_that_counted_no_call_records_nothing(self):
        trial, record = self.graded(printed=())
        self.assertEqual(record['status'], 'evaluated')
        self.assertNotIn('package_output', harness.read_trial(trial))

    def test_08_B1_a_no_driver_grade_records_nothing(self):
        trial, record = self.graded('no-driver', emit=False)
        self.assertEqual(record['equivalence']['status'], 'no-driver')
        self.assertNotIn('package_output', harness.read_trial(trial))

    def test_08_B1_a_stopped_run_records_nothing(self):
        run, result = self.pending()
        trial = self.root / 'stopped'
        trial.mkdir()
        done = harness.conclude(trial, 'tq9-v2', self.home / 'runs' / run['run_id'], {**result, 'status': 'blocked'},
                                password=self.password_object(), truth_source=self.refused_truth)
        self.assertEqual(done['sealed'], True)
        self.assertNotIn('package_output', done)

    def test_08_B1_an_unscored_grading_records_its_partial_figures(self):
        trial, record = self.graded(after=host_fault)
        self.assertEqual((record['status'], record['equivalence']['status']), ('unscored', 'unscored'))
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 500, 'calls': 3})

    def test_08_B1_the_figure_survives_a_seal_that_failed(self):
        with patch.object(evaluator, 'seal', side_effect=OSError('No space left on device')):
            trial, record = self.graded()
        self.assertEqual((record['status'], record['sealed']), ('awaiting-evaluation', False))
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 500, 'calls': 3})

    def test_08_B1_the_figure_survives_a_post_that_failed(self):
        self.unreachable = ('record_evaluation',)
        trial, record = self.graded()
        self.assertEqual((record['status'], record['sealed']), ('awaiting-evaluation', True))
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 500, 'calls': 3})

    def test_08_B1_a_harness_that_dies_before_its_record_leaves_the_trial_unmeasured(self):
        with patch.object(evaluator, 'seal', side_effect=SystemExit('harness killed after grading')), \
                self.assertRaises(SystemExit):
            self.graded()
        trial = self.root / 'bench/trial'
        self.assertEqual(harness.read_trial(trial)['status'], 'evaluating')
        self.assertNotIn('package_output', harness.read_trial(trial))
        # The resume rebuilds the grade from the draft, which holds no figure.
        resumed = self.resumed(trial)
        self.assertEqual(resumed['status'], 'evaluated')
        self.assertNotIn('package_output', resumed)
        self.assertNotIn('package_output', harness.read_trial(trial))


class LaterGradingTests(MeasuredTrialFixture):
    def test_08_B4_a_later_grading_that_counts_calls_records_its_own_figures_in_their_place(self):
        trial, record = self.graded(after=host_fault)
        self.assertEqual(record['package_output'], {'largest_bytes': 500, 'calls': 3})
        # Smaller than the earlier figure: its own, not the largest of the two.
        resumed = self.resumed(trial, reference(PASS, printed=(('x' * 7, 'y' * 2),)))
        self.assertEqual(resumed['status'], 'evaluated')
        self.assertEqual(resumed['package_output'], {'largest_bytes': 9, 'calls': 1})
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 9, 'calls': 1})

    def test_08_B4_a_final_rebuilt_from_the_draft_keeps_the_earlier_figure(self):
        self.unreachable = ('record_evaluation',)
        trial, record = self.graded()
        self.assertEqual(record['status'], 'awaiting-evaluation')
        self.unreachable = ()
        resumed = self.resumed(trial)
        self.assertEqual(resumed['status'], 'evaluated')
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 500, 'calls': 3})

    def test_08_B4_a_later_grading_that_counts_none_keeps_the_earlier_figure(self):
        trial, _ = self.graded(after=host_fault)
        resumed = self.resumed(trial, RuntimeError('Scripted host, before any call'))
        self.assertEqual(resumed['status'], 'unscored')
        self.assertEqual(harness.read_trial(trial)['package_output'], {'largest_bytes': 500, 'calls': 3})


class SameGradeTests(MeasuredTrialFixture):
    def grade(self, name, run, result, **given):
        return evaluator.grade('tq9-v2', self.root / 'grading' / name, self.home / 'runs' / run['run_id'], result,
                               password=self.password_object(), truth_source=self.unlocked_truth, **given)

    def test_08_B3_a_grade_with_a_meter_returns_the_same_record_and_final_as_without(self):
        run, result = self.pending()
        meter = OutputMeter()
        for values in (PASS, FAIL):
            with self.subTest(values=values), \
                    patch.object(evaluator, 'reference_execute', side_effect=reference(values, printed=PRINTED)):
                measured = self.grade('measured-' + str(values[1]), run, result, meter=meter)
                unmeasured = self.grade('unmeasured-' + str(values[1]), run, result)
                self.assertEqual(measured, unmeasured)
                self.assertFalse(keys_in(measured[1]) & MEASURED)
        self.assertEqual((meter.largest_bytes, meter.calls), (500, 6))

    def test_08_B3_a_final_the_draft_holds_counts_no_call(self):
        run, result = self.pending()
        with patch.object(evaluator, 'reference_execute', side_effect=reference(PASS, printed=PRINTED)):
            self.grade('held', run, result)
        meter = OutputMeter()
        with patch.object(evaluator, 'reference_execute', side_effect=AssertionError('graded again')):
            self.grade('held', run, result, meter=meter)
        self.assertEqual((meter.largest_bytes, meter.calls), (0, 0))

    def test_08_B3_the_evidence_holds_no_figure_and_passes_the_offline_regrade(self):
        from generative_driver.reporting import report_run
        trial, record = self.graded()
        self.assertEqual(record['package_output'], {'largest_bytes': 500, 'calls': 3})
        password = self.password_object()
        evidence = password.unlock(trial / 'run-evidence.enc', record['equivalence']['evidence_sha256'])
        self.assertEqual(evidence['final']['raw'], {'events': [], 'monitor': EXPECTED})
        self.assertFalse(keys_in(evidence) & MEASURED)
        with patch('generative_driver.client.call', side_effect=self.configurator):
            report = report_run(record['run_id'], home=self.home, output=self.root / 'report.json')
        regraded = regrade_v3(report, trial / 'run-evidence.enc', read_password(self.password))
        self.assertEqual(regraded['equivalence'], record['equivalence'])


class StaysWithTheHarnessTests(MeasuredTrialFixture):
    def test_08_B5_the_figure_reaches_no_configurator_call_or_file_of_the_configurators_home(self):
        trial, record = self.graded()
        self.assertEqual(record['package_output'], {'largest_bytes': 500, 'calls': 3})
        self.assertIn('record_evaluation', [method for method, _ in self.sent])
        self.assertFalse(keys_in(json.loads(json.dumps(self.sent, default=str))) & MEASURED)
        for path in files(self.home):
            for text in MEASURED:
                self.assertNotIn(text.encode(), path.read_bytes(), path)


if __name__ == '__main__':
    unittest.main()
