import unittest

from generative_driver.benchmark_support.behavior import bind_task, validate_records, project_feedback


class BehaviorTests(unittest.TestCase):
    def test_each_target_reaches_the_candidate_parameter(self):
        model = {'operations': {'set_percent': {'parameters': {
            'percent': {'type': 'integer', 'minimum': 0, 'maximum': 100},}, 'outputs': {}}}}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {
            'set_output': {'operation': 'set_percent', 'constants': {},
                'inputs': {'fraction': {'parameter': 'percent', 'scale': 100, 'offset': 0}},
                'outputs': {}}}}
        first = bind_task(mapping, 'set_output', {'fraction': .25}, model)
        second = bind_task(mapping, 'set_output', {'fraction': .75}, model)
        self.assertEqual(first, {'operation': 'set_percent', 'parameters': {'percent': 25}})
        self.assertEqual(second['parameters'], {'percent': 75})
        with self.assertRaises(ValueError):
            bind_task(mapping, 'set_output', {'fraction': True}, model)

    def test_boolean_cannot_pass_as_temperature(self):
        check = {'id': 'toy/read/temperature', 'revision': 0, 'kind': 'number',
                 'expected': 1.0, 'absolute_tolerance': .01, 'unit': 'degC',
                 'channel': 'independent-monitor'}
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': [check]}
        record = {'id': check['id'], 'revision': 0, 'value': True, 'unit': 'degC',
                  'channel': 'independent-monitor', 'artifact_sha256': 'a' * 64}
        self.assertEqual(validate_records(contract, [record])['verdict'], 'failed')

    def test_exact_inventory_and_units_cannot_be_bypassed(self):
        checks = [{'id': f'toy/{i}/sample', 'revision': 0, 'kind': 'number',
                   'expected': value, 'absolute_tolerance': .01, 'unit': 'degC',
                   'channel': 'independent-monitor'}
                  for i, value in enumerate((-2.5, 0.0, 6.25))]
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': checks}
        records = [{'id': row['id'], 'revision': 0, 'value': row['expected'],
                    'unit': 'degC', 'channel': 'independent-monitor',
                    'artifact_sha256': 'a' * 64} for row in checks]
        self.assertEqual(validate_records(contract, records)['verdict'], 'passed')
        wrong = [records[:-1], records + [records[0]],
                 [dict(r, value=0) for r in records],
                 [dict(r, unit='raw') for r in records],
                 [dict(r, value=float('nan')) for r in records],
                 [dict(r, revision=1) for r in records],
                 [dict(r, artifact_sha256='b' * 64) for r in records]]
        for rows in wrong:
            with self.subTest(rows=rows):
                self.assertEqual(validate_records(contract, rows)['verdict'], 'failed')

    def test_numeric_tolerance_is_inclusive_and_far_value_fails(self):
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': [{'id': 'toy/read', 'revision': 0, 'kind': 'number',
                                'expected': 1.0, 'absolute_tolerance': .01,
                                'unit': 'degC', 'channel': 'independent-monitor'}]}
        record = {'id': 'toy/read', 'revision': 0, 'value': 1.005,
                  'unit': 'degC', 'channel': 'independent-monitor',
                  'artifact_sha256': 'a' * 64}
        self.assertEqual(validate_records(contract, [record])['verdict'], 'passed')
        self.assertEqual(validate_records(contract, [dict(record, value=100.5)])['verdict'], 'failed')
        self.assertEqual(validate_records(contract, [dict(record, value='1.0')])['verdict'], 'failed')

    def test_invalid_contract_tolerance_raises(self):
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': [{'id': 'toy/read', 'revision': 0, 'kind': 'number',
                                'expected': 1.0, 'absolute_tolerance': -.01,
                                'unit': 'degC', 'channel': 'independent-monitor'}]}
        with self.assertRaises(ValueError):
            validate_records(contract, [])

    def test_binding_rejects_incomplete_and_colliding_parameters(self):
        model = {'operations': {'set': {'parameters': {
            'level': {'type': 'integer', 'minimum': 0, 'maximum': 100},
            'bank': {'type': 'string', 'enum': ['A', 'B']}}, 'outputs': {}}}}
        task = {'operation': 'set', 'constants': {'bank': 'A'},
                'inputs': {'fraction': {'parameter': 'level', 'scale': 100, 'offset': 0}},
                'outputs': {}}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {'set_output': task}}
        self.assertEqual(bind_task(mapping, 'set_output', {'fraction': .25}, model)['parameters'],
                         {'level': 25, 'bank': 'A'})
        bad = [({'fraction': .25, 'extra': 1}, task),
               ({}, task),
               ({'fraction': .255}, task),
               ({'fraction': 1.01}, task),
               ({'fraction': float('inf')}, task),
               ({'fraction': .25}, dict(task, constants={'bank': 'C'})),
               ({'fraction': .25}, dict(task, constants={'level': 1, 'bank': 'A'})),
               ({'fraction': .25}, dict(task, constants={})),
               ({'fraction': .25}, dict(task, inputs={'fraction': {'parameter': 'level', 'scale': float('nan'), 'offset': 0}}))]
        for supplied, spec in bad:
            with self.subTest(supplied=supplied, spec=spec):
                with self.assertRaises(ValueError):
                    bind_task({'schema': 'benchmark-capabilities/2', 'tasks': {'set_output': spec}},
                              'set_output', supplied, model)

    def test_copy_preserves_bounded_bank_enums_and_operation_independence(self):
        parameter = {'type': 'string', 'enum': ['A', 'B']}
        model = {'operations': {name: {'parameters': {'bank': parameter}, 'outputs': {}}
                                for name in ('select_bank', 'pick_register')}}
        tasks = {name: {'operation': name, 'constants': {},
                        'inputs': {'requested_bank': {'parameter': 'bank', 'kind': 'copy'}},
                        'outputs': {}}
                 for name in model['operations']}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': tasks}
        for op in tasks:
            for bank in ('A', 'B'):
                self.assertEqual(bind_task(mapping, op, {'requested_bank': bank}, model),
                                 {'operation': op, 'parameters': {'bank': bank}})
            with self.assertRaises(ValueError):
                bind_task(mapping, op, {'requested_bank': 'C'}, model)

    def test_output_mapping_checks_name_and_unit_without_rewriting_values(self):
        model = {'operations': {'read': {'parameters': {}, 'outputs': {
            'sensor_temp': {'from': 'reply', 'kind': 'ascii_number', 'unit': 'degC'}}}}}
        task = {'operation': 'read', 'constants': {}, 'inputs': {},
                'outputs': {'temperature': {'output': 'sensor_temp', 'unit': 'degC'}}}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {'read_temp': task}}
        self.assertEqual(bind_task(mapping, 'read_temp', {}, model),
                         {'operation': 'read', 'parameters': {}})
        for output in ({'output': 'wrong', 'unit': 'degC'},
                       {'output': 'sensor_temp', 'unit': 'kelvin'}):
            with self.subTest(output=output):
                with self.assertRaises(ValueError):
                    bind_task({'schema': 'benchmark-capabilities/2', 'tasks': {
                        'read_temp': dict(task, outputs={'temperature': output})}},
                        'read_temp', {}, model)

    def test_feedback_projects_only_safe_observed_diagnostics(self):
        records = [{'id': 'toy/read', 'task_id': 'read_temp', 'value': 23.5,
                    'unit': 'degC', 'reason': 'value mismatch',
                    'expected': 22.0, 'absolute_tolerance': .1,
                    'artifact_sha256': 'a' * 64, 'private_evidence': '/secret'}]
        feedback = project_feedback(records)
        rendered = str(feedback)
        self.assertIn('read_temp', rendered)
        self.assertIn('23.5', rendered)
        self.assertNotIn('22.0', rendered)
        self.assertNotIn('0.1', rendered)
        self.assertNotIn('/secret', rendered)
        self.assertNotIn('aaaa', rendered)

    def test_boolean_and_text_checks_compare_exact_values(self):
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': [
                        {'id': 'toy/enabled', 'revision': 1, 'kind': 'boolean', 'expected': False,
                         'unit': '', 'channel': 'runtime-transcript'},
                        {'id': 'toy/name', 'revision': 1, 'kind': 'text', 'expected': 'ready',
                         'unit': '', 'channel': 'runtime-transcript'}]}
        records = [{'id': 'toy/enabled', 'revision': 1, 'value': False, 'unit': '',
                    'channel': 'runtime-transcript', 'artifact_sha256': 'a' * 64},
                   {'id': 'toy/name', 'revision': 1, 'value': 'ready', 'unit': '',
                    'channel': 'runtime-transcript', 'artifact_sha256': 'a' * 64}]
        self.assertEqual(validate_records(contract, records)['verdict'], 'passed')
        for change in ({'value': 0}, {'value': True}, {'channel': 'independent-monitor'}):
            with self.subTest(change=change):
                self.assertEqual(validate_records(contract, [dict(records[0], **change), records[1]])['verdict'], 'failed')
        self.assertEqual(validate_records(contract, [records[0], dict(records[1], value='READY')])['verdict'], 'failed')

    def test_duplicate_contract_ids_and_invalid_numeric_expectations_raise(self):
        check = {'id': 'toy/read', 'revision': 0, 'kind': 'number', 'expected': 1.0,
                 'absolute_tolerance': .01, 'unit': 'degC', 'channel': 'independent-monitor'}
        contract = {'schema': 'benchmark-behavior/1', 'artifact_sha256': 'a' * 64,
                    'checks': [check, dict(check)]}
        with self.assertRaises(ValueError):
            validate_records(contract, [])
        for bad in (float('nan'), float('inf'), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_records(dict(contract, checks=[dict(check, expected=bad)]), [])

    def test_copy_numeric_scalar_obeys_model_bounds(self):
        model = {'operations': {'set': {'parameters': {'gain': {
            'type': 'number', 'minimum': -1.0, 'maximum': 1.0}}, 'outputs': {}}}}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {'set_gain': {
            'operation': 'set', 'constants': {},
            'inputs': {'gain': {'parameter': 'gain', 'kind': 'copy'}}, 'outputs': {}}}}
        self.assertEqual(bind_task(mapping, 'set_gain', {'gain': -.5}, model)['parameters'], {'gain': -.5})
        for bad in (True, float('nan'), 1.01):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                bind_task(mapping, 'set_gain', {'gain': bad}, model)

    def test_feedback_rejects_private_reason_and_complex_value(self):
        projection = project_feedback([{'task_id': 'toy', 'value': {'expected': 99},
                                        'unit': 'degC', 'reason': 'expected 99 ± 0.1',
                                        'evidence_path': '/secret'}])
        rendered = str(projection)
        self.assertIn('toy', rendered)
        self.assertNotIn('99', rendered)
        self.assertNotIn('/secret', rendered)

    def test_extreme_integer_input_fails_as_validation_error(self):
        model = {'operations': {'set': {'parameters': {'x': {
            'type': 'number', 'minimum': -1.0, 'maximum': 1.0}}, 'outputs': {}}}}
        mapping = {'schema': 'benchmark-capabilities/2', 'tasks': {'set_x': {
            'operation': 'set', 'constants': {},
            'inputs': {'x': {'parameter': 'x', 'kind': 'copy'}}, 'outputs': {}}}}
        with self.assertRaises(ValueError):
            bind_task(mapping, 'set_x', {'x': 10 ** 1000}, model)
