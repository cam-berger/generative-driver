"""A valid generated device: the executable form of docs/generated-device-interface.md (ADR 0007).

`validate_device` checks the plaintext device that the generator seals and the evaluator opens: the identity part of
its manifest entry, its image, its public mission and, given one, its answer key. `answer_key` checks a key alone, for
a grader that holds only the key, and `checks` derives the key's check ids. Each raises ValueError naming the first
defect. The module holds no device's design, so the private generator may import it (ADR 0005).
"""
import json
import re

from .capabilities import bind_task, numeric_value
from .missions import validate_mission

FRAMINGS = ('text-lines', 'binary-frames', 'register-access')
SCHEMA = 'generated-answer-key/2'
REQUIRED_MUTANTS = frozenset({'wrong-scale', 'constant-output', 'wrong-state'})
ANSWERS = ('value', 'refusal', 'no_reply')
_FIELDS = ('schema', 'nonce', 'recipe', 'state', 'reset', 'machine', 'sequences', 'mission', 'references', 'mutants')
_STEP_FIELDS = ('task', 'inputs', 'grants', 'answer', 'state', 'transition')
_GRANTS = ('read', 'write', 'actuate', 'reset')
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
_HEX = re.compile(r'(?:[0-9a-f]{2})+')
_DEVICE_ID = re.compile(r'[0-9a-f]{8}')
_LEVELS = (1, 2, 3, 4, 5)


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


def _machine(machine, cells, reset):
    """The `machine`'s states and its transitions by id, once it is well formed: distinct states, each the values of the
    `cells` in it, the initial one the `reset`, and transitions with distinct ids that start and end among them."""
    _fields(machine, ('initial', 'states', 'transitions'), what='machine')
    states = machine['states']
    _require(isinstance(states, dict) and bool(states) and all(_named(name) for name in states),
             'the machine names its states')
    for name, values in states.items():
        _state(values, cells, 'state ' + name)
    _require(len({tuple(sorted(values.items())) for values in states.values()}) == len(states),
             'distinct states hold distinct cell values')
    _require(_named(machine['initial']) and machine['initial'] in states and states[machine['initial']] == reset,
             'the initial state is the reset')
    transitions = machine['transitions']
    _require(isinstance(transitions, list) and bool(transitions), 'the machine has transitions')
    for transition in transitions:
        _fields(transition, ('id', 'from', 'task', 'answer', 'to'), what='transition')
        _require(_named(transition['id']) and _named(transition['task']) and transition['answer'] in ANSWERS,
                 'a transition names its id, task and answer')
        _require(all(_named(transition[end]) and transition[end] in states for end in ('from', 'to')),
                 'transition %s starts and ends among the states' % transition['id'])
    by_id = {transition['id']: transition for transition in transitions}
    _require(len(by_id) == len(transitions), 'transitions have distinct ids')
    return states, by_id


