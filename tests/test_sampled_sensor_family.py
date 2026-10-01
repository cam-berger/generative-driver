import copy
import tempfile
import unittest
from pathlib import Path

from generative_driver.benchmark_support import sampled_sensor
from generative_driver.benchmark_support.behavior import validate_records
from generative_driver.benchmark_support.emulated_actions import execute_plan
from generative_driver.benchmark_support.family_support import integer_vector, signed16
from benchmark_family_fixtures import toy_sensor_phase, records_for


class SampledSensorOracleTests(unittest.TestCase):
    def setUp(self):
        self.pin, self.truth = toy_sensor_phase()
        self.raw = {"values": {"latched_q4": 65456, "acquisition_counter": 3},
                    "reads": [{"response": "toy monitor bytes"}], "time": 1.0, "physical": False}
        self.result = {"ok": True, "outputs": {"temperature": -5.0, "sequence": 3},
                       "units": {"temperature": "degC", "sequence": "count"}, "transcript": []}

    def verdict(self, result):
        contract = sampled_sensor.contract(self.pin, self.truth, "diagnostic")
        observed = sampled_sensor.observations(self.raw, result, {})
        return validate_records(contract, records_for(contract, observed))["verdict"]

    def test_correct_independent_sample_passes(self):
        self.assertEqual(self.verdict(self.result), "passed")

    def test_wrong_scale_unsigned_and_stale_counter_fail(self):
        for key, value in (("temperature", -2.5), ("temperature", 4091.0), ("sequence", 2)):
            with self.subTest(key=key, value=value):
                result = copy.deepcopy(self.result)
                result["outputs"][key] = value
                self.assertEqual(self.verdict(result), "failed")

    def test_missing_or_boolean_monitor_value_is_unavailable(self):
        for values in ({"acquisition_counter": 3}, {"latched_q4": True, "acquisition_counter": 3}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                sampled_sensor.observations({"values": values}, self.result, {})

    def test_contract_and_plan_are_defensive_copies(self):
        first = sampled_sensor.contract(self.pin, self.truth, "diagnostic")
        first["checks"].clear()
        self.assertEqual(len(sampled_sensor.contract(self.pin, self.truth, "diagnostic")["checks"]), 5)
        plan = sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")
        plan[0]["values"]["source_q4"] = 123
        self.assertEqual(sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")[0]["values"]["source_q4"], -80)

    def test_three_changing_stimuli_reject_constant_reading(self):
        failed = 0
        for sequence, q4 in enumerate((-80, 112, 304), start=3):
            for check in self.truth["phases"]["diagnostic"]["contract"]["checks"]:
                name = check["id"].rsplit("/", 1)[1]
                if name in ("temperature", "reference_temperature"):
                    check["expected"] = q4 / 16
                if name in ("sequence", "monitor_sequence"):
                    check["expected"] = sequence
            self.raw["values"] = {"latched_q4": q4 & 65535, "acquisition_counter": sequence}
            good = copy.deepcopy(self.result)
            good["outputs"] = {"temperature": q4 / 16, "sequence": sequence}
            self.assertEqual(self.verdict(good), "passed")
            failed += self.verdict(self.result) == "failed"
        self.assertEqual(failed, 2)

    def test_scenario_phase_binds_exact_scenario_hash_and_current_revision(self):
        selected = copy.deepcopy(self.truth["phases"])
        selected["diagnostic"]["actions"][0]["values"]["source_q4"] = 112
        self.truth["scenario_phases"] = {"control": selected}
        self.pin.update(scenario_id="control", revision=7)
        self.truth["artifact_sha256"] = "b" * 64
        contract = sampled_sensor.contract(self.pin, self.truth, "diagnostic")
        self.assertEqual(contract["artifact_sha256"], "b" * 64)
        self.assertEqual({check["revision"] for check in contract["checks"]}, {7})
        self.assertEqual(sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")[0]["values"], {"source_q4": 112})
        self.pin["scenario_id"] = "missing"
        with self.assertRaises(KeyError):
            sampled_sensor.contract(self.pin, self.truth, "diagnostic")

    def test_scenario_phase_selects_exact_scenario(self):
        selected = copy.deepcopy(self.truth["phases"])
        selected["diagnostic"]["actions"][0]["values"]["source_q4"] = 112
        self.truth["scenario_phases"] = {"control": selected}
        self.pin["scenario_id"] = "control"
        self.assertEqual(sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")[0]["values"], {"source_q4": 112})
        self.pin["scenario_id"] = "missing"
        with self.assertRaises(KeyError):
            sampled_sensor.build_plan(self.pin, self.truth, "diagnostic")

    def test_helper_rejects_boolean_and_out_of_range_words(self):
        for word in (True, -1, 65536):
            with self.subTest(word=word), self.assertRaises(ValueError):
                signed16(word)
        self.assertEqual(signed16(65456), -80)
        vector = integer_vector({"registers": [1, 2]}, "registers", 2)
        vector[0] = 99
        self.assertEqual(integer_vector({"registers": [1, 2]}, "registers", 2), [1, 2])
        for value in ([1, True], [1], "1,2"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                integer_vector({"registers": value}, "registers", 2)

    def test_action_records_use_measured_units_and_result_ok_metadata(self):
        contract = sampled_sensor.contract(self.pin, self.truth, "diagnostic")
        plan = [{"episode": "toy", "step": "measure", "kind": "call", "task": "measure", "inputs": {}, "grants": []},
                {"episode": "toy", "step": "measure", "kind": "observe",
                 "checks": [check["id"] for check in contract["checks"]]}]
        class Session:
            def set_running(self, value):
                self.running = value
            def observe(self):
                return self.raw
        session = Session()
        session.raw = self.raw
        with tempfile.TemporaryDirectory() as temp:
            evidence = Path(temp) / "evidence.json"
            def run(result, observe=sampled_sensor.observations):
                return execute_plan(session, lambda *args: result, plan, contract, evidence,
                                    observe)
            self.assertEqual(validate_records(contract, run(self.result))["verdict"], "passed")
            for units in ({"temperature": "kelvin", "sequence": "count"},
                          {"sequence": "count"}):
                with self.subTest(units=units):
                    result = copy.deepcopy(self.result)
                    result["units"] = units
                    grade = validate_records(contract, run(result))
                    self.assertEqual(grade["verdict"], "failed")
                    self.assertEqual(next(row["reason"] for row in grade["checks"]
                                          if row["id"].endswith("/temperature")), "unit mismatch")
            failed = copy.deepcopy(self.result)
            failed["ok"] = False
            def spoof(raw, result, inputs):
                return {**sampled_sensor.observations(raw, result, inputs), "operation_ok": True}
            spoof.monitor_units = sampled_sensor.observations.monitor_units
            rows = run(failed, spoof)
            self.assertEqual(next(row for row in rows if row["id"].endswith("/operation_ok"))["value"], False)


if __name__ == "__main__":
    unittest.main()
