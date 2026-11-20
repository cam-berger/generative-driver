"""The outcome reading: what one tool result says about how its dispatch ended.

A tool's result used to be read in about ten places, and the rule that decides whether a write stays uncertain was
written twice. This module reads a result once, from the tool's name and the result alone, for the verdict, the
dispatch record and the device recovery. It holds no database and no service. Why a result leaves an effect uncertain
is decided in docs/adr/0011-what-leaves-an-effect-uncertain.md."""
import builtins
from dataclasses import dataclass
import re
from typing import NamedTuple

# The trusted tools: only the installed runtime or the configurator writes their results, so only these may say that a
# command was never sent. A call read with `generated` is trusted too: the configurator runs the installed runtime in a
# child process over a checked copy of the package's data. So is a result read with `undispatched`, which the
# configurator wrote itself.
TRUSTED_TOOLS = {
    'interface_execute': 'the toolkit runs the installed runtime over the model',
    'probe_run': 'the toolkit runs the installed runtime over the model',
    'emit_test_live': 'the toolkit runs the installed runtime over a checked copy of the package; its result is a '
                      'summary that holds no execution record, so it is read as a whole',
}


class Execution(NamedTuple):
    """What one execution record says: whether it succeeded, whether the transport was opened, and whether the
    requested command may have been sent."""
    ok: bool
    opened: bool
    sent: bool


@dataclass(frozen=True)
class Reading:
    """What `read` makes of one result.

    `succeeded` is the one success test. `trusted` says the result is a trusted result, and `executions` holds one
    `Execution` per execution record of a trusted result, empty when it holds none or is not trusted.
    `leaves_uncertain` says a marked dispatch with this result stays uncertain, and `refused_before_open` is the value
    stored in `tool.finished`. `outcome` is `succeeded`, `refused`, `failed` or `inconclusive`, and `detail` names the
    fault of an inconclusive one. `answered` says a probe_run's record holds what the device did, and `host_fault` that
    the host, not the tool's input, failed. `pending_faults` are the faults of the result's records as a pending
    effect, and `model_failures` the records among them that are the model's. `other_faults` are the faults the result
    adds as another failed call."""
    succeeded: bool
    trusted: bool
    executions: tuple
    leaves_uncertain: bool
    refused_before_open: bool
    outcome: str
    detail: str | None
    answered: bool
    host_fault: bool
    pending_faults: tuple
    model_failures: tuple
    other_faults: tuple


def _succeeded(result):
    """The one success test: `_exit` 0 or absent and a truthy `ok`; an absent `ok` takes `available`, and an absent
    `available` counts as true."""
    return (isinstance(result, dict) and result.get('_exit', 0) == 0
            and bool(result.get('ok', result.get('available', True))))


def _records(result, generated):
    """The execution records a result holds: the result itself when it is one, or every item of a non-empty
    `results` list when each item is one; otherwise none. A `driver-result/1` counts only when the configurator itself
    ran the package's operation (`generated`)."""
    schemas = ('interface-result/1', 'driver-result/1') if generated else ('interface-result/1',)
    record = lambda item: isinstance(item, dict) and item.get('schema') in schemas
    if record(result):
        return [result]
    items = result.get('results') if isinstance(result, dict) else None
    return list(items) if isinstance(items, list) and items and all(record(item) for item in items) else []


def _sent(record):
    """Whether the requested command of one execution record may have been sent: yes, unless the record shows it was
    not. A record that lacks the field it would need counts as sent."""
    if record.get('transport_open_attempted') is False:
        return False
    if record['schema'] == 'driver-result/1':
        return record.get('requested_command_dispatched') is not False
    steps, operation = record.get('transcript'), record.get('operation')
    if not isinstance(steps, list) or not isinstance(operation, str):
        return True
    return any(isinstance(step, dict) and step.get('operation') == operation and step.get('op') != 'delay'
               for step in steps)


def _listed(results):
    return [item for item in results if isinstance(item, dict)] if isinstance(results, list) else []


def _toolchain_fault(result):
    """The exception type a toolchain error result names ('OSError' for 'OSError: ...'), or 'toolchain error'."""
    error = result.get('error')
    kind = error.split(':', 1)[0].strip() if isinstance(error, str) and ':' in error else ''
    return kind if re.fullmatch(r'[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Expired)', kind) else 'toolchain error'


