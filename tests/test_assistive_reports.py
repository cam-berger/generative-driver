"""Reports, offline scores and comparisons under the assistive protocol: an assistive run's report is
benchmark-report/3, whose success is its one recorded equivalence verdict; `score` regrades it from the trial's sealed
benchmark-run-evidence/2; nothing compares it with a report of another protocol. Scripted stages on synthetic TCP peers,
a toy contract and a scripted reference execution; never Renode, real truth or a real password."""
import contextlib
import copy
import hashlib
import io
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from test_configurator import scripted_stages
from test_evaluation import EXPECTED, FAIL, PASS, TOKEN, evaluation, files, reference
from test_harness import EVIDENCE, unstarted_provider
from test_suite_harness import EMULATOR, HarnessSuiteFixture, manifest

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
GATED = ('accepted_gates', 'evaluations', 'final_evaluation', 'evaluator_verdicts')


class AssistiveFixture(HarnessSuiteFixture):
    """Emulated trials through the bench harness against the in-process configurator, graded after their runs."""

    def configurator(self, method, params=None, home=None, **kwargs):
        if method == 'ping':
            return {'ok': True}
        try:
            return self.controller.call(method, params or {})
        except Exception as error:
            return {'ok': False, 'error': type(error).__name__, 'reason': str(error)}

    def trial(self, name, outcome=PASS, runtime=None, **options):
        """One trial whose frozen driver is graded with `outcome` (values, or an exception the evaluator meets):
        (trial dir, trial record)."""
        from generative_driver.benchmark_support import evaluator, harness
        trial = self.root / 'bench' / name
        def grade(*args, **kwargs):
            if isinstance(outcome, Exception):
                raise outcome
            return reference(outcome)(*args, **kwargs)
        provider = unstarted_provider(self, self.image, trial, self.peers)
        with scripted_stages(self.controller, check=self.check(**options), runtime=runtime), \
                patch.object(harness, 'admit', return_value=(provider, {'calibration_evidence_sha256': EVIDENCE})), \
                patch('generative_driver.client.call', side_effect=self.configurator), \
                patch.object(evaluator, 'case_truth', return_value={'recipe': {}}), \
                patch.object(evaluator, 'reference_execute', side_effect=grade):
            record = harness.run_trial(trial, {'trial_id': name, 'home': str(self.home), 'goal': harness.GOAL, 'case': 'tq9-v2',
                                               'case_options': {'scenario_id': 'original'}, 'effects': ['write', 'actuate'],
                                               'budget_seconds': 120, 'password_file': str(self.password)})
        return trial, record

    def owner(self):
        return patch('generative_driver.client.call', side_effect=lambda method, params, **kw: self.controller.call(method, params))

    def report(self, run_id, name='report'):
        from generative_driver.reporting import report_run
        with self.owner():
            return report_run(run_id, home=self.home, output=self.root / 'reports' / name / 'report.json')

    def score(self, report, evidence, password=None):
        from generative_driver.benchmark import score
        return score(copy.deepcopy(report), password or self.password, evidence_path=evidence)


class RunReportTests(AssistiveFixture):
    def test_an_assistive_run_reports_its_one_recorded_verdict_as_benchmark_report_3(self):
        # Catches an assistive run reported under the gated schema, a report whose success is not the recorded verdict,
        # gates, stage evaluations or stage verdicts in it, and a report or its Markdown that carries an expected value.
        for name, outcome, label, diff in (('good', PASS, 'equivalent', []),
                                           ('wrong', FAIL, 'not equivalent', [{'id': 'final/effect-1/duty', 'reason': 'value mismatch'}])):
            with self.subTest(driver=name):
                trial, record = self.trial(name, outcome)
                result = self.controller.call('result', {'run_id': record['run_id']})
                report = self.report(record['run_id'], name)
                self.assertEqual((report['schema'], report['protocol'], report['verdict'], report['equivalence_status'],
                                  report['workflow_status'], report['outcome_category']),
                                 ('benchmark-report/3', 'assistive/1', label, 'graded', 'completed', 'completed'))
                self.assertEqual(report['equivalence'], result['equivalence'])
                self.assertEqual(report['equivalence'], record['equivalence'])
                self.assertEqual(report['equivalence']['diff'], diff)
                self.assertEqual(report['progress']['protocol'], 'assistive/1')
                self.assertEqual(report['case_pin']['case_id'], 'tq9-v2')
                self.assertEqual(report['snapshot_sha256'], result['snapshot_sha256'])
                for key in GATED:
                    self.assertNotIn(key, report)
                self.assertEqual(list(report['stages']), list(STAGES))
                self.assertFalse([stage for stage, row in report['stages'].items() if 'evaluator_status' in row])
                saved = self.root / 'reports' / name / 'report.json'
                self.assertEqual(json.loads(saved.read_text()), report)
                text = saved.with_suffix('.md').read_text()
                self.assertIn('Equivalence (evaluator): **' + label + '**', text)
                self.assertIn('Independent verdict: **' + label + '**', text)
                self.assertNotIn('| Evaluator |', text)
                for path in (saved, saved.with_suffix('.md')):
                    self.assertNotIn(str(EXPECTED), path.read_text())
                    self.assertNotIn(str(self.root), path.read_text())

    def test_a_run_awaiting_its_grade_or_stopped_before_it_reports_unscored(self):
        # Catches a pending, unscored or stopped assistive run reported as graded or as a failed driver.
        run, result = self.pending()
        report = self.report(run['run_id'], 'pending')
        self.assertEqual((report['schema'], report['verdict'], report['equivalence'], report['equivalence_status']),
                         ('benchmark-report/3', 'unscored', None, 'pending'))
        posted = self.controller.call('record_evaluation', {**run, 'token': TOKEN, 'equivalence': {
            **evaluation(self.emitted(result)), 'status': 'unscored', 'equivalent': None, 'checks': [], 'passed': 0, 'total': 0,
            'diff': [{'id': 'evaluation', 'reason': 'evaluator host fault: RuntimeError'}], 'evidence_sha256': None}})
        self.assertTrue(posted['ok'])
        report = self.report(run['run_id'], 'unscored')
        self.assertEqual((report['verdict'], report['equivalence'], report['equivalence_status']), ('unscored', None, 'unscored'))
        trial, record = self.trial('stopped', runtime=self.stopping_probe())
        self.assertEqual(record['status'], 'run-stopped')
        report = self.report(record['run_id'], 'stopped')
        self.assertEqual((report['schema'], report['verdict'], report['equivalence'], report['equivalence_status'],
                          report['workflow_status'], report['outcome_category']),
                         ('benchmark-report/3', 'unscored', None, None, 'blocked', 'operator'))


