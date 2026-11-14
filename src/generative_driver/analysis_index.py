"""The kit analysis's export as interpret workers read it: the index, and the bounded excerpts of code and strings an
interpret task gets (docs/features/kit-fixes/06-interpret-tasks.md, 06-B3, 06-B6, 06-B7 and 06-B9).

A worker that reads a whole export pays for every function in its context, although the commands live in a few. The
index says where to look: each function with its size, callers, callees, strings and switch tables, and the likely
command dispatchers ranked with the evidence for each rank. Everything here is read from the exporter's own files
(ExportDecomp.java), in a fixed order, so the same export always gives the same bytes."""
import json
import re
from pathlib import Path

INDEX_SCHEMA = 'kit-analysis-index/1'
# Bounds that tests may shorten.
INDEX_MAX_BYTES = 2 * 1024 * 1024
CODE_MAX_BYTES = 256 * 1024
STRINGS_MAX_BYTES = 256 * 1024
# A string value is shown up to this many characters in the index; the export keeps it whole.
STRING_CLIP = 200
# The note at the head of a code excerpt lists at most this many function addresses of each kind.
NOTE_ADDRESSES = 64
# A command-like string: up to three short words of letters, digits and punctuation a command uses, with a letter, as
# "ID?", "T" or "SET RATE". Format strings (with %) and sentences are not.
COMMAND = re.compile(r'(?=.*[A-Za-z])[A-Za-z0-9_?*:+\-.=!#$/,]{1,16}(?: [A-Za-z0-9_?*:+\-.=!#$/,]{1,16}){0,2}')
LINE_END = re.compile(r'(?:\\r|\\n)+$')
RANKING = ('Each function is scored 2 for every distinct case value of its switch tables and 1 for every command-like '
           'string it references; a function with a switch table of two or more cases, or two or more command-like '
           'strings, is a candidate. Candidates are ranked by score, highest first, then by address.')


def _rows(path):
    """The data rows of an exporter table as dicts by its header's columns; none when the table is absent."""
    try:
        lines = Path(path).read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return []
    if not lines:
        return []
    columns = lines[0].split('\t')
    return [dict(zip(columns, line.split('\t'))) for line in lines[1:] if line.strip()]


def _split(value):
    return [item for item in (value or '').split(',') if item]


def _command_like(value):
    """Whether an exported string value (escaped as the exporter escapes it) looks like a command."""
    return bool(COMMAND.fullmatch(LINE_END.sub('', value or '').strip()))