def answer_key(truth):
    """`truth` once it is a well-formed answer key, `generated-answer-key/2` in docs/generated-device-interface.md,
    beside the `artifact_sha256` the evaluator adds: each field's own rules and those that need no mission. ValueError
    names the first defect."""
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
    # A verdict publishes check ids built from cell and sequence names, so the names are numbers.
    _require(set(cells) == {'c%d' % number for number in range(1, len(cells) + 1)},
             'the state cells are named c1 to c%d' % len(cells))
    _state(truth['reset'], cells, 'the reset')
    _state(_fields(truth['mission'], ('state',), what='mission')['state'], cells, 'the mission')
    states, transitions = _machine(truth['machine'], cells, truth['reset'])
    sequences = truth['sequences']
    _require(isinstance(sequences, list) and bool(sequences), 'there are sequences')
    taken = set()
    for index, sequence in enumerate(sequences, 1):
        _fields(sequence, ('id', 'steps'), what='sequence')
        _require(sequence['id'] == 's%d' % index, 'sequence %d is named s%d' % (index, index))
        _require(isinstance(sequence['steps'], list) and bool(sequence['steps']), 'a sequence has steps')
        # Each sequence walks the machine from its initial state, one transition a step.
        current = truth['machine']['initial']
        for number, step in enumerate(sequence['steps'], 1):
            where = 'step %s/%d' % (sequence['id'], number)
            _step(step, cells, where)
            transition = transitions.get(step['transition']) if isinstance(step['transition'], str) else None
            _require(transition is not None, where + ' takes a declared transition')
            _require((transition['task'], transition['answer']) == (step['task'], step['answer']),
                     where + ' has its transition\'s task and answer')
            _require(transition['from'] == current,
                     where + ' starts where the previous step ended, or in the initial state')
            _require(step['state'] == states[transition['to']], where + ' ends in its transition\'s end state')
            current = transition['to']
            taken.add(transition['id'])
    _require(taken == set(transitions), 'the hidden sequences take every transition')
    by_task = {}
    for sequence in sequences:
        for step in sequence['steps']:
            by_task.setdefault(step['task'], []).append(step)
    for task, steps in by_task.items():
        # The runtime refuses an ungranted effect before sending, which grades as a matching refusal: a refusal step
        # without its task's grant would pass any driver. A value step lets the reference runs prove the grants.
        _require(len({frozenset(step['grants']) for step in steps}) == 1,
                 'the steps of task %s carry the same grants' % task)
        _require(any(step['answer'] == 'value' for step in steps),
                 'task %s has a value step, which proves its grants and replies' % task)
    # The runtime runs a driver's identity operation, a read, before every call: a read that changed the state would
    # shift every later one. A read task's steps grant nothing beyond reading, and its steps agree on their grants.
    reads = {task for task, steps in by_task.items() if set(steps[0]['grants']) <= {'read'}}
    for transition in transitions.values():
        _require(transition['task'] not in reads or transition['from'] == transition['to'],
                 'transition %s of read task %s starts and ends in the same state'
                 % (transition['id'], transition['task']))
        # A refusal matches whether the device or the driver made it, and a driver's own refusal sends nothing.
        _require(transition['answer'] != 'refusal' or transition['from'] == transition['to'],
                 'refusal transition %s starts and ends in the same state' % transition['id'])
    # Hidden sequences include out-of-range values, so passing on the device's refusals is graded.
    _require(any(transition['answer'] == 'refusal' for transition in transitions.values()),
             'the hidden sequences take a refusal step')
    steps = [step for sequence in sequences for step in sequence['steps']]
    for cell in cells:
        # Renode reads an unmapped address as 0, so a cell that never changes would test nothing.
        _require(any(step['state'][cell] != truth['reset'][cell] for step in steps),
                 'cell %s changes value in some step' % cell)
    # The required mutants wrong-scale and constant-output break an output.
    _require(any(step.get('outputs') for step in steps), 'a value step reports an output')
    references = truth['references']
    _require(isinstance(references, dict) and set(references) == {'a', 'b'}, 'the references are a and b')
    for reference in references.values():
        _driver(_fields(reference, ('model', 'capabilities'), what='reference'))
    mutants = truth['mutants']
    _require(isinstance(mutants, list), 'the mutants are a list')
    ids = {check['id'] for check in checks(truth, 0)}
    for mutant in mutants:
        failed = _driver(_fields(mutant, ('id', 'model', 'capabilities', 'expected_failed_checks'), what='mutant'))[
            'expected_failed_checks']
        _require(_named(mutant['id']) and isinstance(failed, list) and bool(failed)
                 and all(isinstance(check, str) for check in failed) and len(set(failed)) == len(failed)
                 and set(failed) <= ids, 'a mutant expects distinct failures of the contract\'s checks')
    _require(len({mutant['id'] for mutant in mutants}) == len(mutants), 'mutant ids are distinct')
    _require(REQUIRED_MUTANTS <= {mutant['id'] for mutant in mutants},
             'the mutants include ' + ', '.join(sorted(REQUIRED_MUTANTS)))
    return truth


def _identity_request(model):
    """What the `model`'s identity operation sends first: the bytes of its first exchange when its parts are text and
    hex, otherwise those parts. ValueError for a model whose identity operation sends nothing."""
    try:
        operation = model['operations'][model['identity']['operation']]
        parts = next(step for step in operation['steps'] if step.get('op') == 'exchange')['tx']
        if all('text' in part or 'hex' in part for part in parts):
            return b''.join(part['text'].encode('utf-8') if 'text' in part else bytes.fromhex(part['hex'])
                            for part in parts)
        return json.dumps(parts, sort_keys=True)
    except (KeyError, TypeError, ValueError, StopIteration, AttributeError) as error:
        raise ValueError('Invalid generated answer key: a reference\'s identity operation sends no request') from error


