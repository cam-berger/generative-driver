import unittest
from copy import deepcopy
import tempfile
from pathlib import Path

from generative_driver.benchmark_support import parameter_store
from generative_driver.benchmark_support.behavior import validate_records
from generative_driver.benchmark_support.emulated_actions import execute_plan
from interface_runtime.engine import execute, validate_model
from benchmark_family_fixtures import FourCellStoreDevice, records_for, toy_store_model_four_cell, toy_store_phase


class ParameterStoreOracleTests(unittest.TestCase):
    def test_required_state_evidence_must_follow_final_call_in_contiguous_episode(self):
        # Pre-call state or an intervening foreign reset cannot prove update's effects.
        for defect in ('early-observation', 'one-cell-before-update', 'interleaved-reset', 'interleaved-call'):
            pin, truth = toy_store_phase()
            phase = truth['phases']['diagnostic']
            actions = phase['actions']
            target = 'pending-same-bank-update'
            start = next(i for i,a in enumerate(actions) if a['episode']==target)
            end = next(i for i,a in enumerate(actions) if a['episode']==target and a['kind']=='observe')
            if defect=='early-observation':
                observation=actions.pop(end);actions.insert(start+1,observation)
            elif defect=='one-cell-before-update':
                identifier=next(i for i in actions[end]['checks'] if i.endswith('/cell_A_0'))
                actions[end]['checks'].remove(identifier)
                actions.insert(start+1,{'kind':'observe','episode':target,'checks':[identifier]})
            else:
                kind='reset' if defect=='interleaved-reset' else 'call'
                foreign_episode='pending-different-bank-update' if kind=='reset' else 'idle-update'
                foreign=next(a for a in actions if a['episode']==foreign_episode and a['kind']==kind)
                actions.remove(foreign);actions.insert(start+2,foreign)
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                parameter_store.build_plan(pin,truth,'diagnostic')

    def test_intermediate_state_observations_retain_complete_ending_coverage(self):
        pin, truth = toy_store_phase()
        phase=truth['phases']['diagnostic'];actions=phase['actions']
        index=next(i for i,a in enumerate(actions) if a['episode']=='stage-abort' and a['kind']=='call')
        identifier='stage-abort/staged/pending_active'
        phase['contract']['checks'].append({'id':identifier,'scenario':'stage-abort','revision':0,
            'kind':'boolean','unit':'boolean','channel':'independent-monitor','expected':True})
        actions.insert(index+1,{'kind':'observe','episode':'stage-abort','checks':[identifier]})
        self.assertEqual(len(parameter_store.build_plan(pin,truth,'diagnostic')),len(actions))

    def test_pending_update_scenarios_are_mandatory_and_reject_idle_only_model(self):
        # Omitting a reset-delimited pending episode must invalidate the oracle.
        pin, truth = toy_store_phase()
        incomplete = deepcopy(truth)
        incomplete['phases']['diagnostic']['actions'] = [a for a in incomplete['phases']['diagnostic']['actions']
            if a['episode'] != 'pending-same-bank-update']
        with self.assertRaises(ValueError):
            parameter_store.build_plan(pin, incomplete, 'diagnostic')
        for phase in ('diagnostic', 'final'):
            for composed in (False, True):
                device = FourCellStoreDevice([4, 5, 6, 7, 8, 9, 10, 11])
                model = toy_store_model_four_cell()
                if composed:
                    cancel = deepcopy(model['operations']['abort']['steps'][0])
                    cancel['expect']['reject_line_prefix'] = ['ERR:syntax', 'ERR:range']
                    model['operations']['update']['steps'].insert(0, cancel)
                class Session:
                    def reset(self, values):
                        device.__init__(values['committed'])
                        return device.observe()
                    def set_running(self, value): pass
                    def observe(self): return device.observe()
                with tempfile.TemporaryDirectory() as temp:
                    contract = parameter_store.contract(pin, truth, phase)
                    rows = execute_plan(Session(), lambda task, inputs, grants: execute(model, task, inputs,
                        binding={}, allow_effects=grants, transport_factory=device.factory),
                        parameter_store.build_plan(pin, truth, phase), contract, Path(temp)/'evidence.json',
                        parameter_store.observations)
                grade = validate_records(contract, rows)
                with self.subTest(phase=phase, composed=composed):
                    self.assertEqual(grade['verdict'], 'passed' if composed else 'failed')
                    if not composed:
                        failed = {c['id'].split('/')[0] for c in grade['checks'] if not c['passed']}
                        self.assertEqual(failed, {'pending-same-bank-update', 'pending-different-bank-update'})

    def test_complete_rejection_cannot_be_replaced_by_incomplete_reply(self):
        # A truncated rejection must not satisfy operation_ok=False on its own.
        pin, truth = toy_store_phase()
        for mode in ('complete', 'incomplete', 'positive-mismatch', 'identity-rejection', 'timeout'):
            device = FourCellStoreDevice([4, 5, 6, 7, 8, 9, 10, 11])
            original = device.exchange
            def exchange(tx, rx, timeout_ms):
                reply = original(tx, rx, timeout_ms)
                if mode=='identity-rejection' and tx==b'ID\n': return b'ERR:identity\nREADY\n'
                if reply.startswith(b'ERR:'):
                    if mode=='incomplete': return reply.replace(b'READY\n', b'')
                    if mode=='positive-mismatch': return b'OTHER\nREADY\n'
                    if mode=='timeout': raise TimeoutError('toy reply timed out')
                return reply
            device.exchange = exchange
            model = toy_store_model_four_cell()
            if mode=='positive-mismatch':
                model['operations']['commit']['steps'][0]['expect']['contains_line']='ACCEPTED'
            class Session:
                def reset(self, values):
                    device.__init__(values['committed']); return device.observe()
                def set_running(self, value): pass
                def observe(self): return device.observe()
            contract = parameter_store.contract(pin, truth, 'diagnostic')
            checks = [c for c in contract['checks'] if c['scenario'] == 'rejection']
            contract['checks'] = checks; contract.pop('required_scenarios', None)
            plan = [a for a in truth['phases']['diagnostic']['actions'] if a['episode']=='rejection']
            with tempfile.TemporaryDirectory() as temp:
                def run():
                    return execute_plan(Session(), lambda task, inputs, grants: execute(model, task, inputs,
                        binding={}, allow_effects=grants, transport_factory=device.factory), plan, contract,
                        Path(temp)/'evidence.json', parameter_store.observations)
                if mode=='timeout':
                    with self.assertRaisesRegex(RuntimeError, 'Host execution failed'): run()
                    continue
                rows = run()
            with self.subTest(mode=mode):
                self.assertEqual(validate_records(contract, rows)['verdict'], 'passed' if mode=='complete' else 'failed')

    def test_commit_changes_one_cell_and_generation_once(self):
        initial = [4, 5, 6, 7, 8, 9, 10, 11]
        state = parameter_store.expected_state(initial, [
            {"kind": "begin", "bank": "B"},
            {"kind": "put", "slot": 2, "value": -3},
            {"kind": "commit"},
        ])
        self.assertEqual(state, {"committed": [4, 5, 6, 7, 8, 9, -3, 11],
                                 "pending": [4, 5, 6, 7, 8, 9, -3, 11],
                                 "generation": 1, "pending_active": False})
        self.assertEqual(initial, [4, 5, 6, 7, 8, 9, 10, 11])

    def test_abort_discards_stage_and_preserves_generation(self):
        initial = list(range(8))
        state = parameter_store.expected_state(initial, [
            {"kind": "begin", "bank": "A"},
            {"kind": "put", "slot": 0, "value": -9},
            {"kind": "abort"},
        ])
        self.assertEqual(state, {"committed": initial, "pending": initial,
                                 "generation": 0, "pending_active": False})

    def test_stage_is_pending_and_bank_isolated(self):
        state = parameter_store.expected_state(list(range(8)), [
            {"kind": "begin", "bank": "B"},
            {"kind": "put", "slot": 1, "value": -7},
        ])
        self.assertEqual(state["committed"], list(range(8)))
        self.assertEqual(state["pending"], [0, 1, 2, 3, 4, -7, 6, 7])
        self.assertTrue(state["pending_active"])
        self.assertEqual(state["generation"], 0)

    def test_illegal_order_and_invalid_numbers_are_rejected(self):
        cases = [
            [{"kind": "commit"}], [{"kind": "abort"}],
            [{"kind": "put", "slot": 0, "value": 1}],
            [{"kind": "begin", "bank": "A"}, {"kind": "begin", "bank": "B"}],
            [{"kind": "begin", "bank": "A"}, {"kind": "put", "slot": True, "value": 1}],
            [{"kind": "begin", "bank": "A"}, {"kind": "put", "slot": 4, "value": 1}],
            [{"kind": "begin", "bank": "A"}, {"kind": "put", "slot": 0, "value": 32768}],
            [{"kind": "begin", "bank": "A"}, {"kind": "put", "slot": 0, "value": True}],
            [{"kind": "begin", "bank": "C"}], [{"kind": "unknown"}],
        ]
        for actions in cases:
            with self.subTest(actions=actions), self.assertRaises(ValueError):
                parameter_store.expected_state([0] * 8, actions)
        for initial in ([0] * 7, [0] * 7 + [True], [0] * 7 + [-32769]):
            with self.subTest(initial=initial), self.assertRaises(ValueError):
                parameter_store.expected_state(initial, [])

    def test_monitor_projection_and_mutants(self):
        raw = {"values": {"committed": [0, 1, 2, 3, 4, 65529, 6, 7],
                          "pending": [0, 1, 2, 3, 4, 65529, 6, 7],
                          "generation": 1, "pending_active": 0}}
        result = {"ok": True, "outputs": {"value": -7},
                  "units": {"value": "configuration-unit"}}
        observed = parameter_store.observations(raw, result, {})
        self.assertEqual(observed["cell_B_1"], -7)
        self.assertEqual(observed["pending_B_1"], -7)
        self.assertEqual(observed["cell_A_1"], 1)
        self.assertEqual(observed["generation"], 1)
        self.assertFalse(observed["pending_active"])
        self.assertTrue(observed["operation_ok"])
        self.assertEqual(observed["value"], -7)
        names = ("cell_A_2", "cell_B_1", "generation", "value", "pending_active")
        checks = [{"id": "toy/state/" + name, "revision": 0,
                   "kind": "boolean" if name == "pending_active" else "number",
                   "unit": "boolean" if name == "pending_active" else
                           "count" if name == "generation" else "configuration-unit",
                   "channel": "runtime-transcript" if name == "value" else "independent-monitor",
                   "expected": observed[name], **({} if name == "pending_active" else {"absolute_tolerance": 0})}
                  for name in names]
        contract = {"schema": "benchmark-behavior/1", "artifact_sha256": "a" * 64,
                    "checks": checks}
        self.assertEqual(validate_records(contract, records_for(contract, observed))["verdict"], "passed")
        for name, mutation in (("cell_A_2", -3), ("cell_B_1", 9),
                               ("generation", 2), ("value", 20), ("pending_active", True)):
            with self.subTest(name=name):
                changed = deepcopy(observed); changed[name] = mutation
                grade = validate_records(contract, records_for(contract, changed))
                self.assertEqual(grade["verdict"], "failed")
                self.assertEqual([c["id"] for c in grade["checks"] if not c["passed"]], ["toy/state/" + name])

    def test_missing_or_short_monitor_matrix_is_unavailable(self):
        for values in ({}, {"committed": [0] * 7, "pending": [0] * 8,
                            "generation": 0, "pending_active": 0},
                       {"committed": [0] * 8, "pending": [0] * 8,
                        "generation": -1, "pending_active": 0},
                       {"committed": [0] * 8, "pending": [0] * 8,
                        "generation": 0, "pending_active": 2}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                parameter_store.observations({"values": values}, {"ok": True}, {})

    def test_private_contract_and_plan_rebind_and_copy(self):
        phase = {"contract": {"schema": "benchmark-behavior/1", "artifact_sha256": "a" * 64,
                              "checks": [{"id": "toy/state/generation", "revision": 0}]},
                 "actions": [{"kind": "observe", "checks": ["toy/state/generation"]}]}
        truth = {"family": "parameter-store", "artifact_sha256": "b" * 64,
                 "phases": {"diagnostic": phase}}
        pin = {"family": "parameter-store", "scenario_id": "original", "revision": 4}
        contract = parameter_store.contract(pin, truth, "diagnostic")
        self.assertEqual(contract["artifact_sha256"], "b" * 64)
        self.assertEqual(contract["checks"][0]["revision"], 4)
        contract["checks"].clear()
        plan = parameter_store.build_plan(pin, truth, "diagnostic")
        plan[0]["checks"].clear()
        self.assertEqual(len(parameter_store.contract(pin, truth, "diagnostic")["checks"]), 1)
        self.assertEqual(len(parameter_store.build_plan(pin, truth, "diagnostic")[0]["checks"]), 1)

    def test_real_stage_commit_abort_cross_connections_match_independent_oracle(self):
        device, model = FourCellStoreDevice(list(range(8))), toy_store_model_four_cell()
        self.assertTrue(validate_model(model)["ok"])
        def call(task, inputs=None):
            result = execute(model, task, inputs, binding={}, allow_effects=["write"],
                             transport_factory=device.factory)
            self.assertTrue(result["ok"], result)
            return result
        def assert_state(actions, result=None):
            expected = parameter_store.expected_state(list(range(8)), actions)
            observed = parameter_store.observations(device.observe(), result or {"ok": True}, {})
            self.assertEqual([observed[f"cell_{bank}_{slot}"]
                              for bank in "AB" for slot in range(4)], expected["committed"])
            self.assertEqual([observed[f"pending_{bank}_{slot}"]
                              for bank in "AB" for slot in range(4)], expected["pending"])
            self.assertEqual(observed["generation"], expected["generation"])
            self.assertEqual(observed["pending_active"], expected["pending_active"])
        stage_b = [{"kind": "begin", "bank": "B"}, {"kind": "put", "slot": 2, "value": -3}]
        call("stage", {"bank": "B", "slot": 2, "value": -3})
        assert_state(stage_b)
        call("abort")
        assert_state(stage_b + [{"kind": "abort"}])
        self.assertEqual(call("read", {"bank": "B", "slot": 2})["outputs"]["value"], 6)
        call("stage", {"bank": "B", "slot": 2, "value": -3})
        call("commit")
        committed = stage_b + [{"kind": "abort"}] + stage_b + [{"kind": "commit"}]
        assert_state(committed)
        self.assertEqual(call("read", {"bank": "B", "slot": 2})["outputs"]["value"], -3)
        self.assertEqual(call("read", {"bank": "A", "slot": 2})["outputs"]["value"], 2)
        self.assertEqual(device.opens, 7)

    def test_denied_effect_and_wrong_order_mutant_leave_state_unchanged(self):
        device, model = FourCellStoreDevice(list(range(8))), toy_store_model_four_cell()
        denied = execute(model, "stage", {"bank": "A", "slot": 0, "value": -9},
                         binding={}, transport_factory=device.factory)
        self.assertFalse(denied["ok"])
        self.assertEqual(device.opens, 0)
        mutant = deepcopy(model)
        mutant["operations"]["stage"]["steps"].reverse()
        wrong = execute(mutant, "stage", {"bank": "A", "slot": 0, "value": -9},
                        binding={}, allow_effects=["write"], transport_factory=device.factory)
        self.assertFalse(wrong["ok"])
        observed = parameter_store.observations(device.observe(), wrong, {})
        self.assertEqual([observed[f"cell_{bank}_{slot}"] for bank in "AB" for slot in range(4)],
                         list(range(8)))
        self.assertFalse(observed["pending_active"])
        self.assertEqual(observed["generation"], 0)

    def test_real_action_records_keep_runtime_value_unit_and_result_ok(self):
        device, model = FourCellStoreDevice(list(range(8))), toy_store_model_four_cell()
        class Session:
            def set_running(self, value):
                self.running = value
            def observe(self):
                return device.observe()
        checks = [
            {"id": "toy/read/value", "revision": 0, "kind": "number", "expected": 6,
             "unit": "configuration-unit", "channel": "runtime-transcript", "absolute_tolerance": 0},
            {"id": "toy/read/cell_B_2", "revision": 0, "kind": "number", "expected": 6,
             "unit": "configuration-unit", "channel": "independent-monitor", "absolute_tolerance": 0},
            {"id": "toy/read/operation_ok", "revision": 0, "kind": "boolean", "expected": True,
             "unit": "boolean", "channel": "runtime-transcript"},
        ]
        contract = {"schema": "benchmark-behavior/1", "artifact_sha256": "a" * 64, "checks": checks}
        plan = [{"episode": "toy", "step": "read", "kind": "call", "task": "read",
                 "inputs": {"bank": "B", "slot": 2}, "grants": []},
                {"episode": "toy", "step": "observe", "kind": "observe",
                 "checks": [check["id"] for check in checks]}]
        def run(candidate):
            with tempfile.TemporaryDirectory() as temp:
                rows = execute_plan(Session(),
                    lambda task, inputs, grants: execute(candidate, task, inputs, binding={},
                        allow_effects=grants, transport_factory=device.factory),
                    plan, contract, Path(temp) / "evidence.json", parameter_store.observations)
            return validate_records(contract, rows), rows
        grade, rows = run(model)
        self.assertEqual(grade["verdict"], "passed")
        self.assertEqual({row["task_id"]: row["unit"] for row in rows},
                         {"value": "configuration-unit", "cell_B_2": "configuration-unit",
                          "operation_ok": "boolean"})
        wrong_unit = deepcopy(model)
        wrong_unit["operations"]["read"]["outputs"]["value"]["unit"] = "wrong-unit"
        grade, _ = run(wrong_unit)
        self.assertEqual(grade["verdict"], "failed")
        self.assertEqual(next(row["reason"] for row in grade["checks"]
                              if row["id"].endswith("/value")), "unit mismatch")



if __name__ == "__main__":
    unittest.main()
