"""The deletion marking: what deleting the legacy benchmark removes, and what is left without it.

Feature 01 of the untangle Run (docs/features/untangle/01-deletion-marking.md). The owner deletes the benchmark once
it has a repository of its own. Until then `docs/benchmark-deletion-list.md` names every benchmark item, and a marker
sets off benchmark passages in files that stay. `problems` holds the list and the markers to the tree, `strip` carries
the deletion out on a copy, and `tool_test_modules` names the tests that must pass in that copy. Nothing here deletes
in place.
"""
import ast
import asyncio
from collections import namedtuple
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import unquote

_LIST = 'docs/benchmark-deletion-list.md'
_READY, _STILL_USED, _MODULES = 'Ready to delete', 'Still used by the tool', 'Tool modules that import the benchmark'
_COMMANDS, _PASSAGES, _KEPT = 'Commands', 'Passages', 'Kept'
_COLUMNS = {_READY: ('Item', 'Note'), _STILL_USED: ('Item', 'Used by', 'Freed by'), _MODULES: ('Module', 'Freed by'),
            _COMMANDS: ('Command', 'Where', 'Freed by'), _PASSAGES: ('File', 'Unmarked lines', 'Why', 'Freed by'),
            _KEPT: ('Item', 'Why')}
_COVERING = (_READY, _STILL_USED, _KEPT)
_FEATURES = tuple(f'{number:02d}' for number in range(2, 11))
_CHILD_SECONDS = 120
_series = ', '.join


class _Entry(namedtuple('_Entry', 'table cells')):
    """One row of a table of the deletion list."""

    @property
    def name(self):
        return self.cells[0].strip('`')

    @property
    def freed_by(self):
        return self.cells[-1]

    def covers(self, file):
        return file.startswith(self.name) if self.name.endswith('/') else file == self.name

    def __str__(self):
        return f'`{self.name}` under {self.table}'


