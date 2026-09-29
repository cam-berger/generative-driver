"""Installed developer command line."""
import argparse
import json
import os
import sys
from pathlib import Path
from .setup import configure, doctor, integration_assets


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Delegate the benchmark's evolving public interface to its own parser.
    if "benchmark" in argv:
        index = argv.index("benchmark")
        if index in (0, 2) and (index == 0 or argv[0] == "--home"):
            from .benchmark import main as benchmark_main
            if index:
                os.environ["GENERATIVE_DRIVER_HOME"] = argv[1]
            return benchmark_main(argv[index + 1:])
    parser = argparse.ArgumentParser(prog="generative-driver")
    parser.add_argument("--home", help="Configurator state directory")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check installation and optional tools")
    check.add_argument("--json", action="store_true")
    config = commands.add_parser("configure", help="Select an already authenticated agent runtime")
    config.add_argument("--executor", choices=["codex", "goose"], required=True)
    config.add_argument("--command", dest="executable", required=True)
    config.add_argument("--argument", action="append", default=[])
    config.add_argument("--model")
    config.add_argument("--provider")
    config.add_argument("--reasoning-effort", help="Codex model reasoning setting")
    setup = commands.add_parser("setup", help="Generate Codex plugin or Goose recipe using this installation")
    setup.add_argument("--host", choices=["codex", "goose"], required=True)
    setup.add_argument("--output", required=True)
    service = commands.add_parser("service", help="Manage the independent background configurator")
    service.add_argument("action", choices=["start", "status", "stop"])
    commands.add_parser("benchmark", help="Run, score and compare benchmark profiles")
    run = commands.add_parser("run", help="Start a durable run from a JSON specification")
    run.add_argument("--spec", required=True)
    commands.add_parser("tools", help="List installed low-level developer tools")
    tool = commands.add_parser("tool", help="Call one developer tool with explicit arguments")
    tool.add_argument("name")
    tool.add_argument("--arguments", required=True, help="JSON file with tool arguments")
    reporting = commands.add_parser("report", help="Export stage and overall benchmark metrics")
    reporting.add_argument("run_id")
    reporting.add_argument("--output", help="JSON destination; also writes a Markdown sibling")
    for method in ("status", "events", "result", "cancel", "resume", "respond"):
        action = commands.add_parser(method, help=method.capitalize() + " a saved run")
        action.add_argument("run_id")
        if method == "events":
            action.add_argument("--after", type=int, default=0)
        if method == "respond":
            action.add_argument("--observation", required=True, help="JSON file containing observation and source")
        if method == "resume":
            action.add_argument("--budget-seconds",type=float,
                                help="Explicitly approved total wall-clock budget from the original start; may only increase")
            action.add_argument("--budget-reason",help="Required explanation for an explicit budget adjustment")
            approval = action.add_mutually_exclusive_group()
            approval.add_argument("--approve-emulator-tools", action="store_true",
                                  help="Explicitly approve only assigned TQ9 emulator tools for this run")
            approval.add_argument("--approve-bound-device-tools", action="store_true",
                                  help="Explicitly approve assigned tools for this run's selected device and effects")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        result = doctor(args.home)
        print(json.dumps(result, indent=2))
        return 0 if result["core"]["ok"] else 1
    if args.command == "configure":
        print(json.dumps(configure(args.executor, [args.executable, *args.argument],
                                   args.model, args.provider, args.home, args.reasoning_effort), indent=2))
        return 0
    if args.command == "setup":
        print(json.dumps(integration_assets(args.host, args.output, home=args.home), indent=2))
        return 0
    if args.command == "report":
        from .reporting import report_run
        print(json.dumps(report_run(args.run_id, home=args.home, output=args.output), indent=2))
        return 0
    if args.command in ("tools", "tool"):
        from .toolkit import call_tool, list_tools
        if args.command == "tools":
            print(json.dumps(list_tools(), indent=2))
            return 0
        result = call_tool(args.name, json.loads(Path(args.arguments).read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2, default=str))
        return int(result.get("_exit", 0)) or int(result.get("ok") is False)
    from .client import call
    if args.command == "service":
        method = "shutdown" if args.action == "stop" else "ping"
        result = call(method, home=args.home, autostart=args.action == "start")
    elif args.command == "run":
        result = call("start", json.loads(Path(args.spec).read_text(encoding="utf-8")), home=args.home)
    else:
        params = {"run_id": args.run_id}
        if args.command == "events":
            params["after"] = args.after
        if args.command == "respond":
            params["observation"] = json.loads(Path(args.observation).read_text(encoding="utf-8"))
        if args.command == "resume" and args.approve_emulator_tools:
            params["scoped_tool_approval"] = "emulator"
        if args.command == "resume" and args.approve_bound_device_tools:
            params["scoped_tool_approval"] = "bound-device"
        if args.command == "resume" and args.budget_seconds is not None:
            params["budget_seconds"] = args.budget_seconds
            params["budget_reason"] = args.budget_reason
        result = call(args.command, params, home=args.home)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
