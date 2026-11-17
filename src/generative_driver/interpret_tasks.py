"""Interpretation as tasks (docs/features/kit-fixes/06-interpret-tasks.md, 06-B5 to 06-B11).

One interpret worker reads the whole firmware analysis and writes the whole model in one growing context. In task mode
each interpret attempt is instead a few fresh workers with small inputs, run one at a time: an inventory of the
commands, a handler task per command, which writes that operation as a fragment, and a cross-check of the model the
configurator assembles from the fragments. A repair re-runs only the handler tasks of the operations the device
contradicted, then assembles and cross-checks again. The task mode is opt-in (`interpretation: tasks`) and needs the
kit analysis; what it cannot do falls back to the single worker.

The configurator calls `choose` from its interpret preparation and `run` from its stage launch; the attempt's output is
left in the sealed workspace prepared for the single worker, and the interpret check collects it from there."""
import copy
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from . import analysis_index, supplementary_documents
from .integrity import IntegrityError

# The values of the `interpretation` start parameter; the first is the default.
MODES = ('single', 'tasks')
# The run's folder keeps each task-mode attempt's records (its inventory, fragments and record.json) under this name,
# by revision and attempt.
FOLDER = 'interpret-tasks'
# The model's limit on operations, and so on an inventory's commands.
MAX_COMMANDS = 64
# What the configurator observed in acquisition, which the single worker gets and the inventory gets too.
EVIDENCE = ('serial-listen.json', 'BINDING_CONTEXT.json')
# What a handler task and the cross-check get of the single worker's kit: the model document, the contract guide and
# tool, and the validator and qualifier with the runtime they import.
KIT = ('INTERFACE_MODEL.md', 'CONTRACT.md', 'contract.py', 'validate.py', 'qualify.py', '_toolkit')
FRAGMENT_SCHEMA = 'interpret-fragment/1'
RECORD_SCHEMA = 'interpret-task-record/1'


def _instructions(name):
    from .toolkit import resources_root
    return resources_root() / 'interpret_tasks' / name


def choose(spec, analysis, accepted):
    """How an interpret attempt runs: ('tasks' or 'single', notes saying why a run that asked for tasks runs the single
    worker, and for a task-mode repair the previous attempt it starts from: {record, model})."""
    if (spec.get('interpretation') or MODES[0]) != 'tasks':
        return 'single', [], None
    if not analysis['made']:
        return 'single', ['The run asked for interpret tasks, but the kit analysis was not made (' + analysis['cause']
                          + '), so the single worker interpreted.'], None
    if not spec.get('_revision'):
        return 'tasks', [], None
    handoff = next((h for h in reversed(accepted) if h['stage'] == 'interpret'), None) or {}
    account = (((handoff.get('notes') or {}).get('inline') or {}).get('configurator') or {}).get('interpretation') or {}
    model = next((a['path'] for a in handoff.get('artifacts') or () if (Path(a['path']) / 'model.json').is_file()),
                 None)
    if account.get('mode') != 'tasks' or not account.get('record') or model is None:
        return 'single', ['The attempt this repair starts from ran the single worker, so the repair does too.'], None
    return 'tasks', [], {'record': account['record'], 'model': model}


def run(plan, *, launch):
    """The outcome of one interpret attempt run as tasks: {interpretation, result} when the attempt ran as tasks, or
    when a task's worker did not complete, so that the run stops as it would for a stage worker's; or
    {interpretation, fallback: why} when the attempt goes to the single worker. A completed attempt leaves its output
    in the single worker's sealed workspace.

    `plan` names the run's folder (`run_dir`), the attempt (`revision`, `attempt`), the sealed workspace prepared for
    the single worker (`workspace`), whose files the tasks share and where the attempt's output is left, the kit
    analysis (`index`, `export`), the stage's notes so far (`interpretation`) and, for a repair, the attempt it starts
    from (`previous`) and the device's differences (`differences`). `launch(task, workspace, prompt)` runs one task's
    fresh worker and records it, returning the runtime's result."""
    attempt = _Attempt(plan, launch)
    try:
        attempt.repair() if plan.get('previous') else attempt.first()
        return {'interpretation': attempt.account(record=attempt.record()), 'result': attempt.combined()}
    except _Stopped as stopped:
        return {'interpretation': attempt.account(), 'result': stopped.result}
    except _Fallback as fallback:
        attempt.notes.append(str(fallback) + ', so this attempt ran the single worker.')
        return {'interpretation': attempt.account(mode='single'), 'fallback': str(fallback)}


