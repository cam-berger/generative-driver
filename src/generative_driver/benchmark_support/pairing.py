"""Two systems compared device by device on one scoring set.

Reporting code, outside the grader's identity: it reads saved suite reports and grades nothing. A device is a trial's
`case`, and a system's result on it is the outcome its row stores (`scored`), never one derived again here. A leak check
sets the after system's pass rate on the scoring set beside its rate on a fresh batch of devices: a clearly better score
on the scoring set means the set leaked."""
import math

from .outcomes import pass_rate

SCHEMA = 'benchmark-paired-comparison/1'
# Where a device that each system tried once, and graded, falls in the paired table: by its outcome before and after.
PAIRS = {('pass', 'pass'): 'both', ('failure', 'failure'): 'neither',
         ('pass', 'failure'): 'only_before', ('failure', 'pass'): 'only_after'}
# A scoring set that beats fresh devices with a probability below this is suspected of having leaked.
LEAK_ALPHA = 0.05


def mcnemar_exact(only_before: int, only_after: int) -> float:
    """The exact McNemar test of paired devices: the two-sided binomial probability, with each device equally likely to
    favour either system, of a split of the `only_before` and `only_after` devices (those one system passes and the
    other fails) at least this uneven. 1.0 when no device differs. A negative count raises ValueError."""
    if only_before < 0 or only_after < 0:
        raise ValueError('A device count is not negative')
    n = only_before + only_after
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(min(only_before, only_after) + 1)) / 2 ** n)


def leak_p(scoring_passes: int, scoring_n: int, fresh_passes: int, fresh_n: int) -> float:
    """The one-sided Fisher exact probability of a scoring-set pass count at least `scoring_passes` of its `scoring_n`
    graded trials when both sets pass at one rate, given the passes of the two together. A small probability means the
    scoring set does better than fresh devices. 1.0 when either set has no graded trial, as nothing then compares; a
    pass count outside zero to its graded trials raises ValueError."""
    for passes, n in ((scoring_passes, scoring_n), (fresh_passes, fresh_n)):
        if not 0 <= passes <= n:
            raise ValueError('A pass count lies between zero and the graded trials')
    if scoring_n == 0 or fresh_n == 0:
        return 1.0
    passes, total = scoring_passes + fresh_passes, scoring_n + fresh_n
    tail = sum(math.comb(passes, k) * math.comb(total - passes, scoring_n - k)
               for k in range(scoring_passes, min(scoring_n, passes) + 1))
    return tail / math.comb(total, scoring_n)


def _identity(report: dict) -> dict:
    return report['experiment']['comparison_identity']


def _shared(report: dict) -> dict:
    """What two systems must share to be compared, under the name a refusal gives it, in the order it lists them."""
    identity = _identity(report)
    effective = identity['budgets']['effective']
    return {'scoring_set': identity['scoring_set'], 'evaluator_revision': identity['evaluator_revision'],
            # A granted override gives a trial more than the child budget.
            'child_budget_seconds': (effective['child_budget_seconds'], effective['child_budget_overrides']),
            'max_model_repairs': identity['intervention_policy']['max_model_repairs'],
            'qualification': report['qualification']}


def _outcomes_by_device(report: dict) -> dict:
    """The stored outcome of every trial row of each device: a list, as a device may have been tried more than once."""
    devices = {}
    for row in report['trials']:
        devices.setdefault(row['case'], []).append(row.get('scored'))
    return devices


def _pair_devices(before: dict, after: dict) -> tuple[dict, list]:
    """The paired table, counting each device that both systems tried once and graded (passed or failed) by what each
    did with it, and the sorted names of every other device: absent from a report, tried more than once in either, or
    left ungraded by either."""
    first, second = _outcomes_by_device(before), _outcomes_by_device(after)
    devices, excluded = dict.fromkeys(PAIRS.values(), 0), []
    for device in sorted(first.keys() | second.keys()):
        outcomes = first.get(device, []), second.get(device, [])
        pair = (outcomes[0][0], outcomes[1][0]) if len(outcomes[0]) == len(outcomes[1]) == 1 else None
        if pair in PAIRS:
            devices[PAIRS[pair]] += 1
        else:
            excluded.append(device)
    return devices, excluded


