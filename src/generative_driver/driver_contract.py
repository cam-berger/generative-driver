"""The driver contract in a generic run: interpret workers are taught to write it, and emit packages what of it
matches the model (docs/features/kit-fixes/05-driver-package.md, 05-B6 and 05-B8).

Emit keeps each operation of the worker's contract that compiles back to the model and imports the rest from the
model, so a repair that leaves part of the contract behind its model costs only that part. Benchmark runs use none of
this; their interpret instructions stay as they were."""
import json
from pathlib import Path

from .toolkit import resources_root

# What a generic interpret worker is told, in its TASKS.json and, on a repair, in its REPAIR_CONTEXT.json.
TASK = ('Write contract.json as CONTRACT.md describes: the driver contract, all a fresh agent reads about the driver. '
        'Start from `python3 contract.py draft` and run `python3 contract.py check` before finishing.')
REPAIR = ('Keep contract.json in step with the revised model.json: start from previous-model/contract.json, write the '
          'revised contract.json at the workspace root and run `python3 contract.py check`.')
_IMPORTED = 'the contract the kit imports from the model'


def interpret_inputs():
    """The sealed inputs a generic interpret workspace gets for the contract, by their names in the workspace."""
    folder = resources_root() / 'toolchain' / 'contract'
    return {'CONTRACT.md': str(folder / 'CONTRACT.md'), 'contract.py': str(folder / 'contract.py')}


def emit_inputs(inputs):
    """Write the contract emit packages, with the protocol derived from the model, into the interpreted model's folder
    among an emit stage's copied `inputs`, and return the notes that say which operations got the contract the kit
    imports from the model, and why. A folder whose model the runtime refuses is left as it is: emit refuses it."""
    from interface_runtime.contracts import from_model
    from interface_runtime.evidence import read_json
    folder = next((Path(path) for path in inputs if (Path(path) / 'model.json').is_file()), None)
    if folder is None:
        return []
    try:
        model = read_json(folder / 'model.json', 1048576)
        imported, protocol = from_model(model)
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return []
    if not (folder / 'contract.json').is_file():
        contract, notes = imported, ['The interpreted model came with no contract.json, so every operation has '
                                     + _IMPORTED + '.']
    else:
        contract, notes = _merged(folder / 'contract.json', model, imported, protocol)
    # A protocol.json the worker wrote is replaced too: emit_package refuses a lone contract, and the protocol is the
    # model's own.
    for name, document in (('contract.json', contract), ('protocol.json', protocol)):
        (folder / name).write_text(json.dumps(document, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    return notes


def _merged(path, model, imported, protocol):
    """The worker's contract at `path` with each operation that does not match the model replaced by the imported
    entry, and the notes that say which and why."""
    from interface_runtime.contracts import check_preconditions, compile_contract, describes
    from interface_runtime.evidence import read_json
    try:
        authored = read_json(path, 1048576)
        found = describes(authored, model)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        return imported, ['contract.json cannot be read as a contract (' + str(error) + '), so every operation has '
                          + _IMPORTED + '.']
    why = {name: ('contract.json leaves it out' if reason == 'the contract leaves it out' else
                  'its contract.json entry does not compile to the model: ' + reason)
           for name, reason in found.items() if reason}
    merged = {**imported, 'operations': {name: authored['operations'][name] if name not in why else entry
                                         for name, entry in imported['operations'].items()}}
    # An entry is kept or replaced whole, preconditions included: replacing one can break a precondition that observes
    # it, so the check repeats until nothing changes.
    changed = True
    while changed:
        changed = False
        for name in [name for name in merged['operations'] if name not in why]:
            try:
                check_preconditions(merged, model, name)
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                merged['operations'][name] = imported['operations'][name]
                why[name] = 'its precondition no longer holds with the other operations packaged: ' + str(error)
                changed = True
    try:
        if compile_contract(merged, protocol) != model:
            raise ValueError('it compiles to another model')
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as error:
        return imported, ['The merged contract does not compile to the model (' + str(error) + '), so every '
                          'operation has ' + _IMPORTED + '.']
    notes = ['The package gives operation ' + name + ' ' + _IMPORTED + ', because ' + reason + '.'
             for name, reason in why.items()]
    extra = [name for name in authored['operations'] if name not in model['operations']]
    notes += ['contract.json describes operation ' + str(name) + ', which the model does not have; it is left out.'
              for name in extra]
    # Matching entirely, the worker's document is packaged as it is.
    entire = (not why and not extra and set(authored) == set(imported)
              and all(authored[key] == imported[key] for key in imported if key != 'operations'))
    return (authored if entire else merged), notes
