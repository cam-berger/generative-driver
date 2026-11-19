"""Developer-facing MCP server; workflows belong to the detached configurator."""
import json
import platform
from typing import Literal
from mcp.server.mcpserver import MCPServer
from .setup import doctor

server = MCPServer("generative-driver")


@server.tool()
def driver_doctor() -> str:
    """Check the configured worker runtime, the optional packages and the plugin build, with core Python and the
    optional analysis and emulator tools. plugin.state is stale when the plugin in use is not what setup writes now;
    plugin.reason then gives the setup command to run again."""
    return json.dumps(doctor(), default=str)


def _call(method, **params):
    from .client import call
    return json.dumps(call(method, params, autostart=platform.system() != 'Windows'), default=str)


@server.tool()
def driver_start(goal: str, executor: Literal["codex"] | None = None, inputs: dict | None = None,
                 binding: dict | None = None, effects: list[str] | None = None,
                 budget_seconds: int = 10800, request_id: str | None = None,
                 scoped_tool_approval: Literal["bound-device"] | None = None,
                 ghidra_home: str | None = None, java_home: str | None = None,
                 interpretation: Literal["single", "tasks"] | None = None) -> str:
    """Start a durable six-stage run using a configured runtime. Save the returned run_id.
    Configure the Codex executor with `generative-driver configure` before starting a run.
    Supply explicit inputs and operator binding; effects default to read. request_id deduplicates starts.
    Effects are read, write, actuate and reset; grant reset only for devices that restart when their port opens.
    ghidra_home and java_home optionally select native analysis installations; the configurator then analyzes the
    acquired firmware once for every interpret worker. interpretation is single (the default: one interpret worker) or
    tasks: an inventory, one fresh worker per command and a cross-check, which needs that analysis. Task mode is
    opt-in and its effect on cost and quality is unmeasured.
    Set bound-device approval only with the user's explicit consent to assigned tools for that binding/effect scope.
    """
    return _call("start", goal=goal, executor=executor, inputs=inputs or {}, binding=binding,
                 effects=effects if effects is not None else ["read"], budget_seconds=budget_seconds,
                 request_id=request_id, scoped_tool_approval=scoped_tool_approval,
                 ghidra_home=ghidra_home, java_home=java_home,
                 **({"interpretation": interpretation} if interpretation is not None else {}))


@server.tool()
def driver_status(run_id: str) -> str:
    """Reconnect to the current stage, status, blockers, uncertain effects, latest stage notes and final verdict of a
    durable run. It also gives `deadline`, when the run's budget runs out (seconds since the epoch, like `created`);
    `repairs`, the repairs `used` and the repair `budget`; `worker`, null unless a stage worker is running, else its
    `stage`, when it `started`, its `last_activity` and whether that was `output`, a `tool` call or its start, and the
    interpret `task` it runs ({kind, command}) when the run interprets as tasks; and
    `log`, the path of the service's log, which holds the traceback behind a failed run.
    """
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
    """Read worker reports, accepted evidence with its notes, evaluator verdicts and the final equivalence verdict separately."""
    return _call("result", run_id=run_id)


@server.tool()
def driver_resume(run_id: str, scoped_tool_approval: Literal["emulator", "bound-device"] | None = None,
                  budget_seconds: float | None = None, budget_reason: str | None = None) -> str:
    """Resume after correcting a blocker; recheck accepted evidence.
    Set scoped_tool_approval only after explicit user approval. emulator covers the assigned tools of a run on a supplied device;
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


def main():
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
