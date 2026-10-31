"""Two systems compared device by device on one scoring set.

Reporting code, outside the grader's identity: it reads saved suite reports and grades nothing. A device is a trial's
`case`, and a system's result on it is the outcome its row stores (`scored`), never one derived again here. A leak check
sets the after system's pass rate on the scoring set beside its rate on a fresh batch of devices: a clearly better
score on the scoring set means the set leaked. A comparison is an output of a sealed scoring set, so it carries
aggregates only: no case name, run id or trial key appears in it, nor in the reason of a refusal."""
import json
import math

from .outcomes import pass_rate

SCHEMA = 'benchmark-paired-comparison/1'
# The shared conditions compared for each case the systems both ran, not as a whole.
PER_CASE = ('entry_pins', 'time_policy')
# Where a device that each system tried once, and graded, falls in the paired table: by its outcome before and after.
PAIRS = {('pass', 'pass'): 'both', ('failure', 'failure'): 'neither',
         ('pass', 'failure'): 'only_before', ('failure', 'pass'): 'only_after'}
# Why a device is left out of the paired table, in the order the reasons are tried.
EXCLUSIONS = ('repeated', 'absent', 'ungraded')
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


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _per_case(identity: dict, key: str) -> dict:
    """For each case, the sorted canonical JSON forms of the `key` (`entry_pins` or `time_policy`) of every entry that
    names it. A case may have several entries, and the order of a suite's entries is its own."""
    cases = {}
    for entry, item in zip(identity['manifest']['entries'], identity[key]):
        cases.setdefault(entry['case'], []).append(_canonical(item))
    return {case: sorted(forms) for case, forms in cases.items()}


def _shared(report: dict) -> dict:
    """What two systems must share to be compared, under the name a refusal gives it, in the order it lists them."""
    identity = _identity(report)
    effective, policy = identity['budgets']['effective'], identity['intervention_policy']
    return {'scoring_set': identity.get('scoring_set'), 'evaluator_revision': identity['evaluator_revision'],
            'execution': identity['execution'], 'evidence_track': identity['evidence_track'],
            'scope': identity['scope'], 'entry_pins': _per_case(identity, 'entry_pins'),
            'time_policy': _per_case(identity, 'time_policy'),
            # A granted override gives a trial more than the child budget.
            'child_budget_seconds': (effective['child_budget_seconds'], effective['child_budget_overrides']),
            'max_model_repairs': policy['max_model_repairs'], 'scoped_tool_approval': policy['scoped_tool_approval'],
            'qualification': report['qualification']}


def _same(name: str, first, second) -> bool:
    """Whether two systems agree on a shared condition. The pins and time policies are judged for each case that both
    ran and for no other, so a device only one system ran is left to the exclusions."""
    if name in PER_CASE:
        return all(first[case] == second[case] for case in first.keys() & second.keys())
    return first == second


def _outcomes_by_device(report: dict) -> dict:
    """The stored outcome of every trial row of each device: a list, as a device may have been tried more than once."""
    devices = {}
    for row in report['trials']:
        devices.setdefault(row['case'], []).append(row.get('scored'))
    return devices


def _pair_devices(before: dict, after: dict) -> tuple[dict, dict]:
    """The paired table, counting each device that both systems tried once and graded (passed or failed) by what each
    did with it, and the count of every other device under the first reason that applies: `repeated` (tried more than
    once in either report), `absent` (in only one) or `ungraded` (not passed or failed in at least one). Counts only: a
    scoring set's devices are never named."""
    first, second = _outcomes_by_device(before), _outcomes_by_device(after)
    devices, excluded = dict.fromkeys(PAIRS.values(), 0), dict.fromkeys(EXCLUSIONS, 0)
    for device in first.keys() | second.keys():
        outcomes = first.get(device, []), second.get(device, [])
        if len(outcomes[0]) > 1 or len(outcomes[1]) > 1:
            excluded['repeated'] += 1
        elif not outcomes[0] or not outcomes[1]:
            excluded['absent'] += 1
        elif (outcomes[0][0], outcomes[1][0]) in PAIRS:
            devices[PAIRS[(outcomes[0][0], outcomes[1][0])]] += 1
        else:
            excluded['ungraded'] += 1
    return devices, excluded


def _changed_dimensions(before: dict, after: dict) -> list:
    """The sorted names of the protocol, if it differs, and of each dimension that differs between the systems."""
    left, right = before['experiment']['dimensions'], after['experiment']['dimensions']
    changed = {key for key in left.keys() | right.keys() if left.get(key) != right.get(key)}
    if _identity(before).get('protocol') != _identity(after).get('protocol'):
        changed.add('protocol')
    return sorted(changed)


