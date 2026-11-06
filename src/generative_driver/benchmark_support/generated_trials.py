"""The bench harness's way to a device of a generated set (Feature 03, generated trials).

A generated device has no case of its own: its files are in the answer vault, which the configurator cannot open and
which the operator closes while workers run (Feature 05, vault gate; spec decision 17). So the harness holds the set's
sealed files in memory and watches its folder go out of reach here, builds each device's pin, and the configurator only
freezes it, taking the harness's word for the image and answer hashes: that is within the same-user limit of
`docs/design.md`, not isolation. Admission decrypts the held device, writes what a worker receives into the trial folder
and hands back the answer key for the grade. This module is the harness's, outside the grading core: only the generated
family's own closure holds `generated_sets`.
"""
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from . import generated, generated_sets
from .snapshots import canonical_bytes, canonical_digest
from .truth import UnlockedTruth

# The folder of a trial that holds what its worker receives, as the trial's endpoint folder holds the configurator's.
PUBLIC_INPUTS = 'public-inputs'
_SHIPPED_SETS = Path(__file__).resolve().parents[1] / 'resources' / 'registrations' / 'sets.json'


@dataclass(frozen=True, repr=False)
class HeldSet:
    """A set as a suite's harness holds it in memory (spec decision 17), so that its folder can go out of reach before
    any worker starts. `sealed` stays ciphertext until a slot opens it; `record` is the unlocked qualification record,
    whose digest is `evidence_sha256`. It holds no path, and has no JSON form."""
    registration: str
    manifest: dict
    record: dict
    evidence_sha256: str
    sealed: dict


def hold_set(set_root, registration, device_ids, password):
    """The `HeldSet` of the devices `device_ids` of set `registration`: its manifest, as `load_set` checks it, its
    qualification record, decrypted once, and each device's two sealed files, read once and checked against their
    manifest hashes. ValueError for what `load_set` refuses, an unknown device, a sealed file that is not its manifest's
    and a record that cannot be read or decrypted (`qualification unreadable`, naming the error's class alone); a
    manifest or sealed file that cannot be read raises OSError."""
    manifest = generated_sets.load_set(set_root, registration)
    entries = {entry['id']: entry for entry in manifest['devices']}
    unknown = ['Unknown generated device: ' + str(device) for device in device_ids if device not in entries]
    if unknown:
        raise ValueError('; '.join(unknown))
    sealed = {device: {name: _sealed_file(set_root, entries[device], name) for name in ('inputs', 'answer')}
              for device in device_ids}
    path = Path(set_root) / generated_sets._QUALIFICATION_FILE
    try:
        data = path.read_bytes()
        record = password.unlock_bytes(data, hashlib.sha256(data).hexdigest())
        evidence = canonical_digest(record)
    except generated_sets._UNREADABLE as error:
        reason = 'qualification unreadable: ' + generated_sets._named(error)
    else:
        return HeldSet(registration, manifest, record, evidence, sealed)
    raise ValueError(reason)


def set_unreachable(set_root):
    """Whether the set's folder is out of reach, which is all the harness can see of a closed vault: `manifest.json`
    under `set_root` is gone (`FileNotFoundError` or `NotADirectoryError` from `os.stat`), and the root is gone in the
    same way or is an empty directory. Anything else keeps it reachable, `PermissionError` included, so a locked volume
    that still answers keeps the gate shut."""
    root = Path(set_root)
    absent = (FileNotFoundError, NotADirectoryError)
    try:
        os.stat(root / generated_sets._MANIFEST_FILE)
    except absent:
        pass
    except OSError:
        return False
    else:
        return False
    try:
        os.stat(root)
    except absent:
        return True
    except OSError:
        return False
    try:
        with os.scandir(root) as listing:
            return next(listing, None) is None
    except OSError:  # a root that is a file answers NotADirectoryError here, and it is no empty directory
        return False


def _sealed_file(set_root, entry, name):
    """The bytes of the sealed file `name` of the device `entry`, once they are the ones its manifest entry hashes."""
    file = entry[name]
    data = generated_sets._inside(set_root, file['path']).read_bytes()
    if hashlib.sha256(data).hexdigest() != file['sha256']:
        raise ValueError('The ' + name + ' file of device ' + entry['id'] + ' does not match its manifest hash')
    return data


