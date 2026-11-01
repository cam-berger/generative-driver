"""The grader's import closure and identity: the code whose change must void a qualification.

The closure is read from import statements, without importing anything: every module the grading entry modules and
the seeded families import, in function bodies too, and the parent packages below `generative_driver` whose
`__init__.py` runs first. The top-level `generative_driver/__init__.py` is not part of it. Loads that are not import
statements (`import_module`, `__import__`) are not followed; list such a module in ENTRY_MODULES. The core is the
closure of ENTRY_MODULES alone. It imports no registered family: it reaches one through `registrations`, which loads it
by name, so the walk seeds each family itself and a family's closure is the core's plus its own. A case's identity
hashes the core, its own family's closure and its own registration entry, so another family's code or another case's
entry can change without voiding its admission. The identity covers the closure alone, so the system under test can
change without voiding an admission either; SYSTEM_MODULES names the modules the closure must never reach. It is one
tuple for the system under test and for the reporting code that reads the results of runs and suites (outcomes, audit,
pairing, scorecard, the suite report and the corpus): reporting grades nothing, so a grading module that imports any of
it fails the boundary test instead of moving the identity.
"""
import ast
import hashlib
import importlib.metadata
from dataclasses import asdict
from pathlib import Path

from . import registrations
from .snapshots import canonical_digest

# Module names are relative to the generative_driver package.
ENTRY_MODULES = ('benchmark_support.evaluator', 'benchmark_support.evidence', 'benchmark_support.registry',
                 'benchmark_support.calibration', 'benchmark_support.device_provider')
# Not grading code: the system under test and the run, suite, authoring and calibration code around it, and the reporting
# code that reads what runs and suites recorded (the last row).
SYSTEM_MODULES = ('configurator', 'toolkit', '_toolchain', 'agents', 'client', 'setup', 'benchmark', 'reporting',
                  'replay', 'device_access', 'analysis',
                  'benchmark_support.emulated', 'benchmark_support.cases', 'benchmark_support.ground',
                  'benchmark_support.physical', 'benchmark_support.legacy', 'benchmark_support.harness',
                  'benchmark_support.suites', 'benchmark_support.suite_store', 'benchmark_support.authoring',
                  'benchmark_support.adapters', 'benchmark_support.family_calibration', 'benchmark_support.asset_build',
                  'benchmark_support.rehosting', 'benchmark_support.reference_calibration',
                  'benchmark_support.upstream_qualification', 'benchmark_support.recorded_grading', 'interface_runtime',
                  'benchmark_support.outcomes', 'benchmark_support.audit', 'benchmark_support.pairing',
                  'benchmark_support.scorecard', 'benchmark_support.suite_reporting', 'benchmark_support.corpus')
_OUTSIDE = 'interface_runtime'  # the top-level package beside generative_driver: listed by name, never read


def _is_outside(name):
    return name.partition('.')[0] == _OUTSIDE


def _source(root, name):
    """The source file of module `name`, named from `root`, or None."""
    base = root.joinpath(*name.split('.'))
    for path in (base.with_suffix('.py'), base / '__init__.py'):
        if path.is_file():
            return path
    return None


def _named_from_root(module, level, package):
    """The dotted name an import refers to, from the root. A relative import resolves against `package` (None above
    the root); an absolute `generative_driver.` name loses its prefix; `interface_runtime` stays as written.
    The standard library and other third-party packages give None."""
    if level:
        parts = package.split('.') if package else []
        if level - 1 > len(parts):
            return None
        return '.'.join(parts[:len(parts) - level + 1] + ([module] if module else []))
    head, _, rest = module.partition('.')
    if head == 'generative_driver':
        return rest
    return module if head == _OUTSIDE else None


