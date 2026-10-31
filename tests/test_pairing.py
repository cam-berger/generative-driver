"""Two systems compared device by device on one scoring set: the exact tests, the pairing of saved suite reports, the leak
check and the `suite compare` command. Scripted reports, never a model run or a real scoring set."""
import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from generative_driver.benchmark_support import pairing, registry
from generative_driver.benchmark_support.outcomes import pass_rate
from generative_driver.benchmark_support.suite_reporting import compare_suites

# The devices of a scoring set (d) and of a fresh batch (f). A real set registers its devices as cases; each test
# registers these for its length.
DEVICES = tuple(prefix + str(number) for prefix in 'df' for number in range(10))
# What a saved row stores for each outcome: its status, its stop category and its verdict (none before a run). A pass is
# a counted equivalent driver, a flagged one too, since the transcript audit leaves the grade as it was.
ROWS = {'pass': ('finished', 'completed', 'equivalent'), 'failure': ('finished', 'completed', 'not equivalent'),
        'unknown': ('finished', 'completed', 'unscored'), 'fault': ('blocked', 'host', 'unscored'),
        'flagged': ('finished', 'completed', 'equivalent'), 'running': ('running', 'unknown', 'unscored'),
        'not_run': ('pending', 'unknown', None)}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def suite_report(protocol, outcomes, manifest_sha256='a' * 64):
    """A saved benchmark-suite-report/3 of one scoring set, with a trial row for each device of `outcomes` (a mapping, or a
    list of (device, outcome) pairs for a device tried more than once) and a comparison identity that names the
    scoring set, the protocol, evaluator revision `b * 64`, a child budget of 10800 seconds and 2 model repairs."""
    pairs = list(outcomes.items()) if isinstance(outcomes, dict) else list(outcomes)
    tried = {}
    entries = []
    for device, _ in pairs:
        entries.append({'case': device, 'scenario': 'original', 'case_seed': tried.get(device, 0)})
        tried[device] = tried.get(device, 0) + 1
    manifest = {'schema': 'benchmark-suite/1', 'id': 'scoring', 'version': '1', 'entries': entries, 'repetitions': 1,
                'child_budget_seconds': 10800, 'suite_budget_seconds': 86400, 'max_active_children': 1}
    pins, slots, trials, snapshots = [], [], [], []
    for index, (entry, (_, outcome)) in enumerate(zip(entries, pairs)):
        pins.append({'case_id': entry['case'], 'case_version': '1', 'evaluator_version': '1', 'family': 'generated',
                     'scenario_id': 'original', 'case_seed': entry['case_seed'], 'execution': 'actual-agent-emulation',
                     'evidence_track': 'firmware', 'scope': 'full-workflow', 'manifest_sha256': 'c' * 64,
                     'truth_sha256': 'b' * 64, 'image_hashes': {'image.bin': 'c' * 64}, 'pin_sha256': f'{index:064x}'})
        slot = {**entry, 'ordinal': index, 'entry_index': index, 'repeat_index': 0, 'trial_key': f'entry-{index:03d}-repeat-000'}
        slots.append(slot)
        status, category, verdict = ROWS[outcome]
        run_id = None if outcome == 'not_run' else f'r{index}'
        trials.append({**slot, 'family': 'generated', 'run_id': run_id, 'status': status, 'outcome_category': category,
                       **({'verdict': verdict} if verdict else {}), 'scored': outcome, 'interventions': []})
        snapshots.append({'trial_key': slot['trial_key'], 'snapshot_sha256': 'd' * 64 if run_id else None})
    counted = sum(outcome in ('pass', 'flagged') for _, outcome in pairs)
    budgets = {'child_budget_seconds': 10800, 'suite_budget_seconds': 86400}
    identity = {'protocol': protocol, 'scoring_set': {'registration': 'generated-set-1', 'manifest_sha256': manifest_sha256},
                'manifest': manifest, 'manifest_sha256': digest(manifest), 'entry_pins': pins, 'trials': slots,
                'repetitions': 1, 'execution': 'actual-agent-emulation', 'evidence_track': 'firmware',
                'scope': 'full-workflow', 'time_policy': [{} for _ in pins], 'evaluator_revision': 'b' * 64,
                'budgets': {'original': budgets, 'effective': {**budgets, 'child_budget_overrides': []}},
                'intervention_policy': {'scoped_tool_approval': None, 'max_model_repairs': 2}}
    return {'schema': 'benchmark-suite-report/3', 'protocol': protocol, 'qualification': 'recorded-controller-verdicts',
            'experiment': {'schema': 'benchmark-suite-experiment/1', 'comparison_identity': identity,
                           'dimensions': {'runtime': 'codex', 'model': 'toy', 'skills_revision': 'b' * 64,
                                          'toolchain_revision': 'c' * 64},
                           'execution_snapshots': snapshots},
            'trials': trials, 'equivalence': {'equivalent': counted, 'denominator': len(pairs), 'rate': counted / len(pairs)},
            'pass_rate': pass_rate([outcome for _, outcome in pairs])}


def identity(report):
    return report['experiment']['comparison_identity']


def unpaired(protocol='assistive/1', outcomes=None):
    """A saved suite report of a suite that ran no scoring set."""
    report = suite_report(protocol, outcomes or {'d0': 'pass'})
    del identity(report)['scoring_set']
    return report


