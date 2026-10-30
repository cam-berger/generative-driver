"""The stage adapter of a registered case. This is system behavior, so the evaluator never imports it."""
from types import SimpleNamespace

from .registry import resolve_case


def _legacy_emulator():
    """Legacy tq9: stages observed without truth on the emulator the bench harness provides, as the v2 cases'."""
    from . import cases, emulated, legacy
    return SimpleNamespace(prepare_stage=cases.prepare_stage, observe_stage=cases.observe_stage,
                           package_call=emulated.package_call, record_call=emulated.record_call, score=legacy.score)


def _legacy_physical():
    """bme280: stages observed without truth on the operator's bound device, which the configurator reaches itself."""
    from . import physical, legacy
    return SimpleNamespace(prepare_stage=physical.prepare_stage, observe_stage=physical.observe_stage,
                           package_execute=physical.package_execute, score=legacy.score)


def _smoke():
    from . import legacy
    def unsupported(*args, **kwargs):
        raise ValueError('Setup smoke has no agent stages')
    return SimpleNamespace(prepare_stage=unsupported, observe_stage=unsupported,
                           package_execute=unsupported, score=legacy.score)


def _emulated():
    from . import emulated
    return emulated


_ADAPTERS = {'emulator-v2': _emulated, 'smoke': _smoke, 'legacy-emulator': _legacy_emulator,
             'legacy-physical': _legacy_physical}


def adapter_for(case_id: str):
    definition = resolve_case(case_id)
    loader = _ADAPTERS.get(definition.adapter_key)
    if loader is None:
        raise ValueError('Benchmark adapter is pending: ' + definition.adapter_key)
    return loader()
