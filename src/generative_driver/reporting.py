"""Measured benchmark summaries; unavailable telemetry never becomes zero usage."""
import hashlib
import json
import platform
import sys
from pathlib import Path
from .benchmark import STAGES


def _usage(workers):
    counters = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
    known = [r.get("usage") for r in workers if isinstance(r.get("usage"), dict)
             and all(type(r["usage"].get(k)) is int for k in ("input_tokens", "output_tokens"))]
    complete = bool(workers) and len(known) == len(workers)
    values = {key: sum(u[key] for u in known) if known and all(type(u.get(key)) is int for u in known) else None
              for key in counters}
    observed = sum(u["input_tokens"] + u["output_tokens"] for u in known) if known else None
    return {**{k: v if complete else None for k, v in values.items()},
            "total_tokens": observed if complete else None, "observed_total_tokens": observed,
            "reported_attempts": len(known), "attempts": len(workers),
            "coverage": len(known) / len(workers) if workers else None,
            "counting": "input + output; cached input and reasoning output are subsets"}


def summarize(result, events, *, case_manifest=None, case_state=None, provenance=None):
    """Summarize real configurator records without inferring missing stage outcomes."""
    case_manifest, case_state = case_manifest or {}, case_state or {}
    workers = result.get("worker_reports", [])
    accepted = {h["assignment_id"]: h for h in result.get("accepted_handoffs", [])}
    opened, tool_seconds, tool_calls = {}, {}, {}
    for event in events:
        data = event.get("data", {})
        key = (data.get("stage"), data.get("name"))
        if event["kind"] == "tool.started":
            opened[key] = event["time"]
            tool_calls[key[0]] = tool_calls.get(key[0], 0) + 1
        elif event["kind"] == "tool.finished" and key in opened:
            tool_seconds[key[0]] = tool_seconds.get(key[0], 0) + max(0, event["time"] - opened.pop(key))
    stages = {}
    verdicts = case_state.get("stage_verdicts", {})
    for stage in STAGES:
        attempts = [w for w in workers if w["stage"] == stage]
        last = attempts[-1] if attempts else None
        checked = verdicts.get(stage, {})
        stages[stage] = {"attempts": [{"assignment_id": w["assignment_id"], "status": w["status"],
            "worker_status": (w.get("report") or {}).get("status"), "accepted": w["assignment_id"] in accepted,
            "elapsed_seconds": w.get("elapsed_seconds"), "usage": w.get("usage")}
            for w in attempts], "attempt_count": len(attempts),
            "workflow_status": "accepted" if last and last["assignment_id"] in accepted else last["status"] if last else "not_run",
            "evaluator_status": checked.get("status", "not_run"), "checks": checked.get("checks", []),
            "worker_seconds": sum(w.get("elapsed_seconds", 0) for w in attempts),
            "tool_seconds": tool_seconds.get(stage, 0), "tool_calls": tool_calls.get(stage, 0),
            "usage": _usage(attempts)}
    status = result["status"]
    verdict = "blocked" if status == "blocked" else "incomplete"
    if status == "completed":
        verdict = "unscored"
    if status in ("failed", "cancelled") or any(v.get("status") == "failed" for v in verdicts.values()):
        verdict = "failed"
    required = case_manifest.get("required_stages", list(STAGES))
    if status == "completed" and required and all(
        verdicts.get(s, {}).get("status") == "passed" and stages[s]["workflow_status"] == "accepted"
        for s in required
    ):
        verdict = "passed"
    execution = case_manifest.get("execution", "unclassified")
    evidence = {"stage_verdicts": verdicts,
                "probe_evaluation": {"observations": case_state.get("probe_evaluation", {}).get("observations", {})},
                "drift_detected": case_state.get("drift_detected", False)}
    if "physical_grounding" in case_state:
        evidence["physical_grounding"] = {k: v for k, v in case_state["physical_grounding"].items()
                                         if k in {"observations", "reference", "physical", "score"}}
    return {"schema": "benchmark-report/1", "run_id": result["run_id"],
            "case": case_manifest.get("id"), "case_version": case_manifest.get("version"),
            "evaluator_version": case_manifest.get("evaluator_version"), "seed": 0,
            "execution": execution,
            "model_benchmark": execution in ("actual-agent-emulation", "actual-agent-physical"),
            "workflow_status": status, "verdict": verdict,
            "reason": result.get("reason"), "stages": stages,
            "case_state": evidence,
            "elapsed_seconds": max(0, result["updated"] - result["created"]),
            "worker_seconds": sum(w.get("elapsed_seconds", 0) for w in workers),
            "tool_seconds": sum(tool_seconds.values()), "tool_calls": sum(tool_calls.values()),
            "tool_time_scope": "Managed worker calls; evaluator time is included only in overall wall time",
            "unfinished_tool_calls": len(opened), "usage": _usage(workers),
            "human_inputs": sum(e["kind"] == "operator.response" for e in events),
            "additional_attempts": sum(max(0, s["attempt_count"] - 1) for s in stages.values()),
            "limitations": case_manifest.get("limitations", []),
            **{k: v for k, v in (provenance or {}).items() if k in {
                "agent", "environment", "budget_seconds", "attempt_limits", "toolchain_revision",
                "skills_revision", "time_policy", "artifact_hashes", "truth_sha256", "case_inputs"}}}