def grade(protocol, mission=True):
    """A well-formed equivalence/1 record of an equivalent driver, as the harness posts it for a run under `protocol`."""
    return {'schema': 'equivalence/1', 'status': 'graded', 'equivalent': True, 'basis': 'evaluator', 'method': 'fresh-final',
            'checks': [{'id': 'final/effect-0/duty', 'passed': True}], 'diff': [], 'passed': 1, 'total': 1,
            'mission': {'name': 'fresh_worker_mission', 'passed': mission}, 'frozen_artifact_sha256': 'e' * 64,
            'capabilities_sha256': 'c' * 64, 'evidence_sha256': 'e' * 64, 'protocol': protocol}


# What each system must share, and a change to a report that breaks it. The order is the one a refusal names them in.
SHARED = {'scoring_set': lambda report: identity(report)['scoring_set'].update(registration='generated-set-2'),
          'evaluator_revision': lambda report: identity(report).update(evaluator_revision='c' * 64),
          'child_budget_seconds': lambda report: identity(report)['budgets']['effective'].update(child_budget_seconds=21600),
          'max_model_repairs': lambda report: identity(report)['intervention_policy'].update(max_model_repairs=3),
          'qualification': lambda report: report.update(qualification='regraded-encrypted-evidence')}


class ReportCase(unittest.TestCase):
    """A test that reads saved suite reports of the devices above."""

    def setUp(self):
        registered = patch.dict(registry.CURRENT_VERSIONS, {device: '1' for device in DEVICES})
        registered.start()
        self.addCleanup(registered.stop)


class StatisticsTests(unittest.TestCase):
    def test_mcnemar_exact(self):
        self.assertEqual((pairing.mcnemar_exact(0, 0), pairing.mcnemar_exact(0, 6), pairing.mcnemar_exact(2, 8)),
                         (1.0, 0.03125, 0.109375))

    def test_mcnemar_does_not_favour_a_side_and_never_exceeds_one(self):
        # Catches a p-value that depends on which system the discordant devices favour, and an even split that reads
        # as more than certain: 3 and 3 give twice the binomial tail of 42/64, which is capped at 1.
        self.assertEqual(pairing.mcnemar_exact(8, 2), pairing.mcnemar_exact(2, 8))
        self.assertEqual(pairing.mcnemar_exact(3, 3), 1.0)

    def test_mcnemar_refuses_counts_no_pair_of_devices_can_give(self):
        # Catches a negative count that shrinks the tail and reads as a significant difference.
        for only_before, only_after in ((-1, 3), (3, -1), (-2, -2)):
            with self.subTest(only_before=only_before, only_after=only_after), self.assertRaises(ValueError):
                pairing.mcnemar_exact(only_before, only_after)

    def test_leak_p_flags_a_clear_leak_and_not_a_close_call(self):
        self.assertLess(pairing.leak_p(10, 10, 2, 10), 0.05)
        self.assertGreater(pairing.leak_p(6, 10, 5, 10), 0.05)

    def test_leak_p_is_the_one_sided_fisher_tail(self):
        # Catches a two-sided tail, a tail that leaves the observed table out, and a rate read the wrong way round. Of
        # the 184756 ways to place the passes of both sets among the scoring set's ten trials: 66 for 10 passes of 12,
        # 92378 for at least 6 of 11, and 124130 for at least 5 of 10.
        self.assertEqual(pairing.leak_p(10, 10, 2, 10), 66 / 184756)
        self.assertEqual(pairing.leak_p(6, 10, 5, 10), 0.5)
        self.assertEqual(pairing.leak_p(5, 10, 5, 10), 124130 / 184756)
        # A scoring set that does worse than the fresh batch is no leak: it cannot have fewer than two passes here.
        self.assertEqual(pairing.leak_p(2, 10, 10, 10), 1.0)

    def test_leak_p_has_nothing_to_say_without_graded_trials_on_both_sides(self):
        # Catches a probability computed from an empty table, which reads as a leak (p = 0) or divides by zero.
        for counts in ((0, 0, 3, 10), (3, 10, 0, 0), (0, 0, 0, 0)):
            with self.subTest(counts=counts):
                self.assertEqual(pairing.leak_p(*counts), 1.0)

    def test_leak_p_refuses_counts_no_batch_can_give(self):
        # Catches more passes than graded trials, which makes the tail empty and the probability zero: a leak
        # suspected from a table that cannot exist.
        for counts in ((11, 10, 2, 10), (-1, 10, 2, 10), (3, 10, 11, 10), (3, 10, -1, 10), (1, 0, 3, 10), (0, -1, 3, 10)):
            with self.subTest(counts=counts), self.assertRaises(ValueError):
                pairing.leak_p(*counts)


class DevicePairingTests(ReportCase):
    def test_devices_pair_across_protocols(self):
        before = suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure', 'd2': 'failure', 'd3': 'fault'})
        after = suite_report('learning/1', {'d0': 'pass', 'd1': 'pass', 'd2': 'failure', 'd3': 'pass', 'd4': 'pass'})
        result = pairing.paired_comparison(before, after)
        self.assertTrue(result['compatible'])
        self.assertIn('protocol', result['changed_dimensions'])
        self.assertEqual(result['devices'], {'both': 1, 'neither': 1, 'only_before': 0, 'only_after': 1})
        self.assertEqual(sorted(result['excluded']), ['d3', 'd4'])
        self.assertEqual(result['mcnemar_p'], 1.0)


