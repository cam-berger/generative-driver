"""The family seam: the one way the grader closure reaches a family's code, so that the grading core names no case or
family and editing one family's code moves only the identities of the cases that use it (Feature 01, family-seam).

A family module declares a module-level `HOOKS`, family hook name to value; a family hook is one thing the core asks of a
family. `family_for` loads the module a case's registration names and `family_from` checks what it declares and fills
what it omits. `docs/benchmark-case-authoring.md` holds the table of hooks. This is the only module of the closure that
loads code by name: `grader_identity` seeds each case's module itself, because the walk does not follow such a load.
"""
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable

from .registrations import CaseRegistration, registration_for

_GRADED = 'emulator-v2'
_REQUIRED = ('contract', 'observations', 'reuse_passed', 'required_mutants')
_HOOKS = frozenset({*_REQUIRED, 'execute', 'build_plan', 'hydrate_truth', 'admission_valid', 'validate_phase',
                    'required_scenarios', 'mutant_checks_may_exceed_expected', 'input_commitments', 'inventory_files',
                    'instrument_units', 'public_observation', 'development_conditions', 'start_session',
                    'validate_execution'})
_INVENTORY_COMMITMENTS = frozenset({'source', 'contract', 'recipe', 'references', 'mutations', 'build', 'inventory'})


@dataclass(frozen=True)
class Family:
    """One case's family hooks, every one filled. Reading `required_mutants` raises for a case that declares none."""
    contract: Callable
    observations: Callable
    reuse_passed: Callable
    _required_mutants: Callable[[], frozenset]
    execute: Callable
    build_plan: Callable
    hydrate_truth: Callable
    admission_valid: Callable
    validate_phase: Callable
    required_scenarios: frozenset
    mutant_checks_may_exceed_expected: bool
    input_commitments: frozenset
    inventory_files: frozenset
    instrument_units: Callable
    public_observation: Callable
    development_conditions: Callable
    start_session: Callable
    validate_execution: Callable

    @property
    def required_mutants(self) -> frozenset:
        return self._required_mutants()


def _refusal(registration, hook):
    """A required hook of a case that does not declare it: calling it raises ValueError naming the case."""
    def refuse(*_args, **_kwargs):
        if registration.native_module is None:
            raise ValueError('Unknown native family: ' + registration.id)
        raise ValueError('Case declares no ' + hook + ' hook: ' + registration.id)
    return refuse


def _renode(*, recipe, **arguments):
    from .execution import start_renode
    return start_renode(recipe=recipe, **arguments)


def _check(registration, hooks):
    case_id = registration.id
    for hook in hooks:
        if hook not in _HOOKS:
            raise ValueError('Family of case ' + case_id + ' declares an unknown hook: ' + str(hook))
    if registration.adapter_key != _GRADED:
        return
    for hook in _REQUIRED:
        if hook not in hooks:
            raise ValueError('Graded case ' + case_id + ' lacks a required hook: ' + hook)
    if ('execute' in hooks) == ('build_plan' in hooks):
        raise ValueError('Graded case ' + case_id + ' must declare exactly one hook of: execute, build_plan')


def family_from(registration: CaseRegistration, hooks: dict[str, Any]) -> Family:
    """The `Family` of `registration` from the `hooks` its module declares, each omitted hook filled with its default.
    A hook required of a graded case (one whose `adapter_key` is `emulator-v2`) defaults to a refusal, which only a
    case that is not graded can omit. ValueError naming the case and the hook when a hook name is unknown, a graded case
    lacks a required hook, or it declares both `execute` and `build_plan`, or neither."""
    _check(registration, hooks)

    def declared(hook):
        return hooks[hook] if hook in hooks else _refusal(registration, hook)

    observations = declared('observations')

    def default_units():
        return observations() if 'observations' not in hooks else dict(observations.monitor_units)

    units = hooks.get('instrument_units', default_units)

    def default_projection(raw):
        named = units()
        observed = observations(raw, {}, {})
        return {'values': {key: observed[key] for key in named}, 'units': named}

    build_plan = declared('build_plan')

    def run_plan(session, invoke, pin, contract, truth, evidence_path, *, phase, healthy_device_failures):
        from .emulated_actions import execute_plan
        return execute_plan(session, invoke, build_plan(pin, truth, phase), contract, evidence_path, observations,
                            truth['contracts']['time_policy']['sample_settle_seconds'],
                            healthy_device_failures=healthy_device_failures)

    def refuse_native_execution(manifest, truth):
        if manifest.get('execution_setup', {}).get('method') == 'native-sim':
            raise ValueError('Family validates no native-sim execution of case: ' + registration.id)

    mutants = hooks['required_mutants'] if 'required_mutants' in hooks else None
    return Family(
        contract=declared('contract'), observations=observations, reuse_passed=declared('reuse_passed'),
        _required_mutants=_refusal(registration, 'required_mutants') if mutants is None else (lambda: mutants),
        execute=hooks.get('execute', run_plan), build_plan=build_plan,
        hydrate_truth=hooks.get('hydrate_truth', lambda truth: truth),
        admission_valid=hooks.get('admission_valid', lambda pin, truth, record: False),
        validate_phase=hooks.get('validate_phase', lambda phase: None),
        required_scenarios=hooks.get('required_scenarios', frozenset()),
        mutant_checks_may_exceed_expected=hooks.get('mutant_checks_may_exceed_expected', False),
        input_commitments=hooks.get('input_commitments', _INVENTORY_COMMITMENTS),
        inventory_files=hooks.get('inventory_files', frozenset()),
        instrument_units=units, public_observation=hooks.get('public_observation', default_projection),
        development_conditions=hooks.get('development_conditions', lambda recipe: None),
        start_session=hooks.get('start_session', _renode),
        validate_execution=hooks.get('validate_execution', refuse_native_execution))


def family_for(case_id: str) -> Family:
    """The `Family` of a registered case: the hooks its row's `native_module` declares, or every default when the row
    names none. ValueError for an unregistered case, or one whose module declares a malformed family."""
    registration = registration_for(case_id)
    module = registration.native_module
    return family_from(registration, import_module('.' + module, __package__).HOOKS if module else {})
