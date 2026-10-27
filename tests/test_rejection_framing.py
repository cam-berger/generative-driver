"""Complete error-response behavior through public runtime and worker seams."""
import json
from pathlib import Path
import tempfile
import unittest


def line_model(max_lines=2):
    def step(command, capture, lines):
        return {"op": "exchange", "tx": [{"text": command + "\n"}],
                "rx": {"mode": "lines", "max_bytes": 128,
                       "end": {"line_equals": "END"}, "max_lines": lines},
                "timeout_ms": 1000, "capture": capture,
                "expect": {"final_line_equals": "END", "reject_line_prefix": ["ERR:"]}}
    identify = step("WHO", "identity", 4)
    identify["expect"]["contains_line"] = "DEVICE=LAB"
    apply = step("APPLY", "reply", max_lines)
    apply["expect"]["first_line_prefix"] = "END"
    return {"schema": "interface-model/4", "device": {"name": "Scripted controller"},
            "channel": {"type": "tcp"}, "identity": {"operation": "identify"},
            "operations": {
                "identify": {"effect": "read", "parameters": {},
                             "outputs": {"identity": {"from": "identity", "kind": "utf8", "strip": True}},
                             "steps": [identify]},
                "apply": {"effect": "write", "parameters": {}, "outputs": {},
                          "steps": [apply]}},
            "provenance": [{"item": "operations." + name, "source": "independent toy protocol fixture"}
                           for name in ("identify", "apply")]}


def predicted_replies():
    return {"schema": "interface-replies/1", "operations": {
        "identify": {"steps": [["DEVICE=LAB", "END"]]},
        "apply": {"steps": [["END"]]}}}