def _tree_hash(root):
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo"):
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _public(value, home):
    if isinstance(value, dict):
        return {k: _public(v, home) for k, v in value.items()
                if k not in {"evaluator_password_file", "authkey", "password"}}
    if isinstance(value, list):
        return [_public(v, home) for v in value]
    if isinstance(value, str):
        if Path(value).is_absolute() and "\n" not in value:
            return Path(value).name
        return value.replace(str(home), "RUN_HOME").replace(str(Path.home()), "USER_HOME")
    return value


def markdown(report):
    def show(value):
        return "unknown" if value is None else f"{value:.2f}" if isinstance(value, float) else str(value)
    lines = ["# Generative Driver benchmark", "",
             f"Run: `{report['run_id']}`. Execution: `{report['execution']}`.", "",
             f"Workflow: **{report['workflow_status']}**. Independent verdict: **{report['verdict']}**.", "",
             "| Stage | Workflow | Evaluator | Attempts | Worker seconds | Tool seconds | Tokens |",
             "|---|---|---|---:|---:|---:|---:|"]
    for name, row in report["stages"].items():
        lines.append(f"| {name} | {row['workflow_status']} | {row['evaluator_status']} | {row['attempt_count']} | "
                     f"{show(row['worker_seconds'])} | {show(row['tool_seconds'])} | {show(row['usage']['total_tokens'])} |")
    lines += ["", f"Overall wall time: {show(report['elapsed_seconds'])} seconds. "
              f"Reported tokens: {show(report['usage']['total_tokens'])}; "
              f"usage available for {report['usage']['reported_attempts']}/{report['usage']['attempts']} worker attempts. "
              f"Human inputs: {report['human_inputs']}.", "",
              "Worker time includes tool waits; tool time is a subset and is not added again. "
              "Cached input and reasoning tokens are subsets of input/output. "
              "Unrun stages and unavailable usage remain visible."]
    if report.get("reason"):
        lines += ["", "Run outcome: " + report["reason"]]
    if report.get("limitations"):
        lines += ["", "## Limits", "", *["- " + item for item in report["limitations"]]]
    return "\n".join(lines) + "\n"


def report_run(run_id, home=None, output=None):
    """Export a saved run to portable JSON and a human-readable Markdown sibling."""
    from .client import call, default_home
    from .benchmark import case_root
    root = Path(home or default_home()).resolve()
    result = call("result", {"run_id": run_id}, home=root)
    if not result.get("ok"):
        raise ValueError(result.get("reason", "Cannot read run"))
    events, cursor = [], 0
    while True:
        page = call("events", {"run_id": run_id, "after": cursor}, home=root)
        events.extend(page["events"])
        if page["cursor"] == cursor or len(page["events"]) < 500:
            break
        cursor = page["cursor"]
    config = result.get("configuration") or {k: result.get(k) for k in ("case", "budget_seconds", "limits", "agent")}
    case = config.get("case")
    manifest_path = case_root() / "cases" / case / "case.json" if case else None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path and manifest_path.is_file() else {}
    state_path = root / "runs" / run_id / "benchmark/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    package_root = Path(__file__).parent
    agent = config.get("agent")
    if not agent and result.get("worker_reports"):
        record = result["worker_reports"][0]
        agent = {key: record.get(key) for key in ("runtime", "provider", "model", "version", "settings")}
    provenance = {"agent": agent, "environment": {"system": platform.system(), "release": platform.release(),
                   "architecture": platform.machine(), "python": platform.python_version()},
                  "budget_seconds": config.get("budget_seconds"), "attempt_limits": config.get("limits"),
                  "toolchain_revision": hashlib.sha256(
                      (_tree_hash(package_root) + _tree_hash(package_root.parent / "interface_runtime")).encode()
                  ).hexdigest(),
                  "skills_revision": _tree_hash(package_root / "resources/toolchain/skills"),
                  "truth_sha256": manifest.get("truth", {}).get("sha256"), "case_inputs": manifest.get("images"),
                  "time_policy": manifest.get("time_policy", state.get("time_policy")),
                  "artifact_hashes": [{"stage": h["stage"], "revision": h.get("revision", 0),
                     "artifacts": [{"name": Path(a["path"]).name, "sha256": a["sha256"], "kind": a.get("kind")}
                                   for a in h["artifacts"]]} for h in result.get("accepted_handoffs", [])]}
    report = summarize(result, events, case_manifest=manifest, case_state=state, provenance=provenance)
    report["evaluator_verdicts"] = result.get("evaluator_verdicts", [])
    report = _public(report, root)
    destination = Path(output).resolve() if output else root / "runs" / run_id / "report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    destination.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return report
