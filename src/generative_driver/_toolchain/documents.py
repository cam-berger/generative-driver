"""Stage 1. Import a document the operator passed with a run (docs/features/kit-fixes/07-input-paths.md, 07-B1).

A file passed with a run is copied into the acquire workspace's `inputs/` folder, where it went no further. This tool
turns one of those files into page text with an index of its pages, as a datasheet's pages are, so that the acquire
handoff carries the file and its pages to interpretation as a supplementary document. A PDF needs pypdf, which is
loaded only when the tool runs.
"""
import hashlib
import json
import os
import shutil

from . import core

_TEXT_SUFFIXES = ('.txt', '.md')


def _refused(reason, **extra):
    return {'available': False, 'rung': 'document', 'reason': reason, **extra, '_exit': 1}


def _pages(path, data):
    """(the format and the text of each page, None) of the document at `path`, or (None, the reason it cannot be
    read)."""
    suffix = os.path.splitext(path)[1].lower()
    if suffix == '.pdf':
        try:
            import pypdf
        except ImportError:
            return None, ('pypdf is not installed, so a PDF cannot be read: install the docs extra '
                          '(generative-driver[docs])')
        try:
            return ('pdf', [page.extract_text() or '' for page in pypdf.PdfReader(path).pages]), None
        except Exception as error:  # pypdf raises its own errors for a file it cannot parse
            return None, 'pypdf cannot read the PDF: ' + type(error).__name__ + ': ' + str(error)[:200]
    if suffix in _TEXT_SUFFIXES:
        try:
            return ('text', [data.decode('utf-8')]), None
        except UnicodeDecodeError as error:
            return None, 'the file is not UTF-8 text: ' + str(error)[:200]
    return None, ('only a PDF, a .txt or a .md file can be imported, not a ' + (suffix or 'file without a suffix')
                  + ' file')


@core.tool('acquire_document')
def acquire_document(run_dir, name, inputs_dir, **_):
    """Import the file `name` in `inputs_dir`, the acquire workspace's inputs folder: a PDF becomes the text of each
    page, a .txt or .md file in UTF-8 one page. The original and the pages, with an index of them, are written under
    the run's acquire/documents folder. Any other path or file is refused with the reason, and nothing is written."""
    if (not isinstance(name, str) or not name or name in ('.', '..') or os.path.basename(name) != name
            or '/' in name or '\\' in name or '\x00' in name):
        return _refused('name must be the name of a file in the inputs folder, not a path: ' + repr(name)[:120])
    folder = os.path.realpath(inputs_dir)
    source = os.path.join(folder, name)
    if os.path.islink(source) or not os.path.isfile(source) or os.path.dirname(os.path.realpath(source)) != folder:
        return _refused('no file of that name was passed with the run: ' + repr(name)[:120])
    with open(source, 'rb') as file:
        data = file.read()
    read, why = _pages(source, data)
    if read is None:
        return _refused(why, source=name)
    kind, pages = read
    run = core.resolve_run(run_dir)
    out = os.path.join(run, 'acquire', 'documents', name)
    if os.path.exists(out):
        return _refused('this document is already imported in this run; its evidence is kept as it is', source=name)
    os.makedirs(os.path.join(out, 'pages'))
    original = os.path.join(out, name)
    shutil.copyfile(source, original)
    index, files = [], {}
    for number, text in enumerate(pages, 1):
        page = os.path.join(out, 'pages', '%03d.txt' % number)
        with open(page, 'w', encoding='utf-8', newline='') as file:
            file.write(text)
        first = next((line.strip() for line in text.splitlines() if line.strip()), '')
        index.append({'page': number, 'chars': len(text), 'first_line': first[:90]})
        files[core.label(page)] = core.sha256(page)
    with open(os.path.join(out, 'pages', 'index.json'), 'w', encoding='utf-8') as file:
        json.dump(index, file, indent=1)
    sha = hashlib.sha256(data).hexdigest()
    files[core.label(original)] = sha
    return {'available': True, 'rung': 'document', 'format': kind, 'source': name, 'sha256': sha, 'size': len(data),
            'artifact': core.label(original), 'pages_dir': core.label(os.path.join(out, 'pages')),
            'page_count': len(pages), '_files': files}