@dataclass(frozen=True, repr=False)
class SetDevice:
    """One device of a held set, as a slot of its suite names it. It holds no path, and has no JSON form."""
    held: HeldSet
    device_id: str


def fixture_variable_reasons(registration, shipped=None):
    """[the reason the set `registration` may not run], or [] when it may: with `GD_TEST_HARNESS_FIXTURE` set, which can
    replace any check of the harness, a set named in the file `shipped`, by default the package's own `sets.json` read
    by its own path, is refused. This catches a mistake only (`docs/design.md`)."""
    if not os.environ.get('GD_TEST_HARNESS_FIXTURE'):
        return []
    if registration not in generated_sets.set_registrations(_SHIPPED_SETS if shipped is None else shipped):
        return []
    return ['GD_TEST_HARNESS_FIXTURE is set, and this is a shipped set']


def _entry(manifest, device_id):
    entry = next((listed for listed in manifest['devices'] if listed['id'] == device_id), None)
    if entry is None:
        raise ValueError('Unknown generated device: ' + str(device_id))
    return entry


def device_pin(manifest, device_id, evidence_sha256):
    """The pin of device `device_id` of the set whose loaded manifest is `manifest`: the template of `generated`, with
    the set's manifest hash, the hashes of the device's image, mission and answer file, and the qualification's
    `evidence_sha256`. ValueError for a device the manifest does not list."""
    entry = _entry(manifest, device_id)
    return generated.device_pin(manifest['set'], device_id, manifest_sha256=manifest['manifest_sha256'],
                                truth_sha256=entry['answer']['sha256'], image_sha256=entry['image_sha256'],
                                mission_sha256=entry['mission_sha256'], evidence_sha256=evidence_sha256)


def admit_device(device, pin, password, trial_dir, *, renode=None):
    """Admit one device of a held set for a trial: (provider, admission, `UnlockedTruth`), reading no file of the set.
    No flag or test switch bypasses this: a device its set's qualification does not admit
    (`generated_sets.admitted_held`), or a shipped set under the test variable (`fixture_variable_reasons`), raises
    ValueError naming every reason, and so does a `pin` whose hashes differ from the set's manifest entry. The device's
    sealed inputs and answer are decrypted once each, its execution setup validated, and what a worker receives written
    into `trial_dir/public-inputs`: the image and the canonical JSON of the mission, whose bytes its hash covers. The
    provider starts from that image and receives only the recipe, and the admission names the digest of the set's
    qualification record. The caller starts the provider, so that it holds the device even when the start fails."""
    from .device_provider import DeviceProvider
    from .execution import validate_setup
    held = device.held
    reasons = fixture_variable_reasons(held.registration) or generated_sets.admitted_held(held, device.device_id)
    if reasons:
        raise ValueError('The generated set ' + held.registration + ' is not admitted: ' + '; '.join(reasons))
    if device_pin(held.manifest, device.device_id, (pin.get('calibration') or {}).get('evidence_sha256')) != pin:
        raise ValueError('A device pin differs from its set\'s manifest entry: ' + device.device_id)
    opened = generated_sets.open_held(held.manifest, device.device_id, held.sealed[device.device_id], password,
                                      answer=True)
    truth = UnlockedTruth(opened['answer_key'], _entry(held.manifest, device.device_id)['answer']['sha256'])
    validate_setup(pin['manifest'], opened['answer_key'])
    folder = Path(trial_dir) / PUBLIC_INPUTS
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    image = folder / generated.ARTIFACT
    image.write_bytes(opened['image'])
    (folder / generated.MISSION).write_bytes(canonical_bytes(opened['mission']))
    provider = DeviceProvider(pin['case_id'], image, opened['answer_key']['recipe'], renode, Path(trial_dir) / 'device',
                              image_sha256=pin['image_hashes'][generated.ARTIFACT])
    return provider, {'calibration_evidence_sha256': held.evidence_sha256}, truth


def answer_key(device, password):
    """The `UnlockedTruth` of `device`'s answer key, for a trial graded after its harness stopped: only the answer is
    decrypted, once (`generated_sets.held_answer`). ValueError for what that refuses."""
    held = device.held
    key = generated_sets.held_answer(held.manifest, device.device_id, held.sealed[device.device_id], password)
    return UnlockedTruth(key, _entry(held.manifest, device.device_id)['answer']['sha256'])
