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
from shutil import rmtree

from . import generated_device
from .generated_sets import QUALIFICATION_SCHEMA, load_set, open_device, seal_qualification
from .grader_identity import grader_identity

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


def _private(directory, *, exist_ok=False):
    """Make `directory` readable by its owner only, whatever the umask. Without `exist_ok`, one already there raises."""
    directory.mkdir(mode=0o700, exist_ok=exist_ok)
    directory.chmod(0o700)


def _run(key, candidate, run, *, renode, image, output):
    """(result, None) of one run of the candidate {'model', 'capabilities'} on the device whose answer key is `key`:
    the evaluator's result for the package emitted from it (`evaluator.reference_execute`), with its grade. (None,
    error) when the run raised. The run's evidence is kept in `output/runs/<run>`, and its two emulator sessions' files
    in `output/native/<run>/session` (grading) and `session-emission`."""
    from .reference_calibration import calibration_run
    try:
        evidence, sessions = output / 'runs' / run, output / 'native' / run
        for directory in (evidence, sessions):
            _private(directory.parent, exist_ok=True)  # the device's directory
            # Made here, never found: a run whose name meets another's, as ids that differ only in case do on a file
            # system that ignores case, fails instead of sharing that run's files.
            _private(directory)
        result = calibration_run(PIN, key, candidate['model'], candidate['capabilities'], renode=renode, image=image,
                                 output_dir=evidence, private_dir=sessions / 'session', phase='final')
        return result, None
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
    failures [str], seconds {run: float}, runs {run: {'backend', 'native_process_observed'}}}. `runs` holds the
    evaluator's own account of each run: the backend the device's recipe names, and whether the grading session's own
    emulator process was still running when grading ended, False for an attached session such as a test fixture's. A
    run that raised has None for both. The record is sealed with `password` beside the manifest
    (`generated_sets.seal_qualification`), and a plaintext copy is written to `output_dir/qualification.json`.

    Nothing runs before these checks: `set_root`, which receives the sealed record, and `output_dir` both lie outside
    the repository, the installed package and known candidate roots (`authoring.evaluator_directory`); `output_dir` is
    new or empty; `load_set` accepts the manifest that `registrations` (by default `set_registrations()`) pins; and
    every device opens with `password`, which checks it against the generated-device interface. A failed check raises,
    and nothing is sealed, so a wrong password never replaces a record.

    Then, device by device in id order, references `a` and `b` run, then every mutant on a sampled device (`sample`).
    A reference passes with the verdict `passed`. A mutant is rejected when its verdict is `failed` and its
    `expected_failed_checks` are among the checks that failed (`passed is False`; an undecided check fails nothing).
    A run that raises neither passes nor rejects. The set passes when no failure is recorded. Each failure names the
    device:
    - `reference <a|b> <verdict>:<device>` for a reference that did not pass, and `mutant <id> <verdict>:<device>` or
      `mutant <id> missed expected checks:<device>` for a mutant not rejected. A run that raised gives
      `<run> error:<device> (<what it raised>)`;
    - `mutant inventory:<device>` when the device's mutants do not include the family's REQUIRED_MUTANTS;
    - `decrypted image not removed:<device>` when the device's image is still on disk after its runs.
    Two references alike never get this far: opening the device refuses references whose identity operations send the
    same command.
    The set is pending too, with `grader identity changed during qualification`, when the generated family's grader
    identity at the end is not the one stamped at the start: the grading modules load as the runs need them.

    A run is named `<device>/reference-<a|b>` or `<device>/mutant-<id>`: its seconds and its execution go under that
    name, its evidence in `output_dir/runs/<name>` and its emulator sessions' files in `output_dir/native/<name>`. The
    output holds the reference drivers and the hidden checks, so keep it with the answer vault: the output, `runs`,
    `native` and each device's and run's directory in them are made readable by their owner only. Each device's
    decrypted image lies in a private directory under `output_dir` (`image-*`) during its runs only."""
    from .authoring import evaluator_directory
    from .case_assets import write_json
    try:
        root = evaluator_directory(set_root)
    except ValueError as error:
        raise ValueError('A generated set must lie outside the repository, the installed package and known candidate '
                         'roots') from error
    output = evaluator_directory(output_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Set qualification requires a new or empty output directory')
    manifest = load_set(root, registration, registrations=registrations)
    identity = grader_identity(case_id='generated')
    devices = {}
    for entry in sorted(manifest['devices'], key=lambda entry: entry['id']):
        devices[entry['id']] = open_device(root, manifest, entry['id'], password, answer=True)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)  # an output that already existed, empty, keeps its own mode otherwise
    for name in ('runs', 'native'):
        _private(output / name)
    sampled = sample(manifest['devices'])
    record = {'schema': QUALIFICATION_SCHEMA, 'registration': registration,
              'manifest_sha256': manifest['manifest_sha256'], 'grader_identity': identity, 'status': 'pending',
              'devices': {}, 'sample': sampled, 'mutants': [], 'failures': [], 'seconds': {}, 'runs': {}}
    failures = record['failures']
    for device_id, opened in devices.items():
        key = opened['answer_key']
        references = key['references']
        if not generated_device.REQUIRED_MUTANTS <= {mutant['id'] for mutant in key['mutants']}:
            failures.append('mutant inventory:' + device_id)
        private = Path(tempfile.mkdtemp(prefix='image-', dir=output))
        try:
            image = private / 'image'
            image.write_bytes(opened['image'])

            def run(name, candidate):
                """(grade, None) of the device's run `name` of `candidate`, or (None, error) when it raised."""
                named, started = device_id + '/' + name, time.monotonic()
                result, error = _run(key, candidate, named, renode=renode, image=image, output=output)
                record['seconds'][named] = round(time.monotonic() - started, 3)
                record['runs'][named] = {field: None if result is None else result[field]
                                         for field in ('backend', 'native_process_observed')}
                return (None if result is None else result['grade']), error

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
        finally:
            rmtree(private, ignore_errors=True)
        if private.exists():  # a decrypted image left on disk, as on Windows while a process holds it, is reported
            failures.append('decrypted image not removed:' + device_id)
    # The grader's modules load as the runs need them, so the identity stamped before them must still be the grader's.
    if grader_identity(case_id='generated') != identity:
        failures.append('grader identity changed during qualification')
    record['status'] = 'pending' if failures else 'passed'
    write_json(output / 'qualification.json', record)
    seal_qualification(root, record, password)
    return record