def _host_fault(result):
    """Whether a tool result says the host, not its input, failed, by a positive list: the tool says so (fault host),
    or it is a toolchain error naming a host failure (the OSError family, ImportError, MemoryError or a subprocess
    timeout), or an error that names no exception type, which the toolkit returns when even its own record of the call
    failed. Any other error, such as an AttributeError or RecursionError on content a worker wrote, is no host fault."""
    if result.get('fault') == 'host':
        return True
    if not isinstance(result.get('error'), str):
        return False
    kind = _toolchain_fault(result)
    cls = getattr(builtins, kind, None)
    return (kind == 'toolchain error' or kind.rsplit('.', 1)[-1] == 'TimeoutExpired'
            or isinstance(cls, type) and issubclass(cls, (OSError, ImportError, MemoryError)))


def _before_contact(result):
    """Whether a result declares a stop before its first device call: its own `transport_open_attempted` is false and
    its `results` lists no entry."""
    return result.get('transport_open_attempted') is False and not _listed(result.get('results'))


def _outcome(result, succeeded):
    """How one live operation ended, for the verdict:

    - 'succeeded': it succeeded, or some of its operations passed on the device and every one that did not was
      refused before the device was contacted (a refused operation is not a live operation);
    - 'refused': refused before the device was contacted, so not a live operation. Either the runtime's preflight
      refused every operation (none attempted to open the transport), or the tool declared that it refused before its
      first device call (`_before_contact`, with no host fault: an argument, binding, grant, snapshot or integrity
      refusal);
    - 'failed': the model it ran failed on the device: the runtime attributes a failure to the model after the
      transport opened;
    - 'inconclusive': anything else that did not succeed, such as an operator, host or transport fault (exit 4), a
      host fault the tool declared before its first device call, or a toolchain error the tool did not declare as a
      refusal, which may have come after the device was contacted (labelled a host fault only when it names one,
      `_host_fault`). It says nothing about the driver.

    Returns (outcome, fault detail for an inconclusive one). Unlike `executions`, every object in a `results` list
    counts here, as the summary of emit_test_live does."""
    from interface_runtime.faults import classify_failure
    if succeeded:
        return 'succeeded', None
    executions = (_listed(result['results']) if isinstance(result.get('results'), list) else
                  [result] if result.get('schema') in ('interface-result/1', 'driver-result/1') else [])
    failed = [r for r in executions if r.get('ok') is not True]
    refused = bool(failed) and all(r.get('transport_open_attempted') is False for r in failed)
    if (_before_contact(result) and result.get('fault') != 'host') or (refused and len(failed) == len(executions)):
        return 'refused', None

    def fault(record):
        if record.get('schema') == 'interface-result/1':
            return classify_failure(record)
        error = record.get('error') if isinstance(record.get('error'), dict) else {}
        return error.get('fault', record.get('fault'))

    if any(fault(r) == 'model' and r.get('transport_open_attempted', True) is True for r in failed):
        return 'failed', None
    if refused and result.get('fault') is None:
        return 'succeeded', None
    if executions or 'fault' in result or result.get('_exit') == 4:
        first = failed[0] if failed else result
        error = first.get('error') if isinstance(first.get('error'), dict) else {}
        named = _toolchain_fault(first) if isinstance(first.get('error'), str) else 'toolchain error'
        code = first.get('error_code') or error.get('code') or (named if named != 'toolchain error' else None)
        return 'inconclusive', str((fault(first) if failed else result.get('fault')) or 'unclassified') + ' fault' + (
            ' (' + str(code) + ')' if code else '')
    # The tool raised without declaring a refusal: say whether that names a host failure, never guess one.
    return 'inconclusive', ('host fault' if _host_fault(result) else 'undeclared toolchain error') + (
        ' (' + _toolchain_fault(result) + ')')


def _answered(name, result, succeeded):
    """Whether a probe_run's record holds what the device did: a live run that succeeded, or a failure the runtime
    attributes to the model after every failed record opened the transport. The caller checks that the configurator
    supplied the connection."""
    if name != 'probe_run' or result.get('evidence_kind', 'live') != 'live':
        return False
    if succeeded:
        return True
    failed = [r for r in _listed(result.get('results')) if r.get('ok') is False]
    return (result.get('fault') == 'model' and bool(failed)
            and all(r.get('transport_open_attempted') is True for r in failed))


