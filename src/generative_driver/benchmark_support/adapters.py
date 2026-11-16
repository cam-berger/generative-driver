"""The adapter of a registered case: the firmware and the mission the benchmark gives the tool's stage package for the
case, and the regrade of its saved reports. The stages themselves run on the tool's stage package (Feature 05 of the
untangle Run, docs/features/untangle/05-supplied-device-stages.md). This is system behavior, so the evaluator never
imports it."""
from types import SimpleNamespace

from .registrations import registration_for


def _smoke():
    from . import legacy
    def unsupported(*args, **kwargs):
        raise ValueError('Setup smoke has no agent stages')
    return SimpleNamespace(supplied_inputs=unsupported, score=legacy.score)


def _supplied_inputs(case_id, run_dir, options=None):
    """The firmware and the mission of the emulated case `case_id`, as the tool's stage package takes them. `firmware`
    holds the `path` of the image file and the `sha256` the case pins for it; a run with an execution snapshot
    (`run_dir`) reads its checked inputs folder. `mission` holds the `tasks`, the `reuse_objective` and, where the
    case's mission file has them, the `operating_conditions`, and so not the case's name. `ValueError` for options
    that name a scenario other than `original`."""
    from .case_assets import case_inputs
    from .execution import artifact_name
    from .missions import load_mission
    if (options or {}).get('scenario_id') not in (None, 'original'):
        raise ValueError('Unknown case scenario')
    inputs, manifest = case_inputs(case_id, run_dir)
    name = artifact_name(manifest)
    mission = load_mission(case_id, run_dir)
    return {'firmware': {'path': str(inputs / name), 'sha256': manifest['images'][name]},
            'mission': {key: mission[key] for key in ('tasks', 'reuse_objective', 'operating_conditions')
                        if key in mission}}


def _regrade(report, password=None, *, evidence_path=None):
    """Offline regrade requires an explicit evaluator sidecar; never guesses paths."""
    from .evidence import regrade_v2
    return regrade_v2(report, evidence_path, password)


_EMULATED = SimpleNamespace(supplied_inputs=_supplied_inputs, score=_regrade)
_ADAPTERS = {'emulator-v2': lambda: _EMULATED, 'smoke': _smoke}


def adapter_for(case_id: str):
    """The adapter of `case_id`, by the `adapter_key` of its registration: it offers `supplied_inputs` and `score`.
    Finding it opens no file: a worker changing one must not stop the run (Feature 02, unlocked keys), and a generated
    case has none (Feature 03, generated trials). A run's snapshot check and each start catch a changed case."""
    adapter_key = registration_for(case_id).adapter_key
    loader = _ADAPTERS.get(adapter_key)
    if loader is None:
        raise ValueError('Benchmark adapter is pending: ' + adapter_key)
    return loader()