def _references(key):
    """ValueError unless the two references show what qualification's reference runs must: both forward every request
    a refusal step makes, within their parameter bounds, so the runs see the device's refusal bytes and not a driver's
    own bounds; and their identity operations, which the runtime runs before every call, send different commands."""
    references = key['references']
    for sequence in key['sequences']:
        for number, step in enumerate(sequence['steps'], 1):
            for name, reference in references.items():
                if step['answer'] != 'refusal':
                    continue
                try:
                    bind_task(reference['capabilities'], step['task'], step['inputs'], reference['model'])
                except ValueError as error:
                    raise ValueError('Invalid generated answer key: reference %s forwards the request of refusal step '
                                     '%s/%d within its parameter bounds' % (name, sequence['id'], number)) from error
    _require(_identity_request(references['a']['model']) != _identity_request(references['b']['model']),
             'the references\' identity operations send different commands')


def _driver(candidate):
    _require(isinstance(candidate['model'], dict) and isinstance(candidate['capabilities'], dict),
             'a driver is a model and its capabilities')
    return candidate


def checks(key, revision):
    """The checks of the hidden sequences, steps numbered from 1. An output's check expects the unit the public mission
    states for it, which the driver must report; the answer and the state cells are the evaluator's own observations,
    in the units `answer` and `cell-value`."""
    found = []
    for sequence in key['sequences']:
        for number, step in enumerate(sequence['steps'], 1):
            prefix = '%s/%d/' % (sequence['id'], number)
            found.append({'id': prefix + 'answer', 'revision': revision, 'kind': 'text', 'expected': step['answer'],
                          'unit': 'answer', 'channel': 'runtime-transcript'})
            found.extend({'id': prefix + 'out:' + name, 'revision': revision, 'kind': 'number',
                          'expected': output['value'], 'absolute_tolerance': output['tolerance'],
                          'unit': output['unit'], 'channel': 'runtime-transcript'}
                         for name, output in step.get('outputs', {}).items())
            found.extend({'id': prefix + 'state:' + cell, 'revision': revision, 'kind': 'number',
                          'expected': step['state'][cell], 'absolute_tolerance': 0, 'unit': 'cell-value',
                          'channel': 'device-state'} for cell in key['state'])
    return found


def validate_identity(device):
    """Nothing, once `device` names a valid `id`, `level` and `framing`: eight lowercase hexadecimal digits, which carry
    no design and never name a Windows device, a level from 1 to 5 and one of FRAMINGS. ValueError names the first
    defect."""
    if not isinstance(device, dict):
        raise ValueError('A generated device is an object')
    identity, level, framing = device.get('id'), device.get('level'), device.get('framing')
    if not isinstance(identity, str) or not _DEVICE_ID.fullmatch(identity):
        raise ValueError('Invalid device id: ' + repr(identity))
    if type(level) is not int or level not in _LEVELS:  # a bool is not a level
        raise ValueError('Invalid level of device ' + identity + ': ' + repr(level))
    if framing not in FRAMINGS:
        raise ValueError('Invalid framing of device ' + identity + ': ' + repr(framing))


def _mission(mission):
    """ValueError unless `mission` is a public mission for a generated device, which states effects only: a
    `benchmark-public-mission/1` of case `generated` without operating conditions or sampling, whose inputs are integers
    or numbers and whose outputs have units, each with no other key, such as a range. Capability binding takes no bytes,
    and takes a string only from a set the driver declares."""
    validate_mission(mission, 'generated')

    def require(condition, defect):
        if not condition:
            raise ValueError('Invalid generated mission: ' + defect)

    require('operating_conditions' not in mission, 'it states no operating conditions')
    for name, task in mission['tasks'].items():
        require('sampling' not in task, 'task %s states no sampling' % name)
        for item, spec in task['inputs'].items():
            require(set(spec) == {'type'} and spec['type'] in ('integer', 'number'),
                    'task %s input %s is an integer or a number, with no other key' % (name, item))
        for item, spec in task['outputs'].items():
            require(set(spec) == {'unit'} and isinstance(spec['unit'], str) and bool(spec['unit']),
                    'task %s output %s has a unit and no other key' % (name, item))


