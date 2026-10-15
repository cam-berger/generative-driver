"""Durable configurator. Worker statements, accepted evidence and verdicts stay distinct."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
import uuid
import sys
import copy
import ipaddress
import tempfile
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass

from .setup import configured_executor
from .agents import StageRequest, execute

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
TERMINAL = {'completed', 'failed', 'cancelled', 'blocked'}
# Runs record their protocol; a run saved without this label used the earlier gated stage progression.
PROTOCOL = 'assistive/1'
# Generic stage inputs, each taken from the latest accepted handoff of its stage that has artifacts. Emit's are a model
# and a record it wrote (`Controller._emit_inputs`).
STAGE_INPUTS = {'probe': (('interpret', 'interpreted model'),),
                'ground': (('interpret', 'interpreted model'), ('probe', 'probe records'))}
STAGE_TOOLS = {
    'acquire': ['acquire_serial_listen','acquire_firmware_artifact','acquire_datasheet','acquire_protocol_spec','acquire_classify'],
    'interpret': ['firmware_prepare','firmware_collect','firmware_validate','model_validate','model_provenance_audit','ghidra_run','image_map_check'],
    # Probe and ground workers get no model_validate: the register validator writes beside the model it checks, which
    # would alter an assigned input. The configurator validates each assigned model on its own copy and notes it.
    'probe': ['model_qualify','probe_run','probe_diff','interface_describe','interface_execute'],
    'ground': ['probe_run','interface_execute','probe_diff','probe_observe'],
    'emit': ['emit_package','emit_check'],
    'reuse': ['interface_describe','interface_execute','emit_check','emit_test_live'],
}
GROUND_OBJECTIVE = ('Test the meanings the interpreted model gives the device: units, scaling and the effects of its '
                    'operations. Run the model on the device with probe_run and interface_execute through the '
                    'configurator-owned connection, compare the outputs with the probe records and with operator '
                    'observations where given (probe_observe checks a decoded capture), and report what the device '
                    'did that the model did not predict. Goal: ')


# Fresh reuse's package tool on a benchmark device; an emulated device's result carries its instrument reading.
BENCHMARK_PACKAGE_EXECUTE = {'name':'benchmark_package_execute','description':'Execute a packaged driver operation over '
    'the configurator-owned binding. When the device has an instrument, as an emulated device does, the result also '
    'carries its reading after the call: device evidence, as a real device\'s reading would be, never a grade.',
    'inputSchema':{'type':'object','required':['package_dir','operation'],'additionalProperties':False,
        'properties':{'package_dir':{'type':'string'},'operation':{'type':'string'},'parameters':{'type':'object'}}}}
# A benchmark run whose stages are observed without truth completes awaiting the independent grade after the run.
PENDING = 'Frozen driver awaits independent evaluation'
# What the start of an emulated run without the device its bench harness provides says: its workers run until probe.
NO_HARNESS_DEVICE = ('This run has no device its bench harness provides: acquisition and interpretation run, and every '
                     'device stage from probe on stops for the operator, so it never reaches a verdict. Start it with '
                     '`benchmark run`, whose bench harness provides the emulator and grades the driver after the run.')


def _observed_case(spec):
    """Whether a run is a benchmark run. Every case's adapter, legacy tq9 and bme280 included, observes its stages
    without truth; the bench harness grades the frozen driver once, after the run."""
    return bool(spec.get('case'))


def _emulated(spec):
    """Whether a benchmark run's device is an emulator the bench harness provides (the v2 cases and legacy tq9); a
    bme280 run uses the operator's bound device."""
    return (spec.get('_case_pin') or {}).get('execution') == 'actual-agent-emulation'


def _v2(spec):
    return (spec.get('_case_pin') or {}).get('manifest', {}).get('schema') == 'benchmark-case/2'


def digest(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Artifact symlinks are not admissible')
    if path.is_file():
        hasher = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                hasher.update(block)
        return hasher.hexdigest()
    if path.is_dir():
        entries = []
        for item in sorted(path.rglob('*')):
            if item.is_symlink():
                raise ValueError('Artifact symlinks are not admissible')
            if item.is_file():
                entries.append([item.relative_to(path).as_posix(), digest(item)])
        return hashlib.sha256(json.dumps(entries, separators=(',', ':')).encode()).hexdigest()
    raise ValueError('Artifact does not exist: ' + str(path))


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def validate_scoped_approval(spec):
    scope=spec.get('scoped_tool_approval')
    if scope is None:
        return
    case=spec.get('case')
    case_id=case.get('id') if isinstance(case,dict) else case
    if scope=='bound-device':
        if not spec.get('binding'):
            raise ValueError('Bound-device approval requires an explicit operator binding')
        binding_identity(spec['binding'])
        if not spec.get('effects'):
            raise ValueError('Bound-device approval requires nonempty operator effect grants')
        return
    if scope!='emulator':
        raise ValueError('Unsupported scoped_tool_approval')
    if case_id is None:
        raise ValueError('Emulator approval requires a registered benchmark case such as tq9')
    from .benchmark_support.registry import resolve_case
    definition = resolve_case(case_id)
    if definition.execution != 'actual-agent-emulation' or definition.approval_scope != 'emulator':
        raise ValueError('scoped_tool_approval supports only a registered emulator profile')
    case_option_values=spec.get('case_options')
    nested_case_options=case.get('options') if isinstance(case,dict) else None
    if ('binding' in spec or
            (isinstance(case_option_values,dict) and 'binding' in case_option_values) or
            (isinstance(nested_case_options,dict) and 'binding' in nested_case_options)):
        raise ValueError('Emulator tool approval cannot authorize an operator-supplied device binding')


def case_options(spec):
    case=spec.get('case')
    options={**(case.get('options',{}) if isinstance(case,dict) else {}),**spec.get('case_options',{})}
    options.pop('_execution_run_dir', None)
    options.pop('device', None)
    if spec.get('_snapshot_sha256'):
        options['_execution_run_dir'] = spec['_execution_run_dir']
    if spec.get('binding'):
        options['binding']=spec['binding']
    if spec.get('device') and spec.get('_device'):
        # The provided device as the configurator's durable record holds it now (a recovery changes its binding).
        options['device']={key:spec['_device'][key] for key in ('session_id','binding')}
    return {**options,'revision':spec.get('_revision',0),'feedback':spec.get('_feedback'),
        'operator_observations':spec.get('_operator_observations',[]),'configured_effects':spec['effects']}


def _refuse_secrets(spec):
    """The configurator never holds a password, a password file or the emulator executable, for any case: the bench
    harness does. The request itself is checked too, since the run keeps all of it as its spec."""
    case = spec.get('case')
    for options in (spec, spec.get('case_options'), case.get('options') if isinstance(case, dict) else None):
        if isinstance(options, dict) and any('password' in str(key).lower() or str(key).lower() == 'renode'
                                             for key in options):
            raise ValueError('The configurator takes no password, password file or emulator executable; the bench '
                             'harness holds them')


OLD_SUITE = ('This suite was frozen before the bench harness drove suites (it holds evaluator password files or no '
             'scheduler) and is read-only: start a new suite')


def _harness_identity(params, refusal):
    """The sha256 of a bench harness's token, the harness's process id and the lock file it holds for as long as it runs
    (where its file system offers one), as the harness hands them over. Without a process to watch, a harness that died
    without letting go of its suite would leave the suite running, or stopping, for good."""
    token, pid, lock = params.get('harness_token_sha256'), params.get('harness_pid'), params.get('harness_lock')
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError(refusal)
    if type(pid) is not int or pid <= 0:
        raise ValueError('harness_pid must be the positive process id of the bench harness')
    if lock is not None and (not isinstance(lock, str) or not Path(lock).is_absolute()):
        raise ValueError('harness_lock must be an absolute path')
    return token, pid, lock


def _attached_to(suite, token_sha256):
    """Whether the bench harness holding `token_sha256` is the one attached to the suite."""
    held = suite['harness']['token_sha256']
    return bool(suite['harness']['attached'] and isinstance(held, str) and isinstance(token_sha256, str)
                and hmac.compare_digest(held, token_sha256))


def _assistive(suite):
    """Whether every child of the suite is graded after its run, as the assistive protocol grades it: a suite its bench
    harness drives. A suite frozen before the harness names no protocol and counts no equivalent drivers."""
    from .benchmark_support.suites import harness_driven
    return harness_driven(suite['frozen'])


# A child whose recorded request differs from its frozen slot holds that slot's request_id for good.
TAKEN = "; the slot's request_id is taken by that child, so start a new suite"


def _provided_device(params, pin):
    """The device record of an emulated run, from what the bench harness hands over: for a v2 case its admission (the
    calibration evidence hash, compared with the public pin; legacy tq9 has no calibration), the sha256 of its
    evaluation token, and the descriptor of the device it started from the pinned image. The configurator admits
    nothing itself and holds no truth."""
    from .device_client import descriptor_errors
    if pin['manifest'].get('schema') == 'benchmark-case/2':
        calibration = pin.get('calibration') or {}
        if calibration.get('status') != 'passed':
            raise ValueError('V2 case requires independently validated passed calibration before a run')
        admission = params.get('admission') if isinstance(params.get('admission'), dict) else {}
        if not isinstance(admission.get('calibration_evidence_sha256'), str) or \
                admission['calibration_evidence_sha256'] != calibration.get('evidence_sha256'):
            raise ValueError('Calibration admission evidence differs from the public case pin; the bench harness admits '
                             'an emulated case')
    token = params.get('evaluation_token_sha256')
    if not isinstance(token, str) or not re.fullmatch('[0-9a-f]{64}', token):
        raise ValueError('An emulated case requires the sha256 of the bench harness evaluation token')
    device = params.get('device')
    errors = descriptor_errors(device)
    if errors:
        raise ValueError('An emulated case requires the device descriptor of the bench harness: ' + '; '.join(errors))
    binding_identity(device['binding'])
    if device['image_sha256'] != pin.get('image_hashes', {}).get('firmware.bin'):
        raise IntegrityError('Provided device image differs from the pinned firmware image')
    return {'session_id': device['session_id'], 'binding': dict(device['binding']), 'receipts': []}


def binding_identity(binding):
    """Lease physical endpoints, ignoring tuning knobs and permission flags.

    USB and FTDI selectors can omit a serial number, so the first release takes
    a conservative product-wide lease instead of risking overlapping selectors.
    """
    if not isinstance(binding,dict):
        raise ValueError('binding must be an operator-supplied object')
    if isinstance(binding.get('host'),str) and type(binding.get('port')) is int:
        host = binding['host'].strip().lower().rstrip('.')
        try:
            address = ipaddress.ip_address(host)
            host = 'loopback' if address.is_loopback else address.compressed
        except ValueError:
            if host == 'localhost': host = 'loopback'
        return ['tcp',host,binding['port']]
    if isinstance(binding.get('port'),str) and binding['port'].strip():
        port = binding['port'].strip()
        if port.upper().startswith(('COM','\\\\.\\COM')):
            port = port.upper().removeprefix('\\\\.\\')
        else:
            port = str(Path(port).expanduser().resolve())
        return ['serial',port]
    if isinstance(binding.get('url'),str) and binding['url'].lower().startswith('ftdi://'):
        # A selector may be a serial, bus address or index for the same adapter.
        # All FTDI connections share a lease until selector resolution is added.
        return ['ftdi']
    if type(binding.get('vendor_id')) is int and type(binding.get('product_id')) is int:
        return ['usb',binding['vendor_id'],binding['product_id']]
    raise ValueError('Unsupported binding shape; use host/port, serial port, FTDI url, or USB vendor_id/product_id')


def _lease(binding):
    """The key of the lease a run holds on a device binding."""
    return hashlib.sha256(_json(binding_identity(binding)).encode()).hexdigest()


def _is_serial_binding(binding):
    """A serial binding names a port string or a USB vendor/product identity."""
    if not isinstance(binding, dict):
        return False
    if isinstance(binding.get('port'), str) and binding['port'].strip():
        return True
    return type(binding.get('vendor_id')) is int and type(binding.get('product_id')) is int


def _model_json(path):
    """A model.json read as the runtime reads it, with its strict bounded reader: the configurator judges the bytes the
    runtime executes. It accepts a byte-order mark or UTF-16 as the runtime does, and raises ValueError for a duplicate
    key, a non-finite constant or the size bound, RecursionError for nesting and OSError when it cannot read."""
    from interface_runtime.evidence import read_json_hashed
    return read_json_hashed(path, 1048576)[0]


def _model_channel(name, arguments):
    model_dir = arguments.get('model_dir')
    path = Path(model_dir)/'model.json' if model_dir else Path(arguments['package_dir'])/'driver/model.json'
    return _model_json(path).get('channel', {})


def operation_effects(name, arguments):
    """Read declared operation effects before opening an operator-owned binding."""
    model_dir=arguments.get('model_dir')
    if model_dir:
        model_path=Path(model_dir)/'model.json'
    else:
        model_path=Path(arguments['package_dir'])/'driver/model.json'
    model=_model_json(model_path)
    operations=model.get('operations',{})
    if model.get('schema')=='interface-model/1':
        steps=[step for group in operations.values() for step in group]+model.get('state',[])
        return {'write'} if any(step.get('op') in ('reg_write','raw_write') for step in steps) else {'read'}
    names=arguments.get('operations') if name=='emit_test_live' else [arguments.get('operation')]
    if names is None:
        names=list(operations)
    if not names or any(item not in operations for item in names):
        raise ValueError('Select existing named operations before device dispatch')
    return {operations[item].get('effect','actuate') for item in names}


def _refuse_invalid_model(model_dir):
    """Refuse a model the toolchain refuses before it opens the device: model.json bytes the runtime's strict bounded
    reader cannot load (probe_run and interface_execute read every model with it first: a duplicate key, a non-finite
    constant, the size bound, nesting), or an interface model that fails validation. The gateway calls this before it
    marks an effectful dispatch uncertain, so such a refusal leaves no effect to reconcile. A register model has no
    validation in the runtime: its probe runs it on the bus."""
    from interface_runtime.engine import SCHEMAS, validate_model
    try:
        model = _model_json(Path(model_dir) / 'model.json')
    except (OSError, ValueError, RecursionError) as error:
        raise ValueError('The runtime cannot load model.json, so it would refuse it before opening the device; nothing '
                         'was dispatched: ' + type(error).__name__ + ': ' + str(error)) from None
    if isinstance(model, dict) and model.get('schema') in SCHEMAS:
        checked = validate_model(model)
        if not checked['ok']:
            raise ValueError('The model fails validation, so the runtime would refuse it before opening the device; '
                             'nothing was dispatched: ' + str(checked['defects']))


class IntegrityError(ValueError):
    """A recorded or observed hash, sealed input or handoff no longer matches: the run stops for integrity."""


class SafetyError(ValueError):
    """A device binding conflict: the run stops for safety."""


@dataclass(frozen=True)
class Outcome:
    """The decision after one worker: advance, revise or stop. Stops name safety, integrity, operator or host."""
    kind: str
    reason: str | None = None
    category: str | None = None
    diff: object = None


_REPORT_FAULTS = ('no_report', 'invalid_report', 'output_limit')
_CHANGED = re.compile(r'(?i)stale|changed|altered|hash')


def _runtime_stop(result):
    """A runtime that could not run, ran out of time or crashed stops; one that ran and reported badly does not."""
    status, cause = result.get('status', 'completed'), result.get('cause')
    if status == 'completed' or cause in _REPORT_FAULTS:
        return None
    host = cause == 'exit_error' or (cause is None and status == 'failed')
    return {'category': 'host' if host else 'operator', 'reason': result.get('reason') or 'Configured runtime ' + str(status)}


def _outcome(stage, result, checked, *, uncertain, repairs, max_repairs):
    """Decide one stage. Worker report status, failed or not_run checks and missing artifacts never stop, and neither
    does anything a stage observation reports beyond its stop: no adapter grades a stage."""
    stop = _runtime_stop(result) or checked.get('stop') or (uncertain and {'category': 'safety', 'reason':
        checked.get('reason') or 'Outstanding effect is uncertain; operator reconciliation is required before further execution'})
    if stop:
        return Outcome('stop', stop['reason'], stop['category'])
    diff = checked.get('device_diff') if stage in ('probe', 'ground') else None
    if diff and repairs < max_repairs:
        return Outcome('revise', 'Run evidence contradicts the model', diff=diff)
    return Outcome('advance', 'Repair budget exhausted; continuing with best revision' if diff else None)


EVIDENCE_ARGUMENTS = ('model_dir', 'probe', 'probe_json', 'bus', 'replay', 'package_dir')


def _argument_digests(arguments):
    """The content of each evidence path a tool is given, hashed by the configurator before dispatch; None when
    unreadable. Later checks use these, never the worker's word, for which model and record a tool used."""
    digests = {}
    for key in EVIDENCE_ARGUMENTS:
        if isinstance(arguments.get(key), str):
            try:
                digests[key] = digest(arguments[key])
            except (OSError, ValueError):
                digests[key] = None
    return digests


def _ran_assigned_model(event, assignment):
    """Whether a tool ran the assigned model: the model_dir bytes the configurator hashed before dispatch are an
    input of this assignment. A model the worker wrote in its workspace is not."""
    sha = (event.get('argument_sha256') or {}).get('model_dir')
    return sha is not None and sha in set((assignment.get('inputs') or {}).values())


def _result_of(event):
    return event.get('result') if isinstance(event.get('result'), dict) else {}


def _listed(results):
    return [r for r in results if isinstance(r, dict)] if isinstance(results, list) else []


def _replaced(value, replacements):
    """`value` with every (old, new) substring replaced, in order, in each string it holds."""
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_replaced(item, replacements) for item in value]
    return {key: _replaced(item, replacements) for key, item in value.items()} if isinstance(value, dict) else value


def _answered(event):
    """Whether a probe_run's record holds what the device did on the connection the configurator supplied: a live run
    that succeeded, a register probe that read an unexpected identity, or a failure the runtime attributes to the
    model after the transport opened."""
    result = _result_of(event)
    if event.get('name') != 'probe_run' or event.get('connected') is not True or result.get('evidence_kind', 'live') != 'live':
        return False
    if event.get('ok') is True or ('fault' not in result and result.get('_exit') == 2):
        return True
    failed = [r for r in _listed(result.get('results')) if r.get('ok') is False]
    return result.get('fault') == 'model' and bool(failed) and all(r.get('transport_open_attempted') is True for r in failed)


def _probe_records(events):
    """The probe records one assignment's runs leave to hand on, from the configurator's own record of its tools.

    For each record path only the last probe_run that wrote it counts, against the hash the configurator recorded
    when that run returned: a register probe always writes probe.json, so a later run replaces the earlier record.
    That record is kept when the run answered on the owned connection, whatever model it ran; the handoff's notes
    bind each record to the digest of the model that wrote it (`_record_writers`). Returns ({path: (sha256, event)},
    dispatch ids of the runs whose record a later run replaced)."""
    last, superseded = {}, []
    for event in events:
        probe = _result_of(event).get('probe')
        if event.get('name') != 'probe_run' or not isinstance(probe, str):
            continue
        path = str(Path(probe).resolve())
        sha = (event.get('artifacts') or {}).get(path)
        if sha is None:
            continue
        if path in last:
            superseded.append(last[path][1].get('dispatch_id'))
        last[path] = (sha, event)
    return {path: kept for path, kept in last.items() if _answered(kept[1])}, superseded


def _record_writers(accepted):
    """{record sha256: digests of the models whose runs wrote it}, from the `records` the configurator bound in the
    notes of accepted handoffs. A handoff without them binds nothing."""
    writers = {}
    for handoff in accepted:
        notes = (handoff.get('notes') or {}).get('inline')
        configurator = notes.get('configurator') if isinstance(notes, dict) else None
        for record in (configurator.get('records') if isinstance(configurator, dict) else None) or []:
            if isinstance(record, dict) and isinstance(record.get('sha256'), str):
                writers.setdefault(record['sha256'], set()).add(record.get('model_sha256'))
    return writers


def _device_diff(events, assignment, contracts=None, writers=None):
    """What the assigned model got wrong, from the configurator's own record of one assignment: its validation of the
    assigned model and the tool results it observed on the device connection it supplied.

    - a `model_contract` entry: the configurator's own validation of an assigned model (`contracts` maps the input's
      path to its model_validate result) found defects. For an interface model the runtime refuses such a model before
      any transport opens; a register model's validation also covers what its probe does not check (a safety entry,
      provenance, the conversion). Either way the entry is the configurator's finding about the model, never that the
      device did anything.
    - a probe_run or interface_execute failure of the assigned model that the runtime attributes to the model after
      the transport opened;
    - probe_diff defects over a probe record that such a run of the assigned model wrote, or that an earlier stage
      handed on when the assigned model wrote it (`writers` maps a record's digest to the digests of the models that
      wrote it, as the earlier stage's notes bound them).
    Replays, records and captures the worker supplied, other models and their records, and operator and host faults
    are not the model contradicted. Records are named by content, and only records that are handed on: an input of
    this assignment, or a record this stage hands on (a record a later run replaced is not named). Never a worker
    claim, never truth."""
    from interface_runtime.faults import classify_failure
    paths = assignment.get('inputs') or {}
    inputs = set(paths.values())
    assigned = lambda event: _ran_assigned_model(event, assignment)
    def live(event):
        return event.get('connected') is True and _result_of(event).get('evidence_kind', 'live') == 'live' and assigned(event)
    def opened(results):
        failed = [r for r in _listed(results) if r.get('ok') is False]
        return bool(failed) and all(r.get('transport_open_attempted') is True for r in failed)
    def record_of(event):
        probe = _result_of(event).get('probe')
        return (event.get('artifacts') or {}).get(str(Path(probe).resolve())) if isinstance(probe, str) else None
    kept, _ = _probe_records(events)
    # Records the diff may name: those handed on to this assignment and those this stage hands on.
    named = {sha: Path(path).name for path, sha in paths.items()}
    named.update({sha: Path(path).name for path, (sha, _) in kept.items()})
    # Records a probe_diff may compare: those handed on that the assigned model wrote, and those the assigned model's
    # runs wrote here as the device answered. A record of another model says what the device did for that model.
    handed = {sha for sha in inputs if inputs & (writers or {}).get(sha, set())}
    compared = handed | {record_of(event) for event in events if live(event) and _answered(event)}
    probes = [_result_of(event)['probe'] for event in events
              if event.get('name') == 'probe_run' and isinstance(_result_of(event).get('probe'), str)]
    replacements = sorted(((path, Path(path).name) for path in [*paths, *probes]), key=lambda item: -len(item[0]))
    if assignment.get('workspace'):
        replacements.append((str(assignment['workspace']), '<' + str(assignment.get('stage')) + ' workspace>'))
    cite = lambda sha: {'name': named[sha], 'sha256': sha} if sha in named else None
    diff = []
    for path, validation in (contracts or {}).items():
        defects = validation.get('defects') if isinstance(validation, dict) and validation.get('ok') is not True else None
        if path in paths and isinstance(defects, list) and defects:
            diff.append(_replaced({'source': 'model_contract', 'model': {'name': Path(path).name, 'sha256': paths[path]},
                                   'defects': defects}, replacements))
    for event in events:
        name, result, entry = event.get('name'), _result_of(event), None
        if name == 'probe_diff' and assigned(event) and isinstance(result.get('defects'), list) and result['defects']:
            shas = event.get('argument_sha256') or {}
            sha = shas.get('probe_json', shas.get('probe'))
            if sha is not None and sha in compared and result.get('source_evidence_kind', 'live') == 'live':
                entry = {'probe': cite(sha), 'defects': result['defects']}
        elif name in ('probe_run', 'interface_execute') and not event.get('ok') and live(event):
            failed = [r for r in _listed(result.get('results') if name == 'probe_run' else [result]) if r.get('ok') is False]
            model = (result.get('fault') == 'model' if name == 'probe_run' else
                     result.get('schema') == 'interface-result/1' and classify_failure(result) == 'model')
            # Register probes predate fault labels: exit 2 is an unexpected identity, exit 3 no acknowledgement.
            legacy = name == 'probe_run' and 'fault' not in result and result.get('_exit') in (2, 3)
            if (model and opened(failed)) or legacy:
                error = result.get('error') if isinstance(result.get('error'), dict) else {'message': result.get('error')}
                what = result.get('error_message') or error.get('message') or (
                    'device identity ' + repr(result.get('identity')) + ' is not one the model expects' if result.get('identity_ok') is False else None)
                entry = {'code': result.get('error_code') or error.get('code'), 'what': what,
                         'probe': cite(record_of(event)) if name == 'probe_run' else None, 'failed': failed}
        if entry is not None:
            entry = {key: value for key, value in entry.items() if value not in (None, [])}
            diff.append(_replaced({'source': name, 'dispatch_id': event.get('dispatch_id'), **entry}, replacements))
    return diff


