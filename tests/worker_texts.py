"""What a worker is given to read, for the tests of the one driver format and of the package of data.

The interface model is the one statement of a driver, in one format, and workers get one document for it
(docs/adr/0010-a-package-is-data-in-one-format.md, decision 3). A package holds data and no code
(docs/features/package-format/03-package.md, Behaviour 03-B16). This file names that document and the words no text a
worker gets may still hold.
"""
import re
from pathlib import Path

# An earlier format tag, talk of schemas 3 to 6, or a contract: one a worker writes, a tool it writes one with, or
# the file a package of an earlier format held.
EARLIER = re.compile(r'interface-model/[3-6]|[Ss]chemas?[ -][3-6]\b|contract\.json|CONTRACT\.md|contract\.py')
# What a package of an earlier format held or did: its reference, its own command line, the tool that ran that
# command line, the flag for its own copy of the runtime, and its other files.
GONE = re.compile(r"REFERENCE\.md|package reference|python -m driver|package's (?:own )?command line|package_execute"
                  r'|runtime_current|mcp-tools\.json|protocol\.json|manifest\.sha256|driver/model\.json'
                  r'|driver/__main__')
# What BRIEF.md of each front end asks of the worker's model.
ASK = ('In `model.json`, write a `description` of each operation, parameter and output, the `unit` of each number, and '
       'an uncertainty where the evidence leaves a meaning open.')


def model_document():
    """The path of the tool's one model document."""
    from generative_driver.toolkit import resources_root
    return resources_root() / 'docs' / 'INTERFACE_MODEL.md'


def texts_of(folder):
    """Every file under `folder` that is UTF-8 text, by its relative name."""
    texts = {}
    for path in sorted(Path(folder).rglob('*')):
        if path.is_file():
            try:
                texts[path.relative_to(folder).as_posix()] = path.read_text(encoding='utf-8')
            except UnicodeDecodeError:
                pass
    return texts


def earlier_names(texts):
    """Each (name, words) of `texts`, {name: text}, where a text still names an earlier format or a worker's contract."""
    return sorted((name, found) for name, text in texts.items() for found in EARLIER.findall(text))


def gone_names(texts):
    """Each (name, words) of `texts`, {name: text}, where a text still names what a package no longer holds or does.
    A text is read as one line, so words that a line break divides are found."""
    return sorted((name, found) for name, text in texts.items() for found in GONE.findall(' '.join(text.split())))
