"""Installed, client-independent access to the reusable driver compiler tools.

The configurator owns assignments and hardware access. This facade owns portable
resource lookup and deterministic tool dispatch. It never launches an agent.
"""
from __future__ import annotations

import copy
import json
import importlib
import inspect
from pathlib import Path


_MODULE_TOOLS = {
    "acquire": ("acquire_firmware_artifact", "acquire_flash", "acquire_datasheet",
                "acquire_selfdescription", "acquire_protocol_spec", "acquire_classify",
                "acquire_bus_scan", "acquire_register_sweep"),
    "bench": ("bus_prepare",),
    "firmware": ("firmware_prepare", "firmware_collect", "firmware_validate"),
    "interpret": ("interpret_run", "interpret_collect", "model_validate", "model_provenance_audit"),
    "probe": ("probe_run", "probe_diff", "probe_observe"),
    "emit": ("emit_package", "emit_check", "emit_test_live"),
    "interface": ("interface_describe", "interface_execute", "model_qualify"),
    "usage": ("usage_record", "usage_summary"),
    "imagemap": ("image_map_check",),
    "ghidra": ("ghidra_run",),
}
_DISPATCH = {name: module for module, names in _MODULE_TOOLS.items() for name in names}
_PATH_ARGUMENTS = {"run_dir", "source_path", "artifact", "model_dir", "probe", "probe_json", "package_dir", "image",
                   "workspace", "workspace_root", "ghidra_path", "java_home", "export_dir", "script_path",
                   "wiring", "transcript", "replies", "bus", "replay"}


def resources_root() -> Path:
    """Installed generic assets, never a location for run outputs."""
    return Path(__file__).resolve().parent / "resources"


def list_tools() -> list[dict]:
    """Return MCP-compatible contracts without loading optional dependencies."""
    manifest = json.loads((resources_root() / "toolchain/tools.json").read_text(encoding="utf-8"))
    result = []
    for item in manifest["tools"]:
        if item["name"] not in _DISPATCH:
            continue
        schema = copy.deepcopy(item["inputSchema"])
        schema.setdefault("properties", {})["run_dir"] = {
            "type": "string", "description": "Absolute directory for this run's outputs and evidence."
        }
        required = schema.setdefault("required", [])
        if "run_dir" not in required:
            required.append("run_dir")
        if item["name"] == "interpret_run":
            schema["properties"]["mode"] = {"type": "string", "enum": ["prepare"], "default": "prepare"}
        result.append({"name": item["name"], "description": item["description"], "inputSchema": schema})
    return result


def call_tool(name: str, arguments: dict) -> dict:
    """Call one generic tool, recording evidence outside installed resources.

    Paths supplied by clients are absolute; this makes calls independent of the
    UI's working directory and prevents accidental writes into the installation.
    Agent execution is owned by the configurator, so interpretation only prepares
    a handoff here.
    """
    try:
        if name not in _DISPATCH:
            raise ValueError(f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        arguments = dict(arguments)
        if name in ("emit_package", "probe_diff") and "probe_json" in arguments:
            if "probe" in arguments and arguments["probe"] != arguments["probe_json"]:
                raise ValueError("probe and probe_json must name the same evidence")
            arguments["probe"] = arguments.pop("probe_json")
        run = arguments.get("run_dir")
        if not isinstance(run, str) or not Path(run).is_absolute():
            raise ValueError("run_dir must be an absolute path")
        if Path(run).resolve().is_relative_to(Path(__file__).resolve().parent):
            raise ValueError("run_dir must be outside installed resources")
        for key in _PATH_ARGUMENTS & arguments.keys():
            value = arguments[key]
            if value is not None and (not isinstance(value, str) or not Path(value).is_absolute()):
                raise ValueError(f"{key} must be an absolute path")
        if "inputs" in arguments:
            inputs = arguments["inputs"]
            if not isinstance(inputs, dict) or any(not isinstance(value, str) or not Path(value).is_absolute()
                                                   for value in inputs.values()):
                raise ValueError("inputs must map workspace names to absolute paths")
        if name == "interpret_run":
            if arguments.get("mode", "prepare") != "prepare":
                raise ValueError("The configurator launches agents; interpret_run supports mode='prepare' only")
            arguments["mode"] = "prepare"
        module = importlib.import_module(f"generative_driver._toolchain.{_DISPATCH[name]}")
        function = getattr(module, name)
        signature = inspect.signature(function)
        if "run_dir" not in signature.parameters and not any(
                parameter.kind == parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            arguments.pop("run_dir")
        result = function(**arguments)
        result.setdefault("_exit", 0)
        return result
    except (ValueError, TypeError, OSError, ImportError) as exc:
        return {"ok": False, "_exit": 1, "error": str(exc)}
