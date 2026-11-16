"""The stage adapter of a registered case. This is system behavior, so the evaluator never imports it."""
from types import SimpleNamespace

from .registrations import registration_for


def _smoke():
    from . import legacy
    def unsupported(*args, **kwargs):
        raise ValueError('Setup smoke has no agent stages')
    return SimpleNamespace(prepare_stage=unsupported, observe_stage=unsupported,
                           package_execute=unsupported, score=legacy.score)


def _emulated():
    from . import emulated
    return emulated


_ADAPTERS = {'emulator-v2': _emulated, 'smoke': _smoke}


def adapter_for(case_id: str):
    """The adapter module of `case_id`, by the `adapter_key` of its registration. It runs on every stage and package
    call, so it opens no file: a worker changing one must not stop the run (Feature 02, unlocked keys), and a generated
    case has none (Feature 03, generated trials). A run's snapshot check and each start catch a changed case."""
    adapter_key = registration_for(case_id).adapter_key
    loader = _ADAPTERS.get(adapter_key)
    if loader is None:
        raise ValueError('Benchmark adapter is pending: ' + adapter_key)
    return loader()