class DeviceExclusionTests(ReportCase):
    def pair(self, before, after):
        return pairing.paired_comparison(suite_report('assistive/1', before), suite_report('assistive/1', after))

    def test_a_device_pairs_only_when_each_system_graded_it_once(self):
        # Catches a device counted from a trial that no grade settled, and a device a system left out counted as a
        # failure of it: each such device is excluded, whichever system lacks the grade.
        for outcome in ('unknown', 'fault', 'flagged', 'running', 'not_run'):
            for side in ('before', 'after'):
                systems = {'before': {'d0': 'pass', 'd1': 'pass'}, 'after': {'d0': 'failure', 'd1': 'pass'}}
                systems[side] = {**systems[side], 'd1': outcome}
                with self.subTest(outcome=outcome, side=side):
                    result = self.pair(systems['before'], systems['after'])
                    self.assertEqual((result['excluded'], result['devices']),
                                     (['d1'], {'both': 0, 'neither': 0, 'only_before': 1, 'only_after': 0}))

    def test_a_device_one_system_lacks_is_excluded(self):
        result = self.pair({'d0': 'pass', 'd5': 'pass'}, {'d0': 'pass', 'd2': 'failure', 'd1': 'pass'})
        self.assertEqual((result['excluded'], result['devices']),
                         (['d1', 'd2', 'd5'], {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 0}))

    def test_a_device_tried_more_than_once_is_excluded_whichever_system_tried_it(self):
        # Catches a device paired from one of its trials, which counts it twice or chooses between its grades: one
        # trial per device per system is the design, and a repeat takes the device out of the pairs.
        result = self.pair([('d0', 'pass'), ('d0', 'pass'), ('d1', 'pass'), ('d2', 'failure')],
                           [('d0', 'pass'), ('d1', 'pass'), ('d2', 'failure'), ('d2', 'failure')])
        self.assertEqual((result['excluded'], result['devices']),
                         (['d0', 'd2'], {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 0}))
        # Even when the repeats agree with one another, and when the repeat never ran.
        result = self.pair([('d0', 'failure'), ('d0', 'not_run'), ('d1', 'failure')], {'d0': 'failure', 'd1': 'pass'})
        self.assertEqual((result['excluded'], result['devices']),
                         (['d0'], {'both': 0, 'neither': 0, 'only_before': 0, 'only_after': 1}))

    def test_mcnemar_counts_the_devices_one_system_passes_and_the_other_fails(self):
        # Catches the two sides counted the wrong way round, which would make a regression read as an improvement.
        before = {**{f'd{n}': 'pass' for n in range(8)}, 'd8': 'failure', 'd9': 'failure'}
        after = {**{f'd{n}': 'failure' for n in range(8)}, 'd8': 'pass', 'd9': 'pass'}
        result = self.pair(before, after)
        self.assertEqual(result['devices'], {'both': 0, 'neither': 0, 'only_before': 8, 'only_after': 2})
        self.assertEqual(result['mcnemar_p'], 0.109375)


class CompatibilityTests(ReportCase):
    def test_another_scoring_set_is_incompatible(self):
        other = suite_report('assistive/1', {'d0': 'pass'}, manifest_sha256='9' * 64)
        self.assertEqual(pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), other)['incompatible_fields'],
                         ['scoring_set'])

    def test_a_report_without_a_scoring_set_is_not_paired(self):
        # Catches two reports with no scoring set read as sharing one (none equals none), and a missing field that
        # surfaces as a KeyError instead of a refusal.
        plain = unpaired()
        for before, after in ((plain, suite_report('assistive/1', {'d0': 'pass'})),
                              (suite_report('assistive/1', {'d0': 'pass'}), plain), (plain, copy.deepcopy(plain))):
            with self.assertRaisesRegex(ValueError, 'scoring set'):
                pairing.paired_comparison(before, after)

    def test_systems_must_share_the_scoring_set_evaluator_limits_and_qualification(self):
        # Catches a comparison across scoring sets, evaluator revisions, child budgets, repair limits or qualification
        # modes, and a refusal that names the wrong field.
        for field, change in SHARED.items():
            after = suite_report('assistive/1', {'d0': 'pass'})
            change(after)
            with self.subTest(field=field):
                result = pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), after)
                self.assertEqual((result['compatible'], result['incompatible_fields']), (False, [field]))
        everything = suite_report('assistive/1', {'d0': 'pass'})
        for change in SHARED.values():
            change(everything)
        self.assertEqual(pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), everything)['incompatible_fields'],
                         ['scoring_set', 'evaluator_revision', 'child_budget_seconds', 'max_model_repairs', 'qualification'])

    def test_a_granted_child_budget_override_changes_the_budget(self):
        # Catches a child budget compared by its suite-wide number alone, when a granted override gave a trial more.
        after = suite_report('assistive/1', {'d0': 'pass'})
        identity(after)['budgets']['effective']['child_budget_overrides'] = [
            {'trial_key': 'entry-000-repeat-000', 'budget_seconds': 21600}]
        result = pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), after)
        self.assertEqual((result['compatible'], result['incompatible_fields']), (False, ['child_budget_seconds']))

    def test_what_is_not_shared_may_differ_between_compatible_systems(self):
        # Catches a refusal over the protocol, a dimension or the suite budget: those are what a comparison names.
        after = suite_report('learning/1', {'d0': 'pass'})
        identity(after)['budgets']['effective']['suite_budget_seconds'] = 172800
        after['experiment']['dimensions'].update(model='other', toolchain_revision='d' * 64)
        result = pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), after)
        self.assertEqual((result['compatible'], result['incompatible_fields']), (True, []))

    def test_an_incompatible_comparison_pairs_nothing(self):
        # Catches device counts, pass rates or a p-value set beside each other across different scoring sets, which
        # compare nothing.
        after = suite_report('assistive/1', {'d0': 'failure'})
        SHARED['evaluator_revision'](after)
        self.assertEqual(pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), after),
                         {'schema': 'benchmark-paired-comparison/1', 'compatible': False,
                          'incompatible_fields': ['evaluator_revision']})


