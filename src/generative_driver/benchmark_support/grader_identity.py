"""The grader's import closure and identity: the code whose change must void a qualification.

The closure is read from import statements, without importing anything: every module the grading entry modules and
the registered families import, in function bodies too, and the parent packages below `generative_driver` whose
`__init__.py` runs first. The top-level `generative_driver/__init__.py` is not part of it. Loads that are not import
statements (`import_module`, `__import__`) are not followed; list such a module in ENTRY_MODULES. The identity covers
this closure alone, so the system under test can change without voiding an admission; SYSTEM_MODULES names the
modules the closure must never reach. It is one tuple for the system under test and for the reporting code that reads
the results of runs and suites (outcomes, audit, pairing, scorecard, the suite report and the corpus): reporting
grades nothing, so a grading module that imports any of it fails the boundary test instead of moving the identity.
"""
import ast
import hashlib
import importlib.metadata
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


def _walk(root):
    """Everything the grader imports as {module name: sha256 of its source}; `interface_runtime` names map to None.
    Each file is read once, so the hash is of the bytes that were parsed."""
    pending = list(ENTRY_MODULES) + ['benchmark_support.' + case.native_module
                                     for case in registrations._CASES if case.native_module]
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


def grader_closure(root: Path | None = None) -> tuple[str, ...]:
    """Sorted names of every module the grader imports: the entry modules and registered families, then what each
    imports. `root` is a generative_driver package directory, by default the installed one; the families are those the
    installed `registrations._CASES` names, whatever `root` is. The standard library and other third-party imports end
    the walk; `interface_runtime` modules are listed but not read. Raises ValueError when an entry or family module has
    no source under `root`."""
    return tuple(sorted(_walk(_package_root(root))))


def grader_identity(root: Path | None = None) -> dict:
    """What a qualification pins: the hash of each closure module's source under `root` and the cryptography version."""
    body = {'schema': 'benchmark-grader-identity/1',
            'modules': {name: digest for name, digest in sorted(_walk(_package_root(root)).items()) if digest},
            'dependencies': {'cryptography': importlib.metadata.version('cryptography')}}
    return {**body, 'sha256': canonical_digest(body)}
