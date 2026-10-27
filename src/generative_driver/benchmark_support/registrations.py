"""Code-owned case identities and their existing execution adapters.

Add executable support here after implementing its adapter and public resources.
Catalogs, manifests and encrypted truth describe cases; they never select imports.
Keep known identities here when removing a case from a selection catalog, so saved
pins and reports retain their meaning.
"""
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType


@dataclass(frozen=True)
class CaseRegistration:
    id: str
    family: str
    version: str
    adapter_key: str
    schema: str | None
    native_module: str | None = None
    mission_case: str | None = None
    truth_schema: str | None = None
    build_kind: str | None = None
    calibration_kind: str | None = None


_CASES = (
    CaseRegistration('setup-smoke', 'setup-smoke', '1', 'smoke', None),
    CaseRegistration('tq9', 'tq9', '2', 'legacy-emulator', 'benchmark-case/1', mission_case='tq9-v2'),
    CaseRegistration('bme280', 'bme280', '3', 'legacy-physical', 'benchmark-case/1'),
    CaseRegistration('tq9-v2', 'tq9', '5', 'emulator-v2', 'benchmark-case/2',
                     native_module='tq9_v2', calibration_kind='reference'),
    CaseRegistration('sampled-sensor-v1', 'sampled-sensor', '5', 'emulator-v2', 'benchmark-case/2',
                     native_module='sampled_sensor', truth_schema='benchmark-family-truth/1',
                     calibration_kind='family'),
    CaseRegistration('parameter-store-v1', 'parameter-store', '5', 'emulator-v2', 'benchmark-case/2',
                     native_module='parameter_store', truth_schema='benchmark-family-truth/1',
                     calibration_kind='family'),
    CaseRegistration('zephyr-modbus-v1', 'zephyr-modbus', '1', 'emulator-v2', 'benchmark-case/2',
                     native_module='zephyr_modbus', truth_schema='benchmark-upstream-truth/1',
                     build_kind='zephyr-modbus', calibration_kind='upstream'),
)
_REGISTRATIONS = MappingProxyType({case.id: case for case in _CASES})


def registration_for(case_id: str) -> CaseRegistration:
    if not isinstance(case_id, str) or case_id not in _REGISTRATIONS:
        raise ValueError('Unknown benchmark case: ' + str(case_id))
    return _REGISTRATIONS[case_id]


def registered_case_ids() -> tuple[str, ...]:
    return tuple(_REGISTRATIONS)


def build_case_ids() -> tuple[str, ...]:
    """Cases with an upstream rebuild command, without loading native adapters."""
    return tuple(case.id for case in _CASES if case.build_kind is not None)


def native_family_for(case_id: str):
    """Load only this case's trusted family, independently of unused families."""
    registration = registration_for(case_id)
    if registration.native_module is None:
        raise ValueError('Unknown native family: ' + case_id)
    return import_module('.' + registration.native_module, __package__)
