import copy
import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile


class UpstreamModbusTests(unittest.TestCase):
    def test_public_case_grants_the_actuation_its_coil_mission_requires(self):
        from generative_driver.benchmark_support.registry import resolve_case
        case = resolve_case('zephyr-modbus-v1')
        self.assertEqual(case.approval_scope, 'emulator')
        self.assertTrue({'write', 'actuate'} <= set(case.default_effects))

    def test_monitor_values_do_not_trust_driver_claims_or_require_one_wire_encoding(self):
        from generative_driver.benchmark_support.instruments import family_for
        family = family_for('zephyr-modbus-v1')
        raw = {'values': {'register_pair0': 0xabcd1234, 'register_pair1': 0,
                          'register_pair2': 0, 'register_pair3': 0xffff0000, 'gpio_d': 0x5000}}
        observed = family.observations(raw, {'ok': True, 'outputs': {'value': 999}}, {})
        self.assertEqual(observed['register0'], 0x1234)
        self.assertEqual(observed['register1'], 0xabcd)
        self.assertEqual(observed['register7'], 65535)
        self.assertEqual([observed['coil'+str(i)] for i in range(3)], [1, 0, 1])
        self.assertEqual(observed['value'], 999)
        self.assertTrue(observed['operation_ok'])

    def test_refusal_depends_on_failed_operation_not_internal_error_spelling(self):
        from generative_driver.benchmark_support.zephyr_modbus import observations
        raw = {'values': {**{'register_pair'+str(i): 0 for i in range(4)}, 'gpio_d': 0}}
        for code in ('execution_error', 'framing', 'timeout', 'another-model-error'):
            result = {'ok': False, 'identity_verified': True, 'error': {'fault': 'model', 'code': code}}
            self.assertTrue(observations(raw, result, {})['operation_refused'])
        for result in ({'ok': True}, {'ok': False, 'error': {'fault': 'host'}}, {}):
            self.assertFalse(observations(raw, result, {})['operation_refused'])

    def test_fresh_reuse_requires_requested_effect_readback_and_unchanged_other_state(self):
        from generative_driver.benchmark_support.zephyr_modbus import reuse_passed
        initial = {**{'register'+str(i): 0 for i in range(8)}, **{'coil'+str(i): 0 for i in range(3)}}
        written = {**initial, 'register3': 4660}
        on = {**written, 'coil0': 1}
        caps = {'tasks': {'read_register': {'operation': 'get', 'outputs': {
            'value': {'output': 'reading', 'unit': 'register-value'}}}}}
        def event(before, after, op='set', outputs=None):
            return {'operation': op, 'result': {'ok': True, 'outputs': outputs or {},
                    'units': {'reading': 'register-value'}}, 'before_observation': before, 'observation': after}
        events = [event(initial, written), event(written, written, 'get', {'reading': 4660}),
                  event(written, on), event(on, written)]
        self.assertTrue(reuse_passed(events, caps))
        self.assertFalse(reuse_passed(events[:-1], caps))
        self.assertFalse(reuse_passed([events[0], *events[2:]], caps))
        changed = copy.deepcopy(events); changed[-1]['observation']['register2'] = 11
        self.assertFalse(reuse_passed(changed, caps))

        # An exploratory refusal with no device effect does not undo a later
        # successful mission. A host failure or an uncertain write is different.
        refused = event(initial, initial)
        refused['result'] = {'ok': False, 'error': {'fault': 'operator', 'code': 'invalid_argument'}}
        self.assertTrue(reuse_passed([refused, *events], caps))
        host_failure = copy.deepcopy(refused)
        host_failure['result']['error']['fault'] = 'host'
        self.assertFalse(reuse_passed([host_failure, *events], caps))
        uncertain_write = copy.deepcopy(refused)
        uncertain_write['observation'] = written
        self.assertFalse(reuse_passed([uncertain_write, *events], caps))

    def test_pending_upstream_case_is_visible_and_cannot_launch_without_assets(self):
        from generative_driver.benchmark_support.registry import case_ids, resolve_case, PendingCaseError
        self.assertIn('zephyr-modbus-v1', case_ids())
        with tempfile.TemporaryDirectory() as root, patch('generative_driver.benchmark.case_root', return_value=Path(root)):
            with self.assertRaises(PendingCaseError):
                resolve_case('zephyr-modbus-v1')