def _named_operations(differences):
    """The device differences by the operation each names: where the runtime says it failed, the `operations.<name>`
    prefix of a defect's location, or else the operation of the last step of a failed result's transcript (06-B11).
    A difference that names no operation is not listed."""
    named = {}
    for entry in differences or ():
        if not isinstance(entry, dict):
            continue
        names = [defect['where'].split('.')[1] for defect in entry.get('defects') or ()
                 if isinstance(defect, dict) and isinstance(defect.get('where'), str)
                 and defect['where'].startswith('operations.') and len(defect['where'].split('.')) > 1]
        if not names:
            for failed in entry.get('failed') or ():
                transcript = failed.get('transcript') if isinstance(failed, dict) else None
                last = transcript[-1] if isinstance(transcript, list) and transcript else None
                if isinstance(last, dict) and isinstance(last.get('operation'), str):
                    names.append(last['operation'])
        for name in dict.fromkeys(names):
            named.setdefault(name, []).append(entry)
    return named


class _Stopped(Exception):
    """A task's worker did not complete: it could not be launched, exited with an error, ran out of the run's budget
    or was cancelled."""

    def __init__(self, result):
        super().__init__(result.get('reason'))
        self.result = result


class _Fallback(Exception):
    """The attempt goes to the single worker; the text says why."""


