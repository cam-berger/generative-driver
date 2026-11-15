"""Supplementary documents in interpretation (docs/features/kit-fixes/07-input-paths.md, 07-B3 and 07-B4).

A datasheet's pages, a protocol specification and the documents an operator passes with a run used to stop at the
acquire stage. The configurator now lays out the text of every supplementary document the latest acquire handoff
carries under `documents/` in each interpret workspace of a generic run, with `DOCUMENTS.json` listing them, within a
bound on their total size. Interpreters read them as evidence of meanings, units and framing; the firmware or the
device wins where they disagree (CONTEXT.md, "Supplementary document").

Which document an artifact of the handoff belongs to, and its source, come from the configurator's own record of the
acquire tool calls that wrote it, matched to the handoff's copy by hash."""
import json
import shutil
from pathlib import Path

# The bound on the documents' text an interpret worker gets (07-B3), and on what one interpret task gets (07-B4).
BOUND_BYTES = 2 * 1024 * 1024
TASK_BOUND_BYTES = 256 * 1024
_FOLDER = 'documents'
_INDEX = 'DOCUMENTS.json'
_SCHEMA = 'supplementary-documents/1'
# What TASKS.json tells an interpret worker of the documents (07-B3). The truth they are weighed against is the
# firmware and the device, or the device alone in device-first interpretation.
_DOCUMENTS = ('The files under documents/, listed in DOCUMENTS.json, are supplementary documents acquired beside the '
              '{}: datasheet pages, protocol specifications and files the operator supplied, as text. Read them as '
              'evidence of meanings, units and framing. ')
_TRUTH = ('The firmware is the truth, and so are the device\'s replies where you have them: where a document disagrees '
          'with the firmware or the device, follow the firmware or the device')
_DEVICE_TRUTH = 'The device\'s replies are the truth: where a document disagrees with them, follow the device'
_CITE = (', and record the disagreement in NOTES.md and as an uncertainty in contract.json. In provenance, cite a '
         'document page by its path, such as documents/1-datasheet/002.txt. DOCUMENTS.json names each page left out '
         'by its size bound.')


def sealed_inputs(folder, handoff, calls):
    """The documents of an acquire `handoff` an interpret workspace gets, by workspace name: `documents` (a folder of
    text pages) and `DOCUMENTS.json`, written under `folder`, or {} when the handoff carries none. `calls` are the
    configurator's tool.finished records of the acquire assignment that handed it off, in order."""
    documents = _documents(handoff, calls)
    if not documents:
        return {}
    root = Path(folder) / _FOLDER
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    listed, left_out = [], []
    for number, document in enumerate(documents, 1):
        listed.append({'document': number, 'kind': document['kind'], 'source': document['source'],
                       'sha256': document['sha256'], 'pages': []})
        if document.get('excluded'):
            left_out.append({'document': number, 'reason': document['excluded']})
    taken, past = _round_robin([[((number, rank), page['bytes']) for rank, page in enumerate(document['pages'])]
                                for number, document in enumerate(documents, 1)], BOUND_BYTES)
    for number, rank in taken:
        page = documents[number - 1]['pages'][rank]
        path = _FOLDER + '/' + str(number) + '-' + documents[number - 1]['kind'] + '/%03d.txt' % (rank + 1)
        target = Path(folder) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(page['file'], target)
        listed[number - 1]['pages'].append({'page': rank + 1, **page.get('extra', {}), 'path': path,
                                            'bytes': page['bytes']})
    for number, rank in past:
        left_out.append({'document': number, 'page': rank + 1,
                         'bytes': documents[number - 1]['pages'][rank]['bytes'],
                         'reason': 'Past the bound of %d bytes on the documents\' text' % BOUND_BYTES})
    for entry in listed:
        entry['pages'].sort(key=lambda page: page['page'])
    index = Path(folder) / _INDEX
    index.write_text(json.dumps({'schema': _SCHEMA, 'bound_bytes': BOUND_BYTES,
                                 'forwarded_bytes': sum(p['bytes'] for d in listed for p in d['pages']),
                                 'documents': listed, 'left_out': left_out}, indent=1) + '\n', encoding='utf-8')
    return {_FOLDER: str(root), _INDEX: str(index)}


def task_entry(device_first=False):
    """TASKS.json's `documents` entry: where the documents are, what they are evidence of, that the firmware or the
    device wins, and how provenance cites a page (07-B3). A device-first run has no firmware: its device is the
    truth."""
    instructions = (_DOCUMENTS.format('device' if device_first else 'firmware')
                    + (_DEVICE_TRUTH if device_first else _TRUTH) + _CITE)
    return {'index': _INDEX, 'folder': _FOLDER + '/', 'instructions': instructions}