def _targets(root, tree, package):
    """The modules the import statements of `tree` load, named from `root`: files that exist under it and
    `interface_runtime` names. `from m import x` also loads `m.x` when that is a module file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            statements = [(alias.name, 0, []) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            statements = [(node.module, node.level, [alias.name for alias in node.names])]
        else:
            continue
        for module, level, imported in statements:
            module = _named_from_root(module, level, package)
            if module is None:
                continue
            if _is_outside(module):
                yield module
                continue
            for name in [module] + [module + '.' + item if module else item for item in imported]:
                if name and _source(root, name):
                    yield name


def _registered(root):
    """The registrations of the package at `root`, from its own registration table."""
    return registrations.read_cases(registrations.cases_file(root))


def _walk(root, families=None):
    """Everything the grader imports as {module name: sha256 of its source}; `interface_runtime` names map to None.
    Each file is read once, so the hash is of the bytes that were parsed. `families` names the native modules to seed
    beside the entry modules, relative to `benchmark_support`: None seeds every one registered under `root`."""
    if families is None:
        families = [case.native_module for case in _registered(root) if case.native_module]
    pending = list(ENTRY_MODULES) + ['benchmark_support.' + name for name in families]
    found = {}
    while pending:
        name = pending.pop()
        if name in found:
            continue
        if _is_outside(name):
            found[name] = None
            continue
        path = _source(root, name)
        if path is None:
            raise ValueError('Grader module missing from ' + str(root) + ': ' + name)
        source = path.read_bytes()
        found[name] = hashlib.sha256(source).hexdigest()
        parent = name.rpartition('.')[0]
        package = name if path.name == '__init__.py' else parent
        pending.extend(_targets(root, ast.parse(source, str(path)), package))
        if parent and _source(root, parent):
            pending.append(parent)
    return found


def _package_root(root):
    return Path(root) if root is not None else Path(__file__).resolve().parents[1]


def grader_closure(root: Path | None = None, *, families: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Sorted names of every module the grader imports: the entry modules and registered families, then what each
    imports. `root` is a generative_driver package directory, by default the installed one. `families` names the native
    modules to seed (such as 'tq9_v2'): None seeds every one that `root`'s registration table names, a tuple only those,
    and () the entry modules alone (the core). The standard library and other third-party imports end the walk;
    `interface_runtime` modules are listed but not read. Raises ValueError when an entry or family module has no source
    under `root`."""
    return tuple(sorted(_walk(_package_root(root), families)))


def grader_identity(root: Path | None = None, *, case_id: str | None = None) -> dict:
    """What a qualification pins: the hash of each closure module's source under `root`, the registration entries it
    covers and the cryptography version. With `case_id`, the identity of that case (`scope` is its id): the core, the
    closure of its native module when it names one, and its own entry. Without, the whole grader's (`scope` 'grader'):
    every family and every entry. Entries are read from `root`'s registration table. Raises ValueError for an
    unregistered case, or when a closure module has no source under `root`."""
    package = _package_root(root)
    entries, families = _registered(package), None
    if case_id is not None:
        entries = tuple(case for case in entries if case.id == case_id)
        if not entries:
            raise ValueError('Unknown benchmark case: ' + str(case_id))
        families = (entries[0].native_module,) if entries[0].native_module else ()
    body = {'schema': 'benchmark-grader-identity/2', 'scope': 'grader' if case_id is None else case_id,
            'modules': {name: digest for name, digest in sorted(_walk(package, families).items()) if digest},
            'registrations': [asdict(case) for case in entries],
            'dependencies': {'cryptography': importlib.metadata.version('cryptography')}}
    return {**body, 'sha256': canonical_digest(body)}


def combined_revision(revisions: dict) -> str:
    """The one evaluator revision of cases whose identities' sha256 are `revisions` ({case id: sha256}): that sha256
    when they are all equal, else the canonical digest of the mapping. A suite of the cases is compared by it."""
    shared = set(revisions.values())
    return shared.pop() if len(shared) == 1 else canonical_digest(revisions)


def case_revisions(case_ids) -> dict:
    """What a run or suite of `case_ids` is stamped with: `evaluator_revisions`, the sha256 of each case's grader
    identity, and `evaluator_revision`, the one they combine into. Raises ValueError without a case, or for an
    unregistered one."""
    revisions = {case_id: grader_identity(case_id=case_id)['sha256'] for case_id in sorted(set(case_ids))}
    if not revisions:
        raise ValueError('An evaluator revision names at least one case')
    return {'evaluator_revision': combined_revision(revisions), 'evaluator_revisions': revisions}
