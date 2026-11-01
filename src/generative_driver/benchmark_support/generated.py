"""The generated family: grades the frozen driver of any generated device from its decrypted answer key, step by step.

An answer key (`generated-answer-key/1`) holds the device's emulator recipe, the memory cells of its state, the state a
verified reset leaves, the hidden sequences, the state the public mission's objective leaves, two reference drivers and
the mutants. Each hidden sequence starts from a reset that the evaluator verifies by reading the state, then calls the
mission's tasks one step at a time. A step passes when the driver's answer and the device state read afterwards both
match the reference's. The answer is a value, a refusal (the device's, or the driver's own before it sent the request),
no reply, or an error, which never matches (decision 19). A check the evaluator cannot decide, a cell it cannot read or
a sequence whose reset it could not verify, is undecided, never failed. Only the evaluator reads the state: workers see
no instrument of a generated device (decision 15).
"""
import re

from .capabilities import numeric_value

SCHEMA = 'generated-answer-key/1'
REQUIRED_MUTANTS = frozenset({'wrong-scale', 'constant-output', 'wrong-state'})
ANSWERS = ('value', 'refusal', 'no_reply')
_FIELDS = ('schema', 'nonce', 'recipe', 'state', 'reset', 'sequences', 'mission', 'references', 'mutants')
_STEP_FIELDS = ('task', 'inputs', 'grants', 'answer', 'state')
_GRANTS = ('read', 'write', 'actuate', 'reset')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
_HEX = re.compile(r'(?:[0-9a-f]{2})+')


def _require(condition, defect):
    if not condition:
        raise ValueError('Invalid generated answer key: ' + defect)


def _fields(value, required, optional=(), what='answer key'):
    _require(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional),
             'unknown or missing fields of the ' + what)
    return value


def _named(value):
    return isinstance(value, str) and _NAME.fullmatch(value) is not None


def _finite(value):
    try:
        numeric_value(value)
    except ValueError:
        return False
    return True


def _state(values, cells, what):
    _require(isinstance(values, dict) and set(values) == set(cells), what + ' names each state cell and no other')
    _require(all(type(values[cell]) is int and 0 <= values[cell] < 2 ** cells[cell]['width'] for cell in cells),
             what + ' holds an unsigned value of each cell\'s width')


def _step(step, cells, where):
    _fields(step, _STEP_FIELDS, ('outputs', 'refusal_rx_hex'), where)
    _require(_named(step['task']), where + ' names its task')
    _require(isinstance(step['inputs'], dict) and all(_named(name) and (type(value) is str or _finite(value))
                                                      for name, value in step['inputs'].items()),
             where + ' inputs are named numbers or strings')
    grants = step['grants']
    _require(isinstance(grants, list) and all(grant in _GRANTS for grant in grants) and len(set(grants)) == len(grants),
             where + ' grants are distinct effects')
    _require(step['answer'] in ANSWERS, where + ' answer is value, refusal or no_reply')
    if 'outputs' in step:
        _require(step['answer'] == 'value' and isinstance(step['outputs'], dict), where + ' outputs belong to a value')
        for name, output in step['outputs'].items():
            _fields(output, ('value', 'tolerance'), what=where + ' output')
            _require(_named(name) and _finite(output['value']) and _finite(output['tolerance'])
                     and output['tolerance'] >= 0, where + ' outputs are named values with a tolerance')
    if 'refusal_rx_hex' in step:
        _require(step['answer'] == 'refusal' and isinstance(step['refusal_rx_hex'], str)
                 and _HEX.fullmatch(step['refusal_rx_hex']) is not None, where + ' refusal_rx_hex is a refusal in hex')
    _state(step['state'], cells, where + ' state')


