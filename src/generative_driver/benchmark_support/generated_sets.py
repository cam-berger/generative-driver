"""Generated device sets: bundles outside this repository, registered by the hash of their manifest.

A set is a directory that the operator keeps outside the repository and the installed package (ADR 0003):

    manifest.json             plaintext; lists every device, and its SHA-256 is what the set's registration pins
    qualification.enc         sealed qualification record (`set_qualification.qualify`), which `admitted` reads
    devices/<id>.inputs.enc   sealed {"image_base64", "mission"}: the public inputs of a trial
    devices/<id>.answer.enc   sealed answer key: opened only by grading and qualification

The registrations are code-owned data: `resources/registrations/sets.json` (set name -> manifest SHA-256), installed beside
`cases.json` and read relative to the package, never through `case_root()`, which an environment variable can redirect. They
lie outside every grader identity, so registering a set changes no case's admission; a comparison carries the manifest hash
instead. Each file is checked on the way in: the manifest against its registration, each sealed file against the manifest,
and each plaintext against its content hash. Nothing is returned before every check has passed.
"""
import base64
import hashlib
import json
import re
from pathlib import Path

from . import truth
from .generated_device import validate_device, validate_identity
from .grader_identity import grader_identity
from .snapshots import canonical_digest

QUALIFICATION_SCHEMA = 'generated-set-qualification/1'
_QUALIFICATION_FILE = 'qualification.enc'
_QUALIFICATION_FIELDS = {'schema', 'registration', 'manifest_sha256', 'grader_identity', 'status', 'devices', 'sample',
                         'mutants', 'failures', 'seconds', 'runs'}
_MANIFEST_FILE = 'manifest.json'
_MANIFEST_SCHEMA = 'generated-set-manifest/1'
_REGISTRATIONS_SCHEMA = 'generated-set-registrations/1'
_SETS_FILE = Path(__file__).resolve().parents[1] / 'resources' / 'registrations' / 'sets.json'
_SHA256 = re.compile(r'[0-9a-f]{64}')
_ENTRY_FIELDS = {'id', 'level', 'framing', 'image_sha256', 'mission_sha256', 'answer_key_sha256', 'inputs', 'answer'}


def _unique_keys(pairs):
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError('Duplicate key')
    return dict(pairs)


def _read_json(data, what):
    """The JSON value of the UTF-8 `data`, refusing a duplicate key. ValueError names `what` for anything unreadable."""
    try:
        return json.loads(data.decode('utf-8'), object_pairs_hook=_unique_keys)
    except ValueError as error:  # a JSON error, undecodable text and a duplicate key are all ValueErrors
        raise ValueError('Cannot read ' + what + ': ' + str(error)) from error


def _is_sha256(value):
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def set_registrations(path: Path | None = None) -> dict[str, str]:
    """Registered set name -> manifest SHA-256, from the table at `path`, by default the code-owned one. The table is
    `{"schema": "generated-set-registrations/1", "sets": {name: sha256}}`. ValueError for a duplicate key, another schema,
    an unknown or missing field, or a pin that is not 64 lowercase hexadecimal characters; a table that cannot be read
    raises OSError."""
    file = _SETS_FILE if path is None else Path(path)
    table = _read_json(file.read_bytes(), 'generated set registrations ' + str(file))
    if (not isinstance(table, dict) or set(table) != {'schema', 'sets'} or table['schema'] != _REGISTRATIONS_SCHEMA
            or not isinstance(table['sets'], dict)):
        raise ValueError('Unsupported generated set registrations: ' + str(file))
    if not all(_is_sha256(pin) for pin in table['sets'].values()):
        raise ValueError('A generated set registration pins its manifest by a lowercase hexadecimal SHA-256')
    return dict(table['sets'])


def _inside(set_root, relative):
    """The path that the manifest path `relative` names below `set_root`. It must be a relative POSIX path of plain
    names: a backslash, a colon (a Windows drive or stream), a leading slash and an empty, `.` or `..` name are refused.
    It must also lie inside the set once symlinks resolve. ValueError otherwise."""
    if (not isinstance(relative, str) or any(char in relative for char in '\\:\0')
            or any(name in ('', '.', '..') for name in relative.split('/'))):
        raise ValueError('A set path must be a relative POSIX path of plain names: ' + repr(relative))
    root = Path(set_root).resolve()
    path = root.joinpath(*relative.split('/')).resolve()
    if not path.is_relative_to(root):
        raise ValueError('A set path leaves the set: ' + relative)
    return path


def _check_entry(set_root, entry):
    """ValueError unless `entry` names every field of a device, validly, and its two files lie inside the set."""
    if not isinstance(entry, dict) or set(entry) != _ENTRY_FIELDS:
        raise ValueError('Unknown or missing device fields')
    validate_identity(entry)
    if not all(_is_sha256(entry[name]) for name in ('image_sha256', 'mission_sha256', 'answer_key_sha256')):
        raise ValueError('Invalid content hash of device ' + entry['id'])
    for name in ('inputs', 'answer'):
        file = entry[name]
        if not isinstance(file, dict) or set(file) != {'path', 'sha256'} or not _is_sha256(file['sha256']):
            raise ValueError('Invalid ' + name + ' file of device ' + entry['id'])
        _inside(set_root, file['path'])


