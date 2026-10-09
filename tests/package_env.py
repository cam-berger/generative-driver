"""Child processes must import the package under test, not whatever their interpreter finds first.

The MCP stdio client passes a child only a short list of inherited variables, so PYTHONPATH is lost and a
worktree or source run would otherwise exercise an editable install elsewhere.
"""
import os
from pathlib import Path

import generative_driver

PACKAGE_ROOT = str(Path(generative_driver.__file__).resolve().parents[1])


def package_env(**values):
    inherited = os.environ.get('PYTHONPATH')
    return {'PYTHONPATH': PACKAGE_ROOT + (os.pathsep + inherited if inherited else ''), **values}
