"""What a worker is given to read, for the tests of the one driver format.

The interface model is the one statement of a driver, in one format, and workers get one document for it
(docs/adr/0010-a-package-is-data-in-one-format.md, decision 3). This file names that document and the words no text a
worker gets may still hold.
"""
import re
from pathlib import Path

# An earlier format tag, talk of schemas 3 to 6, or a contract a worker writes or a tool it writes one with. The
# package's own file `driver/contract.json` is none of them: it stays until the package's files change.
EARLIER = re.compile(r'interface-model/[3-6]|[Ss]chemas?[ -][3-6]\b|(?<!driver/)contract\.json|CONTRACT\.md'
                     r'|contract\.py')
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