def _notes(result, checked, outcome, *, refusals=(), missing=()):
    """stage-notes/1: worker claims, truth-free configurator checks, open questions and the outcome.

    `missing` names stage inputs that were absent; `refusals` are the gateway's refusals of this worker's tool calls.
    The configurator's `records` bind each probe record a stage hands on to the digest of the model that wrote it.
    """
    report = result.get('report') if isinstance(result.get('report'), dict) else {}
    listed = lambda key: report[key] if isinstance(report.get(key), list) else []
    observed = checked.get('notes') or {}
    unresolved = [item for item in listed('unresolved') if isinstance(item, str)]
    questions = list(missing) + unresolved
    if result.get('status') not in ('completed', 'skipped'):
        questions.append('Worker runtime produced no usable report: ' + str(result.get('reason')))
    elif report.get('status') in ('needs_revision', 'blocked', 'failed'):
        questions.append('Worker reported ' + report['status'] + ': ' + str(report.get('summary', '')))
    return {'schema': 'stage-notes/1',
            'worker': {'runtime_status': result.get('status'), 'runtime_reason': result.get('reason'),
                       'status': report.get('status'), 'summary': report.get('summary'),
                       'checks': listed('checks'), 'unresolved': unresolved},
            'configurator': {'checks': observed.get('checks', []), 'dropped_artifacts': observed.get('dropped_artifacts', []),
                             'records': observed.get('records', []),
                             'tool_refusals': list(refusals), 'device_diff': checked.get('device_diff')},
            'open_questions': questions, 'outcome': {'kind': outcome.kind, 'reason': outcome.reason}}


def _notes_bytes(notes):
    """The exact bytes of notes.json; its sha256 is recorded with the handoff and re-derivable from the inline copy."""
    return (json.dumps(notes, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=1) + '\n').encode('utf-8')


def _prior_notes(accepted, revision):
    """Notes of the handoffs that make up the current revision: acquire is shared, later stages are per revision."""
    return [{'stage': h['stage'], 'revision': h.get('revision', 0), 'notes': h['notes']['inline']} for h in accepted
            if (h.get('notes') or {}).get('inline') and (h.get('revision', 0) == revision or h['stage'] == STAGES[0])]


def _notes_summary(handoff):
    """One handoff's notes in a few fields for status and the dashboard; free text is shortened."""
    notes = (handoff.get('notes') or {}).get('inline')
    if not isinstance(notes, dict):
        return None
    clip = lambda text: text[:300] if isinstance(text, str) else None
    worker, configurator, outcome = (notes.get(key) if isinstance(notes.get(key), dict) else {}
                                     for key in ('worker', 'configurator', 'outcome'))
    return {'stage': handoff.get('stage'), 'revision': handoff.get('revision', 0), 'outcome': outcome.get('kind'),
            'reason': clip(outcome.get('reason')), 'worker_status': worker.get('status'), 'summary': clip(worker.get('summary')),
            'open_questions': [clip(q) for q in notes.get('open_questions') or [] if isinstance(q, str)][:5],
            'failed_checks': [c.get('name') for c in configurator.get('checks') or [] if isinstance(c, dict) and c.get('passed') is False],
            'device_diff_entries': len(configurator.get('device_diff') or [])}


def stage_notes(accepted):
    """The notes summary of each stage's latest handoff, in stage order; a stage without notes is left out."""
    latest = {h['stage']: h for h in accepted}
    return [summary for summary in (_notes_summary(latest[stage]) for stage in STAGES if stage in latest) if summary]


# The run's one final verdict is stored in verdicts under this id suffix; it is not an evaluator stage verdict.
FINAL = ':final'
# How emit_check verified a handed-off package (the emit check's `verified_by`); only REPLAYED meets Ruling 7. A
# replay passes from whatever exchanges the package bundles; it replays device exchanges only when the package's probe
# evidence was recorded live from the device, not from a replay the worker supplied.
REPLAYED, SUPPLIED = 'replay of the recorded device exchanges', 'replay of supplied exchanges, not recorded from the device'
SEEDED = 'seeded package-versus-model check'
UNCERTAIN_AFTER_VERDICT = 'Outstanding effect is uncertain after the final verdict; operator reconciliation is required'
UNCERTAIN_AWAITING = ('Outstanding effect is uncertain after fresh reuse; reconcile it, then resume, and the frozen driver '
                      'awaits its grade')


def _final_verdict(db, run_id):
    row = db.execute('SELECT payload FROM verdicts WHERE id=?', (run_id + FINAL,)).fetchone()
    return json.loads(row['payload']) if row else None


def equivalence_label(equivalence):
    return {True: 'equivalent', False: 'not equivalent'}.get((equivalence or {}).get('equivalent'), 'unscored')


def _verdict_reason(equivalence):
    """The completed run's reason: its verdict and, unless equivalent, the first differences."""
    label = equivalence_label(equivalence)
    reasons = [d['reason'] for d in equivalence.get('diff') or [] if isinstance(d, dict) and isinstance(d.get('reason'), str)]
    detail = '; '.join(reasons[:3]) + ('; ' + str(len(reasons) - 3) + ' more' if len(reasons) > 3 else '')
    return 'Final verdict: ' + label + (': ' + detail if label != 'equivalent' and reasons else '')


# The public evaluation record the bench harness posts after a run: check ids with passed flags and failing ids with a
# reason. Expected values, tolerances, witnesses and raw records stay sealed, so no other field is accepted.
EVALUATION = {'schema', 'status', 'equivalent', 'basis', 'method', 'checks', 'diff', 'passed', 'total', 'mission',
              'frozen_artifact_sha256', 'capabilities_sha256', 'evidence_sha256', 'protocol'}


# How the evaluator grades a frozen driver: on a fresh emulator of its own, or (legacy tq9 and bme280) from the fresh
# reuse the run recorded.
METHODS = ('fresh-final', 'recorded-reuse')


def _evaluation_errors(record):
    """Why a posted evaluation is not a well-formed equivalence/1 record, or []."""
    sha = lambda value: value is None or isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None
    text = lambda value: isinstance(value, str) and 0 < len(value) <= 300
    if not isinstance(record, dict) or set(record) != EVALUATION:
        return ['an equivalence/1 record has exactly the fields ' + ', '.join(sorted(EVALUATION))]
    errors = []
    if (record['schema'], record['basis'], record['protocol']) != ('equivalence/1', 'evaluator', PROTOCOL) or \
            record['method'] not in METHODS:
        errors.append('schema equivalence/1, basis evaluator, method ' + ' or '.join(METHODS) + ' and protocol ' + PROTOCOL
                      + ' are required')
    checks, diff = record['checks'], record['diff']
    if not isinstance(checks, list) or any(not isinstance(c, dict) or set(c) != {'id', 'passed'} or not text(c['id'])
                                           or type(c['passed']) is not bool for c in checks):
        errors.append('checks are [{id, passed}]')
        checks = []
    if not isinstance(diff, list) or any(not isinstance(d, dict) or set(d) != {'id', 'reason'} or not text(d['id'])
                                         or not text(d['reason']) for d in diff):
        errors.append('diff is [{id, reason}]')
    if any(type(record[key]) is not int for key in ('passed', 'total')) or \
            (record['passed'], record['total']) != (sum(c['passed'] for c in checks), len(checks)):
        errors.append('passed and total count the checks')
    status, equivalent = record['status'], record['equivalent']
    if (status, type(equivalent)) not in (('graded', bool), ('no-driver', bool), ('unscored', type(None))) or \
            status == 'no-driver' and equivalent is not False:
        errors.append('status graded or no-driver with equivalent true or false, or unscored with equivalent null')
    elif equivalent is True and (not checks or not all(c['passed'] for c in checks) or diff):
        errors.append('an equivalent driver passed every check, with no difference')
    elif equivalent is not True and not diff:
        errors.append('a driver that is not judged equivalent names its differences')
    mission = record['mission']
    if mission is not None and (not isinstance(mission, dict) or not set(mission) <= {'name', 'passed', 'reason', 'fault'}
                                or type(mission.get('passed')) not in (bool, type(None))
                                or any(not isinstance(mission[k], str) or len(mission[k]) > 300 for k in set(mission) - {'passed'})):
        errors.append('mission is {name, passed, reason, fault}')
    if not all(sha(record[key]) for key in ('frozen_artifact_sha256', 'capabilities_sha256', 'evidence_sha256')):
        errors.append('hashes are sha256 hex or null')
    elif status != 'unscored' and record['evidence_sha256'] is None:
        errors.append('a grade names the evidence it was sealed in')
    return errors


def _packages(handoff):
    """The emitted packages among a handoff's artifacts: directories that hold a manifest."""
    return [a for a in handoff['artifacts'] if Path(a['path']).is_dir() and (Path(a['path']) / 'manifest.json').is_file()]


def _emit_check_failure(result):
    """Why emit_check did not pass a package, in the tool's own terms."""
    if isinstance(result.get('error'), str):
        return result['error']
    if result.get('integrity_ok') is False:
        return 'package integrity failed'
    if result.get('runtime_current') is False:
        return 'runtime is not current, so replay did not run'
    if result.get('replay_status') not in (None, 'passed'):
        return 'replay ' + str(result['replay_status'])
    return 'seeded check failed' if 'seeds_run' in result else 'emit_check reported failure'


def _emit_builds(events):
    """{path: sha256} of each package_dir a successful emit_package built, as the configurator hashed it when the tool
    returned, oldest first: its own record of one assignment's tools (tool.finished payloads), never a worker's claim."""
    built = {}
    for event in events:
        raw = _result_of(event).get('package_dir') if event.get('ok') and event.get('name') == 'emit_package' else None
        path = str(Path(raw).resolve()) if isinstance(raw, str) else None
        if path in (event.get('artifacts') or {}):
            built.pop(path, None)
            built[path] = event['artifacts'][path]
    return built


def _observed_packages(named, built, given, unseen):
    """The packages an emit may hand on: those the worker's report names (a directory with a manifest.json), else the
    newest emit_package build, each only while its bytes are ones the configurator observed (`given`: digests it
    recorded itself). A report names which package, never its hash. Returns (packages, not_handed_off, dropped); a
    package is not handed off as `unseen`, or as changed when it is a build whose bytes changed after the tool returned."""
    packages = [p for p in dict.fromkeys(named) if p.is_dir() and (p / 'manifest.json').is_file()]
    packages = packages or [Path(path) for path in list(built)[-1:]]
    observed, not_handed_off, dropped = [], [], []
    for package in packages:
        try:
            current = digest(package)
        except (OSError, ValueError):
            current = None
        if current is not None and current in given:
            observed.append(package)
            continue
        why = 'changed after emit_package returned' if str(package) in built else unseen
        not_handed_off.append({'package': package.name, 'reason': why})
        dropped.append({'path': str(package), 'reason': 'Package not handed off: ' + why})
    return observed, not_handed_off, dropped


def _toolchain_fault(result):
    """The exception type a toolchain error result names ('OSError' for 'OSError: ...'), or 'toolchain error'."""
    error = result.get('error')
    kind = error.split(':', 1)[0].strip() if isinstance(error, str) and ':' in error else ''
    return kind if re.fullmatch(r'[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Expired)', kind) else 'toolchain error'


def _host_fault(result):
    """Whether a tool result says the host, not its input, failed, by a positive list: the tool says so (fault host),
    or it is a toolchain error naming a host failure (the OSError family, ImportError, MemoryError or a subprocess
    timeout), or an error that names no exception type, which the toolkit returns when even its own record of the call
    failed. Any other error, such as an AttributeError or RecursionError on content a worker wrote, is no host fault."""
    if result.get('fault') == 'host':
        return True
    if not isinstance(result.get('error'), str):
        return False
    import builtins
    kind = _toolchain_fault(result)
    cls = getattr(builtins, kind, None)
    return (kind == 'toolchain error' or kind.rsplit('.', 1)[-1] == 'TimeoutExpired'
            or isinstance(cls, type) and issubclass(cls, (OSError, ImportError, MemoryError)))


def _before_contact(result):
    """Whether a tool declared that it stopped before it contacted the device: its own result, with no execution
    record, says no transport was opened (the runtime's interface-result/1 does the same for one execution)."""
    return result.get('transport_open_attempted') is False and not _listed(result.get('results'))


def _declared_refusal(result):
    """Whether a tool declared that it refused before it contacted the device (`_before_contact`). A host fault it
    declares there (its temporary folder, a snapshot copy it cannot read back) is no refusal: nothing reached the
    device, but the call says nothing about its input either, so it is inconclusive."""
    return _before_contact(result) and result.get('fault') != 'host'


def _live_outcome(event):
    """How one live operation the configurator dispatched ended, from its own record of the tool result:

    - 'succeeded': it succeeded, or some of its operations passed on the device and every one that did not was
      refused before the device was contacted (a refused operation is not a live operation);
    - 'refused': refused before the device was contacted, so not a live operation. Either the runtime's preflight
      refused it (no execution passed and none that failed attempted to open the transport), or the tool declared
      that it refused before its first device call (transport_open_attempted false and no execution record: an
      argument, binding, grant, snapshot or integrity refusal);
    - 'failed': the model it ran failed on the device. The runtime attributes a failure to the model after the
      transport opened, or a register package's own live suite reported failing checks. The verdict counts it as the
      driver's only when the operation ran the frozen bytes;
    - 'inconclusive': anything else that did not succeed, such as an operator, host or transport fault (exit 4), a
      host fault the tool declared before its first device call, or a toolchain error the tool did not declare as a
      refusal, which may have come after the device was contacted (labelled a host fault only when it names one,
      `_host_fault`). It says nothing about the driver.

    Returns (outcome, fault detail for an inconclusive one)."""
    from interface_runtime.faults import classify_failure
    result = _result_of(event)
    if event.get('ok') is True:
        return 'succeeded', None
    executions = (_listed(result['results']) if isinstance(result.get('results'), list) else
                  [result] if result.get('schema') == 'interface-result/1' else [])
    failed = [r for r in executions if r.get('ok') is not True]
    refused = bool(failed) and all(r.get('transport_open_attempted') is False for r in failed)
    if event.get('refused_before_open') is True or _declared_refusal(result) or (refused and len(failed) == len(executions)):
        return 'refused', None
    fault = lambda r: classify_failure(r) if r.get('schema') == 'interface-result/1' else r.get('fault')
    if any(fault(r) == 'model' and r.get('transport_open_attempted', True) is True for r in failed):
        return 'failed', None
    if refused and result.get('fault') is None:
        return 'succeeded', None
    if executions or 'fault' in result or result.get('_exit') == 4:
        first = failed[0] if failed else result
        error = first.get('error') if isinstance(first.get('error'), dict) else {}
        named = _toolchain_fault(first) if isinstance(first.get('error'), str) else 'toolchain error'
        code = first.get('error_code') or error.get('code') or (named if named != 'toolchain error' else None)
        return 'inconclusive', str((fault(first) if failed else result.get('fault')) or 'unclassified') + ' fault' + (
            ' (' + str(code) + ')' if code else '')
    if isinstance(result.get('output'), list):
        # A register package's live suite ran on the device; failing checks are the driver's.
        return ('failed', None) if isinstance(result.get('failed'), int) and result['failed'] > 0 else (
            'inconclusive', 'live suite exited with an error')
    # The tool raised without declaring a refusal: say whether that names a host failure, never guess one.
    return 'inconclusive', ('host fault' if _host_fault(result) else 'undeclared toolchain error') + (
        ' (' + _toolchain_fault(result) + ')')


def _frozen_package(package, strict=False):
    """What judging a run of a frozen package needs, read from its bytes: its content digest, its manifest's digest
    (which the snapshot emit_test_live executes carries), its driver directory's digest (which interface_execute runs
    as a model), its identity operation, each operation's declared effect (absent is actuate, as the gateway reads it),
    whether its channel declares that opening it resets the device (its runtime then needs the reset grant for every
    operation) and its functions: the operations beyond identity with a step that reaches the device (an exchange or
    a USB control transfer) asking something identity's own steps do not. An operation of delays alone sends nothing,
    and one that repeats identity's requests asks nothing identity does not, however it reads or checks the reply, so
    running either is no evidence of function. A step is compared by what reaches the device: its op and tx, and for
    a USB control transfer its setup and its rx, whose length the transfer requests. What a step accepts back (its
    receive mode or bound on a stream, expect and match), its capture name and timeout ask the device nothing. The
    model is read as the runtime reads it. A register (schema 1) model has no named operations: its effects and
    functions are None.

    A part that is absent or does not parse is None. So is one the host cannot read, unless `strict`: the final verdict
    reads a package whose digest it has just verified, so a host failure there says nothing about the driver and
    raises, stopping the run for the host instead of grading it."""
    root = Path(package['path'])
    def read(load):
        try:
            return load()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError, ValueError, RecursionError):
            return None
        except OSError:
            if strict:
                raise
            return None
    model = read(lambda: _model_json(root / 'driver' / 'model.json'))
    identity = model.get('identity') if isinstance(model, dict) else None
    identity = identity.get('operation') if isinstance(identity, dict) else None
    operations = model.get('operations') if isinstance(model, dict) else None
    named = isinstance(operations, dict) and model.get('schema') != 'interface-model/1'
    effects = ({name: op.get('effect', 'actuate') if isinstance(op, dict) else None for name, op in operations.items()}
               if named else None)
    def requests(op):
        """What each step that reaches the device asks of it."""
        steps = op.get('steps') if isinstance(op, dict) else None
        sent = lambda step: ('op', 'tx', 'setup', 'rx') if step.get('op') == 'usb_control' else ('op', 'tx')
        return {json.dumps({key: step.get(key) for key in sent(step)}, sort_keys=True)
                for step in steps if isinstance(step, dict) and step.get('op') != 'delay'} if isinstance(steps, list) else set()
    own = requests(operations.get(identity)) if named and identity is not None else set()
    functions = (sorted(name for name, op in operations.items() if name != identity and requests(op) - own)
                 if named and identity is not None else None)
    channel = model.get('channel') if isinstance(model, dict) else None
    return {'sha256': package['sha256'], 'manifest_sha256': read(lambda: digest(root / 'manifest.json')),
            'driver_sha256': read(lambda: digest(root / 'driver')), 'identity': identity, 'effects': effects,
            'resets_on_open': isinstance(channel, dict) and channel.get('resets_on_open') is True,
            'functions': functions}


NEVER_RAN = 'fresh reuse never ran the frozen package with emit_test_live'
IDENTITY_ONLY = ('fresh reuse ran no function of the frozen package beyond its identity operation with emit_test_live; '
                 "identity alone is not evidence of function, nor is an operation that only waits or repeats identity's requests")
UNGRANTED = ("the operator's grants permit no function of the frozen package beyond its identity operation (each needs an "
             'effect, or the reset its channel declares on open, that was not granted), so fresh reuse could not test '
             'one; that limit says nothing about the driver')
# Why the gateway refused every connection to the run's device (`_unreachable`). Each but UNLISTENED is an operator or
# host limit that says nothing about the driver; UNLISTENED is the candidate pipeline's own omission.
UNBOUND = ('the operator bound no device to this run, so the configurator refused every device connection: no live probe '
           'or fresh reuse could reach a device; that limit says nothing about the driver')
UNREACHABLE = ('the operator granted no reset and the bound serial device was observed resetting when opened, so the '
               'configurator refused every connection to it: no live probe or fresh reuse could reach the device; that '
               'limit says nothing about the driver')
UNHEARD = ('the operator granted no reset and no listen to the bound serial device succeeded (an operator, host or '
           'transport failure, or a listen whose result was never recorded), so the configurator refused every '
           'connection to it: no live probe or fresh reuse could reach the device; that says nothing about the driver')
UNLISTENED = ('no worker listened to the bound serial device with acquire_serial_listen, which needs no grant, and without '
              'a listen or a reset grant the configurator refuses every connection to a serial device, so fresh reuse '
              'could not reach it')


def _reset_ungranted(package, grants):
    """Whether the frozen package (`_frozen_package`) has a function, but its channel resets the device on open and the
    operator's `grants` (None when not known) hold no reset: its runtime then refuses every operation of those bytes
    before it opens the device, a live probe of them included."""
    return (package is not None and grants is not None and isinstance(package.get('functions'), list)
            and bool(package['functions']) and package.get('resets_on_open') is True and 'reset' not in grants)


def _ungranted(package, grants):
    """Whether the operator's grants (`grants`, or None when not known) permit none of the frozen package's functions
    (`_frozen_package`), although it has one: the gateway refuses every function whose effect is not granted, and the
    package's own runtime refuses every operation, before it opens the device, when its channel resets the device on
    open and the reset is not granted (`_reset_ungranted`)."""
    if (package is None or grants is None or not isinstance(package.get('functions'), list)
            or not isinstance(package.get('effects'), dict)):
        return False
    functions = [package['effects'].get(name) for name in package['functions']]
    return _reset_ungranted(package, grants) or bool(functions) and not any(effect in ('read', *grants) for effect in functions)


def _unreachable(binding, state, grants):
    """Why the gateway refused every connection to the run's device (`_dispatch`), or None when it may have opened one.
    UNBOUND when the operator bound no device. For a serial `binding`, while the operator's `grants` (None when not
    known) hold no reset, by the run's binding `state` as of fresh reuse (`Controller._binding_state`): UNREACHABLE when
    the run observed the device resetting on open; otherwise, when no listen succeeded, UNHEARD if one failed outside
    the worker's own arguments and UNLISTENED if no worker tried. Only UNLISTENED is the candidate pipeline's omission:
    listening needs no grant."""
    if not binding:
        return UNBOUND
    if grants is None or 'reset' in grants or not _is_serial_binding(binding) or not isinstance(state, dict):
        return None
    if state.get('resets_on_open'):
        return UNREACHABLE
    if state.get('listened'):
        return None
    return UNHEARD if state.get('unheard') else UNLISTENED