def _check_manifest(set_root, manifest, registration):
    """ValueError unless `manifest` is a well-formed manifest of set `registration` whose files lie inside `set_root`."""
    if (not isinstance(manifest, dict) or set(manifest) != {'schema', 'set', 'devices'}
            or manifest['schema'] != _MANIFEST_SCHEMA or not isinstance(manifest['devices'], list)):
        raise ValueError('Unsupported generated set manifest')
    if manifest['set'] != registration:
        raise ValueError('The manifest names set ' + repr(manifest['set']) + ', not ' + repr(registration))
    if not manifest['devices']:  # an empty set would qualify vacuously
        raise ValueError('The manifest of set ' + repr(registration) + ' lists no device')
    for entry in manifest['devices']:
        _check_entry(set_root, entry)
    ids = [entry['id'] for entry in manifest['devices']]
    if len(set(ids)) != len(ids):
        raise ValueError('Duplicate device id in the manifest of set ' + registration)


def seal_device(set_root: Path, device: dict, *, image: bytes, mission: dict, answer_key: dict, password: str) -> dict:
    """Seal one device under `set_root/devices/`, creating it: its public inputs `{"image_base64", "mission"}` and its
    answer key, each through `truth.seal`. `device` names the `id` (eight lowercase hexadecimal digits), `level` (1 to 5)
    and `framing` (one of `generated_device.FRAMINGS`).
    Returns the device's manifest entry. Before anything is written: ValueError for an invalid device, an image that is
    not bytes, a mission or answer key that is not an object, a device that breaks the generated-device interface
    (`generated_device.validate_device`) or an unusable password; and, from `canonical_digest`, TypeError for content
    that JSON cannot carry (a set, bytes) and ValueError for NaN or text that cannot be encoded."""
    validate_identity(device)
    if not isinstance(image, bytes):
        raise ValueError('The image of a device is bytes')
    if not isinstance(mission, dict):
        raise ValueError('The mission of a device is a JSON object')
    if not isinstance(answer_key, dict):
        raise ValueError('The answer key of a device is a JSON object')
    identity = device['id']
    entry = {'id': identity, 'level': device['level'], 'framing': device['framing'],
             'image_sha256': hashlib.sha256(image).hexdigest(), 'mission_sha256': canonical_digest(mission),
             'answer_key_sha256': canonical_digest(answer_key)}
    validate_device(device, image=image, mission=mission, key=answer_key)
    sealed = {'inputs': {'image_base64': base64.b64encode(image).decode('ascii'), 'mission': mission},
              'answer': answer_key}
    for name, content in sealed.items():
        path = 'devices/' + identity + '.' + name + '.enc'
        entry[name] = {'path': path, 'sha256': truth.seal(content, Path(set_root) / path, password)}
    return entry


def write_manifest(set_root: Path, registration: str, devices: list[dict]) -> str:
    """Write `set_root/manifest.json` for the set `registration` from the entries `seal_device` returned, and return the
    SHA-256 of the bytes written, which the set's registration pins. ValueError, with nothing written, for a manifest that
    `load_set` would refuse."""
    manifest = {'schema': _MANIFEST_SCHEMA, 'set': registration, 'devices': list(devices)}
    _check_manifest(set_root, manifest, registration)
    data = (json.dumps(manifest, indent=2) + '\n').encode('utf-8')
    (Path(set_root) / _MANIFEST_FILE).write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def load_set(set_root: Path, registration: str, *, registrations: dict | None = None) -> dict:
    """The manifest of set `registration` under `set_root`, with `manifest_sha256` added, once the SHA-256 of its bytes is
    the one `registrations` pins (by default `set_registrations()`) and it is well formed: its schema and set name, at
    least one device, unique ids, valid levels and framings, and every path inside `set_root`. ValueError otherwise; a
    manifest that cannot be read raises OSError."""
    pins = set_registrations() if registrations is None else registrations
    if registration not in pins:
        raise ValueError('Generated set not registered: ' + str(registration))
    data = (Path(set_root) / _MANIFEST_FILE).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != pins[registration]:
        raise ValueError('The ' + _MANIFEST_FILE + ' of ' + registration + ' is not the registered manifest: ' + digest)
    manifest = _read_json(data, 'the manifest of ' + registration)
    _check_manifest(set_root, manifest, registration)
    return {**manifest, 'manifest_sha256': digest}


