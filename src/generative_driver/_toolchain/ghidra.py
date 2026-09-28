"""Headless Ghidra as a toolchain tool.

The implementation is bench/tools/workspace/ghidra_run.py, a standard-library file that an offline worker can
carry on its own; this module loads it by path and wraps it as `ghidra_run` so the call is an event in
the run log with the sha256 of the image, the exports and the analyzer's captured output. One
implementation serves both entry points on purpose: the offline trial of 2026-09-18 showed how much a
hand-typed analyzeHeadless invocation can go wrong (rejected loader selectors, `|| true` masking, exit
141 from preview pipelines), and two copies of the fix would drift.

`ok` is true only when analyzeHeadless exited 0, its log says "Import succeeded", and the exporter's
functions.tsv (at least one row, at least one decompiled), disassembly.txt (nonempty) and function_*.c
files verify on disk. The Log4j "Could not determine local host name" block is reported in
`hostname_diagnostic`; every other stderr line stays in `stderr_other`.
"""
import importlib.util
import os

from . import core

SKILL = os.path.join(core.BENCH, "toolchain", "skills", "interpret-firmware-binary")
IMPL_PATH = os.path.join(core.BENCH, "tools", "workspace", "ghidra_run.py")


def _load_impl():
    spec = importlib.util.spec_from_file_location("bench_tools_ghidra_run", IMPL_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


impl = _load_impl()
LOADER_SELECTORS = impl.LOADER_SELECTORS
MARKERS = impl.MARKERS


@core.tool("ghidra_run")
def ghidra_run(run_dir, workspace, image, base_address, processor, ghidra_path, java_home, export_dir,
               loader="BinaryLoader", prescripts=None, script_path=None, timeout_s=1800, noanalysis=False):
    """Import one raw image into a workspace-local Ghidra project, analyse it, export with
    ExportDecomp.java, and verify the exports. `script_path` None means the interpret-firmware-binary
    skill directory, which holds the exporter. Exit 0 only for the full verdict; 1 otherwise, with the
    reasons listed."""
    out = impl.run(workspace=workspace, image=image, base_address=base_address, processor=processor,
                   ghidra_path=ghidra_path, java_home=java_home, export_dir=export_dir, loader=loader,
                   prescripts=prescripts, script_path=script_path or SKILL, timeout_s=timeout_s,
                   noanalysis=noanalysis)
    files = {}
    exports = out.get("exports") or {}
    for path in (out.get("image"), exports.get("functions_tsv"), exports.get("disassembly_txt"),
                 out.get("stdout_path"), out.get("stderr_path"), out.get("log_path")):
        if path and os.path.isfile(path):
            files[path] = core.sha256(path)
    out["_files"] = files
    return out
