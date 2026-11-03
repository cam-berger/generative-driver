"""The generated family: grades the frozen driver of any generated device from its decrypted answer key, step by step.

An answer key (`generated-answer-key/1`) holds the device's emulator recipe, the memory cells of its state, the state a
verified reset leaves, the hidden sequences, the state the public mission's objective leaves, two reference drivers and
the mutants. Each hidden sequence starts from a reset that the evaluator verifies by reading the state, then calls the
mission's tasks one step at a time. A step passes when the driver's answer and the device state read afterwards both
match the reference's. The answer is a value, a refusal (the device's, or the driver's own decision not to send the
request), no reply, or an error, which never matches (decision 19). A check the evaluator cannot decide, a cell it
cannot read or a sequence whose reset it could not verify, is undecided, never failed. Only the evaluator reads the
state: workers see no instrument of a generated device (decision 15).
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
            _fields(output, ('value', 'tolerance', 'unit'), what=where + ' output')
            _require(_named(name) and _finite(output['value']) and _finite(output['tolerance'])
                     and output['tolerance'] >= 0 and isinstance(output['unit'], str) and bool(output['unit']),
                     where + ' outputs are named values with a tolerance and a unit')
    # A driver that forwards the request on a refusal step receives the device's refusal, which reads as a refusal only
    # against the step's record of it.
    _require(('refusal_rx_hex' in step) == (step['answer'] == 'refusal'),
             where + ' has refusal_rx_hex exactly when its answer is a refusal')
    if 'refusal_rx_hex' in step:
        _require(isinstance(step['refusal_rx_hex'], str) and _HEX.fullmatch(step['refusal_rx_hex']) is not None,
                 where + ' refusal_rx_hex is a refusal in hex')
    _state(step['state'], cells, where + ' state')


def answer_key(truth):
    """`truth` once it is a well-formed answer key: every field of `generated-answer-key/1` and no other, beside the
    `artifact_sha256` the evaluator adds. ValueError names the first defect.

    - `nonce`: 64 lowercase hexadecimal digits, random, so that the key's digest in the plaintext set manifest cannot be
      confirmed by guessing the key.
    - `recipe`: a `NativeSession` recipe for Renode with reset commands and an observation, which the evaluator's health
      check reads. The evaluator reads the reset state as soon as the reset commands return, with the emulator paused,
      so they must leave the firmware booted to idle: Renode keeps RAM across `machine Reset` and the firmware's startup
      code initializes it, so they end, for example, with `emulation RunFor` over a boot interval.
    - `state`: {cell: {'address', 'width' 8, 16 or 32}}. `reset`, each step's `state` and `mission.state` give every
      cell an unsigned value of its width.
    - `sequences`: [{'id', 'steps'}]. A step is {'task', 'inputs', 'grants', 'answer' ('value', 'refusal' or
      'no_reply'), 'state'}, with `outputs` ({name: {'value', 'tolerance', 'unit'}}, the unit the public mission states
      and '1' for a dimensionless output) only on a value step, and `refusal_rx_hex` (the device's refusal in lowercase
      hex) on every refusal step and no other, so that a driver that forwards the request and receives the device's
      refusal answers a refusal. A timed-out exchange records none of the bytes it received, so a partial reply
      followed by silence reads as no reply: a `no_reply` step cannot tell the two apart.
    - `references` a and b, and `mutants` [{'id', 'model', 'capabilities', 'expected_failed_checks'}], whose expected
      failures are checks of the key's own contract."""
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
    """The checks of the hidden sequences, steps numbered from 1. An output's check expects the unit the public mission
    states for it, which the driver must report; the answer and the state cells are the evaluator's own observations,
    in the units `answer` and `cell-value`."""
    checks = []
    for sequence in key['sequences']:
        for number, step in enumerate(sequence['steps'], 1):
            prefix = '%s/%d/' % (sequence['id'], number)
            checks.append({'id': prefix + 'answer', 'revision': revision, 'kind': 'text', 'expected': step['answer'],
                           'unit': 'answer', 'channel': 'runtime-transcript'})
            checks.extend({'id': prefix + 'out:' + name, 'revision': revision, 'kind': 'number',
                           'expected': output['value'], 'absolute_tolerance': output['tolerance'],
                           'unit': output['unit'], 'channel': 'runtime-transcript'}
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
    key = answer_key(truth)
    return {'schema': 'benchmark-behavior/1', 'artifact_sha256': key.get('artifact_sha256'),
            'checks': _checks(key, pin.get('revision', 0))}


def _contact(transcript):
    """Whether a transcript may show bytes that reached the device: any entry but a delay, and anything but a list of
    entries. An absent transcript shows none."""
    if transcript is None:
        return False
    return not isinstance(transcript, list) or any(not isinstance(entry, dict) or entry.get('op') != 'delay'
                                                   for entry in transcript)


def _reads(result):
    """The results of the precondition reads the driver made before the requested operation, or None when its
    `observations` are not a list of {'result': object}."""
    observations = result.get('observations', [])
    if not isinstance(observations, list) or any(not isinstance(observation, dict)
                                                 or not isinstance(observation.get('result'), dict)
                                                 for observation in observations):
        return None
    return [observation['result'] for observation in observations]


def classify_answer(result: dict, step: dict) -> str:
    """The driver's answer to `step`, from the result of its package call as the runtime returns it:

    - `value`: the call succeeded.
    - `refusal`: the driver declined on purpose, or the device refused. The driver declined when, with a fault of the
      model or the operator, nothing reached the device: `transport_open_attempted` is false and no transcript, its own
      or one of its precondition reads', shows contact (a capability mapping that cannot bind the input is such a
      refusal). It also declined when it read the device's state and a precondition failed (`precondition_failed`
      after reads that all succeeded with their identity verified). The device refused when, after a verified
      identity, it answered the requested operation with the bytes the step names as its refusal (`refusal_rx_hex`).
    - `no_reply`: after a verified identity, the requested operation's exchange timed out with nothing received.
    - `error`: anything else, a failed identity verification and a precondition the driver could not check among them.

    A fault the package labels the host's is no evidence of trouble on the host, so it is an error: the evaluator's own
    health check alone tells the host's faults from the driver's (`emulated_actions.graded_result`), and a result it
    found on a healthy device keeps the label as `reported_fault`. A package process that could not start (`spawn`) is
    the host's whatever the device: `graded_result` raises before such a result reaches this classification."""
    if result.get('ok') is True:
        return 'value'
    error = result.get('error') if isinstance(result.get('error'), dict) else {}
    if error.get('reported_fault', error.get('fault')) not in ('model', 'operator'):
        return 'error'
    transcript, reads = result.get('transcript'), _reads(result)
    if result.get('transport_open_attempted') is False:
        contacted = reads is None or _contact(transcript) or any(_contact(read.get('transcript')) for read in reads)
        return 'error' if contacted else 'refusal'
    if error.get('code') == 'precondition_failed':
        checked = bool(reads) and all(read.get('ok') is True and read.get('identity_verified') is True
                                      for read in reads)
        return 'refusal' if checked and not _contact(transcript) else 'error'
    operation = result.get('operation')
    if (result.get('identity_verified') is not True or not isinstance(operation, str)
            or not isinstance(transcript, list)):
        return 'error'
    requested = [entry for entry in transcript if isinstance(entry, dict) and entry.get('operation') == operation]
    if (not requested or transcript[-1] is not requested[-1]
            or requested[-1].get('op') not in ('exchange', 'usb_control')):
        return 'error'
    last, refusal = requested[-1], step.get('refusal_rx_hex')
    if refusal and last.get('status') == 'completed' and last.get('rx_hex') == refusal:
        return 'refusal'
    if error.get('code') == 'timeout' and last.get('status') == 'attempted' and last.get('rx_hex') == '':
        return 'no_reply'
    return 'error'


def execute(session, invoke, contract, truth, evidence_path, *, healthy_device_failures):
    """Run the answer key's hidden sequences on the evaluator's device and return one record per check of `contract`.

    Each sequence starts with `session.reset({})` and a read of the state. A state other than the key's `reset` leaves
    every record of the sequence undecided ('unverified reset') and its steps uncalled. Each step calls its task through
    `run_call` with its grants, classifies the answer and reads the state; a cell that cannot be read leaves its record
    undecided ('unreadable state'). Only these observations of the evaluator's leave a record undecided, never what the
    package returned. An output's record carries the unit the driver reported. A call reported as a host fault is
    graded as `execute_plan` grades it (`emulated_actions.graded_result`): the driver's error while grading a frozen
    package (`healthy_device_failures`) on a device that is still healthy, unless its package process could not start
    (`spawn`); otherwise, and for `spawn` on any device, RuntimeError, and nothing is graded. A failed read is undecided
    only on a healthy device too. The raw evidence is written to `evidence_path` as the steps run."""
    from .behavior import _check_contract
    from .case_assets import write_json
    from .emulated_actions import _device_healthy, graded_result, run_call
    key = answer_key(truth)
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

    def record(identifier, value, undecided, **reported):
        check = checks[identifier]
        records.append({'id': identifier, 'revision': check['revision'], 'artifact_sha256': contract['artifact_sha256'],
                        'channel': check['channel'], 'unit': check['unit'], 'value': value, **reported,
                        **({'undecided': undecided} if undecided else {})})

    try:
        for sequence in key['sequences']:
            event = {'sequence': sequence['id'], 'reset': session.reset({})}
            events.append(event)
            event['state'] = read()
            verified = event['verified'] = event['state'] == key['reset']
            for number, step in enumerate(sequence['steps'], 1):
                answer, outputs, units, state = None, {}, {}, {}
                if verified:
                    result = run_call(session, lambda task, inputs: invoke(task, inputs, step['grants']),
                                      {'operation': step['task'], 'parameters': step['inputs']})
                    event = {'sequence': sequence['id'], 'step': number, 'result': result}
                    events.append(event)
                    result = event['result'] = graded_result(session, result, event,
                                                             healthy_device_failures=healthy_device_failures)
                    answer = event['answer'] = classify_answer(result, step)
                    outputs = result['outputs'] if isinstance(result.get('outputs'), dict) else {}
                    units = result['units'] if isinstance(result.get('units'), dict) else {}
                    state = event['state'] = read()
                reason = None if verified else 'unverified reset'
                prefix = '%s/%d/' % (sequence['id'], number)
                record(prefix + 'answer', answer, reason)
                for name in step.get('outputs', {}):
                    # The unit the driver reported, so that another unit fails the check; an undecided record keeps
                    # its check's.
                    reported = {} if reason else {'unit': units.get(name)}
                    record(prefix + 'out:' + name, outputs.get(name), reason, **reported)
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
