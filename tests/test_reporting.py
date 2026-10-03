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
        self.assertNotIn("drift_detected", report["case_state"])
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
        stages = ["acquire", "interpret", "probe", "ground", "emit", "reuse"]
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

    def test_interventions_preserve_budget_history_without_exposing_operator_messages(self):
        from generative_driver.reporting import summarize
        result={"run_id":"example","status":"cancelled","created":10,"updated":600,
                "worker_reports":[],"accepted_handoffs":[]}
        events=[
            {"id":1,"kind":"run.started","time":10,"data":{"budget_seconds":120}},
            {"id":2,"kind":"run.authorization","time":20,"data":{"scoped_tool_approval":"emulator","scope":"Assigned tools only"}},
            {"id":3,"kind":"run.cancelled","time":40,"data":{"stage":"interpret","reason":"Cancellation requested"}},
            {"id":4,"kind":"operator.response","time":450,"data":{"message":"PRIVATE OPERATOR MESSAGE","observation":{"private":"PRIVATE OBSERVATION"}}},
            {"id":5,"kind":"run.budget_extended","time":500,"data":{"old_budget_seconds":120,"new_budget_seconds":620,
                "extension_seconds":500,"old_deadline":130,"new_deadline":630,"reason":"User authorized restoring remaining time after pause","private":"EXCLUDED"}},
            {"id":6,"kind":"run.resumed","time":501,"data":{}}]
        report=summarize(result,events,provenance={"budget_seconds":620,"toolchain_revision":"exporter-new"})
        self.assertEqual(report["original_budget_seconds"],120)
        self.assertEqual(report["effective_budget_seconds"],620)
        self.assertEqual(report["human_inputs"],1)
        self.assertEqual(report["intervention_counts"]["run.budget_extended"],1)
        self.assertEqual(len(report["interventions"]),5)
        extension=next(i for i in report["interventions"] if i["kind"]=="run.budget_extended")
        self.assertEqual(extension["new_deadline"],630)
        self.assertIn("restoring",extension["reason"])
        self.assertNotIn("PRIVATE",json.dumps(report))
        self.assertNotIn("EXCLUDED",json.dumps(report))
        self.assertIn("export",report["provenance_meaning"]["toolchain_revision"])
        self.assertEqual(report["elapsed_seconds"],590)
        self.assertNotIn("active_seconds",report)

    def test_export_uses_execution_snapshot_when_installed_manifest_and_exporter_drift(self):
        import shutil
        from unittest.mock import patch
        from generative_driver.benchmark import case_root
        # Import before patching case_root: the adapter keeps a module-level alias.
        from generative_driver.benchmark_support import legacy
        from generative_driver.configurator import Controller
        from generative_driver.reporting import report_run
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installed = root / 'installation'
            shutil.copytree(case_root(), installed)
            def scripted(request, config, cancel):
                return {'runtime': 'codex', 'status': 'blocked', 'reason': 'scripted fixture',
                        'elapsed_seconds': 0, 'usage': None, 'report': None}
            with patch('generative_driver.benchmark.case_root', return_value=installed), patch('generative_driver.configurator.execute', scripted):
                controller = Controller(root / 'home')
                try:
                    run = controller.start({'goal': 'report pinned execution', 'case': 'tq9',
                        'executor_config': {'model': 'original'}})
                    until = time.monotonic() + 5
                    while time.monotonic() < until:
                        status = controller.call('status', run)
                        if status['status'] == 'blocked' and not status['stopping']:
                            break
                        time.sleep(.01)
                    saved = json.loads((root / 'home/runs' / run['run_id'] / 'benchmark/execution.json').read_text())
                    manifest_path = installed / 'cases/tq9/case.json'
                    manifest = json.loads(manifest_path.read_text())
                    manifest.update(version='exporter-new', evaluator_version='exporter-new', execution='wrong-exporter-track')
                    manifest_path.write_text(json.dumps(manifest))
                    with patch('generative_driver.client.call', side_effect=lambda method, params, **kwargs: controller.call(method, params)), patch('generative_driver.reporting._tree_hash', return_value='exporter-new'):
                        report = report_run(run['run_id'], home=root / 'home')
                    self.assertEqual(report['case_version'], '1')
                    self.assertEqual(report['evaluator_version'], '1')
                    self.assertEqual(report['execution'], 'actual-agent-emulation')
                    self.assertEqual(report['snapshot_sha256'], saved['snapshot_sha256'])
                    self.assertEqual(report['toolchain_revision'], saved['executed']['toolchain_revision'])
                    self.assertEqual(report['evaluator_revision'], saved['executed']['evaluator_revision'])
                    self.assertEqual(report['case_pin'], saved['case_pin'])
                    self.assertEqual(report['scenario_id'], 'identity')
                    self.assertEqual(report['case_seed'], 0)
                    self.assertNotIn('exporting installation', report['provenance_meaning']['toolchain_revision'])
                finally:
                    controller.close()

class FreshReuseReportTests(unittest.TestCase):
    def completed(self, stages):
        return {'run_id': 'scripted', 'status': 'completed', 'created': 0, 'updated': 1,
                'worker_reports': [{'stage': s, 'assignment_id': s, 'status': 'completed'} for s in stages],
                'accepted_handoffs': [{'stage': s, 'assignment_id': s} for s in stages]}

    def test_complete_six_gate_report_is_passed_and_has_no_cycle_limits(self):
        # Catches requiring an obsolete seventh gate after accepted fresh reuse.
        from generative_driver.reporting import summarize
        stages = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse']
        report = summarize(self.completed(stages), [],
            case_manifest={'required_stages': stages},
            case_state={'stage_verdicts': {s: {'status': 'passed'} for s in stages}},
            provenance={'attempt_limits': {'max_model_repairs': 2, 'max_maintenance_cycles': 1}})
        self.assertEqual(report['verdict'], 'passed')
        self.assertEqual(list(report['stages']), stages)
        self.assertEqual(report['attempt_limits'], {'max_model_repairs': 2})

    def test_historical_completed_report_cannot_pass_as_six_gate_workflow(self):
        # Catches silently recasting historical measured successes or a partial gate list.
        from generative_driver.reporting import summarize
        six = ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse']
        for stages in (six + ['maintain'], six[:-1]):
            with self.subTest(stages=stages):
                report = summarize(self.completed(stages), [],
                    case_manifest={'required_stages': stages},
                    case_state={'stage_verdicts': {s: {'status': 'passed'} for s in stages}})
                self.assertEqual(report['verdict'], 'incompatible')
                self.assertIn('Unsupported', report['reason'])