def _fresh_reuse(package, events, started=(), grants=None, unreachable=None):
    """Judge fresh reuse's live operations of a frozen package (`_frozen_package`, or None when there is none), from
    the configurator's own records: `events` are tool.finished payloads and `started` tool.started payloads with
    their dispatch ids; `grants` are the run's operator grants; `unreachable` says why the gateway refused every
    connection to the run's device (`_unreachable`), or is None. The final verdict and each reuse attempt's notes use
    this one rule.

    - It passes when every live operation succeeded (a call refused before the device was contacted is not one) and
      at least one emit_test_live ran the frozen package and a function of it beyond its identity operation (an
      operation with a step that reaches the device) on the device: identity alone is not evidence of function, nor is
      an operation that only waits. The frozen package ran when the configurator hashed those bytes before dispatch and
      the snapshot the tool executed carries the frozen manifest.
    - It fails when the driver failed on the device (a failure is the driver's only when the operation ran the frozen
      bytes: the package for emit_test_live, or its driver directory for interface_execute), or when no such
      operation ran, including when no worker listened to a serial device the gateway then kept from fresh reuse
      (UNLISTENED): listening needs no grant.
    - It is unsettled (None) when a live operation was inconclusive (an operator, host or transport fault, or a
      failure of other bytes) or was dispatched without a recorded result, or when no function ran because the
      operator's grants permit none (an effect or the reset its channel needs) or the operator's or the host's limits
      let the configurator open no connection to the device: those calls are refused before the device, which says
      nothing of the driver.

    Returns (the fresh_reuse_live_operations check, its diff entries)."""
    shas = lambda e: e.get('argument_sha256') or {}
    snapshot = lambda e: _result_of(e).get('manifest_sha256')
    dispatched = lambda e: (package is not None and e.get('name') == 'emit_test_live'
                            and shas(e).get('package_dir') == package['sha256'])
    emitted = lambda e: dispatched(e) and snapshot(e) is not None and snapshot(e) == package['manifest_sha256']
    ran_driver = lambda e: emitted(e) or (package is not None and package['driver_sha256'] is not None
                                          and e.get('name') == 'interface_execute' and shas(e).get('model_dir') == package['driver_sha256'])
    def outcome_of(event):
        outcome, detail = _live_outcome(event)
        if outcome == 'failed' and not ran_driver(event):
            return 'inconclusive', 'model fault outside the emitted driver'
        return outcome, detail
    def beyond_identity(event):
        """Whether the call ran a function of the frozen package (beyond its identity operation) on the device: True,
        False, or None when its result does not say (it recorded no execution, or stopped at a fault before one)."""
        result = _result_of(event)
        if isinstance(result.get('output'), list):
            return True  # a register package's live suite reads its identity, then measures
        executions = _listed(result.get('results'))
        other = lambda name: name in (package['functions'] or ())
        if any(other(r.get('operation')) and r.get('transport_open_attempted') is not False for r in executions):
            return True
        pending = [name for name in result.get('not_run') if other(name)] if isinstance(result.get('not_run'), list) else []
        return None if pending or not executions else False
    def functional(event, outcome):
        if not dispatched(event) or outcome == 'refused':
            return False
        if outcome in ('inconclusive', 'unresolved'):
            # It stopped at a fault: it may have run a function of the frozen package, unless it plainly ran other
            # bytes or no function.
            return beyond_identity(event) is not False and snapshot(event) in (None, package['manifest_sha256'])
        return emitted(event) and beyond_identity(event) is True
    recorded = {e.get('dispatch_id') for e in events}
    # A device dispatch without a recorded result (the tool raised after it was dispatched) is unresolved.
    rows = [(e, *outcome_of(e)) for e in events if e.get('connected') is True]
    rows += [(e, 'unresolved', None) for e in started if e.get('connected', True) is True and e.get('dispatch_id') not in recorded]
    rows.sort(key=lambda row: int(row[0]['dispatch_id']) if str(row[0].get('dispatch_id')).isdigit() else 0)
    failed = [e for e, outcome, _ in rows if outcome == 'failed']
    unsettled = [row for row in rows if row[1] in ('inconclusive', 'unresolved')]
    refused = sum(1 for _, outcome, _ in rows if outcome == 'refused')
    ran = [e for e, outcome, _ in rows if emitted(e) and outcome != 'refused']
    exercised = [e for e, outcome, _ in rows if functional(e, outcome)]
    blocked = not exercised and unreachable is not None and unreachable != UNLISTENED
    ungranted = not exercised and _ungranted(package, grants)
    check = {'id': 'fresh_reuse_live_operations',
             'passed': (False if failed else None if blocked or ungranted else False if not exercised else None if unsettled
                        else True),
             'operations': [{'dispatch_id': e.get('dispatch_id'), 'name': e.get('name'), 'ok': outcome == 'succeeded',
                             'outcome': outcome, **({'fault': detail} if detail else {}), 'emitted_package': emitted(e)}
                            for e, outcome, detail in rows],
             'basis': 'Live operations the configurator dispatched for fresh reuse on the device connection it supplied'}
    dispatch = lambda e: ' (dispatch ' + str(e.get('dispatch_id')) + ')'
    diff = [{'id': check['id'], 'reason': str(e.get('name')) + ' failed on the device' + dispatch(e)} for e in failed]
    diff += [{'id': check['id'], 'reason': str(e.get('name')) + (
        ' was inconclusive: ' + str(detail) + ', not a driver failure' if outcome == 'inconclusive' else
        ' was dispatched but its result was never recorded') + dispatch(e)} for e, outcome, detail in unsettled]
    if not exercised:
        # The rule is a live test of a function of the frozen package itself; running its driver directory as a
        # model is judged above but is not that test.
        diff.append({'id': check['id'], 'reason': unreachable if blocked else UNGRANTED if ungranted else IDENTITY_ONLY if ran else
                     UNLISTENED if unreachable == UNLISTENED else NEVER_RAN + (
            ' (' + str(refused) + ' call' + ('s' if refused > 1 else '') + ' refused before the device was contacted)' if refused else '')})
    return check, diff


def _self_evidence(accepted, events, started=(), assignments=(), grants=None, unreachable=None):
    """A generic run's final verdict, from the configurator's own records only.

    `events` are tool.finished payloads; `started` are tool.started payloads with their dispatch ids; `assignments`
    are the run's reuse assignments as {id, inputs}, with the input hashes the configurator recorded; `grants` are the
    run's operator grants; `unreachable` says why the gateway refused every connection to the run's device as of fresh
    reuse (`_unreachable`), or is None.

    - **Equivalent:** the emitted package passed emit_check replay of the recorded device exchanges, which the
      configurator ran at emit, and fresh reuse passed `_fresh_reuse`: every live operation succeeded, and at least
      one ran the frozen package with emit_test_live (the bytes the configurator hashed before dispatch, executed as
      a snapshot of the frozen manifest) and a function of it beyond its identity operation, on the connection the
      configurator supplied.
    - **Not equivalent:** no single package passed emit_check replay of device exchanges (a seeded package-versus-model
      check, or a replay of exchanges the worker supplied, is not one), the driver failed on the device, or fresh reuse
      never ran a function of the package beyond its identity with emit_test_live.
    - **Unscored (None):** otherwise, when a live operation was inconclusive (an operator, host or transport fault, or
      a failure of bytes other than the emitted driver) or was dispatched without a recorded result, or when the
      operator's grants permit no function of the package beyond its identity (an effect or the reset its channel
      needs), so none could run. So it is without a replay of device exchanges when no live probe could have recorded
      one: the operator's or the host's limits let the configurator open no connection to the device (any `unreachable`
      but UNLISTENED, the pipeline's own omission), or the frozen package's runtime refuses every operation of its bytes
      for want of the reset grant its channel declares. That excuses only the one package that passed emit_check
      without a replay of device exchanges, never a driver that was not emitted, a candidate emit_check rejected
      offline or several packages handed off.

    The frozen package is read strictly: a host failure to read it raises (a host stop that resume grades again),
    never a verdict about the driver.

    Fresh reuse is every reuse assignment that received the frozen package, so an attempt that stopped before its
    handoff and was resumed is judged with the accepted one. This is self-evidence about the driver's outputs on the
    device, labelled as such; never an independent grade or truth."""
    emits = [h for h in accepted if h['stage'] == 'emit']
    emit = next((h for h in reversed(emits) if _packages(h)), None)
    packages = _packages(emit) if emit else []
    frozen = {'name': Path(packages[0]['path']).name, 'sha256': packages[0]['sha256']} if len(packages) == 1 else None
    check_of = lambda handoff: next((c for c in (handoff or {}).get('checks', []) if isinstance(c, dict)
                                     and c.get('name') == 'package_integrity_and_replay'), {})
    passed = frozen is not None and check_of(emit).get('passed') is True
    verified = check_of(emit).get('verified_by')
    kind = verified[0] if isinstance(verified, list) and len(verified) == 1 and isinstance(verified[0], str) else None
    # Ruling 7 counts only a replay of the recorded device exchanges. A seeded package-versus-model check compares the
    # package with its own model, not with the device, a replay of supplied exchanges reached no device, and a check
    # that recorded neither establishes no replay.
    replayed = passed and kind == REPLAYED
    replay = {'id': 'emitted_package_replay', 'passed': replayed, **({'package': frozen} if frozen else {}),
              'basis': 'emit_check' + (' ' + kind if kind else '') + ', run by the configurator at emit'}
    reuse = next((h for h in reversed(accepted) if h['stage'] == 'reuse'), None)
    judged = {a['id'] for a in assignments if frozen is not None and frozen['sha256'] in set((a.get('inputs') or {}).values())}
    judged |= {reuse['assignment_id']} if reuse is not None else set()
    mine = lambda e: e.get('assignment_id') in judged
    package = _frozen_package(packages[0], strict=True) if frozen else None
    operations, live = _fresh_reuse(package, [e for e in events if mine(e)], [e for e in started if mine(e)], grants,
                                    unreachable)
    diff = []
    if not replayed:
        if passed:
            reason = ('the emitted package passed emit_check without a replay of the recorded device exchanges'
                      + (' (' + kind + ')' if kind else ''))
        elif packages:
            reason = ('the emitted package has no passed emit_check' if frozen else
                      str(len(packages)) + ' emitted packages; no single driver to judge')
        else:
            # Generic emit hands off only packages that passed; the check counts the ones it ran emit_check on.
            latest = check_of(emits[-1] if emits else None)
            candidates = latest.get('candidates') if isinstance(latest.get('candidates'), int) else 0
            why = next((r['reason'] for r in latest.get('rejected') or [] if isinstance(r, dict) and isinstance(r.get('reason'), str)), None)
            # A package emit built whose bytes the configurator did not observe was not handed off: a driver was built.
            gone = next((r['reason'] for r in latest.get('not_handed_off') or [] if isinstance(r, dict) and isinstance(r.get('reason'), str)), None)
            reason = ('emitted package not handed off: ' + gone if not candidates and gone else 'no driver emitted' if not candidates else
                      'the emitted package did not pass emit_check' + (': ' + why if why else '') if candidates == 1 else
                      'none of the ' + str(candidates) + ' emitted packages passed emit_check')
        diff.append({'id': replay['id'], 'reason': reason})
    # No live probe could record device exchanges to replay when the operator's or the host's limits kept every
    # connection from the device, or the frozen package's runtime refuses its bytes without the reset grant its channel
    # declares. Then the one package that passed emit_check lacks its replay of device exchanges for that limit, not
    # for the driver, and the verdict is unscored unless the driver failed; it is never equivalent. No driver emitted,
    # a candidate emit_check rejected offline, or several packages handed off failed whatever the device access.
    unprobed = (unreachable is not None and unreachable != UNLISTENED) or _reset_ungranted(package, grants)
    equivalent = (operations['passed'] if replayed else
                  None if unprobed and passed and operations['passed'] is not False else False)
    return {'equivalent': equivalent, 'basis': 'self-evidence', 'checks': [replay, operations], 'diff': diff + live}


@contextmanager
def _integrity_checked():
    """Adapter and toolchain ValueErrors about changed bytes or stale evidence are integrity stops."""
    try:
        yield
    except ValueError as error:
        if not isinstance(error, (IntegrityError, SafetyError)) and _CHANGED.search(str(error)):
            raise IntegrityError(str(error)) from error
        raise


def _has_terminal_final_failure(db, run_id):
    """Only the gated protocol's terminal shapes, which no stage produces any more and runs recorded before keep: the
    progress flag, or a frozen final evaluation that failed for the model inside reuse. The final verdict
    (`<run_id>:final`) ends a completed run whatever it says, so it is never one of them."""
    saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
    if saved and json.loads(saved['payload']).get('terminal_final_failure'):
        return True
    # Also recognize verdicts saved by versions that could lose the progress
    # marker to cancellation, or by a process that stopped after saving a verdict.
    verdicts = db.execute("SELECT payload FROM verdicts WHERE run_id=? AND stage!='finalize'", (run_id,)).fetchall()
    return any(value.get('final_evaluation') is True and value.get('fault') == 'model'
               and value.get('verdict') == 'failed' and not value.get('route')
               for value in (json.loads(row['payload']) for row in verdicts))


