"""The client interface, version 1: what a client that brings its own device and mission needs in code.

Such a client asks four things of the service: start a run on a device it supplies, replace that device, post one
final verdict, and read the run's records and its frozen driver. This module names the calls it may make, holds the
rules of the two records it sends, and finds the frozen driver among a run's handoffs. The service applies these
rules itself, so a record without a fault here is not refused there for its form. `docs/client-interface.md` says
the same in plain words (Feature 03 of the untangle Run, docs/features/untangle/03-client-interface.md).
"""
import re

# The faults of a device descriptor, by the function the service itself applies to a supplied device.
from .device_client import descriptor_errors as device_errors

__all__ = ['CALLS', 'PROTOCOL', 'VERDICT_FIELDS', 'VERSION', 'device_errors', 'frozen_driver', 'verdict_errors']

# The title of `docs/client-interface.md` carries this number.
VERSION = 1
# Runs record their protocol; a run saved without this label used the earlier gated stage progression.
PROTOCOL = 'assistive/1'
# The service knows other calls too; these are the ones a client of this interface sends.
CALLS = ('ping', 'start', 'resume', 'status', 'events', 'result', 'attach_device', 'record_evaluation')
# The verdict record a client posts after a run: check ids with passed flags (null for a check the evaluator could
# not decide) and failing and undecided ids with a reason. Expected values, tolerances, witnesses and raw records stay
# sealed, so no other field is accepted.
VERDICT_FIELDS = frozenset({'schema', 'status', 'equivalent', 'basis', 'method', 'checks', 'diff', 'passed', 'total',
                            'mission', 'frozen_artifact_sha256', 'capabilities_sha256', 'evidence_sha256', 'protocol'})
# How the evaluator grades a frozen driver: on a fresh device of its own, or from the fresh reuse the run recorded.
_METHODS = ('fresh-final', 'recorded-reuse')


def verdict_errors(record):
    """The faults of the verdict record `record` as a list of sentences, empty for a well-formed `equivalence/1`
    record. The service refuses a posted verdict that has one."""
    sha = lambda value: value is None or isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
    text = lambda value: isinstance(value, str) and 0 < len(value) <= 300
    if not isinstance(record, dict) or set(record) != VERDICT_FIELDS:
        return ['an equivalence/1 record has exactly the fields ' + ', '.join(sorted(VERDICT_FIELDS))]
    errors = []
    if (record['schema'], record['basis'], record['protocol']) != ('equivalence/1', 'evaluator', PROTOCOL) or \
            record['method'] not in _METHODS:
        errors.append('schema equivalence/1, basis evaluator, method ' + ' or '.join(_METHODS) + ' and protocol ' + PROTOCOL
                      + ' are required')
    checks, diff = record['checks'], record['diff']
    status, equivalent = record['status'], record['equivalent']
    # A check's passed flag is true or false, or null for a check the evaluator could not decide. An unknown grade holds
    # such checks beside passed ones, a driver graded not equivalent beside a failed one, and no other record holds one.
    null_ok = status == 'unknown' or status == 'graded' and equivalent is False and isinstance(checks, list) and any(
        isinstance(c, dict) and c.get('passed') is False for c in checks)
    passed_flag = lambda value: type(value) is bool or value is None and null_ok
    if not isinstance(checks, list) or any(not isinstance(c, dict) or set(c) != {'id', 'passed'} or not text(c['id'])
                                           or not passed_flag(c['passed']) for c in checks):
        errors.append('checks are [{id, passed}], with passed null only for an undecided check in an unknown grade or '
                      'beside a failed one')
        checks = []
    if not isinstance(diff, list) or any(not isinstance(d, dict) or set(d) != {'id', 'reason'} or not text(d['id'])
                                         or not text(d['reason']) for d in diff):
        errors.append('diff is [{id, reason}]')
        diff = []
    if any(type(record[key]) is not int for key in ('passed', 'total')) or \
            (record['passed'], record['total']) != (sum(c['passed'] is True for c in checks), len(checks)):
        errors.append('passed and total count the checks')
    undecided, named = {c['id'] for c in checks if c['passed'] is None}, {d['id'] for d in diff}
    if (status, type(equivalent)) not in (('graded', bool), ('no-driver', bool), ('unknown', type(None)),
                                          ('unscored', type(None))) or status == 'no-driver' and equivalent is not False:
        errors.append('status graded or no-driver with equivalent true or false, or unscored or unknown with equivalent null')
    elif equivalent is True and (not checks or not all(c['passed'] for c in checks) or diff):
        errors.append('an equivalent driver passed every check, with no difference')
    elif equivalent is not True and not diff:
        errors.append('a driver that is not judged equivalent names its differences')
    elif status == 'unknown' and (not undecided or any(c['passed'] is False for c in checks) or not undecided <= named):
        errors.append('an unknown grade has no failed check, at least one undecided check and a difference for each')
    elif status == 'graded' and not undecided <= named:
        errors.append('a graded driver has a difference for each undecided check')
    mission = record['mission']
    if mission is not None and (not isinstance(mission, dict) or not set(mission) <= {'name', 'passed', 'reason', 'fault'}
                                or type(mission.get('passed')) not in (bool, type(None))
                                or any(not isinstance(mission[k], str) or len(mission[k]) > 300 for k in set(mission) - {'passed'})):
        errors.append('mission is {name, passed, reason, fault}')
    if not all(sha(record[key]) for key in ('frozen_artifact_sha256', 'capabilities_sha256', 'evidence_sha256')):
        errors.append('hashes are sha256 hex or null')
    elif status != 'unscored' and record['evidence_sha256'] is None:
        errors.append('a grade names the evidence it was sealed in')
    return errors


def frozen_driver(handoffs):
    """The frozen driver among a run's accepted `handoffs`, as its `path` and its `sha256`, or `None` when the run
    froze none: the first artifact of the latest `emit` handoff that has an artifact. `handoffs` are in the order the
    `result` call gives them. A posted verdict names this driver's `sha256` in `frozen_artifact_sha256`."""
    for handoff in reversed(handoffs):
        if handoff['stage'] == 'emit' and handoff['artifacts']:
            artifact = handoff['artifacts'][0]
            return {'path': artifact['path'], 'sha256': artifact['sha256']}
    return None