class _Attempt:
    def __init__(self, plan, launch):
        self.plan, self.launch = plan, launch
        self.run_dir = Path(plan['run_dir'])
        self.records = self.run_dir / FOLDER / str(plan['revision']) / plan['attempt']
        self.source = Path(plan['workspace'])
        self.export = analysis_index.Export(plan['export'])
        self.notes = list(plan['interpretation'].get('notes', ()))
        self.results = []  # (task, runtime result) of each task run so far
        self.task_notes = {}  # each completed task's NOTES.md, by task
        self.ignored = set()  # (command, path) of each citation of no forwarded document page, noted once
        self.kept = {'inventory': None, 'fragments': {}}  # what this attempt's records hold, as the notes name it
        tasks = json.loads((self.source / 'TASKS.json').read_text(encoding='utf-8'))
        tasks.pop('kit_analysis', None)  # It tells the single worker of the export, which no task gets.
        self.tasks_json = json.dumps(tasks, indent=1)

    def account(self, mode='tasks', record=None):
        """The stage's notes on how it interpreted: the mode, the notes and what the records keep, with hashes."""
        return {**self.plan['interpretation'], 'mode': mode, 'notes': self.notes,
                **({'record': record} if record else {}),
                **({'inventory': self.kept['inventory']} if self.kept['inventory'] else {}),
                **({'fragments': self.kept['fragments']} if self.kept['fragments'] else {})}

    def first(self):
        """A first attempt: the inventory, the handler tasks, the assembly and the cross-check."""
        inventory = self.inventory()
        fragments = self.handlers(inventory)
        assembled, validation = self.assemble(inventory, fragments)
        self.cross_check(inventory, assembled, validation)

    def inventory(self):
        """The inventory the inventory task wrote, once that task has run (06-B6)."""
        candidates = [candidate['function'] for candidate in self.export.candidates()]
        strings, left = self.export.strings_table()
        documents, entry = self.documents()
        workspace = self.task({'kind': 'inventory'}, 'INVENTORY.md', {
            'ANALYSIS_INDEX.json': Path(self.plan['index']),
            'DISPATCHERS.c': self.export.excerpt(candidates, 'The decompiled code of the top-ranked dispatcher '
                                                            'candidates, by rank'),
            'strings.tsv': strings, 'TASKS.json': self.tasks_json,
            **{name: self.source / name for name in EVIDENCE if (self.source / name).is_file()}, **documents,
            'TASK.json': {'schema': 'interpret-task/1', 'kind': 'inventory', 'writes': ['inventory.json', 'NOTES.md'],
                          'max_commands': MAX_COMMANDS, 'dispatcher_candidates': candidates,
                          'strings_left_out': left, **entry}})
        inventory, why = _read_inventory(workspace / 'inventory.json')
        if inventory is None:
            raise _Fallback(why)
        self.kept['inventory'] = self.keep('inventory.json', inventory)
        return inventory

    def handlers(self, inventory):
        """Run a handler task per command, one at a time, the identity command's first, and return the usable
        fragments by command (06-B7)."""
        frame = {key: inventory.get(key) for key in ('schema', 'device', 'channel', 'framing')}
        fragments, identity = {}, None
        for command in _identity_first(inventory):
            fragment, why = self.handler(command, frame, identity)
            if command['name'] == inventory['identity']:
                if fragment is None:
                    raise _Fallback('The identity command ' + command['name'] + "'s task wrote no usable fragment ("
                                    + why + ')')
                identity = {'operation': fragment['operation'], 'model': fragment['model']}
            elif fragment is None:
                self.notes.append('The handler task for ' + command['name'] + ' wrote no usable fragment (' + why
                                  + '); its command is left out of the assembly.')
                continue
            fragments[command['name']] = fragment
            self.keep_fragment(command['name'], fragment)
        return fragments

    def handler(self, command, frame, identity, repair=None):
        """The fragment `command`'s handler task wrote and None, or None and why it is not usable, once that task has
        run. `identity` is the identity operation ({operation, model}) for every task but the identity command's;
        `repair` holds the previous fragment, operation and differences."""
        functions = self.export.nearest(command['handlers'])
        documents, entry = self.documents([command])
        workspace = self.task({'kind': 'handler', 'command': command['name']}, 'HANDLER.md', {
            'MODEL_FRAME.json': frame, 'COMMAND.json': command, **({'IDENTITY.json': identity} if identity else {}),
            'CODE.c': self.export.excerpt(functions, 'The decompiled code of the handler functions of '
                                          + command['name'] + ' and of the functions they call, nearest first'),
            'STRINGS.json': self.export.strings_used(functions), 'TASKS.json': self.tasks_json, **self.kit(),
            **(repair or {}), **documents,
            'TASK.json': {'schema': 'interpret-task/1', 'kind': 'handler', 'command': command['name'],
                          'repair': bool(repair), 'writes': ['fragment.json', 'NOTES.md'],
                          'fragment_schema': FRAGMENT_SCHEMA, **entry}})
        return _read_fragment(workspace / 'fragment.json')

    def keep_fragment(self, command, fragment):
        """Keep `command`'s fragment in this attempt's records; a later repair starts from it."""
        self.kept['fragments'][command] = {'operation': fragment['operation'], **self.keep(
            'fragments/' + str(len(self.results)) + '-' + _safe(command) + '.json', fragment)}

    def assemble(self, inventory, fragments):
        """model.json, replies.json and contract.json assembled from the inventory and the fragments, by name, and the
        whole model's validation (06-B8). A fragment that does not validate on its own, or whose operation name is
        taken, is left out; the identity command's sends the attempt to the single worker."""
        identity = fragments[inventory['identity']]
        frame = {'schema': inventory['schema'], 'device': inventory['device'], 'channel': inventory['channel'],
                 'identity': {'operation': identity['operation']}}
        kept = []
        for command, fragment in fragments.items():
            alone = [identity] if fragment is identity else [identity, fragment]
            defects = _validation(_model(frame, alone))['defects']
            if defects:
                why = ' does not validate on its own (' + _defect(defects[0]) + ')'
                if fragment is identity:
                    raise _Fallback('The identity command ' + command + "'s fragment" + why)
                self.notes.append('The fragment of ' + command + why + '; it is left out of the assembly.')
            elif any(fragment['operation'] == other['operation'] for other in kept):
                self.notes.append('The fragment of ' + command + ' names the operation ' + fragment['operation']
                                  + ', which is already taken; it is left out of the assembly.')
            else:
                kept.append(fragment)
        model = _model(frame, kept)
        replies = {'schema': 'interface-replies/1',
                   'operations': {fragment['operation']: fragment['replies'] for fragment in kept}}
        contract = {'schema': 'driver-contract/1', 'device': model['device'], 'identity': model['identity'],
                    'operations': {fragment['operation']: fragment['contract'] for fragment in kept},
                    'provenance': model['provenance']}
        return {'model.json': model, 'replies.json': _replies_schema(replies), 'contract.json': contract}, \
            _validation(model)

    def repair(self):
        """A repair from the previous attempt's final, cross-checked model: the handler task of each operation a
        difference names and a handler wrote runs again, the configurator puts the re-run operations in place of
        theirs, and the cross-check runs again with the previous final model and every difference (06-B11)."""
        previous = self.plan['previous']
        record = self.load(previous['record'])
        inventory = self.load(record['inventory'])
        written = {command: self.load(entry) for command, entry in record['fragments'].items()}
        self.kept = {'inventory': record['inventory'], 'fragments': dict(record['fragments'])}
        final = Path(previous['model'])
        files = {name: json.loads((final / name).read_text(encoding='utf-8'))
                 for name in ('model.json', 'replies.json', 'contract.json') if (final / name).is_file()}
        model, differences = files['model.json'], self.plan.get('differences') or []
        named = _named_operations(differences)
        frame = {'schema': model.get('schema'), 'device': model.get('device'), 'channel': model.get('channel'),
                 'framing': inventory.get('framing')}
        identity = (model.get('identity') or {}).get('operation')
        operations = model.get('operations') or {}
        rerun = [command for command in _identity_first(inventory)
                 if command['name'] in written and written[command['name']]['operation'] in named]
        replaced = {}
        for command in rerun:
            name = written[command['name']]['operation']
            fragment, why = self.handler(command, frame, None if name == identity else {
                'operation': identity, 'model': operations.get(identity)}, repair={
                'PREVIOUS_FRAGMENT.json': written[command['name']],
                'PREVIOUS_OPERATION.json': {'operation': name, 'model': operations.get(name)},
                'DIFFERENCES.json': named[name]})
            if fragment is not None and fragment['operation'] != name:
                why = 'it names the operation ' + fragment['operation'] + ' in place of ' + name
            elif fragment is not None:
                alone = {**{key: model.get(key) for key in ('schema', 'device', 'channel')},
                         'identity': {'operation': identity}}
                parts = [fragment] if name == identity else [_from_model(model, identity), fragment]
                defects = _validation(_model(alone, parts))['defects']
                why = 'it does not validate on its own (' + _defect(defects[0]) + ')' if defects else None
            if why is not None:
                self.notes.append('The repair of ' + name + ' wrote no usable fragment (' + why + '); the previous '
                                  'final model\'s operation stays.')
                continue
            replaced[name] = fragment
            self.keep_fragment(command['name'], fragment)
            if name == identity:
                operations = {**operations, name: fragment['model']}
        self.notes.append('This repair re-ran the handler tasks of ' + (', '.join(c['name'] for c in rerun) or 'no '
                          'command') + '; every other operation and the channel come from the previous final model.')
        assembled = _replaced(files, replaced)
        self.cross_check(inventory, assembled, _validation(assembled['model.json']),
                         {'previous-model': final, 'DIFFERENCES.json': differences})

    def cross_check(self, inventory, assembled, validation, repair=None):
        """Run the cross-check on the assembled files and leave what it leaves as the attempt's output, or the
        assembled files with a note when it leaves no usable result (06-B9)."""
        handlers = list(dict.fromkeys(address for command in _identity_first(inventory)
                                      for address in command['handlers']))
        documents, entry = self.documents(_identity_first(inventory))
        workspace, sealed = self.task({'kind': 'cross-check'}, 'CROSS_CHECK.md', {
            'VALIDATION.json': validation, 'inventory.json': inventory, 'ANALYSIS_INDEX.json': Path(self.plan['index']),
            'HANDLERS.c': self.export.excerpt(handlers, 'The decompiled code of every handler function'),
            **{'notes/' + name + '.md': text for name, text in self.task_notes.items()},
            'TASKS.json': self.tasks_json, **self.kit(), **(repair or {}), **documents,
            'TASK.json': {'schema': 'interpret-task/1', 'kind': 'cross-check', 'repair': bool(repair),
                          'writes': ['model.json', 'replies.json', 'contract.json', 'NOTES.md'], **entry}},
            editable=assembled, with_seal=True)
        try:
            defects = _validation(json.loads((workspace / 'model.json').read_text(encoding='utf-8')))['defects']
            why = _defect(defects[0]) if defects else None
        except (OSError, ValueError) as error:
            why = 'model.json cannot be read: ' + type(error).__name__
        if why is not None:
            self.notes.append('The cross-check left no usable result (' + why + '); the assembled files are handed '
                              'off as the configurator assembled them.')
            self.deliver({**assembled, 'NOTES.md': '\n\n'.join(self.notes) + '\n'})
            return
        left = {}
        for path in sorted(workspace.rglob('*')):
            name = path.relative_to(workspace).as_posix()
            if (path.is_file() and name not in sealed and not path.name.startswith('_agent')
                    and '__pycache__' not in path.parts):
                if (self.source / name).exists():
                    self.notes.append('The cross-check left ' + name + ', which would replace a sealed input of the '
                                      'attempt; it is not handed off.')
                    continue
                left[name] = path.read_bytes()
        self.deliver(left)

    def deliver(self, files):
        """Leave the attempt's output in the single worker's sealed workspace, where the interpret check collects it
        as it collects a worker's."""
        _write(self.source, files)

    def keep(self, name, value):
        """Keep `value` as `name` in this attempt's records and return where, relative to the run's folder, with its
        hash."""
        data = (json.dumps(value, indent=1, sort_keys=True) + '\n').encode('utf-8')
        path = self.records / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {'path': path.relative_to(self.run_dir).as_posix(), 'sha256': hashlib.sha256(data).hexdigest()}

    def load(self, kept):
        """A record an earlier attempt kept, as long as it is still the bytes its hash names."""
        path = self.run_dir / kept['path']
        data = path.read_bytes() if path.is_file() else b''
        if hashlib.sha256(data).hexdigest() != kept['sha256']:
            raise IntegrityError('An interpret task record changed after it was kept: ' + str(path))
        return json.loads(data)

    def record(self):
        """Keep the attempt's record.json, which the next repair starts from, and return where with its hash."""
        return self.keep('record.json', {'schema': RECORD_SCHEMA, 'revision': self.plan['revision'],
                                         'attempt': self.plan['attempt'], 'inventory': self.kept['inventory'],
                                         'fragments': self.kept['fragments'],
                                         'repair_of': (self.plan.get('previous') or {}).get('record'),
                                         'tasks': [{**task, 'status': result.get('status')}
                                                   for task, result in self.results]})

    def combined(self):
        """The attempt's runtime result for the stage: its tasks' results together, their token use summed."""
        last = self.results[-1][1]
        counters = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
        usages = [result.get('usage') for _, result in self.results if isinstance(result.get('usage'), dict)]
        usage = {key: sum(u[key] for u in usages) if all(type(u.get(key)) is int for u in usages) else None
                 for key in counters} if usages else None
        return {**{key: last.get(key) for key in ('runtime', 'runtime_version', 'provider', 'model', 'settings',
                                                  'boundary')},
                'status': 'completed', 'report': None, 'usage': usage,
                'elapsed_seconds': sum(result.get('elapsed_seconds') or 0 for _, result in self.results),
                'tasks': [{**task, 'status': result.get('status'), 'usage': result.get('usage')}
                          for task, result in self.results]}

    def kit(self):
        """The model document, the contract guide and tool, the validator and the qualifier with the runtime they use,
        as the single worker's workspace has them."""
        return {name: self.source / name for name in KIT}

    def documents(self, commands=None):
        """A task's supplementary documents (07-B4) by workspace name, and the `documents` entry of its TASK.json ({}
        without documents): the inventory's are taken from every forwarded page, a handler task's and the cross-check's
        from the pages the inventory's `commands` cite. A citation that names no forwarded page is ignored, with a
        note."""
        cited = None if commands is None else [(command['name'], path) for command in commands
                                               for path in supplementary_documents.citations(command.get('evidence'))]
        inputs, entry, unknown = supplementary_documents.for_task(
            self.source, None if cited is None else [path for _, path in cited])
        for name, path in cited or ():
            if path in unknown and (name, path) not in self.ignored:
                self.ignored.add((name, path))
                self.notes.append('The inventory cites ' + path + ' for ' + name + ', which names no forwarded '
                                  'document page; the citation is ignored.')
        return inputs, ({'documents': entry} if entry else {})

    def task(self, task, instructions, inputs, editable=None, with_seal=False):
        """The workspace of one task (and its seal, when `with_seal`), once the task's fresh worker completed with
        every sealed input as it was given. The workspace is new, under the system temporary folder, with the task's
        `inputs` ({name: a Path, text, bytes or a JSON value}) and its instructions sealed and its `editable` files
        written unsealed."""
        workspace = Path(tempfile.mkdtemp(prefix='gd-task-')).resolve()
        _write(workspace, {**inputs, instructions: _instructions(instructions)})
        sealed = {path.relative_to(workspace).as_posix(): _sha(path) for path in workspace.rglob('*') if path.is_file()}
        _write(workspace, editable or {})
        result = self.launch(task, workspace, _prompt(workspace, instructions))
        self.results.append((task, result))
        if result.get('status') != 'completed':
            raise _Stopped(result)
        changed = sorted(name for name, sha in sealed.items() if _sha(workspace / name) != sha)
        if changed:
            raise IntegrityError('The ' + _named(task) + ' changed its sealed inputs: ' + ', '.join(changed))
        if (workspace / 'NOTES.md').is_file():
            self.task_notes['-'.join(_safe(part) for part in (task['kind'], task.get('command')) if part)] = \
                (workspace / 'NOTES.md').read_text(encoding='utf-8', errors='replace')
        return (workspace, sealed) if with_seal else workspace


