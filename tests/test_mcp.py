"""The real stdio protocol is the public MCP seam."""
import asyncio
import json
import sys
import tempfile
import unittest
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class McpTests(unittest.TestCase):
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
