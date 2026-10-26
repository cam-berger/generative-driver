"""Authenticated, evaluator-owned upstream case inventories.

This is data validation, not a plugin loader. Native families remain allowlisted
in instruments.py; neither a catalog nor an encrypted bundle selects Python code.
"""
import copy
from .snapshots import canonical_digest


def hydrate_truth(payload):
    from .behavior import _check_contract
    from .calibration import apply_mutation
    from .zephyr_modbus import REQUIRED_MUTANTS
    try:
        fields = {'schema', 'case_id', 'family', 'inventory_state', 'images', 'source', 'build',
                  'recipe', 'phases', 'references', 'mutations', 'public_mission'}
        if (not isinstance(payload, dict) or set(payload) - fields - {'calibration'} or fields - set(payload)
                or payload['schema'] != 'benchmark-upstream-truth/1'
                or payload['case_id'] != 'zephyr-modbus-v1' or payload['family'] != 'zephyr-modbus'
                or payload['inventory_state'] not in ('pending', 'qualified')):
            raise ValueError('Invalid upstream truth identity')
        if set(payload['references']) != {'a', 'b'} or set(payload['phases']) != {'diagnostic', 'final'}:
            raise ValueError('Upstream truth requires two references and disjoint phases')
        for phase in payload['phases'].values():
            _check_contract(phase['contract'])
            actions = phase['actions']
            wanted = [row['id'] for row in phase['contract']['checks']]
            observed = [key for action in actions if action['kind'] == 'observe' for key in action['checks']]
            if (not actions or actions[0]['kind'] != 'reset' or not any(a['kind'] == 'call' for a in actions)
                    or len(observed) != len(set(observed)) or set(wanted) != set(observed)):
                raise ValueError('Upstream phase lacks complete reset/call/observe coverage')
        mutants = payload['mutations']['mutants']
        if len(mutants) != len(REQUIRED_MUTANTS) or {m['id'] for m in mutants} != REQUIRED_MUTANTS:
            raise ValueError('Upstream mutant coverage changed')
        for mutant in mutants:
            reference = payload['references'][mutant['reference']]
            apply_mutation(reference['model'], reference['capabilities'], mutant)
            checks = {c['id'] for c in payload['phases'][mutant['phase']]['contract']['checks']}
            if not mutant['expected_failed_checks'] or not set(mutant['expected_failed_checks']) <= checks:
                raise ValueError('Mutant does not target a declared functional requirement')
        from .missions import load_mission
        if payload['public_mission'] != load_mission(payload['case_id']):
            raise ValueError('Public mission changed; native qualification must be repeated')
        groups = {'source': payload['source'], 'contract': payload['phases'], 'recipe': payload['recipe'],
                  'references': payload['references'], 'mutations': payload['mutations'], 'build': payload['build']}
        hashes = {key: canonical_digest(value) for key, value in groups.items()}
        hashes['inventory'] = canonical_digest({key: payload[key] for key in sorted(fields - {'inventory_state'})})
        return {**copy.deepcopy(payload), 'input_hashes': hashes,
                'contracts': {'time_policy': {'sample_settle_seconds': .1}}}
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Malformed upstream truth inventory') from error
