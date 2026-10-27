"""Operator qualification checks use controlled admission, never native or model runs."""
import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@contextmanager
def public_case():
    """Expose a public ready case without opening any evaluator credentials."""
    from generative_driver.benchmark_support.corpus import load_catalog, lineage_identity
    catalog = load_catalog()
    for row in catalog['lineages']:
        row['case'] = None
    row = catalog['lineages'][-1]
    row.update(case='zephyr-modbus-v1', blockers=[])
    manifest = {'schema': 'benchmark-case/2', 'id': row['case'], 'family': 'zephyr-modbus',
                'corpus_identity': lineage_identity(row)}
    pin = {'schema': 'benchmark-case-pin/1', 'case': row['case'], 'case_id': row['case'],
           'scenario_id': 'original', 'case_seed': 0, 'case_version': '1', 'evaluator_version': '1',
           'manifest': manifest, 'manifest_sha256': 'a'*64, 'truth_sha256': 'b'*64,
           'execution': 'actual-agent-emulation', 'evidence_track': 'firmware',
           'scope': 'full-workflow', 'calibration': {'status': 'passed', 'evidence_sha256': 'c'*64}}
    definition = SimpleNamespace(id=row['case'], execution='actual-agent-emulation',
                                 evidence_track='firmware', manifest=manifest)
    with patch('generative_driver.benchmark_support.registry.resolve_case', return_value=definition), \
            patch('generative_driver.benchmark_support.registry.pin_case', side_effect=lambda *args: copy.deepcopy(pin)):
        yield catalog, row, pin


