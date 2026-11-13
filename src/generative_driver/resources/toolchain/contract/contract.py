"""Draft and check the driver contract in an interpret workspace; dependencies are sealed beside this file.

`python3 contract.py draft [folder]` writes contract.json from model.json as the kit imports a model, and never
overwrites one. `python3 contract.py check [folder]` reports whether contract.json describes model.json, with advice,
and exits nonzero only when it does not (CONTRACT.md)."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent/'_toolkit'))
from interface_runtime.contracts import from_model, compile_contract, describes, check_preconditions  # noqa: E402
from interface_runtime.evidence import read_json  # noqa: E402

NUMERIC = ('integer', 'number')
UNIT_NOT_STATED = 'unit_not_stated'


def draft(folder):
    contract = from_model(read_json(folder/'model.json', 1048576))[0]
    try:
        with (folder/'contract.json').open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(contract, indent=2, allow_nan=False) + '\n')
    except FileExistsError:
        return {'ok': False, 'error': 'contract.json exists, and draft never overwrites it: edit it, or remove it '
                                      'to draft again'}
    return {'ok': True, 'wrote': 'contract.json',
            'next': 'Replace each placeholder description as CONTRACT.md describes, then run python3 contract.py check'}


def _mismatches(contract, model, imported):
    """Where the contract does not describe the model: each operation with its first difference, then the rest."""
    try:
        found = describes(contract, model)
    except ValueError as error:
        return [{'where': 'contract.json', 'difference': str(error)}]
    out = [{'where': 'operations.' + name, 'difference': why} for name, why in found.items() if why]
    for name, why in found.items():
        if why is None and contract['operations'][name].get('preconditions'):
            try:
                check_preconditions(contract, model, name)
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                out.append({'where': 'operations.' + name + '.preconditions', 'difference': str(error)})
    out += [{'where': 'operations.' + name, 'difference': 'model.json has no such operation'}
            for name in contract['operations'] if name not in model['operations']]
    out += [{'where': key, 'difference': 'the contract gives ' + json.dumps(contract.get(key), sort_keys=True)[:200]
             + ' where model.json has ' + json.dumps(imported[key], sort_keys=True)[:200]}
            for key in ('schema', 'device', 'identity', 'provenance') if contract.get(key) != imported[key]]
    return out


def _advice(contract, imported):
    """What a finished contract would not still hold: draft descriptions, and numbers whose unit is null unexplained."""
    advice = []
    operations = contract.get('operations') if type(contract) is dict else None
    for name, operation in (operations.items() if type(operations) is dict else ()):
        draft = imported['operations'].get(name)
        if type(operation) is not dict or draft is None:
            continue
        if operation.get('description') == draft['description']:
            advice.append('operations.' + name + '.description is still the draft placeholder')
        for group in ('parameters', 'outputs'):
            definitions = operation.get(group)
            for key, definition in (definitions.items() if type(definitions) is dict else ()):
                where = 'operations.' + name + '.' + group + '.' + key
                if type(definition) is not dict:
                    continue
                if key in draft[group] and definition.get('description') == draft[group][key]['description']:
                    advice.append(where + '.description is still the draft placeholder')
                schema = definition.get('schema')
                caveats = definition.get('uncertainties')
                explained = type(caveats) is list and any(type(item) is dict and item.get('code') == UNIT_NOT_STATED
                                                          for item in caveats)
                if (group == 'outputs' and type(schema) is dict and schema.get('type') in NUMERIC
                        and definition.get('unit') is None and not explained):
                    advice.append(where + '.unit is null with no unit_not_stated uncertainty: write the unit the '
                                  'evidence states, or add one that says where you searched')
    return advice


def check(folder):
    model = read_json(folder/'model.json', 1048576)
    imported, protocol = from_model(model)
    if not (folder/'contract.json').is_file():
        return {'ok': False, 'describes_model': False, 'error': 'no contract.json: run python3 contract.py draft first'}
    try:
        contract = read_json(folder/'contract.json', 1048576)
    except ValueError as error:
        return {'ok': False, 'describes_model': False, 'mismatches': [
            {'where': 'contract.json', 'difference': 'cannot be read as JSON: ' + str(error)}], 'advice': []}
    try:
        describes_model = compile_contract(contract, protocol) == model
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        describes_model = False
    mismatches = [] if describes_model else _mismatches(contract, model, imported) or [
        {'where': 'contract.json', 'difference': 'does not compile back to model.json'}]
    return {'ok': describes_model, 'describes_model': describes_model, 'mismatches': mismatches,
            'advice': _advice(contract, imported)}


def main(argv):
    if len(argv) not in (1, 2) or argv[0] not in ('draft', 'check'):
        report = {'ok': False, 'error': 'usage: python3 contract.py draft|check [model folder]'}
    else:
        folder = Path(argv[1] if len(argv) > 1 else '.').resolve()
        try:
            report = (draft if argv[0] == 'draft' else check)(folder)
        except (OSError, ValueError, TypeError, KeyError) as error:
            report = {'ok': False, 'error': 'model.json cannot be read as an interface model: ' + str(error)}
    print(json.dumps(report, indent=1, allow_nan=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
