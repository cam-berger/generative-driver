"""Where a bench home keeps its suites and trials, and what becomes of a scoring suite's trials once they conclude
(Feature 07, trial sealing; spec decision 21). This is the only code that knows the bench home's layout.

A concluded scoring trial is sealed in place under its set's password, which the harness already holds: a tar.gz of its
folders, split into parts, each passed as base64 in one JSON field to `EvaluatorPassword.seal`, with an index that holds
each file's hash and original path. This catches slips and casual browsing. It is not isolation: a worker running as
the same user can read the harness's memory or copy the ciphertext, and the runs database stays readable."""
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import time
import uuid
from collections import namedtuple
from pathlib import Path

# How many bytes of a seal's tar.gz one sealed part holds at most, so memory stays bounded whatever a trial wrote.
PART_BYTES = 64 * 1024 * 1024
SEALED = 'sealed'
TOKEN = 'evaluation-token.enc'
_INDEX_SCHEMA, _PART_SCHEMA = 'bench-sealed-index/1', 'bench-sealed-part/1'
# What `harness._private` and `EvaluatorPassword.seal` leave if they are killed while they write.
_WRITER_TEMPORARY = re.compile(r'\..+\.[0-9a-f]{16,32}')
_SEALED_NAME = re.compile(r'sealed-([0-9]+)-(?:[0-9]+|index)\.enc')
_WORKSPACE_ATTEMPT, _WORKSPACE_FOLDER = re.compile('attempt[0-9]+'), re.compile('gd-candidate-.+')
_CONCLUDED = ('finished', 'faulted', 'flagged')
_DELETE_ATTEMPTS, _DELETE_PAUSE = 50, .05


class SealError(ValueError):
    """A sealing, verifying, restoring or deleting failure, which stops the suite for a resume. Its message names no
    path."""


class Unreachable(SealError):
    """The configurator could not be read, so nothing was decided about any trial."""


def bench_home(home=None):
    """The bench home: a sibling of the configurator home `home`, never inside it."""
    from ..client import default_home
    home = Path(home or default_home()).expanduser().resolve()
    return home.parent / (home.name + '-bench')


def suite_dir(home, key):
    """The folder of one suite harness, by its key: each launch and resume makes its own, so a suite's trials can span
    several."""
    return bench_home(home) / 'suites' / key


def new_trial_dir(suite_folder, trial_key):
    """A fresh trial folder's path in the folder of the suite harness running it."""
    return Path(suite_folder) / 'trials' / (trial_key + '-' + uuid.uuid4().hex[:8])


def solo_trial_dir(bench, trial_id):
    """The folder of a trial no suite runs, under the bench home `bench`."""
    return Path(bench) / 'trials' / trial_id


def trials_under(suites):
    """Every trial folder under the folder of suite harnesses `suites` that has a record, in order."""
    return [path.parent for path in sorted(Path(suites).glob('*/trials/*/trial.json'))]


