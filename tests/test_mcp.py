"""The real stdio protocol is the public MCP seam."""
import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpTests(unittest.TestCase):
    def test_registered_emulator_fixture_obeys_home_and_windows_owner_boundaries(self):
        asyncio.run(self.refuse_fixture_owner_mismatch())

    async def refuse_fixture_owner_mismatch(self):
        with tempfile.TemporaryDirectory(prefix='MCP emulated fixture ') as temp:
            root = Path(temp)
            resources = root / 'resources'
            case = resources / 'cases' / 'tq9-v2'
            case.mkdir(parents=True)
            image = case / 'image.bin'
            image.write_bytes(b'fixture')
            truth = resources / 'groundtruth' / 'tq9-v2.enc'
            truth.parent.mkdir()
            truth.write_bytes(b'ciphertext')
            sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
            manifest = {'schema': 'benchmark-case/2', 'id': 'tq9-v2', 'family': 'tq9',
                        'version': '2', 'evaluator_version': '2', 'execution': 'actual-agent-emulation',
                        'evidence_track': 'firmware', 'adapter_key': 'emulator-v2',
                        'approval_scope': 'emulator', 'default_effects': ['write'],
                        'scenarios': ['semantic'], 'required_stages': ['acquire'],
                        'images': {'image.bin': sha(image)},
                        'truth': {'path': 'groundtruth/tq9-v2.enc', 'sha256': sha(truth)},
                        'time_policy': {}, 'provenance': {}, 'limitations': [],
                        'calibration': {'status': 'pending'}}
            (case / 'case.json').write_text(json.dumps(manifest))
            owner = root / 'owner'
            other = root / 'other'
            program = ("import platform; platform.system=lambda:'Windows'; "
                       "import generative_driver.benchmark as b; from pathlib import Path; "
                       f"b.case_root=lambda:Path({str(resources)!r}); "
                       "from generative_driver.mcp import main; main()")
            params = StdioServerParameters(command=sys.executable, args=['-c', program],
                                           cwd=root, env={'GENERATIVE_DRIVER_HOME': str(owner)})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    reply = await session.call_tool('driver_benchmark_run', {
                        'profile': 'tq9-v2', 'options': {'home': str(other)}})
                    self.assertFalse(reply.is_error, reply)
                    self.assertIn('configured MCP home', json.loads(reply.content[0].text)['reason'])
                    reply = await session.call_tool('driver_benchmark_run', {'profile': 'tq9-v2'})
                    self.assertFalse(reply.is_error, reply)
                    self.assertIn('service start', json.loads(reply.content[0].text)['reason'])
                    self.assertFalse((owner / 'service.json').exists())

    def test_unknown_benchmark_profile_fails_before_owner_creation(self):
        asyncio.run(self.refuse_unknown_benchmark_profile())

    async def refuse_unknown_benchmark_profile(self):
        with tempfile.TemporaryDirectory(prefix='MCP unknown benchmark ') as temp:
            params = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'],
                                           cwd=temp, env={'GENERATIVE_DRIVER_HOME':temp})
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    reply = await session.call_tool('driver_benchmark_run', {'profile': 'unknown-case'})
                    self.assertFalse(reply.is_error, reply)
                    refused = json.loads(reply.content[0].text)
                    self.assertFalse(refused.get('ok'), refused)
                    self.assertIn('Unknown benchmark case', refused.get('reason', ''))
                    self.assertFalse((Path(temp)/'service.json').exists())

    def test_benchmark_home_must_match_the_mcp_configurator(self):
        asyncio.run(self.keep_benchmark_on_connected_owner())

    async def keep_benchmark_on_connected_owner(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='MCP selected owner ') as directory:
            root = Path(directory).resolve()
            owner, other = root/'owner', root/'other'
            for home in (owner, other):
                configure('codex', ['no-such-agent-runtime'], home=home)
                call('ping', {}, home=home)
            params = StdioServerParameters(command=sys.executable, args=['-m','generative_driver.mcp'],
                                           cwd=root, env={'GENERATIVE_DRIVER_HOME':str(owner)})
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        for profile in ('tq9', 'bme280'):
                            reply = await session.call_tool('driver_benchmark_run', {
                                'profile':profile, 'options':{'home':str(other)}})
                            self.assertFalse(reply.is_error, reply)
                            refused = json.loads(reply.content[0].text)
                            self.assertFalse(refused.get('ok'), refused)
                            self.assertIn('configured MCP home', refused.get('reason', ''))
                            self.assertFalse(list((other/'runs').glob('*')))
                        reply = await session.call_tool('driver_benchmark_run', {
                            'profile': 'tq9', 'options': {'home': str(owner),
                            'scoped_tool_approval': 'emulator', 'binding': {}}})
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused.get('ok'), refused)
                        self.assertIn('binding', refused.get('reason', ''))
                        self.assertFalse(list((owner/'runs').glob('*')))
                        for same_home in (str(owner), 'owner/.'):
                            reply = await session.call_tool('driver_benchmark_run', {
                                'profile':'bme280', 'options':{'home':same_home}})
                            self.assertFalse(reply.is_error, reply)
                            started = json.loads(reply.content[0].text)
                            self.assertTrue(started.get('ok'), started)
                            reply = await session.call_tool('driver_status', {'run_id':started['run_id']})
                            visible = json.loads(reply.content[0].text)
                            self.assertTrue(visible.get('ok'), visible)
                            self.assertEqual(visible['run_id'], started['run_id'])
            finally:
                for home in (owner, other):
                    call('shutdown', {}, home=home)

    def test_worker_gateway_never_creates_an_absent_owner(self):
        asyncio.run(self.refuse_worker_owner_creation())

    async def refuse_worker_owner_creation(self):
        from generative_driver.client import call
        from mcp.shared.exceptions import MCPError
        from mcp.types import CallToolRequest, CallToolRequestParams, CallToolResult
        with tempfile.TemporaryDirectory(prefix='MCP absent worker owner ') as temp:
            expected_error_log = tempfile.TemporaryFile(mode='w+t')
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'generative_driver.worker_tools', '--home', temp,
                      '--run', 'absent-run', '--assignment', 'absent-assignment'], cwd=temp)
            try:
                async with stdio_client(params, errlog=expected_error_log) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        with self.assertRaisesRegex(MCPError, 'service start'):
                            await session.list_tools()
                        self.assertFalse((Path(temp)/'service.json').exists())
                        reply = await session.send_request(CallToolRequest(params=CallToolRequestParams(
                            name='model_validate', arguments={'model_dir':str(Path(temp)/'model')})), CallToolResult)
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused.get('ok'), refused)
                        self.assertIn('service start', refused.get('reason', ''))
                        self.assertFalse((Path(temp)/'service.json').exists())
                        self.assertFalse((Path(temp)/'runs').exists())
                expected_error_log.seek(0)
                error_text = expected_error_log.read()
                self.assertIn("handler for 'tools/list' raised", error_text)
                self.assertIn('Configurator is not reachable', error_text)
            finally:
                expected_error_log.close()
                call('shutdown', {}, temp)

    def test_windows_mcp_requires_an_independently_started_configurator(self):
        asyncio.run(self.refuse_windows_child_owner())

    async def refuse_windows_child_owner(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='MCP independent owner ') as temp:
            configure('codex', ['no-such-agent-runtime'], home=temp)
            # Substitute only the OS policy boundary; exercise real stdio MCP
            # and client IPC on every host without skipping this Windows rule.
            program = "import platform; platform.system=lambda:'Windows'; from generative_driver.mcp import main; main()"
            params = StdioServerParameters(command=sys.executable, args=['-c', program],
                                           cwd=temp, env={'GENERATIVE_DRIVER_HOME':temp})
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reply = await session.call_tool('driver_start', {'goal':'Absent owner contract'})
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused.get('ok'), refused)
                        self.assertIn('service start', refused.get('reason', ''))
                        self.assertFalse((Path(temp)/'service.json').exists())
                        self.assertFalse((Path(temp)/'runs').exists())
            finally:
                call('shutdown', {}, temp)

    def test_windows_agent_benchmarks_require_an_owner_but_replay_does_not(self):
        asyncio.run(self.require_windows_benchmark_owner())

    async def require_windows_benchmark_owner(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix='MCP benchmark owner ') as temp:
            configure('codex', ['no-such-agent-runtime'], home=temp)
            program = "import platform; platform.system=lambda:'Windows'; from generative_driver.mcp import main; main()"
            params = StdioServerParameters(command=sys.executable, args=['-c', program],
                                           cwd=temp, env={'GENERATIVE_DRIVER_HOME':temp})
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        for profile in ('tq9', 'bme280'):
                            reply = await session.call_tool('driver_benchmark_run', {'profile':profile})
                            self.assertFalse(reply.is_error, reply)
                            refused = json.loads(reply.content[0].text)
                            self.assertFalse(refused.get('ok'), refused)
                            self.assertIn('service start', refused.get('reason', ''))
                            self.assertFalse((Path(temp)/'service.json').exists())
                        reply = await session.call_tool('driver_benchmark_run', {
                            'profile':'setup-smoke', 'output_dir':str(Path(temp)/'replay')})
                        self.assertFalse(reply.is_error, reply)
                        replay = json.loads(reply.content[0].text)
                        self.assertEqual(replay['verdict'], 'passed', replay)
                        self.assertFalse(replay['model_benchmark'])
                        self.assertFalse((Path(temp)/'service.json').exists())
                # The final benchmark start must also refuse if the owner
                # disappears after the MCP preflight has succeeded.
                from generative_driver.benchmark import run
                refused = run('tq9', options={'home':temp}, autostart=False)
                self.assertFalse(refused.get('ok'), refused)
                self.assertIn('service start', refused.get('reason', ''))
                self.assertFalse((Path(temp)/'service.json').exists())
            finally:
                call('shutdown', {}, temp)

    def test_mcp_resume_records_an_explicit_total_budget_without_resetting_the_run(self):
        asyncio.run(self.adjust_budget())

    async def adjust_budget(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix="MCP budget adjustment ") as temp:
            run=call('start',{'goal':'MCP budget contract','budget_seconds':60,
                'executor_config':{'command':['no-such-agent-runtime']}},temp)
            params=StdioServerParameters(command=sys.executable,args=['-m','generative_driver.mcp'],cwd=temp,
                env={'GENERATIVE_DRIVER_HOME':temp})
            try:
                async with stdio_client(params) as (reader,writer):
                    async with ClientSession(reader,writer) as session:
                        await session.initialize()
                        async def stopped(reports):
                            until=time.monotonic()+5
                            while time.monotonic()<until:
                                result=call('result',run,temp)
                                if result['status']=='blocked' and len(result['worker_reports'])>=reports:return result
                                await asyncio.sleep(.01)
                            self.fail('Scripted runtime did not stop with its retained report')
                        async def resume(arguments):
                            until=time.monotonic()+5
                            while time.monotonic()<until:
                                reply=await session.call_tool('driver_resume',{'run_id':run['run_id'],**arguments})
                                self.assertFalse(reply.is_error,reply)
                                response=json.loads(reply.content[0].text)
                                if response.get('ok') or 'stopping' not in response.get('reason',''):return reply
                                await asyncio.sleep(.01)
                            self.fail('Previous scripted worker did not finish stopping')
                        original=await stopped(1)
                        reply=await resume({'budget_seconds':120,'budget_reason':'Operator approved test extension'})
                        self.assertFalse(reply.is_error,reply)
                        self.assertTrue(json.loads(reply.content[0].text)['ok'],reply)
                        reply=await session.call_tool('driver_result',{'run_id':run['run_id']})
                        result=json.loads(reply.content[0].text)
                        self.assertEqual(result['budget_seconds'],120)
                        self.assertEqual(result['created'],original['created'])
                        self.assertEqual(result['worker_reports'][0],original['worker_reports'][0])
                        reply=await session.call_tool('driver_events',{'run_id':run['run_id']})
                        events=json.loads(reply.content[0].text)['events']
                        self.assertEqual(len([e for e in events if e['kind']=='run.budget_extended']),1)
                        await stopped(2)
                        reply=await resume({'budget_seconds':30,'budget_reason':'A total cannot shrink'})
                        self.assertFalse(json.loads(reply.content[0].text)['ok'])
                        self.assertIn('cannot decrease',json.loads(reply.content[0].text)['reason'])
                        self.assertEqual(call('result',run,temp)['budget_seconds'],120)
            finally:
                call('shutdown',{},temp)

    def test_emulator_tool_approval_cannot_authorize_a_generic_device_run(self):
        asyncio.run(self.refuse_non_emulator_approval())

    async def refuse_non_emulator_approval(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix="MCP scoped approval ") as temp:
            configure("codex", ["no-such-agent-runtime"], home=temp)
            run = call("start", {"goal": "Check the approval boundary", "executor_config": {
                "command": ["no-such-agent-runtime"]}}, temp)
            call("cancel", run, temp)
            params = StdioServerParameters(command=sys.executable, args=["-m", "generative_driver.mcp"],
                                            cwd=temp, env={"GENERATIVE_DRIVER_HOME": temp})
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reply = await session.call_tool("driver_resume", {
                            "run_id": run["run_id"], "scoped_tool_approval": "emulator"})
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused["ok"])
                        self.assertIn("tq9", refused["reason"].lower())
                        reply = await session.call_tool("driver_resume", {
                            "run_id": run["run_id"], "scoped_tool_approval": "bound-device"})
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused["ok"])
                        self.assertIn("binding", refused["reason"].lower())
                        reply = await session.call_tool("driver_start", {
                            "goal": "Missing binding must not launch a device run",
                            "scoped_tool_approval": "bound-device"})
                        self.assertFalse(reply.is_error, reply)
                        refused = json.loads(reply.content[0].text)
                        self.assertFalse(refused["ok"])
                        self.assertIn("binding", refused["reason"].lower())
            finally:
                call("shutdown", {}, temp)

    def test_scoped_worker_server_executes_only_the_assigned_tool(self):
        asyncio.run(self.run_scoped_worker())

    async def run_scoped_worker(self):
        from generative_driver.client import call
        with tempfile.TemporaryDirectory(prefix="MCP assigned worker ") as temp:
            source = Path(temp) / "input.bin"
            source.write_bytes(b"abc")
            try:
                started = call("start", {"goal": "Scripted worker gateway check", "inputs": {"image": str(source)},
                    "executor_config": {"command": [sys.executable, "-c", "import time; time.sleep(20)"]}}, home=temp)
                deadline = time.monotonic() + 5
                assignment = None
                while time.monotonic() < deadline:
                    events = call("events", started, home=temp)["events"]
                    assignment = next((e["data"] for e in events if e["kind"] == "stage.assigned"), None)
                    if assignment:
                        break
                    await asyncio.sleep(.02)
                self.assertIsNotNone(assignment)
                params = StdioServerParameters(command=sys.executable,
                    args=["-m", "generative_driver.worker_tools", "--home", temp,
                          "--run", started["run_id"], "--assignment", assignment["id"]], cwd=temp)
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        catalog = {t.name: t for t in (await session.list_tools()).tools}
                        self.assertIn("acquire_firmware_artifact", catalog)
                        self.assertNotIn("interface_execute", catalog)
                        reply = await session.call_tool("acquire_firmware_artifact", {
                            "source_path": next(iter(assignment["inputs"])), "origin": "provided_binary",
                            "expected_sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"})
                        self.assertFalse(reply.is_error, reply)
                        acquired = json.loads(reply.content[0].text)
                        self.assertEqual(Path(acquired["artifact"]).read_bytes(), b"abc")
                        call("cancel", started, home=temp)
                        reply = await session.call_tool("acquire_firmware_artifact", {
                            "source_path": str(source), "origin": "provided_binary", "expected_sha256": "0" * 64})
                        self.assertFalse(json.loads(reply.content[0].text)["ok"])
            finally:
                call("shutdown", {}, home=temp)

    def test_user_can_run_installation_replay_from_the_mcp_interface(self):
        asyncio.run(self.run_smoke())

    async def run_smoke(self):
        with tempfile.TemporaryDirectory(prefix="MCP benchmark ") as temp:
            params = StdioServerParameters(command=sys.executable,
                                           args=["-m", "generative_driver.mcp"], cwd=temp)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    reply = await session.call_tool("driver_benchmark_run", {"profile": "setup-smoke", "output_dir": str(Path(temp) / "run")})
                    self.assertFalse(reply.is_error, reply)
                    report = json.loads(reply.content[0].text)
                    self.assertEqual(report["execution"], "scripted-replay")
                    self.assertFalse(report["model_benchmark"])
                    self.assertEqual(report["verdict"], "passed")

    def test_workflow_survives_the_mcp_client_disconnecting(self):
        asyncio.run(self.reconnect_workflow())

    async def reconnect_workflow(self):
        from generative_driver.client import call
        from generative_driver.setup import configure
        with tempfile.TemporaryDirectory(prefix="MCP durable run ") as temp:
            configure("codex", ["no-such-agent-runtime"], home=temp)
            owner = call('ping', {}, home=temp)
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "generative_driver.mcp"], env={"GENERATIVE_DRIVER_HOME": temp}, cwd=temp)
            try:
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        reply = await session.call_tool("driver_start", {"goal": "Describe a supplied binary",
                            "executor": "codex", "request_id": "mcp-reconnection-check"})
                        self.assertFalse(reply.is_error, reply)
                        started = json.loads(reply.content[0].text)
                        self.assertTrue(started["ok"], started)
                self.assertEqual(call('ping', {}, home=temp, autostart=False)['pid'], owner['pid'])
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        deadline = time.monotonic() + 5
                        while time.monotonic() < deadline:
                            reply = await session.call_tool("driver_status", {"run_id": started["run_id"]})
                            result = json.loads(reply.content[0].text)
                            if result["status"] == "blocked":
                                break
                            await asyncio.sleep(.03)
                        self.assertEqual(result["status"], "blocked", result)
                        self.assertIn("Cannot start", result["reason"])
                        reply = await session.call_tool("driver_result", {"run_id": started["run_id"]})
                        self.assertEqual(json.loads(reply.content[0].text)["accepted_handoffs"], [])
                        reply = await session.call_tool('driver_events', {'run_id':started['run_id']})
                        events = json.loads(reply.content[0].text)['events']
                        self.assertFalse(any(event['kind']=='run.recovered' for event in events))
            finally:
                call("shutdown", {}, home=temp)

    def test_fresh_client_can_check_installation_without_optional_hardware(self):
        asyncio.run(self.check_installation())

    async def check_installation(self):
        with tempfile.TemporaryDirectory(prefix="MCP user space ") as temp:
            params = StdioServerParameters(command=sys.executable,
                                           args=["-m", "generative_driver.mcp"], cwd=temp)
            async with stdio_client(params) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    catalog = {tool.name for tool in (await session.list_tools()).tools}
                    self.assertIn("driver_doctor", catalog)
                    reply = await session.call_tool("driver_doctor", {})
                    self.assertFalse(reply.is_error)
                    report = json.loads(reply.content[0].text)
                    self.assertTrue(report["core"]["ok"])
                    self.assertIn("goose", report["dependencies"])