def _read_inventory(path):
    """An inventory task's inventory.json and None, or None and why it cannot be used."""
    from interface_runtime.engine import SCHEMAS
    from interface_runtime.transports import validate_channel
    try:
        inventory = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None, 'The inventory cannot be read: the task wrote no inventory.json'
    except (OSError, ValueError) as error:
        return None, 'The inventory cannot be read: ' + type(error).__name__ + ': ' + str(error)[:200]
    if not isinstance(inventory, dict):
        return None, 'The inventory cannot be read: it is not a JSON object'
    commands = inventory.get('commands')
    if not isinstance(commands, list) or not commands:
        return None, 'The inventory lists no command'
    names = [command.get('name') if isinstance(command, dict) else None for command in commands]
    problem = None
    if len(commands) > MAX_COMMANDS:
        problem = 'it lists %d commands, more than the model\'s %d' % (len(commands), MAX_COMMANDS)
    elif inventory.get('schema') not in SCHEMAS:
        problem = 'its schema is not an interface-model tag the kit knows'
    elif (not isinstance(inventory.get('device'), dict)
          or not isinstance(inventory['device'].get('name'), (str, type(None)))):
        problem = 'its device has no name or null'
    elif not all(isinstance(name, str) and name for name in names) or len(set(names)) != len(names):
        problem = 'every command needs a name of its own'
    elif not all(isinstance(command.get('handlers'), list) and all(isinstance(a, str) for a in command['handlers'])
                 for command in commands):
        problem = "every command's handlers must be a list of function addresses"
    elif inventory.get('identity') not in names:
        problem = 'its identity is not one of its commands'
    else:
        try:
            validate_channel(inventory.get('channel'))
        except (ValueError, OSError, TypeError) as error:
            problem = 'its channel is not one the model accepts (' + str(error)[:200] + ')'
    if problem:
        return None, 'The inventory cannot be read: ' + problem
    return inventory, None