class ChangedDimensionTests(ReportCase):
    def compare(self, protocol, **dimensions):
        after = suite_report(protocol, {'d0': 'pass'})
        after['experiment']['dimensions'].update(dimensions)
        result = pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), after)
        return result['changed_dimensions'], result['comparison_kind']

    def test_the_comparison_names_every_dimension_that_changed(self):
        # Catches a protocol or dimension change left out of the comparison, so that a combined change reads as one
        # dimension, and a dimension only one system names counted as unchanged.
        self.assertEqual(self.compare('assistive/1'), ([], 'repeat'))
        self.assertEqual(self.compare('assistive/1', model='other'), (['model'], 'model'))
        self.assertEqual(self.compare('learning/1'), (['protocol'], 'protocol'))
        self.assertEqual(self.compare('assistive/1', max_turns=40), (['max_turns'], 'max_turns'))
        self.assertEqual(self.compare('learning/1', model='other'), (['model', 'protocol'], 'combined-system'))
        self.assertEqual(self.compare('assistive/1', toolchain_revision='d' * 64, skills_revision='e' * 64),
                         (['skills_revision', 'toolchain_revision'], 'combined-system'))


class PassRateTests(ReportCase):
    def test_pass_rate_is_each_whole_report_and_not_the_paired_devices(self):
        # Catches a pass rate taken over the paired devices alone, which hides the faults and unknowns of a system and
        # the devices only one of them tried, and one that drops a count.
        before = suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure', 'd2': 'fault', 'd3': 'unknown', 'd4': 'not_run'})
        after = suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass', 'd2': 'pass', 'd3': 'flagged', 'd4': 'running',
                                             'd5': 'failure'})
        result = pairing.paired_comparison(before, after)
        self.assertEqual(result['pass_rate'], {
            'before': {'passes': 1, 'failures': 1, 'unknown': 1, 'faults': 1, 'flagged': 0, 'running': 0, 'not_run': 1,
                       'planned': 5, 'rate': 0.5},
            'after': {'passes': 3, 'failures': 1, 'unknown': 0, 'faults': 0, 'flagged': 1, 'running': 1, 'not_run': 0,
                      'planned': 6, 'rate': 0.75}})
        # A validated report stores the same figure.
        self.assertEqual(result['pass_rate'], {'before': before['pass_rate'], 'after': after['pass_rate']})

    def test_a_system_with_no_graded_trial_has_no_rate(self):
        result = pairing.paired_comparison(suite_report('assistive/1', {'d0': 'fault', 'd1': 'unknown'}),
                                           suite_report('assistive/1', {'d0': 'pass'}))
        self.assertEqual((result['pass_rate']['before']['rate'], result['pass_rate']['after']['rate']), (None, 1.0))

    def test_a_stored_outcome_that_is_no_outcome_is_refused_not_dropped(self):
        # Catches a row whose outcome is missing or invented, counted nowhere and so missing from the pass rate.
        for scored in (None, 'stopped', 'PASS'):
            report = suite_report('assistive/1', {'d0': 'pass'})
            report['trials'][0]['scored'] = scored
            with self.subTest(scored=scored), self.assertRaises(ValueError):
                pairing.paired_comparison(report, suite_report('assistive/1', {'d0': 'pass'}))


