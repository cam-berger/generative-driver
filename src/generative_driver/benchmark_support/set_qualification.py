"""Qualify a generated set once, unattended, with the set's own password (spec decision 11).

Both reference drivers run on every device, which shows that its firmware and answer key agree. Every mutant runs on a
sample of two devices per level and framing, which shows that the hidden sequences catch wrong drivers. A run is a final
calibration run (`reference_calibration.calibration_run`): the reference runner emits the driver's package against a
fresh emulator, and the evaluator grades it on another as it grades a trial's frozen driver. There every host fault
raises, so an outage never passes for a mutant's rejection. Each run records its seconds, so the first runs can be timed
before a full set is qualified.

This module runs the grader and grades nothing itself, so it lies outside every grader identity, as
`reference_calibration` does.
"""
import subprocess
import tempfile
import time
from pathlib import Path

from . import generated
from .generated_sets import QUALIFICATION_SCHEMA, load_set, open_device, seal_qualification
from .grader_identity import grader_identity
from .snapshots import canonical_digest

PIN = {'case_id': 'generated', 'scenario_id': 'original', 'case_seed': 0, 'revision': 0}
# What a run that could not be graded raises: the run's error, recorded as a failure, and the next run goes on.
_RUN_ERRORS = (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError)


def sample(devices: list[dict]) -> list[str]:
    """The ids of the devices whose mutants run, in id order: of the manifest entries `devices`, the first two by id of
    each (level, framing)."""
    groups = {}
    for device in sorted(devices, key=lambda device: device['id']):
        groups.setdefault((device['level'], device['framing']), []).append(device['id'])
    return sorted(identity for ids in groups.values() for identity in ids[:2])


def _run(key, candidate, run, *, renode, image, output):
    """(grade, None) of one run of the candidate {'model', 'capabilities'} on the device whose answer key is `key`:
    the evaluator's grade of the package emitted from it. (None, error) when the run raised. The run's evidence is
    kept in `output/runs/<run>` and its emulator sessions' files in `output/native/<run>`."""
    from .reference_calibration import calibration_run
    try:
        directory = output / 'runs' / run
        directory.mkdir(parents=True, mode=0o700)  # never another run's: ids that differ only in case meet here
        result = calibration_run(PIN, key, candidate['model'], candidate['capabilities'], renode=renode, image=image,
                                 output_dir=directory, private_dir=output / 'native' / run, phase='final')
        return result['grade'], None
    except _RUN_ERRORS as error:
        return None, error


def _failure(run, device_id, grade, error):
    """The failure of a run that did not count: `<run> <verdict>:<device>`, or `<run> error:<device> (<what it
    raised>)`."""
    if grade is None:
        return '%s error:%s (%s: %s)' % (run, device_id, type(error).__name__, str(error)[:200])
    return '%s %s:%s' % (run, grade['verdict'], device_id)


