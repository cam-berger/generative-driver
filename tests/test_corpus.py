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
        self.assertEqual(plan['schema'], 'benchmark-corpus-plan/1')
        self.assertEqual(len(plan['slots']), 7)
        self.assertEqual(plan['catalog']['id'], 'seven-area-pilot')


if __name__ == '__main__':
    unittest.main()
