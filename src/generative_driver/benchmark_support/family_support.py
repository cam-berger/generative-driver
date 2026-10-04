"""Pure evaluator helpers for family-specific observations and private phases."""
from copy import deepcopy


def integer(values, name):
    value = values[name] if name in values else None
    if type(value) is not int:
        raise ValueError("Unavailable integer observation: " + name)
    return value


def signed16(value):
    if type(value) is not int or not 0 <= value <= 65535:
        raise ValueError("Unavailable unsigned 16-bit register")
    return value - 65536 if value & 32768 else value


def integer_vector(values, name, length):
    value = values.get(name)
    if not isinstance(value, list) or len(value) != length or any(type(x) is not int for x in value):
        raise ValueError("Unavailable integer vector: " + name)
    return list(value)


def private_phase(pin, truth, phase):
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    if phase not in ('diagnostic', 'final'):
        raise ValueError('Unknown behavior phase')
    if truth["family"] != pin["family"]:
        raise ValueError("Family evidence mismatch")
    phases = truth["phases"]
    result = deepcopy(phases[phase])
    result["contract"]["artifact_sha256"] = truth["artifact_sha256"]
    for check in result["contract"]["checks"]:
        check["revision"] = pin.get("revision", 0)
    seed = pin.get('case_seed', 0)
    if seed and result['actions'] and result['actions'][0]['kind'] == 'reset':
        episodes = []
        for action in result['actions']:
            if action['kind'] == 'reset':
                episodes.append([])
            episodes[-1].append(action)
        offset = seed % len(episodes)
        result['actions'] = [action for episode in episodes[offset:] + episodes[:offset] for action in episode]
    return result
