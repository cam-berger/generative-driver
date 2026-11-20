"""The client interface, version 4: what a client that brings its own device, firmware and mission needs in code.

Such a client asks five things of the service: start a run on a device it supplies, replace that device, post one
final verdict, read the run's records and its frozen driver, and stop or seal a run. This module names the calls it
may make and the names it may import from the tool, holds the rules of the records it sends, and finds the frozen
driver among a run's handoffs. The service applies these rules itself, so a record without a fault here is not
refused there for its form. `docs/client-interface.md` says the same in plain words, and names the records on disk a
client may read.
"""
import math
import re
from pathlib import Path
from types import MappingProxyType

# The faults of a device descriptor, by the function the service itself applies to a supplied device.
from .device_client import descriptor_errors as device_errors

__all__ = ['CALLS', 'IMPORTS', 'PROTOCOL', 'VERDICT_FIELDS', 'VERSION', 'device_errors', 'firmware_errors',
           'frozen_driver', 'mission_errors', 'verdict_errors']

# The title of `docs/client-interface.md` carries this number.
VERSION = 4
# Runs record their protocol; a run saved without this label used the earlier gated stage progression.
PROTOCOL = 'assistive/1'
# The service knows other calls too; these are the ones a client of this interface sends.
CALLS = ('ping', 'start', 'resume', 'status', 'events', 'result', 'attach_device', 'record_evaluation', 'cancel',
         'seal')
# Every name of the tool a client may import, by module, as the document's `What a client imports` lists them. A
# listed module may also be imported for where it is installed, which is all `interface_runtime` is listed for.
IMPORTS = MappingProxyType({
    'generative_driver.client_interface': ('CALLS', 'IMPORTS', 'PROTOCOL', 'VERDICT_FIELDS', 'VERSION',
                                           'device_errors', 'firmware_errors', 'frozen_driver', 'mission_errors',
                                           'verdict_errors'),
    'generative_driver.client': ('call', 'default_home'),
    'generative_driver.integrity': ('digest',),
    'generative_driver.setup': ('SUPPORTED_EXECUTORS', 'resolve_executor'),
    'generative_driver.replay': ('candidate_config',),
    'generative_driver.toolkit': ('call_tool', 'resources_root'),
    'interface_runtime.engine': ('validate_model',),
    'interface_runtime': (),
})
# The verdict record a client posts after a run: check ids with passed flags (null for a check the evaluator could
# not decide) and failing and undecided ids with a reason. Expected values, tolerances, witnesses and raw records stay
# sealed, so no other field is accepted.
VERDICT_FIELDS = frozenset({'schema', 'status', 'equivalent', 'basis', 'checks', 'diff', 'passed', 'total', 'mission',
                            'frozen_artifact_sha256', 'evidence_sha256', 'protocol'})
_SHA256 = '[0-9a-f]{64}'
_TASK_NAME = '[A-Za-z][A-Za-z0-9_]{0,63}'
_INPUT_TYPES = ('integer', 'number', 'string', 'bytes')
_SAMPLING = ('acquire', 'retained')


def verdict_errors(record):
    """The faults of the verdict record `record` as a list of sentences, empty for a well-formed `equivalence/2`
    record. The service refuses a posted verdict that has one."""
    sha = lambda value: value is None or isinstance(value, str) and re.fullmatch(_SHA256, value) is not None
    text = lambda value: isinstance(value, str) and 0 < len(value) <= 300
    if not isinstance(record, dict) or set(record) != VERDICT_FIELDS:
        return ['an equivalence/2 record has exactly the fields ' + ', '.join(sorted(VERDICT_FIELDS))]
    errors = []
    if (record['schema'], record['basis'], record['protocol']) != ('equivalence/2', 'evaluator', PROTOCOL):
        errors.append('schema equivalence/2, basis evaluator and protocol ' + PROTOCOL + ' are required')
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
    if not all(sha(record[key]) for key in ('frozen_artifact_sha256', 'evidence_sha256')):
        errors.append('hashes are sha256 hex or null')
    elif status != 'unscored' and record['evidence_sha256'] is None:
        errors.append('a grade names the evidence it was sealed in')
    return errors


def firmware_errors(firmware):
    """The faults of the supplied `firmware` as a list of sentences, empty for an object with exactly `path`, an
    absolute path as text, and `sha256`, 64 lowercase hex characters. Whether a file lies at `path`, and whether its
    bytes hash to `sha256`, is for the service to say when the run starts."""
    if not isinstance(firmware, dict) or set(firmware) != {'path', 'sha256'}:
        return ['a firmware has exactly path and sha256']
    errors = []
    if not isinstance(firmware['path'], str) or not Path(firmware['path']).is_absolute():
        errors.append('path is an absolute path')
    if not isinstance(firmware['sha256'], str) or not re.fullmatch(_SHA256, firmware['sha256']):
        errors.append('sha256 is 64 lowercase hex characters')
    return errors


def _text(value):
    """Whether `value` is text that is not blank."""
    return isinstance(value, str) and bool(value.strip())


def _output_ok(output):
    """Whether one output of a task is well formed: a unit that is null or text, and with an `instrument` the
    tolerance the tool holds the device's reading of that name to."""
    if not isinstance(output, dict) or 'unit' not in output:
        return False
    if output['unit'] is not None and (not isinstance(output['unit'], str) or not output['unit']):
        return False
    if 'instrument' not in output:
        return True
    tolerance = output.get('absolute_tolerance')
    return (isinstance(output['instrument'], str) and bool(output['instrument'])
            and type(tolerance) in (int, float) and math.isfinite(tolerance) and tolerance >= 0)


def _task_ok(name, task):
    """Whether the task `name` of a mission is well formed."""
    if not isinstance(name, str) or not re.fullmatch(_TASK_NAME, name) or not isinstance(task, dict):
        return False
    if not {'meaning', 'inputs', 'outputs'} <= set(task) <= {'meaning', 'inputs', 'outputs', 'sampling'}:
        return False
    if not _text(task['meaning']) or not isinstance(task['inputs'], dict) or not isinstance(task['outputs'], dict):
        return False
    if 'sampling' in task and task['sampling'] not in _SAMPLING:
        return False
    return (all(isinstance(value, dict) and value.get('type') in _INPUT_TYPES for value in task['inputs'].values())
            and all(_output_ok(output) for output in task['outputs'].values()))


def mission_errors(mission):
    """The faults of the supplied `mission` as a list of sentences, empty for a well-formed one. A mission has
    `tasks` and `reuse_objective`, and may have `operating_conditions`. A task that breaks a rule gives one fault,
    with its name. The service refuses a `start` by this function."""
    if (not isinstance(mission, dict) or not {'tasks', 'reuse_objective'} <= set(mission)
            or not set(mission) <= {'tasks', 'reuse_objective', 'operating_conditions'}):
        return ['a mission has tasks and reuse_objective, and may have operating_conditions']
    errors = []
    if not _text(mission['reuse_objective']):
        errors.append('reuse_objective is text that is not blank')
    if 'operating_conditions' in mission:
        conditions = mission['operating_conditions']
        if (not isinstance(conditions, list) or not 1 <= len(conditions) <= 16
                or any(not _text(condition) or len(condition) > 1000 for condition in conditions)):
            errors.append('operating_conditions are 1 to 16 texts that are not blank, of at most 1000 characters each')
    tasks = mission['tasks']
    if not isinstance(tasks, dict) or not tasks:
        errors.append('tasks is an object with at least one task')
    else:
        errors += ['invalid task: ' + str(name) for name, task in tasks.items() if not _task_ok(name, task)]
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