class LeakCheckTests(ReportCase):
    def leak(self, scoring, fresh, protocol='learning/1'):
        """The leak check of the after system's outcomes on its scoring set against its fresh batch's."""
        batch = suite_report(protocol, fresh, manifest_sha256='9' * 64)
        return pairing.paired_comparison(suite_report(protocol, {'d0': 'pass'}), suite_report(protocol, scoring),
                                         leak_check=batch)['leak_check']

    def test_there_is_no_leak_check_unless_one_is_given(self):
        result = pairing.paired_comparison(suite_report('assistive/1', {'d0': 'pass'}), suite_report('assistive/1', {'d0': 'pass'}))
        self.assertIn('leak_check', result)
        self.assertIsNone(result['leak_check'])

    def test_a_scoring_set_far_better_than_fresh_devices_is_suspected_of_leaking(self):
        scoring = {f'd{n}': 'pass' for n in range(10)}
        fresh = {f'f{n}': 'pass' if n < 2 else 'failure' for n in range(10)}
        self.assertEqual(self.leak(scoring, fresh),
                         {'scoring_rate': 1.0, 'fresh_rate': 0.2, 'p': 66 / 184756, 'suspected': True})

    def test_a_scoring_set_no_better_than_fresh_devices_is_not(self):
        scoring = {f'd{n}': 'pass' if n < 6 else 'failure' for n in range(10)}
        fresh = {f'f{n}': 'pass' if n < 5 else 'failure' for n in range(10)}
        self.assertEqual(self.leak(scoring, fresh), {'scoring_rate': 0.6, 'fresh_rate': 0.5, 'p': 0.5, 'suspected': False})

    def test_a_leak_is_suspected_below_five_percent_and_not_at_it(self):
        # Catches a threshold read as at most 5%: 3 of 3 against 0 of 3 has a probability of exactly 1/20.
        passes = lambda n, outcome, prefix: {f'{prefix}{i}': outcome for i in range(n)}
        self.assertEqual(self.leak(passes(3, 'pass', 'd'), passes(3, 'failure', 'f')),
                         {'scoring_rate': 1.0, 'fresh_rate': 0.0, 'p': 0.05, 'suspected': False})
        self.assertEqual(self.leak(passes(4, 'pass', 'd'), passes(4, 'failure', 'f')),
                         {'scoring_rate': 1.0, 'fresh_rate': 0.0, 'p': 1 / 70, 'suspected': True})

    def test_the_probability_counts_graded_trials_and_not_planned_ones(self):
        # Catches a probability over planned trials, which lets faults, unknowns and unrun devices count as graded:
        # 3 of 4 graded against 1 of 4 graded, whatever else each batch planned.
        scoring = {'d0': 'pass', 'd1': 'pass', 'd2': 'pass', 'd3': 'failure', **{f'd{n}': 'fault' for n in range(4, 10)}}
        fresh = {'f0': 'pass', 'f1': 'failure', 'f2': 'failure', 'f3': 'failure', 'f4': 'unknown', 'f5': 'not_run'}
        self.assertEqual(self.leak(scoring, fresh), {'scoring_rate': 0.75, 'fresh_rate': 0.25, 'p': 17 / 70, 'suspected': False})

    def test_a_batch_with_no_graded_trial_gives_no_rate_and_no_suspicion(self):
        self.assertEqual(self.leak({'d0': 'pass'}, {'f0': 'fault'}),
                         {'scoring_rate': 1.0, 'fresh_rate': None, 'p': 1.0, 'suspected': False})
        self.assertEqual(self.leak({'d0': 'unknown'}, {'f0': 'pass'}),
                         {'scoring_rate': None, 'fresh_rate': 1.0, 'p': 1.0, 'suspected': False})

    def test_the_leak_check_must_be_the_after_systems_fresh_batch(self):
        # Catches a leak test on another system or on the scoring set itself, which measures nothing, and a refusal
        # that does not say what differs.
        after = suite_report('learning/1', {'d0': 'pass'})
        before = suite_report('assistive/1', {'d0': 'pass'})

        def refused(batch, *words):
            with self.assertRaises(ValueError) as caught:
                pairing.paired_comparison(before, after, leak_check=batch)
            for word in words:
                self.assertIn(word, str(caught.exception))
        refused(suite_report('assistive/1', {'f0': 'pass'}, manifest_sha256='9' * 64), 'protocol')
        other = suite_report('learning/1', {'f0': 'pass'}, manifest_sha256='9' * 64)
        other['experiment']['dimensions']['model'] = 'other'
        refused(other, 'dimensions')
        other = suite_report('learning/1', {'f0': 'pass'}, manifest_sha256='9' * 64)
        SHARED['evaluator_revision'](other)
        refused(other, 'evaluator_revision')
        other['experiment']['dimensions']['model'] = 'other'
        refused(other, 'dimensions', 'evaluator_revision')
        # The after system's own scoring set is no fresh batch, even when it is every other way the same system.
        refused(suite_report('learning/1', {'d0': 'pass'}), 'not fresh', 'scoring set')
        refused(copy.deepcopy(after), 'not fresh')

    def test_a_fresh_batch_may_carry_another_scoring_set_or_none(self):
        before = after = suite_report('assistive/1', {'d0': 'pass'})
        batch = suite_report('assistive/1', {'f0': 'failure'}, manifest_sha256='9' * 64)
        self.assertEqual(pairing.paired_comparison(before, after, leak_check=batch)['leak_check']['fresh_rate'], 0.0)
        del identity(batch)['scoring_set']
        self.assertEqual(pairing.paired_comparison(before, after, leak_check=batch)['leak_check']['fresh_rate'], 0.0)