class Controller:
    """Public request interface; the daemon owns this object, clients do not."""
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.home / 'runs.sqlite3'
        self._lock = threading.RLock()
        self._workers = {}
        self._suite_locks = {}
        self._operations = {}
        self._inflight = set()
        self._closing = False
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, request_id TEXT UNIQUE, spec TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT, reason TEXT, created REAL NOT NULL,
                    updated REAL NOT NULL, cancelled INTEGER DEFAULT 0, uncertain INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS reports (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS handoffs (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS verdicts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS assignments (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                    state TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings (
                    identity TEXT PRIMARY KEY, run_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS pending_effects (
                    dispatch_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, assignment_id TEXT, actor TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS progress (
                    run_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(runs)')}
            if 'outcome_category' not in columns:
                db.execute("ALTER TABLE runs ADD COLUMN outcome_category TEXT NOT NULL DEFAULT 'unknown'")
            for row in db.execute("SELECT * FROM runs WHERE status IN ('queued','running')").fetchall():
                final = _final_verdict(db, row['id'])
                if final:
                    # A recorded verdict is never graded again: the run completes from it unless an effect is uncertain.
                    held = bool(row['uncertain'])
                    db.execute('UPDATE runs SET status=?,outcome_category=?,reason=?,updated=? WHERE id=?',
                               ('blocked' if held else 'completed', 'safety' if held else 'completed',
                                UNCERTAIN_AFTER_VERDICT if held else _verdict_reason(final['equivalence']), time.time(), row['id']))
                    db.execute("UPDATE assignments SET state='interrupted' WHERE run_id=? AND state='active'", (row['id'],))
                    self._event(row['id'], 'run.recovered', {'final_verdict': True, 'uncertain_effect': held}, db)
                    continue
                if _has_terminal_final_failure(db, row['id']):
                    db.execute("UPDATE runs SET status='failed',outcome_category='model',reason=?,updated=? WHERE id=?",
                               ('Frozen final evaluation failed', time.time(), row['id']))
                    db.execute("UPDATE assignments SET state='failed' WHERE run_id=? AND state='active'", (row['id'],))
                    self._event(row['id'], 'run.recovered', {'terminal_final_failure': True}, db)
                    continue
                spec = json.loads(row['spec'])
                uncertain = row['uncertain'] or bool(set(spec.get('effects',[])) & {'write','actuate'})
                reason = 'Configurator restarted; explicit resume required'
                try:
                    progress = self._workflow_progress(row['id'], db)
                except ValueError as error:
                    reason = str(error)
                else:
                    if progress['next_stage'] in ('finalize', None):
                        # Reuse was accepted and finalize dispatches nothing: only a recorded pending effect is outstanding.
                        uncertain = row['uncertain']
                        if spec.get('case') and not uncertain:
                            # A benchmark run's finalize only records that its frozen driver awaits the grade after the run.
                            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (row['id'], _json(
                                {**progress, 'next_stage': None, 'equivalence': 'pending'})))
                            db.execute("UPDATE runs SET status='completed',outcome_category='completed',reason=?,updated=? WHERE id=?",
                                       (PENDING, time.time(), row['id']))
                            db.execute("UPDATE assignments SET state='interrupted' WHERE run_id=? AND state='active'", (row['id'],))
                            self._event(row['id'], 'run.recovered', {'awaiting_evaluation': True, 'uncertain_effect': False}, db)
                            continue
                db.execute("UPDATE runs SET status='blocked',reason=?,uncertain=?,updated=? WHERE id=?",
                           (reason, int(uncertain),time.time(),row['id']))
                db.execute("UPDATE assignments SET state='interrupted' WHERE run_id=? AND state='active'", (row['id'],))
                self._event(row['id'],'run.recovered',{'uncertain_effect':bool(uncertain)},db)

        from .benchmark_support.suite_store import SuiteStore
        self._suites = SuiteStore(self._db)
        self._suites.recover()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=20)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA busy_timeout=20000')
            with db:
                yield db
        finally:
            db.close()

    def _event(self, run_id, kind, payload, db=None):
        if db is None:
            with self._db() as own:
                return self._event(run_id, kind, payload, own)
        cursor = db.execute('INSERT INTO events(run_id,kind,payload,created) VALUES(?,?,?,?)',
                   (run_id, kind, _json(payload), time.time()))
        return cursor.lastrowid

    def _state(self, run_id, status, reason=None, stage=None, *, outcome_category=None, from_verdict=False):
        """Change a run's status unless it was cancelled. Cancellation never replaces the completed status of a run
        with a final verdict.

        `from_verdict` completes the run from its recorded final verdict, even when a cancellation arrived after the
        verdict was committed: the verdict ends the run whatever happens next."""
        category = status if status in ('completed', 'cancelled') else outcome_category or 'unknown'
        if category not in ('completed','safety','integrity','operator','host','model','cancelled','unknown'):
            raise ValueError('Invalid owner outcome category')
        if from_verdict and status != 'completed':
            raise ValueError('Only completion follows from a final verdict')
        with self._db() as db:
            if from_verdict:
                changed = db.execute('UPDATE runs SET status=?,reason=?,stage=COALESCE(?,stage),updated=?,outcome_category=?,cancelled=0 '
                                     'WHERE id=? AND EXISTS(SELECT 1 FROM verdicts WHERE id=?)',
                                     (status, reason, stage, time.time(), category, run_id, run_id + FINAL))
            else:
                changed = db.execute('UPDATE runs SET status=?,reason=?,stage=COALESCE(?,stage),updated=?,outcome_category=? WHERE id=? '
                                     "AND (cancelled=0 OR ?='cancelled') AND NOT (?='cancelled' AND status='completed' "
                                     'AND EXISTS(SELECT 1 FROM verdicts WHERE id=?))',
                                     (status, reason, stage, time.time(), category, run_id, status, status, run_id + FINAL))
            if changed.rowcount:
                if status in TERMINAL:
                    db.execute("UPDATE assignments SET state=? WHERE run_id=? AND state='active'",(status,run_id))
                self._event(run_id, 'run.' + status, {'stage': stage, 'reason': reason, 'outcome_category': category}, db)

    def _row(self, run_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Unknown run_id')
        return row

    def call(self, method, params=None):
        params = params or {}
        if method == 'benchmark_spawn':
            from .benchmark_support.harness import bench_home, _spawn
            kind = params.get('kind')
            directory = Path(params['directory']).resolve()
            if kind not in ('trial', 'suite') or directory.parent != bench_home(self.home) / (
                    'trials' if kind == 'trial' else 'suites'):
                raise ValueError('A bench harness must launch in its owner\'s bench directory')
            channel = params.get('channel') or {}
            if (set(channel) != {'schema', 'port', 'token'} or channel['schema'] != 'bench-launch-channel/1'
                    or type(channel['port']) is not int or not 0 < channel['port'] < 65536
                    or not isinstance(channel['token'], str) or not re.fullmatch('[0-9a-f]{64}', channel['token'])):
                raise ValueError('Invalid bench harness launch channel')
            with self._lock:
                if self._closing:
                    raise ValueError('Configurator is stopping')
                process = _spawn(directory, ['--' + kind + '-dir', str(directory)], channel)
            return {'ok':True, 'pid':process.pid}
        if method == 'start':
            return self.start(params)
        if method.startswith('suite_'):
            return self._suite_call(method,params)
        run_id = params.get('run_id')
        row = self._row(run_id)
        if method in ('tool', 'tools'):
            with self._lock:
                operation_lock = self._operations.setdefault(run_id,threading.RLock())
            with operation_lock:
                if method == 'tools':
                    return self._tool(method,params,self._row(run_id))
                with self._active_operation(run_id):
                    return self._tool(method, params, self._row(run_id))
        if method == 'status':
            with self._lock:
                # Cleanup updates the durable outcome and worker ownership under
                # this lock. Read both together before a suite may settle a child.
                row = self._row(run_id)
                stopping = row['status'] in TERMINAL and (run_id in self._workers or run_id in self._inflight)
                out = {'ok': True, **{k: row[k] for k in ('status', 'stage', 'reason', 'created', 'updated', 'outcome_category')},
                       'run_id': run_id, 'uncertain_effect': bool(row['uncertain']), 'stopping': stopping}
            with self._db() as db:
                final = _final_verdict(db, run_id)
                accepted = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM handoffs WHERE run_id=? ORDER BY created', (run_id,))]
            # The final verdict and the latest notes of each stage; neither is an evaluator stage verdict.
            return {**out, 'equivalence': final['equivalence'] if final else None, 'stage_notes': stage_notes(accepted)}
        if method == 'events':
            with self._db() as db:
                events = db.execute('SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq LIMIT 500',
                                    (run_id, int(params.get('after', 0)))).fetchall()
            return {'ok': True, 'run_id': run_id,
                    'events': [{'id': e['seq'], 'kind': e['kind'], 'data': json.loads(e['payload']), 'time': e['created']} for e in events],
                    'cursor': events[-1]['seq'] if events else int(params.get('after', 0))}
        if method == 'result':
            out = self.call('status', {'run_id': run_id})
            spec=json.loads(row['spec'])
            config=spec['executor_config']
            case=spec.get('case')
            out.update(case=case.get('id') if isinstance(case,dict) else case,
                scoped_tool_approval=spec.get('scoped_tool_approval'),
                budget_seconds=spec['budget_seconds'],limits={'max_model_repairs':spec.get('max_revisions',2)},
                agent={'runtime':spec['executor'],'model':config.get('model'),
                    'provider':config.get('provider') or ('openai' if spec['executor']=='codex' else None),
                    'version':config.get('version'),'settings':{k:config[k] for k in ('reasoning_effort','max_turns') if k in config}})
            if spec.get('_snapshot_sha256'):
                out['snapshot_sha256'] = spec['_snapshot_sha256']
                out['case_pin'] = spec['_case_pin']
            with self._db() as db:
                for table, key in (('reports', 'worker_reports'), ('handoffs', 'accepted_handoffs'), ('verdicts', 'evaluator_verdicts')):
                    # The final verdict is `equivalence`, apart from evaluator stage verdicts.
                    out[key] = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM ' + table + ' WHERE run_id=?' +
                                (" AND stage!='finalize'" if table == 'verdicts' else '') + ' ORDER BY created', (run_id,))]
                recoveries = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='emulator.recovered' ORDER BY seq",(run_id,)).fetchall()
                out['emulator_recoveries'] = [json.loads(r['payload']) for r in recoveries]
                out['emulator_recovery_count'] = len(recoveries)
                saved = db.execute('SELECT payload FROM progress WHERE run_id=?',(run_id,)).fetchone()
                out['progress'] = {key: value for key, value in json.loads(saved['payload']).items()
                    if key != 'maintenance_cycles'} if saved else None
            if spec.get('_case_pin',{}).get('manifest',{}).get('schema') == 'benchmark-case/2':
                from .benchmark_support.evidence import public_v2_report
                state_path = self.home/'runs'/run_id/'benchmark/state.json'
                state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else {}
                summary = public_v2_report(state)
                out['benchmark_summary'] = {key:summary[key] for key in
                    ('accepted_gates','evaluations','final_evaluation')}
            return out
        if method == 'cancel':
            with self._lock:
                with self._db() as db:
                    # A run completed from its final verdict, or completed awaiting the independent evaluation after it,
                    # keeps that status; there is nothing left to cancel.
                    saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
                    awaiting = (db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()['status'] == 'completed'
                                and saved is not None and json.loads(saved['payload']).get('equivalence') == 'pending')
                    requested = 0 if awaiting else db.execute("UPDATE runs SET cancelled=1 WHERE id=? AND NOT (status='completed' "
                                           'AND EXISTS(SELECT 1 FROM verdicts WHERE id=?))', (run_id, run_id + FINAL)).rowcount
                if not requested:
                    return self.call('status', {'run_id': run_id})
                worker = self._workers.get(run_id)
                if worker:
                    worker[0].set()
                wait_for_worker = worker is not None and run_id not in self._inflight
                self._state(run_id, 'cancelled', 'Cancellation requested; retain outstanding-effect uncertainty')
            # Worker cleanup acquires _lock. Never join while holding it, and
            # keep cancellation responsive when a device/tool call is in flight.
            if wait_for_worker:
                worker[1].join(timeout=10)
            return self.call('status', {'run_id': run_id})
        if method == 'respond':
            observation = params.get('observation')
            message = params.get('message') or (_json(observation) if isinstance(observation,dict) and observation else None)
            resolution = params.get('effect_resolution') or (observation.get('effect_resolution') if isinstance(observation,dict) else None)
            if not isinstance(message,str) or not message.strip():
                raise ValueError('respond requires an operator message')
            if row['status'] not in TERMINAL or row['status'] == 'completed':
                raise ValueError('Respond only while the run is stopped')
            with self._lock:
                if resolution is not None and (run_id in self._inflight or run_id in self._workers):
                    raise ValueError('Previous worker or operation is still stopping; reconcile after it finishes')
            with self._db() as db:
                if resolution is not None:
                    if resolution != 'confirmed_safe':
                        raise ValueError('effect_resolution must be confirmed_safe after checking the device')
                    db.execute('UPDATE runs SET uncertain=0 WHERE id=?',(run_id,))
                    db.execute('DELETE FROM pending_effects WHERE run_id=?',(run_id,))
                self._event(run_id,'operator.response',{'message':message,'observation':observation,'effect_resolution':resolution},db)
            return self.call('status', {'run_id': run_id})
        if method == 'attach_device':
            return self._attach_device(run_id, params)
        if method == 'record_evaluation':
            return self._record_evaluation(run_id, params)
        if method == 'resume':
            self._resume_device(run_id)
            with self._lock:
                row=self._row(run_id)
                if self._closing:
                    raise ValueError('Configurator is stopping')
                if run_id in self._workers or run_id in self._inflight:
                    raise ValueError('Previous worker is still stopping')
                if row['status'] not in ('blocked','failed','cancelled'):
                    raise ValueError('Only a stopped incomplete run can resume')
                with self._db() as db:
                    if _has_terminal_final_failure(db, run_id):
                        raise ValueError('Frozen final evaluation failure is terminal; start a new trial')
                if row['uncertain']:
                    raise ValueError('Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation')
                with self._db() as db:
                    final = _final_verdict(db, run_id)
                if final is not None:
                    # The recorded verdict is never graded again, and completing from it runs no stage and touches no
                    # device: no budget, binding claim or worker. The run's own lease is released as a finished run's is.
                    with self._db() as db:
                        db.execute('DELETE FROM bindings WHERE run_id=?', (run_id,))
                        db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'", (run_id,))
                        self._event(run_id, 'run.resumed', {'final_verdict': True}, db)
                    self._state(run_id, 'completed', _verdict_reason(final['equivalence']), from_verdict=True)
                    return self.call('status', {'run_id': run_id})
                progress = self._workflow_progress(run_id)
                original_spec=json.loads(row['spec'])
                spec = dict(original_spec)
                # A run stopped after fresh reuse only finalizes: a generic run computes its verdict from the
                # configurator's records and a benchmark run records that its driver awaits the grade. Neither runs a
                # stage or contacts a device, so neither the budget nor the device lease applies.
                finalizing = progress.get('next_stage') in ('finalize', None)
                try:
                    with _integrity_checked():
                        self._verify_snapshot(spec, self.home / 'runs' / run_id)
                        # Accepted artifacts, notes.json and the recorded inline notes are all re-hashed.
                        for handoff in self.call('result',{'run_id':run_id})['accepted_handoffs']:
                            self._verify_handoff(handoff)
                except IntegrityError as error:
                    self._state(run_id, 'blocked', str(error), outcome_category='integrity')
                    raise
                budget_change=None
                if params.get('budget_seconds') is not None:
                    budget=params['budget_seconds']
                    reason=params.get('budget_reason')
                    if type(budget) not in (int,float) or not 0<budget<=7*86400 or not math.isfinite(budget):
                        raise ValueError('budget_seconds must be finite, positive and no more than seven days')
                    if not isinstance(reason,str) or not reason.strip():
                        raise ValueError('An explicit budget_reason is required when setting a resume budget')
                    if budget<spec['budget_seconds']:
                        raise ValueError('Resume budget_seconds cannot decrease the saved total budget')
                    if budget>spec['budget_seconds']:
                        budget_change={'old_budget_seconds':spec['budget_seconds'],'new_budget_seconds':budget,
                            'extension_seconds':budget-spec['budget_seconds'],'reason':reason.strip(),
                            'old_deadline':row['created']+spec['budget_seconds'],'new_deadline':row['created']+budget}
                        spec['budget_seconds']=budget
                if params.get('scoped_tool_approval') is not None:
                    spec['scoped_tool_approval']=params['scoped_tool_approval']
                    validate_scoped_approval(spec)
                if not finalizing and time.time() >= row['created'] + spec['budget_seconds']:
                    raise ValueError('Run budget exhausted; supply an explicitly approved larger budget or start a new run')
                with self._db() as db:
                    if not finalizing:
                        self._claim_binding(spec.get('binding'),run_id,db)
                        # A provided device is leased again under the binding the run's durable record holds now.
                        self._claim_binding((progress.get('device') or {}).get('binding') if spec.get('device') else None,
                                            run_id, db)
                    if spec!=original_spec:
                        db.execute('UPDATE runs SET spec=? WHERE id=?',(_json(spec),run_id))
                    if budget_change:
                        self._event(run_id,'run.budget_extended',budget_change,db)
                    if spec.get('scoped_tool_approval')!=original_spec.get('scoped_tool_approval'):
                        self._event(run_id,'run.authorization',{'scoped_tool_approval':spec['scoped_tool_approval'],
                            'scope':'Assigned tools only; saved binding and effect grants apply'},db)
                    db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'",(run_id,))
                    db.execute("UPDATE runs SET cancelled=0,status='queued',outcome_category='unknown',reason=NULL,updated=? WHERE id=?",(time.time(),run_id))
                    self._event(run_id,'run.resumed',{},db)
                self._spawn(run_id)
            return self.call('status',{'run_id':run_id})
        raise ValueError('Unknown method: ' + str(method))

    def _configured_executors(self):
        path = self.home / 'config.json'
        return json.loads(path.read_text(encoding='utf-8')).get('executors', {}) if path.is_file() else {}

    def suite_start(self,params):
        """Freeze and store a suite for the bench harness that drives it, attached by the sha256 of its token. Nothing is
        scheduled here: the harness reserves, starts, attaches and settles each slot."""
        from .benchmark_support.suites import normalize_manifest,freeze_suite,refuse_secrets
        from .benchmark_support.snapshots import execution_provenance
        manifest=normalize_manifest(params.get('manifest'))
        executor=configured_executor(params.get('executor'),self.home)
        options=params.get('options',{})
        refuse_secrets(options)
        token,pid,lock=_harness_identity(params,'A suite is started and driven by its bench harness: `benchmark suite start`')
        request={'manifest':manifest,'executor':executor,'options':options}
        with self._lock:
            saved=self._suites.find_request(params.get('request_id'),request)
            if saved is None:
                if self._closing:raise ValueError('Configurator is stopping')
                config={**self._configured_executors().get(executor,{}),'runtime':executor}
                frozen=freeze_suite(manifest,executor,config,options,execution_provenance(config),harness_token_sha256=token)
                saved=self._suites.create(frozen,params.get('request_id'),harness_pid=pid,harness_lock=lock)
                if not saved['duplicate']:
                    self._suites.set_state(saved['suite_id'],'running')
                    return {'ok':True,'suite_id':saved['suite_id'],'duplicate':False,'attached':True}
            # A repeated request. Its harness, sending it again after a lost reply, is told it still drives the suite.
            return {'ok':True,'suite_id':saved['suite_id'],'duplicate':True,'attached':_attached_to(saved,token)}

    def _suite_lock(self, suite_id):
        with self._lock:
            return self._suite_locks.setdefault(suite_id, threading.RLock())

    @staticmethod
    def _harness_alive(suite):
        """Whether the bench harness attached to the suite is still running: by the lock it holds for as long as it runs,
        which ends with it (a process id may name another process once its harness ended); where that cannot be told,
        by its process id. An attached harness with neither is taken to have ended."""
        harness = suite['harness']
        if not harness['attached']:
            return False
        if harness.get('lock'):
            from .client import process_lock_held
            held = process_lock_held(harness['lock'])
            if held is not None:
                return held
        if type(harness['pid']) is not int or harness['pid'] <= 0:
            return False
        from .client import _wait_for_exit
        try:
            return not _wait_for_exit(harness['pid'], 0)
        except OSError:
            return True

    def _suite(self, suite_id):
        """The suite, with a running suite whose harness ended without letting go of it blocked for an explicit resume."""
        from .benchmark_support.suites import harness_driven
        suite = self._suites.get(suite_id)
        if (harness_driven(suite['frozen']) and suite['status'] in ('queued','running') and suite['harness']['attached']
                and not self._harness_alive(suite)):
            with self._suite_lock(suite_id):
                suite = self._suites.get(suite_id)
                if suite['status'] in ('queued','running') and suite['harness']['attached'] and not self._harness_alive(suite):
                    self._suites.set_state(suite_id,'blocked','The bench harness driving the suite ended; resume it through its harness')
                    self._suites.detach_harness(suite_id)
                    suite = self._suites.get(suite_id)
        return suite

    def _suite_driver(self, suite_id, params):
        """The suite, for the bench harness that drives it: only a suite frozen for a harness, and only with the token
        whose sha256 the suite holds."""
        from .benchmark_support.suites import harness_driven
        suite = self._suite(suite_id)
        if not harness_driven(suite['frozen']):
            raise ValueError(OLD_SUITE)
        token, held = params.get('token'), suite['harness']['token_sha256']
        if not isinstance(token, str) or not isinstance(held, str) or \
                not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), held):
            raise ValueError('Only the bench harness driving the suite may schedule it (harness token)')
        return suite

    @staticmethod
    def _slot(suite, ordinal):
        slot = next((t for t in suite['trials'] if type(ordinal) is int and t['ordinal'] == ordinal), None)
        if slot is None:
            raise ValueError('Unknown or stale suite slot')
        return slot

    def _child_evaluation(self, run_id):
        """A suite child's evaluation as the configurator holds it: (its final equivalence or None, the reason of its
        latest unscored evaluation while it still awaits its grade, or None), never the child's own say."""
        with self._db() as db:
            final = _final_verdict(db, run_id)
            unscored = None if final else db.execute("SELECT payload FROM events WHERE run_id=? AND kind='evaluation.unscored' "
                                                     'ORDER BY seq DESC LIMIT 1', (run_id,)).fetchone()
        reasons = [d.get('reason') for d in json.loads(unscored['payload']).get('diff') or [] if isinstance(d, dict)] if unscored else []
        return (final['equivalence'] if final else None,
                (str(reasons[0]) if reasons else 'unscored') if unscored else None)

    def _child_mismatch(self, suite, slot, run_id):
        """Why the run is not the frozen slot's child, from the request the run recorded as submitted (see
        `child_identity_differs`), or None when it is."""
        from .benchmark_support.suites import child_identity_differs
        return child_identity_differs(suite['frozen'], slot, json.loads(self._row(run_id)['spec']).get('_submitted_intent'))

    def _cancel_live(self, run_id):
        """Cancel a suite child that still runs; one already stopped keeps its own stop and reason."""
        if run_id and self.call('status',{'run_id':run_id})['status'] in ('queued','running'):
            self.call('cancel',{'run_id':run_id})

    def _suite_reserve(self, suite_id, params):
        """The slot the harness works on next: the active one, or the next pending slot, reserved. None once the suite
        is cancelled (a child of its that still runs, however it came to run, is cancelled too), stopped, out of budget
        (it blocks and its active child is cancelled) or complete. A slot whose child exists but was never attached is
        attached here, from the child the configurator finds by the slot's request, and only if that child's request is
        the frozen slot's own: another blocks the suite and is cancelled."""
        from .benchmark_support.suites import child_request
        expired = live = None
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            trial = None
            if suite['cancelled']:
                active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
                live = active and (active['run_id'] or (self.find_request(active['child_request_id']) or {}).get('run_id'))
            elif suite['status'] == 'running':
                expired = self._suite_expired(suite)
                if expired is None:
                    trial = self._suites.reserve(suite_id)
                    if trial is None:
                        self._suites.set_state(suite_id,'completed')
                    elif trial['run_id'] is None:
                        found = self.find_request(trial['child_request_id'])
                        mismatch = found and self._child_mismatch(suite, trial, found['run_id'])
                        if mismatch:
                            self._suites.set_state(suite_id,'blocked',mismatch + TAKEN)
                            trial, live = None, found['run_id']
                        elif found:
                            self._suites.attach(suite_id,trial['ordinal'],trial['child_request_id'],found['run_id'])
                            trial['run_id'] = found['run_id']
            status = self._suites.get(suite_id)['status']
        if expired:
            # Cancellation can join a worker; never hold the suite lock meanwhile.
            self.call('cancel',{'run_id':expired})
        if live:
            self._cancel_live(live)
        if trial is None:
            return {'ok':True,'suite_id':suite_id,'status':status,'slot':None}
        return {'ok':True,'suite_id':suite_id,'status':status,
                'slot':{key:trial[key] for key in ('ordinal','trial_key','case','child_request_id','run_id')},
                'request':child_request(suite['frozen'],trial)}

    def _suite_expired(self, suite):
        """Block a running suite whose wall-clock budget is spent. Returns None while it is not spent; otherwise the active
        child to cancel ('' if there is none), as the suite's budget ends its active child whatever state that child is in."""
        if time.time() < suite['created'] + suite['budget_seconds']:
            return None
        active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
        found = active and (active['run_id'] or (self.find_request(active['child_request_id']) or {}).get('run_id'))
        self._suites.set_state(suite['suite_id'],'blocked','Suite wall-clock budget exhausted')
        return found or ''

    def _suite_attach(self, suite_id, params):
        """Attach the child the harness started for a slot: only the run the configurator itself finds by that slot's
        request, and only if that run's request is the frozen slot's own (another blocks the suite and is cancelled). A
        child attached to a suite that was cancelled or stopped meanwhile is cancelled. Only a reserved slot takes a
        child: a run found under a pending slot's request would run beside the active one, out of order."""
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            if slot['status'] not in ('launching','running'):
                raise ValueError('The suite slot is not reserved')
            found = self.find_request(slot['child_request_id'])
            if not found or found['run_id'] != params.get('run_id'):
                raise ValueError('The run is not the child of this suite slot')
            stopped = suite['cancelled'] or suite['status'] != 'running'
            mismatch = self._child_mismatch(suite, slot, found['run_id'])
            if mismatch:
                mismatch += TAKEN
                if not stopped:
                    self._suites.set_state(suite_id,'blocked',mismatch)
            else:
                self._suites.attach(suite_id,slot['ordinal'],slot['child_request_id'],found['run_id'])
        if mismatch or stopped:
            # Cancellation can join a worker; never hold the suite lock meanwhile.
            self._cancel_live(found['run_id'])
        if mismatch:
            raise ValueError(mismatch)
        return {'ok':True,'suite_id':suite_id,'run_id':found['run_id'],**({'cancelled':True} if stopped else {})}

    def _suite_start_child(self, suite_id, params):
        """Start a slot's child for its harness and attach it, in one step under the suite lock: only while the suite
        runs, so a suite cancelled or stopped as its harness provided the device starts no child; only within the suite's
        wall-clock budget, so a budget spent meanwhile blocks the suite, as on every reservation, before anything starts;
        and only from the frozen slot's own request plus what the harness adds (see `child_identity_differs`). A request
        that differs blocks the suite before anything starts. The same start sent again after a lost reply is answered
        with the child it started, whatever the suite did since (cancelled, blocked, out of budget), and starts nothing:
        that child runs on the device of the harness that sent it, which must not stop that device under it."""
        from .benchmark_support.suites import child_identity_differs
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            mismatch = child_identity_differs(suite['frozen'], slot, params.get('request'))
            started = None if mismatch else self._started_from(slot, params.get('request'))
            if started:
                if slot['run_id'] is None and slot['status'] in ('launching','running'):
                    self._suites.attach(suite_id,slot['ordinal'],slot['child_request_id'],started)
                return {'ok':True,'run_id':started,'duplicate':True,'suite_id':suite_id}
            if suite['cancelled'] or suite['status'] != 'running':
                raise ValueError('The suite is ' + suite['status'] + '; no child starts for it')
            if slot['status'] not in ('launching','running'):
                raise ValueError('The suite slot is not reserved')
            expired = self._suite_expired(suite)
            if expired is None:
                if mismatch:
                    self._suites.set_state(suite_id,'blocked',mismatch)
                    raise ValueError(mismatch)
                started = self.start(params['request'])
                self._suites.attach(suite_id,slot['ordinal'],slot['child_request_id'],started['run_id'])
                return {**started,'suite_id':suite_id}
        self._suite_spent(expired)

    def _started_from(self, slot, request):
        """The run id of the slot's child when that child was started from exactly `request`, as its submitted intent
        records it (a harness's start sent again: the same device, admission and evaluation token hash), else None. The
        caller has checked `request` against the frozen slot."""
        if not isinstance(request, dict):
            return None
        with self._db() as db:
            found = db.execute('SELECT id,spec FROM runs WHERE request_id=?', (slot['child_request_id'],)).fetchone()
        if found is None or (slot['run_id'] is not None and slot['run_id'] != found['id']):
            return None
        intent = json.loads(found['spec']).get('_submitted_intent')
        return found['id'] if intent is not None and _json(intent) == _json(request) else None

    def _suite_spent(self, expired):
        """Refuse a child's start or resume once the suite's budget is spent; the active child `_suite_expired` names,
        however it came to exist, is cancelled outside the suite lock, since cancellation can join a worker."""
        if expired:
            self.call('cancel',{'run_id':expired})
        raise ValueError('Suite wall-clock budget exhausted')

    def _suite_resume_child(self, suite_id, params):
        """Resume a slot's stopped child for its harness, once the harness attached a device anew, in one step under the
        suite lock: only while the suite runs, so a suite cancelled or stopped as its harness provided the device resumes
        nothing; only within the suite's wall-clock budget (a budget spent meanwhile blocks the suite and ends the child,
        as on every reservation); and only the slot's attached child, whose request is the frozen slot's own. An approved
        child budget extension (`budget_seconds`, `budget_reason`) reaches that child's resume. The same resume sent
        again after a lost reply, for the device session the harness attached (`session_id`), finds the child already
        resumed on that device and resumes nothing."""
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            if suite['cancelled'] or suite['status'] != 'running':
                raise ValueError('The suite is ' + suite['status'] + '; its child does not resume')
            if slot['status'] != 'running' or not slot['run_id'] or slot['run_id'] != params.get('run_id'):
                raise ValueError('The run is not the attached child of this suite slot')
            expired = self._suite_expired(suite)
            if expired is None:
                mismatch = self._child_mismatch(suite, slot, slot['run_id'])
                if mismatch:
                    raise ValueError(mismatch + TAKEN)
                if self._resumed_on(slot['run_id'], params.get('session_id')):
                    # Its harness's resume again, the first reply lost: the child was resumed on the device that harness
                    # attached, whatever it has done since (an operator may have cancelled it), and is not resumed again.
                    return {**self.call('status',{'run_id':slot['run_id']}),'duplicate':True}
                return self.call('resume',{'run_id':slot['run_id'],**{key:params[key] for key in ('budget_seconds','budget_reason')
                                                                      if params.get(key) is not None}})
        self._suite_spent(expired)

    def _resumed_on(self, run_id, session_id):
        """Whether the run was resumed since the device of `session_id` was attached to it, the device attached last."""
        if not isinstance(session_id, str):
            return False
        with self._db() as db:
            attached = db.execute("SELECT seq,payload FROM events WHERE run_id=? AND kind='device.attached' "
                                  'ORDER BY seq DESC LIMIT 1', (run_id,)).fetchone()
            return (attached is not None and json.loads(attached['payload']).get('session_id') == session_id
                    and db.execute("SELECT 1 FROM events WHERE run_id=? AND kind='run.resumed' AND seq>? LIMIT 1",
                                   (run_id, attached['seq'])).fetchone() is not None)

    def _suite_settle(self, suite_id, params):
        """Settle a slot from what the configurator reads itself, the child's status and its recorded evaluation; the
        harness names only the slot. A child that may not advance blocks a running suite."""
        from .benchmark_support.suites import child_disposition
        expired = None
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            slot = self._slot(suite, params.get('ordinal'))
            if not slot['run_id']:
                raise ValueError('Settlement does not match attached child')
            if slot['status'] != 'finished' and suite['status'] == 'running' and not suite['cancelled']:
                # The suite's budget is checked before its child's outcome, as on every reservation.
                expired = self._suite_expired(suite)
            if expired is not None:
                decision = 'block'
            elif slot['status'] == 'finished':
                decision = 'advance'
            else:
                # Every child is graded after its run: the configurator reads its recorded evaluation itself.
                equivalence, unscored = self._child_evaluation(slot['run_id'])
                observed = {**self.call('status',{'run_id':slot['run_id']}),'equivalence':equivalence}
                decision = child_disposition(observed)
                mismatch = self._child_mismatch(suite, slot, slot['run_id']) if decision != 'wait' else None
                if mismatch:
                    decision = 'block'
                if decision == 'advance':
                    self._suites.settle(suite_id,slot['ordinal'],observed)
                elif decision == 'block' and suite['status'] == 'running' and not suite['cancelled']:
                    reason = (mismatch + TAKEN if mismatch else
                              'Trial ' + slot['trial_key'] + ' is unscored: ' + unscored if unscored else
                              'Trial ' + slot['trial_key'] + ' awaits its evaluation'
                              if observed.get('status') == 'completed' else observed.get('reason'))
                    self._suites.set_state(suite_id,'blocked',reason)
            status = self._suites.get(suite_id)['status']
        if expired:
            self.call('cancel',{'run_id':expired})
        return {'ok':True,'suite_id':suite_id,'decision':decision,'status':status}

    def _suite_detach(self, suite_id, params):
        """The harness lets go of the suite; one that stops while the suite still runs leaves it blocked for a resume."""
        reason = params.get('reason')
        with self._suite_lock(suite_id):
            suite = self._suite_driver(suite_id, params)
            if suite['status'] in ('queued','running') and not suite['cancelled']:
                self._suites.set_state(suite_id,'blocked','The bench harness stopped driving the suite'
                                       + (': ' + reason if isinstance(reason, str) and reason.strip() else ''))
            self._suites.detach_harness(suite_id)
        return self._suite_call('suite_status',{'suite_id':suite_id})

    def find_request(self, request_id):
        """Reconcile an already created child without creating or resuming work."""
        with self._db() as db:
            row = db.execute('SELECT id FROM runs WHERE request_id=?',(request_id,)).fetchone()
        return self.call('status',{'run_id':row['id']}) if row else None

    def _suite_cancel(self, suite_id):
        """Cancel the suite and its active child, then wait a while for its harness to let go, unless the child is still
        stopping (an operation in flight)."""
        deadline = time.monotonic()+10
        with self._suite_lock(suite_id):
            self._suites.cancel(suite_id)
            suite = self._suites.get(suite_id)
            active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
            child = active['run_id'] if active else None
            if active and not child:
                found = self.find_request(active['child_request_id'])
                if found:
                    # Whatever runs under the slot's request is cancelled; only the slot's frozen child is attached.
                    child = found['run_id']
                    if not self._child_mismatch(suite, active, child):
                        self._suites.attach(suite_id,active['ordinal'],active['child_request_id'],child)
        if child:
            self.call('cancel',{'run_id':child})
        while True:
            status = self._suite_call('suite_status',{'suite_id':suite_id})
            if (not status['stopping'] or time.monotonic() >= deadline
                    or (child and self.call('status',{'run_id':child})['stopping'])):
                return status
            time.sleep(.05)

    def _verify_suite(self, suite):
        from .benchmark_support.registry import pin_case
        from .benchmark_support.snapshots import execution_provenance
        frozen = suite['frozen']
        for pin in frozen['entry_pins']:
            if pin_case(pin['case_id'],pin['scenario_id'],pin['case_seed']) != pin:
                raise ValueError('Frozen suite case pin changed')
        if execution_provenance(frozen['executor_config']) != frozen['provenance']:
            raise ValueError('Frozen suite implementation or execution environment changed')
        for trial in suite['trials']:
            found = self.find_request(trial['child_request_id'])
            if trial['run_id'] and (not found or trial['run_id'] != found['run_id']):
                raise ValueError('Frozen suite child identity mismatch')
            if found:
                spec = json.loads(self._row(found['run_id'])['spec'])
                if spec.get('_case_pin') != frozen['entry_pins'][trial['entry_index']]:
                    raise ValueError('Frozen suite child pin mismatch')
                mismatch = self._child_mismatch(suite, trial, found['run_id'])
                if mismatch:
                    raise ValueError(mismatch + TAKEN)
                self._verify_snapshot(spec,self.home/'runs'/found['run_id'])

    def _suite_resume(self, suite_id, params):
        """Resume a stopped suite for a newly launched bench harness, which takes over its scheduling with a token of its
        own. A suite frozen before the harness drove suites is read-only. A stopped child is left to the harness, which
        first attaches a device it provides anew and passes an approved child budget extension itself; a child recorded
        with the earlier protocol's terminal final failure is settled as it stands."""
        from .benchmark_support.suites import harness_driven
        with self._suite_lock(suite_id):
            suite = self._suite(suite_id)
            if not harness_driven(suite['frozen']):
                raise ValueError(OLD_SUITE)
            if suite['status'] in ('queued','running') and _attached_to(suite, params.get('harness_token_sha256')):
                # Its harness's resume again, the first reply lost: that harness still drives the suite.
                return {**self._suite_call('suite_status',{'suite_id':suite_id}),'attached':True}
            if suite['status'] not in ('blocked','failed','cancelled'):
                raise ValueError('Only a stopped incomplete suite can resume')
            if self._suite_call('suite_status',{'suite_id':suite_id})['stopping']:
                raise ValueError('Previous suite work is still stopping')
            self._verify_suite(suite)
            active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
            found = self.find_request(active['child_request_id']) if active else None
            if found and found['uncertain_effect']:
                # Nothing resumes, and nothing about the suite changes, while its child's effect is unreconciled.
                raise ValueError('Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation')
            token, pid, lock = _harness_identity(params,'A suite resumes through its bench harness: `benchmark suite resume`')
            if params.get('suite_budget_seconds') is not None:
                self._suites.extend_budget(suite_id,params['suite_budget_seconds'],params.get('budget_reason'))
                suite = self._suites.get(suite_id)
            if time.time() >= suite['created'] + suite['budget_seconds']:
                raise ValueError('Suite budget exhausted; supply an explicitly approved larger total budget')
            if found:
                self._suites.attach(suite_id,active['ordinal'],active['child_request_id'],found['run_id'])
                with self._db() as db:
                    terminal_final = _has_terminal_final_failure(db,found['run_id'])
                if (terminal_final and found['status'] in ('failed','blocked')
                        and not found['stopping'] and not found['uncertain_effect']):
                    self._state(found['run_id'],'failed','Frozen final evaluation failed',outcome_category='model')
                    self._suites.settle(suite_id,active['ordinal'],
                        self.call('status',{'run_id':found['run_id']}),terminal_final=True)
            self._suites.attach_harness(suite_id,token,pid,lock)
            self._suites.set_state(suite_id,'running')
        return {**self._suite_call('suite_status',{'suite_id':suite_id}),'attached':True}

    def _suite_call(self,method,params):
        if method=='suite_start':return self.suite_start(params)
        suite_id=params.get('suite_id')
        suite=self._suite(suite_id)
        if method=='suite_cancel':return self._suite_cancel(suite_id)
        if method=='suite_resume':return self._suite_resume(suite_id,params)
        if method=='suite_reserve':return self._suite_reserve(suite_id,params)
        if method=='suite_attach':return self._suite_attach(suite_id,params)
        if method=='suite_start_child':return self._suite_start_child(suite_id,params)
        if method=='suite_resume_child':return self._suite_resume_child(suite_id,params)
        if method=='suite_settle':return self._suite_settle(suite_id,params)
        if method=='suite_detach':return self._suite_detach(suite_id,params)
        if method=='suite_events':return {'ok':True,**self._suites.events(suite_id,params.get('after',0))}
        if method not in ('suite_status','suite_result'):
            raise ValueError('Unknown suite method: '+str(method))
        # The slots and the harness's liveness are read together under the suite's lock, which settlement and detach
        # hold, and the harness lets go of its lock file only after it detached: a suite no longer read as stopping
        # carries the slots its harness left, never a count from before its last settlement.
        with self._suite_lock(suite_id):
            suite=self._suites.get(suite_id)
            active=next((trial for trial in suite['trials'] if trial['status'] in ('launching','running')),None)
            child = self.call('status',{'run_id':active['run_id']}) if active and active['run_id'] else None
            status={'ok':True,'suite_id':suite_id,**{key:suite[key] for key in ('status','created','updated')},
                'reason':None if suite['reason'] is None else 'Explicit suite resolution required',
                'active_child_id':active['run_id'] if active else None,
                'planned':len(suite['trials']),'completed':sum(trial['status']=='finished' for trial in suite['trials']),
                # Equivalent drivers among the settled children, out of `planned`; none applies to a suite frozen before
                # the harness drove suites, whose children were graded inside their runs.
                'equivalent':sum(trial['status']=='finished' and (trial['result'] or {}).get('equivalent') is True
                                 for trial in suite['trials']) if _assistive(suite) else None,
                'stopping':(suite['status'] in TERMINAL and self._harness_alive(suite)) or bool(child and child['stopping'])}
            if method=='suite_status':return status
            page = self._suites.trials(suite_id,params.get('offset',0),params.get('limit',50))
        return self._suite_result(suite,status,page)

    def _suite_result(self,suite,status,page):
        """A page of the suite's slots as read with its status, each with its child's interventions and, while it is
        not settled, its child's current state."""
        from .reporting import _interventions
        from .benchmark_support.evidence import public_v2_report
        for trial in page['trials']:
            events, cursor = [], 0
            if trial['run_id']:
                while True:
                    batch = self.call('events',{'run_id':trial['run_id'],'after':cursor})
                    events.extend(batch['events'])
                    if batch['cursor'] == cursor or len(batch['events']) < 500:
                        break
                    cursor = batch['cursor']
            trial['interventions'] = public_v2_report({'interventions':_interventions(events)[0]})['interventions']
            if trial['run_id'] and trial['status'] != 'finished':
                current = self.call('status',{'run_id':trial['run_id']})
                trial['outcome_category'] = current['outcome_category']
                if current['status'] in ('blocked','failed','cancelled','completed'):
                    trial['status'] = current['status']
                equivalence, unscored = self._child_evaluation(trial['run_id'])
                if equivalence:
                    trial.update(equivalent=equivalence.get('equivalent'),equivalence_status=equivalence.get('status'))
                elif current['status'] == 'completed':
                    # Not settled: its grade is unscored, or still pending.
                    trial['equivalence_status'] = 'unscored' if unscored else 'pending'
        return {**status,**page,'experiment':self._suite_experiment(suite)}

    def _suite_experiment(self,suite):
        # Public provenance is an allowlist, never the private execution snapshot.
        from .benchmark_support.evidence import public_pin
        frozen=suite['frozen'];manifest=frozen['manifest'];provenance=frozen['provenance']
        config=frozen['executor_config']
        def scalars(value,keys):
            return {key:value[key] for key in keys if key in value and
                    (value[key] is None or type(value[key]) in (str,int,float,bool))}
        dimensions=scalars(config,('model','provider','version','reasoning_effort','max_turns'))
        dimensions.update(runtime=frozen['executor'],**scalars(provenance,('skills_revision','toolchain_revision')))
        policy={key:manifest[key] for key in ('child_budget_seconds','suite_budget_seconds')}
        snapshots=[];overrides=[]
        for trial in suite['trials']:
            spec=json.loads(self._row(trial['run_id'])['spec']) if trial['run_id'] else {}
            snapshots.append({'trial_key':trial['trial_key'],'snapshot_sha256':spec.get('_snapshot_sha256')})
            if spec and spec['budget_seconds']!=policy['child_budget_seconds']:
                overrides.append({'trial_key':trial['trial_key'],'budget_seconds':spec['budget_seconds']})
        return {'schema':'benchmark-suite-experiment/1',
            # Only a suite its bench harness drives, whose children are graded after their runs, names the assistive protocol.
            'comparison_identity':{**({'protocol':PROTOCOL} if _assistive(suite) else {}),
                'manifest':manifest,'manifest_sha256':frozen['manifest_sha256'],
                'entry_pins':[public_pin(pin) for pin in frozen['entry_pins']],
                'trials':frozen['trials'],'repetitions':manifest['repetitions'],
                'execution':frozen['entry_pins'][0]['execution'],
                'evidence_track':frozen['entry_pins'][0]['evidence_track'],'scope':frozen['scope'],
                'time_policy':[scalars(pin['time_policy'],('probe','package_calls','clock','limitation')) for pin in frozen['entry_pins']],
                'evaluator_revision':provenance['evaluator_revision'],
                'budgets':{'original':policy,'effective':{**policy,'suite_budget_seconds':suite['budget_seconds'],'child_budget_overrides':overrides}},
                'intervention_policy':{'scoped_tool_approval':frozen['options'].get('scoped_tool_approval'),
                    'max_model_repairs':2}},
            'dimensions':dimensions,'execution_snapshots':snapshots}

    def start(self, params):
        # Submitted intent is compared before reading mutable defaults or resources.
        params = json.loads(_json(params))
        with self._lock:
            if params.get('request_id'):
                with self._db() as db:
                    found = db.execute('SELECT id,spec FROM runs WHERE request_id=?', (params['request_id'],)).fetchone()
                if found:
                    saved = json.loads(found['spec'])
                    if '_submitted_intent' in saved:
                        if _json(saved['_submitted_intent']) != _json(params):
                            raise ValueError('request_id was already used with different inputs')
                        return {'ok': True, 'run_id': found['id'], 'duplicate': True}
            return self._start_new(params)

    def _start_new(self, params):
        goal = params.get('goal')
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError('start requires a nonempty goal')
        executor = configured_executor(params.get('executor'), self.home)
        if executor not in ('codex', 'goose'):
            raise ValueError('executor must be codex or goose')
        budget = params.get('budget_seconds', 10800)
        if type(budget) not in (int, float) or not 0 < budget <= 7 * 86400:
            raise ValueError('budget_seconds must be positive and no more than seven days')
        effects = params.get('effects', ['read'])
        if not isinstance(effects, list) or not set(effects) <= {'read', 'write', 'actuate', 'reset'}:
            raise ValueError('effects must contain only read, write, actuate, reset')
        config = {}
        if 'case_pin' in params:
            if not isinstance(params.get('executor_config'),dict):
                raise ValueError('Frozen case pin requires complete executor_config')
            if not params.get('case'):
                raise ValueError('Expected case pin requires a registered case')
        else:
            config = self._configured_executors().get(executor, {})
        config = {**config, **params.get('executor_config', {}), 'runtime': executor}
        spec = {**params, 'executor': executor, 'executor_config': config, 'budget_seconds': budget, 'effects': effects,
                '_submitted_intent': params}
        case=spec.get('case')
        option_bindings=[spec.get('case_options',{}).get('binding')]
        if isinstance(case,dict): option_bindings.append(case.get('options',{}).get('binding'))
        if spec.get('binding') and any(value and value!=spec['binding'] for value in option_bindings):
            raise ValueError('Operator and case options contain conflicting device bindings')
        validate_scoped_approval(spec)
        _refuse_secrets(spec)
        if 'device' in spec and not case:
            raise ValueError('A provided device descriptor belongs to an emulated benchmark case')
        encoded = _json(spec)
        device = note = None
        with self._lock:
            if self._closing:
                raise ValueError('Configurator is stopping')
            with self._db() as db:
                if params.get('request_id'):
                    found = db.execute('SELECT id,spec FROM runs WHERE request_id=?', (params['request_id'],)).fetchone()
                    if found:
                        if {k:v for k,v in json.loads(found['spec']).items() if k != '_submitted_intent'} != {k:v for k,v in spec.items() if k != '_submitted_intent'}:
                            raise ValueError('request_id was already used with different inputs')
                        return {'ok': True, 'run_id': found['id'], 'duplicate': True}
                run_id = 'run-' + uuid.uuid4().hex[:16]
                if case:
                    from .benchmark_support.registry import pin_case
                    from .benchmark_support.snapshots import snapshot_case, execution_provenance
                    options = case_options(spec)
                    pin = pin_case(case['id'] if isinstance(case, dict) else case,
                                   options.get('scenario_id'), options.get('case_seed', 0))
                    if 'case_pin' in params and _json(params['case_pin']) != _json(pin):
                        raise ValueError('Expected case pin differs from current case inputs')
                    if pin['manifest'].get('schema') == 'benchmark-case/2':
                        required_effects = set(pin['manifest']['default_effects']) - {'read'}
                        if required_effects - set(effects):
                            raise ValueError('Case evaluator requires the declared write/actuate effect grants before admission')
                        device = _provided_device(spec, pin)
                    elif 'device' in spec and pin.get('execution') == 'actual-agent-emulation':
                        # Legacy tq9 on the emulator its bench harness provides. Started without one, its device stages
                        # stop for the operator: the configurator never starts an emulator itself.
                        device = _provided_device(spec, pin)
                    elif pin.get('execution') == 'actual-agent-emulation':
                        note = NO_HARNESS_DEVICE
                    elif 'device' in spec:
                        raise ValueError('A provided device descriptor belongs to an emulated benchmark case')
                    elif 'evaluation_token_sha256' in spec and not (isinstance(spec['evaluation_token_sha256'], str) and
                            re.fullmatch('[0-9a-f]{64}', spec['evaluation_token_sha256'])):
                        raise ValueError('evaluation_token_sha256 must be the sha256 of the bench harness evaluation token')
                if not config.get('command'):
                    # Never fall back to whatever program of that name is on PATH: it may be something else entirely.
                    raise ValueError(f'{executor} is not configured; run `generative-driver configure {executor} <command>` '
                                     'with the runtime this configurator should launch')
                if case:
                    spec['_case_pin'] = pin
                    saved = snapshot_case(self.home / 'runs' / run_id, pin, execution_provenance(config))
                    spec['_snapshot_sha256'] = saved['snapshot_sha256']
                    spec['_execution_run_dir'] = str(self.home / 'runs' / run_id)
                    encoded = _json(spec)
                self._claim_binding(spec.get('binding'),run_id,db)
                if device:
                    self._claim_binding(device['binding'],run_id,db)
                now = time.time()
                db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                           (run_id, params.get('request_id'), encoded, 'queued', 'acquire', now, now))
                db.execute('INSERT INTO progress VALUES(?,?)',(run_id,_json({'revision':0,'next_stage':'acquire','repairs':0,
                    'feedback':None,'protocol':PROTOCOL,**({'device':device} if device else {})})))
                self._event(run_id, 'run.started', {'goal': goal, 'executor': executor, 'budget_seconds': budget,
                                                    **({'note': note} if note else {})}, db)
            self._spawn(run_id)
        return {'ok': True, 'run_id': run_id, 'duplicate': False, **({'note': note} if note else {})}

    def _workflow_progress(self, run_id, db=None):
        if db is None:
            with self._db() as own:
                return self._workflow_progress(run_id, own)
        saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
        progress = json.loads(saved['payload']) if saved else None
        if (not isinstance(progress, dict) or 'maintenance_cycles' in progress
                or not {'revision', 'next_stage', 'repairs', 'feedback'} <= progress.keys()
                or progress['next_stage'] not in (*STAGES, 'finalize', None) or progress.get('protocol', PROTOCOL) != PROTOCOL):
            raise ValueError('Unsupported saved workflow progress; start a new six-stage run')
        if 'protocol' not in progress:
            # Its quality gates and evaluator feedback are never replayed under the assistive protocol.
            raise ValueError('Run was created under the earlier gated protocol; start a new run')
        return progress

    def _spawn(self, run_id):
        cancel = threading.Event()
        thread = threading.Thread(target=self._run, args=(run_id, cancel), daemon=True)
        self._workers[run_id] = (cancel, thread)
        thread.start()

    def _run(self, run_id, cancel):
        spec = None
        run_dir = self.home / 'runs' / run_id
        try:
            row = self._row(run_id)
            spec = json.loads(row['spec'])
            accepted = self.call('result', {'run_id': run_id})['accepted_handoffs']
            progress = self._workflow_progress(run_id)
            revision = progress['revision']
            # After reuse the queue is empty and next_stage is 'finalize' until the final verdict is recorded.
            queue = list(STAGES[STAGES.index(progress['next_stage']):]) if progress['next_stage'] in STAGES else []
            completion = 'Fresh reuse accepted'
            while queue:
                stage = queue.pop(0)
                if cancel.is_set():
                    return
                remaining = row['created'] + spec['budget_seconds'] - time.time()
                if remaining <= 0:
                    self._state(run_id, 'blocked', 'Run exhausted its wall-clock budget', outcome_category='operator')
                    return
                attempt = uuid.uuid4().hex
                workspace = run_dir / 'work' / stage / attempt
                workspace.mkdir(parents=True, exist_ok=True)
                for prior in accepted:
                    self._verify_handoff(prior)
                current_spec = {**spec, '_revision':revision, '_feedback':progress.get('feedback'), '_device':progress.get('device')}
                with self._db() as db:
                    responses=db.execute("SELECT payload FROM events WHERE run_id=? AND kind='operator.response' ORDER BY seq",(run_id,)).fetchall()
                current_spec['_operator_observations']=[value.get('observation') or {'message':value['message']} for value in (json.loads(r['payload']) for r in responses)]
                with _integrity_checked():
                    prepared = self._prepare(current_spec, stage, run_dir, workspace, accepted)
                if prepared.get('blocked'):
                    # Preparation waits for the operator: a binding, a grant, a reference or the device the bench
                    # harness provides.
                    self._state(run_id,'blocked',prepared['blocked'],stage,outcome_category='operator')
                    return
                if prepared.get('skip'):
                    # No worker runs. A difference preparation found in the model (one the device binding cannot carry)
                    # is decided like any other device diff: it revises within the budget, then the run advances.
                    diff = prepared.get('device_diff')
                    outcome = (_outcome(stage, {}, {'device_diff': diff}, uncertain=bool(self._row(run_id)['uncertain']),
                                        repairs=progress['repairs'], max_repairs=int(spec.get('max_revisions', 2)))
                               if diff else Outcome('advance', prepared['skip']))
                    if outcome.kind == 'stop':
                        self._state(run_id, 'blocked', outcome.reason, stage, outcome_category=outcome.category)
                        return
                    if outcome.kind == 'revise':
                        progress.update(revision=revision+1, next_stage='interpret', repairs=progress['repairs']+1, feedback=outcome.diff)
                    else:
                        progress['next_stage'] = queue[0] if queue else 'finalize'
                    accepted.append(self._skip(run_id, stage, attempt, revision, run_dir, workspace.resolve(), progress,
                                               prepared['skip'], checks=prepared.get('checks', ()), device_diff=diff, outcome=outcome))
                    if outcome.kind == 'revise':
                        revision, queue = progress['revision'], list(STAGES[1:])
                    elif stage == STAGES[-1]:
                        completion = prepared['skip']
                    continue
                workspace = Path(prepared.get('work_dir', workspace)).resolve()
                workspace.mkdir(parents=True, exist_ok=True)
                input_hashes = {str(Path(p).resolve()): digest(p) for p in prepared.get('inputs', [])}
                assignment = {'id': attempt, 'stage': stage, 'workspace': str(workspace), 'inputs': input_hashes,
                              'allowed_tools': prepared.get('allowed_tools', []), 'report_required': prepared.get('report_required', True),
                              'binding': prepared.get('binding', spec.get('binding')), 'effects': spec['effects'], 'revision': revision}
                if spec.get('scoped_tool_approval')=='bound-device' and assignment['binding']!=spec['binding']:
                    raise SafetyError('Prepared stage changed the explicitly approved device binding')
                if prepared.get('collection'):
                    assignment['collection'] = prepared['collection']
                with self._db() as db:
                    self._claim_binding(assignment.get('binding'),run_id,db)
                    db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)',
                               (attempt, run_id, stage, 'active', _json(assignment), time.time()))
                    self._event(run_id, 'stage.assigned', assignment, db)
                self._state(run_id, 'running', stage=stage)
                prompt = prepared.get('prompt')
                if prompt is None:
                    prompt = ('Execute exactly the assigned stage. Do not start another stage. Return a JSON stage report. '
                              'Write report.json and include artifact paths and actual SHA-256 hashes. '
                              'Report status and checks honestly: pass, fail or not_run. Use needs_revision for a candidate '
                              'protocol, decoder, unit or capability discrepancy and blocked for missing host tools, operator '
                              'input or permission. Every report is kept as notes and the run continues; preserve partial '
                              'artifacts and observations.\n' + _json({
                                  'stage': stage, 'objective': prepared.get('objective', spec['goal']),
                                  'inputs': list(input_hashes), 'context': prepared.get('context', {}),
                                  'allowed_tools': assignment['allowed_tools'], 'effect_grants': spec['effects'],
                                  'report_schema': __import__('generative_driver.agents', fromlist=['REPORT_SCHEMA']).REPORT_SCHEMA}))
                gateway = {'command': sys.executable, 'args': ['-c',
                    'import sys; sys.path.insert(0,' + repr(str(Path(__file__).resolve().parents[1])) + '); from generative_driver.worker_tools import main; main()',
                    '--home',str(self.home),'--run',run_id,'--assignment',attempt]}
                result = execute(StageRequest(stage=stage, workspace=workspace, prompt=prompt,
                                budget_seconds=max(0.01, row['created'] + spec['budget_seconds'] - time.time()),
                                report_required=assignment['report_required'], allowed_tools=assignment['allowed_tools'],
                                gateway=gateway if assignment['allowed_tools'] else None,
                                approve_scoped_tools=spec.get('scoped_tool_approval') in ('emulator','bound-device')),
                                spec['executor_config'], cancel)
                record = {'assignment_id': attempt, 'stage': stage, 'revision':revision,'workspace': str(workspace), **result}
                with self._db() as db:
                    db.execute('INSERT INTO reports VALUES(?,?,?,?,?)', (attempt, run_id, stage, _json(record), time.time()))
                    self._event(run_id, 'worker.finished', {'stage':stage, 'runtime':result['runtime'], 'status':result['status'], 'usage':result.get('usage')}, db)
                if cancel.is_set():
                    return
                # The stage is observed and committed under the run's operation lock: a call of this worker still in the
                # gateway (it gave up waiting, or its process group was killed) finishes first, and one queued behind it
                # finds the assignment closed.
                with self._lock:
                    operation_lock = self._operations.setdefault(run_id, threading.RLock())
                operation_lock.acquire()
                report = result.get('report') if isinstance(result.get('report'), dict) else {}
                checked, recovery, settled = {}, None, False
                def abandon(reason):
                    # A verified replacement that did not commit with this stage's handoff: the write it replaced stays
                    # uncertain and is never re-executed, and the recovery that started is recorded as failed, once.
                    nonlocal settled
                    if recovery and not settled:
                        settled = True
                        self._event(run_id, 'emulator.recovery_failed', {'assignment_id': attempt, 'reason': reason})
                try:
                    if _runtime_stop(result) is None:
                        self._verify_inputs(input_hashes)
                        with _integrity_checked():
                            if self._row(run_id)['uncertain']:
                                from .device_recovery import reconcile
                                checked = reconcile(self, run_id, current_spec, assignment, run_dir, progress)
                                recovery = checked.get('recovery')
                                if recovery:
                                    # The verified replacement commits with this stage's handoff; until then the effect
                                    # stays uncertain, and every exit before that commit records the recovery as failed.
                                    checked = {**self._check(current_spec, stage, run_dir, workspace, report, accepted, assignment),
                                               'recovery': recovery}
                            else:
                                checked = self._check(current_spec, stage, run_dir, workspace, report, accepted, assignment)
                        # Stages are observed without truth: nothing a check returns is a grade, a stage verdict or a
                        # final. The bench harness grades the frozen driver once, after the run.
                        if cancel.is_set():
                            abandon('Recovery cancelled before durable reconciliation')
                            return
                        self._verify_inputs(input_hashes)
                    if recovery and time.time() >= row['created'] + spec['budget_seconds']:
                        abandon('Recovery cancelled before durable reconciliation: the run budget ended')
                        self._state(run_id, 'blocked', 'Emulator recovery unverified: the run budget ended', stage, outcome_category='safety')
                        return
                    outcome = _outcome(stage, result, checked, uncertain=bool(self._row(run_id)['uncertain']) and not recovery,
                                       repairs=progress['repairs'], max_repairs=int(spec.get('max_revisions',2)))
                    if outcome.kind == 'stop':
                        # A stop of the stage's own observation (integrity when bytes it observed changed) keeps its category.
                        abandon(outcome.reason)
                        self._state(run_id, result['status'] if _runtime_stop(result) else 'blocked', outcome.reason, stage,
                                    outcome_category=outcome.category)
                        return
                    with self._db() as db:
                        rows = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='tool.refused' ORDER BY seq", (run_id,)).fetchall()
                    refusals = [{'name': event.get('name'), 'reason': event.get('reason')} for event in (json.loads(r['payload']) for r in rows)
                                if event.get('assignment_id') == attempt and event.get('actor') == 'worker']
                    notes = _notes(result, checked, outcome, refusals=refusals, missing=prepared.get('input_notes', ()))
                    if outcome.kind == 'revise':
                        # The partial handoff, the new revision and the route to interpret commit together.
                        progress.update(revision=revision+1,next_stage='interpret',repairs=progress['repairs']+1,feedback=outcome.diff)
                    else:
                        progress['next_stage'] = queue[0] if queue else 'finalize'
                    handoff = self._accept(run_id, assignment, checked, run_dir, workspace, progress, notes,
                                           revise=outcome.reason if outcome.kind == 'revise' else None, recovery=recovery)
                    settled = True
                except Exception as error:
                    if not recovery:
                        raise
                    # Nothing of the recovery committed, whether the stage's observation or the commit failed: the write
                    # stays uncertain and is never re-executed, and the run stops for safety (integrity when a hash changed).
                    abandon(type(error).__name__ + ': ' + str(error))
                    if isinstance(error, IntegrityError):
                        raise
                    self._state(run_id, 'blocked', 'Emulator recovery unverified: ' + str(error), stage, outcome_category='safety')
                    return
                finally:
                    abandon('The stage ended before the emulator recovery committed')
                    operation_lock.release()
                accepted.append(handoff)
                if outcome.kind == 'revise':
                    revision, queue = progress['revision'], list(STAGES[1:])
            if cancel.is_set():
                return
            final = self._finalize(run_id, spec, accepted)
            if final:
                # Once recorded, the verdict completes the run even if a cancellation landed after it.
                self._state(run_id, 'completed', _verdict_reason(final['equivalence']), from_verdict=True)
            elif _observed_case(spec):
                self._state(run_id, 'completed', PENDING if completion == 'Fresh reuse accepted'
                            else completion + '; the run awaits independent evaluation')
            else:
                self._state(run_id, 'completed', completion)
        except Exception as exc:
            from .device_client import DeviceUnavailable
            # A provided device that cannot be reached or verified is the operator's to restore: nothing was dispatched.
            category = ('integrity' if isinstance(exc, IntegrityError) else 'safety' if isinstance(exc, SafetyError)
                        else 'operator' if isinstance(exc, DeviceUnavailable) else 'host')
            self._state(run_id, 'failed' if category == 'host' else 'blocked', type(exc).__name__ + ': ' + str(exc),
                        outcome_category=category)
        finally:
            if spec:
                try:
                    failures = self._release(run_id, spec, run_dir)
                except Exception as exc:
                    failures = [('cleanup.failed', type(exc).__name__ + ': ' + str(exc), None)]
                self._settle_release(run_id, failures)
            with self._lock:
                with self._db() as db:
                    row = db.execute('SELECT uncertain FROM runs WHERE id=?',(run_id,)).fetchone()
                    if row and not row['uncertain'] and run_id not in self._inflight:
                        db.execute('DELETE FROM bindings WHERE run_id=?',(run_id,))
                self._workers.pop(run_id, None)

    def _finalize(self, run_id, spec, accepted):
        """Record the run's one final verdict after reuse, as `<run_id>:final`, and end the queue in the same
        transaction. A recorded verdict is returned as it is and never graded again.

        A generic run's verdict is self-evidence. No evaluator is called here for a benchmark run (`spec.case`), whose
        stages were observed without truth: it records `progress.equivalence = 'pending'` for the independent grade
        after the run, which its bench harness posts with record_evaluation."""
        with self._db() as db:
            final = _final_verdict(db, run_id)
        if final is not None:
            return final
        equivalence = None
        if not spec.get('case'):
            self._state(run_id, 'running', stage='finalize')
            for handoff in accepted:
                self._verify_handoff(handoff)
            with self._db() as db:
                rows = db.execute("SELECT seq,kind,payload FROM events WHERE run_id=? AND kind IN ('tool.started','tool.finished') "
                                  "ORDER BY seq", (run_id,)).fetchall()
                # Every reuse attempt, including one that stopped before its handoff and was resumed.
                reuses = db.execute("SELECT id,payload FROM assignments WHERE run_id=? AND stage='reuse'", (run_id,)).fetchall()
                marks = db.execute("SELECT seq,kind,payload FROM events WHERE run_id=? AND kind IN "
                                   "('stage.assigned','stage.accepted','stage.skipped','tool.started','tool.finished','tool.refused')",
                                   (run_id,)).fetchall()
            # Whether fresh reuse could reach the device is judged by the run's binding state as of its last event: an
            # operator observation recorded after it never turns a connection the gateway refused into a driver verdict.
            attempts = {row['id'] for row in reuses}
            owner = lambda row, data: data.get('id') if row['kind'] == 'stage.assigned' else data.get('assignment_id')
            until = max((row['seq'] for row in marks if owner(row, json.loads(row['payload'])) in attempts), default=None)
            equivalence = _self_evidence(accepted, [json.loads(row['payload']) for row in rows if row['kind'] == 'tool.finished'],
                                         [{**json.loads(row['payload']), 'dispatch_id': str(row['seq'])}
                                          for row in rows if row['kind'] == 'tool.started'],
                                         [{'id': row['id'], 'inputs': json.loads(row['payload']).get('inputs')} for row in reuses],
                                         spec.get('effects'),
                                         _unreachable(spec.get('binding'), self._binding_state(run_id, until), spec.get('effects')))
        with self._db() as db:
            if db.execute('SELECT cancelled FROM runs WHERE id=?', (run_id,)).fetchone()['cancelled']:
                return None
            progress = {**self._workflow_progress(run_id, db), 'next_stage': None,
                        **({'equivalence': 'pending'} if _observed_case(spec) else {})}
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (run_id, _json(progress)))
            if equivalence is None:
                return None
            frozen = next((c.get('package') for c in equivalence['checks'] if c['id'] == 'emitted_package_replay'), None)
            final = {'stage': 'finalize', 'equivalence': equivalence, 'protocol': PROTOCOL,
                     'frozen_artifact_sha256': (frozen or {}).get('sha256')}
            db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)', (run_id + FINAL, run_id, 'finalize', _json(final), time.time()))
            self._event(run_id, 'run.finalized', {key: equivalence[key] for key in ('equivalent', 'basis', 'diff')}, db)
        return final

    def _release(self, run_id, spec, run_dir):
        """Give up what the run holds. A provided device is released, never stopped or sealed here: the bench harness
        owns the device and the evidence. Returns the failures in the order they happened as (event kind, reason, event
        detail). A generic run owns nothing, and neither does any other benchmark run: the configurator never holds an
        emulator itself, and the operator owns a bound device."""
        failures = []
        if not spec.get('case'):
            return failures
        if spec.get('device'):
            from .device_client import open_device
            with self._lock:
                operation_lock = self._operations.setdefault(run_id, threading.RLock())
            try:
                # Live calls hold this lock across the device's window. A cancelled worker's call may still be inside
                # it: the run lets go of the device only after that call's window has closed and its reading is taken.
                with operation_lock:
                    with self._db() as db:
                        saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
                    held = (json.loads(saved['payload']) if saved else {}).get('device') or {}
                    open_device(spec['device']).release(held.get('session_id'))
            except Exception as error:
                failures.append(('device.release_failed', type(error).__name__ + ': ' + str(error), None))
        return failures

    def _settle_release(self, run_id, failures):
        """Before a final verdict a release that failed (`cleanup.failed`: giving up what the run holds raised) retains
        the lease as uncertain. After the verdict each failure is an event; the verdict and the completed status stand
        unless an effect is uncertain, which blocks for safety. So does an uncertain effect on a run that completed
        awaiting its independent grade, which an uncertain run cannot record. This relies on the configurator owning no
        device resource once a verdict exists: generic runs own nothing, and the bench harness, not the configurator,
        stops and seals a provided device."""
        with self._db() as db:
            final = _final_verdict(db, run_id)
            saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run_id,)).fetchone()
        awaiting = final is None and saved is not None and json.loads(saved['payload']).get('equivalence') == 'pending'
        if any(kind != 'device.release_failed' for kind, _, _ in failures):
            awaiting = False  # The failed cleanup blocks the run for the host below.
        for kind, reason, detail in failures:
            if kind == 'device.release_failed':
                # The harness still owns the device and stops it after the run; the run's status stands.
                self._event(run_id, kind, {'reason': reason})
                continue
            # Before the verdict, a failed stop may leave an external resource live: retain
            # its lease until explicit reconciliation, even after this worker exits.
            with self._lock:
                with self._db() as db:
                    if final is None:
                        db.execute('UPDATE runs SET uncertain=1 WHERE id=?',(run_id,))
                    self._event(run_id, kind, {'reason': reason}, db)
                if final is None:
                    self._state(run_id,'blocked','Benchmark cleanup failed; operator reconciliation required',
                                outcome_category='host')
        if (final is not None or awaiting) and self._row(run_id)['uncertain']:
            # The operator's reconciliation and resume complete a run awaiting its grade again.
            self._state(run_id, 'blocked', UNCERTAIN_AFTER_VERDICT if final is not None else UNCERTAIN_AWAITING,
                        outcome_category='safety')

    @contextmanager
    def _active_operation(self,run_id):
        with self._lock:
            self._inflight.add(run_id)
        try:
            yield
        finally:
            with self._lock:
                self._inflight.discard(run_id)
                with self._db() as db:
                    row=db.execute('SELECT status,uncertain FROM runs WHERE id=?',(run_id,)).fetchone()
                    if row and row['status'] in TERMINAL and not row['uncertain'] and run_id not in self._workers:
                        db.execute('DELETE FROM bindings WHERE run_id=?',(run_id,))

    def _claim_binding(self, binding, run_id, db):
        if not binding:
            return
        identity = _lease(binding)
        prior = db.execute('SELECT run_id FROM bindings WHERE identity=?',(identity,)).fetchone()
        if prior and prior['run_id'] != run_id:
            owner = db.execute('SELECT status,uncertain FROM runs WHERE id=?',(prior['run_id'],)).fetchone()
            if owner and (owner['status'] in ('queued','running') or owner['uncertain'] or prior['run_id'] in self._workers or prior['run_id'] in self._inflight):
                raise SafetyError('Device binding is already owned by another run')
        db.execute('INSERT OR REPLACE INTO bindings VALUES(?,?)',(identity,run_id))

    @staticmethod
    def _verify_inputs(inputs):
        for path, expected in inputs.items():
            try:
                observed = digest(path)
            except (OSError, ValueError):
                observed = None
            if observed != expected:
                raise IntegrityError('Stale handoff: an assigned input was altered: ' + path)

    @classmethod
    def _verify_handoff(cls, handoff):
        """Re-hash an accepted handoff: its artifacts, its notes.json and the inline notes recorded with it."""
        hashes = {a['path']: a['sha256'] for a in handoff['artifacts']}
        notes = handoff.get('notes') or {}
        if 'path' in notes:
            if hashlib.sha256(_notes_bytes(notes.get('inline'))).hexdigest() != notes.get('sha256'):
                raise IntegrityError('Stale handoff: recorded notes differ from their accepted hash: ' + notes['path'])
            hashes[notes['path']] = notes['sha256']
        cls._verify_inputs(hashes)

    @staticmethod
    def _stage_inputs(stage, accepted):
        """Each input comes from the latest accepted handoff of its stage that has artifacts; absence is a note."""
        selected, missing = [], []
        for source, label in STAGE_INPUTS.get(stage, ()):
            found = next(([a['path'] for a in h['artifacts']] for h in reversed(accepted) if h['stage'] == source and h['artifacts']), None)
            if found:
                selected += found
            else:
                missing.append('No ' + label + ' available')
        return selected, missing

    @staticmethod
    def _emit_inputs(accepted):
        """Generic emit's inputs: an interpreted model and the newest successful probe record that model wrote, from a
        probe or ground handoff, as the configurator bound each record to the digest of its writer in that handoff's
        notes. The latest model is preferred; when it wrote no successful record, the newest model that did is taken,
        so the model and its record always belong together. Without one, the latest model alone, with a note."""
        models = [a for h in reversed(accepted) if h['stage'] == 'interpret' for a in h['artifacts']
                  if (Path(a['path']) / 'model.json').is_file()]
        if not models:
            return [], ['No interpreted model available']
        def successful(path):
            try:
                record = json.loads(Path(path).read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return False
            # An interface probe record says ok; a register probe's record says whether the identity answered.
            return isinstance(record, dict) and (record.get('ok') is True or record.get('identity_ok') is True)
        written = []  # (record path, digests of the models that wrote it), newest first
        for handoff in reversed(accepted):
            if handoff['stage'] in ('probe', 'ground'):
                writers = _record_writers([handoff])
                written += [(a['path'], writers[a['sha256']]) for a in reversed(handoff['artifacts'])
                            if a['sha256'] in writers and successful(a['path'])]
        for rank, model in enumerate(models):
            record = next((path for path, writers in written if model['sha256'] in writers), None)
            if record:
                return [model['path'], record], ([] if rank == 0 else [
                    'No successful probe record of the latest interpreted model; emitting the latest model that wrote one'])
        return [models[0]['path']], ['No successful probe record of an interpreted model available']

    @staticmethod
    def _verify_snapshot(spec, run_dir):
        if '_snapshot_sha256' not in spec:
            return None  # Historical runs have no execution evidence to reconstruct.
        from .benchmark_support.snapshots import require_snapshot, execution_provenance
        saved = require_snapshot(run_dir, spec['_case_pin'])
        if saved['snapshot_sha256'] != spec['_snapshot_sha256']:
            raise IntegrityError('Execution snapshot hash differs from saved run')
        if execution_provenance(spec['executor_config']) != saved['executed']:
            raise IntegrityError('Execution snapshot implementation or runtime environment changed; start a new run')
        return saved

    def _prepare(self, spec, stage, run_dir, workspace, accepted):
        self._verify_snapshot(spec, run_dir)
        case = spec.get('case')
        if case:
            from .benchmark import prepare_stage
            case_id = case['id'] if isinstance(case, dict) else case
            options = case_options(spec)
            prepared = prepare_stage(case_id, stage, run_dir, workspace, accepted, options)
            if spec.get('device') and prepared.get('binding') and not prepared.get('skip'):
                # The stage runs on the provided device as the durable record holds it, and only while it answers.
                if prepared['binding'] != (spec.get('_device') or {}).get('binding'):
                    raise SafetyError('Prepared stage binding differs from the provided device')
                self._device_ready(spec)
            if prepared.get('unwired_model'):
                prepared = {**prepared, **self._unwired_model(run_dir, workspace.name, prepared['unwired_model'])}
            # Stages observed without truth pass their earlier, truth-free notes forward, as generic stages do. Fresh
            # reuse never receives notes.
            prior = _prior_notes(accepted, spec.get('_revision', 0)) if stage != 'reuse' else []
            if prior and not prepared.get('skip') and 'prompt' not in prepared:
                prepared = {**prepared, 'context': {**(prepared.get('context') or {}), 'prior_notes': prior}}
            return prepared
        if stage == 'interpret':
            # Interpretation that cannot start hands off empty with the reason; it never stops the run.
            from .toolkit import call_tool
            acquisition = next((h for h in reversed(accepted) if h['stage']=='acquire'),None)
            artifacts = [Path(a['path']) for a in acquisition['artifacts']] if acquisition else []
            named = [p for p in artifacts if re.fullmatch(r'(?:\d+-)?interpret-input\.(?:elf|bin)', p.name)]
            images = named if len(named) == 1 else [p for p in artifacts if p.suffix.lower() in ('.bin', '.elf')]
            if spec.get('front_end','interpret-interface') != 'interpret-interface':
                return {'skip':'Interpretation skipped: generic blind interpretation supports only front_end=interpret-interface'}
            if len(images) != 1:
                return {'skip':'Interpretation skipped: no single acquired binary to interpret (' + str(len(images)) + ' candidates)'}
            # A repair keeps the sealed workspace and adds the device diff and the candidate it contradicts.
            inputs = {'image.bin':str(images[0])}
            # A successful passive listen records transport settings and boot/reset evidence.
            # Keep that observed acquisition evidence in the sealed interpretation inputs.
            for artifact in artifacts:
                if artifact.suffix.lower() != '.json':
                    continue
                try:
                    evidence = json.loads(artifact.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if isinstance(evidence, dict) and evidence.get('schema') == 'serial-listen/1':
                    key = 'serial-listen.json' if 'serial-listen.json' not in inputs else artifact.name
                    inputs[key] = str(artifact)
                    if key == 'serial-listen.json' and type(evidence.get('baudrate')) is int:
                        binding_context = run_dir / 'preparation' / workspace.name / 'context' / 'BINDING_CONTEXT.json'
                        binding_context.parent.mkdir(parents=True, exist_ok=True)
                        binding_context.write_text(json.dumps({
                            'runtime_channel': {'type': 'uart', 'baudrate': evidence['baudrate'],
                                'resets_on_open': evidence.get('unsolicited_output') is True},
                            'source': 'Observed acquisition in serial-listen.json; no physical endpoint is supplied.',
                            'instructions': 'Use runtime_channel for the executable model. resets_on_open declares '
                                'the reset property the configurator observed; startup synchronization still requires '
                                'an evidence-backed exchange. The configurator supplies the endpoint separately.'
                        }, indent=1), encoding='utf-8')
                        inputs['BINDING_CONTEXT.json'] = str(binding_context)
            tasks = run_dir / 'preparation' / workspace.name / 'context' / 'TASKS.json'
            tasks.parent.mkdir(parents=True, exist_ok=True)
            tasks.write_text(json.dumps({'schema': 'candidate-task-contract/1',
                'tasks': {'requested': {'meaning': spec['goal']}}, 'effect_grants': spec.get('effects', ['read']),
                'instructions': 'The objective describes requested behavior, not protocol evidence. Recover bytes, '
                    'decoders and units from supplied evidence. Report unsupported behavior. Do not execute or '
                    'relabel effects outside the grants.'}, indent=1), encoding='utf-8')
            inputs['TASKS.json'] = str(tasks)
            previous = next((a['path'] for h in reversed(accepted) if h['stage'] == 'interpret' for a in h['artifacts']
                             if (Path(a['path']) / 'model.json').is_file()), None)
            if previous:
                inputs['previous-model'] = previous
            defects = spec.get('_feedback') if spec.get('_revision') else None
            tool_run = run_dir / 'preparation' / workspace.name
            if previous or defects:
                # Say what each repair input is, so the agent starts from the prior candidate, and that every repair
                # input is sealed: the configurator re-hashes them after the agent, and a change stops the run.
                sealed = [*(key + ('/' if key == 'previous-model' else '') for key in inputs), *(['defects.json'] if defects else []),
                          'REPAIR_CONTEXT.json']
                context = tool_run / 'repair' / 'REPAIR_CONTEXT.json'
                context.parent.mkdir(parents=True, exist_ok=True)
                context.write_text(json.dumps({'schema': 'device-repair-context/1', 'mode': 'repair',
                    **({'previous_candidate': 'previous-model/'} if previous else {}),
                    **({'feedback': 'defects.json', 'feedback_meaning': 'What the previous candidate got wrong, from '
                        'the configurator\'s own record: what the device did that the candidate did not predict, in '
                        'tool results it observed through its own connection, and, in entries with source '
                        'model_contract, defects the configurator\'s own validation found in the candidate (for an '
                        'interface model, the runtime refuses such a model before it opens the device). It is '
                        'evidence, not a grade.'} if defects else {}),
                    'sealed_inputs': sealed,
                    'instructions': ('Start from previous-model/ and change what the evidence contradicts. ' if previous else
                                     'Change what the evidence contradicts. ') +
                                    ', '.join(sealed[:-1]) + ' and ' + sealed[-1] + ' are sealed inputs, like every file '
                                    'supplied in this workspace: read them and leave them unchanged; changing any sealed '
                                    'input stops the run. Write the revised model.json, and replies.json when BRIEF.md '
                                    'asks for it, at the workspace root, and record which evidence each correction '
                                    'addresses in NOTES.md.'}, indent=1), encoding='utf-8')
                inputs['REPAIR_CONTEXT.json'] = str(context)
            prepared = call_tool('interpret_run',{'run_dir':str(tool_run),'front_end':'interpret-interface','inputs':inputs,
                **({'defects':defects} if defects else {}),'mode':'prepare','workspace_root':tempfile.mkdtemp(prefix='gd-candidate-')})
            if not prepared.get('ok'):
                return {'skip':'Could not prepare a sealed interpretation workspace: '+
                        str(prepared.get('reason',prepared.get('error','unknown preparation error')))}
            return {'work_dir':prepared['workspace'],'prompt':prepared['prompt_for_agent'],'report_required':False,
                'inputs':[str(Path(prepared['workspace'])/name) for name in prepared['sealed_inputs']], 'allowed_tools':[],
                'collection':{'run_dir':str(tool_run),'expected_seal_sha256':prepared['seal_sha256'],'model_dir':prepared['model_dir']}}
        # Reuse receives only the emitted package and never earlier notes. Acquire receives the operator inputs;
        # later stages receive what they need from the latest handoff that has it, and earlier notes.
        # Neither receives the prior conversation.
        missing = []
        if stage == 'reuse':
            source = next(([a['path'] for a in _packages(h)] for h in reversed(accepted) if h['stage'] == 'emit' and _packages(h)), [])
            if len(source) != 1:
                return {'skip': 'Fresh reuse skipped: no single accepted emitted package to reuse'}
        elif stage == STAGES[0]:
            source = list(spec.get('inputs', {}).values())
        elif stage == 'emit':
            source, missing = self._emit_inputs(accepted)
        else:
            source, missing = self._stage_inputs(stage, accepted)
        inputs = []
        for index, raw in enumerate(source):
            path = Path(raw).expanduser().resolve()
            digest(path)
            target = workspace / 'inputs' / (str(index) + '-' + path.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(path, target) if path.is_dir() else shutil.copy2(path, target)
            inputs.append(str(target))
        context = {'stage': stage, 'acceptance': ('Report evidence and checks for this stage honestly. Observed tool '
                   'evidence is handed on; every report is kept as notes and the run continues.')}
        if spec.get('binding'):
            # Workers learn that a device exists, never its connection.
            context['device'] = ('An operator-owned device binding is attached. The configurator supplies its '
                                 'connection to device tools; do not pass a port or binding.')
        if spec.get('binding') and stage == 'acquire':
            context['serial'] = ('Listen first with acquire_serial_listen. If it reports unsolicited output the device '
                                 'resets on open, and every later connection needs the operator reset grant. '
                                 'Granted effects: ' + ', '.join(spec.get('effects', [])) + '.')
        if spec.get('_operator_observations'):
            context['operator_observations'] = spec['_operator_observations']
        if missing:
            context['input_notes'] = missing
        prior = _prior_notes(accepted, spec.get('_revision', 0)) if stage != 'reuse' else []
        if prior:
            context['prior_notes'] = prior
        return {'objective': GROUND_OBJECTIVE + spec['goal'] if stage == 'ground' else spec['goal'], 'inputs': inputs,
                'allowed_tools': STAGE_TOOLS[stage], 'context': context, **({'input_notes': missing} if missing else {})}

    @staticmethod
    def _device_ready(spec):
        """The provided device answers, healthy, with the session and binding the run recorded; otherwise the stage
        stops for the operator before any worker or dispatch."""
        from .device_client import open_device, DeviceUnavailable
        held = spec.get('_device') or {}
        status = open_device(spec['device']).status()
        if not isinstance(status, dict) or status.get('session_id') != held.get('session_id') or status.get('binding') != held.get('binding'):
            raise DeviceUnavailable('Device session changed; attach a verified device')
        if status.get('healthy') is not True:
            raise DeviceUnavailable('Device provider unavailable: the provided device is not healthy')

    def _resume_device(self, run_id):
        """A stopped emulated run resumes a stage only on its provided device as the run recorded it: the device must
        answer, healthy, with the recorded session and binding. A device the harness stopped, replaced or withdrew
        stops the run for the operator, who has the harness attach a verified one (`attach_device`); a run started
        before the harness provided devices has none. Only the device's status is asked, outside the controller lock;
        everything else about the resume is checked under it afterwards."""
        from .device_client import DeviceUnavailable
        row = self._row(run_id)
        spec = json.loads(row['spec'])
        if (not _emulated(spec) or row['status'] not in ('blocked', 'failed', 'cancelled') or row['uncertain']
                or run_id in self._workers or run_id in self._inflight):
            return
        try:
            progress = self._workflow_progress(run_id)
        except ValueError:
            return
        if progress['next_stage'] not in STAGES:
            return
        if not spec.get('device'):
            if not _v2(spec):
                return  # Legacy tq9 started without its bench harness: its device stages stop for the operator again.
            raise ValueError('This emulated run started before the bench harness provided devices; start a new trial')
        try:
            self._device_ready({**spec, '_device': progress.get('device')})
        except DeviceUnavailable as error:
            reason = str(error) if str(error).endswith('attach a verified device') else str(error) + '; attach a verified device'
            self._state(run_id, 'blocked', reason, outcome_category='operator')
            raise ValueError(reason) from None

    def _attach_device(self, run_id, params):
        """The bench harness attaches a device it provides anew to a stopped run of its trial, after the device the run
        recorded was stopped or replaced outside the run. Only the run's harness may (the sha256 of its evaluation
        token), only while the run is stopped with nothing uncertain, and only a device from the pinned image that
        answers, healthy, with the session and binding its descriptor names. The run keeps its recovery receipts; its
        resume leases the new binding. Nothing reaches the device but its status."""
        from .device_client import descriptor_errors, open_device
        spec = json.loads(self._row(run_id)['spec'])
        if not spec.get('device') or not spec.get('evaluation_token_sha256'):
            raise ValueError('Only a run started on a device the bench harness provides can attach one')
        token, device = params.get('token'), params.get('device')
        if not isinstance(token, str) or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),
                                                                 spec['evaluation_token_sha256']):
            raise ValueError('attach_device requires the run\'s evaluation token')
        errors = descriptor_errors(device)
        if errors:
            raise ValueError('Invalid provided device descriptor: ' + '; '.join(errors))
        binding_identity(device['binding'])
        if device['image_sha256'] != spec['_case_pin'].get('image_hashes', {}).get('firmware.bin'):
            raise IntegrityError('Provided device image differs from the pinned firmware image')
        status = open_device(device).status()
        if (not isinstance(status, dict) or status.get('healthy') is not True or status.get('session_id') != device['session_id']
                or status.get('binding') != device['binding']):
            raise ValueError('The provided device does not answer, healthy, with the session and binding its descriptor names')
        with self._lock:
            row = self._row(run_id)
            if row['status'] not in ('blocked', 'failed', 'cancelled') or run_id in self._workers or run_id in self._inflight:
                raise ValueError('attach_device only while the run is stopped')
            if row['uncertain']:
                raise ValueError('Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation')
            with self._db() as db:
                progress = self._workflow_progress(run_id, db)
                held = progress.get('device') or {}
                progress['device'] = {'session_id': device['session_id'], 'binding': dict(device['binding']),
                                      'receipts': list(held.get('receipts', []))}
                saved = {**json.loads(row['spec']), 'device': device}
                db.execute('UPDATE runs SET spec=? WHERE id=?', (_json(saved), run_id))
                db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (run_id, _json(progress)))
                self._event(run_id, 'device.attached', {'session_id': device['session_id'], 'binding': device['binding'],
                    'provider_id': device.get('provider_id'),
                    'previous': {'session_id': held.get('session_id'), 'binding': held.get('binding')}}, db)
        return self.call('status', {'run_id': run_id})

    def _record_evaluation(self, run_id, params):
        """The bench harness posts the independent evaluation of a completed run's frozen driver, graded after the run.
        Only the run's harness may (the sha256 of its evaluation token), only for a completed run with nothing uncertain
        that awaits its evaluation, only under its case's grading method (`fresh-final` for a v2 case, `recorded-reuse`
        for legacy tq9 and bme280), and only about the run's accepted emitted package: the first artifact of its latest
        emit handoff that handed one on, or none. A graded or no-driver record becomes the run's one final verdict; an
        identical repeat changes nothing and any other is refused. An unscored one is an event, and the run still awaits."""
        spec = json.loads(self._row(run_id)['spec'])
        if not spec.get('evaluation_token_sha256'):
            raise ValueError('Only a run started by the bench harness can record an evaluation')
        token, record = params.get('token'), params.get('equivalence')
        if not isinstance(token, str) or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),
                                                                 spec['evaluation_token_sha256']):
            raise ValueError('record_evaluation requires the run\'s evaluation token')
        errors = _evaluation_errors(record)
        if errors:
            raise ValueError('Invalid evaluation record: ' + '; '.join(errors))
        # The method keeps grades on a fresh emulator apart from grades of recorded reuse: the run's pinned case names it.
        method = 'fresh-final' if _v2(spec) else 'recorded-reuse'
        if record['method'] != method:
            raise ValueError('The run\'s case is graded by method ' + method + ', not ' + str(record['method']))
        with self._lock:
            row = self._row(run_id)
            if row['status'] != 'completed' or run_id in self._workers or run_id in self._inflight:
                raise ValueError('record_evaluation only for a completed run')
            if row['uncertain']:
                raise ValueError('Outstanding effect is uncertain; operator must respond with confirmed_safe after reconciliation')
            with self._db() as db:
                final = _final_verdict(db, run_id)
                if final is not None:
                    if final.get('equivalence') != record:
                        raise ValueError('A different evaluation is already recorded for this run')
                    return self.call('status', {'run_id': run_id})
                progress = self._workflow_progress(run_id, db)
                if progress.get('equivalence') != 'pending':
                    raise ValueError('The run awaits no independent evaluation')
                accepted = [json.loads(r['payload']) for r in db.execute('SELECT payload FROM handoffs WHERE run_id=? ORDER BY created', (run_id,))]
                frozen = next((h['artifacts'][0]['sha256'] for h in reversed(accepted) if h['stage'] == 'emit' and h['artifacts']), None)
                if record['frozen_artifact_sha256'] != frozen:
                    raise IntegrityError('The evaluated package is not the run\'s accepted emitted package')
                if record['status'] == 'unscored':
                    self._event(run_id, 'evaluation.unscored', {'diff': record['diff'], 'frozen_artifact_sha256': frozen}, db)
                else:
                    db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)', (run_id + FINAL, run_id, 'finalize', _json(
                        {'stage': 'finalize', 'equivalence': record, 'protocol': PROTOCOL, 'frozen_artifact_sha256': frozen}), time.time()))
                    db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (run_id, _json({**progress, 'equivalence': record['status']})))
                    db.execute('UPDATE runs SET reason=?,updated=? WHERE id=?', (_verdict_reason(record), time.time(), run_id))
                    self._event(run_id, 'run.finalized', {key: record[key] for key in ('equivalent', 'basis', 'diff')}, db)
        return self.call('status', {'run_id': run_id})

    def _device_lost(self, run_id, assignment):
        """A stop for the operator when the provided device failed this assignment's live calls: before a call (it was
        refused, nothing was dispatched) or after one (its result stands)."""
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='device.unavailable' ORDER BY seq", (run_id,)).fetchall()
        lost = [event for event in (json.loads(row['payload']) for row in rows) if event.get('assignment_id') == assignment['id']]
        if not lost:
            return None
        first = lost[0]
        return {'stop': {'category': 'operator', 'reason': 'Device control lost after a dispatched call: ' + first['reason']
                         if first['phase'] == 'after' else first['reason'] + '; nothing was dispatched'}}

    def _device_call(self, run, assignment, name, arguments, device, invoke):
        """One live call on the provided device: the instrument reading before it, the call inside the device's window,
        the reading after it. A provider that fails before the call refuses it (nothing reached the device, so nothing
        is uncertain); one that fails after the call leaves the call's own result to reconcile. Either is recorded and
        stops the stage for the operator. The caller holds the run's operation lock (every tool call does), so one live
        call at a time is inside the window and its readings are its own."""
        from .device_client import DeviceUnavailable, reading
        started, result, before, after, lost = False, None, None, None, None
        try:
            before = reading(device.observe())
            with device.window():
                started = True
                result = invoke()
            after = reading(device.observe())
        except DeviceUnavailable as error:
            if started and result is None:
                raise  # The call's own outcome is unknown: its effect stays uncertain.
            lost = ('after' if started else 'before', str(error))
        if lost:
            self._event(run['id'], 'device.unavailable', {'stage': assignment['stage'], 'assignment_id': assignment['id'],
                                                          'name': name, 'phase': lost[0], 'reason': lost[1]})
        if not started:
            item = {'schema': 'interface-result/1', 'ok': False, 'transport_open_attempted': False, 'outputs': {},
                    'units': {}, 'transcript': [], 'error': {'fault': 'operator', 'code': 'device_unavailable',
                    'message': 'The device provider is unavailable; nothing was dispatched: ' + lost[1]}}
            return {'ok': False, 'results': [item], 'error': item['error']} if name == 'probe_run' else item
        spec = json.loads(run['spec'])
        from .benchmark import record_call
        record_call(spec['_case_pin']['case_id'], self.home / 'runs' / run['id'], assignment['stage'], name, arguments,
                    result, before, after, {'assignment_id': assignment['id']})
        return {**result, 'before_observation': before, **({'observation': after} if after else {})}

    def _check(self, spec, stage, run_dir, workspace, report, accepted, assignment):
        if spec.get('case'):
            # Truth-free observation, for every case: the adapter's case evidence and notes, and for probe and ground the
            # configurator's own device evidence and diff. No evaluator runs and nothing is graded.
            from .benchmark import observe_stage
            case = spec['case']
            case_id = case['id'] if isinstance(case, dict) else case
            options = case_options(spec)
            lost = self._device_lost(run_dir.name, assignment) if spec.get('device') else None
            if lost:
                return lost
            device = {}
            if stage in ('probe', 'ground'):
                device = self._observe_device(stage, assignment, self._finished(run_dir.name, assignment), accepted,
                                              run_dir, workspace)
                if device.get('stop'):
                    return device
            options.update(assignment_id=assignment['id'], observed_records=device.get('artifacts', []))
            if stage == 'emit':
                # The packages it saw emit_package build in this assignment, by the hash it recorded when the tool
                # returned: the only bytes a benchmark emit may hand on.
                options['emit_builds'] = _emit_builds(self._finished(run_dir.name, assignment))
            checked = observe_stage(case_id, stage, run_dir, workspace, report, accepted, options)
            if checked.get('stop') or not device:
                return checked
            checks, notes = [*checked['checks'], *device['checks']], checked.get('notes') or {}
            return {**checked, 'artifacts': list(dict.fromkeys([*checked['artifacts'], *device['artifacts']])),
                    'checks': checks, 'notes': {'checks': checks, 'records': device['records'],
                                                'dropped_artifacts': [*notes.get('dropped_artifacts', []), *device['dropped']]},
                    **({'device_diff': device['device_diff']} if device['device_diff'] else {})}
        # Generic checks use only run evidence. Failures become notes and the stage hands off what was
        # observed; only a recorded or observed hash that changed stops the run.
        def handed(selected, checks, dropped=(), scope='Observed tool outputs and executable artifact contracts; '
                   'physical meaning requires independent ground evidence', diff=None, records=()):
            return {'ok':True,'artifacts':list(dict.fromkeys(selected)),'checks':checks,
                    'notes':{'checks':checks,'dropped_artifacts':list(dropped),'records':list(records)},
                    'validation_scope':scope, **({'device_diff':diff} if diff else {})}
        def changed(reason):
            return {'stop':{'category':'integrity','reason':reason}}
        if stage == 'interpret':
            # A model that exists behind an intact seal is handed off whether or not it validates; its validation
            # is a note. Only a changed or unverifiable seal stops.
            from .toolkit import call_tool
            prepared = assignment.get('collection')
            if not prepared:
                return handed([], [{'name':'sealed_model_collection','passed':False,'reason':'No pinned interpretation preparation record'}])
            result = call_tool('interpret_collect',{'run_dir':prepared['run_dir'],
                'expected_seal_sha256':prepared['expected_seal_sha256'],'seal_kind':'instruction'})
            reason = result.get('reason') or result.get('error')
            if not result.get('ok') and re.search(r'(?i)seal|changed|differ', str(result.get('reason') or '')):
                return changed('Sealed interpretation evidence changed or is unverifiable: ' + str(reason))
            model = Path(prepared['model_dir'])
            present = bool((result.get('ok') or result.get('sealed_inputs_unchanged') is True) and (model / 'model.json').is_file())
            validation = call_tool('model_validate',{'run_dir':str(workspace/'checks'),'model_dir':str(model)}) if present else {}
            return handed([str(model)] if present else [], [
                {'name':'sealed_model_collection','passed':bool(result.get('ok')),**({} if result.get('ok') else {'reason':reason})},
                {'name':'executable_model_contract','passed':bool(validation.get('ok')),
                 **({'defects':validation['defects']} if validation.get('defects') else {}),
                 **({} if present else {'reason':'No model.json to validate'})}],
                scope='Sealed-input integrity and executable model contract; meaning requires probe and independent ground evidence')
        report = report if isinstance(report, dict) else {}
        paths, dropped, named = [], [], []
        for artifact in report.get('artifacts') if isinstance(report.get('artifacts'), list) else []:
            # Worker artifacts are claims: the configurator hashes the bytes itself and drops mismatches.
            raw = artifact.get('path') if isinstance(artifact, dict) else None
            if not isinstance(raw, str):
                continue
            path = Path(raw) if Path(raw).is_absolute() else workspace / raw
            if not path.resolve().is_relative_to(workspace):
                dropped.append({'path':raw,'reason':'Reported artifact is outside the assigned workspace'})
                continue
            if stage == 'emit':
                # An emit report names which package it means; the configurator's own record supplies its hash.
                named.append(path.resolve())
                continue
            try:
                observed_hash = digest(path)
            except (OSError, ValueError) as error:
                dropped.append({'path':raw,'reason':'Reported artifact is unreadable: '+str(error)})
                continue
            if observed_hash != artifact.get('sha256'):
                dropped.append({'path':raw,'reason':'Worker artifact hash disagrees with observed bytes'})
                continue
            paths.append(path.resolve())
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='tool.finished' ORDER BY seq", (run_dir.name,)).fetchall()
        mine = [event for event in (json.loads(row['payload']) for row in rows) if event.get('assignment_id') == assignment['id']]
        observed = [event for event in mine if event.get('ok')]
        def unchanged(raw, expected):
            path = Path(raw).resolve()
            try:
                return path.is_relative_to(workspace) and digest(path) == expected
            except (OSError, ValueError):
                return False
        checks, selected = [], []
        def check(name, reason, **evidence):
            checks.append({'name':name,'passed':bool(selected),**({'evidence':selected} if selected else {'reason':reason}),**evidence})
        if stage == 'acquire':
            calls = [e for e in observed if e['name'].startswith('acquire_') and e.get('result',{}).get('available')]
            for call in calls:
                for raw, expected in call.get('artifacts',{}).items():
                    if not unchanged(raw, expected):
                        return changed('Observed acquisition artifact changed after the tool returned')
                    selected.append(str(Path(raw).resolve()))
            check('observed_acquisition_hashes', 'No observed acquisition with verifiable output artifacts',
                  **({} if selected else {'unobserved':[str(p) for p in paths]}))
        elif stage in ('probe', 'ground'):
            device = self._observe_device(stage, assignment, mine, accepted, run_dir, workspace)
            if device.get('stop'):
                return device
            return handed(device['artifacts'], device['checks'], [*dropped, *device['dropped']], diff=device['device_diff'],
                          records=device['records'], **({} if stage == 'probe' else {'scope': 'Observed device tests of the '
                          'model and their differences from it; physical meaning rests on operator observations where given'}))
        elif stage == 'emit':
            from .toolkit import call_tool
            # A package is bytes the configurator observed, as probe records are: those of a package_dir an
            # emit_package of this assignment built, hashed when the tool returned, or of a package an emit_check of
            # this assignment was given, hashed before dispatch. The report may name which (a copy of those bytes
            # elsewhere in the workspace too); without a named package the newest emit_package build is taken. A
            # worker's claimed hash is never used.
            built = _emit_builds(observed)
            given = ({(e.get('argument_sha256') or {}).get('package_dir') for e in mine if e['name'] == 'emit_check'}
                     | set(built.values())) - {None}
            candidates, unobserved, gone = _observed_packages(
                named, built, given, 'not built by emit_package or given to emit_check through the configurator')
            dropped += gone
            verified, rejected = [], []
            for package in candidates:
                # The check's own records go outside the worker's workspace, so nothing the worker leaves there can
                # make the configurator's check fail for the host.
                result = call_tool('emit_check',{'package_dir':str(package),'run_dir':str(run_dir/'checks'/assignment['id'])})
                if _host_fault(result):
                    # The host could not run the check, which says nothing about the package: stop for the host
                    # (resumable) rather than reject the package.
                    raise RuntimeError('emit_check could not run on this host for ' + package.name + ': '
                                       + str(result.get('error')))
                replayed=result.get('replay_status')=='passed' or result.get('seeds_run',0)>0
                if result.get('ok') and result.get('_exit',0)==0 and result.get('runtime_current') and replayed:
                    selected.append(str(package))
                    # An interface package replays the exchanges it bundles, which are device exchanges only when
                    # its probe evidence was recorded live; a register package is checked against its model on seeded
                    # register files and replays no device exchange.
                    verified.append(SEEDED if result.get('replay_status') != 'passed' else
                                    REPLAYED if result.get('source_evidence_kind') == 'live' else SUPPLIED)
                else:
                    why = _emit_check_failure(result)
                    rejected.append({'package': package.name, 'reason': why})
                    dropped.append({'path': str(package), 'reason': 'emit_check did not pass: ' + why})
            check('package_integrity_and_replay', 'No independently checked emitted package', candidates=len(candidates),
                  **({'verified_by': verified} if verified else {}), **({'rejected': rejected} if rejected else {}),
                  **({'not_handed_off': unobserved} if unobserved else {}))
        elif stage == 'reuse':
            # This attempt's live operations, judged by the final verdict's rule for the package it received.
            package = next(({'path':raw,'sha256':sha} for raw, sha in (assignment.get('inputs') or {}).items()
                            if (Path(raw) / 'manifest.json').is_file()), None)
            with self._db() as db:
                started = [{**json.loads(row['payload']), 'dispatch_id': str(row['seq'])} for row in db.execute(
                    "SELECT seq,payload FROM events WHERE run_id=? AND kind='tool.started' ORDER BY seq", (run_dir.name,))]
            operations, live = _fresh_reuse(_frozen_package(package) if package else None, mine,
                                            [e for e in started if e.get('assignment_id') == assignment['id']],
                                            spec.get('effects'),
                                            _unreachable(assignment.get('binding'), self._binding_state(run_dir.name),
                                                         spec.get('effects')))
            executed = operations['passed'] is True
            selected = [str(p) for p in paths] if executed else []
            checks.append({'name':'fresh_package_execution','passed':operations['passed'],
                           'operations':operations['operations'],
                           'basis':"The final verdict's rule for fresh reuse, over this attempt's live operations",
                           **({'evidence':selected} if executed else
                              {'reason':'; '.join(d['reason'] for d in live) or 'No live operation of the package'})})
        return handed(selected, checks, dropped)

    def _finished(self, run_id, assignment):
        """The configurator's tool.finished records of one assignment, in order."""
        with self._db() as db:
            rows = db.execute("SELECT payload FROM events WHERE run_id=? AND kind='tool.finished' ORDER BY seq", (run_id,)).fetchall()
        return [event for event in (json.loads(row['payload']) for row in rows) if event.get('assignment_id') == assignment['id']]

    def _observe_device(self, stage, assignment, mine, accepted, run_dir, workspace):
        """Probe and ground evidence from the configurator's own record, for generic and benchmark runs alike: its
        validation of each assigned model, every probe record of the device answering on its connection, the device
        diff and the stage's device-test note. Returns {artifacts, checks, dropped, records, device_diff}, or a stop
        when bytes it observed changed after the tool returned."""
        from .toolkit import call_tool
        workspace = Path(workspace).resolve()
        def changed(reason):
            return {'stop':{'category':'integrity','reason':reason}}
        def unchanged(raw, expected):
            path = Path(raw).resolve()
            try:
                return path.is_relative_to(workspace) and digest(path) == expected
            except (OSError, ValueError):
                return False
        observed = [event for event in mine if event.get('ok')]
        checks, selected, dropped = [], [], []
        # The configurator validates each assigned model itself, on a copy in its own run directory because a
        # validator may write beside the model: a model that fails validation goes back to interpret whether or not
        # the worker ran it. (For an interface model the runtime refuses such a model before opening the device.)
        contracts = {}
        for raw, expected in (assignment.get('inputs') or {}).items():
            if not (Path(raw) / 'model.json').is_file():
                continue
            scratch = run_dir / 'checks' / assignment['id']; scratch.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=scratch, ignore_cleanup_errors=True) as copied:
                model = Path(copied) / Path(raw).name
                shutil.copytree(raw, model)
                if digest(model) != expected:
                    return changed('Assigned model differs from its recorded hash: ' + raw)
                validation = call_tool('model_validate', {'run_dir': str(scratch), 'model_dir': str(model)})
            contracts[raw] = _replaced(validation, [(str(model), raw)])
        if contracts:
            checks.append({'name':'executable_model_contract','passed':all(v.get('ok') is True for v in contracts.values()),
                           'models':[{'name':Path(raw).name,'sha256':assignment['inputs'][raw],'passed':v.get('ok') is True,
                                      **({'defects':v['defects']} if v.get('defects') else {}),
                                      **({'reason':'Validation did not run: ' + str(v['error'])} if v.get('error') else {})}
                                     for raw, v in contracts.items()]})
        # Every record of the device answering on the configurator's connection is handed on, whatever model ran; a
        # replay reached no device. Only the last run that wrote a record path counts: a later run replaces the record.
        # The notes bind each record to the digest of the model that wrote it, so a later stage compares a record
        # with its assigned model only when that model wrote it.
        kept, superseded = _probe_records(mine)
        records = []
        for path, (sha, event) in kept.items():
            if not Path(path).is_file():
                dropped.append({'path':path,'reason':'Observed probe record no longer exists'})
                continue
            if not unchanged(path, sha):
                return changed('Observed probe artifact changed after the tool returned')
            try:
                json.loads(Path(path).read_text(encoding='utf-8'))
            except ValueError:
                dropped.append({'path':path,'reason':'Observed probe output is not valid JSON'})
                continue
            selected.append(path)
            records.append({'name':Path(path).name,'sha256':sha,'dispatch_id':event.get('dispatch_id'),
                            'model_sha256':(event.get('argument_sha256') or {}).get('model_dir')})
        # What the assigned model got wrong, by its validation or on the device, is the diff that drives repair.
        diff = _device_diff(mine, assignment, contracts, _record_writers(accepted))
        device = [e for e in observed if e.get('connected') is True]
        replaced = {'superseded':superseded} if superseded else {}
        if stage == 'probe':
            # Only a probe of the assigned model probes it; successful runs of other models stay visible.
            probed = [path for path in selected if kept[path][1].get('ok') is True and _ran_assigned_model(kept[path][1], assignment)]
            others = [e['dispatch_id'] for e in device if e['name'] == 'probe_run' and not _ran_assigned_model(e, assignment)]
            checks.append({'name':'observed_probe_execution','passed':bool(probed),
                           **({'evidence':probed} if probed else {'reason':'No observed successful probe of the assigned '
                                                                            'model through the owned connection'}),
                           **({'other_model_runs':others} if others else {}), **replaced})
        else:
            # A device test runs the assigned model; a live execution is one too, though it writes no record to hand on.
            # Runs of other models stay visible but test nothing that was assigned.
            tested = [e for e in device if e['name'] in ('probe_run', 'interface_execute') and _ran_assigned_model(e, assignment)]
            ran = {e['dispatch_id'] for e in tested if e['name'] == 'probe_run'}
            evidence = [path for path in selected if kept[path][1].get('dispatch_id') in ran]
            executions = [e['dispatch_id'] for e in tested if e['name'] == 'interface_execute']
            others = [e['dispatch_id'] for e in device if e['name'] in ('probe_run', 'interface_execute') and e not in tested]
            checks.append({'name':'observed_device_test','passed':bool(evidence or executions),
                           **({'evidence':evidence} if evidence else {}), **({'executions':executions} if executions else {}),
                           **({'other_model_runs':others} if others else {}), **replaced,
                           **({} if evidence or executions else {'reason':'No observed successful device test of the assigned '
                                                                         'model through the owned connection'})})
            captures = [e for e in mine if e['name'] == 'probe_observe' and type(e.get('result',{}).get('refuted')) is int]
            if captures:
                refuted = [line for e in captures for line in e['result'].get('lines') or [] if isinstance(line, str) and line.startswith('refuted')]
                checks.append({'name':'supplied_capture_observations','passed':not refuted,'dispatch_ids':[e['dispatch_id'] for e in captures],
                               'basis':'A decoded capture supplied by the worker, not a device observation by the configurator; '
                                       'kept as a note, never a repair trigger', **({'refuted':refuted} if refuted else {})})
        return {'artifacts': selected, 'checks': checks, 'dropped': dropped, 'records': records, 'device_diff': diff}

    def _accept(self, run_id, assignment, checked, run_dir, workspace, progress, notes=None, *, revise=None, recovery=None):
        """Accept what the stage observed, even nothing, with hashed notes. A revise (its reason) commits the
        new revision, the route to interpret and the run.revision event in the same transaction. A verified emulator
        `recovery` of the provided device commits there too: the run's device becomes the replacement (its session,
        binding and receipt), the lease moves from the stopped emulator's binding to the replacement's, and the
        outstanding effect it replaced is cleared."""
        accepted_dir = run_dir / 'accepted' / str(assignment.get('revision',0)) / assignment['stage'] / assignment['id']
        artifacts = []
        for index, raw in enumerate(checked.get('artifacts', [])):
            source = Path(raw).resolve()
            if not source.is_relative_to(workspace) and not source.is_relative_to(run_dir):
                raise ValueError('Artifact is outside the assigned workspace and run directory')
            expected = digest(source)
            target = accepted_dir / (str(index) + '-' + source.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
            if digest(target) != expected:
                raise IntegrityError('Artifact changed while copying accepted evidence')
            artifacts.append({'path': str(target), 'sha256': expected, 'kind': 'directory' if target.is_dir() else 'file'})
        handoff = {'stage': assignment['stage'], 'assignment_id': assignment['id'], 'revision':assignment.get('revision',0),
                   'route':'interpret' if revise else checked.get('route'), 'artifacts': artifacts,
                   'checks': checked.get('checks', []), 'validation_scope': checked.get('validation_scope', 'Case evaluator'),
                   'accepted_at': time.time()}
        if notes is not None:
            notes = json.loads(_json(notes))
            encoded = _notes_bytes(notes)
            path = accepted_dir / 'notes.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded)
            recorded = hashlib.sha256(encoded).hexdigest()
            if digest(path) != recorded:
                raise IntegrityError('Notes changed while recording accepted evidence')
            handoff['notes'] = {'path': str(path), 'sha256': recorded, 'inline': notes}
        saved = progress
        if recovery:
            if not progress.get('device'):
                raise SafetyError('An emulator recovery commits only on a device the bench harness provides')
            # The replacement is the run's device from this commit on: its session, its binding and the receipt.
            receipt = recovery['receipt']
            saved = {**progress, 'device': {'session_id': receipt['new_session_id'], 'binding': receipt['binding'],
                                            'receipts': [*progress['device'].get('receipts', []), receipt]}}
        with self._db() as db:
            active = db.execute('SELECT state FROM assignments WHERE id=? AND run_id=?', (assignment['id'],run_id)).fetchone()
            prior = db.execute('SELECT payload FROM handoffs WHERE run_id=? AND stage=?', (run_id,assignment['stage'])).fetchall()
            duplicate = any(json.loads(p['payload']).get('revision',0)==assignment.get('revision',0) for p in prior)
            if not active or active['state'] != 'active' or duplicate:
                raise IntegrityError('Stale or duplicate handoff rejected')
            if db.execute('SELECT cancelled FROM runs WHERE id=?',(run_id,)).fetchone()['cancelled']:
                raise ValueError('Cancelled assignment cannot produce an accepted handoff')
            if recovery:
                if self._closing:
                    raise RuntimeError('Recovery cancelled before durable reconciliation')
                if progress['device'].get('binding'):
                    db.execute('DELETE FROM bindings WHERE identity=? AND run_id=?', (_lease(progress['device']['binding']), run_id))
                self._claim_binding(recovery['receipt']['binding'], run_id, db)
                db.execute('DELETE FROM pending_effects WHERE run_id=?', (run_id,))
                db.execute('UPDATE runs SET uncertain=0 WHERE id=?', (run_id,))
            db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)', (assignment['id'],run_id,assignment['stage'],_json(handoff),time.time()))
            db.execute('UPDATE assignments SET state=? WHERE id=?', ('accepted', assignment['id']))
            if revise:
                db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'",(run_id,))
                db.execute('UPDATE runs SET stage=? WHERE id=?',(progress['next_stage'],run_id))
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(run_id,_json(saved)))
            self._event(run_id, 'stage.accepted', handoff, db)
            if recovery:
                self._event(run_id, 'emulator.recovered', {**recovery, 'revision': progress['revision'],
                                                           'repairs': progress['repairs']}, db)
            if revise:
                self._event(run_id,'run.revision', {**saved,'reason':revise},db)
        progress.update(saved)
        return handoff

    def _skip(self, run_id, stage, attempt, revision, run_dir, workspace, progress, reason, *, checks=(), device_diff=None,
              outcome=None):
        """A stage with nothing to work on runs no worker; it hands off empty with the reason as a note. The checks and
        device diff its preparation found are notes too, and a revise its outcome takes commits with the handoff."""
        outcome = outcome or Outcome('advance', reason)
        assignment = {'id': attempt, 'stage': stage, 'workspace': str(workspace), 'inputs': {}, 'allowed_tools': [],
                      'report_required': False, 'binding': None, 'effects': [], 'revision': revision, 'skipped': reason}
        with self._db() as db:
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)', (attempt, run_id, stage, 'active', _json(assignment), time.time()))
            self._event(run_id, 'stage.skipped', {'stage': stage, 'assignment_id': attempt, 'revision': revision, 'reason': reason}, db)
        self._state(run_id, 'running', stage=stage)
        checked = {'artifacts': [], 'checks': list(checks), 'validation_scope': 'No worker ran: ' + reason,
                   'notes': {'checks': list(checks)}, **({'device_diff': device_diff} if device_diff else {})}
        notes = _notes({'status': 'skipped', 'reason': reason}, checked, outcome, missing=[reason])
        return self._accept(run_id, assignment, checked, run_dir, workspace, progress, notes,
                            revise=outcome.reason if outcome.kind == 'revise' else None)

    def _unwired_model(self, run_dir, attempt, unwired):
        """The configurator's own finding about an interpreted model the device binding cannot carry, so that no device
        runs it: its validation of a copy of the model, as `_observe_device` validates an assigned model, and the wiring
        error, as one `model_contract` device-diff entry, with their checks. Nothing here touched the device."""
        from .toolkit import call_tool
        source, expected = Path(unwired['path']), unwired['sha256']
        scratch = run_dir / 'checks' / attempt; scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch, ignore_cleanup_errors=True) as copied:
            model = Path(copied) / 'model'; model.mkdir()
            shutil.copyfile(source, model / 'model.json')
            if digest(model / 'model.json') != expected:
                raise IntegrityError('Accepted interpretation bytes changed: ' + source.name)
            validation = _replaced(call_tool('model_validate', {'run_dir': str(scratch), 'model_dir': str(model)}),
                                   [(str(model), 'model')])
        defects = validation.get('defects') if validation.get('ok') is not True and isinstance(validation.get('defects'), list) else []
        found = {'name': 'model.json', 'sha256': expected, 'passed': validation.get('ok') is True,
                 **({'defects': defects} if defects else {}),
                 **({'reason': 'Validation did not run: ' + str(validation['error'])} if validation.get('error') else {})}
        wiring = {'where': 'channel', 'what': unwired['reason']}
        return {'checks': [{'name': 'executable_model_contract', 'passed': found['passed'], 'models': [found]},
                           {'name': 'emulator_wiring', 'passed': False, 'reason': unwired['reason']}],
                'device_diff': [{'source': 'model_contract', 'model': {'name': 'model.json', 'sha256': expected},
                                 'defects': [*defects, wiring]}]}

    def _binding_state(self, run_id, until=None):
        """Per-run serial facts, from the run's events up to sequence number `until` when given: whether the device was
        listened to, whether opening it resets it, and whether, with no listen succeeding, one failed outside the
        worker's own arguments (`unheard`): it was dispatched and never recorded a result, ended in an error or a
        declared fault, or the gateway refused it for an operator or host fault (no port matches the bound device). The
        listen tool's refusal of the worker's own seconds or baudrate declares neither, so it is the worker's to fix."""
        listened = resets = overridden = failed = False
        pending = set()
        with self._db() as db:
            rows = db.execute("SELECT seq,kind,payload FROM events WHERE run_id=? AND kind IN "
                              "('tool.started','tool.finished','tool.refused','operator.response')"
                              + (' AND seq<=?' if until is not None else '') + ' ORDER BY seq',
                              (run_id,) + ((until,) if until is not None else ())).fetchall()
        for row in rows:
            data = json.loads(row['payload'])
            listen = data.get('name') == 'acquire_serial_listen'
            if row['kind'] == 'tool.started' and listen:
                pending.add(str(row['seq']))
            elif row['kind'] == 'tool.finished' and listen:
                pending.discard(str(data.get('dispatch_id')))
                result = data.get('result') if isinstance(data.get('result'), dict) else {}
                if data.get('ok'):
                    listened = True
                    resets = resets or (not overridden and bool(result.get('unsolicited_output')))
                elif 'error' in result or result.get('fault') in ('operator', 'host'):
                    failed = True
            elif row['kind'] == 'tool.refused' and listen and data.get('fault') in ('operator', 'host'):
                failed = True
            elif row['kind'] == 'operator.response':
                observed = data.get('observation') or {}
                if observed.get('binding_property') == 'resets_on_open' and observed.get('value') is False:
                    resets, overridden = False, True
        return {'listened': listened, 'resets_on_open': resets, 'unheard': not listened and (failed or bool(pending))}

    def _tool(self, method, params, run):
        """The worker gateway. A refusal before dispatch is recorded so that stage's notes can name it."""
        marker = {}
        try:
            return self._dispatch(method, params, run, marker)
        except Exception as error:
            if method == 'tool' and 'assignment' in marker and 'dispatched' not in marker:
                # A refusal the runtime owns (no port matches the bound device) names its fault.
                fault = getattr(error, 'fault', None)
                self._event(run['id'], 'tool.refused', {'stage': marker['assignment']['stage'],
                    'assignment_id': marker['assignment']['id'], 'name': params.get('name'), 'reason': str(error),
                    **({'fault': fault} if fault in ('operator', 'host') else {}), 'actor': 'worker'})
            raise

    def _dispatch(self, method, params, run, marker):
        from .toolkit import list_tools, call_tool
        if self._closing:
            raise ValueError('Configurator is stopping; no new tools may be dispatched')
        with self._db() as db:
            assigned = db.execute('SELECT * FROM assignments WHERE id=? AND run_id=?',
                                  (params.get('assignment_id'), run['id'])).fetchone()
        if not assigned or assigned['state'] != 'active' or run['status'] != 'running' or run['cancelled']:
            raise ValueError('Worker assignment is inactive or unknown')
        assignment = json.loads(assigned['payload'])
        with self._db() as db:
            saved=db.execute('SELECT payload FROM progress WHERE run_id=?',(run['id'],)).fetchone()
        progress=json.loads(saved['payload']) if saved else {}
        if assignment['stage'] != run['stage'] or assignment.get('revision',0) != progress.get('revision',0):
            raise ValueError('Worker assignment is inactive after stage or revision change')
        marker['assignment'] = assignment
        allowed=assignment['allowed_tools']
        contracts = [copy.deepcopy(t) for t in list_tools() if t['name'] in allowed]
        if 'benchmark_package_execute' in assignment['allowed_tools']:
            contracts.append(copy.deepcopy(BENCHMARK_PACKAGE_EXECUTE))
        for contract in contracts:
            schema = contract['inputSchema']
            for bound in ('run_dir','binding','allow_effects','bus_url','port','allow_reset'):
                schema.get('properties', {}).pop(bound, None)
                if bound in schema.get('required', []):
                    schema['required'].remove(bound)
        if method == 'tools':
            return {'ok': True, 'tools': contracts}
        name = params.get('name')
        if name not in [c['name'] for c in contracts]:
            raise ValueError('Tool is not assigned to this worker')
        arguments = dict(params.get('arguments', {}))
        if {'run_dir','binding','allow_effects','bus_url','allow_reset','port'} & arguments.keys():
            raise ValueError('Connection, output directory and effect grants are configurator-owned')
        workspace = Path(assignment['workspace'])
        from .toolkit import _PATH_ARGUMENTS
        for key in _PATH_ARGUMENTS & arguments.keys():
            value = arguments[key]
            if value is None:
                continue
            path = Path(value).resolve()
            if not path.is_relative_to(workspace):
                raise ValueError('Tool path is outside this stage workspace: ' + key)
        if 'inputs' in arguments:
            if not isinstance(arguments['inputs'],dict) or any(not isinstance(p,str) or not Path(p).is_absolute() or not Path(p).resolve().is_relative_to(workspace) for p in arguments['inputs'].values()):
                raise ValueError('Every tool input must be inside this stage workspace')
        binding = assignment.get('binding') or {}
        serial_binding = _is_serial_binding(binding)
        serial_tool = name in ('acquire_serial_listen','acquire_flash') or (name == 'acquire_classify' and serial_binding)
        model_tool = name in ('probe_run','interface_execute','emit_test_live') and not arguments.get('replay')
        if name in ('acquire_serial_listen','acquire_flash') and not serial_binding:
            raise ValueError('No operator-owned serial binding for this stage')
        if serial_binding and (serial_tool or model_tool):
            model_channel = _model_channel(name, arguments) if model_tool else None
            granted = 'reset' in assignment['effects']
            state = self._binding_state(run['id'])
            if name != 'acquire_serial_listen' and not state['listened'] and not granted:
                raise ValueError('Listen to the bound serial device first, or supply a reset grant')
            if state['resets_on_open'] and not granted:
                raise ValueError('The bound device resets when opened; this connection requires a reset grant')
            if model_channel is not None and state['resets_on_open'] and not model_channel.get('resets_on_open'):
                if model_channel.get('type') == 'uart':
                    raise ValueError('The model omits resets_on_open although this run observed the device reset on open')
                raise ValueError('The model channel is not declared as resetting although this run observed the device reset on open')
            if serial_tool:
                from interface_runtime.transports import resolve_serial_port
                arguments['port'] = resolve_serial_port(binding)
                if name != 'acquire_serial_listen':
                    arguments['allow_reset'] = granted
        arguments['run_dir'] = str(workspace / 'tools')
        connected = name in ('probe_run','interface_execute','emit_test_live','benchmark_package_execute') and not arguments.get('replay')
        if connected:
            if not assignment.get('binding'):
                raise ValueError('No operator-owned device binding for this stage')
            arguments['binding'] = assignment['binding']
            arguments['allow_effects'] = assignment['effects']
        effects=operation_effects(name,arguments) if connected else set()
        if effects-{'read'}-set(assignment['effects']):
            raise ValueError('Operation requires an effect grant the operator has not supplied')
        effectful=bool(effects & {'write','actuate'})
        if effectful and name in ('probe_run','interface_execute'):
            # Refused here, never marked uncertain: the runtime would refuse it before opening the device.
            _refuse_invalid_model(arguments['model_dir'])
        spec = json.loads(run['spec'])
        device = invoke = None
        if connected and _emulated(spec) and not spec.get('device'):
            # The configurator holds no emulator: an emulated case runs only on the device the bench harness provides.
            raise ValueError('An emulated case runs only on the device the bench harness provides')
        if connected and spec.get('device'):
            # A device the bench harness provides. Everything refusable is checked before the dispatch is marked: the
            # binding is the one the run recorded, and a package call runs only the unchanged stage-assigned copy.
            from .device_client import open_device
            if arguments['binding'] != (progress.get('device') or {}).get('binding'):
                raise ValueError('Assigned binding differs from the provided device')
            if name == 'benchmark_package_execute':
                from .benchmark import package_call
                invoke = package_call(spec['_case_pin']['case_id'], self.home / 'runs' / run['id'], assignment['stage'], arguments)
            else:
                invoke = lambda: call_tool(name, arguments)
            device = open_device(spec['device'])
        # Facts the device diff rests on, recorded by the configurator, never taken from the worker: whether it
        # supplied the device connection (a replay gets none), and the content of every evidence path the tool reads.
        facts = {'connected': connected, 'argument_sha256': _argument_digests(arguments)}
        # The uncertain flag is persisted before dispatch. A crash or cancellation
        # cannot turn an outstanding physical effect into an automatic retry.
        with self._lock:
            with self._db() as db:
                active = db.execute('SELECT status,stage,cancelled,uncertain FROM runs WHERE id=?',(run['id'],)).fetchone()
                # The assignment is checked again where the dispatch is marked: one whose stage was accepted, superseded
                # or left since the call arrived dispatches nothing, so no call runs outside the stage that observes it.
                current = db.execute('SELECT state FROM assignments WHERE id=? AND run_id=?', (assignment['id'], run['id'])).fetchone()
                saved = db.execute('SELECT payload FROM progress WHERE run_id=?', (run['id'],)).fetchone()
                if (active['cancelled'] or active['status'] != 'running' or not current or current['state'] != 'active'
                        or assignment['stage'] != active['stage']
                        or assignment.get('revision', 0) != (json.loads(saved['payload']) if saved else {}).get('revision', 0)):
                    raise ValueError('Worker assignment became inactive before dispatch')
                if effectful and active['uncertain']:
                    raise ValueError('Outstanding effect is uncertain; another effectful operation is refused until operator reconciliation')
                if effectful:
                    db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run['id'],))
                # The facts go on the start too: a dispatch that raises before it finishes is still known to the verdict.
                dispatch_id = self._event(run['id'], 'tool.started', {'stage':assignment['stage'],'assignment_id':assignment['id'],'name':name,'effectful':effectful,
                    **facts, 'actor':'worker'}, db)
                if effectful:
                    db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)',(dispatch_id,run['id'],assignment['id'],'worker'))
        marker['dispatched'] = dispatch_id
        if device is not None:
            result = self._device_call(run, assignment, name, arguments, device, invoke)
        elif name == 'benchmark_package_execute':
            from .benchmark import package_execute
            case = spec.get('case')
            if not case:
                raise ValueError('Benchmark package execution requires an assigned case')
            options = case_options(spec)
            options['assignment_id'] = assignment['id']
            arguments['run_dir'] = str(self.home / 'runs' / run['id'])
            result = package_execute(case['id'] if isinstance(case,dict) else case,
                                     stage=assignment['stage'],options=options,**arguments)
        else:
            result = call_tool(name, arguments)
        observed_artifacts = {}
        for key in ('artifact','provenance','pages','pages_dir','manifest','package_dir','probe','model_dir','transcript'):
            raw = result.get(key)
            if isinstance(raw,str) and Path(raw).is_absolute():
                path = Path(raw).resolve()
                if path.is_relative_to(workspace) and path.exists():
                    observed_artifacts[str(path)] = digest(path)
        for raw in result.get('artifacts', []) if isinstance(result.get('artifacts'), list) else []:
            if isinstance(raw, str) and Path(raw).is_absolute():
                path = Path(raw).resolve()
                if path.is_relative_to(workspace) and path.exists():
                    observed_artifacts[str(path)] = digest(path)
        with self._db() as db:
            cancelled = db.execute('SELECT cancelled FROM runs WHERE id=?',(run['id'],)).fetchone()['cancelled']
            success = result.get('_exit',0)==0 and bool(result.get('ok',result.get('available',True)))
            # A trusted runtime preflight refusal never attempted to open the device, nor did a refusal the toolchain
            # declared before its first device call. Empty transcripts alone cannot establish this: opening may
            # itself reset it.
            executions = result.get('results') if name == 'probe_run' else [result]
            refused_before_open = ((name in ('interface_execute', 'probe_run', 'benchmark_package_execute')
                and type(executions) is list and bool(executions)
                and all(type(item) is dict and item.get('schema') == 'interface-result/1'
                        and item.get('transport_open_attempted') is False for item in executions))
                or (name in ('interface_execute', 'probe_run', 'emit_test_live') and _declared_refusal(result)))
            # A host fault the tool declared before its first device call refused nothing, but it reached no device
            # either, so it leaves no effect to reconcile.
            before_contact = refused_before_open or (name in ('interface_execute', 'probe_run', 'emit_test_live')
                                                     and _before_contact(result))
            effects_reconciled = bool(effectful and not cancelled and (success or before_contact))
            if effects_reconciled:
                db.execute('DELETE FROM pending_effects WHERE dispatch_id=?',(dispatch_id,))
                db.execute('UPDATE runs SET uncertain=EXISTS(SELECT 1 FROM pending_effects WHERE run_id=?) WHERE id=?',(run['id'],run['id']))
            evidence_id = self._event(run['id'], 'tool.finished', {'stage':assignment['stage'],'assignment_id':assignment['id'],'name':name,'ok':success,'result':result,'artifacts':observed_artifacts,
                'dispatch_id':str(dispatch_id),'effectful':effectful,'effects_reconciled':effects_reconciled,
                'refused_before_open':refused_before_open,**facts,'actor':'worker'}, db)
        if name == 'benchmark_package_execute':
            result = {**result, 'evidence_id': str(evidence_id)}
        return result

    def close(self, timeout=10):
        with self._lock:
            self._closing = True
            workers = list(self._workers.values())
        deadline = time.monotonic() + timeout
        for cancel, thread in workers:
            cancel.set()
        for cancel, thread in workers:
            thread.join(max(0, deadline - time.monotonic()))
        return not any(thread.is_alive() for _, thread in workers)
