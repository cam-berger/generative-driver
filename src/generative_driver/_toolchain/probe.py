"""Stage 3. Execute the model on the real device and say what disagreed with it.

The probe is the first time the chain touches the device. It runs the model exactly as written; it never
repairs it. Anything that does not match goes back to the interpret stage as a defect list.
"""
import os

from . import core


@core.tool("probe_run")
def probe_run(run_dir, model_dir, n=3, operation=None, parameters=None, binding=None, allow_effects=None, replay=None,
              **_):
    """Run one operation of the model `n` times on the caller's binding, or on a replay trace, and record what came
    back.

    Exit 0 ok, 1 a failure the runtime attributes to the model, 4 an operator, host or transport fault, which makes
    the stage inconclusive rather than failed. Every refusal before the device is contacted says so with
    transport_open_attempted false and exit 1, and writes no record: model bytes the strict reader refuses, a format
    the runtime does not know, and everything `interface.probe_run` checks before it opens a transport."""
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    from . import interface
    try:
        if not os.path.isfile(os.path.join(md, "model.json")):
            raise ValueError("model_dir holds no model.json")
        loaded, _sha = interface.load(md)
        if not isinstance(loaded, dict):
            raise ValueError("model.json is not a JSON object")
    except (OSError, ValueError, TypeError, LookupError, RecursionError) as exc:
        return interface.before_contact(f"{type(exc).__name__}: {exc}")
    return interface.probe_run(run_dir, md, n=n, operation=operation, parameters=parameters,
        binding=binding, allow_effects=allow_effects, replay=replay)


@core.tool("probe_diff")
def probe_diff(model_dir, probe, **_):
    """What the device did that the model did not predict. Returns the defect list an interpret retry gets."""
    md = model_dir if os.path.isabs(model_dir) else os.path.join(core.BENCH, model_dir)
    pp = probe if os.path.isabs(probe) else os.path.join(core.BENCH, probe)
    from . import interface
    return interface.probe_diff(md, pp)