def _answer_key(truth):
    """`truth` once it is a well-formed answer key: every field of `generated-answer-key/1` and no other, beside the
    `artifact_sha256` the evaluator adds. ValueError names the first defect."""
    from .native import _validate_recipe
    _fields(truth, _FIELDS, ('artifact_sha256',))
    _require(truth['schema'] == SCHEMA, 'schema')
    _require(isinstance(truth['nonce'], str) and re.fullmatch(r'[0-9a-f]{64}', truth['nonce']) is not None,
             'the nonce is 64 lowercase hexadecimal digits')
    recipe = truth['recipe']
    _validate_recipe(recipe)
    _require(bool(recipe.get('reset_commands')) and bool(recipe.get('observations'))
             and recipe.get('backend', 'renode') == 'renode', 'the recipe resets and observes a Renode emulator')
    cells = truth['state']
    _require(isinstance(cells, dict) and bool(cells), 'the state names its cells')
    for name, cell in cells.items():
        _fields(cell, ('address', 'width'), what='state cell')
        _require(_named(name) and type(cell['address']) is int and 0 <= cell['address'] <= 0xffffffff
                 and type(cell['width']) is int and cell['width'] in (8, 16, 32), 'a state cell\'s address or width')
    _state(truth['reset'], cells, 'the reset')
    _state(_fields(truth['mission'], ('state',), what='mission')['state'], cells, 'the mission')
    sequences = truth['sequences']
    _require(isinstance(sequences, list) and bool(sequences), 'there are sequences')
    for sequence in sequences:
        _fields(sequence, ('id', 'steps'), what='sequence')
        _require(_named(sequence['id']) and isinstance(sequence['steps'], list) and bool(sequence['steps']),
                 'a sequence has an id and steps')
        for number, step in enumerate(sequence['steps'], 1):
            _step(step, cells, 'step %s/%d' % (sequence['id'], number))
    _require(len({sequence['id'] for sequence in sequences}) == len(sequences), 'sequence ids are distinct')
    references = truth['references']
    _require(isinstance(references, dict) and set(references) == {'a', 'b'}, 'the references are a and b')
    for reference in references.values():
        _driver(_fields(reference, ('model', 'capabilities'), what='reference'))
    mutants = truth['mutants']
    _require(isinstance(mutants, list), 'the mutants are a list')
    checks = {check['id'] for check in _checks(truth, 0)}
    for mutant in mutants:
        failed = _driver(_fields(mutant, ('id', 'model', 'capabilities', 'expected_failed_checks'), what='mutant'))[
            'expected_failed_checks']
        _require(_named(mutant['id']) and isinstance(failed, list) and bool(failed)
                 and all(isinstance(check, str) for check in failed) and len(set(failed)) == len(failed)
                 and set(failed) <= checks, 'a mutant expects distinct failures of the contract\'s checks')
    _require(len({mutant['id'] for mutant in mutants}) == len(mutants), 'mutant ids are distinct')
    return truth


def _driver(candidate):
    _require(isinstance(candidate['model'], dict) and isinstance(candidate['capabilities'], dict),
             'a driver is a model and its capabilities')
    return candidate


def _checks(key, revision):
    """The checks of the hidden sequences, steps numbered from 1. Units are not graded, so a record carries its check's
    unit: an output's value is in the unit the public mission states, and a cell holds an unsigned integer."""
    checks = []
    for sequence in key['sequences']:
        for number, step in enumerate(sequence['steps'], 1):
            prefix = '%s/%d/' % (sequence['id'], number)
            checks.append({'id': prefix + 'answer', 'revision': revision, 'kind': 'text', 'expected': step['answer'],
                           'unit': 'answer', 'channel': 'runtime-transcript'})
            checks.extend({'id': prefix + 'out:' + name, 'revision': revision, 'kind': 'number',
                           'expected': output['value'], 'absolute_tolerance': output['tolerance'],
                           'unit': 'mission-unit', 'channel': 'runtime-transcript'}
                          for name, output in step.get('outputs', {}).items())
            checks.extend({'id': prefix + 'state:' + cell, 'revision': revision, 'kind': 'number',
                           'expected': step['state'][cell], 'absolute_tolerance': 0, 'unit': 'cell-value',
                           'channel': 'device-state'} for cell in key['state'])
    return checks


def contract(pin, truth, phase):
    """The `benchmark-behavior/1` contract of the answer key in `truth` for its `artifact_sha256`. Each step has the
    text check `<sequence>/<step>/answer`, the number check `out:<name>` of each output of a value step, and
    `state:<cell>` of every state cell, with tolerance 0 on channel `device-state`. A generated device is graded in its
    final phase only. ValueError for a malformed answer key."""
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    if phase != 'final':
        raise ValueError('A generated device is graded in its final phase only')
    key = _answer_key(truth)
    return {'schema': 'benchmark-behavior/1', 'artifact_sha256': key.get('artifact_sha256'),
            'checks': _checks(key, pin.get('revision', 0))}


def classify_answer(result: dict, step: dict) -> str:
    """The driver's answer to `step`, from the result of its package call, as the runtime records it:

    - `value`: the call succeeded;
    - `refusal`: the driver declined before contact, with a fault of the model or the operator (the transport was never
      opened, or the transcript has no entry of the requested operation), or the device answered the requested
      operation with the bytes the step names as its refusal (`refusal_rx_hex`);
    - `no_reply`: the identity was verified and the requested operation's exchange timed out with nothing received;
    - `error`: anything else.

    A fault the package labels the host's is no evidence of trouble on the host, so it is an error: the evaluator's own
    health check alone tells the host's faults from the driver's (`execute`), and a result it found on a healthy device
    keeps the label as `reported_fault`."""
    if result.get('ok') is True:
        return 'value'
    error = result.get('error') if isinstance(result.get('error'), dict) else {}
    if error.get('reported_fault', error.get('fault')) not in ('model', 'operator'):
        return 'error'
    if result.get('transport_open_attempted') is False:
        return 'refusal'
    operation = result.get('operation')
    if not isinstance(operation, str):
        return 'error'
    transcript = result.get('transcript') if isinstance(result.get('transcript'), list) else []
    requested = [entry for entry in transcript if isinstance(entry, dict) and entry.get('operation') == operation]
    if not requested:
        return 'refusal'
    last = transcript[-1]
    if last is not requested[-1] or last.get('op') not in ('exchange', 'usb_control'):
        return 'error'
    refusal = step.get('refusal_rx_hex')
    if refusal and last.get('status') == 'completed' and last.get('rx_hex') == refusal:
        return 'refusal'
    if (result.get('identity_verified') is True and error.get('code') == 'timeout' and last.get('status') == 'attempted'
            and last.get('rx_hex') == ''):
        return 'no_reply'
    return 'error'