class CompleteRejectionTests(unittest.TestCase):
    def test_qualification_refuses_a_bound_that_cannot_capture_a_complete_rejection(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "model.json").write_text(json.dumps(line_model(max_lines=2)))
            (root / "replies.json").write_text(json.dumps(predicted_replies()))
            arguments = {"run_dir": str(root / "run"), "model_dir": str(root),
                         "replies": str(root / "replies.json")}
            self.assertTrue(call_tool("model_qualify", arguments)["ok"])
            (root / "model.json").write_text(json.dumps(line_model(max_lines=1)))
            result = call_tool("model_qualify", arguments)
            self.assertFalse(result["ok"], "A line-limit failure is not a captured device rejection")

    def test_candidate_contract_refusal_preserves_structural_runtime_validity(self):
        from interface_runtime.engine import validate_model
        from generative_driver.toolkit import call_tool
        for end in ({"line_equals": "END"}, {"line_token": "END"}, {"line_prefix": "END"}):
            with self.subTest(end=end), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate = line_model(max_lines=1)
                candidate["operations"]["apply"]["steps"][0]["rx"]["end"] = end
                self.assertTrue(validate_model(candidate)["ok"], "Bad behavior must remain executable by the evaluator")
                (root / "model.json").write_text(json.dumps(candidate))
                checked = call_tool("model_validate", {"model_dir": str(root), "run_dir": str(root / "run")})
                self.assertFalse(checked["ok"])
                self.assertIn("max_lines", json.dumps(checked["defects"]))

    def test_a_rejection_that_is_its_own_completion_can_use_one_line(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = line_model(max_lines=1)
            step = candidate["operations"]["apply"]["steps"][0]
            step["rx"]["end"] = {"line_token": "RESULT"}
            step["expect"] = {"final_line_equals": "RESULT OK", "reject_line_prefix": ["RESULT ERR:"]}
            replies = predicted_replies()
            replies["operations"]["apply"]["steps"] = [["RESULT OK"]]
            (root / "model.json").write_text(json.dumps(candidate))
            (root / "replies.json").write_text(json.dumps(replies))
            result = call_tool("model_qualify", {"run_dir": str(root / "run"),
                "model_dir": str(root), "replies": str(root / "replies.json")})
            self.assertTrue(result["ok"], result)

    def test_multiline_rejection_is_captured_completely_without_retry_or_later_steps(self):
        from interface_runtime.engine import execute
        from line_device import line_device
        for newline in (b"\n", b"\r\n"):
            with self.subTest(newline=newline):
                candidate = line_model(max_lines=3)
                candidate["operations"]["apply"]["steps"].append(
                    {**candidate["operations"]["apply"]["steps"][0],
                     "tx": [{"text": "VERIFY\n"}], "capture": "verified"})
                rejection = newline.join((b"ERR:denied", b"DETAIL:state not ready", b"END")) + newline
                seen = []
                with line_device({b"WHO": newline.join((b"DEVICE=LAB", b"END")) + newline,
                                  b"APPLY": rejection, b"VERIFY": b"END\n"}, seen) as binding:
                    result = execute(candidate, "apply", binding=binding, allow_effects=["write"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["outputs"], {})
                self.assertEqual(result["error"]["fault"], "model")
                self.assertEqual(result["error"]["code"], "execution_error")
                self.assertEqual(result["transcript"][-1]["rx_hex"], rejection.hex())
                self.assertEqual(result["transcript"][-1]["status"], "completed")
                self.assertTrue(result["transcript"][-1]["device_rejected"])
                self.assertEqual(seen, [b"WHO", b"APPLY"])

    def test_missing_completion_and_receive_overflow_remain_incomplete_and_are_not_retried(self):
        from interface_runtime.engine import execute
        from line_device import line_device
        for reply, code in ((b"ERR:denied\n", "timeout"),
                            (b"ERR:denied\nDETAIL:extra\nEND\n", "limit")):
            with self.subTest(code=code):
                candidate = line_model(max_lines=2)
                candidate["operations"]["apply"]["steps"][0]["timeout_ms"] = 100
                seen = []
                with line_device({b"WHO": b"DEVICE=LAB\nEND\n", b"APPLY": reply}, seen) as binding:
                    result = execute(candidate, "apply", binding=binding, allow_effects=["write"])
                self.assertFalse(result["ok"])
                self.assertEqual(result["outputs"], {})
                self.assertEqual(result["error"]["code"], code)
                self.assertEqual(result["transcript"][-1]["status"], "attempted")
                self.assertNotIn("device_rejected", result["transcript"][-1])
                self.assertEqual(seen, [b"WHO", b"APPLY"])

    def test_generic_deployment_repairs_the_contract_without_contacting_a_device(self):
        from generative_driver.client import call
        from suite_fixtures import write_scripted_case_worker
        import time
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            home = root / "home"
            source = root / "source.bin"
            source.write_bytes(b"independent synthetic framing fixture")
            command = write_scripted_case_worker(root)
            script = Path(command[1])
            replacement = "    candidate=json.loads(" + repr(json.dumps(line_model(max_lines=1))) + ")\n"
            replacement += """    if pathlib.Path('defects.json').is_file():
        feedback=json.loads(pathlib.Path('defects.json').read_text())
        assert any(d.get('source')=='model_contract' and 'max_lines' in json.dumps(d.get('defects')) for d in feedback)
        candidate['operations']['apply']['steps'][0]['rx']['max_lines']=2
    pathlib.Path('model.json').write_text(json.dumps(candidate))
"""
            original = "    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')\n"
            text = script.read_text()
            self.assertIn(original, text)
            script.write_text(text.replace(original, replacement))
            run = None
            try:
                run = call("start", {"goal": "Scripted generic framing repair; no device or inference",
                    "inputs": {"image.bin": str(source)}, "effects": ["read"], "budget_seconds": 60,
                    "executor_config": {"command": command}}, home)
                deadline = time.monotonic() + 20
                while True:
                    result = call("result", run, home)
                    if result["status"] in ("completed", "failed", "blocked", "cancelled") and not result["stopping"]:
                        break
                    if time.monotonic() >= deadline:
                        self.fail(result)
                    time.sleep(.03)
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(result["progress"]["repairs"], 1, result["reason"])
                self.assertFalse(result["uncertain_effect"])
                handoffs = result["accepted_handoffs"]
                self.assertEqual([(h["stage"], h["revision"]) for h in handoffs],
                    [("acquire", 0), ("interpret", 0), ("probe", 0), ("interpret", 1),
                     ("probe", 1), ("ground", 1), ("emit", 1), ("reuse", 1)])
                probe = next(h for h in handoffs if h["stage"] == "probe")
                self.assertEqual(probe["route"], "interpret")
                feedback = probe["notes"]["inline"]["configurator"]["device_diff"]
                self.assertTrue(any(d["source"] == "model_contract" and "max_lines" in json.dumps(d) for d in feedback))
                events = call("events", run, home)["events"]
                names = [e["data"]["name"] for e in events if e["kind"] == "tool.started"]
                self.assertEqual(names, ["acquire_firmware_artifact"])
            finally:
                if run is not None:
                    call("cancel", run, home)
                call("shutdown", {}, home)

    def test_qualification_uses_a_minimal_error_reply_within_the_declared_byte_bound(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = line_model(max_lines=2)
            candidate["operations"]["apply"]["steps"][0]["rx"]["max_bytes"] = 12
            (root / "model.json").write_text(json.dumps(candidate))
            (root / "replies.json").write_text(json.dumps(predicted_replies()))
            result = call_tool("model_qualify", {"run_dir": str(root / "run"),
                "model_dir": str(root), "replies": str(root / "replies.json")})
            self.assertTrue(result["ok"], result)

    def test_qualification_completes_a_whitespace_prefixed_single_line_error(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = line_model(max_lines=1)
            step = candidate["operations"]["apply"]["steps"][0]
            step["rx"]["end"] = {"line_token": "RESULT"}
            step["expect"] = {"final_line_equals": "RESULT OK", "reject_line_prefix": ["  "]}
            replies = predicted_replies();replies["operations"]["apply"]["steps"] = [["RESULT OK"]]
            (root / "model.json").write_text(json.dumps(candidate))
            (root / "replies.json").write_text(json.dumps(replies))
            result = call_tool("model_qualify", {"run_dir": str(root / "run"),
                "model_dir": str(root), "replies": str(root / "replies.json")})
            self.assertTrue(result["ok"], result)

    def test_managed_write_with_an_impossible_reply_is_refused_before_contact_and_repairs(self):
        from generative_driver.client import call
        from suite_fixtures import write_scripted_case_worker
        from line_device import line_device
        import time
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve();home = root / "home"
            source = root / "source.bin";source.write_bytes(b"scripted managed framing fixture")
            command = write_scripted_case_worker(root);script = Path(command[1])
            text = script.read_text()
            original = "    shutil.copy2(resources_root()/'bench/cases/setup-smoke/model.json','model.json')\n"
            replacement = "    candidate=json.loads(" + repr(json.dumps(line_model(1))) + ")\n"
            replacement += """    if pathlib.Path('defects.json').is_file():
        candidate['operations']['apply']['steps'][0]['rx']['max_lines']=2
    pathlib.Path('model.json').write_text(json.dumps(candidate))
"""
            self.assertIn(original, text);text = text.replace(original, replacement)
            needle = "    else:\n        report={'status':'blocked'"
            probe = """    elif task['stage']=='probe':
        args=json.loads(next(a.split('=',1)[1] for a in sys.argv if a.startswith('mcp_servers.stage.args=')))
        identity={'run_id':args[args.index('--run')+1],'assignment_id':args[args.index('--assignment')+1]}
        model=next(p for p in task['inputs'] if pathlib.Path(p,'model.json').is_file())
        if json.loads(pathlib.Path(model,'model.json').read_text())['operations']['apply']['steps'][0]['rx']['max_lines']==1:
            out=call('tool',{**identity,'name':'interface_execute','arguments':{'model_dir':model,'operation':'apply'}},args[args.index('--home')+1])
            assert out.get('ok') is False and 'max_lines' in out.get('reason',''),out
        report={'status':'completed','summary':'Managed dispatch refused the model before contact','artifacts':[],'checks':[],'unresolved':[]}
    else:
        report={'status':'blocked'"""
            self.assertIn(needle, text);script.write_text(text.replace(needle, probe))
            seen = [];run = None
            with line_device({b"WHO": b"DEVICE=LAB\nEND\n", b"APPLY": b"ERR:denied\nEND\n"}, seen) as binding:
                try:
                    run = call('start', {'goal':'Managed candidate contract repair, no inference',
                        'inputs':{'image.bin':str(source)},'binding':binding,'effects':['read','write'],
                        'budget_seconds':60,'executor_config':{'command':command}}, home)
                    deadline=time.monotonic()+20
                    while True:
                        result=call('result',run,home)
                        if result['status'] in ('completed','failed','blocked','cancelled') and not result['stopping']:break
                        if time.monotonic()>=deadline:self.fail(result)
                        time.sleep(.03)
                    self.assertEqual(result['status'],'completed',str({'reason':result['reason'],'wire':seen,
                        'stderr':Path(result['worker_reports'][-1]['stderr']).read_text() if result['status']!='completed' else ''}))
                    self.assertFalse(result['uncertain_effect'])
                    self.assertEqual(result['progress']['repairs'],1)
                    events=call('events',run,home)['events']
                    self.assertTrue(any(e['kind']=='tool.refused' and 'max_lines' in e['data']['reason'] for e in events))
                    self.assertFalse(any(e['kind']=='tool.started' and e['data']['name']=='interface_execute' for e in events))
                    self.assertEqual(seen,[])
                finally:
                    if run is not None:call('cancel',run,home)
                    call('shutdown',{},home)

    def test_qualifier_reports_delay_only_operations_without_a_transfer_crash(self):
        from generative_driver.toolkit import call_tool
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = line_model()
            candidate["operations"]["apply"]["steps"] = [{"op": "delay", "ms": 1}]
            replies = predicted_replies();replies["operations"]["apply"]["steps"] = []
            (root / "model.json").write_text(json.dumps(candidate))
            (root / "replies.json").write_text(json.dumps(replies))
            result = call_tool("model_qualify", {"run_dir": str(root / "run"),
                "model_dir": str(root), "replies": str(root / "replies.json")})
            self.assertFalse(result["ok"])
            self.assertIn("apply", result.get("not_qualified", []), result)
            self.assertTrue(Path(result["report"]).is_file())