def qualify(set_root: Path, registration: str, password: str, *, renode: Path, output_dir: Path,
            registrations: dict | None = None) -> dict:
    """Qualify the set `registration` under `set_root` once and return its record (`generated-set-qualification/1`):
    {schema, registration, manifest_sha256, grader_identity (the generated family's, whole), status 'passed' or
    'pending', devices {id: {'a': bool, 'b': bool}}, sample [ids], mutants [{device, id, rejected, failed_checks}],
    failures [str], seconds {run: float}}. It is sealed with `password` beside the manifest
    (`generated_sets.seal_qualification`), and a plaintext copy is written to `output_dir/qualification.json`.

    Nothing runs before these checks: `output_dir` is new or empty and outside the repository; `load_set` accepts the
    manifest that `registrations` (by default `set_registrations()`) pins; and every device opens with `password` and
    holds a valid answer key. A failed check raises, and nothing is sealed, so a wrong password never replaces a record.

    Then, device by device in id order, references `a` and `b` run, then every mutant on a sampled device (`sample`).
    A reference passes with the verdict `passed`. A mutant is rejected when its verdict is `failed` and its
    `expected_failed_checks` are among the checks that failed (`passed is False`; an undecided check fails nothing).
    A run that raises neither passes nor rejects. The set passes when no failure is recorded. Each failure names the
    device:
    - `reference <a|b> <verdict>:<device>` for a reference that did not pass, and `mutant <id> <verdict>:<device>` or
      `mutant <id> missed expected checks:<device>` for a mutant not rejected. A run that raised gives
      `<run> error:<device> (<what it raised>)`;
    - `mutant inventory:<device>` when the device's mutants do not include the family's REQUIRED_MUTANTS;
    - `references not distinct:<device>` when its two references have the same model.

    A run is named `<device>/reference-<a|b>` or `<device>/mutant-<id>`: its seconds go under that name, and its
    evidence in `output_dir/runs/<name>`. The output holds the reference drivers and the hidden checks, so keep it with
    the answer vault. Each device's decrypted image lies in a private file under `output_dir` during its runs only."""
    from .authoring import evaluator_directory
    from .case_assets import write_json
    output = evaluator_directory(output_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Set qualification requires a new or empty output directory')
    manifest = load_set(set_root, registration, registrations=registrations)
    identity = grader_identity(case_id='generated')
    devices = {}
    for entry in sorted(manifest['devices'], key=lambda entry: entry['id']):
        devices[entry['id']] = opened = open_device(set_root, manifest, entry['id'], password, answer=True)
        generated.answer_key(opened['answer_key'])
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    sampled = sample(manifest['devices'])
    record = {'schema': QUALIFICATION_SCHEMA, 'registration': registration,
              'manifest_sha256': manifest['manifest_sha256'], 'grader_identity': identity, 'status': 'pending',
              'devices': {}, 'sample': sampled, 'mutants': [], 'failures': [], 'seconds': {}}
    failures = record['failures']
    for device_id, opened in devices.items():
        key = opened['answer_key']
        references = key['references']
        if not generated.REQUIRED_MUTANTS <= {mutant['id'] for mutant in key['mutants']}:
            failures.append('mutant inventory:' + device_id)
        if canonical_digest(references['a']['model']) == canonical_digest(references['b']['model']):
            failures.append('references not distinct:' + device_id)
        with tempfile.TemporaryDirectory(prefix='image-', dir=output, ignore_cleanup_errors=True) as private:
            image = Path(private) / 'image'
            image.write_bytes(opened['image'])

            def run(name, candidate):
                started = time.monotonic()
                outcome = _run(key, candidate, device_id + '/' + name, renode=renode, image=image, output=output)
                record['seconds'][device_id + '/' + name] = round(time.monotonic() - started, 3)
                return outcome

            passed = record['devices'][device_id] = {}
            for name in ('a', 'b'):
                grade, error = run('reference-' + name, references[name])
                passed[name] = grade is not None and grade['verdict'] == 'passed'
                if not passed[name]:
                    failures.append(_failure('reference ' + name, device_id, grade, error))
            if device_id in sampled:
                for mutant in key['mutants']:
                    grade, error = run('mutant-' + mutant['id'], mutant)
                    failed = [check['id'] for check in grade['checks'] if check['passed'] is False] if grade else []
                    rejected = (grade is not None and grade['verdict'] == 'failed'
                                and set(mutant['expected_failed_checks']) <= set(failed))
                    record['mutants'].append({'device': device_id, 'id': mutant['id'], 'rejected': rejected,
                                              'failed_checks': failed})
                    if rejected:
                        continue
                    if grade is not None and grade['verdict'] == 'failed':
                        failures.append('mutant %s missed expected checks:%s' % (mutant['id'], device_id))
                    else:
                        failures.append(_failure('mutant ' + mutant['id'], device_id, grade, error))
    record['status'] = 'pending' if failures else 'passed'
    write_json(output / 'qualification.json', record)
    seal_qualification(set_root, record, password)
    return record
