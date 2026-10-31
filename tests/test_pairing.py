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
def graded(status, equivalent):
    """The fields of a row whose run the evaluator graded: the grade's status, and the equivalence record that says it."""
    return {'equivalence_status': status, 'equivalence': {'status': status, 'equivalent': equivalent}}


SETTLED = {'status': 'finished', 'outcome_category': 'completed', 'workflow_status': 'completed'}
FINDINGS = [{'transcript': 'work/probe/a1/_agent_events.jsonl', 'command': 'cat /vault/key'}]
# What a saved row stores for each outcome, which is what the label is read from (`outcomes.classify`): its status, stop
# category, verdict (none before a run), grade and, for a flagged trial, the findings of the audit. A pass is a counted
# equivalent driver, a flagged one too, since the transcript audit leaves the grade as it was.
ROWS = {'pass': {**SETTLED, 'verdict': 'equivalent', **graded('graded', True)},
        'failure': {**SETTLED, 'verdict': 'not equivalent', **graded('graded', False)},
        'unknown': {**SETTLED, 'verdict': 'unscored', **graded('unknown', None)},
        'fault': {'status': 'blocked', 'outcome_category': 'host', 'workflow_status': 'blocked', 'verdict': 'unscored'},
        'flagged': {**SETTLED, 'verdict': 'equivalent', **graded('graded', True), 'audit': FINDINGS},
        'running': {'status': 'running', 'outcome_category': 'unknown', 'verdict': 'unscored'},
        'not_run': {'status': 'pending', 'outcome_category': 'unknown'}}


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
        pin = {'case_id': entry['case'], 'case_version': '1', 'evaluator_version': '1', 'family': 'generated',
               'scenario_id': 'original', 'case_seed': entry['case_seed'], 'execution': 'actual-agent-emulation',
               'evidence_track': 'firmware', 'scope': 'full-workflow', 'manifest_sha256': 'c' * 64,
               'truth_sha256': 'b' * 64, 'image_hashes': {'image.bin': 'c' * 64}}
        # A device's pin is the same in every report that ran it, and no other device has it.
        pins.append({**pin, 'pin_sha256': digest(pin)})
        slot = {**entry, 'ordinal': index, 'entry_index': index, 'repeat_index': 0, 'trial_key': f'entry-{index:03d}-repeat-000'}
        slots.append(slot)
        run_id = None if outcome == 'not_run' else f'r{index}'
        trials.append({**slot, 'family': 'generated', 'run_id': run_id, **copy.deepcopy(ROWS[outcome]), 'scored': outcome,
                       'interventions': []})
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