class ScoringSetIdentityTests(ReportCase):
    def test_a_saved_report_may_name_a_scoring_set(self):
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        saved = _saved_report(suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure'}))
        self.assertEqual(identity(saved)['scoring_set'], {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64})

    def test_the_experiment_keeps_the_scoring_set_in_the_identity_it_returns(self):
        # Catches an experiment that drops the scoring set, so that the report a suite exports cannot be paired, and
        # one that names a scoring set for a suite that ran none: a key marks a report as a scoring set's.
        from generative_driver.benchmark_support.suite_reporting import _experiment
        report = suite_report('assistive/1', {'d0': 'pass'})
        self.assertEqual(_experiment(report['experiment'])['comparison_identity']['scoring_set'],
                         {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64})
        del identity(report)['scoring_set']
        self.assertNotIn('scoring_set', _experiment(report['experiment'])['comparison_identity'])

    def test_a_scoring_set_is_a_registration_and_a_manifest_hash_and_nothing_else(self):
        # Catches a scoring set that is not a mapping, one with a field missing or added, a registration that names
        # nothing and a manifest hash that is not a lowercase sha256.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        good = {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64}
        invalid = [None, [], 'generated-set-1', {}, {'registration': 'generated-set-1'}, {'manifest_sha256': 'a' * 64},
                   {**good, 'extra': 'value'}, {**good, 'registration': ''}, {**good, 'registration': '  '},
                   {**good, 'registration': 7}, {**good, 'registration': None}, {**good, 'manifest_sha256': 'a' * 63},
                   {**good, 'manifest_sha256': 'A' * 64}, {**good, 'manifest_sha256': 'g' * 64},
                   {**good, 'manifest_sha256': 7}, {**good, 'manifest_sha256': None}]
        for scoring_set in invalid:
            report = suite_report('assistive/1', {'d0': 'pass'})
            identity(report)['scoring_set'] = scoring_set
            with self.subTest(scoring_set=scoring_set), self.assertRaisesRegex(ValueError, 'scoring set'):
                _saved_report(report)

    def test_only_a_suite_report_3_may_carry_a_scoring_set(self):
        # Catches an earlier report that names a scoring set, which stores no outcome to pair: the same file without
        # the scoring set reads as it did.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        earlier = suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure'})
        earlier['schema'] = 'benchmark-suite-report/2'
        del earlier['pass_rate']
        for row in earlier['trials']:
            del row['scored']
        scoring_set = identity(earlier).pop('scoring_set')
        self.assertEqual(_saved_report(earlier)['schema'], 'benchmark-suite-report/2')
        identity(earlier)['scoring_set'] = scoring_set
        with self.assertRaisesRegex(ValueError, 'scoring set'):
            _saved_report(earlier)

    def test_a_scoring_set_report_may_name_any_protocol_of_the_form_name_and_number(self):
        # Catches a protocol check that reads only assistive/1, so that a learning/1 report cannot be read at all, and
        # one that lets a name or number of any other shape stand for a series.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        for protocol in ('assistive/1', 'learning/1', 'learning/12'):
            with self.subTest(protocol=protocol):
                saved = _saved_report(suite_report(protocol, {'d0': 'pass'}))
                self.assertEqual((saved['protocol'], identity(saved)['protocol']), (protocol, protocol))
        for protocol in ('Learning/1', 'learning/0', 'learning/-1', 'learning/1.5', 'learning/1/2', 'learning', '/1',
                         'learning/', 'learning /1', 'learning/1\n', '', ' learning/1', None, 1, ['learning/1']):
            report = suite_report('learning/1', {'d0': 'pass'})
            report['protocol'] = identity(report)['protocol'] = protocol
            with self.subTest(protocol=protocol), self.assertRaisesRegex(ValueError, 'protocol'):
                _saved_report(report)

    def test_a_report_without_a_scoring_set_keeps_the_assistive_protocol_alone(self):
        # Catches the protocol names a scoring set allows leaking to reports that name no scoring set, which stay
        # separate series.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        plain = unpaired('learning/1')
        with self.assertRaisesRegex(ValueError, 'protocol'):
            _saved_report(plain)
        identity(plain)['protocol'] = plain['protocol'] = 'assistive/1'
        self.assertEqual(_saved_report(plain)['protocol'], 'assistive/1')


class ReportProtocolTests(ReportCase):
    def test_an_equivalent_report_is_judged_under_the_protocol_it_names(self):
        # Catches a saved report revalidated under this installation's protocol (assistive/1), which refuses or
        # miscounts a learning/1 report, and a grade accepted under a protocol other than its report's.
        from generative_driver.benchmark_support.suite_reporting import _report_equivalent
        report = lambda protocol, grading=None: {
            'schema': 'benchmark-report/3', 'protocol': protocol, 'workflow_status': 'completed', 'verdict': 'equivalent',
            'equivalence': grade(grading or protocol)}
        self.assertTrue(_report_equivalent(report('assistive/1')))
        self.assertTrue(_report_equivalent(report('assistive/1'), 'assistive/1'))
        self.assertTrue(_report_equivalent(report('learning/1'), 'learning/1'))
        self.assertFalse(_report_equivalent(report('learning/1')))
        self.assertFalse(_report_equivalent(report('assistive/1'), 'learning/1'))
        self.assertFalse(_report_equivalent(report('learning/1'), 'assistive/1'))
        # A grade names the protocol of its own report.
        self.assertFalse(_report_equivalent(report('learning/1', 'assistive/1'), 'learning/1'))
        self.assertFalse(_report_equivalent(report('assistive/1', 'learning/1')))
        grading = report('learning/1')['equivalence']
        del grading['protocol']
        self.assertFalse(_report_equivalent({**report('learning/1'), 'equivalence': grading}, 'learning/1'))

    def test_a_saved_report_counts_its_joint_successes_under_its_own_protocol(self):
        # Catches a learning/1 report whose equivalent driver with a passed mission reads as no joint success, so that
        # its stored count is refused, and a count the rows do not support accepted under either protocol.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        joint = lambda passed: {'passed': passed, 'denominator': 2, 'rate': passed / 2}
        for protocol in ('assistive/1', 'learning/1'):
            report = suite_report(protocol, {'d0': 'pass', 'd1': 'failure'})
            report['trials'][0]['equivalence'] = grade(protocol)
            with self.subTest(protocol=protocol):
                self.assertEqual(_saved_report({**report, 'equivalent_and_mission': joint(1)})['equivalent_and_mission'], joint(1))
                self.assertEqual(_saved_report(report)['equivalent_and_mission'], joint(1))
                for stored in (joint(0), joint(2)):
                    with self.assertRaisesRegex(ValueError, 'joint'):
                        _saved_report({**report, 'equivalent_and_mission': stored})
                # A failed mission is no joint success.
                report['trials'][0]['equivalence'] = grade(protocol, mission=False)
                self.assertEqual(_saved_report(report)['equivalent_and_mission'], joint(0))
                with self.assertRaisesRegex(ValueError, 'joint'):
                    _saved_report({**report, 'equivalent_and_mission': joint(1)})

    def test_a_saved_report_whose_grade_names_another_protocol_counts_no_joint_success(self):
        # Catches a grade of one protocol counted in a report of another, which a report never mixes.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        report = suite_report('learning/1', {'d0': 'pass', 'd1': 'failure'})
        report['trials'][0]['equivalence'] = grade('assistive/1')
        self.assertEqual(_saved_report(report)['equivalent_and_mission'], {'passed': 0, 'denominator': 2, 'rate': 0.0})


class RoutingTests(ReportCase):
    def test_compare_suites_routes_scoring_sets(self):
        self.assertEqual(compare_suites(suite_report('assistive/1', {'d0': 'pass'}), suite_report('learning/1', {'d0': 'pass'}))['schema'],
                         'benchmark-paired-comparison/1')

    def test_two_reports_of_one_protocol_that_carry_a_scoring_set_pair_too(self):
        result = compare_suites(suite_report('assistive/1', {'d0': 'pass', 'd1': 'failure'}),
                                suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'}))
        self.assertEqual((result['schema'], result['comparison_kind'], result['devices']),
                         ('benchmark-paired-comparison/1', 'repeat', {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 1}))

    def test_systems_on_different_scoring_sets_get_the_pairing_refusal(self):
        result = compare_suites(suite_report('assistive/1', {'d0': 'pass'}),
                                suite_report('assistive/1', {'d0': 'pass'}, manifest_sha256='9' * 64))
        self.assertEqual(result, {'schema': 'benchmark-paired-comparison/1', 'compatible': False,
                                  'incompatible_fields': ['scoring_set']})

    def test_every_report_it_is_given_must_read_back(self):
        # Catches a leak check, or either system's report, used without the checks a saved report gets.
        forged = suite_report('assistive/1', {'d0': 'pass'})
        forged['trials'][0]['scored'] = 'failure'
        refusal = {'schema': 'benchmark-suite-comparison/1', 'compatible': False, 'reason': 'Invalid or incomplete saved suite report'}
        for position in range(3):
            reports = [suite_report('assistive/1', {'d0': 'pass'}), suite_report('assistive/1', {'d0': 'pass'}),
                       suite_report('assistive/1', {'f0': 'pass'}, manifest_sha256='9' * 64)]
            with self.subTest(position=position):
                self.assertNotEqual(compare_suites(reports[0], reports[1], leak_check=reports[2]), refusal)
                reports[position] = forged
                self.assertEqual(compare_suites(reports[0], reports[1], leak_check=reports[2]), refusal)
        missing = str(Path(tempfile.gettempdir()) / 'no such report.json')
        self.assertEqual(compare_suites(suite_report('assistive/1', {'d0': 'pass'}), suite_report('assistive/1', {'d0': 'pass'}),
                                        leak_check=missing), refusal)

    def test_a_replay_is_never_paired_with_a_model_run(self):
        # Catches a replay suite set beside a model suite on a shared scoring set: that refusal comes before pairing,
        # and before the protocol check too.
        for protocol in ('assistive/1', 'learning/1'):
            replay = suite_report(protocol, {'d0': 'pass'})
            replay['experiment']['dimensions'].update(runtime='replay', replay_candidates={'d0': 'f' * 64})
            for before, after in ((suite_report('assistive/1', {'d0': 'pass'}), replay),
                                  (replay, suite_report('assistive/1', {'d0': 'pass'}))):
                with self.subTest(protocol=protocol):
                    result = compare_suites(before, after)
                    self.assertEqual((result['schema'], result['compatible']), ('benchmark-suite-comparison/1', False))
                    self.assertIn('Replay results and model runs are separate series', result['reason'])

    def test_a_paired_comparison_can_take_a_leak_check(self):
        after = suite_report('learning/1', {f'd{n}': 'pass' for n in range(10)})
        fresh = suite_report('learning/1', {f'f{n}': 'pass' if n < 2 else 'failure' for n in range(10)}, manifest_sha256='9' * 64)
        result = compare_suites(suite_report('assistive/1', {'d0': 'pass'}), after, leak_check=fresh)
        self.assertEqual(result['leak_check'], {'scoring_rate': 1.0, 'fresh_rate': 0.2, 'p': 66 / 184756, 'suspected': True})
        self.assertIsNone(compare_suites(suite_report('assistive/1', {'d0': 'pass'}), after)['leak_check'])

    def test_a_leak_check_that_is_not_the_after_systems_fresh_batch_is_a_refusal_with_its_reason(self):
        after = suite_report('learning/1', {'d0': 'pass'})
        result = compare_suites(suite_report('assistive/1', {'d0': 'pass'}), after, leak_check=copy.deepcopy(after))
        self.assertEqual(result, {'schema': 'benchmark-suite-comparison/1', 'compatible': False,
                                  'reason': 'The leak check is not fresh: it carries the scoring set it checks'})

    def test_a_leak_check_without_two_scoring_set_reports_is_refused_and_never_ignored(self):
        # Catches a leak check dropped when the comparison does not pair, which reads as a comparison that passed one.
        fresh = suite_report('assistive/1', {'f0': 'pass'}, manifest_sha256='9' * 64)
        for before, after in ((unpaired(), unpaired()), (suite_report('assistive/1', {'d0': 'pass'}), unpaired()),
                              (unpaired(), suite_report('assistive/1', {'d0': 'pass'}))):
            result = compare_suites(before, after, leak_check=fresh)
            self.assertEqual((result['schema'], result['compatible']), ('benchmark-suite-comparison/1', False))
            self.assertIn('leak check', result['reason'])

    def test_a_report_with_no_scoring_set_is_compared_as_before(self):
        # Catches pairing of reports outside a scoring set, which stay separate series: they compare as whole suites,
        # and within one protocol.
        plain = compare_suites(unpaired(), unpaired())
        self.assertEqual((plain['schema'], plain['compatible'], plain['change_scope']),
                         ('benchmark-suite-comparison/1', True, 'unchanged'))
        mixed = compare_suites(suite_report('assistive/1', {'d0': 'pass'}), unpaired())
        self.assertEqual((mixed['schema'], mixed['compatible'], mixed['reason']),
                         ('benchmark-suite-comparison/1', False, 'Frozen experiment identities differ'))
        self.assertIn('Protocols differ', compare_suites(suite_report('learning/1', {'d0': 'pass'}), unpaired())['reason'])


class CommandTests(ReportCase):
    def compare(self, *arguments):
        """`benchmark suite compare` as the command line runs it: (exit code, the JSON it prints)."""
        from generative_driver.benchmark import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(['suite', 'compare', *arguments])
        return code, json.loads(output.getvalue())

    def test_suite_compare_takes_the_fresh_batch_as_a_leak_check(self):
        reports = {'before': suite_report('assistive/1', {'d0': 'pass'}),
                   'after': suite_report('learning/1', {f'd{n}': 'pass' for n in range(10)}),
                   'fresh': suite_report('learning/1', {f'f{n}': 'pass' if n < 2 else 'failure' for n in range(10)},
                                         manifest_sha256='9' * 64)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'saved reports'
            root.mkdir()
            paths = {}
            for name, report in reports.items():
                paths[name] = str(root / (name + '.json'))
                Path(paths[name]).write_text(json.dumps(report), encoding='utf-8')
            code, result = self.compare(paths['before'], paths['after'], '--leak-check', paths['fresh'])
            self.assertEqual((code, result['schema'], result['leak_check']['suspected']), (0, 'benchmark-paired-comparison/1', True))
            code, result = self.compare(paths['before'], paths['after'])
            self.assertEqual((code, result['schema'], result['leak_check']), (0, 'benchmark-paired-comparison/1', None))
            # A leak check that is not fresh, and one that no scoring-set pair can take, fail the command.
            code, result = self.compare(paths['before'], paths['after'], '--leak-check', paths['after'])
            self.assertEqual((code, result['compatible'], result['schema']), (1, False, 'benchmark-suite-comparison/1'))
            self.assertIn('not fresh', result['reason'])
            code, result = self.compare(paths['before'], paths['before'], '--leak-check', paths['before'])
            self.assertEqual((code, result['compatible']), (1, False))


class AggregatedReportTests(ReportCase):
    def test_a_report_the_aggregate_wrote_keeps_its_scoring_set_and_its_outcomes_into_the_pairing(self):
        # Catches an aggregate that drops the scoring set from the identity it exports, so that its own report cannot be
        # paired, and a pairing that reads anything but the outcomes the rows store. Its nine trials are three tries of
        # each of three cases, so every device is excluded as tried more than once.
        from test_assistive_reports import assistive_fixture
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        suite, trials, reports, experiment = assistive_fixture()
        scoring_set = {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64}
        experiment['comparison_identity']['scoring_set'] = scoring_set
        saved = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        self.assertEqual(identity(saved)['scoring_set'], scoring_set)
        result = compare_suites(saved, saved)
        self.assertEqual((result['schema'], result['compatible'], result['comparison_kind']),
                         ('benchmark-paired-comparison/1', True, 'repeat'))
        self.assertEqual(result['pass_rate'], {'before': saved['pass_rate'], 'after': saved['pass_rate']})
        self.assertEqual(saved['pass_rate']['planned'], 9)
        self.assertEqual((result['excluded'], result['devices']),
                         (['parameter-store-v1', 'sampled-sensor-v1', 'tq9-v2'], {'both': 0, 'neither': 0, 'only_before': 0, 'only_after': 0}))


class SkillTests(unittest.TestCase):
    def test_the_client_skill_says_reports_on_a_scoring_set_compare_device_by_device(self):
        # Catches a skill that leaves a client comparing reports of one scoring set as whole suites, or comparing series
        # that have no scoring set in common.
        path = Path(__file__).resolve().parents[1] / 'src/generative_driver/resources/client/skills/generative-driver/SKILL.md'
        text = ' '.join(path.read_text(encoding='utf-8').split())
        self.assertRegex(text, r'scoring set are compared device by device[^.]*`--leak-check`[^.]*; other series stay separate\.')


if __name__ == '__main__':
    unittest.main()