def _items(result):
    """What the device recovery attributes: the `results` of a result that has the key, else the result itself; None
    when that is not a non-empty list."""
    items = result.get('results') if isinstance(result, dict) and 'results' in result else [result]
    return items if isinstance(items, list) and items else None


def _pending_faults(result, generated):
    """The faults (`model`, `operator`, `host`, `unknown`) of the failed execution records of a pending effect, and
    the records that count as the model's: a failure the runtime attributes to the model after it verified identity,
    opened the transport and wrote a transcript. A record the recovery cannot attribute, and a result with no failed
    record, add `unknown`."""
    items = _items(result)
    if items is None:
        return ('unknown',), ()
    faults, failures, models = [], 0, []
    for item in items:
        written = (generated and item.get('schema') == 'driver-result/1'
                   and item.get('requested_command_dispatched') is True) if isinstance(item, dict) else False
        if (not isinstance(item, dict) or (item.get('schema') != 'interface-result/1' and not written)
                or type(item.get('ok')) is not bool):
            faults.append('unknown')
            continue
        if item['ok']:
            continue
        failures += 1
        fault = (item.get('error') if isinstance(item.get('error'), dict) else {}).get('fault')
        if fault in ('host', 'operator'):
            faults.append(fault)
        elif (fault == 'model' and item.get('identity_verified') is True
              and item.get('transport_open_attempted') is True and item.get('transcript')):
            faults.append('model')
            models.append(item)
        else:
            faults.append('unknown')
    if not failures:
        faults.append('unknown')
    return tuple(faults), tuple(models)


def _other_faults(result, generated):
    """The faults a failed call adds beside the pending effects: the host, operator and unknown faults it declares and
    those of its failed records that opened the transport. A call whose records all stopped before the open adds
    none."""
    if not isinstance(result, dict):
        return ('unknown',)
    items = _items(result)
    if items is None:
        return ('unknown',)
    if all(isinstance(item, dict) and item.get('transport_open_attempted') is False for item in items):
        return ()
    faults = []
    outer = result.get('fault') or (result.get('error') if isinstance(result.get('error'), dict) else {}).get('fault')
    if outer in ('host', 'operator'):
        faults.append(outer)
    elif outer not in (None, 'model'):
        faults.append('unknown')
    for item in items:
        if not isinstance(item, dict):
            faults.append('unknown')
            continue
        if item.get('ok') is True or item.get('transport_open_attempted') is False:
            continue
        fault = (item.get('error') if isinstance(item.get('error'), dict) else {}).get('fault')
        if fault in ('host', 'operator'):
            faults.append(fault)
        elif fault != 'model' or (item.get('schema') != 'interface-result/1' and not
                                  (item.get('schema') == 'driver-result/1' and generated)):
            faults.append('unknown')
    return tuple(faults)


def read(name, result, *, generated=False, undispatched=False):
    """How the dispatch of tool `name` ended, read from its `result` alone. `generated` says the configurator itself
    ran an operation of a package; `undispatched` says the configurator wrote `result` itself because the call never
    started. Anything that is not an object reads as a failed result with no execution record."""
    succeeded = _succeeded(result)
    trusted = bool(undispatched or generated or name in TRUSTED_TOOLS)
    whole = result if isinstance(result, dict) else {}
    executions = tuple(Execution(record.get('ok') is True, record.get('transport_open_attempted') is not False,
                                 _sent(record)) for record in _records(result, generated)) if trusted else ()
    refused_before_open = trusted and (all(not execution.opened for execution in executions) if executions else
                                       _before_contact(whole) and whole.get('fault') != 'host')
    if executions:
        # Each record on its own: one stays when it failed after the requested command may have been sent. Records
        # that all passed in a call that did not succeed stay too: something failed that no record shows.
        leaves_uncertain = (any(not execution.ok and execution.sent for execution in executions)
                            or (not succeeded and all(execution.ok for execution in executions)))
    else:
        leaves_uncertain = not (succeeded or trusted and _before_contact(whole))
    outcome, detail = _outcome(whole, succeeded)
    pending_faults, model_failures = _pending_faults(result, generated)
    return Reading(succeeded, trusted, executions, leaves_uncertain, refused_before_open, outcome, detail,
                   _answered(name, whole, succeeded), _host_fault(whole), pending_faults, model_failures,
                   _other_faults(result, generated))