def _changed_dimensions(before: dict, after: dict) -> list:
    """The sorted names of the protocol, if it differs, and of each dimension that differs between the systems."""
    left, right = before['experiment']['dimensions'], after['experiment']['dimensions']
    changed = {key for key in left.keys() | right.keys() if left.get(key) != right.get(key)}
    if _identity(before).get('protocol') != _identity(after).get('protocol'):
        changed.add('protocol')
    return sorted(changed)


def _leak_check(after: dict, scoring: dict, batch: dict) -> dict:
    """The after system's pass rate on its scoring set (`scoring`, its whole-report `pass_rate`) beside its rate on `batch`,
    its fresh batch, and the probability of a scoring-set result this much better by chance. ValueError unless `batch`
    is that fresh batch: the same protocol, dimensions and evaluator revision as `after`, and not the scoring set `after`
    ran."""
    left, right = _identity(after), _identity(batch)
    differing = [name for name, same in (
        ('protocol', left.get('protocol') == right.get('protocol')),
        ('dimensions', after['experiment']['dimensions'] == batch['experiment']['dimensions']),
        ('evaluator_revision', left['evaluator_revision'] == right['evaluator_revision'])) if not same]
    if differing:
        raise ValueError('The leak check differs from the after system in ' + ', '.join(differing))
    if right.get('scoring_set') == left['scoring_set']:
        raise ValueError('The leak check is not fresh: it carries the scoring set it checks')
    fresh = pass_rate([row.get('scored') for row in batch['trials']])
    p = leak_p(scoring['passes'], scoring['passes'] + scoring['failures'], fresh['passes'], fresh['passes'] + fresh['failures'])
    return {'scoring_rate': scoring['rate'], 'fresh_rate': fresh['rate'], 'p': p, 'suspected': p < LEAK_ALPHA}


def paired_comparison(before: dict, after: dict, *, leak_check: dict | None = None) -> dict:
    """Two systems' saved suite reports (`benchmark-suite-report/3`, as `_saved_report` reads them) of one scoring set,
    compared device by device: `benchmark-paired-comparison/1`, with

    - `compatible` and `incompatible_fields`: the `scoring_set`, `evaluator_revision`, `child_budget_seconds` (with its
      overrides), `max_model_repairs` and `qualification` the systems must share; those that differ are named, in that
      order. An incompatible result carries these and the schema alone: no device counts, pass rates or p-value, since
      none would compare like with like, and it leaves `leak_check` unexamined;
    - `changed_dimensions` and `comparison_kind`: the protocol and dimensions that differ, sorted, and 'repeat' for none,
      the one dimension, or 'combined-system', as `benchmark.compare` names them;
    - `devices`: counts of the devices both systems passed (`both`), both failed (`neither`), only the before system
      passed (`only_before`) and only the after system passed (`only_after`);
    - `excluded`: the sorted devices left out of those counts (see `_pair_devices`);
    - `pass_rate`: each report's whole-report `outcomes.pass_rate`, whatever its devices;
    - `mcnemar_p`: the exact McNemar test of the devices that differ;
    - `leak_check`: None, or what `_leak_check` gives for `leak_check`, the after system's fresh batch (a saved suite report).

    Raises ValueError for a report that carries no scoring set, a stored outcome that is no outcome, or a `leak_check`
    that is not the after system's fresh batch."""
    if any('scoring_set' not in _identity(report) for report in (before, after)):
        raise ValueError('Pairing needs two reports that carry a scoring set')
    shared = _shared(before), _shared(after)
    incompatible = [name for name in shared[0] if shared[0][name] != shared[1][name]]
    if incompatible:
        return {'schema': SCHEMA, 'compatible': False, 'incompatible_fields': incompatible}
    # Each system's whole report, whatever its devices: a name that is no outcome raises ValueError.
    rates = {'before': pass_rate([row.get('scored') for row in before['trials']]),
             'after': pass_rate([row.get('scored') for row in after['trials']])}
    devices, excluded = _pair_devices(before, after)
    changed = _changed_dimensions(before, after)
    return {'schema': SCHEMA, 'compatible': True, 'incompatible_fields': [], 'changed_dimensions': changed,
            'comparison_kind': 'combined-system' if len(changed) > 1 else changed[0] if changed else 'repeat',
            'devices': devices, 'excluded': excluded, 'pass_rate': rates,
            'mcnemar_p': mcnemar_exact(devices['only_before'], devices['only_after']),
            'leak_check': None if leak_check is None else _leak_check(after, rates['after'], leak_check)}
