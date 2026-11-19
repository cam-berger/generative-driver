"""Where the installed tool lies: the folder of its package and the folder of its runtime.

Feature 12 of the untangle Run (docs/features/untangle/12-firmware-bench.md, 12-B2). The benchmark hashes the tool it
evaluates into its revisions, starts the harness child with the folder that holds the tool on its search path, and keeps
evaluator output out of the tool's folders. It finds both folders through names the client interface lists,
`resources_root()` and the imported `interface_runtime`, never by a path beside the benchmark's own code, so they are
right wherever the two packages are installed.

This module is not grading code, and grading code does not import it: `snapshots.execution_provenance` takes the two
folders from its callers.
"""
from pathlib import Path


def tool_folder():
    """The folder of the installed package `generative_driver`: the parent of its resources."""
    from generative_driver.toolkit import resources_root
    return Path(resources_root()).parent


def runtime_folder():
    """The folder of the installed package `interface_runtime`."""
    import interface_runtime
    return Path(interface_runtime.__file__).resolve().parent


def folders():
    """Both folders as the keywords `execution_provenance` takes: `tool` and `runtime`."""
    return {'tool': tool_folder(), 'runtime': runtime_folder()}
