"""The bench harness's way to a device of a generated set (Feature 03, generated trials).

A generated device has no case of its own: its files are in the answer vault, which the configurator cannot open and
which a later Feature unmounts while workers run. So the harness builds each device's pin here, and the configurator
only freezes it, taking the harness's word for the image and answer hashes: that is within the same-user limit of
`docs/design.md`, not isolation. Admission unlocks the device, writes what a worker receives into the trial folder and
hands back the answer key for the grade. This module is the harness's, outside the grading core: only the generated
family's own closure holds `generated_sets`.
"""
import contextlib
import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import generated, generated_sets
from .snapshots import canonical_bytes, canonical_digest
from .truth import UnlockedTruth

# The folder of a trial that holds what its worker receives, as the trial's endpoint folder holds the configurator's.
PUBLIC_INPUTS = 'public-inputs'


@dataclass(frozen=True, repr=False)
class SetDevice:
    """One device of a set, as a slot of its suite names it. `set_root` lives in the harness's memory: this has no JSON
    form, and no record holds it."""
    set_root: Path
    registration: str
    device_id: str


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


def qualification_digest(set_root, password):
    """The `canonical_digest` of the set's unlocked qualification record, which each device's pin names as its evidence.
    ValueError for a record `password` does not unlock; a record that cannot be read raises OSError."""
    path = Path(set_root) / generated_sets._QUALIFICATION_FILE
    return canonical_digest(password.unlock(path, hashlib.sha256(path.read_bytes()).hexdigest()))


@contextlib.contextmanager
def _reading(device):
    """A file of `device`'s set that cannot be read is a ValueError naming its class alone, so that no path reaches a
    record."""
    try:
        yield
    except OSError as error:
        reason = type(error).__name__
    else:
        return
    raise ValueError('The generated set ' + device.registration + ' cannot be read: ' + reason)


def _unlocked_key(device, manifest, password):
    """(the opened device with its answer key, its `UnlockedTruth`), under the set's loaded `manifest`."""
    with _reading(device):
        opened = generated_sets.open_device(device.set_root, manifest, device.device_id, password, answer=True)
    return opened, UnlockedTruth(opened['answer_key'], _entry(manifest, device.device_id)['answer']['sha256'])


def admit_device(device, pin, password, trial_dir, *, renode=None):
    """Admit one device of a set for a trial: (provider, admission, `UnlockedTruth`). No flag or test switch bypasses
    this: a device its set's qualification does not admit (`generated_sets.admitted`) raises ValueError naming every
    reason, and so does a `pin` whose hashes differ from the set's manifest entry. The device is opened with its answer
    key once, its execution setup validated, and what a worker receives written into `trial_dir/public-inputs`: the
    image and the canonical JSON of the mission, whose bytes its hash covers. The provider starts from that image and
    receives only the recipe, and the admission names the digest of the set's qualification record. The caller starts
    the provider, so that it holds the device even when the start fails."""
    from .device_provider import DeviceProvider
    from .execution import validate_setup
    reasons = generated_sets.admitted(device.set_root, device.registration, device.device_id, password)
    if reasons:
        raise ValueError('The generated set ' + device.registration + ' is not admitted: ' + '; '.join(reasons))
    with _reading(device):
        manifest = generated_sets.load_set(device.set_root, device.registration)
        digest = qualification_digest(device.set_root, password)
    if device_pin(manifest, device.device_id, (pin.get('calibration') or {}).get('evidence_sha256')) != pin:
        raise ValueError('A device pin differs from its set\'s manifest entry: ' + device.device_id)
    opened, truth = _unlocked_key(device, manifest, password)
    validate_setup(pin['manifest'], opened['answer_key'])
    folder = Path(trial_dir) / PUBLIC_INPUTS
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    image = folder / generated.ARTIFACT
    image.write_bytes(opened['image'])
    (folder / generated.MISSION).write_bytes(canonical_bytes(opened['mission']))
    provider = DeviceProvider(pin['case_id'], image, opened['answer_key']['recipe'], renode, Path(trial_dir) / 'device',
                              image_sha256=pin['image_hashes'][generated.ARTIFACT])
    return provider, {'calibration_evidence_sha256': digest}, truth


def answer_key(device, password):
    """The `UnlockedTruth` of `device`'s answer key, for a trial graded after its harness stopped. ValueError for what
    `generated_sets.open_device` refuses, and for a set that cannot be read."""
    with _reading(device):
        manifest = generated_sets.load_set(device.set_root, device.registration)
    return _unlocked_key(device, manifest, password)[1]