def _typed(value, kind):
    """Whether `value` has the public input type `kind`: an integer, or a finite number of either kind."""
    return type(value) is int if kind == 'integer' else type(value) in (int, float) and _finite(value)


def _agreement(key, mission):
    """ValueError unless the answer `key` agrees with its device's public `mission`: its steps call exactly the
    mission's tasks, each with the task's declared inputs, and each value step reports the task's declared outputs in
    the mission's units; every reference and mutant maps exactly the mission's tasks."""
    tasks = mission['tasks']
    steps = [('%s/%d' % (sequence['id'], number), step) for sequence in key['sequences']
             for number, step in enumerate(sequence['steps'], 1)]
    _require({step['task'] for _, step in steps} == set(tasks), 'the steps call exactly the mission\'s tasks')
    for where, step in steps:
        task = tasks[step['task']]
        _require(set(step['inputs']) == set(task['inputs'])
                 and all(_typed(step['inputs'][name], spec['type']) for name, spec in task['inputs'].items()),
                 'step %s inputs are its task\'s declared inputs, with their types' % where)
        if step['answer'] == 'value':
            outputs = step.get('outputs', {})
            _require(set(outputs) == set(task['outputs'])
                     and all(outputs[name]['unit'] == spec['unit'] for name, spec in task['outputs'].items()),
                     'step %s outputs are its task\'s declared outputs, in the mission\'s units' % where)
    drivers = [('reference ' + name, reference) for name, reference in key['references'].items()]
    drivers += [('mutant ' + mutant['id'], mutant) for mutant in key['mutants']]
    for name, driver in drivers:
        mapped = driver['capabilities'].get('tasks')
        _require(isinstance(mapped, dict) and set(mapped) == set(tasks), name + ' maps exactly the mission\'s tasks')


def _reply_mode(reference, task):
    """How `reference` reads the reply to `task`'s request: the receive mode of the last exchange of the operation it maps
    the task to, `until` only on a line feed. None for a mapping or model without one."""
    try:
        operation = reference['model']['operations'][reference['capabilities']['tasks'][task]['operation']]
        rx = [step for step in operation['steps'] if step.get('op') == 'exchange'][-1]['rx']
        return rx['mode'] if rx['mode'] != 'until' or rx.get('delimiter_hex') == '0a' else None
    except (KeyError, TypeError, IndexError, AttributeError):
        return None


def _framing(key, framing):
    """ValueError unless the answer `key` suits its device's `framing`. Every correct driver must capture a refusal as
    the same bytes, so a text-lines device's refusal replies end with a line feed, and its two references read each
    refusing task's reply with `until` on the line feed and with `lines`: qualification then shows two capture styles
    see the bytes the key records. A correct driver must read a whole binary reply anyway."""
    if framing != 'text-lines':
        return
    for sequence in key['sequences']:
        for number, step in enumerate(sequence['steps'], 1):
            _require(step.get('refusal_rx_hex', '0a').endswith('0a'),
                     'step %s/%d: a text-lines refusal ends with a line feed' % (sequence['id'], number))
    refusing = {step['task'] for sequence in key['sequences'] for step in sequence['steps']
                if step['answer'] == 'refusal'}
    for task in sorted(refusing):
        _require({_reply_mode(reference, task) for reference in key['references'].values()} == {'until', 'lines'},
                 'the references read the reply of refusing task %s with until and with lines' % task)


def validate_device(device, *, image, mission, key=None):
    """Nothing, once the plaintext device is valid: `device`, the identity part of its manifest entry, its `image`, its
    public `mission` and, given one, its answer `key` and the key's agreement with the mission. ValueError names the
    first defect."""
    validate_identity(device)
    _mission(mission)
    if key is not None:
        answer_key(key)
        _agreement(key, mission)
        _references(key)
        _framing(key, device['framing'])