def _files(root):
    """The files of the working tree at `root`, as sorted paths from it written with `/`: those git tracks and the
    untracked ones it does not ignore."""
    command = ['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard']
    try:
        listed = subprocess.run(command, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(f'Not a git working tree: {root}') from error
    names = {name for name in listed.decode('utf-8', 'surrogateescape').split('\0') if name}
    return sorted(name for name in names if (Path(root) / name).is_file())


def _read_list(root):
    """The entries of the deletion list at `root`, keyed by table, and the problems of its form."""
    entries, found = {table: [] for table in _COLUMNS}, []
    path = Path(root) / _LIST
    if not path.is_file():
        return entries, [f'{_LIST} is missing, so nothing says what the deletion removes']
    rows, heading = {}, None
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line.startswith('#'):
            heading = line.lstrip('#').strip()
        elif line.startswith('|') and heading in _COLUMNS:
            cells = re.split(r'(?<!\\)\|', line[1:-1] if line.endswith('|') else line[1:])
            rows.setdefault(heading, []).append([cell.strip() for cell in cells])
    for table, columns in _COLUMNS.items():
        if table not in rows:
            found.append(f'{_LIST} has no table under {table}')
        elif tuple(rows[table][0]) != columns:
            found.append(f'{_LIST}: the table under {table} has the columns {_series(rows[table][0])}, '
                         f'not {_series(columns)}')
        else:
            for cells in rows[table][1:]:
                if all(cell and set(cell) <= set('-:') for cell in cells):
                    continue  # the rule under the column names
                if len(cells) == len(columns):
                    entries[table].append(_Entry(table, tuple(cells)))
                else:
                    found.append(f'{_LIST}: the row of {cells[0]} under {table} has {len(cells)} cells, '
                                 f'not {len(columns)}')
    return entries, found


def _entry_problems(entries, files):
    """The entries that match no file, that cover a file another entry covers, that repeat a passage row, or whose
    `Freed by` names no later Feature (01-B1)."""
    found = []
    for table in (*_COVERING, _MODULES, _PASSAGES):
        found += [f'{entry} matches no file' for entry in entries[table] if not any(map(entry.covers, files))]
    covering = [entry for table in _COVERING for entry in entries[table]]
    pairs = {}
    for file in files:
        first, *others = [entry for entry in covering if entry.covers(file)] or [None]
        for other in others:
            pairs.setdefault((first, other), file)
    found += [f'{first} and {other} both cover {file}' for (first, other), file in pairs.items()]
    rows = [(entry.name, entry.freed_by) for entry in entries[_PASSAGES]]
    found += [f'`{file}` under {_PASSAGES} has more than one row freed by {feature}'
              for file, feature in sorted(set(rows)) if rows.count((file, feature)) > 1]
    for table in (_STILL_USED, _MODULES, _COMMANDS, _PASSAGES):
        allowed = (*_FEATURES, 'stays') if table == _PASSAGES else _FEATURES
        for entry in entries[table]:
            if not entry.freed_by:
                found.append(f'{entry} has no Freed by')
            elif entry.freed_by not in allowed:
                found.append(f'{entry} has a Freed by that is not one of {_series(allowed)}: {entry.freed_by}')
    return found


def _covered(entries, file, tables=_COVERING):
    """Whether an entry of one of `tables` covers `file`."""
    return any(entry.covers(file) for table in tables for entry in entries[table])


def _has_benchmark_name(file):
    """Whether a part of the path `file` marks it as the benchmark's (01-B2)."""
    return any(part in ('bench', 'registrations') or part.startswith(('bench-', 'bench_'))
               or any(word in part for word in ('benchmark', 'firmware_bench', 'firmware-bench'))
               for part in file.split('/'))


def _imports(root, files):
    """The dotted names each Python file of `files` imports, in import statements at any depth, keyed by file, and
    the files that cannot be read as Python. A relative import is named from the folders of its file, so
    `from .benchmark import run` in `src/generative_driver/cli.py` names `src.generative_driver.benchmark.run`."""
    imports, unread = {}, []
    for file in files:
        if not file.endswith('.py'):
            continue
        try:
            tree = ast.parse((Path(root) / file).read_bytes())
        except (SyntaxError, ValueError):
            unread.append(file)
            continue
        names, folders = set(), file.split('/')[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = folders[:len(folders) - node.level + 1] if node.level else []
                base = '.'.join([*base, *([node.module] if node.module else [])])
                names.update({base, *(f'{base}.{alias.name}' for alias in node.names)})
        imports[file] = names
    return imports, unread


def _names_benchmark(name):
    """Whether the dotted `name` is the benchmark package or lies in it."""
    parts = name.split('.')
    return 'firmware_bench' in parts or any(
        package == 'generative_driver' and module in ('benchmark', 'benchmark_support')
        for package, module in zip(parts, parts[1:]))


def _benchmark_tests(imports):
    """The files under `tests/` that import the benchmark package, or a file under `tests/` that does (01-B3)."""
    tests = {file for file in imports if file.startswith('tests/')}

    def imported(file):
        paths = {'tests/' + name.removeprefix('tests.').replace('.', '/') for name in imports[file]}
        return {path + ending for path in paths for ending in ('.py', '/__init__.py')} & tests

    items = {file for file in tests if any(map(_names_benchmark, imports[file]))}
    while more := {file for file in tests - items if imported(file) & items}:
        items |= more
    return items


def _import_problems(root, entries, files):
    """The Python files that import the benchmark package and are not accounted for, and the listed tool modules
    that no longer import it (01-B3)."""
    imports, unread = _imports(root, files)
    found = [f'{file} cannot be read as Python, so its imports are unknown' for file in unread]
    items, listed = _benchmark_tests(imports), {entry.name for entry in entries[_MODULES]}
    for file in sorted(imports):
        if _covered(entries, file, (_READY, _STILL_USED)):
            continue
        if file in items:
            found.append(f'{file} imports the benchmark package, itself or through a test file it imports, and no '
                         f'entry under {_READY} or {_STILL_USED} covers it')
        elif not file.startswith('tests/') and any(map(_names_benchmark, imports[file])) and file not in listed:
            found.append(f'{file} imports the benchmark package and is not listed under {_MODULES}')
    found += [f'{entry} no longer imports the benchmark package' for entry in entries[_MODULES]
              if entry.name in files and entry.name not in unread
              and not any(map(_names_benchmark, imports.get(entry.name, ())))]
    return found


def _lines(root, file):
    """The lines of `file` as bytes with their endings, or None when it is not UTF-8 text."""
    data = (Path(root) / file).read_bytes()
    try:
        data.decode('utf-8')
    except UnicodeDecodeError:
        return None
    return data.splitlines(keepends=True)


def _markers(file, lines):
    """The markers of `lines` as (index, 'begin' or 'end') pairs. A marker stands alone on its line in the comment
    form of `file`: an HTML comment in Markdown, a `#` comment elsewhere (01-B4)."""
    form = '<!-- legacy-benchmark:{} -->' if file.endswith('.md') else '# legacy-benchmark:{}'
    kinds = {form.format(kind).encode(): kind for kind in ('begin', 'end')}
    return [(index, kinds[line.strip()]) for index, line in enumerate(lines) if line.strip() in kinds]


def _passages(file, lines):
    """The indexes of the lines inside the marked passages of `lines`, markers included, the indexes of the markers
    that belong to no passage, and the marker problems of `file`, each naming its line (01-B4)."""
    inside, stray, found, begun = set(), set(), [], None
    for index, kind in _markers(file, lines):
        if kind == 'begin' and begun is None:
            begun = index
        elif kind == 'begin':
            stray.add(index)
            found.append(f'{file}:{index + 1} begins a passage inside the passage that begins at line {begun + 1}')
        elif begun is None:
            stray.add(index)
            found.append(f'{file}:{index + 1} ends a passage that has no begin marker')
        else:
            inside.update(range(begun, index + 1))
            begun = None
    if begun is not None:
        stray.add(begun)
        found.append(f'{file}:{begun + 1} begins a passage that has no end marker')
    return inside, stray, found


def _marking_problems(root, entries, files):
    """The markers that do not pair into passages, the markers in files that an entry covers (01-B4), and the files
    whose unmarked lines differ from their rows under `Passages` (01-B5)."""
    found, listed = [], {}
    for entry in entries[_PASSAGES]:
        figure = entry.cells[1]
        if figure.isdigit():
            listed[entry.name] = listed.get(entry.name, 0) + int(figure)
        else:
            found.append(f'{entry} has an Unmarked lines figure that is not a whole number: {figure}')
    for file in files:
        lines, unmarked = _lines(root, file), 0
        covering = [entry for table in _COVERING for entry in entries[table] if entry.covers(file)]
        if lines is not None and covering:
            found += [f'{file}:{index + 1} holds a marker, but {covering[0]} covers the file'
                      for index, _ in _markers(file, lines)]
        elif lines is not None:
            inside, stray, faults = _passages(file, lines)
            found += faults
            if not file.endswith('.py'):
                # A stray marker is already a problem of its own, so it is not counted as a line of text as well.
                unmarked = sum(b'bench' in line.lower() for index, line in enumerate(lines)
                               if index not in inside and index not in stray)
        if unmarked != listed.get(file, 0):
            found.append(f'{file} has {unmarked} unmarked lines that say bench, and its rows under {_PASSAGES} '
                         f'count {listed.get(file, 0)}')
    return found


async def _mcp_tools(root, source):
    """The names of the tools the MCP server of the tree at `root` offers over stdio, importing from `source`."""
    # The SDK is the tool's dependency, so a tree without the tool needs none of it.
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    server = StdioServerParameters(command=sys.executable, args=['-m', 'generative_driver.mcp'], cwd=str(root),
                                   env={'PYTHONPATH': source})
    async with stdio_client(server) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            return [tool.name for tool in (await session.list_tools()).tools]


def _command_problems(root, entries, files):
    """The benchmark commands the tool offers that `Commands` does not name, and the commands it names that the tool
    does not offer (01-B8). The tool is the `generative_driver` under `src` of the tree at `root`: its command line
    offers `generative-driver benchmark` when that command's `--help` succeeds, and its MCP server offers each tool
    whose name contains `benchmark`. Both run as children whose `PYTHONPATH` is that `src`, so they answer for the
    tree under check and not for the package this process imported."""
    offered, source = set(), str(Path(root) / 'src')
    if 'src/generative_driver/__main__.py' in files:
        command = [sys.executable, '-m', 'generative_driver', 'benchmark', '--help']
        done = subprocess.run(command, cwd=root, env={**os.environ, 'PYTHONPATH': source}, capture_output=True,
                              timeout=_CHILD_SECONDS)
        offered.update({'generative-driver benchmark'} if done.returncode == 0 else ())
    if 'src/generative_driver/mcp.py' in files:
        tools = asyncio.run(asyncio.wait_for(_mcp_tools(root, source), _CHILD_SECONDS))
        offered.update(tool for tool in tools if 'benchmark' in tool)
    listed = {entry.name for entry in entries[_COMMANDS]}
    return ([f'`{command}` is a benchmark command the tool offers, and {_COMMANDS} does not name it'
             for command in sorted(offered - listed)]
            + [f'{entry} is a command the tool does not offer' for entry in entries[_COMMANDS]
               if entry.name not in offered])


def problems(root):
    """Every way the deletion list and the markers of the working tree at `root` break their rules, each as a
    sentence that names the path."""
    files = _files(root)
    entries, found = _read_list(root)
    if found:
        # Entries read from a malformed list would only repeat its fault as missing coverage.
        return found
    found += _entry_problems(entries, files)
    found += [f'{file} has a benchmark name and no entry under {_series(_COVERING)} covers it'
              for file in files if _has_benchmark_name(file) and not _covered(entries, file)]
    found += _import_problems(root, entries, files)
    found += _marking_problems(root, entries, files)
    found += _command_problems(root, entries, files)
    return found


_LINK = re.compile(r'\[[^\]]*\]\(\s*<?([^)\s>]+)')
_REFERENCE = re.compile(r'\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)')


def _links(text):
    """The targets of the Markdown links of `text`, leaving out fenced blocks and code spans, which hold no link."""
    targets, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith(('```', '~~~')):
            fenced = not fenced
        elif not fenced:
            line = re.sub(r'`[^`]*`', '', line)
            targets += _LINK.findall(line)
            targets += _REFERENCE.match(line).groups() if _REFERENCE.match(line) else ()
    return targets


def _copy_failures(destination, documents):
    """The relative links of the Markdown `documents` of the copy at `destination` to files it lacks, and the parse
    error of its `pyproject.toml` (01-B6)."""
    found = []
    for file in documents:
        for target in _links((destination / file).read_text(encoding='utf-8')):
            path = unquote(target.split('#')[0].split('?')[0])
            if not path or path.startswith('/') or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', target):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(destination, os.path.dirname(file), path))):
                found.append(f'{file} links to {target}, which the copy lacks')
    try:
        tomllib.loads((destination / 'pyproject.toml').read_text(encoding='utf-8'))
    except FileNotFoundError:
        pass
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as error:
        found.append(f'pyproject.toml does not parse in the copy: {error}')
    return found


def strip(root, destination):
    """The failures of the stripped copy of the working tree at `root`, after writing that copy into `destination`:
    the files of the tree without the items under `Ready to delete` and without the marked passages (01-B6). Each
    failure is a sentence that names the file. `destination` is a new or empty directory outside the tree, so
    nothing is written in the tree itself."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination == root or root in destination.parents:
        raise ValueError(f'Destination is inside the tree it copies: {destination}')
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError(f'Destination is not an empty directory: {destination}')
    entries, found = _read_list(root)
    documents = []
    for file in _files(root):
        if _covered(entries, file, (_READY,)):
            continue
        target = destination / file
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = None if _covered(entries, file) else _lines(root, file)
        if lines is None:
            shutil.copyfile(root / file, target)
        else:
            inside = _passages(file, lines)[0]
            target.write_bytes(b''.join(line for index, line in enumerate(lines) if index not in inside))
            documents += [file] if file.endswith('.md') else []
        shutil.copymode(root / file, target)
    return found + _copy_failures(destination, documents)


def tool_test_modules(root):
    """The test modules to run in the stripped copy of the working tree at `root`, as sorted paths from it: every
    `tests/test_*.py` that no entry under `Ready to delete` covers and that is not a benchmark item (01-B7)."""
    files = _files(root)
    entries, _ = _read_list(root)
    items = _benchmark_tests(_imports(root, files)[0])
    return [file for file in files if re.fullmatch(r'tests/test_[^/]*\.py', file) and file not in items
            and not _covered(entries, file, (_READY,))]