def _read_fragment(path):
    """A handler task's fragment.json and None, or None and why it cannot be used."""
    try:
        fragment = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None, 'it wrote no fragment.json'
    except (OSError, ValueError) as error:
        return None, 'fragment.json cannot be read: ' + type(error).__name__ + ': ' + str(error)[:200]
    if not isinstance(fragment, dict) or fragment.get('schema') != FRAGMENT_SCHEMA:
        return None, 'fragment.json is not an ' + FRAGMENT_SCHEMA + ' object'
    shapes = {'operation': str, 'model': dict, 'provenance': list, 'replies': dict, 'contract': dict}
    missing = [key for key, shape in shapes.items() if not isinstance(fragment.get(key), shape)]
    if missing or not fragment['operation']:
        return None, 'fragment.json lacks ' + ', '.join(missing or ['operation'])
    return fragment, None


def _identity_first(inventory):
    return sorted(inventory['commands'], key=lambda command: command['name'] != inventory['identity'])


def _model(frame, fragments):
    """A model of `frame` (schema, device, channel and identity) with the operations and provenance of `fragments`."""
    return {**frame, 'operations': {fragment['operation']: fragment['model'] for fragment in fragments},
            'provenance': [entry for fragment in fragments for entry in fragment['provenance']]}


def _from_model(model, name):
    """Operation `name` of `model` as a fragment's operation, model entry and provenance."""
    return {'operation': name, 'model': (model.get('operations') or {}).get(name),
            'provenance': [entry for entry in model.get('provenance') or () if _about(entry, name)]}