def tried_again(report, device, outcome):
    """The report with one more trial row of `device`, as if its suite repeated the device. The identity is as it was, so
    the row suits a pairing test and does not read back as a saved report."""
    row = copy.deepcopy(next(row for row in report['trials'] if row['case'] == device))
    row.update(scored=outcome, run_id=None if outcome == 'not_run' else 'again', trial_key='again')
    report['trials'].append(row)
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
          'execution': lambda report: identity(report).update(execution='physical-device'),
          'evidence_track': lambda report: identity(report).update(evidence_track='hardware'),
          'scope': lambda report: identity(report).update(scope='probe-only'),
          'entry_pins': lambda report: identity(report)['entry_pins'][0].update(truth_sha256='f' * 64),
          'time_policy': lambda report: identity(report)['time_policy'].__setitem__(0, {'clock': 'virtual'}),
          'child_budget_seconds': lambda report: identity(report)['budgets']['effective'].update(child_budget_seconds=21600),
          'max_model_repairs': lambda report: identity(report)['intervention_policy'].update(max_model_repairs=3),
          'scoped_tool_approval': lambda report: identity(report)['intervention_policy'].update(scoped_tool_approval='emulator'),
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
        self.assertEqual(result['excluded'], {'repeated': 0, 'absent': 1, 'ungraded': 1})
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
                                     ({'repeated': 0, 'absent': 0, 'ungraded': 1},
                                      {'both': 0, 'neither': 0, 'only_before': 1, 'only_after': 0}))

    def test_a_device_one_system_lacks_is_excluded(self):
        result = self.pair({'d0': 'pass', 'd5': 'pass'}, {'d0': 'pass', 'd2': 'failure', 'd1': 'pass'})
        self.assertEqual((result['excluded'], result['devices']),
                         ({'repeated': 0, 'absent': 3, 'ungraded': 0},
                          {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 0}))

    def test_a_device_tried_more_than_once_is_excluded_whichever_system_tried_it(self):
        # Catches a device paired from one of its trials, which counts it twice or chooses between its grades: one
        # trial per device per system is the design, and a repeat takes the device out of the pairs.
        before = tried_again(suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass', 'd2': 'failure'}), 'd0', 'pass')
        after = tried_again(suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass', 'd2': 'failure'}), 'd2', 'failure')
        result = pairing.paired_comparison(before, after)
        self.assertEqual((result['excluded'], result['devices']),
                         ({'repeated': 2, 'absent': 0, 'ungraded': 0},
                          {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 0}))
        # Even when the repeat never ran.
        before = tried_again(suite_report('assistive/1', {'d0': 'failure', 'd1': 'failure'}), 'd0', 'not_run')
        result = pairing.paired_comparison(before, suite_report('assistive/1', {'d0': 'failure', 'd1': 'pass'}))
        self.assertEqual((result['excluded'], result['devices']),
                         ({'repeated': 1, 'absent': 0, 'ungraded': 0},
                          {'both': 0, 'neither': 0, 'only_before': 0, 'only_after': 1}))

    def test_each_excluded_device_is_counted_once_under_the_first_reason_that_applies(self):
        # Catches a device counted under two reasons, or under a later one: tried more than once outranks absent, which
        # outranks ungraded. d0 is repeated and absent; d1 absent and ungraded; d2 ungraded alone; d3 and d4 repeated and
        # ungraded; d5 pairs.
        before = suite_report('assistive/1', {'d0': 'pass', 'd1': 'fault', 'd2': 'unknown', 'd3': 'pass', 'd4': 'fault',
                                              'd5': 'pass'})
        after = suite_report('assistive/1', {'d2': 'fault', 'd3': 'failure', 'd4': 'unknown', 'd5': 'pass'})
        result = pairing.paired_comparison(tried_again(tried_again(before, 'd0', 'pass'), 'd4', 'fault'),
                                           tried_again(after, 'd3', 'unknown'))
        self.assertEqual((result['excluded'], result['devices']),
                         ({'repeated': 3, 'absent': 1, 'ungraded': 1},
                          {'both': 1, 'neither': 0, 'only_before': 0, 'only_after': 0}))

    def test_a_comparison_names_no_device_run_or_trial(self):
        # Catches a scoring suite's output that names its sealed devices, as `excluded` once did: a comparison carries
        # aggregates only, and so does every reason it refuses with.
        import re
        named = re.compile(r'\b[df][0-9]\b|\br[0-9]+\b|entry-')
        before = suite_report('assistive/1', [('d0', 'pass'), ('d0', 'pass'), ('d1', 'fault'), ('d2', 'pass'), ('d3', 'failure')])
        after = suite_report('learning/1', {'d2': 'failure', 'd3': 'failure', 'd4': 'unknown'})
        fresh = suite_report('learning/1', {'f0': 'pass', 'f1': 'failure'}, manifest_sha256='9' * 64)
        texts = [json.dumps(pairing.paired_comparison(before, after, leak_check=fresh)),
                 json.dumps(compare_suites(before, after, leak_check=fresh))]
        incompatible = copy.deepcopy(after)
        SHARED['evaluator_revision'](incompatible)
        texts.append(json.dumps(pairing.paired_comparison(before, incompatible)))
        for batch in (before, suite_report('assistive/1', {'f0': 'pass'}, manifest_sha256='9' * 64)):
            with self.assertRaises(ValueError) as caught:
                pairing.paired_comparison(before, after, leak_check=batch)
            texts.append(str(caught.exception))
            texts.append(json.dumps(compare_suites(before, after, leak_check=batch)))
        for text in texts:
            with self.subTest(text=text[:60]):
                self.assertIsNone(named.search(text), text)

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

    def test_systems_must_share_what_a_comparison_holds_fixed(self):
        # Catches a comparison across scoring sets, evaluator revisions, kinds of evidence (an emulated suite beside a
        # physical one), devices, child budgets, repair limits, tool approvals or qualification modes, and a refusal that
        # names the wrong field.
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
                         ['scoring_set', 'evaluator_revision', 'execution', 'evidence_track', 'scope', 'entry_pins',
                          'time_policy', 'child_budget_seconds', 'max_model_repairs', 'scoped_tool_approval', 'qualification'])

    def test_the_pins_and_time_policies_are_compared_for_each_case_both_systems_ran(self):
        # Catches two different devices under one case name paired as one, a swap of two devices' pins that a comparison
        # of the pins pooled across cases cannot see, and a device only one system ran taken to differ.
        def both(change):
            reports = suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'}), suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'})
            change(identity(reports[1]))
            return pairing.paired_comparison(*reports)['incompatible_fields']
        def swap(items):
            items[0], items[1] = items[1], items[0]
        def distinct_policies(items):
            items[0], items[1] = {'clock': 'virtual'}, {'clock': 'wall'}
        self.assertEqual(both(lambda found: swap(found['entry_pins'])), ['entry_pins'])
        self.assertEqual(both(lambda found: found['entry_pins'][1].update(truth_sha256='f' * 64)), ['entry_pins'])
        self.assertEqual(both(lambda found: (distinct_policies(found['time_policy']))), ['time_policy'])
        reports = suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'}), suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'})
        distinct_policies(identity(reports[0])['time_policy'])
        distinct_policies(identity(reports[1])['time_policy'])
        self.assertEqual(pairing.paired_comparison(*reports)['incompatible_fields'], [])
        swap(identity(reports[1])['time_policy'])
        self.assertEqual(pairing.paired_comparison(*reports)['incompatible_fields'], ['time_policy'])
        # A case only one system ran is not compared, whatever its pin.
        before = suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'})
        after = suite_report('assistive/1', {'d0': 'pass', 'd5': 'pass'})
        identity(after)['entry_pins'][1].update(truth_sha256='f' * 64)
        identity(after)['time_policy'][1] = {'clock': 'virtual'}
        self.assertEqual(pairing.paired_comparison(before, after)['incompatible_fields'], [])

    def test_the_entries_of_a_case_compare_as_a_sorted_list_whatever_their_order(self):
        # Catches a device tried twice (two seeds) whose entries one system listed in another order, refused as another
        # device: the pins of a case are compared as the sorted list of their canonical forms.
        before = suite_report('assistive/1', [('d0', 'pass'), ('d0', 'failure'), ('d1', 'pass')])
        after = copy.deepcopy(before)
        found = identity(after)
        for items in (found['manifest']['entries'], found['entry_pins'], found['time_policy']):
            items[0], items[1] = items[1], items[0]
        found['time_policy'][0] = {'clock': 'virtual'}
        found['time_policy'][1] = {'clock': 'wall'}
        identity(before)['time_policy'][0] = {'clock': 'wall'}
        identity(before)['time_policy'][1] = {'clock': 'virtual'}
        self.assertEqual(pairing.paired_comparison(before, after)['incompatible_fields'], [])

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

    def test_a_fresh_batch_matches_the_after_system_in_every_condition_pairing_holds_fixed(self):
        # Catches a "fresh batch" run under other conditions than the scoring set was (another kind of evidence, tool
        # approval, child budget, repair limit or qualification), whose rate would be no check on the scoring set's, and a
        # refusal that does not name the condition.
        before, after = suite_report('assistive/1', {'d0': 'pass'}), suite_report('learning/1', {'d0': 'pass'})
        def refused(batch, name):
            with self.assertRaises(ValueError) as caught:
                pairing.paired_comparison(before, after, leak_check=batch)
            self.assertIn(name, str(caught.exception))
            self.assertNotIn('f0', str(caught.exception))
        for name in ('evaluator_revision', 'execution', 'evidence_track', 'scope', 'child_budget_seconds',
                     'max_model_repairs', 'scoped_tool_approval', 'qualification'):
            batch = suite_report('learning/1', {'f0': 'pass'}, manifest_sha256='9' * 64)
            SHARED[name](batch)
            with self.subTest(name):
                refused(batch, name)
        # An override granted to a trial of the batch changes its child budget too.
        batch = suite_report('learning/1', {'f0': 'pass'}, manifest_sha256='9' * 64)
        identity(batch)['budgets']['effective']['child_budget_overrides'] = [
            {'trial_key': 'entry-000-repeat-000', 'budget_seconds': 21600}]
        refused(batch, 'child_budget_seconds')
        # Its own devices' pins and time policies, and its own scoring set, are its own.
        batch = suite_report('learning/1', {'f0': 'pass'}, manifest_sha256='9' * 64)
        SHARED['entry_pins'](batch)
        SHARED['time_policy'](batch)
        SHARED['scoring_set'](batch)
        self.assertEqual(pairing.paired_comparison(before, after, leak_check=batch)['leak_check']['fresh_rate'], 1.0)

    def test_a_batch_with_the_after_systems_scoring_set_manifest_is_not_fresh_under_any_registration(self):
        # Catches a batch that is the scoring set renamed: its manifest hash is the set's.
        before, after = suite_report('assistive/1', {'d0': 'pass'}), suite_report('learning/1', {'d0': 'pass'})
        batch = suite_report('learning/1', {'f0': 'pass'})
        SHARED['scoring_set'](batch)
        self.assertEqual(identity(batch)['scoring_set'], {'registration': 'generated-set-2', 'manifest_sha256': 'a' * 64})
        with self.assertRaisesRegex(ValueError, 'not fresh.*scoring set'):
            pairing.paired_comparison(before, after, leak_check=batch)

    def test_a_batch_that_shares_a_device_with_the_after_system_is_not_fresh(self):
        # Catches a leak test on a device the system already ran, whatever the batch is called: a device is its pin, not
        # its case name, and the refusal names the condition and no case.
        before = suite_report('assistive/1', {'d0': 'pass', 'd1': 'pass'})
        after = suite_report('learning/1', {'d0': 'pass', 'd1': 'pass'})
        batch = suite_report('learning/1', {'f0': 'pass', 'd1': 'failure'}, manifest_sha256='9' * 64)
        with self.assertRaises(ValueError) as caught:
            pairing.paired_comparison(before, after, leak_check=batch)
        self.assertIn('not fresh', str(caught.exception))
        self.assertIn('device', str(caught.exception))
        self.assertNotIn('d1', str(caught.exception))
        # The same case name under another pin is another device.
        batch = suite_report('learning/1', {'f0': 'pass', 'd1': 'failure'}, manifest_sha256='9' * 64)
        identity(batch)['entry_pins'][1]['pin_sha256'] = '5' * 64
        self.assertEqual(pairing.paired_comparison(before, after, leak_check=batch)['leak_check']['fresh_rate'], 0.5)

    def test_a_leak_check_that_stores_no_outcomes_is_refused_for_its_schema(self):
        # Catches an earlier schema's file refused as 'Unknown trial outcome: None', which says nothing of why: a /2 file
        # stores no outcomes, and a /1 file (no protocol either) is named for its schema before anything else.
        from test_suite_reporting import comparison_fixture
        before, after = suite_report('assistive/1', {'d0': 'pass'}), suite_report('assistive/1', {'d0': 'pass'})
        two = unpaired('assistive/1', {'f0': 'pass'})
        two['schema'] = 'benchmark-suite-report/2'
        del two['pass_rate']
        for row in two['trials']:
            del row['scored']
        for batch, schema in ((two, 'benchmark-suite-report/2'), (comparison_fixture(), 'benchmark-suite-report/1')):
            with self.subTest(schema=schema):
                result = compare_suites(before, after, leak_check=batch)
                self.assertEqual((result['schema'], result['compatible']), ('benchmark-suite-comparison/1', False))
                self.assertIn(schema, result['reason'])
                self.assertNotIn('Unknown trial outcome', result['reason'])

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

    def test_a_scoring_set_needs_the_suite_to_name_its_protocol(self):
        # Catches a scoring set admitted on a suite that names no protocol: its report would be a benchmark-suite-report/1,
        # which no reader accepts with a scoring set, so the producer must not write it.
        from test_suite_reporting import fixture
        from generative_driver.benchmark_support.suite_reporting import _experiment, aggregate_suite
        report = suite_report('assistive/1', {'d0': 'pass'})
        _experiment(report['experiment'])
        del identity(report)['protocol']
        with self.assertRaisesRegex(ValueError, 'scoring set'):
            _experiment(report['experiment'])
        suite, trials, reports, experiment = fixture()
        experiment['comparison_identity']['scoring_set'] = {'registration': 'generated-set-1', 'manifest_sha256': 'a' * 64}
        with self.assertRaisesRegex(ValueError, 'scoring set'):
            aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)

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