class Export:
    """One export folder, read once."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.functions = {row['address']: row for row in _rows(self.folder / 'functions.tsv') if row.get('address')}
        self.callees, self.callers = {}, {}
        for row in _rows(self.folder / 'references.tsv'):
            caller, callee = row.get('from_function'), row.get('to_function')
            if caller and callee and 'CALL' in (row.get('type') or ''):
                self.callees.setdefault(caller, set()).add(callee)
                self.callers.setdefault(callee, set()).add(caller)
        self.strings = [row for row in _rows(self.folder / 'strings.tsv') if row.get('address')]
        self.strings_of = {}
        for row in self.strings:
            for user in _split(row.get('functions')):
                self.strings_of.setdefault(user, []).append(row)
        self.switches = {}
        for row in _rows(self.folder / 'switches.tsv'):
            table = self.switches.setdefault(row.get('function'), {}).setdefault(row.get('switch_address'), [])
            table.append({'value': row.get('case_value'), 'target': row.get('target')})
        try:
            navigation = json.loads((self.folder / 'navigation.json').read_text(encoding='utf-8'))
            self.truncated = sorted(name for name, table in (navigation.get('tables') or {}).items()
                                    if isinstance(table, dict) and table.get('truncated'))
        except (OSError, ValueError, AttributeError):
            self.truncated = []

    def _code_path(self, address):
        return self.folder / ('function_' + re.sub(r'[^A-Za-z0-9_-]', '_', address) + '.c')

    def _code(self, address):
        try:
            return self._code_path(address).read_text(encoding='utf-8', errors='replace')
        except OSError:
            return ''

    def nearest(self, addresses):
        """`addresses` and every function they call, directly or not, nearest first: breadth-first, each level by
        address."""
        order, frontier, seen = [], list(addresses), set()
        while frontier:
            following = []
            for address in frontier:
                if address not in seen:
                    seen.add(address)
                    order.append(address)
                    following += sorted(self.callees.get(address, ()))
            frontier = following
        return order

    def strings_used(self, addresses):
        """The strings `addresses` reference, each once, in the order of the functions that use them."""
        found, seen = [], set()
        for address in addresses:
            for row in self.strings_of.get(address, []):
                if row['address'] not in seen:
                    seen.add(row['address'])
                    found.append({'address': row['address'], 'value': row.get('value') or '',
                                  'functions': _split(row.get('functions'))})
        return found

    def excerpt(self, addresses, purpose):
        """The decompiled code of `addresses`, in their order, within CODE_MAX_BYTES, headed by a note of what it holds
        and what was cut."""
        bound = room = CODE_MAX_BYTES
        while True:
            text = self._excerpt(addresses, purpose, bound, room)
            over = len(text.encode('utf-8')) - bound
            if over <= 0 or room == 0:
                return text if over <= 0 else text.encode('utf-8')[:bound].decode('utf-8', errors='ignore')
            room = max(0, room - over)

    def _excerpt(self, addresses, purpose, bound, room):
        parts, whole, short, left = [], [], None, []
        for address in addresses:
            text = self._code(address)
            size = len(text.encode('utf-8'))
            if text and short is None and not left and size + 1 <= room:
                parts.append(text if text.endswith('\n') else text + '\n')
                whole.append(address)
                room -= size + 1
            elif text and short is None and not left and room > 1:
                cut = text.encode('utf-8')[:room - 1].decode('utf-8', errors='ignore')
                cut = cut[:cut.rfind('\n') + 1] if '\n' in cut else cut
                parts.append(cut)
                short = (address, len(cut.encode('utf-8')), size)
                room = 0
            else:
                left.append(address)
        note = ['/* ' + purpose + ', within ' + str(bound) + ' bytes.', '   Included whole: ' + _listed(whole) + '.']
        if short:
            note.append('   Cut short: %s (its first %d of %d bytes).' % short)
        if left:
            note.append('   Left out: ' + _listed(left) + '.')
        return '\n'.join(note) + ' */\n\n' + '\n'.join(parts)

    def strings_table(self):
        """strings.tsv's text within STRINGS_MAX_BYTES, its header and leading rows whole, and how many rows it left
        out."""
        try:
            lines = (self.folder / 'strings.tsv').read_text(encoding='utf-8', errors='replace').splitlines(True)
        except OSError:
            return '', 0
        kept, size = [], 0
        for line in lines:
            length = len(line.encode('utf-8'))
            if size + length > STRINGS_MAX_BYTES:
                break
            kept.append(line)
            size += length
        return ''.join(kept), len(lines) - len(kept)

    def _size(self, address):
        try:
            return self._code_path(address).stat().st_size
        except OSError:
            return 0

    def _switch_tables(self, address):
        return [{'switch_address': at, 'cases': cases} for at, cases in sorted(self.switches.get(address, {}).items())]

    def _string(self, row):
        return {'address': row['address'], 'value': (row.get('value') or '')[:STRING_CLIP]}

    def candidates(self):
        """The ranked dispatcher candidates, each with its score and the evidence that ranked it."""
        found = []
        for address in sorted(set(self.functions) | set(self.switches) | set(self.strings_of)):
            tables = self._switch_tables(address)
            values = {case['value'] for table in tables for case in table['cases'] if case['value'] != 'unknown'}
            commands = [row for row in self.strings_of.get(address, []) if _command_like(row.get('value'))]
            if not (any(len(table['cases']) >= 2 for table in tables) or len(commands) >= 2):
                continue
            found.append({'function': address, 'name': (self.functions.get(address) or {}).get('name'),
                          'score': 2 * len(values) + len(commands),
                          'evidence': {'switch_tables': tables, 'command_strings': [
                              {**self._string(row), 'functions': _split(row.get('functions')),
                               'refs_outside_functions': _split(row.get('refs_outside_functions'))}
                              for row in commands]}})
        found.sort(key=lambda candidate: (-candidate['score'], candidate['function']))
        return [{'rank': rank, **candidate} for rank, candidate in enumerate(found, 1)]

    def _entry(self, address):
        row = self.functions.get(address) or {}
        return {'address': address, 'name': row.get('name'),
                'decompiled': (row.get('decompilation_complete') or '').lower() == 'true',
                'decompiled_bytes': self._size(address), 'callers': sorted(self.callers.get(address, ())),
                'callees': sorted(self.callees.get(address, ())),
                'strings': [self._string(r) for r in self.strings_of.get(address, [])],
                'switch_tables': self._switch_tables(address)}

    def index(self):
        """ANALYSIS_INDEX.json's bytes: at most INDEX_MAX_BYTES, saying what it left out to stay under."""
        bound = INDEX_MAX_BYTES
        candidates = self.candidates()
        # What is kept first when the bound bites: the candidates' functions by rank, then the rest by address.
        ranked = [c['function'] for c in candidates if c['function'] in self.functions]
        order = ranked + [address for address in sorted(self.functions) if address not in set(ranked)]
        entries = {address: self._entry(address) for address in order}

        def render(kept, listed):
            left = {'functions': len(order) - kept, 'dispatcher_candidates': len(candidates) - listed}
            document = {'schema': INDEX_SCHEMA, 'ranking': RANKING, 'export_truncated_tables': self.truncated,
                        'functions': [entries[a] for a in sorted(order[:kept])],
                        'dispatcher_candidates': candidates[:listed],
                        'omitted': None if not any(left.values()) else {
                            **left, 'rule': 'To stay within ' + str(bound) + ' bytes the index keeps the dispatcher '
                            "candidates' functions by rank, then the other functions by address, and leaves out the "
                            'rest; candidates are left out from the lowest rank.'}}
            return (json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + '\n').encode('utf-8')

        whole = render(len(order), len(candidates))
        if len(whole) <= bound:
            return whole
        # The most functions that fit with every candidate, or else the most candidates that fit with no function.
        kept = _most(lambda k: len(render(k, len(candidates))) <= bound, len(order))
        if kept is not None:
            return render(kept, len(candidates))
        return render(0, _most(lambda k: len(render(0, k)) <= bound, len(candidates)) or 0)


def _listed(addresses):
    if not addresses:
        return 'none'
    shown = ', '.join(addresses[:NOTE_ADDRESSES])
    return shown + (' and %d more' % (len(addresses) - NOTE_ADDRESSES) if len(addresses) > NOTE_ADDRESSES else '')


def _most(fits, count):
    """The largest k in 0..count for which `fits(k)`, assuming it holds up to some k and not after; None when not
    even 0 fits."""
    if not fits(0):
        return None
    low, high = 0, count
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low
