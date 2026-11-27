"""Stage 5. Turn the model and the probe into a package a stranger can use, by generation rather than by
writing: every file of the package is rendered from the model and the probe output, so nothing here can repair a
model.

These are the stage's three tools as a client calls them. Each hands its call to `interface.py`, which builds, checks
and runs the interface package; `emit_check` adds the rule for which failures are the host's. A package is a folder of
data that holds no code. The register package this module once rendered, with a conversion module a worker wrote, is
deleted (docs/adr/0010-a-package-is-data-in-one-format.md).
"""
from . import core


@core.tool("emit_package")
def emit_package(run_dir, model_dir, probe, name=None, version="1.0.0", **_):
    """Write the package of a model and the probe record that model wrote: five files of data and no code."""
    from . import interface
    return interface.emit_package(run_dir, model_dir, probe, name=name, version=version)


# What only the host can make fail. The check reads the package's and the model's bytes inside its own handlers and
# refuses what it cannot read, so an OSError or ImportError that reaches emit_check came from the host's side of the
# check. Running out of memory (MemoryError) rejects the package rather than stopping the run for the host.
HOST_FAULTS = (OSError, ImportError)


@core.tool("emit_check")
def emit_check(package_dir, model_dir=None, expected_manifest_sha256=None, **_):
    """Does the package do what its model says, and nothing else?

    The package's files are checked against its manifest, `README.md` and `mcp.json` against what the tool renders
    from the package's driver and evidence, and its recorded exchanges are replayed in this process on the installed
    runtime. No ground truth and no device knowledge.

    A package or argument the check refuses is a failed check (exit 1), whatever the package bytes made fail,
    including bytes it cannot read or parse or memory exhaustion (MemoryError). Only a failure outside the package
    bytes is the host's: a file failure around the check (its temporary folder, reading back its own copy) or an
    import failure. That result is fault host with exit 4: it says nothing about the package.
    """
    from . import interface
    try:
        return interface.emit_check(package_dir, model_dir, expected_manifest_sha256)
    except HOST_FAULTS as exc:
        return {"ok": False, "fault": "host", "error": f"{type(exc).__name__}: {exc}", "replay_status": "not_run",
                "_exit": 4}
    except MemoryError as exc:
        return {"ok": False, "integrity_ok": False, "replay_status": "not_run",
                "error": f"{type(exc).__name__}: {exc}", "_exit": 1}


@core.tool("emit_test_live")
def emit_test_live(package_dir, run_dir=None, binding=None, operations=None, parameters=None, allow_effects=None,
                   model_dir=None, expected_manifest_sha256=None, **_):
    """Run operations of the package on the live device.

    Each of `operations` (default the model's identity operation) runs once in a child process on the installed
    runtime over the package's data, with the caller's `binding`, the `parameters` given for it and the
    `allow_effects` grants; `manifest_sha256` names the package's own manifest. Exit 4 means a transport fault,
    inconclusive."""
    from . import interface
    return interface.emit_test_live(run_dir, package_dir, binding, operations=operations, parameters=parameters,
                                    allow_effects=allow_effects, model_dir=model_dir,
                                    expected_manifest_sha256=expected_manifest_sha256)
