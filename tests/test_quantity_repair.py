"""Physical quantity differences on the real gateway, using a deterministic TCP sensor."""
import json
import copy
import shutil
from pathlib import Path
import unittest

from test_development_conditions import GatewayConditionFixture
from generative_driver.configurator import digest, _outcome


class QuantityRepairTests(GatewayConditionFixture):
    def setUp(self):
        super().setUp()
        self.spec['case'] = 'sampled-sensor-v1'
        self.run_dir = self.root/'home/runs/run-fixture'
        self.mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {
            task: {'operation': operation, 'constants': {}, 'inputs': {},
                   'outputs': {name: {'output': name, 'unit': unit}
                               for name, unit in [('temperature', 'degC'), ('sequence', 'count')]}}
            for task, operation in [('measure', 'sample'), ('read', 'read_current')]}}
        self.install_model()

    def install_model(self, *, scale=.0625, unit='degC', output='temperature', offset=0):
        model = json.loads((self.model_dir/'model.json').read_text())
        for operation in ('sample', 'read_current'):
            outputs = model['operations'][operation]['outputs']
            old = next(name for name in outputs if name != 'sequence')
            decoder = outputs.pop(old)
            outputs[output] = {**decoder, 'scale': scale, 'unit': unit, 'offset_value': offset}
        (self.model_dir/'model.json').write_text(json.dumps(model))
        for task in self.mapping['tasks'].values():
            task['outputs']['temperature'] = {'output': output, 'unit': unit}
        (self.workspace/'capabilities.json').write_text(json.dumps(self.mapping))
        self.assignment['inputs'] = {str(self.model_dir): digest(self.model_dir)}
        self.save_assignment()
        from generative_driver.benchmark_support.cases import _write
        _write(self.run_dir/'benchmark/state.json', {'adapted_model_dir': str(self.model_dir),
            'adaptation': {'adapted_model_sha256': digest(self.model_dir/'model.json')}, 'revision': 0})

    def save_assignment(self):
        with self.controller._db() as db:
            db.execute('UPDATE assignments SET stage=?,payload=? WHERE id=?',
                (self.assignment['stage'], json.dumps(self.assignment), 'assignment'))
            db.execute('UPDATE runs SET stage=?,spec=? WHERE id=?',
                (self.assignment['stage'], json.dumps(self.spec), 'run-fixture'))

    def check(self, report=None):
        return self.controller._check(self.spec, self.assignment['stage'], self.run_dir, self.workspace,
            report or {'status': 'completed', 'summary': 'Worker claims success'}, [], self.assignment)

    def differences(self):
        return [d for d in self.check().get('device_diff', []) if d['source'] == 'instrument_comparison']

    def test_wrong_scale_in_declared_physical_units_is_a_value_difference(self):
        self.install_model(scale=1)
        self.call(condition_id='low')
        [difference] = self.differences()
        self.assertEqual(difference['reason'], 'value mismatch')
        self.assertEqual(difference['candidate']['value'], -192)
        self.assertEqual(difference['instrument']['value'], -12)

    def test_equivalent_renamed_outputs_and_floating_point_noise_do_not_repair(self):
        self.install_model(output='celsius_result', offset=5e-10)
        self.call(condition_id='fine_fraction')
        checked = self.check({'status': 'needs_revision', 'summary': 'Untrusted contradiction claim'})
        self.assertFalse(checked.get('device_diff'), checked)
        self.assertEqual(_outcome('probe', {'status': 'completed'}, checked,
            uncertain=False, repairs=0, max_repairs=2).kind, 'advance')

    def test_retained_read_compares_measured_sample_not_requested_source(self):
        self.call(condition_id='low')
        read = self.call(operation='read_current', condition_id='high')
        self.assertEqual(read['outputs']['temperature'], -12)
        self.assertIs(read['observation']['condition']['sample_after_change'], False)
        self.assertEqual(self.differences(), [])

    def test_batch_probe_compares_only_last_result_with_after_observation(self):
        result = self.call('probe_run', condition_id='high', n=3)
        self.assertEqual([r['outputs']['sequence'] for r in result['results']], [1, 2, 3])
        self.assertEqual(self.differences(), [])

    def test_ground_uses_its_sealed_mapping_and_live_assigned_model(self):
        self.install_model(scale=1, unit='count')
        self.assignment['stage'] = 'ground'
        mapping = self.workspace/'ground-evidence/capabilities.json'
        mapping.parent.mkdir(); shutil.copyfile(self.workspace/'capabilities.json', mapping)
        self.assignment['inputs'][str(mapping)] = digest(mapping)
        self.save_assignment()
        self.call(condition_id='fractional')
        [difference] = self.differences()
        self.assertEqual(difference['candidate']['value'], 120)
        self.assertEqual(difference['instrument']['value'], 7.5)
        mapping.write_text('{}')
        from generative_driver.configurator import IntegrityError
        with self.assertRaises(IntegrityError):
            self.check()

    def test_missing_mapping_worker_claims_and_other_model_calls_do_not_repair(self):
        variant = self.workspace/'variant'; shutil.copytree(self.model_dir, variant)
        model = json.loads((variant/'model.json').read_text())
        model['operations']['sample']['outputs']['temperature']['scale'] = 1
        (variant/'model.json').write_text(json.dumps(model))
        self.call(model_dir=str(variant), condition_id='high')
        self.assertEqual(self.differences(), [])
        (self.workspace/'capabilities.json').write_text('{}')
        self.assertFalse(self.check({'status': 'needs_revision', 'summary': 'wrong units'}).get('device_diff'))

    def test_untrusted_or_unpaired_observations_cannot_trigger_quantity_repair(self):
        self.install_model(scale=1)
        self.call(condition_id='high')
        with self.controller._db() as db:
            row = db.execute("SELECT seq,payload FROM events WHERE kind='tool.finished'").fetchone()
        original = json.loads(row['payload'])
        mutations = (
            lambda e: e.update(connected=False),
            lambda e: e['argument_sha256'].update(model_dir='different-model'),
            lambda e: e.update(device_session_id='stale-session'),
            lambda e: e['result']['observation'].update(session_id='another-session'),
            lambda e: e['result']['before_observation'].update(session_id='another-session'),
            lambda e: e['result']['observation'].update(source='worker'),
            lambda e: e['result']['observation'].update(reference_temperature=None),
            lambda e: e['result']['observation']['units'].update(reference_temperature='unknown'),
            lambda e: e['result'].pop('observation'),
            lambda e: e['result'].update(evidence_kind='replay'),
            lambda e: e.update(operation='identify'),
            lambda e: e.update(parameters={'not-the-task-input': 1}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                event = copy.deepcopy(original); mutate(event)
                with self.controller._db() as db:
                    db.execute('UPDATE events SET payload=? WHERE seq=?', (json.dumps(event), row['seq']))
                self.assertEqual(self.differences(), [])

    def test_instrument_acquisition_sequence_is_required_for_measure_and_retained_read(self):
        self.mapping['tasks']['measure']['operation'] = 'read_current'
        (self.workspace/'capabilities.json').write_text(json.dumps(self.mapping))
        self.call(operation='read_current', condition_id='high')
        differences = self.differences()
        self.assertTrue(any(d['reason'] == 'no new acquisition' for d in differences), differences)

    def test_successful_raw_count_call_routes_measured_unit_difference_to_sealed_repair(self):
        self.install_model(scale=1, unit='count', output='raw')
        live = self.call('probe_run', condition_id='fine_fraction', n=1)
        self.assertTrue(live['ok'], live)
        checked = self.check()
        outcome = _outcome('probe', {'status': 'completed'}, checked, uncertain=False, repairs=0, max_repairs=2)
        self.assertEqual(outcome.kind, 'revise', checked)
        differences = [d for d in outcome.diff if d['source'] == 'instrument_comparison']
        self.assertTrue(differences)
        difference = differences[0]
        self.assertEqual(difference['task'], 'measure')
        self.assertEqual(difference['quantity'], 'temperature')
        self.assertEqual(difference['reason'], 'unit mismatch')
        self.assertEqual(difference['candidate'], {'output': 'raw', 'value': 297, 'unit': 'count'})
        self.assertEqual(difference['instrument'], {'quantity': 'reference_temperature', 'value': 18.5625, 'unit': 'degC'})
        self.assertEqual(difference['model_sha256'], digest(self.model_dir))
        self.assertEqual(difference['mapping_sha256'], digest(self.workspace/'capabilities.json'))
        self.assertEqual(difference['session_id'], self.session)
        self.assertEqual(difference['sequence'], {'before': 0, 'after': 1})
        self.assertEqual(_outcome('probe', {'status': 'completed'}, checked,
            uncertain=False, repairs=2, max_repairs=2).kind, 'advance')
        from generative_driver.benchmark import prepare_stage
        import shutil
        prepared = prepare_stage('sampled-sensor-v1', 'interpret', self.run_dir, self.root/'repair',
            options={'revision': 1, 'feedback': differences})
        repair = Path(prepared['work_dir']); self.addCleanup(shutil.rmtree, repair.parent, True)
        self.assertEqual(json.loads((repair/'DEFECTS.json').read_text()), differences)
        self.assertEqual(json.loads((repair/'INPUT_HASHES.json').read_text())['DEFECTS.json'], digest(repair/'DEFECTS.json'))


if __name__ == '__main__':
    unittest.main()
