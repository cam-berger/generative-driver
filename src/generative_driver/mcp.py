"""Developer-facing MCP server; workflows belong to the detached configurator."""
import json
from typing import Literal
from mcp.server.mcpserver import MCPServer
from .setup import doctor

server = MCPServer("generative-driver")


@server.tool()
def driver_doctor() -> str:
    """Check installed core and optional agent, analysis and emulator dependencies."""
    return json.dumps(doctor(), default=str)


def _call(method, **params):
    from .client import call
    return json.dumps(call(method, params), default=str)


@server.tool()
def driver_start(goal: str, executor: str = "codex", inputs: dict | None = None,
                 binding: dict | None = None, effects: list[str] | None = None,
                 budget_seconds: int = 10800, request_id: str | None = None) -> str:
    """Start a durable seven-stage run using a configured runtime. Save the returned run_id.
    Supply explicit inputs and operator binding; effects default to read. request_id deduplicates starts.
    """
    return _call("start", goal=goal, executor=executor, inputs=inputs or {}, binding=binding,
                 effects=effects or ["read"], budget_seconds=budget_seconds, request_id=request_id)


@server.tool()
def driver_status(run_id: str) -> str:
    """Reconnect to the current stage, status, blockers and uncertain effects of a durable run."""
    return _call("status", run_id=run_id)


@server.tool()
def driver_events(run_id: str, after: int = 0) -> str:
    """Read ordered progress and evidence events after the previous cursor."""
    return _call("events", run_id=run_id, after=after)


@server.tool()
def driver_cancel(run_id: str) -> str:
    """Cancel further work and preserve any uncertainty about an operation interrupted in progress."""
    return _call("cancel", run_id=run_id)


@server.tool()
def driver_result(run_id: str) -> str:
    """Read worker reports, accepted evidence and independent evaluator verdicts separately."""
    return _call("result", run_id=run_id)


@server.tool()
def driver_resume(run_id: str, scoped_tool_approval: Literal["emulator"] | None = None) -> str:
    """Resume after correcting a blocker; recheck accepted evidence.
    Set scoped_tool_approval only after explicit user approval of assigned TQ9 emulator tools.
    That scope cannot authorize physical devices or unrelated MCP tools.
    """
    params = {"run_id": run_id}
    if scoped_tool_approval is not None:
        params["scoped_tool_approval"] = scoped_tool_approval
    return _call("resume", **params)


@server.tool()
def driver_respond(run_id: str, observation: dict) -> str:
    """Supply a requested operator observation with its source and evidence. Never invent observations."""
    return _call("respond", run_id=run_id, observation=observation)


@server.tool()
def driver_benchmark_run(profile: str = "setup-smoke", executor: str | None = None,
                         output_dir: str | None = None, options: dict | None = None) -> str:
    """Run installation replay or start a configured real-agent benchmark.
    setup-smoke uses no inference. tq9 and bme280 require explicit runtime and documented options.
    Supply evaluator password FILE paths through options, never the password itself.
    """
    from .benchmark import run
    return json.dumps(run(case=profile, output_dir=output_dir, executor=executor, options=options), default=str)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