class ScoreTests(AssistiveFixture):
    def test_offline_score_regrades_the_sealed_final_of_the_accepted_driver(self):
        # Catches a score that trusts the report's verdict, needs the configurator or a process, or regrades another
        # package than the one the run accepted.
        from generative_driver.benchmark import main
        for name, outcome, label, code in (('good', PASS, 'equivalent', 0), ('wrong', FAIL, 'not equivalent', 1)):
            with self.subTest(driver=name):
                trial, record = self.trial(name, outcome)
                report = self.report(record['run_id'], name)
                with patch('generative_driver.client.call', side_effect=AssertionError('service invoked')), \
                        patch('subprocess.Popen', side_effect=AssertionError('process invoked')):
                    scored = self.score(report, trial / 'run-evidence.enc')
                self.assertEqual(scored, report)
                self.assertEqual((scored['verdict'], scored['equivalence']['equivalent']), (label, outcome is PASS))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = main(['score', str(self.root / 'reports' / name / 'report.json'), '--evidence',
                                      str(trial / 'run-evidence.enc'), '--password-file', str(self.password)])
                self.assertEqual((exit_code, json.loads(output.getvalue())['verdict']), (code, label))
                self.assertNotIn(str(EXPECTED), output.getvalue())

    def test_a_claimed_verdict_is_replaced_by_the_regrade_of_the_sealed_records(self):
        # Catches a regrade that keeps the verdict the report or the configurator claims instead of grading the sealed
        # final records again.
        trial, record = self.trial('wrong', FAIL)
        report = self.report(record['run_id'], 'wrong')
        claimed = copy.deepcopy(report)
        claimed.update(verdict='equivalent', reason='Final verdict: equivalent')
        claimed['equivalence'].update(equivalent=True, diff=[], passed=2,
                                      checks=[{**c, 'passed': True} for c in report['equivalence']['checks']])
        scored = self.score(claimed, trial / 'run-evidence.enc')
        self.assertEqual((scored['verdict'], scored['equivalence']), ('not equivalent', report['equivalence']))
        # The reason says what the regrade found, never what was claimed.
        self.assertEqual(scored['reason'], 'Final verdict: not equivalent: value mismatch')
        trial, record = self.trial('good', PASS)
        report = self.report(record['run_id'], 'good')
        claimed = copy.deepcopy(report)
        claimed['equivalence'].update(equivalent=False, diff=[{'id': 'final/effect-1/duty', 'reason': 'value mismatch'}])
        self.assertEqual(self.score(claimed, trial / 'run-evidence.enc')['equivalence'], report['equivalence'])

    def test_a_regrade_refuses_a_report_whose_identity_is_not_the_sealed_one(self):
        # Catches a regrade of evidence sealed for another run, snapshot, case, package, mapping or check set, evidence
        # the report does not name, another password, and an evaluator other than the installed one.
        trial, record = self.trial('good', PASS)
        other, _ = self.trial('other', PASS)
        report = self.report(record['run_id'], 'good')
        evidence = trial / 'run-evidence.enc'
        def changed(change):
            value = copy.deepcopy(report); change(value); return value
        for label, value in (
                ('run', changed(lambda r: r.update(run_id='run-another'))),
                ('snapshot', changed(lambda r: r.update(snapshot_sha256='f' * 64))),
                ('case', changed(lambda r: r['case_pin'].update(truth_sha256='f' * 64))),
                ('execution', changed(lambda r: r.update(execution='actual-agent-physical'))),
                ('toolchain', changed(lambda r: r['executed'].update(toolchain_revision='f' * 64))),
                ('package', changed(lambda r: r['equivalence'].update(frozen_artifact_sha256='f' * 64))),
                ('mapping', changed(lambda r: r['equivalence'].update(capabilities_sha256='f' * 64))),
                ('checks', changed(lambda r: r['equivalence']['checks'][0].update(id='final/other'))),
                ('basis', changed(lambda r: r['equivalence'].update(basis='self-evidence'))),
                ('evidence', changed(lambda r: r['equivalence'].update(evidence_sha256='f' * 64)))):
            with self.subTest(changed=label), self.assertRaises(ValueError):
                self.score(value, evidence)
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.score(report, other / 'run-evidence.enc')
        wrong = self.root / 'other-password'; wrong.write_text('another credential')
        with self.assertRaisesRegex(ValueError, 'password'):
            self.score(report, evidence, wrong)
        for missing in ((None, self.password), (evidence, None)):
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                from generative_driver.benchmark import score
                score(copy.deepcopy(report), missing[1], evidence_path=missing[0])
        with patch('generative_driver.reporting._tree_hash', return_value='0' * 64), \
                self.assertRaisesRegex(ValueError, 'evaluator'):
            self.score(report, evidence)

    def test_a_regrade_trusts_no_sealed_verdict_its_final_does_not_bear_out(self):
        # Catches a regrade that keeps the sealed verdict over the sealed final's records, credits an equivalent verdict
        # sealed without a final, or pairs a final with another mapping than the verdict names.
        from generative_driver.benchmark_support import evaluator
        from generative_driver.benchmark_support.truth import unlock
        trial, record = self.trial('good', PASS)
        result = self.controller.call('result', {'run_id': record['run_id']})
        report = self.report(record['run_id'], 'good')
        run_dir = self.home / 'runs' / record['run_id']
        sealed = unlock(trial / 'run-evidence.enc', self.password.read_text(), report['equivalence']['evidence_sha256'])
        def resealed(name, **content):
            folder = self.root / 'resealed' / name
            digest = evaluator.seal(folder, run_dir, result, password_file=self.password,
                                    **{'final': sealed['final'], 'equivalence': sealed['equivalence'], **content})['sha256']
            return {**report, 'equivalence': {**report['equivalence'], 'evidence_sha256': digest}}, folder / 'run-evidence.enc'
        failing = copy.deepcopy(sealed['final'])
        failing['records'][1]['value'] = 0
        scored = self.score(*resealed('failing', final=failing))
        self.assertEqual((scored['verdict'], scored['equivalence']['diff']),
                         ('not equivalent', [{'id': 'final/effect-1/duty', 'reason': 'value mismatch'}]))
        for name, content in (('no final', {'final': None}),
                              ('other mapping', {'final': {**sealed['final'], 'capabilities_sha256': '0' * 64}})):
            with self.subTest(sealed=name), self.assertRaisesRegex(ValueError, 'final'):
                self.score(*resealed(name.replace(' ', '-'), **content))

    def test_no_driver_regrades_not_equivalent_and_a_run_without_a_verdict_stays_unscored(self):
        # Catches a run that emitted no driver scored as anything but not equivalent, and a run awaiting its grade
        # scored from evidence that names no verdict.
        trial, record = self.trial('none', PASS, emit=False)
        report = self.report(record['run_id'], 'none')
        self.assertEqual((report['verdict'], report['equivalence_status']), ('not equivalent', 'no-driver'))
        self.assertEqual(self.score(report, trial / 'run-evidence.enc'), report)
        run, _ = self.pending()
        report = self.report(run['run_id'], 'pending')
        with patch('generative_driver.benchmark_support.truth.unlock', side_effect=AssertionError('evidence unlocked')):
            scored = self.score(report, None, None)
        self.assertEqual((scored['verdict'], scored['equivalence'], scored['equivalence_status']), ('unscored', None, 'pending'))


