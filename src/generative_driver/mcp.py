"""Developer-facing MCP server; workflows belong to the detached configurator."""
import json
import platform
from pathlib import Path
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
    return json.dumps(call(method, params, autostart=platform.system() != 'Windows'), default=str)


@server.tool()
def driver_start(goal: str, executor: str = "codex", inputs: dict | None = None,
                 binding: dict | None = None, effects: list[str] | None = None,
                 budget_seconds: int = 10800, request_id: str | None = None,
                 scoped_tool_approval: Literal["bound-device"] | None = None) -> str:
    """Start a durable seven-stage run using a configured runtime. Save the returned run_id.
    Supply explicit inputs and operator binding; effects default to read. request_id deduplicates starts.
    Set bound-device approval only with the user's explicit consent to assigned tools for that binding/effect scope.
    """
    return _call("start", goal=goal, executor=executor, inputs=inputs or {}, binding=binding,
                 effects=effects if effects is not None else ["read"], budget_seconds=budget_seconds,
                 request_id=request_id, scoped_tool_approval=scoped_tool_approval)


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
def driver_resume(run_id: str, scoped_tool_approval: Literal["emulator", "bound-device"] | None = None,
                  budget_seconds: float | None = None, budget_reason: str | None = None) -> str:
    """Resume after correcting a blocker; recheck accepted evidence.
    Set scoped_tool_approval only after explicit user approval. emulator covers assigned TQ9 tools;
    bound-device requires the saved explicit device binding and allowed effects. Neither permits unrelated tools.
    Set budget_seconds only after explicit user approval to increase the total wall-clock budget from the original
    start. Supply a nonblank budget_reason; the increase is recorded without resetting time or prior attempts.
    """
    params = {"run_id": run_id}
    if scoped_tool_approval is not None:
        params["scoped_tool_approval"] = scoped_tool_approval
    if budget_seconds is not None:
        params["budget_seconds"] = budget_seconds
        params["budget_reason"] = budget_reason
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
    For real-agent profiles, options.home must resolve to this MCP connection's configured home.
    """
    from .benchmark import run
    autostart = platform.system() != 'Windows'
    if profile in ('tq9', 'bme280'):
        from .client import call, default_home
        selected_home = (options or {}).get('home')
        if selected_home and Path(selected_home).expanduser().resolve() != default_home().resolve():
            return json.dumps({'ok':False, 'reason':
                'Benchmark options.home must match the configured MCP home. Omit options.home, '
                'or reconnect using an MCP configuration for the selected home.'})
        if not autostart:
            owner = call('ping', home=selected_home, autostart=False)
            if not owner.get('ok'):
                return json.dumps(owner, default=str)
    return json.dumps(run(case=profile, output_dir=output_dir, executor=executor, options=options,
                          autostart=autostart), default=str)


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
