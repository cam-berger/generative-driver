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



def _interventions(events):
    """Publish control-event metadata, excluding operator messages and observations."""
    fields = {
        "operator.response": ("effect_resolution",),
        "run.authorization": ("scoped_tool_approval", "scope"),
        "run.budget_extended": ("old_budget_seconds", "new_budget_seconds", "extension_seconds",
                                "old_deadline", "new_deadline", "reason"),
        "run.cancelled": ("stage", "reason"),
        "run.resumed": (),
        "run.recovered": ("uncertain_effect",),
    }
    rows, counts = [], {}
    for event in events:
        kind = event["kind"]
        if kind not in fields:
            continue
        data = event.get("data", {})
        row = {"event_id": event.get("id"), "kind": kind, "time": event["time"],
               **{key: data[key] for key in fields[kind] if key in data}}
        if kind == "operator.response":
            row["has_observation"] = isinstance(data.get("observation"), dict)
        rows.append(row)
        counts[kind] = counts.get(kind, 0) + 1
    return rows, counts


def summarize(result, events, *, case_manifest=None, case_state=None, provenance=None):
    """Summarize real configurator records without inferring missing stage outcomes."""
    case_manifest, case_state = case_manifest or {}, case_state or {}
    interventions, intervention_counts = _interventions(events)
    starts = [e.get("data", {}).get("budget_seconds") for e in events if e["kind"] == "run.started"]
    extensions = [e for e in interventions if e["kind"] == "run.budget_extended"]
    original_budget = starts[0] if starts else extensions[0].get("old_budget_seconds") if extensions else None
    effective_budget = (extensions[-1].get("new_budget_seconds") if extensions else
                        (provenance or {}).get("budget_seconds", original_budget))
    workers = result.get("worker_reports", [])
    accepted = {h["assignment_id"]: h for h in result.get("accepted_handoffs", [])}
    opened, tool_seconds, tool_calls = {}, {}, {}
    actor_seconds = {"worker": {}, "evaluator": {}}
    for event in events:
        data = event.get("data", {})
        actor = "evaluator" if data.get("actor") == "evaluator" else "worker"
        key = (data.get("stage"), data.get("name"), actor)
        if event["kind"] == "tool.started":
            opened[key] = event["time"]
            tool_calls[key[0]] = tool_calls.get(key[0], 0) + 1
        elif event["kind"] == "tool.finished" and key in opened:
            elapsed = max(0, event["time"] - opened.pop(key))
            tool_seconds[key[0]] = tool_seconds.get(key[0], 0) + elapsed
            actor_seconds[actor][key[0]] = actor_seconds[actor].get(key[0], 0) + elapsed
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
            "worker_tool_seconds": actor_seconds["worker"].get(stage, 0),
            "evaluator_tool_seconds": actor_seconds["evaluator"].get(stage, 0),
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
            "worker_tool_seconds": sum(actor_seconds["worker"].values()),
            "evaluator_tool_seconds": sum(actor_seconds["evaluator"].values()),
            "tool_time_scope": "Managed worker and evaluator calls; other evaluator work is included only in overall wall time",
            "unfinished_tool_calls": len(opened), "usage": _usage(workers),
            "human_inputs": sum(e["kind"] == "operator.response" for e in events),
            "human_inputs_scope": "Counts operator.response messages or observations; other control events are listed separately. Not a count of unique people.",
            "interventions": interventions, "intervention_counts": intervention_counts,
            "interventions_scope": "Recorded control events; several events may belong to one human action, and recovery may be automatic.",
            "original_budget_seconds": original_budget, "effective_budget_seconds": effective_budget,
            "elapsed_scope": "Elapsed wall time from run creation to latest state includes stopped intervals; worker totals are recorded separately. No pause-adjusted active time is inferred.",
            "provenance_meaning": {
                "toolchain_revision": "Hash of the exporting installation; not per-attempt execution provenance.",
                "skills_revision": "Hash of skills in the exporting installation; not per-attempt execution provenance.",
                "environment": "Exporting process environment; historical executions require their own snapshot evidence.",
                "agent": "Current saved runtime configuration; historical identity remains in individual worker records.",
                "case_metadata": "Case manifest from the exporting installation; verify against executed snapshots."},
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
              "Worker time includes worker tool waits. Managed evaluator tool time is recorded separately in JSON. "
              "Overall wall time is measured directly; component times overlap. "
              "Cached input and reasoning tokens are subsets of input/output. "
              "Unrun stages and unavailable usage remain visible."]
    if report.get("interventions"):
        from datetime import datetime, timezone
        lines += ["", "## Recorded interventions", "",
                  "These are controller event counts, not a count of unique people. Operator message content is omitted.", "",
                  "| Time (UTC) | Event | Detail |", "|---|---|---|"]
        for event in report["interventions"]:
            when = datetime.fromtimestamp(event["time"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            if event["kind"] == "run.budget_extended":
                detail = f"Budget {show(event.get('old_budget_seconds'))} → {show(event.get('new_budget_seconds'))} seconds. " + event.get("reason", "")
            else:
                detail = event.get("reason") or event.get("scoped_tool_approval") or ("Content omitted" if event["kind"] == "operator.response" else "Recorded")
            lines.append(f"| {when} | {event['kind']} | {str(detail).replace(chr(10), ' ').replace('|', '/')} |")
    lines += ["", "Wall time includes stopped intervals; no pause-adjusted active time is inferred. " +
              ("Source and environment fields come from the saved execution snapshot." if report.get('snapshot_sha256') else
               "Source and environment fields describe the exporting installation; consult execution snapshot history for earlier attempts.")]
    if report.get("reason"):
        lines += ["", "Run outcome: " + report["reason"]]
    if report.get("limitations"):
        lines += ["", "## Limits", "", *["- " + item for item in report["limitations"]]]
    return "\n".join(lines) + "\n"


def report_run(run_id, home=None, output=None, *, autostart=True):
    """Export a saved run to portable JSON and a human-readable Markdown sibling."""
    from .client import call, default_home
    from .benchmark import case_root
    root = Path(home or default_home()).resolve()
    result = call("result", {"run_id": run_id}, home=root, autostart=autostart)
    if not result.get("ok"):
        raise ValueError(result.get("reason", "Cannot read run"))
    events, cursor = [], 0
    while True:
        page = call("events", {"run_id": run_id, "after": cursor}, home=root, autostart=autostart)
        events.extend(page["events"])
        if page["cursor"] == cursor or len(page["events"]) < 500:
            break
        cursor = page["cursor"]
    config = result.get("configuration") or {k: result.get(k) for k in ("case", "budget_seconds", "limits", "agent")}
    case = config.get("case")
    snapshot = None
    if result.get('snapshot_sha256'):
        from .benchmark_support.snapshots import require_snapshot
        snapshot = require_snapshot(root / 'runs' / run_id, result['case_pin'])
        if snapshot['snapshot_sha256'] != result['snapshot_sha256']:
            raise ValueError('Execution snapshot hash differs from saved run')
    manifest_path = case_root() / "cases" / case / "case.json" if case else None
    manifest = snapshot["case_pin"]["manifest"] if snapshot else (json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path and manifest_path.is_file() else {})
    state_path = root / "runs" / run_id / "benchmark/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    if snapshot:
        executed = snapshot['executed']
        runtime = executed['runtime_configuration']
        provenance = {**executed, 'agent': {'runtime': runtime.get('runtime'), 'model': runtime.get('model'),
            'provider': runtime.get('provider') or ('openai' if runtime.get('runtime') == 'codex' else None),
            'version': runtime.get('version'),
            'settings': {k: runtime[k] for k in ('reasoning_effort', 'max_turns') if k in runtime}},
            'budget_seconds': config.get('budget_seconds'), 'attempt_limits': config.get('limits'),
            'truth_sha256': snapshot['case_pin']['truth_sha256'],
            'case_inputs': snapshot['case_pin']['image_hashes'], 'time_policy': snapshot['case_pin']['time_policy'],
            'artifact_hashes': [{'stage': h['stage'], 'revision': h.get('revision', 0),
                'artifacts': [{'name': Path(a['path']).name, 'sha256': a['sha256'], 'kind': a.get('kind')}
                    for a in h['artifacts']]} for h in result.get('accepted_handoffs', [])]}
    else:
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
    if snapshot:
        report.update(snapshot_sha256=snapshot['snapshot_sha256'], case_pin=snapshot['case_pin'],
                      scenario_id=snapshot['case_pin']['scenario_id'], case_seed=snapshot['case_pin']['case_seed'],
                      evaluator_revision=snapshot['executed']['evaluator_revision'],
                      executed=snapshot['executed'])
        report['provenance_meaning'] = {
            'toolchain_revision': 'Implementation captured before execution; pinned by execution snapshot.',
            'skills_revision': 'Skills captured before execution; pinned by execution snapshot.',
            'evaluator_revision': 'Evaluator implementation captured before execution.',
            'environment': 'Environment captured before execution.',
            'agent': 'Effective runtime configuration captured before execution; individual worker records retain reported identity.',
            'case_metadata': 'Case manifest and input hashes captured before execution.'}
    report["evaluator_verdicts"] = result.get("evaluator_verdicts", [])
    if snapshot and manifest.get('schema') == 'benchmark-case/2':
        from .benchmark_support.evidence import public_v2_report
        report.update({key: state[key] for key in ('evaluations', 'accepted_gates', 'maintenance', 'final_evaluation') if key in state})
        report['progress'] = result.get('progress', {})
        report = public_v2_report(report)
    else:
        report = _public(report, root)
    destination = Path(output).resolve() if output else root / "runs" / run_id / "report.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    destination.with_suffix(".md").write_text(markdown(report), encoding="utf-8")
    return report