def _about(entry, name):
    item = entry.get('item') if isinstance(entry, dict) else None
    return isinstance(item, str) and (item == 'operations.' + name or item.startswith('operations.' + name + '.'))


def _replaced(files, fragments):
    """The previous final files with each re-run operation's model entry, provenance, replies and contract entry in
    place of its own."""
    files = copy.deepcopy(files)
    model = files['model.json']
    for name, fragment in fragments.items():
        model['operations'][name] = fragment['model']
        model['provenance'] = ([entry for entry in model['provenance'] if not _about(entry, name)]
                               + fragment['provenance'])
        if isinstance((files.get('replies.json') or {}).get('operations'), dict):
            files['replies.json']['operations'][name] = fragment['replies']
        if isinstance((files.get('contract.json') or {}).get('operations'), dict):
            files['contract.json']['operations'][name] = fragment['contract']
            files['contract.json']['provenance'] = model['provenance']
    if 'replies.json' in files:
        files['replies.json'] = _replies_schema(files['replies.json'])
    return files


def _replies_schema(replies):
    """`replies` under interface-replies/2 when any reply is whole-frame bytes, else /1."""
    binary = any(isinstance(reply, dict) for entry in (replies.get('operations') or {}).values()
                 if isinstance(entry, dict) for reply in entry.get('steps') or ())
    return {**replies, 'schema': 'interface-replies/2' if binary else 'interface-replies/1'}


