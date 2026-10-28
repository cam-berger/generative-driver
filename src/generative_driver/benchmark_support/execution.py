"""Execution adapters used by the provider and evaluator, with one lifecycle Interface."""


def artifact_name(manifest):
    """One selected executable, shared by acquisition, interpretation, provider and evaluator."""
    setup = manifest.get('execution_setup')
    if setup is None:
        return 'firmware.bin'
    required = {'method', 'artifact', 'substitutions', 'validated_behavior', 'hardware_gaps'}
    if not isinstance(setup, dict) or set(setup) != required or setup['method'] not in ('renode', 'native-sim'):
        raise ValueError('Invalid case execution setup')
    name = setup['artifact']
    if (not isinstance(name, str) or not name or name in ('.', '..') or '/' in name or '\\' in name
            or setup['method'] == 'native-sim' and not name.endswith('.elf')):
        raise ValueError('Invalid executed firmware artifact')
    for key in ('substitutions', 'validated_behavior', 'hardware_gaps'):
        if not isinstance(setup[key], list) or any(not isinstance(v, str) or not v.strip() for v in setup[key]):
            raise ValueError('Invalid execution setup ' + key)
    return name


def start_session(*, recipe, **arguments):
    backend = recipe.get('backend', 'renode')
    if backend == 'native-sim':
        from .native_firmware import NativeFirmwareSession
        return NativeFirmwareSession.start(recipe=recipe, **arguments)
    if backend != 'renode':
        raise ValueError('Unknown firmware execution method: ' + str(backend))
    from .native import NativeSession
    return NativeSession.start(recipe=recipe, **arguments)


def validate_setup(manifest, truth):
    """Bind the public method to its authenticated Adapter and native comparison evidence."""
    artifact = artifact_name(manifest)
    method = manifest.get('execution_setup', {}).get('method', 'renode')
    if truth.get('recipe', {}).get('backend', 'renode') != method:
        raise ValueError('Authenticated recipe backend differs from the registered execution setup')
    if method != 'native-sim':
        return
    from .rehosting import compare_executions
    proof = truth.get('build', {}).get('execution_validation', {})
    if not isinstance(proof, dict) or not {'native', 'renode'} <= proof.keys():
        raise ValueError('Native admission requires measured native/Renode comparison evidence')
    comparison = compare_executions(proof['native'], proof['renode'])
    if (comparison['verdict'] != 'passed' or comparison['execution'] != 'firmware-rehosting-validation'
            or comparison['image_hashes'].get('native-sim') != manifest['images'][artifact]
            or comparison['image_hashes'].get('renode') != proof.get('reference_image_sha256')
            or manifest['execution_setup']['validated_behavior'] != [r['id'] for r in proof['native']['records']]):
        raise ValueError('Native execution comparison evidence is incomplete, stale or names different images')
