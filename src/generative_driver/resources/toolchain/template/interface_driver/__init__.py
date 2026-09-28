"""Generated named-operation interface; live bindings are always caller supplied."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load():
    from interface_runtime.package import verify_package
    from interface_runtime.evidence import read_json
    checked = verify_package(ROOT)
    if not checked['ok']:
        raise ValueError(str(checked['defects']))
    return read_json(ROOT/'driver/model.json', 1048576), checked['manifest']


def describe():
    from interface_runtime.transports import capabilities
    from interface_runtime.evidence import read_json, probe_coverage, COVERAGE_SCOPE
    model, manifest = _load()
    return {'schema':model['schema'],'device':model['device'],'channel':model['channel'],
            'identity':model['identity'],'operations':model['operations'],
            'capabilities':capabilities(),'source_evidence_kind':manifest['source_evidence_kind'],
            'probe_coverage':probe_coverage(model,read_json(ROOT/'probe.json')),'coverage_scope':COVERAGE_SCOPE}


def execute(operation, parameters=None, *, binding=None, allow_effects=(), replay=None):
    from interface_runtime.evidence import run_samples
    model, manifest = _load()
    run = run_samples(model, operation, parameters, binding=binding, allow_effects=allow_effects, replay=replay)
    return {**run['results'][0], 'evidence_kind':run['evidence_kind'],
            'timing_policy':run['timing_policy'],
            'model_sha256':manifest['model_sha256'],'transactions':run['transactions']}


def replay_probe():
    from interface_runtime.evidence import read_json, check_probe
    model, manifest = _load()
    return check_probe(model, read_json(ROOT/'probe.json'), manifest['model_sha256'])