class SavedOutcomeTests(ReportCase):
    def test_a_row_is_not_run_exactly_when_it_has_no_run(self):
        # Catches a label that says a started row never ran, and one that says anything else of a row with no run:
        # `classify` gives `not_run` to a row with no run_id and to no other. Each forgery keeps the pass rate matching its
        # labels, so only this check can refuse it.
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        def forged(outcomes, row, **changes):
            report = suite_report('assistive/1', outcomes)
            report['trials'][row].update(changes)
            report['pass_rate'] = pass_rate([trial['scored'] for trial in report['trials']])
            return report
        honest = {'d0': 'pass', 'd1': 'failure', 'd2': 'not_run'}
        self.assertEqual(_saved_report(suite_report('assistive/1', honest))['pass_rate']['not_run'], 1)
        for label, report in (('a started row labelled not_run', forged(honest, 1, scored='not_run')),
                              ('a row with no run labelled fault', forged(honest, 2, scored='fault')),
                              ('a row with no run labelled failure', forged(honest, 2, scored='failure')),
                              ('a not_run row given a run', forged(honest, 2, run_id='r2'))):
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'Saved outcome differs'):
                _saved_report(report)


    def test_a_label_is_the_outcome_its_row_shows_and_no_other_whatever_it_is_relabelled_to(self):
        # Catches a label other than pass and not_run that its row does not bear out. Pairing feeds the stored labels into
        # McNemar and both pass rates, so a failure relabelled fault, unknown, flagged or pass, with the pass rate edited to
        # match, would change the figures of a comparison. Each outcome relabelled as each of the others is refused by the
        # reader and by the comparison; the honest report of each reads.
        from generative_driver.benchmark_support.outcomes import OUTCOMES
        from generative_driver.benchmark_support.suite_reporting import _saved_report
        refusal = {'schema': 'benchmark-suite-comparison/1', 'compatible': False, 'reason': 'Invalid or incomplete saved suite report'}
        for outcome in OUTCOMES:
            honest = suite_report('assistive/1', {'d0': outcome})
            self.assertEqual(_saved_report(honest)['pass_rate'], honest['pass_rate'], outcome)
            for relabelled in OUTCOMES:
                if relabelled == outcome:
                    continue
                forged = copy.deepcopy(honest)
                forged['trials'][0]['scored'] = relabelled
                forged['pass_rate'] = pass_rate([relabelled])
                with self.subTest(outcome=outcome, relabelled=relabelled), self.assertRaises(ValueError):
                    _saved_report(forged)
                self.assertEqual(compare_suites(forged, honest), refusal)


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
                         ({'repeated': 3, 'absent': 0, 'ungraded': 0}, {'both': 0, 'neither': 0, 'only_before': 0, 'only_after': 0}))


class SkillTests(unittest.TestCase):
    def test_the_client_skill_says_reports_on_a_scoring_set_compare_device_by_device(self):
        # Catches a skill that leaves a client comparing reports of one scoring set as whole suites, or comparing series
        # that have no scoring set in common, and one that uses "scoring set" or "fresh batch" without saying what it is.
        path = Path(__file__).resolve().parents[1] / 'src/generative_driver/resources/client/skills/generative-driver/SKILL.md'
        text = ' '.join(path.read_text(encoding='utf-8').split())
        self.assertRegex(text, r'shared scoring set, a fixed, sealed set of devices that every compared system runs, '
                               r'are compared device by device \(`benchmark suite compare`\); other series stay separate\.')
        self.assertRegex(text, r'`--leak-check` takes a fresh batch: newly generated devices, run as a leak check\.')
        # Each term is defined where it first appears.
        for term in ('scoring set', 'fresh batch'):
            self.assertEqual(text.index(term), text.index(term + ', a fixed' if term == 'scoring set' else term + ': newly'))


if __name__ == '__main__':
    unittest.main()
