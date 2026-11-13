"""Generated named-operation interface; live bindings are always caller supplied."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    from interface_runtime.package import verify_package
    from interface_runtime.evidence import read_json
    checked = verify_package(ROOT)
    if not checked['ok']:
        raise ValueError(str(checked['defects']))
    if checked['manifest']['schema'] == 'interface-package/2':
        from interface_runtime.contracts import compile_contract
        model = compile_contract(read_json(ROOT/'driver/contract.json'), read_json(ROOT/'driver/protocol.json'))
    else:
        model = read_json(ROOT/'driver/model.json', 1048576)
    return model, checked['manifest']


def describe():
    from interface_runtime.transports import capabilities
    from interface_runtime.evidence import read_json, probe_coverage, COVERAGE_SCOPE
    model, manifest = _load()
    return {'schema':model['schema'],'device':model['device'],'channel':model['channel'],
            'identity':model['identity'],'operations':model['operations'],
            'capabilities':capabilities(),'source_evidence_kind':manifest['source_evidence_kind'],
            'probe_coverage':probe_coverage(model,read_json(ROOT/'probe.json')),'coverage_scope':COVERAGE_SCOPE,
            **({'contract':read_json(ROOT/'driver/contract.json')}
               if manifest['schema'] == 'interface-package/2' else {})}


def execute(operation, parameters=None, *, binding=None, allow_effects=(), replay=None):
    model, manifest = _load()
    if manifest['schema'] == 'interface-package/2':
        from interface_runtime.driver import execute_package
        return execute_package(ROOT, operation, parameters, binding=binding,
                               allow_effects=allow_effects, replay=replay)
    from interface_runtime.evidence import run_samples
    run = run_samples(model, operation, parameters, binding=binding, allow_effects=allow_effects, replay=replay)
    return {**run['results'][0], 'evidence_kind':run['evidence_kind'],
            'timing_policy':run['timing_policy'],
            'model_sha256':manifest['model_sha256'],'transactions':run['transactions']}


def session(*, binding=None, allow_effects=()):
    """One connection for several operations; a device that resets on open is not restarted between them."""
    model, manifest = _load()
    if manifest['schema'] == 'interface-package/2':
        from interface_runtime.driver import PackageSession
        return PackageSession(ROOT, binding=binding, allow_effects=allow_effects)
    from interface_runtime.engine import Session
    return Session(model, binding=binding, allow_effects=allow_effects)


def sequence(steps, *, binding=None, allow_effects=()):
    """Run [{operation, parameters}] in order over one connection, stopping at the first failure."""
    if type(steps) is not list or not steps or any(type(s) is not dict or type(s.get('operation')) is not str for s in steps):
        raise ValueError('sequence requires a nonempty list of {operation, parameters}')
    results = []
    with session(binding=binding, allow_effects=allow_effects) as current:
        for step in steps:
            result = current.execute(step['operation'], step.get('parameters') or {})
            results.append(result)
            if not result['ok']:
                break
    return {'ok': all(r['ok'] for r in results) and len(results) == len(steps), 'results': results}


def replay_probe():
    from interface_runtime.evidence import read_json, check_probe
    model, manifest = _load()
    return check_probe(model, read_json(ROOT/'probe.json'), manifest['model_sha256'])