def for_task(workspace, cited=None):
    """(one interpret task's document inputs by workspace name, the `documents` entry of its TASK.json, the cited
    paths that name no forwarded page) from the stage's sealed `workspace` (07-B4), or ({}, None, []) when it holds no
    documents. The inputs are DOCUMENTS.json and forwarded pages taken round-robin within TASK_BOUND_BYTES: every
    forwarded page, or only those `cited` (paths) when given. The entry names what the bound left out."""
    index = Path(workspace) / _INDEX
    if not index.is_file():
        return {}, None, []
    listing = json.loads(index.read_text(encoding='utf-8'))
    forwarded = {page['path'] for document in listing['documents'] for page in document['pages']}
    wanted = None if cited is None else set(cited)
    taken, left = _round_robin([[(page['path'], page['bytes']) for page in document['pages']
                                 if wanted is None or page['path'] in wanted] for document in listing['documents']],
                               TASK_BOUND_BYTES)
    inputs = {_INDEX: index, **{path: Path(workspace) / path for path in taken}}
    entry = {'index': _INDEX, 'bound_bytes': TASK_BOUND_BYTES, 'forwarded': taken, 'left_out': left}
    return inputs, entry, [path for path in dict.fromkeys(cited or ()) if path not in forwarded]


def citations(evidence):
    """The document pages an inventory command's `evidence` cites: each entry that is a path under documents/."""
    return [entry.split()[0] for entry in evidence or () if isinstance(entry, str) and entry.startswith(_FOLDER + '/')]


def _round_robin(documents, bound):
    """(keys taken, keys left out) of `documents`, each a list of (key, bytes) in page order, taken round-robin by page
    number (every document's first page, then every second page, and so on) until the next page would pass `bound`.
    A document therefore loses pages only from its end."""
    order = [document[rank] for rank in range(max(map(len, documents), default=0))
             for document in documents if rank < len(document)]
    taken, total = [], 0
    for at, (key, size) in enumerate(order):
        if total + size > bound:
            return taken, [key for key, _ in order[at:]]
        taken.append(key)
        total += size
    return taken, []


def _documents(handoff, calls):
    """The supplementary documents the acquire `handoff` carries, in the order the `calls` that wrote them ran: each
    {kind, source, sha256, pages: [{file, bytes, extra}], excluded: why its text is left out, when it is}."""
    carried = {artifact['sha256']: Path(artifact['path']) for artifact in (handoff or {}).get('artifacts') or ()}
    documents = []
    for call in calls:
        result = call.get('result') if isinstance(call.get('result'), dict) else {}
        observed = call.get('artifacts') if isinstance(call.get('artifacts'), dict) else {}

        def copy(raw):
            """The handoff's copy of what the call wrote at `raw`, with its hash, or (None, None)."""
            sha = observed.get(str(Path(raw).resolve())) if isinstance(raw, str) else None
            return (carried.get(sha), sha) if sha in carried else (None, None)
        name = call.get('name')
        given = result.get('inputs_for_interpret') if isinstance(result.get('inputs_for_interpret'), dict) else {}
        if name in ('acquire_datasheet', 'acquire_document') or (name == 'acquire_classify' and 'pages' in given):
            pages, _ = copy(result.get('pages_dir') or given.get('pages'))
            original, sha = copy(result.get('artifact'))
            if pages is not None and sha is not None:
                source = result.get('source') if isinstance(result.get('source'), str) else original.name
                documents.append({'kind': 'supplied' if name == 'acquire_document' else 'datasheet',
                                  'source': source, 'sha256': sha, 'pages': _pages(pages)})
        elif name == 'acquire_protocol_spec':
            documents += _protocol_spec(result, given, copy)
    return documents


def _pages(folder):
    """The text pages of a pages folder in page order, by its index.json when it can be read."""
    try:
        index = json.loads((folder / 'index.json').read_text(encoding='utf-8'))
        names = ['%03d.txt' % entry['page'] for entry in index]
    except (OSError, ValueError, TypeError, KeyError):
        names = sorted(path.name for path in folder.glob('*.txt'))
    return [{'file': folder / name, 'bytes': (folder / name).stat().st_size} for name in names
            if (folder / name).is_file()]


def _protocol_spec(result, given, copy):
    """Each source of a fetched protocol specification as a document: its sections when it was split, otherwise the
    source itself when it decodes as UTF-8, otherwise listed as left out."""
    sections, _ = copy(given.get('spec/sections'))
    try:
        index = json.loads((sections / 'index.json').read_text(encoding='utf-8')) if sections else []
    except (OSError, ValueError):
        index = []
    documents = []
    for source in result.get('sources') or ():
        if not isinstance(source, dict):
            continue
        path, sha = copy(given.get('spec/' + str(source.get('name'))))
        if path is None:
            continue
        document = {'kind': 'protocol-spec', 'source': source.get('url') or source.get('name'), 'sha256': sha,
                    'pages': []}
        split = [entry for entry in index if isinstance(entry, dict) and entry.get('source') == source.get('name')]
        if split:
            document['pages'] = [{'file': sections / ('%03d.txt' % entry['section']),
                                  'bytes': (sections / ('%03d.txt' % entry['section'])).stat().st_size,
                                  'extra': {'section': entry['section']}} for entry in split]
        else:
            try:
                path.read_bytes().decode('utf-8')
                document['pages'] = [{'file': path, 'bytes': path.stat().st_size}]
            except UnicodeDecodeError:
                document['excluded'] = 'The source is neither split into sections nor UTF-8 text'
        documents.append(document)
    return documents
