"""Device-first interpretation (docs/adr/0009).

A generic run with no single firmware image used to skip interpretation, so its device stages had nothing to test and
the run ended with no driver even when the device answered on its own port. When such a run names a device binding, a
fresh interpret worker now writes the model from the device's replies to operations it declares read, the listen
evidence and the supplementary documents, and hands it on as an interpret worker does. Interpretation stays the one
stage that writes models, so probe, ground and repairs work unchanged.

Its device calls run only operations declared read, whatever the run's grants; opening the device follows the run's
own rules and its reset grant. The configurator records each call as it records a probe stage's, and the interpret
handoff's notes list them; the records stay out of the handoff's artifacts, so probe and ground receive only the
model."""
import hashlib
import json

_MODE = 'device'
TOOLS = ('model_qualify', 'interface_describe', 'interface_execute', 'probe_run')
_INSTRUCTIONS = 'DEVICE_FIRST.md'
# The run's folder keeps each device-first attempt's tool records under this name, outside its sealed workspace.
FOLDER = 'device-first'
_RUNS_ONLY_READS = ('You may run only operations you declare read: the configurator refuses an operation declared '
                    'write or actuate, or with no effect, before it reaches the device, whatever the run\'s grants. ')


def applies(spec, images):
    """Whether a generic interpret attempt with these candidate `images` interprets device first: it has no single
    image and the run names a device binding (07-B5)."""
    return len(images) != 1 and bool(spec.get('binding'))


def sealed_inputs():
    """The device-first worker's own sealed input by workspace name: its instructions."""
    from .toolkit import resources_root
    return {_INSTRUCTIONS: str(resources_root() / 'device_first' / _INSTRUCTIONS)}


def effects(grants):
    """What the device-first worker's calls may do: read, and reset when the run grants it, because opening a device
    that resets when opened needs that grant (07-B7)."""
    return ['read', *(['reset'] if 'reset' in grants else [])]


def tasks(goal, grants):
    """The device-first worker's TASKS.json, without the documents' entry: there is no firmware, the model comes from
    the device, the worker runs only read operations, and probe and ground test the model under the run's `grants`,
    which it names (07-B6)."""
    named = ', '.join(grants) or 'none'
    return {'schema': 'candidate-task-contract/1', 'tasks': {'requested': {'meaning': goal}}, 'effect_grants': grants,
            'instructions': 'The objective describes requested behavior, not protocol evidence. Recover bytes, '
                            'decoders and units from the device\'s replies and the supplied evidence. Report '
                            'unsupported behavior.',
            'device_first': {
                'firmware': None,
                'instructions': 'There is no firmware: this run acquired no single firmware image, so write the model '
                                'from the device, from its replies to the operations you run, with the listen evidence '
                                'and the documents. ' + _RUNS_ONLY_READS + 'Probe and ground then test the model on '
                                'the device under the run\'s grants (' + named + '). Declare a write or actuate '
                                'operation only where the documents or the device\'s read replies support it, and give '
                                'each one an uncertainty with the code not_exercised on that operation in model.json, '
                                'saying interpretation did not exercise it.',
                'run_effects': effects(grants), 'probe_and_ground_grants': grants}}


def interpretation(requested, candidates):
    """The interpret stage's `configurator.interpretation` notes of a device-first attempt (07-B5): the mode, the number
    of candidate images, and a kit analysis that was not made because no single image was acquired."""
    notes = []
    if candidates > 1:
        notes.append('%d firmware images were acquired; device-first interpretation interprets none of them, so none '
                     'was given to the worker.' % candidates)
    if requested == 'tasks':
        notes.append('The run asked for interpret tasks, but device-first interpretation runs as one worker.')
    return {'requested': requested, 'mode': _MODE, 'candidate_images': candidates,
            'kit_analysis': {'made': False, 'cause': 'no_single_image', 'reported': [],
                             'reason': 'No single firmware image was acquired (%d candidates), so there was nothing to '
                                       'analyze' % candidates},
            'notes': notes}


def prompt(workspace):
    """The device-first worker's prompt, in place of the interpret prompt that says the worker has no device access."""
    return ('Keep task evidence and generated artifacts inside ' + str(workspace) + '. Read '
            + str(workspace / _INSTRUCTIONS) + ' and do exactly what it says: there is no firmware image for this '
            'device, so you write its interface model from the device, through the assigned tools, with the listen '
            'evidence and the supplied documents. Reach the device only through those tools, with model_dir set to '
            'this workspace; the configurator owns the connection. ' + _RUNS_ONLY_READS + 'Do not enumerate, open or '
            'query any USB, serial or network device or service yourself, and do not search the web. Do not read '
            'personal instructions, memory files, other repositories, other runs, passwords or evaluator-owned files. '
            'If REPAIR_CONTEXT.json is supplied, this is a repair: read it first. Every file supplied in this '
            'workspace is a sealed input: read it and leave it unchanged. Write model.json, replies.json and NOTES.md '
            'at the workspace root, and report in one paragraph what you produced and what the evidence left '
            'ambiguous.')


def refusal(model, operation, effects_found):
    """Why the device-first worker may not run `operation` of `model` on the device, or None when it is declared
    read: `effects_found` are the effects the gateway read for it (07-B7)."""
    if not set(effects_found) - {'read'}:
        return None
    operations = model.get('operations') if isinstance(model, dict) else None
    entry = operations.get(operation) if isinstance(operations, dict) and operation is not None else None
    declared = entry.get('effect') if isinstance(entry, dict) else None
    named = (repr(operation) + ' declares ' + (repr(declared) if declared else 'no effect') if operation is not None
             else 'the model declares ' + ', '.join(map(repr, sorted(set(effects_found) - {'read'}))))
    return ('Device-first interpretation runs only operations declared read, whatever the run\'s grants; nothing was '
            'dispatched: ' + named)


def calls(started, finished):
    """The device-first worker's device calls for the interpret handoff's notes, in order: each call's dispatch id,
    tool, operation and whether it succeeded, with the sha256 of the configurator's record of it (its tool.finished
    payload as the run's events store it), or None for a call dispatched without a recorded result. `started` are
    the assignment's tool.started payloads with their dispatch ids, `finished` its tool.finished payloads."""
    done = {str(event.get('dispatch_id')): event for event in finished}
    listed = []
    for event in started:
        if event.get('connected') is not True:
            continue
        record = done.get(str(event.get('dispatch_id')))
        result = record.get('result') if record and isinstance(record.get('result'), dict) else {}
        results = result.get('results') if isinstance(result.get('results'), list) else [result]
        operation = next((r.get('operation') for r in results if isinstance(r, dict) and r.get('operation')), None)
        stored = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False) if record else None
        listed.append({'dispatch_id': str(event.get('dispatch_id')), 'name': event.get('name'), 'operation': operation,
                       'ok': record.get('ok') if record else None,
                       'record_sha256': hashlib.sha256(stored.encode('utf-8')).hexdigest() if stored else None})
    return listed