class ProtocolSeparationTests(unittest.TestCase):
    """Reports and suite reports of different protocols are separate series: never scored as one another, never
    aggregated together and never compared."""

    def assistive(self, verdict='equivalent'):
        frozen = 'a' * 64
        return {'schema': 'benchmark-report/3', 'protocol': 'assistive/1', 'run_id': 'r', 'case': 'tq9-v2',
                'case_version': '5', 'evaluator_version': '5', 'scenario_id': 'original', 'case_seed': 0,
                'execution': 'actual-agent-emulation', 'workflow_status': 'completed', 'verdict': verdict,
                'equivalence': evaluation(frozen, verdict == 'equivalent'), 'equivalence_status': 'graded',
                'elapsed_seconds': 5, 'stages': {}, 'usage': {'total_tokens': None}}

    def test_a_run_comparison_across_protocols_is_refused(self):
        # Catches a comparison that sets an assistive result beside a gated or earlier-protocol one.
        from generative_driver.benchmark import compare
        gated = {'schema': 'benchmark-report/2', 'case': 'tq9-v2', 'case_version': '5', 'evaluator_version': '5',
                 'scenario_id': 'original', 'case_seed': 0, 'execution': 'actual-agent-emulation', 'verdict': 'passed',
                 'progress': {'revision': 0}, 'elapsed_seconds': 4}
        for before, after in ((gated, self.assistive()), (self.assistive(), gated),
                              ({'schema': 'benchmark-report/1', 'case': None, 'protocol': None, 'verdict': 'unscored'},
                               {'schema': 'benchmark-report/1', 'case': None, 'protocol': 'assistive/1', 'verdict': 'unscored'})):
            with self.subTest(before=before['schema'], after=after['schema']):
                result = compare(before, after)
                self.assertFalse(result['compatible'])
                self.assertEqual(result['incompatible_fields'], ['protocol'])
                self.assertIn('protocol', result['reason'])
                for key in ('before_verdict', 'after_verdict', 'elapsed_seconds_delta', 'stages'):
                    self.assertNotIn(key, result)
        result = compare(self.assistive(), {**self.assistive('not equivalent'), 'elapsed_seconds': 8})
        self.assertTrue(result['compatible'], result)
        self.assertEqual((result['before_verdict'], result['after_verdict'], result['elapsed_seconds_delta']),
                         ('equivalent', 'not equivalent', 3))
        self.assertFalse(compare(self.assistive(), {**self.assistive(), 'case_seed': 1})['compatible'])

    def test_an_earlier_protocol_report_keeps_its_gate_logic_and_an_assistive_one_is_never_scored_by_it(self):
        # Catches the gated regrade changed for earlier reports, or an assistive run scored as a gated one.
        from generative_driver.benchmark import score
        from generative_driver.benchmark_support import evidence
        from test_benchmark_v2_evidence import SealedEvidenceTests
        fixture = SealedEvidenceTests(); fixture.setUp(); self.addCleanup(fixture.temp.cleanup)
        report, path = fixture.sealed(fixture.payload())
        graded = score(report, fixture.password, evidence_path=path)
        self.assertEqual((graded['schema'], graded['verdict'], graded['final_evaluation']['verdict']),
                         ('benchmark-report/2', 'passed', 'passed'))
        self.assertNotIn('equivalence', graded)
        from generative_driver.reporting import markdown, summarize
        earlier = summarize({'run_id': 'earlier', 'status': 'completed', 'created': 0, 'updated': 1, 'worker_reports': [],
                             'accepted_handoffs': []}, [])
        self.assertIn('| Stage | Workflow | Evaluator |', markdown(earlier))
        with self.assertRaisesRegex(ValueError, 'protocol'):
            evidence.regrade_v2({**report, 'progress': {'protocol': 'assistive/1'}}, path, fixture.password)