def read_trial(trial):
    """The record in the trial folder `trial`. ValueError, naming no path, when it is missing or cannot be read."""
    try:
        record = json.loads((Path(trial) / 'trial.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ValueError('A trial record cannot be read') from None
    if not isinstance(record, dict):
        raise ValueError('A trial record cannot be read')
    return record


def trial_dirs(home, suite_id=None):
    """Every trial folder with a readable record under the bench home of `home`, across all its suite harnesses, or only
    those of suite `suite_id`, chosen by the prefix of their `request_id`."""
    found = trials_under(bench_home(home) / 'suites')
    return found if suite_id is None else [folder for folder, _ in _suite_records(found, suite_id)]


def find_trial(home, suite_id, key):
    """The trial folder of suite `suite_id` named by `key`, a run id, a trial id or a child request id, or None. Among
    the folders one request can have, after a slot was reserved again with no run, it is the one that names a run, or
    else the latest that sent a start."""
    records = _suite_records(trials_under(bench_home(home) / 'suites'), suite_id)
    named = [folder for folder, record in records if key in (record.get('run_id'), record.get('trial_id'), folder.name)]
    if named:
        return named[0]
    return _owner([(folder, record) for folder, record in records if record.get('request_id') == key])


def readable_suites(home):
    """The id of each suite with a trial folder under the bench home of `home` that is not a stub, however it stands:
    concluded, abandoned or active. An unreadable record counts as its suite harness's suite."""
    found = set()
    for folder in sorted((bench_home(home) / 'suites').glob('*/trials/*')):
        try:
            record = read_trial(folder)
        except ValueError:
            if (folder / 'trial.json').exists() and _harness_suite(folder):
                found.add(_harness_suite(folder))
            continue
        request = str(record.get('request_id'))
        if not _is_stub(record) and request.startswith('suite:'):
            found.add(_slot_of(request)[0])
    return sorted(found)


def _owner(held, run_id=None):
    """Of `held`, (folder, record) under one request, the folder that ran its run, or None if `run_id` names one none of
    them names and none could have started it."""
    named = [folder for folder, record in held if record.get('run_id') is not None
             and (run_id is None or record.get('run_id') == run_id)]
    if named:
        return named[0]
    silent = [(record.get('start_sent') or 0, str(folder), folder) for folder, record in held
              if record.get('run_id') is None]
    return max(silent)[2] if silent else None


def _suite_records(folders, suite_id):
    """(folder, record) of each of `folders` whose record's `request_id` is one of suite `suite_id`'s."""
    prefix, chosen = 'suite:' + suite_id + ':trial:', []
    for folder in folders:
        try:
            record = read_trial(folder)
        except ValueError:
            continue
        if str(record.get('request_id')).startswith(prefix):
            chosen.append((folder, record))
    return chosen


def _seal_file(password, value, path):
    """Seal `value` into the file at `path`: the file's SHA-256. The key derivation lives here and in `_open_file`, so a
    test fixture can replace its cost."""
    return password.seal(value, path)


def _open_file(password, path, expected=None):
    """What the sealed file at `path` holds. ValueError for bytes whose hash is not `expected` (when given), the wrong
    password or a broken envelope."""
    data = Path(path).read_bytes()
    return password.unlock_bytes(data, expected or hashlib.sha256(data).hexdigest())


@contextlib.contextmanager
def _failing(step):
    """Whatever goes wrong inside is a `SealError` naming `step` and the error's class, and no path."""
    try:
        yield
    except SealError:
        raise
    except Exception as error:
        raise SealError(step + ' failed: ' + type(error).__name__) from None


def _reply(call, method, params):
    """One configurator request: a refusal or an unreachable configurator is a reply with its reason, never a raise."""
    try:
        reply = call(method, params)
    except Exception as error:
        return {'ok': False, 'reason': type(error).__name__}
    return reply if isinstance(reply, dict) else {'ok': False, 'reason': 'Unreadable configurator reply'}


def _sync_file(path):
    """Have the file at `path` reach the disk, and on POSIX its folder too (Windows cannot open a folder to sync it)."""
    with open(path, 'r+b') as stream:
        stream.flush()
        try:
            import fcntl
            fcntl.fcntl(stream.fileno(), fcntl.F_FULLFSYNC)
        except (ImportError, AttributeError, OSError):
            # Windows has no `fcntl`, only macOS has this flag, and a file system may refuse it: the plain sync stands.
            os.fsync(stream.fileno())
    if os.name != 'nt':
        folder = os.open(Path(path).parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


_Entry = namedtuple('_Entry', 'path relative kind mode size mtime')
_KINDS = ((stat.S_ISREG, 'file'), (stat.S_ISDIR, 'folder'), (stat.S_ISLNK, 'symlink'), (stat.S_ISFIFO, 'fifo'),
          (stat.S_ISSOCK, 'socket'))


def _kind(mode):
    return next((name for test, name in _KINDS if test(mode)), 'other')


def _entries(root, leave=lambda relative: False):
    """Each `_Entry` under the folder `root`, parents first, but what `leave(relative)` says to leave: a link is not
    followed. Its mode is fixed before anything reads or deletes it, so a worker's `chmod 000` stops neither; the entry
    keeps the mode it had."""
    try:
        if not stat.S_ISDIR(os.lstat(root).st_mode):
            return
    except FileNotFoundError:
        return
    os.chmod(root, stat.S_IMODE(os.lstat(root).st_mode) | 0o700)
    folders = [(Path(root), '')]
    while folders:
        folder, prefix = folders.pop()
        with os.scandir(folder) as found:
            names = sorted(found, key=lambda entry: entry.name)
        for name in names:
            relative = prefix + name.name
            if leave(relative):
                continue
            status = name.stat(follow_symlinks=False)
            kind, mode = _kind(status.st_mode), stat.S_IMODE(status.st_mode)
            if kind in ('file', 'folder'):
                os.chmod(name.path, mode | (0o700 if kind == 'folder' else 0o600))
            yield _Entry(Path(name.path), relative, kind, mode, status.st_size, status.st_mtime)
            if kind == 'folder':
                folders.append((Path(name.path), relative + '/'))


def _is_stub(record):
    return record.get('status') == SEALED


def _leave_in_trial(stub):
    """What of a trial folder's top level a seal neither holds nor deletes: its `*.enc` files, lock files and a writer's
    temporary files, which go unsealed, and `private/`, removed first. A stub is no member."""
    def leave(relative):
        if '/' in relative:
            return relative.startswith('private/')
        return (relative == 'private' or relative.endswith(('.enc', '.lock')) or (stub and relative == 'trial.json')
                or _WRITER_TEMPORARY.fullmatch(relative) is not None)
    return leave


def _writer_temporaries(trial):
    return [path for path in Path(trial).iterdir() if _WRITER_TEMPORARY.fullmatch(path.name)]


def _run_entries(run_dir):
    """(member name, `_Entry`) of the run folder but for `benchmark/execution.json` and `benchmark/inputs/`."""
    for entry in _entries(run_dir, lambda relative: relative == 'benchmark'):
        yield 'run/' + entry.relative, entry
    for entry in _entries(Path(run_dir) / 'benchmark', lambda relative: relative in ('execution.json', 'inputs')):
        yield 'run/benchmark/' + entry.relative, entry


def _member_entries(trial, run_dir, workspaces, *, stub):
    """(member name, `_Entry`) of everything a seal of this trial holds: the run folder but for
    `benchmark/execution.json` and `benchmark/inputs/`, which are public to that device's worker and which
    `_verify_suite` and `report_run` need; the trial folder but for what `_leave_in_trial` names; and each temporary
    interpret workspace, its folder itself included."""
    for entry in _entries(trial, _leave_in_trial(stub)):
        yield 'trial/' + entry.relative, entry
    if run_dir is not None:
        yield from _run_entries(run_dir)
    for parent in workspaces:
        parent = Path(parent)
        try:
            status = os.lstat(parent)
        except FileNotFoundError:
            continue
        name = 'workspaces/' + parent.name
        yield name, _Entry(parent, '', 'folder', stat.S_IMODE(status.st_mode), 0, status.st_mtime)
        for entry in _entries(parent):
            yield name + '/' + entry.relative, entry


class _Hashing:
    """A file being tarred: the hash is of the bytes the tar took, whatever the file goes on to hold."""

    def __init__(self, stream):
        self.stream, self.hash = stream, hashlib.sha256()

    def read(self, size=-1):
        data = self.stream.read(size)
        self.hash.update(data)
        return data


class _PartWriter(io.RawIOBase):
    """Where a generation's tar.gz goes: into sealed parts of at most `PART_BYTES` beside the trial, each synced."""

    def __init__(self, trial, generation, password):
        super().__init__()
        self.trial, self.generation, self.password = trial, generation, password
        self.pending, self.parts = bytearray(), []

    def writable(self):
        return True

    def write(self, data):
        self.pending += data
        while len(self.pending) >= PART_BYTES:
            self._seal_part(bytes(self.pending[:PART_BYTES]))
            del self.pending[:PART_BYTES]
        return len(data)

    def _seal_part(self, data):
        name = 'sealed-' + str(self.generation) + '-' + str(len(self.parts) + 1) + '.enc'
        sha256 = _seal_file(self.password, {'schema': _PART_SCHEMA, 'data': base64.b64encode(data).decode('ascii')},
                            self.trial / name)
        _sync_file(self.trial / name)
        self.parts.append({'name': name, 'sha256': sha256})

    def finish(self):
        if self.pending or not self.parts:
            self._seal_part(bytes(self.pending))
            self.pending.clear()
        return self.parts


def _tar_info(name, entry):
    info = tarfile.TarInfo(name)
    info.mode, info.mtime = entry.mode, entry.mtime
    info.type, info.size = (tarfile.REGTYPE, entry.size) if entry.kind == 'file' else (tarfile.DIRTYPE, 0)
    return info


def _open_member(path):
    """A file to tar, opened so that a link or a FIFO swapped in since the walk is neither followed nor waited for."""
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
    return os.fdopen(os.open(path, flags), 'rb')


def _write_generation(trial, generation, chosen, password):
    """Seal `chosen` ((member name, `_Entry`)) as a generation of `trial`, synced to disk, its index written last, so an
    index means every part is whole. Links, FIFOs and sockets are listed as skipped and never read. Its index, with the
    hash of each file as the tar took it, not as the disk has it now, so a file still growing cannot fail its
    verification."""
    members, skipped = {}, []
    sink = _PartWriter(trial, generation, password)
    with tarfile.open(fileobj=sink, mode='w|gz') as tar:
        for name, entry in chosen:
            if entry.kind == 'folder':
                tar.addfile(_tar_info(name, entry))
                members[name] = {'kind': 'folder', 'path': str(entry.path), 'mode': entry.mode}
            elif entry.kind == 'file':
                with _open_member(entry.path) as stream:
                    hashing = _Hashing(stream)
                    tar.addfile(_tar_info(name, entry), hashing)
                members[name] = {'kind': 'file', 'path': str(entry.path), 'mode': entry.mode, 'size': entry.size,
                                 'sha256': hashing.hash.hexdigest()}
            else:
                skipped.append({'name': name, 'kind': entry.kind, 'path': str(entry.path)})
    index = {'schema': _INDEX_SCHEMA, 'generation': generation, 'members': members, 'skipped': skipped,
             'parts': sink.finish()}
    path = Path(trial) / ('sealed-' + str(generation) + '-index.enc')
    _seal_file(password, index, path)
    _sync_file(path)
    return index


class _PartReader(io.RawIOBase):
    """A generation's tar.gz read back, one part in memory at a time, each checked against the hash its index holds."""

    def __init__(self, paths_and_hashes, password):
        super().__init__()
        self.waiting, self.password = list(paths_and_hashes), password
        self.current, self.offset = b'', 0

    def readable(self):
        return True

    def readinto(self, buffer):
        while self.offset >= len(self.current):
            if not self.waiting:
                return 0
            path, sha256 = self.waiting.pop(0)
            self.current, self.offset = base64.b64decode(_open_file(self.password, path, sha256)['data']), 0
        size = min(len(buffer), len(self.current) - self.offset)
        buffer[:size] = self.current[self.offset:self.offset + size]
        self.offset += size
        return size


def _stream_members(paths_and_hashes, password):
    """(tar member, its file or None) of a generation's tar.gz, in order. Only plain files and folders with relative
    paths are accepted."""
    reader = _PartReader(paths_and_hashes, password)
    with tarfile.open(fileobj=reader, mode='r|gz') as tar:
        for member in tar:
            parts = member.name.replace('\\', '/').split('/')
            if not (member.isfile() or member.isdir()) or member.name.startswith('/') or '..' in parts:
                raise SealError('A sealed trial holds something other than plain files and folders inside it')
            yield member, tar.extractfile(member) if member.isfile() else None


def _verify_generation(trial, index, password):
    """Unlock a generation and compare every member with its index, or `SealError`."""
    hashes = {name: row['sha256'] for name, row in index['members'].items() if row['kind'] == 'file'}
    seen = {}
    paths = [(Path(trial) / part['name'], part['sha256']) for part in index['parts']]
    for member, stream in _stream_members(paths, password):
        if stream is not None:
            digest = hashlib.sha256()
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
            seen[member.name] = digest.hexdigest()
    if seen != hashes:
        raise SealError('A sealed trial differs from its index')


def _prove_password(trial, password):
    """Unlock the trial's evaluation token, so plaintext is never deleted under a seal the real password cannot open. A
    no-run trial with no token has nothing to check."""
    path = Path(trial) / TOKEN
    if not path.exists():
        return
    try:
        _open_file(password, path)
    except ValueError:
        raise SealError('The password does not unlock this trial\'s evaluation token') from None


def _remove_file(path):
    """Delete one file or link, retrying as `harness._private` does: a reader on Windows may hold it open a moment."""
    for attempt in range(_DELETE_ATTEMPTS):
        try:
            return os.unlink(path)
        except FileNotFoundError:
            return None
        except PermissionError:
            if attempt == _DELETE_ATTEMPTS - 1:
                raise
            time.sleep(_DELETE_PAUSE)


def _remove_folder(path):
    for attempt in range(_DELETE_ATTEMPTS):
        try:
            return os.rmdir(path)
        except FileNotFoundError:
            return None
        except OSError:
            if attempt == _DELETE_ATTEMPTS - 1:
                raise
            time.sleep(_DELETE_PAUSE)


def _remove_entries(entries):
    """Delete `entries`, deepest first, with no `ignore_errors`."""
    for entry in sorted(entries, key=lambda entry: len(entry.path.parts), reverse=True):
        (_remove_folder if entry.kind == 'folder' else _remove_file)(entry.path)


def _remove_tree(path):
    """Delete the folder `path` and everything in it."""
    _remove_entries(list(_entries(path)))
    if os.path.lexists(path):
        _remove_folder(path)


def _delete_plaintext(trial, run_dir, workspaces):
    """Delete everything a seal of this trial holds, the workspaces first: then a crash here leaves the run's plaintext,
    which says that something is left, without reading the run's events. Then check it is gone."""
    for parent in workspaces:
        _remove_tree(parent)
    leftover = _plaintext(trial, run_dir, workspaces, stub=True)
    _remove_entries(leftover)
    for path in _writer_temporaries(trial):
        _remove_file(path)
    if (_plaintext(trial, run_dir, workspaces, stub=True) or _writer_temporaries(trial)
            or (Path(trial) / 'private').exists()):
        raise SealError('A sealed trial\'s plaintext could not be deleted')


def _plaintext(trial, run_dir, workspaces, *, stub):
    """Each `_Entry` of this trial still on disk that a seal would hold."""
    return [entry for _, entry in _member_entries(trial, run_dir, workspaces, stub=stub)]


def _plaintext_of_run(run_dir):
    """Whether the run folder holds anything but its two kept paths."""
    return any(True for _ in _run_entries(run_dir))


def _slot_of(request_id):
    """(suite id, ordinal) of a slot's child request, `suite:<id>:trial:<ordinal>` with `:rerun` on a rerun."""
    parts = str(request_id).split(':')
    return parts[1], int(parts[3])


def _mark_sealed(call, record, run_id, token):
    """Tell the configurator the run is sealed: repeatable, since a crash may send it again."""
    suite_id, ordinal = _slot_of(record['request_id'])
    reply = _reply(call, 'suite_sealed', {'suite_id': suite_id, 'ordinal': ordinal, 'run_id': run_id,
                                          **({'token': token} if token else {})})
    if reply.get('ok') is not True:
        raise SealError('The configurator did not mark a sealed run')


def _stub(record, run_id, files):
    from .harness import TRIAL
    return {'schema': record.get('schema', TRIAL), 'status': SEALED, 'trial_id': record.get('trial_id'),
            'request_id': record.get('request_id'), 'run_id': run_id, 'case': record.get('case'),
            'start_sent': record.get('start_sent'), 'finished': record.get('finished') or time.time(),
            'sealed_files': files}


def _generation_of(name):
    """The generation in the name of a sealed file, or None."""
    found = _SEALED_NAME.fullmatch(name)
    return int(found.group(1)) if found else None


def _local_generations(trial):
    """The generations of `trial` whose index is beside it, in order: a generation with no index never finished."""
    return sorted(_generation_of(path.name) for path in Path(trial).glob('sealed-*-index.enc')
                  if _generation_of(path.name) is not None)


def _next_generation(trial, record):
    """A generation number no file of this trial has used, here or, as its stub lists them, in the vault."""
    names = [path.name for path in Path(trial).glob('sealed-*.enc')]
    names += [row['name'] for row in record.get('sealed_files', ())]
    return 1 + max((_generation_of(name) or 0 for name in names), default=0)


def _sealed_files(trial, record, indexes):
    """Each file the stub lists: those an earlier stub lists, which may have moved to the vault, and `indexes`'."""
    files = {row['name']: row for row in record.get('sealed_files', ())}
    for index in indexes:
        for name in [part['name'] for part in index['parts']] + ['sealed-' + str(index['generation']) + '-index.enc']:
            path = Path(trial) / name
            files[name] = {'name': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                           'time': os.stat(path).st_mtime}
    return sorted(files.values(), key=lambda row: (_generation_of(row['name']), row['name']))


def _file_hash(path):
    digest = hashlib.sha256()
    with _open_member(path) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _changed(entries, indexes):
    """Those of `entries` ((member name, `_Entry`)) that the latest generation of each member does not hold as they are
    now: a file whose hash differs, or anything it has no record of. A member missing from disk is no disagreement: that
    is a partial delete."""
    members, skipped = {}, set()
    for index in indexes:
        members.update(index['members'])
        skipped.update(row['name'] for row in index['skipped'])
    def differs(name, entry):
        if entry.kind == 'file':
            return members.get(name, {}).get('sha256') != _file_hash(entry.path)
        return name not in (members if entry.kind == 'folder' else skipped)
    return [(name, entry) for name, entry in entries if differs(name, entry)]


def seal_trial(trial, run_dir, workspaces, password, call, token):
    """Seal one concluded trial and return its stub; `run_dir` is None for a trial with no run, and `workspaces` are the
    folders `run_workspaces` names. In order: prove `password` (`_prove_password`), remove `private/`, write the seal
    and sync it, unlock it and compare every member, write the stub and sync it, tell the configurator (`call`, with the
    harness's `token` if it has one), delete the plaintext and check it is gone. So no plaintext is deleted before its
    seal has verified. A trial with a seal already is decided from what is on disk, and no seal is overwritten: every
    generation is verified, what matches is deleted, and a changed or extra file goes in a further generation. A stub
    with nothing left beside it is returned as it is, and nothing is unlocked. Any failure is a `SealError`."""
    trial = Path(trial)
    with _failing('Sealing a concluded trial'):
        record = read_trial(trial)
        stub = _is_stub(record)
        if stub and not _plaintext(trial, run_dir, workspaces, stub=True):
            return record
        _prove_password(trial, password)
        if (trial / 'private').exists():
            _remove_tree(trial / 'private')
        run_id = Path(run_dir).name if run_dir is not None else record.get('run_id')
        indexes = [_open_file(password, trial / ('sealed-' + str(generation) + '-index.enc'))
                   for generation in _local_generations(trial)]
        for index in indexes:
            _verify_generation(trial, index, password)
        entries = list(_member_entries(trial, run_dir, workspaces, stub=stub))
        chosen = _changed(entries, indexes) if indexes else entries
        if chosen:
            index = _write_generation(trial, _next_generation(trial, record), chosen, password)
            _verify_generation(trial, index, password)
            indexes.append(index)
        written = _stub(record, run_id, _sealed_files(trial, record, indexes))
        from . import harness
        harness._private(trial / 'trial.json', written)
        _sync_file(trial / 'trial.json')
        if run_id is not None:
            _mark_sealed(call, written, run_id, token)
        _delete_plaintext(trial, run_dir, workspaces)
        return written


def run_workspaces(call, run_id, run_dir):
    """The temporary interpret workspaces' folders of a run, from its `stage.assigned` events, each once. Only a path
    shaped `.../gd-candidate-*/attempt<N>` outside the run is accepted, and its parent goes with it: any other shape is
    a `SealError`, before anything is deleted, so a worker cannot aim the harness at another folder."""
    found, after = [], 0
    while True:
        page = _reply(call, 'events', {'run_id': run_id, 'after': after})
        if page.get('ok') is not True:
            raise Unreachable('The configurator did not report a run\'s events')
        for event in page['events']:
            data = event.get('data') or {}
            if event.get('kind') == 'stage.assigned' and data.get('stage') == 'interpret':
                parent = _workspace_folder(data.get('workspace'), run_dir)
                if parent not in found:
                    found.append(parent)
        if page['cursor'] == after or len(page['events']) < 500:
            return found
        after = page['cursor']


def _workspace_folder(named, run_dir):
    """The folder `gd-candidate-*` holding the workspace `named`, or `SealError`."""
    path = Path(named) if isinstance(named, str) else None
    good = (path is not None and path.is_absolute() and '..' not in path.parts
            and _WORKSPACE_ATTEMPT.fullmatch(path.name) is not None
            and _WORKSPACE_FOLDER.fullmatch(path.parent.name) is not None and not path.parent.is_symlink()
            and not path.parent.resolve().is_relative_to(Path(run_dir).resolve()))
    if not good:
        raise SealError('A run names a stage workspace of the wrong shape')
    return path.parent


def _suite_rows(call, suite_id):
    """(every slot row of the suite, its comparison identity), as the configurator reports them, or `Unreachable`."""
    rows, offset, identity = [], 0, {}
    while True:
        page = _reply(call, 'suite_result', {'suite_id': suite_id, 'offset': offset, 'limit': 100})
        if page.get('ok') is not True:
            raise Unreachable('The configurator did not report the suite, so its concluded trials were not swept')
        identity = (page.get('experiment') or {}).get('comparison_identity') or {}
        rows.extend(page.get('trials') or [])
        offset = page.get('next_offset')
        if offset is None:
            return rows, identity


def _request_of(suite_id, row):
    """A slot's current child request, from its ordinal and attempt."""
    return 'suite:' + suite_id + ':trial:' + str(row['ordinal']) + (':rerun' if row.get('attempt') == 2 else '')


def _targets(suite_id, rows, every):
    """(row, request id, run id) of each attempt to seal. Concluded means a slot `finished`, `faulted` or `flagged`, or
    a `first_attempt` whose request differs from the slot's current one; so the current request of an active slot,
    reserved or not, is left alone, and a regrade's restored plaintext is safe. `every` takes each slot's current
    attempt too."""
    for row in rows:
        current, first = _request_of(suite_id, row), row.get('first_attempt') or {}
        if every or row['status'] in _CONCLUDED:
            yield row, current, row.get('run_id')
        if first.get('request_id') not in (None, current):
            yield row, first['request_id'], first.get('run_id')


def _suite_of_harness(harness_folder):
    """The suite id in the record of the suite harness with this folder, or None."""
    try:
        return json.loads((Path(harness_folder) / 'suite.json').read_text(encoding='utf-8')).get('suite_id')
    except (OSError, ValueError, AttributeError):
        return None


def _harness_suite(trial):
    """The suite id in the record of the suite harness whose trial folder this is, or None."""
    return _suite_of_harness(Path(trial).parent.parent)


def _refuse_damaged(home, suite_id):
    """`SealError` for a trial folder of this suite whose record is unreadable, or is missing beside a seal. A record
    says which suite its folder is of, so here its harness's record does, and an unreadable one counts as this suite's.
    A folder with neither a record nor a seal is a trial that never began."""
    for folder in sorted((bench_home(home) / 'suites').glob('*/trials/*')):
        try:
            read_trial(folder)
            continue
        except ValueError:
            pass
        if ((folder / 'trial.json').exists() or any(folder.glob('sealed-*.enc'))) and (
                _harness_suite(folder) in (suite_id, None)):
            raise SealError('A trial record beside a concluded trial cannot be read')


def _made_from_row(home, suite_id, row, request, run_id):
    """A trial folder for a run that has none, made from its slot's row. Its record is then sealed like any other."""
    from . import harness
    key = next((path.parent.name for path in sorted((bench_home(home) / 'suites').glob('*/suite.json'))
                if _suite_of_harness(path.parent) == suite_id), 'suite-run-' + uuid.uuid4().hex[:16])
    folder = new_trial_dir(suite_dir(home, key), row['trial_key'])
    folder.mkdir(parents=True, mode=0o700)
    harness._private(folder / 'trial.json', {'schema': harness.TRIAL, 'trial_id': folder.name, 'request_id': request,
                                             'case': row['case'], 'run_id': run_id, 'status': 'made-from-row',
                                             'start_sent': None, 'finished': time.time()})
    return folder


def _prove_on_a_neighbour(home, suite_id, password):
    """Prove `password` on another trial of the suite, for a folder that has no token of its own."""
    for folder in trial_dirs(home, suite_id):
        if (folder / TOKEN).exists():
            return _prove_password(folder, password)
    return None


def _seal_found(folder, record, run_dir, password, call, token):
    """Seal one folder unless nothing is left of it. Whether anything is left is told without the run's events: the
    delete takes the workspaces first."""
    if _is_stub(record) and not _plaintext(folder, run_dir, [], stub=True):
        return
    workspaces = run_workspaces(call, Path(run_dir).name, run_dir) if run_dir is not None else []
    seal_trial(folder, run_dir, workspaces, password, call, token)


def _sweep(home, suite_id, password, call, token, every=False):
    rows, identity = _suite_rows(call, suite_id)
    if 'scoring_set' not in identity:
        return
    with _failing('Sweeping a suite\'s concluded trials'):
        _refuse_damaged(home, suite_id)
        records = _suite_records(trials_under(bench_home(home) / 'suites'), suite_id)
        for row, request, run_id in _targets(suite_id, rows, every):
            held = [(folder, record) for folder, record in records if record.get('request_id') == request]
            if not held and not run_id:
                if every:
                    continue
                raise SealError('A concluded trial has neither a stub nor plaintext')
            run_dir = Path(home) / 'runs' / run_id if run_id else None
            owner = _owner(held, run_id) if run_id else None
            if run_id and owner is None:
                if not _plaintext_of_run(run_dir):
                    raise SealError('A concluded trial has neither a stub nor plaintext')
                _prove_on_a_neighbour(home, suite_id, password)
                owner = _made_from_row(home, suite_id, row, request, run_id)
                held = [*held, (owner, read_trial(owner))]
            for folder, record in held:
                _seal_found(folder, record, run_dir if folder == owner else None, password, call, token)


def sweep(home, suite_id, password, call, token=None):
    """Seal any concluded trial of the suite `suite_id` still readable, scanning the whole bench home of `home`, since a
    suite's trials can span several harnesses' folders (`_targets`). Each is decided from what is on disk: no seal and
    plaintext is sealed; a valid seal and plaintext is finished (`seal_trial`); a seal and no plaintext, or a stub with
    no local seal and no plaintext, as after `store`, is done; no stub and no plaintext stops the suite. `token` is the
    harness's, with none when no harness is attached. A failure is a `SealError`, and a configurator it cannot read
    `Unreachable`, one of them. A suite that is not scoring is left alone."""
    _sweep(home, suite_id, password, call, token)


def _vault_folder(vault, suite_id, trial):
    """Where `store` keeps the seals of one trial folder under the vault folder `vault`."""
    return Path(vault) / 'sealed-trials' / suite_id / Path(trial).name


def _located(name, trial, suite_id, vault):
    """The sealed file `name` of `trial`, beside it or, given the vault folder, where `store` moved it."""
    for path in (Path(trial) / name, *((_vault_folder(vault, suite_id, trial) / name,) if vault is not None else ())):
        if path.exists():
            return path
    raise SealError('A sealed file is neither beside its trial nor in the vault folder given')


def _indexes(trial, record, suite_id, password, vault):
    """Every generation's index of `trial`, oldest first: those its stub lists and those beside it."""
    names = {row['name'] for row in record.get('sealed_files', ())}
    listed = {_generation_of(name) for name in names if name.endswith('-index.enc')}
    numbers = sorted(listed | set(_local_generations(trial)))
    return [_open_file(password, _located('sealed-' + str(number) + '-index.enc', trial, suite_id, vault))
            for number in numbers]


def _copy_stream(stream, path, mode, expected):
    """Write a member to `path`, private to this user whatever mode it was sealed with, and check its hash."""
    digest = hashlib.sha256()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as target:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            target.write(chunk)
    os.chmod(path, mode | 0o600)
    if digest.hexdigest() != expected:
        raise SealError('A sealed trial differs from its index')


def _each_member(trial, indexes, suite_id, password, vault):
    """(member name, its index row, its file or None) of each member's latest generation, the oldest first."""
    latest = {name: index['generation'] for index in indexes for name in index['members']}
    for index in indexes:
        paths = [(_located(part['name'], trial, suite_id, vault), part['sha256']) for part in index['parts']]
        for member, stream in _stream_members(paths, password):
            row = index['members'].get(member.name)
            if row is None or (stream is None) != (row['kind'] == 'folder'):
                raise SealError('A sealed trial differs from its index')
            if latest[member.name] == index['generation']:
                yield member.name, row, stream


def _read_seal_into(root, trial, record, suite_id, password, vault):
    if password is None:
        raise SealError('A sealed trial opens only with its set\'s password file')
    indexes = _indexes(trial, record, suite_id, password, vault)
    for name, row, stream in _each_member(trial, indexes, suite_id, password, vault):
        target = root / name
        if stream is None:
            target.mkdir(parents=True, exist_ok=True)
        else:
            _copy_stream(stream, target, 0o600, row['sha256'])


def _copy_plaintext(root, trial, run_dir):
    """Copy a trial still plaintext into `root`, as `_read_seal_into` would lay it out. Links, FIFOs and sockets are not
    read, as a seal would skip them."""
    for name, entry in _member_entries(trial, run_dir, [], stub=False) if trial is not None else _run_entries(run_dir):
        target = root / name
        if entry.kind == 'folder':
            target.mkdir(parents=True, exist_ok=True)
        elif entry.kind == 'file':
            target.parent.mkdir(parents=True, exist_ok=True)
            with _open_member(entry.path) as source, open(target, 'wb') as copy:
                shutil.copyfileobj(source, copy)


def _read_into(root, home, suite_id, trial, password, vault):
    folder = Path(trial) if isinstance(trial, Path) else find_trial(home, suite_id, trial)
    record = None
    if folder is not None:
        record = read_trial(folder) if (folder / 'trial.json').exists() else None
        if record is None or not str(record.get('request_id')).startswith('suite:' + suite_id + ':trial:'):
            raise SealError('No such trial of this suite')
    run_id = record.get('run_id') if record else str(trial)
    run_dir = Path(home) / 'runs' / run_id if run_id else None
    if record is None and not (run_dir.is_dir() and _plaintext_of_run(run_dir)):
        raise SealError('No such trial of this suite')
    if record is not None and (_is_stub(record) or _local_generations(folder)):
        _read_seal_into(root, folder, record, suite_id, password, vault)
    else:
        _copy_plaintext(root, folder, run_dir)


@contextlib.contextmanager
def opened(home, suite_id, trial, password, *, vault=None):
    """The one way to read a concluded trial back: a private temporary folder, removed afterwards, with fixed `run/` and
    `trial/` and the workspaces in `workspaces/`, outside `run/`. `trial` is a run id, a trial id or a trial folder. A
    seal is read from beside its trial or, given `vault`, from where `store` moved it, each member from its latest
    generation; a trial still plaintext on a blocked suite opens the same way, and stays as it is. Any failure to open
    it is a `SealError`."""
    with tempfile.TemporaryDirectory(prefix='gd-opened-') as temporary:
        root = Path(temporary)
        (root / 'run').mkdir()
        (root / 'trial').mkdir()
        with _failing('Opening a concluded trial'):
            _read_into(root, home, suite_id, trial, password, vault)
        yield root


def _write_restored(name, row, stream):
    """Put one member back at its original path, its mode as it was but readable by this user. Its file is replaced, as
    a worker may have closed what a crash left."""
    path = Path(row['path'])
    if stream is None:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, row['mode'] | 0o700)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(path):
        os.unlink(path)
    _copy_stream(stream, path, row['mode'], row['sha256'])


def restore(trial, password):
    """Put a sealed trial due a regrade back at its original paths, keyed on the stub on disk, so a repeat changes
    nothing. `trial.json` goes last and in one step, so a crash leaves the stub, and a repeat finishes. A failure, as
    for a trial whose seals `store` has moved, is a `SealError`."""
    trial = Path(trial)
    with _failing('Restoring a sealed trial'):
        record = read_trial(trial)
        if not _is_stub(record):
            return
        suite_id, _ = _slot_of(record['request_id'])
        for row in record.get('sealed_files', ()):
            if not (trial / row['name']).exists():
                raise SealError('A sealed trial is no longer beside its stub')
        original = None
        indexes = _indexes(trial, record, suite_id, password, None)
        for name, row, stream in _each_member(trial, indexes, suite_id, password, None):
            if name == 'trial/trial.json':
                original = stream.read()
                if hashlib.sha256(original).hexdigest() != row['sha256']:
                    raise SealError('A sealed trial differs from its index')
            else:
                _write_restored(name, row, stream)
        temporary = trial / ('.trial.json.' + uuid.uuid4().hex)
        temporary.write_bytes(original)
        os.replace(temporary, trial / 'trial.json')
        _sync_file(trial / 'trial.json')


def _move_to_vault(path, target, password):
    """Move one sealed file to `target`, verifying the copy by unlocking it before the original goes. A file already
    there with other bytes is never replaced."""
    data = path.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise SealError('A file in the vault folder differs from the seal that would replace it')
    else:
        temporary = target.with_name('.' + target.name + '.' + uuid.uuid4().hex)
        temporary.write_bytes(data)
        _sync_file(temporary)
        os.replace(temporary, target)
    _open_file(password, target, hashlib.sha256(data).hexdigest())
    _sync_file(target)
    _remove_file(path)


def store(home, suite_id, vault, password, call):
    """Sweep the suite, then move only its `sealed-*.enc` files under `<vault>/sealed-trials/<suite id>/`: stubs and
    other `*.enc` files stay. It takes a completed suite, or a cancelled one with nothing stopping, on which it seals
    and marks every readable trial, an unsettled slot's too, so cancel then store is how to abandon a suite. A failure
    part way leaves every seal in at least one place, and a repeat finishes the job. The vault folder is only a path,
    and it must be there, so that a closed vault's mount point is not filled."""
    status = _reply(call, 'suite_status', {'suite_id': suite_id})
    if status.get('ok') is not True:
        raise Unreachable('The configurator did not report the suite, so it was not stored')
    if status.get('status') not in ('completed', 'cancelled') or status.get('stopping') is not False:
        raise ValueError('benchmark suite store takes a completed suite, or a cancelled one with nothing stopping: '
                         + str(status.get('status')))
    if not Path(vault).is_dir():
        raise ValueError('The vault folder is not there; open the vault first')
    _, identity = _suite_rows(call, suite_id)
    if 'scoring_set' not in identity:
        raise ValueError('Only a scoring suite seals its trials: ' + suite_id)
    _sweep(home, suite_id, password, call, None, every=status['status'] == 'cancelled')
    with _failing('Storing a suite\'s sealed trials'):
        for folder in trial_dirs(home, suite_id):
            for path in sorted(folder.glob('sealed-*.enc')):
                _move_to_vault(path, _vault_folder(vault, suite_id, folder) / path.name, password)
