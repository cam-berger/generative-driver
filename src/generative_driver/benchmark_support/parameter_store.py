"""Evaluator-owned volatile parameter-store transaction oracle."""
from .family_support import integer, integer_vector, private_phase, signed16


def contract(pin, truth, phase):
    return private_phase(pin, truth, phase)["contract"]


def build_plan(pin, truth, phase):
    return private_phase(pin, truth, phase)["actions"]


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
    """Public mission: commit B1=-7, stage B1=9, abort, read committed B1."""
    tasks=capabilities['tasks']
    ordered=[]
    for event in events:
        if event['result'].get('ok') is True:
            for task in ('update','stage','abort','read'):
                if event.get('operation')==tasks[task]['operation']:
                    ordered.append((task,event))
    if [name for name,_ in ordered]!=['update','stage','abort','read']:return False
    first,staged,aborted,last=[event for _,event in ordered]
    before=first.get('before_observation',{}); final=last['observation']
    if type(before.get('generation')) is not int:return False
    cells=['cell_'+bank+'_'+str(i) for bank in ('A','B') for i in range(4)]
    if any(final.get(name)!=(-7 if name=='cell_B_1' else before.get(name)) for name in cells):return False
    return (final.get('generation')==before['generation']+1
        and first['observation'].get('cell_B_1')==-7
        and staged['observation'].get('pending_active') is True
        and staged['observation'].get('pending_B_1')==9
        and aborted['observation'].get('pending_active') is False
        and final.get('pending_active') is False
        and last['result'].get('outputs',{}).get(tasks['read']['outputs']['value']['output'])==-7)
