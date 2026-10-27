"""Evaluator-owned corpus qualification without starting an owner or any process."""
import copy


def check_corpus(plan, options=None):
    """Authenticate selected ready cases; public availability alone is not admission.

    Password handles are used only for registry admission in this operator process.
    The result contains public inventory and verdicts, never the handles or errors
    from reading them. Runtime configuration, tools and device access are outside
    this qualification check; the bench harness checks them when a run starts.
    """
    from .corpus import validate_plan
    from .registry import require_calibration
    from .suites import preflight_selection

    plan = validate_plan(plan)
    if options is not None and not isinstance(options, dict):
        raise ValueError('Corpus check options must be an object')
    passwords = (options or {}).get('evaluator_password_files')
    if passwords is None:
        passwords = {}
    if not isinstance(passwords, dict) or any(
            not isinstance(case, str) or not case.strip()
            or not isinstance(path, str) or not path.strip() for case, path in passwords.items()):
        raise ValueError('Evaluator password files must map case IDs to nonblank paths')
    choices = ({row['lineage']: row for row in plan['selection']}
               if plan['schema'] == 'benchmark-corpus-plan/2' else
               {row['id']: {'selected': True, 'reason': 'Selected by the legacy plan.'}
                for row in plan['catalog']['lineages']})
    cases = []
    for row, available in zip(plan['catalog']['lineages'], plan['availability']):
        choice = choices[row['id']]
        cases.append({'lineage': row['id'], 'case': row['case'], 'selected': choice['selected'],
                      'selection_reason': choice['reason'], 'availability_status': available['status'],
                      'status': available['status'] if choice['selected'] else 'excluded',
                      'blockers': copy.deepcopy(available['blockers']), 'reason': None})
    failures = []

    def fail(case, code, reason, status='unqualified'):
        case.update(status=status, reason=reason)
        failures.append({'lineage': case['lineage'], 'case': case['case'], 'code': code, 'reason': reason})

    ready = [(case, available['case_pin']) for case, available in zip(cases, plan['availability'])
             if case['selected'] and case['availability_status'] == 'ready']
    if not ready:
        failures.append({'lineage': None, 'case': None, 'code': 'no-selected-ready-cases',
                         'reason': 'The frozen selection has no ready cases to authenticate.'})
    else:
        try:
            _, _, current_pins = preflight_selection(plan['runnable_suite'])
        except (OSError, ValueError):
            for case, _ in ready:
                fail(case, 'selection-unavailable',
                     'The selected suite no longer passes public readiness checks; freeze a new corpus plan.')
        else:
            context_reason = None
            try:
                validate_plan(plan, current=True)
            except (OSError, ValueError):
                context_reason = 'The complete frozen corpus context changed; freeze a new corpus plan.'
                if all(frozen == current for (_, frozen), current in zip(ready, current_pins)):
                    failures.append({'lineage': None, 'case': None, 'code': 'plan-context-changed',
                                     'reason': context_reason})
            for (case, frozen_pin), current_pin in zip(ready, current_pins):
                if current_pin != frozen_pin:
                    fail(case, 'case-pin-changed',
                         'The selected case pin changed; freeze a new corpus plan.')
                    continue
                if context_reason is not None:
                    case.update(status='unqualified', reason=context_reason)
                    continue
                password = passwords.get(case['case'])
                if password is None:
                    fail(case, 'missing-credential', 'No evaluator password file was supplied for this case.',
                         status='missing-credential')
                    continue
                try:
                    admitted = require_calibration(frozen_pin, {'evaluator_password_file': password})
                except (OSError, ValueError):
                    fail(case, 'qualification-failed',
                         'Authenticated qualification failed: the credential or reference and mutant evidence '
                         'could not be validated.')
                    continue
                if admitted.get('ok') is not True:
                    fail(case, 'qualification-failed', 'Authenticated qualification did not pass.')
                    continue
                case['status'] = 'qualified'
                if 'evidence_sha256' in admitted:
                    case['evidence_sha256'] = admitted['evidence_sha256']
    return {'schema': 'benchmark-corpus-check/1', 'plan_sha256': plan['plan_sha256'],
            'catalog_sha256': plan['catalog_sha256'], 'model_benchmark': False,
            'ok': bool(ready) and not failures, 'cases': cases, 'failures': failures,
            'qualification': 'Authenticated corpus qualification only; no owner, worker, native process, '
                             'runtime configuration, tool availability or device operation was checked.'}