class CorpusPreflightTests(unittest.TestCase):
    def test_public_plan_checks_authenticated_admission_and_retains_blocked_inventory(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        credential = '/operator/private-password.txt'
        with public_case() as (catalog, row, pin):
            plan = plan_corpus(catalog, repetitions=2)
            original = copy.deepcopy(plan)
            with patch('generative_driver.benchmark_support.registry.require_calibration',
                       return_value={'ok': True, 'evidence_sha256': 'd'*64}) as admission, \
                    patch('generative_driver.benchmark_support.suites.start_suite', side_effect=AssertionError('owner')), \
                    patch('generative_driver.client.call', side_effect=AssertionError('owner')), \
                    patch('subprocess.Popen', side_effect=AssertionError('process')), \
                    patch('subprocess.run', side_effect=AssertionError('process')):
                result = check_corpus(plan, {'evaluator_password_files': {row['case']: credential},
                                             'renode': '/absent/native-tool', 'runtime': 'absent-runtime'})
        self.assertTrue(result['ok'], result)
        self.assertFalse(result['model_benchmark'])
        self.assertEqual(result['schema'], 'benchmark-corpus-check/1')
        self.assertEqual(result['plan_sha256'], plan['plan_sha256'])
        self.assertEqual(len(result['cases']), len(catalog['lineages']))
        self.assertEqual(sum(case['status'] == 'blocked' for case in result['cases']), 6)
        checked = next(case for case in result['cases'] if case['lineage'] == row['id'])
        self.assertEqual(checked['status'], 'qualified')
        self.assertEqual(checked['evidence_sha256'], 'd'*64)
        self.assertEqual(result['failures'], [])
        admission.assert_called_once_with(pin, {'evaluator_password_file': credential})
        self.assertEqual(plan, original)
        self.assertNotIn(credential, json.dumps(result))
        self.assertNotIn('evaluator_password', json.dumps(result))

    def test_public_ready_label_without_credentials_is_not_authenticated_admission(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        with public_case() as (catalog, row, _):
            plan = plan_corpus(catalog)
            with patch('generative_driver.benchmark_support.registry.require_calibration') as admission:
                result = check_corpus(plan)
        self.assertFalse(result['ok'])
        checked = next(case for case in result['cases'] if case['lineage'] == row['id'])
        self.assertEqual(checked['availability_status'], 'ready')
        self.assertEqual(checked['status'], 'missing-credential')
        self.assertNotIn('evidence_sha256', checked)
        self.assertEqual(result['failures'][0]['code'], 'missing-credential')
        admission.assert_not_called()

    def test_admission_failures_expose_public_reasons_without_paths_or_secret_error_text(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        credential = '/private/operator-credential.txt'
        secret = 'sentinel-secret-from-a-loader-error'
        with public_case() as (catalog, row, _):
            plan = plan_corpus(catalog)
            for error in (OSError(credential + ': ' + secret), ValueError(secret)):
                with self.subTest(error=type(error).__name__), patch(
                        'generative_driver.benchmark_support.registry.require_calibration', side_effect=error):
                    result = check_corpus(plan, {'evaluator_password_files': {row['case']: credential}})
                self.assertFalse(result['ok'])
                checked = next(case for case in result['cases'] if case['lineage'] == row['id'])
                self.assertEqual(checked['status'], 'unqualified')
                self.assertTrue(checked['reason'])
                self.assertNotIn('evidence_sha256', checked)
                self.assertNotIn(credential, json.dumps(result))
                self.assertNotIn(secret, json.dumps(result))

    def test_real_registry_closure_rejects_incomplete_evidence_despite_public_passed_label(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        from generative_driver.benchmark_support.snapshots import canonical_digest
        record = {'schema': 'benchmark-calibration/1', 'status': 'passed'}
        with public_case() as (catalog, row, pin):
            pin['manifest']['images'] = {}
            pin['calibration']['evidence_sha256'] = canonical_digest(record)
            pin['manifest']['calibration'] = copy.deepcopy(pin['calibration'])
            plan = plan_corpus(catalog)
            with patch('generative_driver.benchmark_support.emulator.truth_for_case', return_value={
                    'calibration': record, 'inventory_state': 'qualified', 'images': {},
                    'input_hashes': {}, 'mutations': {'mutants': []}, 'references': {}}) as truth:
                result = check_corpus(plan, {'evaluator_password_files': {row['case']: '/unopened/handle'}})
        self.assertFalse(result['ok'])
        checked = next(case for case in result['cases'] if case['lineage'] == row['id'])
        self.assertEqual(checked['status'], 'unqualified')
        self.assertNotIn('evidence_sha256', checked)
        self.assertEqual(truth.call_count, 1)

    def test_stale_selected_pin_or_lost_readiness_is_refused_before_authentication(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        for defect in ('pin', 'readiness'):
            with self.subTest(defect=defect), public_case() as (catalog, row, pin):
                plan = plan_corpus(catalog)
                if defect == 'pin':
                    pin['manifest_sha256'] = 'e'*64
                else:
                    pin['calibration']['status'] = 'pending'
                with patch('generative_driver.benchmark_support.registry.require_calibration') as admission:
                    result = check_corpus(plan, {'evaluator_password_files': {row['case']: '/unopened/handle'}})
                self.assertFalse(result['ok'])
                checked = next(case for case in result['cases'] if case['lineage'] == row['id'])
                self.assertEqual(checked['status'], 'unqualified')
                self.assertNotIn('evidence_sha256', checked)
                self.assertEqual(result['failures'][0]['code'],
                                 'case-pin-changed' if defect == 'pin' else 'selection-unavailable')
                admission.assert_not_called()

    def test_full_inventory_drift_refuses_check_and_start_before_authentication(self):
        from generative_driver.benchmark_support.corpus import plan_corpus, start_corpus, lineage_identity
        from generative_driver.benchmark_support.preflight import check_corpus
        for selection in ('excluded', 'selected-blocked'):
            with self.subTest(selection=selection), public_case() as (catalog, row, pin):
                pending = catalog['lineages'][0]
                pending.update(case='sampled-sensor-v1', blockers=[])
                # This controlled catalog tests freshness, not an actual upstream admission.
                catalog['development_cases'].remove('sampled-sensor-v1')
                included = [row['id']] if selection == 'excluded' else [row['id'], pending['id']]
                plan = plan_corpus(catalog, include=included)
                self.assertEqual(plan['availability'][0]['status'], 'blocked')
                current = copy.deepcopy(plan['availability'])
                newly_ready = copy.deepcopy(pin)
                newly_ready.update(case=pending['case'], case_id=pending['case'])
                newly_ready['manifest'].update(id=pending['case'], family='sampled-sensor',
                                                corpus_identity=lineage_identity(pending))
                current[0].update(status='ready', blockers=[], case_pin=newly_ready)
                by_lineage = {available['lineage']: available for available in current}
                credential = '/unopened/private-credential-handle'
                with patch('generative_driver.benchmark_support.corpus._availability',
                           side_effect=lambda lineage: copy.deepcopy(by_lineage[lineage['id']])), \
                        patch('generative_driver.benchmark_support.registry.require_calibration',
                              return_value={'ok': True, 'evidence_sha256': 'd'*64}) as admission, \
                        patch('generative_driver.benchmark_support.suites.start_suite') as start:
                    checked = check_corpus(plan, {'evaluator_password_files': {row['case']: credential}})
                    with self.assertRaisesRegex(ValueError, 'freeze a new corpus plan'):
                        start_corpus(plan, executor='codex')
                    start.assert_not_called()
                    self.assertFalse(checked['ok'])
                    self.assertEqual(checked['failures'][0]['code'], 'plan-context-changed')
                    self.assertIn('freeze a new corpus plan', checked['failures'][0]['reason'])
                    self.assertNotIn(credential, json.dumps(checked))
                    self.assertTrue(all('evidence_sha256' not in case for case in checked['cases']))
                    admission.assert_not_called()

    def test_no_selected_ready_cases_fails_and_preserves_exclusion_reasons_and_blockers(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        with public_case() as (catalog, row, _):
            excluded = catalog['lineages'][0]['id']
            plan = plan_corpus(catalog, include=[], exclude=[(excluded, 'Outside this mission.')])
            with patch('generative_driver.benchmark_support.registry.require_calibration') as admission:
                result = check_corpus(plan, {'evaluator_password_files': {row['case']: '/unopened/handle'}})
        self.assertFalse(result['ok'])
        self.assertTrue(all(case['status'] == 'excluded' for case in result['cases']))
        checked = next(case for case in result['cases'] if case['lineage'] == excluded)
        self.assertEqual(checked['selection_reason'], 'Outside this mission.')
        self.assertEqual(checked['availability_status'], 'blocked')
        self.assertTrue(checked['blockers'])
        self.assertEqual(result['failures'][0]['code'], 'no-selected-ready-cases')
        admission.assert_not_called()

    def test_all_selected_but_blocked_is_not_a_successful_check(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        with public_case() as (catalog, _, _):
            catalog['lineages'][-1]['case'] = None
            result = check_corpus(plan_corpus(catalog))
        self.assertFalse(result['ok'])
        self.assertTrue(all(case['status'] == 'blocked' and case['selected'] for case in result['cases']))

    def test_legacy_plan_selects_all_and_tampering_is_refused_before_admission(self):
        from generative_driver.benchmark_support.corpus import plan_corpus, _legacy_plan
        from generative_driver.benchmark_support.preflight import check_corpus
        with public_case() as (catalog, row, _):
            current = plan_corpus(catalog)
            legacy = _legacy_plan(catalog, current['repetitions'], current['availability'])
            with patch('generative_driver.benchmark_support.registry.require_calibration',
                       return_value={'ok': True, 'evidence_sha256': 'd'*64}) as admission:
                result = check_corpus(legacy, {'evaluator_password_files': {row['case']: '/unopened/handle'}})
                self.assertTrue(result['ok'])
                self.assertTrue(all(case['selected'] for case in result['cases']))
                admission.reset_mock()
                legacy['slots'][0]['status'] = 'ready'
                with self.assertRaisesRegex(ValueError, 'Frozen corpus plan changed'):
                    check_corpus(legacy, {'evaluator_password_files': {row['case']: '/unopened/handle'}})
                admission.assert_not_called()

    def test_malformed_credential_maps_are_rejected_without_echoing_values(self):
        from generative_driver.benchmark_support.corpus import plan_corpus
        from generative_driver.benchmark_support.preflight import check_corpus
        with public_case() as (catalog, row, _):
            plan = plan_corpus(catalog)
            for invalid in ([], '', 7, {row['case']: ''}, {row['case']: 7}):
                with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'map case IDs'):
                    check_corpus(plan, {'evaluator_password_files': invalid})


if __name__ == '__main__':
    unittest.main()