def open_device(set_root: Path, manifest: dict, device_id: str, password: str, *, answer: bool = False) -> dict:
    """One device of a loaded set: `{'image': bytes, 'mission': dict}`, and `'answer_key'` when `answer` is true. Without
    it the answer file is never read, let alone decrypted. Each file goes through `truth.unlock` with its manifest hash,
    and the image, the mission and the answer key are checked against their content hashes, then against the
    generated-device interface (`generated_device.validate_device`), the inputs alone without `answer`. ValueError for
    any mismatch or broken rule, the wrong password or an unknown device, before anything is returned; a file that
    cannot be read raises OSError."""
    entry = next((listed for listed in manifest['devices'] if listed.get('id') == device_id), None)
    if entry is None:
        raise ValueError('Unknown generated device: ' + str(device_id))
    # Each path resolves again where it is used: a symlink can change after the set loaded.
    sealed = truth.unlock(_inside(set_root, entry['inputs']['path']), password, entry['inputs']['sha256'])
    if (not isinstance(sealed, dict) or set(sealed) != {'image_base64', 'mission'}
            or not isinstance(sealed['image_base64'], str) or not isinstance(sealed['mission'], dict)):
        raise ValueError('Malformed inputs of device ' + device_id)
    image = base64.b64decode(sealed['image_base64'], validate=True)
    if hashlib.sha256(image).hexdigest() != entry['image_sha256']:
        raise ValueError('The image of device ' + device_id + ' does not match its manifest hash')
    if canonical_digest(sealed['mission']) != entry['mission_sha256']:
        raise ValueError('The mission of device ' + device_id + ' does not match its manifest hash')
    opened = {'image': image, 'mission': sealed['mission']}
    if answer:
        key = truth.unlock(_inside(set_root, entry['answer']['path']), password, entry['answer']['sha256'])
        if not isinstance(key, dict) or canonical_digest(key) != entry['answer_key_sha256']:
            raise ValueError('The answer key of device ' + device_id + ' does not match its manifest hash')
        opened['answer_key'] = key
    validate_device({name: entry[name] for name in ('id', 'level', 'framing')}, image=image,
                    mission=opened['mission'], key=opened.get('answer_key'))
    return opened


def seal_qualification(set_root: Path, record: dict, password: str) -> str:
    """Seal a set's qualification record with the set's password into `set_root/qualification.enc` and return the SHA-256
    of the file written. The record lies beside the manifest, never inside a device's answer key: sealing is not
    deterministic, so a record there would change the manifest hash each time the set is qualified. No pin fixes the
    file's hash; the AES-GCM tag under the set's password authenticates it."""
    return truth.seal(record, Path(set_root) / _QUALIFICATION_FILE, password)


# What reading a file can raise for its content or its absence, which admission reports as a reason: JSON nested deeper
# than the parser reaches raises RecursionError.
_UNREADABLE = (OSError, ValueError, RecursionError)


def _named(error):
    """The class an admission reason names for `error`: an OSError's own (FileNotFoundError: never qualified),
    RecursionError, and ValueError for every ValueError, whose subclass tells only where corrupt bytes broke the parse."""
    return type(error).__name__ if isinstance(error, (OSError, RecursionError)) else 'ValueError'


def admitted(set_root: Path, registration: str, device_id: str, password: str, *,
             registrations: dict | None = None) -> list[str]:
    """The reasons device `device_id` of set `registration` under `set_root` may not run a trial, or [] when it may. Its
    set's qualification record must decrypt with `password`, name `registration` and the manifest that `registrations`
    (by default `set_registrations()`) pins for it, which `load_set` must accept, carry the generated family's current
    grader identity (`grader_identity(case_id='generated')`, the whole identity) and the status `passed`, and show both
    references passed (`True`, never a number equal to it) on the device. A record that cannot be read or decrypted
    gives that one reason. No OSError, ValueError or RecursionError, whatever a file holds, is raised: it is a reason,
    named by its class, so that a caller reports a refusal."""
    path = Path(set_root) / _QUALIFICATION_FILE
    try:
        # The tag under the set's password authenticates the record, so its hash is that of the bytes found.
        record = truth.unlock(path, password, hashlib.sha256(path.read_bytes()).hexdigest())
    except _UNREADABLE as error:
        return ['qualification unreadable: ' + _named(error)]
    if (not isinstance(record, dict) or set(record) != _QUALIFICATION_FIELDS or record['schema'] != QUALIFICATION_SCHEMA
            or not isinstance(record['devices'], dict)):
        return ['qualification malformed']
    reasons = []
    if record['registration'] != registration:
        reasons.append('qualification of another registration')
    try:
        pins = set_registrations() if registrations is None else registrations
        manifest = load_set(set_root, registration, registrations=pins)
    except _UNREADABLE as error:
        reasons.append('manifest refused: ' + _named(error))
    else:
        recorded = record['manifest_sha256']
        if recorded != pins[registration] or recorded != manifest['manifest_sha256']:
            reasons.append('qualification of another manifest')
    try:
        if record['grader_identity'] != grader_identity(case_id='generated'):
            reasons.append('qualification under another grader identity')
    except _UNREADABLE as error:
        reasons.append('grader identity unavailable: ' + _named(error))
    if record['status'] != 'passed':
        reasons.append('qualification pending')
    passed = record['devices'].get(device_id)
    if (not isinstance(passed, dict) or set(passed) != {'a', 'b'}
            or passed['a'] is not True or passed['b'] is not True):
        reasons.append('references not passed:' + str(device_id))
    return reasons