def assistive_fixture():
    """Nine assistive trials of three cases: equivalent, not equivalent, no driver, unscored, pending, stopped for the
    operator and the host, cancelled and not run."""
    from test_suite_reporting import fixture
    from generative_driver.benchmark_support.suites import expand_trials
    suite, _, _, experiment = fixture()
    suite['repetitions'] = 3
    identity = experiment['comparison_identity']
    slots = expand_trials(suite)
    identity.update(protocol='assistive/1', manifest=suite, repetitions=3, trials=slots,
                    manifest_sha256=hashlib.sha256(json.dumps(suite, sort_keys=True, separators=(',', ':')).encode()).hexdigest())
    plan = [('finished', 'completed', 'completed', evaluation('e' * 64, True), 'graded', True),
            ('finished', 'completed', 'completed', evaluation('e' * 64, False), 'graded', False),
            ('finished', 'completed', 'completed', {**evaluation(None, False), 'status': 'no-driver', 'checks': [],
             'passed': 0, 'total': 0, 'capabilities_sha256': None,
             'diff': [{'id': 'frozen_package', 'reason': 'no driver emitted'}]}, 'no-driver', False),
            ('completed', 'completed', 'completed', None, 'unscored', None),
            ('completed', 'completed', 'completed', None, 'pending', None),
            ('blocked', 'operator', 'blocked', None, None, None),
            ('blocked', 'host', 'blocked', None, None, None),
            ('cancelled', 'cancelled', 'cancelled', None, None, None),
            ('pending', 'unknown', None, None, None, None)]
    trials, reports, snapshots = [], {}, []
    for slot, (status, category, workflow, equivalence, equivalence_status, equivalent) in zip(slots, plan):
        pin = identity['entry_pins'][slot['entry_index']]
        run_id = None if workflow is None else 'r' + str(slot['ordinal'])
        trials.append({**slot, 'family': pin['family'], 'run_id': run_id, 'status': status, 'outcome_category': category,
                       'interventions': []})
        snapshots.append({'trial_key': slot['trial_key'], 'snapshot_sha256': 'd' * 64 if run_id else None})
        if run_id is None:
            continue
        if equivalence:
            equivalence['mission'] = {'name': 'fresh_worker_mission', 'passed': equivalent is True}
        verdict = {True: 'equivalent', False: 'not equivalent'}.get(equivalent, 'unscored')
        reports[run_id] = {'schema': 'benchmark-report/3', 'protocol': 'assistive/1', 'run_id': run_id, 'case': slot['case'],
            'scenario_id': slot['scenario'], 'case_seed': slot['case_seed'], 'case_pin': pin, 'snapshot_sha256': 'd' * 64,
            'workflow_status': workflow, 'outcome_category': category, 'verdict': verdict, 'equivalence': equivalence,
            'equivalence_status': equivalence_status, 'worker_seconds': 4, 'elapsed_seconds': 5, 'tool_seconds': 1,
            'usage': {'total_tokens': 10, 'observed_total_tokens': 10}, 'progress': {'protocol': 'assistive/1'},
            'stages': {stage: {'workflow_status': 'accepted', 'attempt_count': 1,
                               'attempts': [{'assignment_id': stage, 'accepted': True, 'status': 'completed'}]} for stage in STAGES},
            'unknown': 'PRIVATE'}
    # Fields the public equivalence/1 record does not have never leave a report.
    reports['r1']['equivalence']['expected'] = 'PRIVATE'
    reports['r1']['equivalence']['checks'][1]['expected'] = 'PRIVATE'
    experiment['execution_snapshots'] = snapshots
    return suite, trials, reports, experiment


