"""The execution setup a case declares, shared by acquisition, the provider and the evaluator, and the default Renode
session a family starts; what a family adds to either sits behind its hooks (Feature 01, family-seam)."""


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


def start_renode(*, recipe, **arguments):
    """A Renode session of `recipe`, what a family starts unless it declares its own `start_session`. ValueError for a
    recipe with any other backend."""
    backend = recipe.get('backend', 'renode')
    if backend != 'renode':
        raise ValueError('Unknown firmware execution method: ' + str(backend))
    from .native import NativeSession
    return NativeSession.start(recipe=recipe, **arguments)


def validate_setup(manifest, truth):
    """Bind the public method to its authenticated recipe, and have the case's family validate how it executes."""
    from .families import family_for
    artifact_name(manifest)
    method = manifest.get('execution_setup', {}).get('method', 'renode')
    if truth.get('recipe', {}).get('backend', 'renode') != method:
        raise ValueError('Authenticated recipe backend differs from the registered execution setup')
    family_for(manifest['id']).validate_execution(manifest, truth)