def _leak_check(after: dict, scoring: dict, batch: dict) -> dict:
    """The after system's pass rate on its scoring set (`scoring`, its whole-report `pass_rate`) beside its rate on
    `batch`, its fresh batch, and the probability of a scoring-set result this much better by chance. ValueError unless
    `batch` is that fresh batch, which in order:
    - stores outcomes (a benchmark-suite-report/3; the refusal names the schema of one that does not);
    - matches `after` in its protocol, dimensions and every condition pairing holds fixed, but the scoring set and the
      pins and time policies of its own devices (the refusal names the conditions that differ);
    - does not carry a scoring set with the manifest hash of the one `after` ran, whatever its registration;
    - shares no device with `after`: no entry pin with a `pin_sha256` that `after` has.
    A refusal names the condition and never a case."""
    if any('scored' not in row for row in batch['trials']):
        raise ValueError('The leak check is a ' + str(batch.get('schema')) + ', which stores no scored outcome')
    left, right = _shared(after), _shared(batch)
    differing = [name for name, same in (
        ('protocol', _identity(after).get('protocol') == _identity(batch).get('protocol')),
        ('dimensions', after['experiment']['dimensions'] == batch['experiment']['dimensions'])) if not same]
    differing += [name for name in left if name not in ('scoring_set', *PER_CASE) and left[name] != right[name]]
    if differing:
        raise ValueError('The leak check differs from the after system in ' + ', '.join(differing))
    theirs = right['scoring_set']
    if isinstance(theirs, dict) and theirs.get('manifest_sha256') == left['scoring_set']['manifest_sha256']:
        raise ValueError('The leak check is not fresh: it carries the scoring set it checks')
    ran = {pin['pin_sha256'] for pin in _identity(after)['entry_pins']}
    if any(pin['pin_sha256'] in ran for pin in _identity(batch)['entry_pins']):
        raise ValueError('The leak check is not fresh: one of its devices is one the after system ran')
    fresh = pass_rate([row.get('scored') for row in batch['trials']])
    p = leak_p(scoring['passes'], scoring['passes'] + scoring['failures'],
               fresh['passes'], fresh['passes'] + fresh['failures'])
    return {'scoring_rate': scoring['rate'], 'fresh_rate': fresh['rate'], 'p': p, 'suspected': p < LEAK_ALPHA}


def paired_comparison(before: dict, after: dict, *, leak_check: dict | None = None) -> dict:
    """Two systems' saved suite reports (`benchmark-suite-report/3`, as `_saved_report` reads them) of one scoring set,
    compared device by device: `benchmark-paired-comparison/1`, with

    - `compatible` and `incompatible_fields`: what the systems must share, those that differ named in this order:
      `scoring_set`, `evaluator_revision`, `execution`, `evidence_track`, `scope`, `entry_pins`, `time_policy`,
      `child_budget_seconds` (with its overrides), `max_model_repairs`, `scoped_tool_approval` and `qualification`.
      The pins and time policies are compared per case, for each case both reports ran: the sorted canonical JSON
      forms of its entries' must be equal. An incompatible result carries these and the schema alone: no device
      counts, pass rates or p-value, since none would compare like with like, and it leaves `leak_check` unexamined;
    - `changed_dimensions` and `comparison_kind`: the protocol and dimensions that differ, sorted, and 'repeat' for
      none, the one dimension, or 'combined-system', as `benchmark.compare` names them;
    - `devices`: counts of the devices both systems passed (`both`), both failed (`neither`), only the before system
      passed (`only_before`) and only the after system passed (`only_after`);
    - `excluded`: counts of the devices left out of those, by the first reason that applies: `repeated`, `absent` or
      `ungraded` (see `_pair_devices`);
    - `pass_rate`: each report's whole-report `outcomes.pass_rate`, whatever its devices;
    - `mcnemar_p`: the exact McNemar test of the devices that differ;
    - `leak_check`: None, or what `_leak_check` gives for `leak_check`, the after system's fresh batch (a saved suite
      report).

    Raises ValueError for a report that carries no scoring set, a stored outcome that is no outcome, or a `leak_check`
    that is not the after system's fresh batch. No case name, run id or trial key appears in a result or a reason."""
    if any('scoring_set' not in _identity(report) for report in (before, after)):
        raise ValueError('Pairing needs two reports that carry a scoring set')
    shared = _shared(before), _shared(after)
    incompatible = [name for name in shared[0] if not _same(name, shared[0][name], shared[1][name])]
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