def execute(session, invoke, contract, truth, evidence_path, *, healthy_device_failures):
    """Run the answer key's hidden sequences on the evaluator's device and return one record per check of `contract`.

    Each sequence starts with `session.reset({})` and a read of the state. A state other than the key's `reset` leaves
    every record of the sequence undecided ('unverified reset') and its steps uncalled. Each step calls its task through
    `run_call` with its grants, classifies the answer and reads the state; a cell that cannot be read leaves its record
    undecided ('unreadable state'). Only these observations of the evaluator's leave a record undecided, never what the
    package returned. As `execute_plan` grades it, a call reported as a host fault is the driver's error only while
    grading a frozen package (`healthy_device_failures`) and only on a device that still runs, answers its monitor and
    accepts a connection; otherwise RuntimeError, and nothing is graded. A failed read is undecided only on such a
    device too. The raw evidence is written to `evidence_path` as the steps run."""
    from .behavior import _check_contract
    from .case_assets import write_json
    from .emulated_actions import _device_healthy, run_call
    key = _answer_key(truth)
    _check_contract(contract)
    checks = {check['id']: check for check in contract['checks']}
    events, records = [], []

    def save(**extra):
        write_json(evidence_path, {'schema': 'generated-step-evidence/1', 'events': events, 'records': records,
                                   **extra})

    def read():
        state = session.read_state(key['state'])
        if None in state.values() and not _device_healthy(session):
            raise RuntimeError('Host execution failed; the device state is unavailable')
        return state

    def record(identifier, value, undecided):
        check = checks[identifier]
        records.append({'id': identifier, 'revision': check['revision'], 'artifact_sha256': contract['artifact_sha256'],
                        'channel': check['channel'], 'unit': check['unit'], 'value': value,
                        **({'undecided': undecided} if undecided else {})})

    try:
        for sequence in key['sequences']:
            event = {'sequence': sequence['id'], 'reset': session.reset({})}
            events.append(event)
            event['state'] = read()
            verified = event['verified'] = event['state'] == key['reset']
            for number, step in enumerate(sequence['steps'], 1):
                answer, outputs, state = None, {}, {}
                if verified:
                    result = run_call(session, lambda task, inputs: invoke(task, inputs, step['grants']),
                                      {'operation': step['task'], 'parameters': step['inputs']})
                    event = {'sequence': sequence['id'], 'step': number, 'result': result}
                    events.append(event)
                    error = result.get('error') if isinstance(result.get('error'), dict) else {}
                    if error.get('fault') == 'host':
                        # As `execute_plan` grades it: a package process that could not start or was killed from outside
                        # is the host's, whatever the device.
                        if healthy_device_failures and error.get('source') != 'package-process':
                            event['device_healthy'] = _device_healthy(session)
                        if not event.get('device_healthy'):
                            raise RuntimeError('Host execution failed; behavioral grading is unavailable')
                        result = event['result'] = {**result, 'error': {**error, 'fault': 'model',
                                                                        'reported_fault': 'host',
                                                                        'classified': 'healthy-device'}}
                    answer = event['answer'] = classify_answer(result, step)
                    outputs = result.get('outputs') if isinstance(result.get('outputs'), dict) else {}
                    state = event['state'] = read()
                reason = None if verified else 'unverified reset'
                prefix = '%s/%d/' % (sequence['id'], number)
                record(prefix + 'answer', answer, reason)
                for name in step.get('outputs', {}):
                    record(prefix + 'out:' + name, outputs.get(name), reason)
                for cell in key['state']:
                    record(prefix + 'state:' + cell, state.get(cell),
                           reason or (None if state.get(cell) is not None else 'unreadable state'))
                save()
    except Exception:
        save(incomplete=True)
        raise
    return records


def observations(raw, canonical_task_result, inputs):
    """No instrument quantity: a generated device shows workers only its UART."""
    return {}


observations.monitor_units = {}


def reuse_passed(events, capabilities):
    """Not judged yet: plan 4 judges fresh reuse by reading the device's memory after it."""
    return None