def assistive_owner(report):
    """The configured owner's records of a graded assistive run with that report."""
    frozen = report['equivalence']['frozen_artifact_sha256']
    handoffs = [{'stage': stage, 'revision': 0, 'assignment_id': stage, 'route': None,
                 'artifacts': [{'path': '/private/' + stage, 'sha256': frozen if stage == 'emit' else '1' * 64}],
                 'checks': [{'name': 'not_run', 'passed': None}]} for stage in STAGES]
    owner = {'ok': True, 'run_id': report['run_id'], 'status': 'completed', 'outcome_category': 'completed', 'stopping': False,
             'uncertain_effect': False, 'progress': {'revision': 0, 'next_stage': None, 'protocol': 'assistive/1',
                                                     'equivalence': 'graded'},
             'accepted_handoffs': handoffs, 'equivalence': copy.deepcopy(report['equivalence']), 'evaluator_verdicts': []}
    events = [{'id': 1, 'kind': 'run.finalized', 'time': 1,
               'data': {key: report['equivalence'][key] for key in ('equivalent', 'basis', 'diff')}}]
    return owner, events


class SuiteAggregateTests(unittest.TestCase):
    def test_end_to_end_success_requires_equivalence_and_successful_fresh_reuse(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        for mission in (True, False, None):
            with self.subTest(mission=mission):
                suite, trials, reports, experiment = assistive_fixture()
                reports['r0']['equivalence']['mission']['passed'] = mission
                # Successful use of a defective driver cannot satisfy the joint criterion either.
                reports['r1']['equivalence']['mission']['passed'] = True
                result = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
                expected = int(mission is True)
                self.assertEqual(result['equivalence']['equivalent'], 1)
                self.assertEqual(result['equivalent_and_mission'], {'passed': expected, 'denominator': 9, 'rate': expected / 9})
                self.assertEqual([row['equivalent_and_mission'] for row in result['trials']], [mission is True] + [False] * 8)

    def test_comparison_recounts_joint_success_from_fresh_mission_evidence(self):
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite, compare_suites
        suite, trials, reports, experiment = assistive_fixture()
        before = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        reports['r0']['equivalence']['mission']['passed'] = False
        after = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        result = compare_suites(before, after)
        self.assertTrue(result['compatible'], result)
        self.assertEqual(result['equivalence_delta'], 0)
        self.assertEqual(result['equivalent_and_mission_delta'], -1 / 9)
        self.assertEqual(result['before_equivalent_and_mission']['passed'], 1)
        self.assertEqual(result['after_equivalent_and_mission']['passed'], 0)
        # Older exports are recounted, never treated as successful merely for being equivalent.
        older = copy.deepcopy(after)
        older.pop('equivalent_and_mission')
        for row in older['trials']:
            row.pop('equivalent_and_mission')
        self.assertEqual(compare_suites(before, older)['equivalent_and_mission_delta'], -1 / 9)
        forged = copy.deepcopy(after)
        forged['equivalent_and_mission'] = before['equivalent_and_mission']
        self.assertFalse(compare_suites(before, forged)['compatible'])

    def test_an_assistive_suite_counts_equivalent_drivers_out_of_planned_and_lists_the_unscored(self):
        # Catches an assistive suite counted by the gated success rule, unscored or stopped trials hidden or counted as
        # failed drivers, a denominator that drops a planned slot, and unknown report fields in the export.
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        suite, trials, reports, experiment = assistive_fixture()
        result = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        self.assertEqual((result['schema'], result['protocol']), ('benchmark-suite-report/2', 'assistive/1'))
        self.assertNotIn('success', result)
        self.assertEqual(result['equivalence'], {'equivalent': 1, 'denominator': 9, 'rate': 1 / 9})
        counts = result['counts']
        self.assertEqual(counts, {'planned': 9, 'started': 8, 'finished': 3, 'not_run': 1, 'equivalent': 1,
                                  'not_equivalent': 2, 'no_driver': 1, 'unqualified': 0, 'unscored': 2, 'running': 0,
                                  'stopped': {'operator': 1, 'host': 1, 'cancelled': 1}})
        self.assertEqual(counts['planned'], sum(counts[key] for key in ('not_run', 'equivalent', 'not_equivalent',
                         'unqualified', 'unscored', 'running')) + sum(counts['stopped'].values()))
        self.assertEqual(result['unscored'], [{'trial_key': 'entry-000-repeat-001', 'run_id': 'r3', 'equivalence_status': 'unscored'},
                                              {'trial_key': 'entry-001-repeat-001', 'run_id': 'r4', 'equivalence_status': 'pending'}])
        self.assertTrue(result['provisional'])
        self.assertEqual(result['case_equivalence']['tq9-v2'], {'equivalent': 1, 'denominator': 3, 'rate': 1 / 3})
        self.assertEqual(result['case_equivalence']['parameter-store-v1']['equivalent'], 0)
        self.assertEqual(result['scenario_equivalence']['original'], {'equivalent': 1, 'denominator': 9, 'rate': 1 / 9})
        self.assertEqual(result['family_equivalence'], {'one': 1 / 6, 'two': 0.0})
        self.assertEqual(result['fresh_mission'], {'passed': 1, 'denominator': 9, 'rate': 1 / 9})
        self.assertEqual((result['first_attempt_equivalent'], result['repaired_equivalent']), (1, 0))
        rows = result['trials']
        self.assertEqual([row.get('verdict') for row in rows], ['equivalent', 'not equivalent', 'not equivalent', 'unscored',
                                                               'unscored', 'unscored', 'unscored', 'unscored', None])
        self.assertEqual(rows[0]['equivalence'], reports['r0']['equivalence'])
        self.assertEqual([row.get('equivalence_status') for row in rows][:5], ['graded', 'graded', 'no-driver', 'unscored', 'pending'])
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertNotIn('final_evaluation', json.dumps(rows))

    def test_each_trial_row_has_the_outcome_of_the_count_it_is_in(self):
        # Catches a trial row whose outcome disagrees with the count that holds it, and a claimed equivalent the suite
        # does not count that does not say why.
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        suite, trials, reports, experiment = assistive_fixture()
        trials[0]['status'] = 'completed'  # An equivalent verdict the configurator did not settle.
        result = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        rows, counts = result['trials'], result['counts']
        self.assertEqual([row['outcome'] for row in rows], ['unqualified', 'not_equivalent', 'not_equivalent', 'unscored',
                                                           'unscored', 'stopped', 'stopped', 'stopped', 'not_run'])
        for outcome in ('not_run', 'equivalent', 'not_equivalent', 'unqualified', 'unscored', 'running'):
            self.assertEqual(sum(row['outcome'] == outcome for row in rows), counts[outcome], outcome)
        self.assertEqual(sum(row['outcome'] == 'stopped' for row in rows), sum(counts['stopped'].values()))
        self.assertEqual(rows[0]['reason'], 'Not counted: the equivalent verdict is not a settled, qualified record')
        self.assertEqual((rows[8]['reason'], rows[8]['stopped_stage'], rows[8]['repairs']), (None, None, None))

    def test_a_verdict_is_counted_only_from_a_settled_complete_record(self):
        # Catches an equivalent driver counted from a trial the configurator did not settle, or from a record that is
        # not a complete evaluator grade.
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite, _report_equivalent
        mutations = [lambda t, r: t[0].update(status='completed'),
                     lambda t, r: t[0].update(outcome_category='model'),
                     lambda t, r: r['r0']['equivalence'].update(equivalent=False),
                     lambda t, r: r['r0']['equivalence']['checks'][1].update(passed=False),
                     lambda t, r: r['r0']['equivalence'].update(diff=[{'id': 'x', 'reason': 'y'}]),
                     lambda t, r: r['r0']['equivalence'].update(basis='self-evidence'),
                     lambda t, r: r['r0']['equivalence'].update(status='no-driver'),
                     lambda t, r: r['r0']['equivalence'].update(evidence_sha256=None),
                     lambda t, r: r['r0']['equivalence'].update(passed=1),
                     lambda t, r: r['r0'].update(workflow_status='blocked'),
                     lambda t, r: r['r0'].update(verdict='not equivalent')]
        for index, mutate in enumerate(mutations):
            suite, trials, reports, experiment = assistive_fixture()
            mutate(trials, reports)
            with self.subTest(index=index):
                result = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
                self.assertEqual(result['equivalence']['equivalent'], 0)
                self.assertNotEqual(result['trials'][0]['verdict'], 'equivalent')
        suite, trials, reports, experiment = assistive_fixture()
        self.assertTrue(_report_equivalent(reports['r0']))
        self.assertFalse(_report_equivalent({**reports['r0'], 'schema': 'benchmark-report/2'}))

    def test_reports_of_another_protocol_are_never_aggregated_with_a_suites_own(self):
        # Catches a gated report counted in an assistive suite, and an assistive report counted in a gated one.
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite
        from test_suite_reporting import fixture
        suite, trials, reports, experiment = assistive_fixture()
        reports['r1'] = {**reports['r1'], 'schema': 'benchmark-report/2'}
        with self.assertRaisesRegex(ValueError, 'protocol'):
            aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        suite, trials, reports, experiment = fixture()
        reports['r2'] = {**reports['r2'], 'schema': 'benchmark-report/3', 'protocol': 'assistive/1'}
        with self.assertRaisesRegex(ValueError, 'protocol'):
            aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        suite, trials, reports, experiment = fixture()
        result = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        self.assertEqual((result['schema'], result['success']['passed']), ('benchmark-suite-report/1', 1))
        self.assertNotIn('equivalence', result)

    def test_a_recorded_verdict_qualifies_only_from_the_owners_own_record_of_the_accepted_driver(self):
        # Catches qualification from the report's claim instead of the configurator's record of the run's accepted
        # emitted driver, and from a run that is not settled.
        from generative_driver.benchmark_support.suite_reporting import _recorded_equivalence
        _, _, reports, _ = assistive_fixture()
        report = reports['r0']
        owner, events = assistive_owner(report)
        self.assertTrue(_recorded_equivalence(owner, events, report))
        mutations = [lambda o, e, r: o.update(stopping=True), lambda o, e, r: o.update(uncertain_effect=True),
                     lambda o, e, r: o.update(status='blocked'), lambda o, e, r: o.update(outcome_category='safety'),
                     lambda o, e, r: o['progress'].update(protocol=None),
                     lambda o, e, r: o['progress'].update(next_stage='reuse'),
                     lambda o, e, r: o['progress'].update(equivalence='pending'),
                     lambda o, e, r: o.update(equivalence=None),
                     lambda o, e, r: o['equivalence'].update(evidence_sha256='0' * 64),
                     lambda o, e, r: o['accepted_handoffs'][4]['artifacts'][0].update(sha256='0' * 64),
                     lambda o, e, r: o['accepted_handoffs'].append({**o['accepted_handoffs'][4], 'assignment_id': 'later',
                                                                   'artifacts': [{'path': '/p', 'sha256': '2' * 64}]}),
                     lambda o, e, r: e.clear(),
                     lambda o, e, r: e.append(copy.deepcopy(e[0])),
                     lambda o, e, r: r.update(verdict='unqualified')]
        for index, mutate in enumerate(mutations):
            report = copy.deepcopy(reports['r0']); owner, events = assistive_owner(report)
            mutate(owner, events, report)
            with self.subTest(index=index):
                self.assertFalse(_recorded_equivalence(owner, events, report))

    def test_saved_suite_reports_compare_only_within_their_protocol(self):
        # Catches an assistive suite report compared with a gated one, and a saved assistive report whose count is
        # not its trials' verdicts.
        from generative_driver.benchmark_support.suite_reporting import aggregate_suite, compare_suites
        from test_suite_reporting import comparison_fixture
        suite, trials, reports, experiment = assistive_fixture()
        before = aggregate_suite(suite, trials, reports, 'recorded-controller-verdicts', experiment=experiment)
        after = copy.deepcopy(before)
        after['experiment']['dimensions']['model'] = 'other'
        result = compare_suites(before, after)
        self.assertTrue(result['compatible'], result)
        self.assertEqual((result['before_equivalence'], result['equivalence_delta'], result['changed_dimensions']),
                         (before['equivalence'], 0, ['model']))
        self.assertNotIn('success_delta', result)
        for gated in ((comparison_fixture(), before), (before, comparison_fixture())):
            refused = compare_suites(*gated)
            self.assertFalse(refused['compatible'])
            self.assertIn('Protocols differ', refused['reason'])
            for key in ('before_success', 'after_success', 'before_equivalence', 'after_equivalence'):
                self.assertNotIn(key, refused)
        for change in (lambda r: r['equivalence'].update(equivalent=2, rate=2 / 9),
                       lambda r: r['trials'][1].update(verdict='equivalent'),
                       lambda r: r.update(schema='benchmark-suite-report/1'),
                       lambda r: r.pop('protocol')):
            forged = copy.deepcopy(before); change(forged)
            refused = compare_suites(forged, before)
            self.assertFalse(refused['compatible']); self.assertEqual(refused['reason'], 'Invalid or incomplete saved suite report')


class SuiteExportTests(AssistiveFixture):
    def test_an_exported_suite_says_why_each_trial_stands_as_it_does(self):
        # Catches an export that drops why a trial stopped or is unscored: each trial row, and the report it names,
        # carries the trial's outcome, a reason with no path or expected value, the stage where it stopped and the
        # repairs it used.
        from generative_driver.benchmark_support.suite_reporting import report_suite
        with self.harness(PASS, FAIL, RuntimeError('evaluator host stopped')):
            graded = self.drive(manifest=manifest(), executor='codex', options={'scoped_tool_approval': 'emulator'},
                                request_id='three-children')
        def stopping(request):
            # The child stops at probe for a runtime its reason names by a path in the configurator's home.
            if request.stage == 'probe':
                return {'runtime': 'codex', 'elapsed_seconds': 0, 'usage': None, 'report': None, 'status': 'blocked',
                        'cause': 'runtime_unavailable', 'reason': 'Cannot start configured runtime: ' + str(self.home / 'codex')}
        with self.harness(runtime=stopping):
            stopped = self.drive(manifest=manifest(), executor='codex', options={}, request_id='stopped-child')
        expected = {graded['suite_id']: [('equivalent', 'Final verdict: equivalent', None, 0),
                                         ('not_equivalent', 'Final verdict: not equivalent: value mismatch', None, 0),
                                         ('unscored', 'Unscored: evaluator host fault: RuntimeError', None, 0)],
                    stopped['suite_id']: [('stopped', 'Cannot start configured runtime: ' + os.path.join('RUN_HOME', 'codex'),
                                           'probe', 0), ('not_run', None, None, None), ('not_run', None, None, None)]}
        for sid, rows in expected.items():
            with self.subTest(suite=sid), self.owner():
                result = report_suite(sid, home=self.home, output=self.root / sid)
                self.assertEqual([(row['outcome'], row['reason'], row['stopped_stage'], row['repairs'])
                                  for row in result['trials']], rows)
                for row in result['trials']:
                    if row['run_id']:
                        saved = json.loads((self.root / sid / row['report_path']).read_text())
                        self.assertEqual((saved['reason'], saved['stopped_stage']), (row['reason'], row['stopped_stage']))
                for path in files(self.root / sid):
                    self.assertNotIn(str(EXPECTED), path.read_text()); self.assertNotIn(str(self.root), path.read_text())

    def test_the_suite_markdown_says_why_from_each_trials_outcome(self):
        # Catches a report.md that is one count and the keys of the unscored trials: it names the experiment's
        # dimensions and why the suite stopped, gives each trial's outcome with its reason, stop stage, repairs, time
        # and tokens, and lists the most frequent failing check ids, with no path or expected value.
        from generative_driver.benchmark_support.suite_reporting import report_suite
        with self.harness(PASS, FAIL, RuntimeError('evaluator host stopped')):
            record = self.drive(manifest=manifest(), executor='codex', options={'scoped_tool_approval': 'emulator'},
                                request_id='three-children')
        with self.owner():
            result = report_suite(record['suite_id'], home=self.home, output=self.root / 'export')
        text = (self.root / 'export/report.md').read_text()
        joint = result['equivalent_and_mission']
        self.assertIn(f"Equivalent drivers with successful fresh reuse: {joint['passed']}/{joint['denominator']}", text)
        self.assertEqual(result['reason'], 'Trial entry-000-repeat-002 is unscored: evaluator host fault: RuntimeError')
        self.assertIn('Suite status: blocked: ' + result['reason'], text)
        self.assertIn('runtime `codex`', text)
        for row in result['trials']:
            line = next(line for line in text.splitlines() if line.startswith('| ' + row['trial_key'] + ' |'))
            cells = [cell.strip() for cell in line.strip().strip('|').split('|')]
            self.assertEqual(cells[:6], [row['trial_key'], row['case'], row['outcome'], row['reason'], '', str(row['repairs'])])
        self.assertIn('Most frequent failing checks: `final/effect-1/duty` (1)', text)
        self.assertNotIn(str(EXPECTED), text); self.assertNotIn(str(self.root), text)

    def test_an_assistive_suite_exports_its_equivalent_drivers_and_regrades_their_sealed_evidence(self):
        # Catches an exported assistive suite counted by gates, an unscored trial hidden or regraded, evidence that does
        # not cover exactly the graded trials, and an export that carries an expected value.
        from generative_driver.benchmark_support.suite_reporting import compare_suites, report_suite
        from test_suite_reporting import comparison_fixture
        with self.harness(PASS, FAIL, RuntimeError('evaluator host stopped')):
            record = self.drive(manifest=manifest(), executor='codex', options={'scoped_tool_approval': 'emulator'},
                                request_id='three-children')
        sid = record['suite_id']
        rows = self.controller.call('suite_result', {'suite_id': sid})['trials']
        with self.owner():
            result = report_suite(sid, home=self.home, output=self.root / 'export')
        self.assertEqual((result['schema'], result['protocol'], result['qualification'], result['status']),
                         ('benchmark-suite-report/2', 'assistive/1', 'recorded-controller-verdicts', 'blocked'))
        self.assertEqual(result['equivalence'], {'equivalent': 1, 'denominator': 3, 'rate': 1 / 3})
        self.assertEqual({key: result['counts'][key] for key in ('equivalent', 'not_equivalent', 'unscored', 'not_run')},
                         {'equivalent': 1, 'not_equivalent': 1, 'unscored': 1, 'not_run': 0})
        self.assertEqual(result['unscored'], [{'trial_key': rows[2]['trial_key'], 'run_id': rows[2]['run_id'],
                                               'equivalence_status': 'unscored'}])
        self.assertEqual([row['verdict'] for row in result['trials']], ['equivalent', 'not equivalent', 'unscored'])
        text = (self.root / 'export/report.md').read_text()
        self.assertIn('Equivalent drivers: 1/3', text)
        self.assertIn('Unscored: ' + rows[2]['trial_key'], text)
        for row in result['trials']:
            saved = json.loads((self.root / 'export' / row['report_path']).read_text())
            self.assertEqual((saved['schema'], saved['run_id']), ('benchmark-report/3', row['run_id']))
        for path in files(self.root / 'export'):
            self.assertNotIn(str(EXPECTED), path.read_text()); self.assertNotIn(str(self.root), path.read_text())
        saved = json.loads((self.root / 'export/suite.json').read_text())
        self.assertTrue(compare_suites(saved, saved)['compatible'])
        self.assertIn('Protocols differ', compare_suites(saved, comparison_fixture())['reason'])
        # Regrading authenticates each recorded verdict from its trial's sealed evidence: exactly the graded trials.
        trials = {row['trial_key']: next(a['trial'] for a in self.admitted if a['trial'].name.startswith(row['trial_key']))
                  for row in rows}
        graded = {key: trial / 'run-evidence.enc' for key, trial in list(trials.items())[:2]}
        passwords = {'tq9-v2': self.password}
        with self.owner(), patch('subprocess.Popen', side_effect=AssertionError('process invoked')):
            regraded = report_suite(sid, home=self.home, output=self.root / 'regraded', evidence_files=graded,
                                    password_files=passwords)
            self.assertEqual((regraded['qualification'], regraded['equivalence']['equivalent']),
                             ('regraded-encrypted-evidence', 1))
            self.assertEqual([row['verdict'] for row in regraded['trials']], ['equivalent', 'not equivalent', 'unscored'])
            keys = list(trials)
            for label, evidence in (('missing', {keys[0]: graded[keys[0]]}),
                                    ('swapped', {keys[0]: graded[keys[1]], keys[1]: graded[keys[0]]}),
                                    ('unscored', {**graded, keys[2]: trials[keys[2]] / 'run-evidence.enc'}),
                                    ('unknown', {**graded, 'entry-009-repeat-000': graded[keys[0]]})):
                with self.subTest(evidence=label), self.assertRaises(ValueError):
                    report_suite(sid, home=self.home, output=self.root / label, evidence_files=evidence,
                                 password_files=passwords)


if __name__ == '__main__':
    unittest.main()
