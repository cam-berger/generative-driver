"""Code-owned case identities and their existing execution adapters.

The registrations are code-owned data: `resources/registrations/cases.json`, installed beside this module and read
relative to it, never through `case_root()`, which an environment variable can redirect. Register a case there after
implementing its adapter and public resources. Catalogs, manifests and encrypted truth describe cases; they never select
imports. Keep known identities there when removing a case from a selection catalog, so saved pins and reports retain
their meaning.
"""
import json
from dataclasses import dataclass, fields
from importlib import import_module
from pathlib import Path
from types import MappingProxyType, ModuleType


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


def cases_file(package: Path) -> Path:
    """The registration table of the generative_driver package directory `package`."""
    return Path(package) / 'resources' / 'registrations' / 'cases.json'


def _unique_keys(pairs):
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('Duplicate key in case registrations')
    return dict(pairs)


def read_cases(path: Path) -> tuple[CaseRegistration, ...]:
    """The registrations of the table at `path`, `{"schema": "benchmark-case-registrations/1", "cases": [...]}`, whose
    entries each name every `CaseRegistration` field. ValueError for a duplicate key, an unknown or missing field, or a
    duplicate id."""
    table = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_unique_keys)
    if (not isinstance(table, dict) or set(table) != {'schema', 'cases'}
            or table['schema'] != 'benchmark-case-registrations/1' or not isinstance(table['cases'], list)):
        raise ValueError('Unsupported case registrations: ' + str(path))
    names = {field.name for field in fields(CaseRegistration)}
    if any(not isinstance(entry, dict) or set(entry) != names for entry in table['cases']):
        raise ValueError('Unknown or missing case registration fields')
    cases = tuple(CaseRegistration(**entry) for entry in table['cases'])
    if len({case.id for case in cases}) != len(cases):
        raise ValueError('Duplicate case registration id')
    return cases


_CASES = read_cases(cases_file(Path(__file__).resolve().parents[1]))
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


def family_module(family: str) -> ModuleType:
    """Load only the one native module that the registered cases of `family` name: tq9-v2 and tq9-v3 both name tq9_v2,
    and the legacy tq9 names none. The shared grading core reaches a family here, by name, instead of importing it.
    ValueError when the cases name no native module or several."""
    names = sorted({case.native_module for case in _CASES if case.family == family and case.native_module})
    if not names:
        raise ValueError('No native module registered for family: ' + str(family))
    if len(names) > 1:
        raise ValueError('Several native modules registered for family ' + str(family) + ': ' + ', '.join(names))
    return import_module('.' + names[0], __package__)
