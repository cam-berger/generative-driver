"""Public installed CLI behavior from a caller-owned working directory."""
import json
import asyncio
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class CliTests(unittest.TestCase):
    def test_setup_connections_keep_the_explicit_state_home(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory(prefix="setup state selection ") as temp:
            root = Path(temp).resolve()
            selected = root / "selected state"
            inherited = root / "different inherited state"
            async def doctor(connection, command_key, environment_key):
                params = StdioServerParameters(command=connection[command_key], args=connection['args'],
                    cwd=str(root), env={'GENERATIVE_DRIVER_HOME': str(inherited),
                                       **connection.get(environment_key, {})})
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reply = await session.call_tool('driver_doctor', {})
                        self.assertFalse(reply.is_error, reply)
                        return json.loads(reply.content[0].text)
            for host in ('codex', 'goose'):
                output = root / host
                generated = subprocess.run([sys.executable, '-m', 'generative_driver', '--home',
                    'selected state', 'setup', '--host', host, '--output', str(output)],
                    cwd=root, env={**os.environ, 'GENERATIVE_DRIVER_HOME': str(inherited)},
                    capture_output=True, text=True, timeout=15)
                self.assertEqual(generated.returncode, 0, generated.stderr)
                if host == 'codex':
                    connection = json.loads((output / 'plugins/generative-driver/.mcp.json').read_text(
                        encoding='utf-8'))['mcpServers']['generative-driver']
                    command_key, environment_key = 'command', 'env'
                else:
                    connection = json.loads((output / 'generative-driver.json').read_text(
                        encoding='utf-8'))['extensions'][0]
                    command_key, environment_key = 'cmd', 'envs'
                report = asyncio.run(doctor(connection, command_key, environment_key))
                self.assertEqual(report['home'], str(selected))
                self.assertEqual(connection[environment_key], {'GENERATIVE_DRIVER_HOME': str(selected)})

    def test_service_commands_start_inspect_and_stop_an_independent_owner(self):
        with tempfile.TemporaryDirectory(prefix="service owner ") as temp:
            home = Path(temp) / "state"
            def cli(action):
                return subprocess.run([sys.executable, "-m", "generative_driver", "--home", str(home),
                    "service", action], cwd=temp, capture_output=True, text=True, timeout=30)
            absent = cli("status")
            self.assertEqual(absent.returncode, 1, absent.stderr)
            self.assertFalse(json.loads(absent.stdout)["ok"])
            self.assertFalse((home / "service.json").exists())
            try:
                started = cli("start")
                self.assertEqual(started.returncode, 0, started.stderr)
                owner = json.loads(started.stdout)
                self.assertTrue(owner["ok"])
                self.assertGreater(owner["pid"], 0)
                inspected = cli("status")
                self.assertEqual(inspected.returncode, 0, inspected.stderr)
                self.assertEqual(json.loads(inspected.stdout)["pid"], owner["pid"])
                stopped = cli("stop")
                self.assertEqual(stopped.returncode, 0, stopped.stderr)
                self.assertTrue(json.loads(stopped.stdout)["stopped"])
                absent = cli("status")
                self.assertEqual(absent.returncode, 1, absent.stderr)
                self.assertFalse(json.loads(absent.stdout)["ok"])
            finally:
                cli("stop")

    def test_cli_budget_extension_requires_a_reason_and_preserves_run_history(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix="CLI budget adjustment ") as temp:
            run=call('start',{'goal':'CLI budget contract','budget_seconds':60,
                'executor_config':{'command':['no-such-agent-runtime']}},temp)
            try:
                until=time.monotonic()+5
                while time.monotonic()<until:
                    original=call('result',run,temp)
                    if original['status']=='blocked':break
                    time.sleep(.01)
                command=[sys.executable,'-m','generative_driver','--home',temp,'resume',run['run_id'],'--budget-seconds','120']
                refused=subprocess.run(command,cwd=temp,capture_output=True,text=True)
                self.assertEqual(refused.returncode,1,refused.stderr)
                self.assertIn('budget_reason',json.loads(refused.stdout)['reason'])
                resumed=subprocess.run([*command,'--budget-reason','Operator approved test extension'],cwd=temp,capture_output=True,text=True)
                self.assertEqual(resumed.returncode,0,resumed.stderr)
                result=call('result',run,temp)
                self.assertEqual(result['budget_seconds'],120)
                self.assertEqual(result['created'],original['created'])
                self.assertEqual(result['worker_reports'][0],original['worker_reports'][0])
                self.assertEqual(len([e for e in call('events',run,temp)['events'] if e['kind']=='run.budget_extended']),1)
            finally:
                call('shutdown',{},temp)

    def test_cli_emulator_approval_refuses_a_generic_device_run(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix="CLI scoped approval ") as temp:
            run = call("start", {"goal": "Check the approval boundary", "executor_config": {
                "command": ["no-such-agent-runtime"]}}, temp)
            call("cancel", run, temp)
            try:
                result = subprocess.run([sys.executable, "-m", "generative_driver", "--home", temp,
                    "resume", run["run_id"], "--approve-emulator-tools"], cwd=temp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("tq9", json.loads(result.stdout)["reason"].lower())
            finally:
                call("shutdown", {}, temp)

    def test_cli_device_approval_requires_an_explicit_binding(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix="CLI bound approval ") as temp:
            run = call("start", {"goal": "Check missing device selection", "executor_config": {
                "command": ["no-such-agent-runtime"]}}, temp)
            call("cancel", run, temp)
            try:
                result = subprocess.run([sys.executable, "-m", "generative_driver", "--home", temp,
                    "resume", run["run_id"], "--approve-bound-device-tools"], cwd=temp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("binding", json.loads(result.stdout)["reason"].lower())
            finally:
                call("shutdown", {}, temp)

    def test_doctor_finds_a_configured_runtime_outside_the_app_search_path(self):
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix="configured doctor ") as temp:
            configure("codex", [sys.executable], model="selected-model", home=temp)
            result = subprocess.run([sys.executable, "-m", "generative_driver", "--home", temp,
                                     "doctor", "--json"], cwd=temp,
                                    env=dict(os.environ, PATH=""), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            configured = json.loads(result.stdout)["dependencies"]["codex"]
            self.assertTrue(configured["available"])
            self.assertEqual(configured["source"], "configuration")
            self.assertEqual(configured["model"], "selected-model")

    def test_developer_can_import_pinned_firmware_through_installed_tool_command(self):
        with tempfile.TemporaryDirectory(prefix="tool caller ") as temp:
            source = Path(temp) / "provided.bin"
            source.write_bytes(b"abc")
            args = Path(temp) / "arguments.json"
            args.write_text(json.dumps({"run_dir": str(Path(temp) / "run"), "source_path": str(source),
                "origin": "provided_binary", "expected_sha256":
                    "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}))
            result = subprocess.run([sys.executable, "-m", "generative_driver", "tool",
                                     "acquire_firmware_artifact", "--arguments", str(args)],
                                    cwd=temp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(Path(report["artifact"]).read_bytes(), b"abc")

    def test_cli_start_and_result_reconnect_to_the_same_background_run(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix="cli workflow ") as temp:
            home = Path(temp) / "state"
            spec = Path(temp) / "spec.json"
            spec.write_text(json.dumps({"goal": "Describe a supplied input", "executor": "codex",
                "executor_config": {"command": ["no-such-codex-runtime"]}}))
            def cli(*args):
                result = subprocess.run([sys.executable, "-m", "generative_driver", "--home", str(home), *args],
                                        cwd=temp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            try:
                started = cli("run", "--spec", str(spec))
                result = cli("result", started["run_id"])
                self.assertEqual(result["run_id"], started["run_id"])
                self.assertIn("accepted_handoffs", result)
                self.assertEqual(cli("cancel", started["run_id"])["status"], "cancelled")
            finally:
                call("shutdown", {}, home=home)

    def test_benchmark_smoke_runs_via_installed_cli_and_reports_replay(self):
        with tempfile.TemporaryDirectory(prefix="benchmark caller ") as temp:
            result = subprocess.run([sys.executable, "-m", "generative_driver", "benchmark", "run",
                                     "--case", "setup-smoke", "--output", str(Path(temp) / "run")],
                                    cwd=temp, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["execution"], "scripted-replay")
            self.assertFalse(report["model_benchmark"])
            self.assertEqual(report["verdict"], "passed")

    def test_setup_generates_host_assets_with_the_installed_interpreter(self):
        from generative_driver.setup import home_path
        with tempfile.TemporaryDirectory(prefix="plugin output ") as temp:
            for host in ("codex", "goose"):
                dest = Path(temp) / host
                result = subprocess.run([sys.executable, "-m", "generative_driver", "setup",
                                         "--host", host, "--output", str(dest)],
                                        cwd=temp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                if host == "codex":
                    plugin = Path(json.loads(result.stdout)["plugin"])
                    marketplace = json.loads((dest / ".agents/plugins/marketplace.json").read_text())
                    self.assertEqual((dest / marketplace["plugins"][0]["source"]["path"]).resolve(), plugin)
                    config = json.loads((plugin / ".mcp.json").read_text())
                    connection = config["mcpServers"]["generative-driver"]
                    self.assertEqual(connection["command"], sys.executable)
                    self.assertEqual(connection["args"], ["-m", "generative_driver.mcp"])
                    self.assertEqual(connection["env"]["GENERATIVE_DRIVER_HOME"], str(home_path()))
                    self.assertTrue((plugin / "skills/generative-driver/SKILL.md").exists())
                else:
                    recipe = json.loads((dest / "generative-driver.json").read_text())
                    connection = recipe["extensions"][0]
                    self.assertEqual(connection["cmd"], sys.executable)
                    self.assertEqual(connection["args"], ["-m", "generative_driver.mcp"])
                    self.assertEqual(connection["envs"]["GENERATIVE_DRIVER_HOME"], str(home_path()))

    def test_configure_preserves_distinct_runtime_commands_and_arguments(self):
        with tempfile.TemporaryDirectory(prefix="driver config ") as temp:
            home = Path(temp) / "state"
            for executor, command in [("codex", "C:/Program Files/Codex/codex.exe"),
                                      ("goose", "/Applications/Goose CLI/goose")]:
                result = subprocess.run([sys.executable, "-m", "generative_driver", "--home", str(home),
                                         "configure", "--executor", executor, "--command", command,
                                         "--model", "configured-model", *(["--reasoning-effort", "high"] if executor == "codex" else [])],
                                        cwd=temp, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads((home / "config.json").read_text())
            self.assertEqual(config["executors"]["codex"]["command"],
                             ["C:/Program Files/Codex/codex.exe"])
            self.assertEqual(config["executors"]["goose"]["command"],
                             ["/Applications/Goose CLI/goose"])
            self.assertEqual(config["executors"]["goose"]["model"], "configured-model")
            self.assertEqual(config["executors"]["codex"]["reasoning_effort"], "high")

    def test_doctor_reports_optional_dependencies_without_requiring_them(self):
        with tempfile.TemporaryDirectory(prefix="driver user space ") as temp:
            env = dict(os.environ, PATH="", GENERATIVE_DRIVER_HOME=str(Path(temp) / "state"))
            result = subprocess.run([sys.executable, "-m", "generative_driver", "doctor", "--json"],
                                    cwd=temp, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertTrue(report["core"]["ok"])
            self.assertFalse(report["dependencies"]["goose"]["available"])
            self.assertEqual(report["dependencies"]["goose"]["required_for"], "Goose workers")
            self.assertTrue(Path(report["home"]).is_absolute())
            self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