def _validation(model):
    """The model's validation as the workers' validate.py gives it; a fragment's malformed JSON is a defect too."""
    from interface_runtime.qualify import validate_candidate
    try:
        return validate_candidate(model)
    except Exception as error:  # A worker wrote the model: whatever it trips over is a defect of the model.
        return {'ok': False, 'defects': [{'where': 'model', 'what': type(error).__name__ + ': ' + str(error)[:200]}]}


def _defect(defect):
    if isinstance(defect, dict):
        return (str(defect.get('where')) + ': ' + str(defect.get('what')))[:300]
    return str(defect)[:300]


def _write(folder, files):
    """Write `files` ({name: a Path to copy, text, bytes or a JSON value}) under `folder`."""
    for name, value in files.items():
        target = Path(folder) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, Path):
            shutil.copytree(value, target) if value.is_dir() else shutil.copyfile(value, target)
        else:
            text = value if isinstance(value, (bytes, str)) else json.dumps(value, indent=1) + '\n'
            target.write_bytes(text if isinstance(text, bytes) else text.encode('utf-8'))


def _safe(name):
    """A command's name as part of a file name."""
    return re.sub(r'[^A-Za-z0-9_-]', '_', str(name))[:64] or '_'


def _named(task):
    return {'inventory': 'inventory task', 'cross-check': 'cross-check task'}.get(
        task['kind'], 'handler task for ' + str(task.get('command')))


def _sha(path):
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _prompt(workspace, name):
    return ('Read ' + str(workspace / name) + ' and do exactly what it says. Work only in this workspace, '
            + str(workspace) + '. Every file supplied in it is a sealed input: read it and leave it unchanged.')
