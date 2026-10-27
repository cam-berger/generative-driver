"""The broader corpus must expose exclusions instead of inflating device coverage."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


def unavailable_catalog():
    from generative_driver.benchmark_support.corpus import load_catalog
    catalog = load_catalog()
    for row in catalog['lineages']:
        row['case'] = None
    return catalog


def measured_fixture():
    from test_assistive_reports import evaluation
    from generative_driver.benchmark_support.corpus import _plan, _availability, lineage_identity, _digest
    from generative_driver.benchmark_support.suites import expand_trials
    from generative_driver.benchmark_support.suite_reporting import aggregate_suite
    catalog = unavailable_catalog()
    row = catalog['lineages'][-1]
    row.update(case='zephyr-modbus-v1', blockers=[])
    pin = {'case_id': row['case'], 'case_version': '1', 'evaluator_version': '1', 'family': 'zephyr-modbus',
           'scenario_id': 'original', 'case_seed': 0, 'execution': 'actual-agent-emulation',
           'evidence_track': 'firmware', 'scope': 'full-workflow', 'manifest_sha256': 'a'*64,
           'truth_sha256': 'b'*64, 'image_hashes': {'firmware.bin': 'c'*64},
           'manifest': {'corpus_identity': lineage_identity(row)}, 'calibration': {'status': 'passed'}}
    available = [_availability(r) for r in catalog['lineages'][:-1]] + [
        {'lineage': row['id'], 'status': 'ready', 'blockers': [], 'case_pin': pin}]
    plan = _plan(catalog, 1, available)
    manifest = plan['runnable_suite']; slots = expand_trials(manifest)
    public_pin = {k: v for k, v in pin.items() if k not in ('manifest', 'calibration')}
    public_pin['pin_sha256'] = _digest(pin)
    budget = {k: manifest[k] for k in ('child_budget_seconds', 'suite_budget_seconds')}
    experiment = {'schema': 'benchmark-suite-experiment/1', 'comparison_identity': {
        'protocol': 'assistive/1', 'manifest': manifest, 'manifest_sha256': _digest(manifest),
        'entry_pins': [public_pin], 'trials': slots, 'repetitions': 1, 'execution': 'actual-agent-emulation',
        'evidence_track': 'firmware', 'scope': 'full-workflow', 'time_policy': [{}],
        'evaluator_revision': 'd'*64, 'budgets': {'original': budget, 'effective': {**budget, 'child_budget_overrides': []}},
        'intervention_policy': {'scoped_tool_approval': 'emulator', 'max_model_repairs': 2}},
        'dimensions': {'runtime': 'codex', 'model': 'unit-fixture'},
        'execution_snapshots': [{'trial_key': slots[0]['trial_key'], 'snapshot_sha256': 'e'*64}]}
    trial = {**slots[0], 'family': 'zephyr-modbus', 'run_id': 'toy-run', 'status': 'finished',
             'outcome_category': 'completed'}
    grade = evaluation('f'*64, True); grade['mission'] = {'name': 'fresh_worker_mission', 'passed': True}
    report = {'schema': 'benchmark-report/3', 'protocol': 'assistive/1', 'run_id': 'toy-run',
        'case': row['case'], 'scenario_id': 'original', 'case_seed': 0, 'case_pin': public_pin,
        'snapshot_sha256': 'e'*64, 'workflow_status': 'completed', 'verdict': 'equivalent',
        'equivalence': grade, 'equivalence_status': 'graded', 'progress': {'protocol': 'assistive/1'},
        'stages': {s: {'workflow_status': 'accepted', 'attempt_count': 1} for s in plan['slots'][0]['required_stages']}}
    return plan, aggregate_suite(manifest, [trial], {'toy-run': report}, 'recorded-controller-verdicts', experiment=experiment)


class CorpusTests(unittest.TestCase):
    def test_explicit_selection_preserves_the_inventory_and_coverage_denominators(self):
        from generative_driver.benchmark_support.corpus import _digest, plan_corpus, report_corpus
        inventory, suite = measured_fixture()
        names = [row['id'] for row in inventory['catalog']['lineages']]
        with patch('generative_driver.benchmark_support.corpus._availability',
                   side_effect=inventory['availability']):
            plan = plan_corpus(inventory['catalog'], repetitions=1, include=[names[0], names[-1]],
                               exclude=[(names[1], 'Outside this experiment objective.')])
        self.assertEqual(plan['schema'], 'benchmark-corpus-plan/2')
        self.assertEqual(plan['catalog'], inventory['catalog'])
        self.assertEqual(len(plan['selection']), 7)
        self.assertEqual(len(plan['slots']), 7)
        self.assertEqual(sum(row['selected'] for row in plan['slots']), 2)
        self.assertEqual(sum(row['status'] == 'excluded' for row in plan['slots']), 5)
        excluded = next(row for row in plan['slots'] if row['lineage'] == names[1])
        self.assertEqual(excluded['selection_reason'], 'Outside this experiment objective.')
        self.assertEqual(excluded['availability_status'], 'blocked')
        self.assertTrue(excluded['blockers'])
        self.assertTrue(all(row['reason'] for row in plan['selection']))
        report = report_corpus(plan)
        self.assertEqual(report['schema'], 'benchmark-corpus-report/2')
        self.assertEqual(report['counts']['planned'], 7)
        self.assertEqual(report['counts']['selected'], 2)
        self.assertEqual(report['counts']['excluded'], 5)
        self.assertEqual(report['counts']['blocked'], 1)
        self.assertEqual(report['measured_success']['denominator'], 0)
        self.assertEqual(report['demonstrated_coverage']['denominator'], 7)
        self.assertEqual(report['selected_coverage']['denominator'], 2)
        identity = suite['experiment']['comparison_identity']
        identity['manifest'] = plan['runnable_suite']
        identity['manifest_sha256'] = _digest(identity['manifest'])
        measured = report_corpus(plan, suite)
        self.assertEqual(measured['counts']['passed'], 1)
        self.assertEqual(measured['measured_success']['rate'], 1)
        self.assertEqual(measured['demonstrated_coverage']['rate'], 1/7)
        self.assertEqual(measured['selected_coverage']['rate'], 1/2)

    def test_missing_negative_grade_never_becomes_a_measured_model_failure(self):
        from generative_driver.benchmark_support.corpus import report_corpus
        plan, suite = measured_fixture()
        trial = suite['trials'][0]
        trial.update(outcome='not_equivalent', verdict='unknown', equivalence=None)
        suite['equivalence'] = {'equivalent': 0, 'denominator': 1, 'rate': 0}
        suite.pop('equivalent_and_mission')
        report = report_corpus(plan, suite)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertEqual(report['counts']['unscored'], 1)
        self.assertIsNone(report['measured_success']['rate'])

    def test_fresh_mission_failure_requires_explicit_model_fault_attribution(self):
        from generative_driver.benchmark_support.corpus import report_corpus
        plan, original = measured_fixture()
        for fault in ('model', 'host', 'operator', None, 'mixed'):
            with self.subTest(fault=fault):
                suite = copy.deepcopy(original)
                mission = {'name': 'fresh_worker_mission', 'passed': False,
                           'reason': 'Fresh reuse could not complete its behavior check.'}
                if fault is not None:
                    mission['fault'] = fault
                suite['trials'][0]['equivalence']['mission'] = mission
                suite.pop('equivalent_and_mission')
                report = report_corpus(plan, suite)
                row = report['slots'][-1]
                self.assertEqual(report['counts']['failed'], int(fault == 'model'))
                self.assertEqual(report['counts']['unscored'], int(fault != 'model'))
                self.assertEqual(report['measured_success']['denominator'], int(fault == 'model'))
                self.assertEqual(row['fresh_mission_fault'], fault)
                self.assertEqual(row['fresh_mission_reason'], mission['reason'])
                if fault != 'model':
                    self.assertEqual(row['reason'], mission['reason'])

    def test_a_host_stop_before_run_creation_stays_visible_without_a_model_failure(self):
        from generative_driver.benchmark_support.corpus import report_corpus
        plan, suite = measured_fixture()
        suite['trials'][0].update(run_id=None, status='blocked', outcome_category='host',
                                 outcome='not_run', verdict=None, equivalence=None,
                                 reason='Runtime could not start.')
        suite['equivalence'] = {'equivalent': 0, 'denominator': 1, 'rate': 0}
        suite.pop('equivalent_and_mission')
        report = report_corpus(plan, suite)
        self.assertEqual(report['counts']['stopped'], 1)
        self.assertEqual(report['counts']['started'], 0)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertEqual(report['slots'][-1]['reason'], 'Runtime could not start.')
        self.assertFalse(report['model_benchmark'])

    def test_selection_errors_are_rejected_before_public_case_preflight(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        catalog = unavailable_catalog()
        name = catalog['lineages'][0]['id']
        for options in (
            {'include': ['unknown']}, {'include': [name, name]},
            {'exclude': [('unknown', 'Wrong ID.')]},
            {'exclude': [(name, 'First.'), (name, 'Second.')]},
            {'exclude': [(name, ' ')]},
            {'include': [name], 'exclude': [(name, 'Conflicting selection.')]},
        ):
            with self.subTest(options=options), \
                    patch('generative_driver.benchmark_support.corpus._availability') as availability:
                with self.assertRaises(ValueError):
                    plan_corpus(catalog, **options)
                availability.assert_not_called()
        plan = plan_corpus(catalog, repetitions=1, include=[])
        self.assertIsNone(plan['runnable_suite'])
        self.assertTrue(all(row['status'] == 'excluded' for row in plan['slots']))

    def test_selected_manifest_is_bound_to_the_complete_frozen_context(self):
        from generative_driver.benchmark_support.corpus import _plan, report_corpus, validate_plan
        plan, suite = measured_fixture()
        alternatives = []
        catalog = copy.deepcopy(plan['catalog'])
        catalog['lineages'][0]['limitations'].append('Different frozen intake evidence.')
        alternatives.append(_plan(catalog, 1, plan['availability']))
        availability = copy.deepcopy(plan['availability'])
        availability[0]['blockers'][0]['reason'] += ' Revised evidence.'
        alternatives.append(_plan(plan['catalog'], 1, availability))
        selection = copy.deepcopy(plan['selection'])
        selection[0].update(selected=False, reason='Outside this experiment objective.')
        alternatives.append(_plan(plan['catalog'], 1, plan['availability'], selection))
        for changed in alternatives:
            self.assertNotEqual(changed['plan_sha256'], plan['plan_sha256'])
            self.assertEqual(changed['runnable_suite']['entries'], plan['runnable_suite']['entries'])
            self.assertNotEqual(changed['runnable_suite']['id'], plan['runnable_suite']['id'])
            with self.assertRaises(ValueError):
                report_corpus(changed, suite)
        changed = copy.deepcopy(plan)
        changed['selection'][0]['reason'] = 'Changed after freezing.'
        with self.assertRaises(ValueError):
            validate_plan(changed)

    def test_start_dispatches_only_selected_ready_cases_and_retains_the_frozen_selection(self):
        from generative_driver.benchmark_support.corpus import plan_corpus, start_corpus
        inventory, _ = measured_fixture()
        ready = inventory['catalog']['lineages'][-1]['id']
        with patch('generative_driver.benchmark_support.corpus._availability',
                   side_effect=inventory['availability']):
            plan = plan_corpus(inventory['catalog'], repetitions=1, include=[ready])
        with patch('generative_driver.benchmark_support.corpus._availability',
                   side_effect=inventory['availability']), \
                patch('generative_driver.benchmark_support.suites.start_suite',
                      return_value={'ok': True, 'suite_id': 'scripted-owner'}) as start:
            result = start_corpus(plan, executor='codex')
        self.assertEqual(result['corpus_plan_sha256'], plan['plan_sha256'])
        self.assertEqual(start.call_args.args[0], plan['runnable_suite'])
        self.assertEqual([entry['case'] for entry in plan['runnable_suite']['entries']],
                         [inventory['catalog']['lineages'][-1]['case']])
        with patch('generative_driver.benchmark_support.corpus._availability',
                   side_effect=inventory['availability']):
            excluded = plan_corpus(inventory['catalog'], repetitions=1,
                                   exclude={ready: 'Intake report only.'})
        with patch('generative_driver.benchmark_support.suites.start_suite') as start:
            with self.assertRaisesRegex(ValueError, 'runnable'):
                start_corpus(excluded, executor='codex')
            start.assert_not_called()

    def test_only_valid_terminal_negative_grades_and_no_driver_enter_measured_failures(self):
        from generative_driver.benchmark_support.corpus import report_corpus
        from test_assistive_reports import evaluation
        plan, original = measured_fixture()
        negative = evaluation('f'*64, False)
        no_driver = evaluation(None, False, status='no-driver', checks=[], passed=0, total=0,
            capabilities_sha256=None, diff=[{'id': 'frozen_package', 'reason': 'no driver emitted'}])
        host_mission_negative = evaluation('f'*64, False, mission={
            'name': 'fresh_worker_mission', 'passed': False, 'fault': 'host',
            'reason': 'The reuse runtime lost its connection.'})
        for grade, status, category, expected in (
            (negative, 'finished', 'completed', 'failed'),
            (no_driver, 'finished', 'completed', 'failed'),
            (host_mission_negative, 'finished', 'completed', 'failed'),
            ({**negative, 'basis': 'self-evidence'}, 'finished', 'completed', 'unscored'),
            ({**negative, 'evidence_sha256': None}, 'finished', 'completed', 'unscored'),
            ({**negative, 'mission': ['malformed']}, 'finished', 'completed', 'unscored'),
            (['malformed'], 'finished', 'completed', 'unscored'),
            (negative, 'running', 'unknown', 'running'),
            (negative, 'finished', 'host', 'stopped'),
        ):
            with self.subTest(status=status, category=category, grade=grade):
                suite = copy.deepcopy(original)
                suite['trials'][0].update(verdict='not equivalent', equivalence=grade,
                    equivalence_status=grade.get('status') if isinstance(grade, dict) else None,
                    outcome='unscored', status=status,
                    outcome_category=category)
                suite['equivalence'] = {'equivalent': 0, 'denominator': 1, 'rate': 0}
                suite.pop('equivalent_and_mission')
                report = report_corpus(plan, suite)
                self.assertEqual(report['slots'][-1]['status'], expected)
                self.assertEqual(report['measured_success']['denominator'], int(expected == 'failed'))

    def test_legacy_frozen_plans_and_saved_suites_keep_their_original_meaning(self):
        from generative_driver.benchmark_support.corpus import _legacy_plan, _digest, report_corpus, validate_plan
        modern, suite = measured_fixture()
        legacy = _legacy_plan(modern['catalog'], modern['repetitions'], modern['availability'])
        self.assertEqual(validate_plan(legacy), legacy)
        identity = suite['experiment']['comparison_identity']
        identity['manifest'] = legacy['runnable_suite']
        identity['manifest_sha256'] = _digest(identity['manifest'])
        report = report_corpus(legacy, suite)
        self.assertEqual(report['schema'], 'benchmark-corpus-report/1')
        self.assertEqual(report['counts']['planned'], 7)
        self.assertEqual(report['counts']['passed'], 1)
        self.assertNotIn('selected_coverage', report)

    def test_measured_success_requires_same_trial_mission_and_six_stages_with_frozen_pins(self):
        from generative_driver.benchmark_support.corpus import report_corpus
        plan, suite = measured_fixture()
        result = report_corpus(plan, suite)
        self.assertEqual(result['counts']['passed'], 1)
        self.assertEqual(result['counts']['blocked'], 6)
        self.assertEqual(result['measured_success']['rate'], 1)
        self.assertEqual(result['demonstrated_coverage']['rate'], 1/7)
        self.assertEqual(result['areas']['usb-peripherals']['counts']['started'], 0)
        changed = copy.deepcopy(suite)
        changed['trials'][0]['stages']['reuse']['workflow_status'] = 'pending'
        self.assertEqual(report_corpus(plan, changed)['counts']['passed'], 0)
        changed = copy.deepcopy(suite)
        changed['trials'][0]['equivalence']['mission']['passed'] = False
        changed['trials'][0]['equivalence']['mission']['fault'] = 'model'
        changed.pop('equivalent_and_mission')
        self.assertEqual(report_corpus(plan, changed)['counts']['failed'], 1)
        for change in (
            lambda s: s['experiment']['comparison_identity']['entry_pins'][0].update(pin_sha256='0'*64),
            lambda s: s['experiment']['dimensions'].update(runtime='replay'),
            lambda s: s['trials'][0].update(case='parameter-store-v1'),
        ):
            changed = copy.deepcopy(suite); change(changed)
            with self.assertRaises(ValueError):
                report_corpus(plan, changed)

    def test_corpus_start_refuses_an_unrunnable_plan_before_starting_an_owner(self):
        from generative_driver.benchmark_support.corpus import load_catalog, plan_corpus, start_corpus
        with patch('generative_driver.benchmark_support.suites.start_suite') as start:
            with self.assertRaisesRegex(ValueError, 'runnable'):
                start_corpus(plan_corpus(unavailable_catalog()))
            start.assert_not_called()

    def test_corpus_report_keeps_unavailable_areas_out_of_model_failures(self):
        from generative_driver.benchmark_support.corpus import load_catalog, plan_corpus, report_corpus
        plan = plan_corpus(unavailable_catalog(), repetitions=2)
        report = report_corpus(plan)
        self.assertEqual(report['counts']['planned'], 14)
        self.assertEqual(report['counts']['blocked'], 14)
        self.assertEqual(report['counts']['failed'], 0)
        self.assertEqual(report['counts']['started'], 0)
        self.assertIsNone(report['measured_success']['rate'])
        self.assertFalse(report['model_benchmark'])
        self.assertEqual(len(report['areas']), 7)

    def test_saved_suite_cannot_be_attached_to_a_different_frozen_corpus(self):
        from generative_driver.benchmark_support.corpus import load_catalog, plan_corpus, report_corpus
        plan = plan_corpus(load_catalog(), repetitions=1)
        with self.assertRaises(ValueError):
            report_corpus(plan, {'schema': 'benchmark-suite-report/2', 'equivalence': {'equivalent': 7}})

    def test_seven_areas_are_distinct_upstream_lineages_not_starter_variants(self):
        from generative_driver.benchmark_support.corpus import load_catalog, validate_catalog
        catalog = validate_catalog(load_catalog())
        self.assertEqual({r['area'] for r in catalog['lineages']}, {
            'robotic-control', 'iot', 'sensors', 'usb-peripherals', 'instruments',
            'controllers', 'industrial-controllers'})
        self.assertEqual(len({r['origin_group'] for r in catalog['lineages']}), 7)
        self.assertTrue(all(len(r['source']['commit']) == 40 for r in catalog['lineages']))
        self.assertTrue(all(r['source']['repository'].startswith('https://github.com/')
                            for r in catalog['lineages']))
        self.assertTrue(all(r['split'] == 'development' for r in catalog['lineages']))
        self.assertEqual(set(catalog['development_cases']), {
            'tq9-v2', 'sampled-sensor-v1', 'parameter-store-v1'})

    def test_plan_retains_blocked_slots_and_never_substitutes_a_synthetic_device(self):
        from generative_driver.benchmark_support.corpus import load_catalog, plan_corpus
        plan = plan_corpus(load_catalog(), repetitions=3)
        self.assertEqual(len(plan['slots']), 21)
        self.assertEqual(len({r['lineage'] for r in plan['slots']}), 7)
        self.assertTrue(all(r['required_stages'] == [
            'acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'] for r in plan['slots']))
        blocked = [r for r in plan['slots'] if r['status'] == 'blocked']
        self.assertTrue(blocked)
        self.assertTrue(all(r['blockers'] for r in blocked))
        self.assertFalse(any(r.get('case') in plan['catalog']['development_cases']
                             for r in plan['slots']))
        usb = next(r for r in plan['slots'] if r['area'] == 'usb-peripherals')
        self.assertEqual(usb['status'], 'blocked')
        self.assertIn('backend', {b['kind'] for b in usb['blockers']})

    def test_family_ancestry_and_evidence_requirements_cannot_be_omitted(self):
        from generative_driver.benchmark_support.corpus import load_catalog, validate_catalog
        for change in (
            lambda c: c['lineages'][0]['source'].update(commit='main'),
            lambda c: c['lineages'][0].update(origin_group=''),
            lambda c: c['lineages'][0]['mission'].update(requirements=[]),
            lambda c: c['lineages'][0]['mission']['requirements'][0].update(basis='source-similarity'),
            lambda c: c['lineages'][0].update(area='unknown'),
            lambda c: c['lineages'].append(copy.deepcopy(c['lineages'][0])),
            lambda c: c['lineages'][1].update(origin_group=c['lineages'][0]['origin_group'], split='test'),
        ):
            data = load_catalog()
            change(data)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_catalog(data)

    def test_plan_is_reproducible_frozen_and_does_not_claim_model_measurements(self):
        from generative_driver.benchmark_support.corpus import load_catalog, plan_corpus, validate_plan
        catalog = load_catalog()
        first = plan_corpus(catalog, repetitions=1)
        self.assertEqual(first, plan_corpus(catalog, repetitions=1))
        self.assertFalse(first['model_benchmark'])
        self.assertEqual(validate_plan(first), first)
        for value in (True, 0, -1, 1.2, 10000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                plan_corpus(catalog, repetitions=value)
        changed = copy.deepcopy(first)
        changed['slots'][0]['status'] = 'ready'
        with self.assertRaises(ValueError):
            validate_plan(changed)
        changed = copy.deepcopy(first)
        changed['catalog']['lineages'][0]['source']['commit'] = 'a' * 40
        with self.assertRaises(ValueError):
            validate_plan(changed)

    def test_cli_reports_coverage_without_runtime_credentials_or_device_access(self):
        result = subprocess.run([sys.executable, '-m', 'generative_driver',
                                 'benchmark', 'corpus', 'plan', '--repetitions', '1'],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan['schema'], 'benchmark-corpus-plan/2')
        self.assertEqual(len(plan['slots']), 7)
        self.assertEqual(plan['catalog']['id'], 'seven-area-pilot')


if __name__ == '__main__':
    unittest.main()
