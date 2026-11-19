"""The generated family: grades the frozen driver of any generated device from its decrypted answer key, step by step.

An answer key (`generated-answer-key/2`) holds the device's emulator recipe, the memory cells of its state, the state a
verified reset leaves, the hidden sequences, the state the public mission's objective leaves, two reference drivers and
the mutants. Each hidden sequence starts from a reset that the evaluator verifies by reading the state, then calls the
mission's tasks one step at a time. A step passes when the driver's answer and the device state read afterwards both
match the reference's. The answer is a value, a refusal (the device's, or the driver's own decision not to send the
request), no reply, or an error, which never matches (decision 19). A check the evaluator cannot decide, a cell it
cannot read or a sequence whose reset it could not verify, is undecided, never failed. Only the evaluator reads the
state: workers see no instrument of a generated device (decision 15). What a valid answer key holds, and its check ids,
are `generated_device`'s. A generated device has no manifest of its own: the template here makes its pin (Feature 03,
generated trials), which never holds its level or framing, because a worker can read it.
"""
from .generated_device import REQUIRED_MUTANTS, answer_key, checks  # noqa: F401  REQUIRED_MUTANTS: family interface


# Copied from parameter-store-v1's manifest, as a pin's own field: every generated device runs under one time policy.
_TIME_POLICY = {'probe': 'continuous emulator virtual time', 'package_calls': 'paused between calls',
                'limitation': 'Thinking time excluded; not a real-time control benchmark'}
ARTIFACT = 'firmware.bin'
MISSION = 'mission.json'


def device_pin(registration, device_id, *, manifest_sha256, truth_sha256, image_sha256, mission_sha256,
               evidence_sha256):
    """The pin of one generated device: the one template, and the hashes and ids that tell its device from another."""
    identity = {'family': 'generated', 'execution': 'actual-agent-emulation', 'evidence_track': 'firmware',
                'scope': 'full-workflow'}
    return {'schema': 'benchmark-case-pin/1', 'case': 'generated', 'case_id': 'generated', 'case_version': '1',
            'evaluator_version': '1', 'scenario_id': 'original', 'case_seed': 0, **identity,
            'set': registration, 'device_id': device_id, 'manifest_sha256': manifest_sha256,
            'truth_sha256': truth_sha256, 'image_hashes': {ARTIFACT: image_sha256},
            'time_policy': dict(_TIME_POLICY), 'calibration': {'status': 'passed', 'evidence_sha256': evidence_sha256},
            'manifest': {'schema': 'benchmark-case/2', 'id': 'generated', 'version': '1', 'evaluator_version': '1',
                         **identity, 'scenarios': ['original'],
                         'required_stages': ['acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse'],
                         'images': {ARTIFACT: image_sha256}, 'assets': {MISSION: mission_sha256},
                         'truth': {'sha256': truth_sha256}, 'default_effects': ['write', 'actuate']}}


def current_pin(pin, *, check_truth=True):
    """`pin`, once the template rebuilds it from its own hashes and `sets.json` registers its `manifest_sha256` for its
    `set`; ValueError otherwise. It opens no file of the set, whose volume is unmounted while a trial runs: whether a
    device's own hashes are its set's is for `admit`, which opens the set. `check_truth` has nothing to do here."""
    from .generated_sets import set_registrations
    try:
        rebuilt = device_pin(pin['set'], pin['device_id'], manifest_sha256=pin['manifest_sha256'],
                             truth_sha256=pin['truth_sha256'], image_sha256=pin['image_hashes'][ARTIFACT],
                             mission_sha256=pin['manifest']['assets'][MISSION],
                             evidence_sha256=pin['calibration']['evidence_sha256'])
    except (KeyError, TypeError) as error:
        raise ValueError('A generated device pin lacks a field: ' + type(error).__name__) from None
    if pin != rebuilt:
        raise ValueError('A generated device pin differs from its template')
    if set_registrations().get(pin['set']) != pin['manifest_sha256']:
        raise ValueError('A generated device pin names a manifest its set does not register: ' + str(pin['set']))
    return rebuilt


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
            'checks': checks(key, pin.get('revision', 0))}


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


def execute(session, invoke, pin, contract, truth, evidence_path, *, phase, healthy_device_failures):
    """Run the answer key's hidden sequences on the evaluator's device and return one record per check of `contract`.

    `contract` already holds the final phase's checks, the only phase of a generated device, so neither `pin` nor
    `phase` selects anything here.

    First, on the fresh session, it reads the startup observation (`device_provider.observed_values`, the read recovery
    compares), before any reset or call. Its values are `boot_reading` in the evidence, for set qualification to compare
    across a device's runs; no check reads them. A read that raises is a run error, as other device errors are.

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
    from .device_provider import observed_values
    from .emulated_actions import _device_healthy, graded_result, run_call
    key = answer_key(truth)
    _check_contract(contract)
    checks = {check['id']: check for check in contract['checks']}
    events, records, boot = [], [], {}

    def save(**extra):
        write_json(evidence_path, {'schema': 'generated-step-evidence/1', **boot, 'events': events, 'records': records,
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
        boot['boot_reading'] = observed_values(session)['values']
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
    """None: fresh reuse of a generated device is not judged, since no Feature holds that judgement yet."""
    return None


HOOKS = {'contract': contract, 'execute': execute, 'observations': observations, 'reuse_passed': reuse_passed,
         'required_mutants': REQUIRED_MUTANTS, 'current_pin': current_pin}
