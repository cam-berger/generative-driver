"""Evaluator-owned volatile parameter-store transaction oracle."""
from .family_support import integer, integer_vector, private_phase, signed16


def contract(pin, truth, phase):
    selected = private_phase(pin, truth, phase)
    if "required_scenarios" in selected["contract"]:
        validate_phase(selected)
    return selected["contract"]


def build_plan(pin, truth, phase):
    selected = private_phase(pin, truth, phase)
    if "required_scenarios" in selected["contract"]:
        validate_phase(selected)
    return selected["actions"]


def expected_state(initial: list[int], actions: list[dict]) -> dict:
    committed = integer_vector({"initial": initial}, "initial", 8)
    if any(not -32768 <= value <= 32767 for value in committed):
        raise ValueError("Initial value outside int16")
    pending, bank, generation = list(committed), None, 0
    for action in actions:
        kind = action["kind"]
        if kind == "begin":
            if bank is not None or action.get("bank") not in ("A", "B"):
                raise ValueError("Invalid begin")
            bank = action["bank"]
            pending = list(committed)
        elif kind == "put":
            slot, value = action.get("slot"), action.get("value")
            if bank is None or type(slot) is not int or not 0 <= slot < 4:
                raise ValueError("Invalid transaction slot")
            if type(value) is not int or not -32768 <= value <= 32767:
                raise ValueError("Invalid transaction value")
            pending[(4 if bank == "B" else 0) + slot] = value
        elif kind in ("commit", "abort"):
            if bank is None:
                raise ValueError("No pending transaction")
            if kind == "commit":
                committed = list(pending)
                generation += 1
            pending = list(committed)
            bank = None
        else:
            raise ValueError("Unknown transition")
    return {"committed": committed, "pending": pending,
            "pending_active": bank is not None, "generation": generation}


def observations(raw, canonical_task_result, inputs):
    values = raw["values"]
    committed = [signed16(value) for value in integer_vector(values, "committed", 8)]
    pending = [signed16(value) for value in integer_vector(values, "pending", 8)]
    generation = integer(values, "generation")
    active = integer(values, "pending_active")
    if generation < 0 or active not in (0, 1):
        raise ValueError("Invalid transaction monitor state")
    result = {"generation": generation, "pending_active": active == 1,
              "operation_ok": canonical_task_result.get("ok") is True,
              "value": canonical_task_result.get("outputs", {}).get("value")}
    for index in range(8):
        label = ("A" if index < 4 else "B") + "_" + str(index % 4)
        result["cell_" + label] = committed[index]
        result["pending_" + label] = pending[index]
    return result


observations.monitor_units = {
    **{prefix + bank + "_" + str(slot): "configuration-unit"
       for prefix in ("cell_", "pending_") for bank in ("A", "B") for slot in range(4)},
    "generation": "count", "pending_active": "boolean",
}


def reuse_passed(events, capabilities):
    """Accept ordered observed effects, independent of transaction composition."""
    if not events:return False
    before=events[0].get('before_observation',{})
    cells=['cell_'+bank+'_'+str(i) for bank in ('A','B') for i in range(4)]
    if (type(before.get('generation')) is not int or before.get('pending_active') is not False
            or any(type(before.get(name)) is not int for name in cells)):return False
    generation=before['generation'];progress=0
    read=capabilities['tasks']['read']
    for event in events:
        observed=event.get('observation',{})
        if (any(observed.get(name)!=before[name] for name in cells if name!='cell_B_1')
                or observed.get('generation') not in (generation,generation+1)):
            return False
        if progress and (observed.get('cell_B_1')!=-7 or observed.get('generation')!=generation+1):
            return False
        if event['result'].get('ok') is not True:continue
        if progress==0:
            if (observed.get('cell_B_1')==-7 and observed.get('generation')==generation+1
                    and observed.get('pending_active') is False):progress=1
        elif progress==1:
            if observed.get('pending_active') is True and observed.get('pending_B_1')==9:progress=2
        elif progress==2:
            if observed.get('pending_active') is False:progress=3
        elif (event.get('operation')==read['operation'] and observed.get('pending_active') is False
                and event['result'].get('outputs',{}).get(read['outputs']['value']['output'])==-7):
            progress=4
    return progress==4 and events[-1]['observation'].get('pending_active') is False


REQUIRED_SCENARIOS = {'idle-update', 'pending-same-bank-update', 'pending-different-bank-update',
                      'stage-commit', 'stage-abort', 'rejection'}


def validate_phase(phase):
    """Require named reset-delimited episodes with complete independent state evidence."""
    contract = phase['contract']
    names = contract.get('required_scenarios', [])
    if set(names) != REQUIRED_SCENARIOS or len(names) != len(REQUIRED_SCENARIOS):
        raise ValueError('Incomplete parameter-store scenarios')
    state = {prefix+bank+'_'+str(slot) for prefix in ('cell_', 'pending_') for bank in 'AB' for slot in range(4)}
    state |= {'generation', 'pending_active'}
    sequences = {'idle-update':['update'], 'pending-same-bank-update':['stage','update'],
                 'pending-different-bank-update':['stage','update'], 'stage-commit':['stage','commit'],
                 'stage-abort':['stage','abort']}
    episodes = {}
    current = None
    for action in phase['actions']:
        label = action.get('episode')
        if action['kind'] == 'reset':
            if label in names and label in episodes:
                raise ValueError('Required scenario has multiple reset-delimited episodes')
            current = []
            episodes.setdefault(label, []).append(current)
        elif current is None or label != current[0].get('episode'):
            raise ValueError('Episode actions must follow their own reset contiguously')
        current.append(action)
    for name in names:
        selected = episodes.get(name, [])
        actions = selected[0] if len(selected) == 1 else []
        checks = [c for c in contract['checks'] if c.get('scenario') == name]
        if not actions or actions[0]['kind'] != 'reset' or sum(a['kind']=='reset' for a in actions)!=1:
            raise ValueError('Scenario requires one initial reset')
        calls = [a for a in actions if a['kind']=='call']
        if not calls or (name in sequences and [a['task'] for a in calls] != sequences[name]):
            raise ValueError('Scenario lacks required task sequence')
        if name.startswith('pending-'):
            same = calls[0]['inputs'].get('bank') == calls[1]['inputs'].get('bank')
            if same != (name=='pending-same-bank-update'):
                raise ValueError('Pending scenario bank mismatch')
        ids = {c['id'] for c in checks}
        observed_ids = [i for a in actions if a['kind']=='observe' for i in a['checks']]
        if not ids or set(observed_ids)!=ids or len(observed_ids)!=len(set(observed_ids)):
            raise ValueError('Scenario lacks complete observations')
        last_call = max(i for i, action in enumerate(actions) if action['kind']=='call')
        ending_ids = {identifier for action in actions[last_call+1:] if action['kind']=='observe'
                      for identifier in action['checks']}
        monitor = {c['id'].rsplit('/',1)[-1] for c in checks
                   if c['channel']=='independent-monitor' and c['id'] in ending_ids}
        if not state <= monitor:
            raise ValueError('Scenario lacks independent complete ending state')
        if name=='rejection' and not any(c['id'].endswith('/operation_rejected') and c['expected'] is True
                and c['channel']=='runtime-transcript' and c['id'] in ending_ids for c in checks):
            raise ValueError('Scenario lacks complete rejection evidence')
    all_observed = [i for a in phase['actions'] if a['kind']=='observe' for i in a['checks']]
    if set(all_observed)!={c['id'] for c in contract['checks']} or len(all_observed)!=len(set(all_observed)):
        raise ValueError('Unobserved or duplicate scenario checks')
