"""Benchmark report behavior using independent, literal telemetry records."""
import unittest
import json
import tempfile
import time
from pathlib import Path


class ReportTests(unittest.TestCase):
    def test_worker_and_evaluator_tool_time_are_reported_separately(self):
        from generative_driver.reporting import summarize
        result = {"run_id": "example", "status": "blocked", "created": 0, "updated": 8,
                  "worker_reports": [{"assignment_id": "p", "stage": "probe", "status": "completed",
                                      "elapsed_seconds": 2}], "accepted_handoffs": []}
        events = [{"kind": kind, "time": when, "data": {"stage": "probe", "name": "probe_run", "actor": actor}}
                  for kind, when, actor in [("tool.started", 1, "worker"), ("tool.finished", 2, "worker"),
                                             ("tool.started", 4, "evaluator"), ("tool.finished", 7, "evaluator")]]
        report = summarize(result, events)
        self.assertEqual(report["stages"]["probe"]["worker_tool_seconds"], 1)
        self.assertEqual(report["stages"]["probe"]["evaluator_tool_seconds"], 3)
        self.assertEqual(report["tool_seconds"], 4)
        self.assertEqual(report["worker_seconds"], 2)
        self.assertEqual(report["elapsed_seconds"], 8)

    def test_report_preserves_independent_observations_for_offline_scoring(self):
        from generative_driver.reporting import summarize
        result = {"run_id": "example", "status": "blocked", "created": 0, "updated": 1,
                  "worker_reports": [], "accepted_handoffs": []}
        state = {"probe_evaluation": {"observations": {"temperature": 22.3}, "private_contract": "excluded"},
                 "drift_detected": True, "stage_verdicts": {}, "evaluator_password_file": "excluded",
                 "physical_grounding": {"observations": {"temperature_c": 22.3}, "physical": True}}
        report = summarize(result, [], case_state=state)
        self.assertEqual(report["case_state"]["probe_evaluation"]["observations"], {"temperature": 22.3})
        self.assertTrue(report["case_state"]["drift_detected"])
        self.assertEqual(report["case_state"]["physical_grounding"], state["physical_grounding"])
        self.assertNotIn("excluded", json.dumps(report))

    def test_saved_report_distinguishes_a_blocked_run_from_a_model_score(self):
        from generative_driver.client import call
        from generative_driver.reporting import report_run
        with tempfile.TemporaryDirectory(prefix="reported run ") as temp:
            try:
                run = call("start", {"goal": "Report an unavailable runtime", "budget_seconds": 120, "executor_config": {
                    "command": ["no-such-agent-runtime"]}}, home=temp)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if call("status", run, home=temp)["status"] == "blocked":
                        break
                    time.sleep(.02)
                output = Path(temp) / "public/report.json"
                report = report_run(run["run_id"], home=temp, output=output)
                self.assertEqual(report["verdict"], "blocked")
                self.assertFalse(report["model_benchmark"])
                self.assertIsNone(report["usage"]["total_tokens"])
                self.assertEqual(report["budget_seconds"], 120)
                self.assertEqual(json.loads(output.read_text())["run_id"], run["run_id"])
                self.assertNotIn(temp, output.read_text())
                self.assertIn("not_run", output.with_suffix(".md").read_text())
            finally:
                call("shutdown", {}, home=temp)

    def test_completion_without_every_independent_gate_cannot_be_a_pass(self):
        from generative_driver.reporting import summarize
        result = {"run_id": "example", "status": "completed", "created": 0, "updated": 1,
                  "worker_reports": [], "accepted_handoffs": [], "evaluator_verdicts": []}
        stages = ["acquire", "interpret", "probe", "ground", "emit", "reuse", "maintain"]
        state = {"stage_verdicts": {s: {"status": "passed"} for s in stages}}
        manifest = {"required_stages": stages, "execution": "actual-agent-emulation"}
        self.assertEqual(summarize(result, [], case_manifest=manifest, case_state=state)["verdict"], "unscored")

    def test_report_keeps_missing_usage_and_unrun_stages_visible(self):
        from generative_driver.reporting import summarize
        result = {"run_id": "example", "status": "blocked", "stage": "interpret",
                  "reason": "analysis unavailable", "created": 10, "updated": 25,
                  "worker_reports": [
                      {"assignment_id": "a", "stage": "acquire", "status": "completed",
                       "elapsed_seconds": 2, "runtime": "codex", "model": "example-model",
                       "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}},
                      {"assignment_id": "b", "stage": "interpret", "status": "blocked",
                       "elapsed_seconds": 7, "runtime": "codex", "model": "example-model", "usage": None}],
                  "accepted_handoffs": [{"assignment_id": "a", "stage": "acquire", "artifacts": []}],
                  "evaluator_verdicts": []}
        events = [{"kind": "tool.started", "time": 11, "data": {"stage": "acquire", "name": "import"}},
                  {"kind": "tool.finished", "time": 12.5, "data": {"stage": "acquire", "name": "import", "ok": True}},
                  {"kind": "operator.response", "time": 23, "data": {"message": "Checked installation"}}]
        report = summarize(result, events, case_manifest={"id": "tq9", "version": "1", "evaluator_version": "1"})
        self.assertEqual(report["elapsed_seconds"], 15)
        self.assertEqual(report["worker_seconds"], 9)
        self.assertEqual(report["stages"]["acquire"]["tool_seconds"], 1.5)
        self.assertEqual(report["stages"]["acquire"]["usage"]["total_tokens"], 120)
        self.assertIsNone(report["usage"]["total_tokens"])
        self.assertEqual(report["usage"]["observed_total_tokens"], 120)
        self.assertEqual(report["usage"]["reported_attempts"], 1)
        self.assertEqual(report["stages"]["reuse"]["workflow_status"], "not_run")
        self.assertEqual(report["human_inputs"], 1)
        self.assertEqual(report["verdict"], "blocked")
