"""Durable configurator. Worker statements, accepted evidence and verdicts stay distinct."""
import hashlib
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

from .agents import StageRequest, execute

STAGES = ('acquire', 'interpret', 'probe', 'ground', 'emit', 'reuse')
TERMINAL = {'completed', 'failed', 'cancelled', 'blocked'}
# Runs record their protocol; a run saved without this label used the earlier gated stage progression.
PROTOCOL = 'assistive/1'
# Generic stage inputs, each taken from the latest accepted handoff of its stage that has artifacts.
STAGE_INPUTS = {'probe': (('interpret', 'interpreted model'),),
                'ground': (('interpret', 'interpreted model'), ('probe', 'probe records')),
                'emit': (('interpret', 'interpreted model'), ('probe', 'probe records'))}
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
    if spec.get('_snapshot_sha256'):
        options['_execution_run_dir'] = spec['_execution_run_dir']
    if spec.get('binding'):
        options['binding']=spec['binding']
    return {**options,'revision':spec.get('_revision',0),'feedback':spec.get('_feedback'),
        'operator_observations':spec.get('_operator_observations',[]),'configured_effects':spec['effects']}


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


def _is_serial_binding(binding):
    """A serial binding names a port string or a USB vendor/product identity."""
    if not isinstance(binding, dict):
        return False
    if isinstance(binding.get('port'), str) and binding['port'].strip():
        return True
    return type(binding.get('vendor_id')) is int and type(binding.get('product_id')) is int


def _model_channel(name, arguments):
    model_dir = arguments.get('model_dir')
    path = Path(model_dir)/'model.json' if model_dir else Path(arguments['package_dir'])/'driver/model.json'
    return json.loads(path.read_text(encoding='utf-8')).get('channel', {})


def operation_effects(name, arguments):
    """Read declared operation effects before opening an operator-owned binding."""
    model_dir=arguments.get('model_dir')
    if model_dir:
        model_path=Path(model_dir)/'model.json'
    else:
        model_path=Path(arguments['package_dir'])/'driver/model.json'
    model=json.loads(model_path.read_text(encoding='utf-8'))
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
    """Refuse an interface model that fails validation, which the runtime refuses before it opens the device. The
    gateway calls this before it marks an effectful dispatch uncertain, so such a refusal leaves no effect to
    reconcile. A register model has no such runtime check: its probe runs it on the bus."""
    from interface_runtime.engine import SCHEMAS, validate_model
    model = json.loads((Path(model_dir) / 'model.json').read_text(encoding='utf-8'))
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


def _adapter_verdict(stage, checked, repairs, max_repairs):
    """Benchmark adapters still return truth-graded verdicts with their legacy categories until Part B."""
    if checked.get('ok', True):
        raise ValueError('Unsupported evaluator revision route')
    if checked.get('route') == 'interpret' and checked.get('fault') == 'model' and stage in ('interpret', 'probe', 'ground'):
        if not checked.get('revision_committed') and repairs >= max_repairs:
            return Outcome('stop', 'Model repair budget exhausted: ' + checked.get('reason', 'checks failed'), 'model')
        return Outcome('revise', checked.get('reason'), diff=checked.get('feedback'))
    fault = checked.get('fault')
    return Outcome('stop', checked.get('reason', 'Independent stage checks failed'),
                   fault if fault in ('model', 'host', 'operator') else 'unknown')


def _outcome(stage, result, checked, *, uncertain, repairs, max_repairs):
    """Decide one stage. Worker report status, failed or not_run checks and missing artifacts never stop."""
    stop = _runtime_stop(result) or checked.get('stop') or (uncertain and {'category': 'safety', 'reason':
        checked.get('reason') or 'Outstanding effect is uncertain; operator reconciliation is required before further execution'})
    if stop:
        return Outcome('stop', stop['reason'], stop['category'])
    if checked.get('ok') is False or checked.get('route'):
        return _adapter_verdict(stage, checked, repairs, max_repairs)
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


def _declared_refusal(result):
    """Whether a tool declared that it refused before it contacted the device: its own result, with no execution
    record, says no transport was opened (the runtime's interface-result/1 does the same for one execution)."""
    return result.get('transport_open_attempted') is False and not _listed(result.get('results'))


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
    - 'inconclusive': anything else that did not succeed, such as an operator, host or transport fault (exit 4), or
      a toolchain error the tool did not declare as a refusal, which may have come after the device was contacted
      (labelled a host fault only when it names one, `_host_fault`). It says nothing about the driver.

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
        code = first.get('error_code') or error.get('code')
        return 'inconclusive', str((fault(first) if failed else result.get('fault')) or 'unclassified') + ' fault' + (
            ' (' + str(code) + ')' if code else '')
    if isinstance(result.get('output'), list):
        # A register package's live suite ran on the device; failing checks are the driver's.
        return ('failed', None) if isinstance(result.get('failed'), int) and result['failed'] > 0 else (
            'inconclusive', 'live suite exited with an error')
    # The tool raised without declaring a refusal: say whether that names a host failure, never guess one.
    return 'inconclusive', ('host fault' if _host_fault(result) else 'undeclared toolchain error') + (
        ' (' + _toolchain_fault(result) + ')')


def _frozen_package(package):
    """What judging a run of a frozen package needs, read from its bytes: its content digest, its manifest's digest
    (which the snapshot emit_test_live executes carries), its driver directory's digest (which interface_execute runs
    as a model), its identity operation and each operation's declared effect (absent is actuate, as the gateway reads
    it). A part that cannot be read is None."""
    root = Path(package['path'])
    def read(load):
        try:
            return load()
        except (OSError, ValueError, RecursionError):
            return None
    model = read(lambda: json.loads((root / 'driver' / 'model.json').read_text(encoding='utf-8')))
    identity = model.get('identity') if isinstance(model, dict) else None
    operations = model.get('operations') if isinstance(model, dict) else None
    effects = ({name: op.get('effect', 'actuate') if isinstance(op, dict) else None for name, op in operations.items()}
               if isinstance(operations, dict) and model.get('schema') != 'interface-model/1' else None)
    return {'sha256': package['sha256'], 'manifest_sha256': read(lambda: digest(root / 'manifest.json')),
            'driver_sha256': read(lambda: digest(root / 'driver')),
            'identity': identity.get('operation') if isinstance(identity, dict) else None, 'effects': effects}


NEVER_RAN = 'fresh reuse never ran the frozen package with emit_test_live'
IDENTITY_ONLY = ('fresh reuse ran no operation of the frozen package beyond its identity operation with emit_test_live; '
                 'identity alone is not evidence of function')
UNGRANTED = ("the operator's effect grants permit no operation of the frozen package beyond its identity operation, so "
             'fresh reuse could not test a function of it; that limit says nothing about the driver')


def _ungranted(package, grants):
    """Whether the operator's effect grants (`grants`, or None when not known) permit no operation of the frozen package
    beyond its identity operation, although it has one: then the gateway refuses every function of it."""
    if package is None or grants is None or package.get('identity') is None or not isinstance(package.get('effects'), dict):
        return False
    functions = [effect for name, effect in package['effects'].items() if name != package['identity']]
    return bool(functions) and not any(effect in ('read', *grants) for effect in functions)


def _fresh_reuse(package, events, started=(), grants=None):
    """Judge fresh reuse's live operations of a frozen package (`_frozen_package`, or None when there is none), from
    the configurator's own records: `events` are tool.finished payloads and `started` tool.started payloads with
    their dispatch ids; `grants` are the run's operator effect grants. The final verdict and each reuse attempt's
    notes use this one rule.

    - It passes when every live operation succeeded (a call refused before the device was contacted is not one) and
      at least one emit_test_live ran the frozen package and an operation of it beyond its identity operation on the
      device: identity alone is not evidence of function. The frozen package ran when the configurator hashed those
      bytes before dispatch and the snapshot the tool executed carries the frozen manifest.
    - It fails when the driver failed on the device (a failure is the driver's only when the operation ran the frozen
      bytes: the package for emit_test_live, or its driver directory for interface_execute), or when no such
      operation ran.
    - It is unsettled (None) when a live operation was inconclusive (an operator, host or transport fault, or a
      failure of other bytes) or was dispatched without a recorded result, or when no function ran because the
      operator's grants permit none beyond identity: the gateway refuses those calls, which says nothing of the driver.

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
        """Whether the call ran an operation beyond the identity operation on the device: True, False, or None when
        its result does not say (it recorded no execution, or stopped at a fault before such an operation)."""
        result = _result_of(event)
        if isinstance(result.get('output'), list):
            return True  # a register package's live suite reads its identity, then measures
        executions = _listed(result.get('results'))
        other = lambda name: package['identity'] is not None and name != package['identity']
        if any(other(r.get('operation')) and r.get('transport_open_attempted') is not False for r in executions):
            return True
        pending = [name for name in result.get('not_run') if other(name)] if isinstance(result.get('not_run'), list) else []
        return None if pending or not executions else False
    def functional(event, outcome):
        if not dispatched(event) or outcome == 'refused':
            return False
        if outcome in ('inconclusive', 'unresolved'):
            # It stopped at a fault: it may have run a function of the frozen package, unless it plainly ran other
            # bytes or only the identity operation.
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
    ungranted = not exercised and _ungranted(package, grants)
    check = {'id': 'fresh_reuse_live_operations',
             'passed': False if failed else None if ungranted else False if not exercised else None if unsettled else True,
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
        diff.append({'id': check['id'], 'reason': UNGRANTED if ungranted else IDENTITY_ONLY if ran else NEVER_RAN + (
            ' (' + str(refused) + ' call' + ('s' if refused > 1 else '') + ' refused before the device was contacted)' if refused else '')})
    return check, diff


def _self_evidence(accepted, events, started=(), assignments=(), grants=None):
    """A generic run's final verdict, from the configurator's own records only.

    `events` are tool.finished payloads; `started` are tool.started payloads with their dispatch ids; `assignments`
    are the run's reuse assignments as {id, inputs}, with the input hashes the configurator recorded; `grants` are the
    run's operator effect grants.

    - **Equivalent:** the emitted package passed emit_check replay of the recorded device exchanges, which the
      configurator ran at emit, and fresh reuse passed `_fresh_reuse`: every live operation succeeded, and at least
      one ran the frozen package with emit_test_live (the bytes the configurator hashed before dispatch, executed as
      a snapshot of the frozen manifest) and an operation of it beyond its identity operation, on the connection the
      configurator supplied.
    - **Not equivalent:** no single package passed emit_check replay of device exchanges (a seeded package-versus-model
      check, or a replay of exchanges the worker supplied, is not one), the driver failed on the device, or fresh reuse
      never ran a function of the package beyond its identity with emit_test_live.
    - **Unscored (None):** otherwise, when a live operation was inconclusive (an operator, host or transport fault, or
      a failure of bytes other than the emitted driver) or was dispatched without a recorded result, or when the
      operator's grants permit no function of the package beyond its identity, so none could run.

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
    operations, live = _fresh_reuse(_frozen_package(packages[0]) if frozen else None,
                                    [e for e in events if mine(e)], [e for e in started if mine(e)], grants)
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
            reason = ('no driver emitted' if not candidates else
                      'the emitted package did not pass emit_check' + (': ' + why if why else '') if candidates == 1 else
                      'none of the ' + str(candidates) + ' emitted packages passed emit_check')
        diff.append({'id': replay['id'], 'reason': reason})
    equivalent = False if not replayed else operations['passed']
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
    """Only the gated protocol's terminal shapes: the progress flag, or a frozen final evaluation that failed for the
    model, which benchmark adapters still produce inside reuse until Part B. The final verdict (`<run_id>:final`)
    ends a completed run whatever it says, so it is never one of them."""
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
        self._suite_threads = {}
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
                    if not spec.get('case') and progress['next_stage'] == 'finalize':
                        # Reuse was accepted and finalize dispatches nothing: only a recorded pending effect is outstanding.
                        uncertain = row['uncertain']
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
                    # A run completed from its final verdict keeps that status; there is nothing left to cancel.
                    requested = db.execute("UPDATE runs SET cancelled=1 WHERE id=? AND NOT (status='completed' "
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
        if method == 'resume':
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
                # A generic run stopped before its verdict only computes it: finalize reads the configurator's records,
                # runs no stage and contacts no device, so neither the budget nor the device lease applies.
                finalizing = not spec.get('case') and progress.get('next_stage') == 'finalize'
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

    def suite_start(self,params):
        from .benchmark_support.suites import normalize_manifest,freeze_suite
        from .benchmark_support.snapshots import execution_provenance
        manifest=normalize_manifest(params.get('manifest'))
        executor=params.get('executor','codex')
        options=params.get('options',{})
        request={'manifest':manifest,'executor':executor,'options':options}
        with self._lock:
            saved=self._suites.find_request(params.get('request_id'),request)
            if saved is not None:
                return {'ok':True,'suite_id':saved['suite_id'],'duplicate':True}
            if self._closing:raise ValueError('Configurator is stopping')
            config_path=self.home/'config.json'
            config=json.loads(config_path.read_text(encoding='utf-8')).get('executors',{}).get(executor,{}) if config_path.is_file() else {}
            config={**config,'runtime':executor}
            frozen=freeze_suite(manifest,executor,config,options,execution_provenance(config))
            saved=self._suites.create(frozen,params.get('request_id'))
            self._suites.set_state(saved['suite_id'],'running')
            self._spawn_suite(saved['suite_id'])
            return {'ok':True,'suite_id':saved['suite_id'],'duplicate':False}

    def _suite_lock(self, suite_id):
        with self._lock:
            return self._suite_locks.setdefault(suite_id, threading.RLock())

    def _spawn_suite(self, suite_id):
        with self._lock:
            if self._closing:
                raise ValueError('Configurator is stopping')
            if suite_id in self._suite_threads:
                return
            wake = threading.Event()
            thread = threading.Thread(target=self._run_suite, args=(suite_id,wake), daemon=True)
            self._suite_threads[suite_id] = (wake,thread)
            thread.start()

    def _run_suite(self, suite_id, wake):
        from .benchmark_support.suites import child_request, child_disposition
        try:
            while not self._closing:
                with self._suite_lock(suite_id):
                    suite = self._suites.get(suite_id)
                    if suite['cancelled'] or suite['status'] != 'running':
                        return
                    if time.time() >= suite['created'] + suite['budget_seconds']:
                        active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
                        child = active['run_id'] if active else None
                        self._suites.set_state(suite_id,'blocked','Suite wall-clock budget exhausted')
                        expired = True
                    else:
                        expired = False
                    if expired:
                        break
                    trial = self._suites.reserve(suite_id)
                    if trial is None:
                        self._suites.set_state(suite_id,'completed')
                        return
                    if trial['run_id'] is None:
                        started = self.start(child_request(suite['frozen'],trial))
                        self._suites.attach(suite_id,trial['ordinal'],trial['child_request_id'],started['run_id'])
                        trial['run_id'] = started['run_id']
                    observed = self.call('status',{'run_id':trial['run_id']})
                    decision = child_disposition(observed)
                    if decision == 'advance':
                        self._suites.settle(suite_id,trial['ordinal'],observed)
                        continue
                    if decision == 'block':
                        self._suites.set_state(suite_id,'blocked',observed.get('reason'))
                        return
                wake.wait(.1)
                wake.clear()
            if not self._closing and expired and child:
                # Cancellation can join a worker; never hold the suite lifecycle lock.
                self.call('cancel',{'run_id':child})
        except Exception as error:
            with self._suite_lock(suite_id):
                if not self._suites.get(suite_id)['cancelled']:
                    self._suites.set_state(suite_id,'blocked',str(error))
        finally:
            with self._lock:
                self._suite_threads.pop(suite_id,None)

    def find_request(self, request_id):
        """Reconcile an already created child without creating or resuming work."""
        with self._db() as db:
            row = db.execute('SELECT id FROM runs WHERE request_id=?',(request_id,)).fetchone()
        return self.call('status',{'run_id':row['id']}) if row else None

    def _suite_cancel(self, suite_id):
        deadline = time.monotonic()+10
        with self._suite_lock(suite_id):
            self._suites.cancel(suite_id)
            suite = self._suites.get(suite_id)
            active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
            child = active['run_id'] if active else None
            if active and not child:
                found = self.find_request(active['child_request_id'])
                if found:
                    child = found['run_id']
                    self._suites.attach(suite_id,active['ordinal'],active['child_request_id'],child)
        with self._lock:
            scheduler = self._suite_threads.get(suite_id)
        if scheduler:
            scheduler[0].set()
        if child:
            self.call('cancel',{'run_id':child})
        if scheduler:
            scheduler[1].join(max(0,deadline-time.monotonic()))
        return self._suite_call('suite_status',{'suite_id':suite_id})

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
                self._verify_snapshot(spec,self.home/'runs'/found['run_id'])

    def _suite_resume(self, suite_id, params):
        with self._suite_lock(suite_id):
            suite = self._suites.get(suite_id)
            if suite['status'] not in ('blocked','failed','cancelled'):
                raise ValueError('Only a stopped incomplete suite can resume')
            if self._suite_call('suite_status',{'suite_id':suite_id})['stopping']:
                raise ValueError('Previous suite work is still stopping')
            self._verify_suite(suite)
            if params.get('suite_budget_seconds') is not None:
                self._suites.extend_budget(suite_id,params['suite_budget_seconds'],params.get('budget_reason'))
                suite = self._suites.get(suite_id)
            if time.time() >= suite['created'] + suite['budget_seconds']:
                raise ValueError('Suite budget exhausted; supply an explicitly approved larger total budget')
            active = next((t for t in suite['trials'] if t['status'] in ('launching','running')),None)
            if active:
                found = self.find_request(active['child_request_id'])
                if found:
                    self._suites.attach(suite_id,active['ordinal'],active['child_request_id'],found['run_id'])
                    with self._db() as db:
                        terminal_final = _has_terminal_final_failure(db,found['run_id'])
                    if (terminal_final and found['status'] in ('failed','blocked')
                            and not found['stopping'] and not found['uncertain_effect']):
                        self._state(found['run_id'],'failed','Frozen final evaluation failed',outcome_category='model')
                        self._suites.settle(suite_id,active['ordinal'],
                            self.call('status',{'run_id':found['run_id']}),terminal_final=True)
                    elif found['status'] not in ('running','queued','completed'):
                        self.call('resume',{'run_id':found['run_id']})
            self._suites.set_state(suite_id,'running')
            self._spawn_suite(suite_id)
        return self._suite_call('suite_status',{'suite_id':suite_id})

    def _suite_call(self,method,params):
        if method=='suite_start':return self.suite_start(params)
        suite_id=params.get('suite_id')
        suite=self._suites.get(suite_id)
        if method=='suite_cancel':return self._suite_cancel(suite_id)
        if method=='suite_resume':return self._suite_resume(suite_id,params)
        if method=='suite_events':return {'ok':True,**self._suites.events(suite_id,params.get('after',0))}
        active=next((trial for trial in suite['trials'] if trial['status'] in ('launching','running')),None)
        child = self.call('status',{'run_id':active['run_id']}) if active and active['run_id'] else None
        status={'ok':True,'suite_id':suite_id,**{key:suite[key] for key in ('status','created','updated')},
            'reason':None if suite['reason'] is None else 'Explicit suite resolution required',
            'active_child_id':active['run_id'] if active else None,
            'planned':len(suite['trials']),'completed':sum(trial['status']=='finished' for trial in suite['trials']),
            'stopping':(suite['status'] in TERMINAL and suite_id in self._suite_threads) or bool(child and child['stopping'])}
        if method=='suite_status':return status
        if method=='suite_result':
            page = self._suites.trials(suite_id,params.get('offset',0),params.get('limit',50))
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
                    if current['status'] in ('blocked','failed','cancelled'):
                        trial['status'] = current['status']
            return {**status,**page,'experiment':self._suite_experiment(suite)}
        raise ValueError('Unknown suite method: '+str(method))

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
            'comparison_identity':{'manifest':manifest,'manifest_sha256':frozen['manifest_sha256'],
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
        executor = params.get('executor', 'codex')
        if executor not in ('codex', 'goose'):
            raise ValueError('executor must be codex or goose')
        budget = params.get('budget_seconds', 10800)
        if type(budget) not in (int, float) or not 0 < budget <= 7 * 86400:
            raise ValueError('budget_seconds must be positive and no more than seven days')
        effects = params.get('effects', ['read'])
        if not isinstance(effects, list) or not set(effects) <= {'read', 'write', 'actuate', 'reset'}:
            raise ValueError('effects must contain only read, write, actuate, reset')
        config = {}
        config_path = self.home / 'config.json'
        if 'case_pin' in params:
            if not isinstance(params.get('executor_config'),dict):
                raise ValueError('Frozen case pin requires complete executor_config')
            if not params.get('case'):
                raise ValueError('Expected case pin requires a registered case')
        elif config_path.is_file():
            config = json.loads(config_path.read_text(encoding='utf-8')).get('executors', {}).get(executor, {})
        config = {**config, **params.get('executor_config', {}), 'runtime': executor}
        spec = {**params, 'executor': executor, 'executor_config': config, 'budget_seconds': budget, 'effects': effects,
                '_submitted_intent': params}
        case=spec.get('case')
        option_bindings=[spec.get('case_options',{}).get('binding')]
        if isinstance(case,dict): option_bindings.append(case.get('options',{}).get('binding'))
        if spec.get('binding') and any(value and value!=spec['binding'] for value in option_bindings):
            raise ValueError('Operator and case options contain conflicting device bindings')
        validate_scoped_approval(spec)
        encoded = _json(spec)
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
                        from .benchmark_support.registry import require_calibration
                        require_calibration(pin, options)
                    spec['_case_pin'] = pin
                    saved = snapshot_case(self.home / 'runs' / run_id, pin, execution_provenance(config))
                    spec['_snapshot_sha256'] = saved['snapshot_sha256']
                    spec['_execution_run_dir'] = str(self.home / 'runs' / run_id)
                    encoded = _json(spec)
                self._claim_binding(spec.get('binding'),run_id,db)
                now = time.time()
                db.execute('INSERT INTO runs(id,request_id,spec,status,stage,created,updated) VALUES(?,?,?,?,?,?,?)',
                           (run_id, params.get('request_id'), encoded, 'queued', 'acquire', now, now))
                db.execute('INSERT INTO progress VALUES(?,?)',(run_id,_json({'revision':0,'next_stage':'acquire','repairs':0,
                                                                            'feedback':None,'protocol':PROTOCOL})))
                self._event(run_id, 'run.started', {'goal': goal, 'executor': executor, 'budget_seconds': budget}, db)
            self._spawn(run_id)
        return {'ok': True, 'run_id': run_id, 'duplicate': False}

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
                current_spec = {**spec, '_revision':revision, '_feedback':progress.get('feedback')}
                with self._db() as db:
                    responses=db.execute("SELECT payload FROM events WHERE run_id=? AND kind='operator.response' ORDER BY seq",(run_id,)).fetchall()
                current_spec['_operator_observations']=[value.get('observation') or {'message':value['message']} for value in (json.loads(r['payload']) for r in responses)]
                with _integrity_checked():
                    prepared = self._prepare(current_spec, stage, run_dir, workspace, accepted)
                if prepared.get('blocked'):
                    self._state(run_id,'blocked',prepared['blocked'],stage)
                    return
                if prepared.get('skip'):
                    progress['next_stage'] = queue[0] if queue else 'finalize'
                    accepted.append(self._skip(run_id, stage, attempt, revision, run_dir, workspace.resolve(), progress, prepared['skip']))
                    completion = prepared['skip'] if stage == STAGES[-1] else completion
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
                report = result.get('report') if isinstance(result.get('report'), dict) else {}
                checked = {}
                if _runtime_stop(result) is None:
                    self._verify_inputs(input_hashes)
                    with _integrity_checked():
                        if self._row(run_id)['uncertain']:
                            from .benchmark_support.emulated_recovery import reconcile
                            checked = reconcile(self,run_id,current_spec,assignment,run_dir,workspace,accepted,report,progress)
                        else:
                            checked = self._check(current_spec, stage, run_dir, workspace, report, accepted, assignment)
                    # Benchmark adapters keep their frozen final path until Part B moves grading after reuse.
                    terminal_final = (not checked.get('ok', True) and checked.get('final_evaluation') is True
                                      and checked.get('fault') == 'model' and not checked.get('route'))
                    if checked.get('evaluator') is not None or terminal_final:
                        with self._db() as db:
                            if checked.get('evaluator') is not None:
                                db.execute('INSERT INTO verdicts VALUES(?,?,?,?,?)', (attempt, run_id, stage,
                                    _json({'stage':stage, 'assignment_id':attempt, **checked['evaluator']}), time.time()))
                            if terminal_final:
                                progress['terminal_final_failure'] = True
                                db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)', (run_id, _json(progress)))
                                self._event(run_id, 'evaluation.final_failed',
                                    {'assignment_id': attempt, 'revision': revision, 'fault': 'model'}, db)
                    if terminal_final:
                        self._state(run_id, 'failed', 'Frozen final evaluation failed', outcome_category='model')
                        return
                    if cancel.is_set():
                        return
                    self._verify_inputs(input_hashes)
                outcome = _outcome(stage, result, checked, uncertain=bool(self._row(run_id)['uncertain']),
                                   repairs=progress['repairs'], max_repairs=int(spec.get('max_revisions',2)))
                if outcome.kind == 'stop':
                    self._state(run_id, result['status'] if _runtime_stop(result) else 'blocked', outcome.reason, stage,
                                outcome_category=outcome.category)
                    return
                if outcome.kind == 'revise' and (checked.get('ok') is False or checked.get('route')):
                    # Benchmark adapter verdicts keep their legacy route, without a handoff, until Part B.
                    if not checked.get('revision_committed'):
                        progress.update(revision=revision+1,next_stage='interpret',repairs=progress['repairs']+1,feedback=outcome.diff)
                        self._route(run_id,progress,outcome.reason)
                    revision = progress['revision']
                    queue = list(STAGES[1:])
                    continue
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
                accepted.append(self._accept(run_id, assignment, checked, run_dir, workspace, progress, notes,
                                             revise=outcome.reason if outcome.kind == 'revise' else None))
                if outcome.kind == 'revise':
                    revision, queue = progress['revision'], list(STAGES[1:])
            if cancel.is_set():
                return
            final = self._finalize(run_id, spec, accepted)
            if final:
                # Once recorded, the verdict completes the run even if a cancellation landed after it.
                self._state(run_id, 'completed', _verdict_reason(final['equivalence']), from_verdict=True)
            else:
                self._state(run_id, 'completed', completion)
        except Exception as exc:
            category = 'integrity' if isinstance(exc, IntegrityError) else 'safety' if isinstance(exc, SafetyError) else 'host'
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

        A generic run's verdict is self-evidence. Benchmark runs (`spec.case`) keep the frozen final path their
        adapters run inside reuse until Part B, so no evaluator is called here and nothing is recorded; Part B
        records a pending verdict at this point for the bench harness to complete."""
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
            equivalence = _self_evidence(accepted, [json.loads(row['payload']) for row in rows if row['kind'] == 'tool.finished'],
                                         [{**json.loads(row['payload']), 'dispatch_id': str(row['seq'])}
                                          for row in rows if row['kind'] == 'tool.started'],
                                         [{'id': row['id'], 'inputs': json.loads(row['payload']).get('inputs')} for row in reuses],
                                         spec.get('effects'))
        with self._db() as db:
            if db.execute('SELECT cancelled FROM runs WHERE id=?', (run_id,)).fetchone()['cancelled']:
                return None
            progress = {**self._workflow_progress(run_id, db), 'next_stage': None}
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
        """Seal a benchmark run's evidence and stop what its adapter owns. Returns the failures in the order they
        happened as (event kind, reason, event detail); a generic run owns nothing to release."""
        failures = []
        if not spec.get('case'):
            return failures
        try:
            from .benchmark import cleanup
            case=spec['case']
            options=spec.get('case_options',{})
            if isinstance(case,dict): options={**case.get('options',{}),**options}
            if spec.get('_case_pin', {}).get('manifest', {}).get('schema') == 'benchmark-case/2':
                from .benchmark_support.emulated_evidence import finalize
                try:
                    finalize(case['id'] if isinstance(case,dict) else case, run_dir,
                             self.call('result', {'run_id':run_id})['accepted_handoffs'], options)
                except Exception as error:
                    failures.append(('evidence.sealing_failed', 'Evaluator evidence sealing failed: '+str(error), type(error).__name__))
            cleanup(case['id'] if isinstance(case,dict) else case,run_dir,options)
        except Exception as exc:
            failures.append(('cleanup.failed', type(exc).__name__+': '+str(exc), None))
        return failures

    def _settle_release(self, run_id, failures):
        """Before a final verdict a release failure stops the run: a sealing failure fails it for the host, and a
        failed stop retains the lease as uncertain. After the verdict each failure is an event; the verdict and the
        completed status stand unless an effect is uncertain, which blocks for safety. This relies on the configurator
        owning no device resource once a verdict exists: generic runs own nothing, and benchmark runs record a verdict
        only from B5, after B3 has moved the emulator stop to the harness behind release()."""
        with self._db() as db:
            final = _final_verdict(db, run_id)
        for kind, reason, detail in failures:
            if kind == 'evidence.sealing_failed':
                if final is None:
                    self._state(run_id, 'failed', reason, outcome_category='host')
                self._event(run_id, kind, {'reason': detail})
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
        if final is not None and self._row(run_id)['uncertain']:
            self._state(run_id, 'blocked', UNCERTAIN_AFTER_VERDICT, outcome_category='safety')

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

    def _route(self, run_id, progress, reason, db=None):
        if db is None:
            with self._db() as connection:
                return self._route(run_id,progress,reason,connection)
        db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'",(run_id,))
        db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(run_id,_json(progress)))
        db.execute('UPDATE runs SET stage=? WHERE id=?',(progress['next_stage'],run_id))
        self._event(run_id,'run.revision', {**progress,'reason':reason},db)

    def _claim_binding(self, binding, run_id, db):
        if not binding:
            return
        identity = hashlib.sha256(_json(binding_identity(binding)).encode()).hexdigest()
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
            if spec.get('_case_pin', {}).get('manifest', {}).get('schema') == 'benchmark-case/2' and stage == 'probe':
                return self._evaluator_operation(run_dir.name, stage, None, options,
                    lambda: prepare_stage(case_id, stage, run_dir, workspace, accepted, options))
            return prepare_stage(case_id, stage, run_dir, workspace, accepted, options)
        if stage == 'interpret':
            # Interpretation that cannot start hands off empty with the reason; it never stops the run.
            from .toolkit import call_tool
            acquisition = next((h for h in reversed(accepted) if h['stage']=='acquire'),None)
            artifacts = [Path(a['path']) for a in acquisition['artifacts']] if acquisition else []
            named = [p for p in artifacts if p.name in ('interpret-input.elf', 'interpret-input.bin')]
            images = named if len(named) == 1 else [p for p in artifacts if p.suffix.lower() in ('.bin', '.elf')]
            if spec.get('front_end','interpret-interface') != 'interpret-interface':
                return {'skip':'Interpretation skipped: generic blind interpretation supports only front_end=interpret-interface'}
            if len(images) != 1:
                return {'skip':'Interpretation skipped: no single acquired binary to interpret (' + str(len(images)) + ' candidates)'}
            # A repair keeps the sealed workspace and adds the device diff and the candidate it contradicts.
            inputs = {'image.bin':str(images[0])}
            previous = next((a['path'] for h in reversed(accepted) if h['stage'] == 'interpret' for a in h['artifacts']
                             if (Path(a['path']) / 'model.json').is_file()), None)
            if previous:
                inputs['previous-model'] = previous
            defects = spec.get('_feedback') if spec.get('_revision') else None
            tool_run = run_dir / 'preparation' / workspace.name
            if previous or defects:
                # Say what each repair input is, so the agent starts from the prior candidate, and that every repair
                # input is sealed: the configurator re-hashes them after the agent, and a change stops the run.
                sealed = ['image.bin', *(['previous-model/'] if previous else []), *(['defects.json'] if defects else []),
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

    def _evaluator_operation(self, run_id, stage, assignment_id, options, invoke):
        """One controller owns evaluator effects, uncertainty and actor-labelled evidence."""
        with self._lock:
            operation_lock = self._operations.setdefault(run_id, threading.RLock())
        with operation_lock, self._active_operation(run_id):
            with self._db() as db:
                row = db.execute('SELECT cancelled,uncertain FROM runs WHERE id=?',(run_id,)).fetchone()
                if self._closing or row['cancelled'] or row['uncertain']:
                    raise ValueError('Evaluator operation requires active ownership and reconciled effects')
                db.execute('UPDATE runs SET uncertain=1 WHERE id=?',(run_id,))
                evidence_id = self._event(run_id, 'tool.started', {'actor':'evaluator','stage':stage,
                    'assignment_id':assignment_id,'name':'benchmark_evaluate','effectful':True}, db)
                db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)',(evidence_id,run_id,assignment_id,'evaluator'))
            options['evaluator_evidence_ids'] = [str(evidence_id)]
            result = invoke()
            with self._db() as db:
                cancelled = db.execute('SELECT cancelled FROM runs WHERE id=?',(run_id,)).fetchone()['cancelled']
                acknowledged = result.get('fault') != 'host'
                if not cancelled and acknowledged:
                    db.execute('DELETE FROM pending_effects WHERE dispatch_id=?',(evidence_id,))
                    db.execute('UPDATE runs SET uncertain=EXISTS(SELECT 1 FROM pending_effects WHERE run_id=?) WHERE id=?',(run_id,run_id))
                self._event(run_id, 'tool.finished', {'actor':'evaluator','stage':stage,'assignment_id':assignment_id,
                    'name':'benchmark_evaluate','ok':bool(result.get('ok', True)), 'dispatch_id':str(evidence_id),
                    'effectful':True,'effects_reconciled':bool(not cancelled and acknowledged), 'evaluation_event_id':str(evidence_id)}, db)
            return result

    def _check(self, spec, stage, run_dir, workspace, report, accepted, assignment):
        if spec.get('case'):
            from .benchmark import check_stage
            case = spec['case']
            options = case_options(spec)
            if (case.get('id') if isinstance(case,dict) else case)=='bme280':
                options['managed_probe']=lambda model_dir,n: self._managed_probe(run_dir.name,assignment,model_dir,n)
            if spec.get('_case_pin', {}).get('manifest', {}).get('schema') == 'benchmark-case/2':
                options['assignment_id'] = assignment['id']
                with self._db() as db:
                    rows = db.execute("SELECT seq,payload FROM events WHERE run_id=? AND kind='tool.finished' ORDER BY seq", (run_dir.name,)).fetchall()
                options['worker_events'] = [{'id':str(row['seq']), **json.loads(row['payload'])} for row in rows
                    if json.loads(row['payload']).get('actor') == 'worker']
                if stage in ('probe','reuse'):
                    return self._evaluator_operation(run_dir.name, stage, assignment['id'], options,
                        lambda: check_stage(case['id'] if isinstance(case, dict) else case, stage, run_dir, workspace, report, accepted, options))
            return check_stage(case['id'] if isinstance(case, dict) else case, stage, run_dir, workspace, report, accepted, options)
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
        paths, dropped = [], []
        for artifact in report.get('artifacts') if isinstance(report.get('artifacts'), list) else []:
            # Worker artifacts are claims: the configurator hashes the bytes itself and drops mismatches.
            raw = artifact.get('path') if isinstance(artifact, dict) else None
            if not isinstance(raw, str):
                continue
            path = Path(raw) if Path(raw).is_absolute() else workspace / raw
            if not path.resolve().is_relative_to(workspace):
                dropped.append({'path':raw,'reason':'Reported artifact is outside the assigned workspace'})
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
            # The configurator validates each assigned model itself, on a copy in its own run directory because a
            # validator may write beside the model: a model that fails validation goes back to interpret whether or not
            # the worker ran it. (For an interface model the runtime refuses such a model before opening the device.)
            from .toolkit import call_tool
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
                return handed(selected, checks, dropped, diff=diff, records=records)
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
            return handed(selected, checks, dropped, diff=diff, records=records, scope='Observed device tests of the '
                          'model and their differences from it; physical meaning rests on operator observations where given')
        elif stage == 'emit':
            from .toolkit import call_tool
            candidates = list(dict.fromkeys(p for p in paths if p.is_dir() and (p/'manifest.json').is_file()))
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
                  **({'verified_by': verified} if verified else {}), **({'rejected': rejected} if rejected else {}))
        elif stage == 'reuse':
            # This attempt's live operations, judged by the final verdict's rule for the package it received.
            package = next(({'path':raw,'sha256':sha} for raw, sha in (assignment.get('inputs') or {}).items()
                            if (Path(raw) / 'manifest.json').is_file()), None)
            with self._db() as db:
                started = [{**json.loads(row['payload']), 'dispatch_id': str(row['seq'])} for row in db.execute(
                    "SELECT seq,payload FROM events WHERE run_id=? AND kind='tool.started' ORDER BY seq", (run_dir.name,))]
            operations, live = _fresh_reuse(_frozen_package(package) if package else None, mine,
                                            [e for e in started if e.get('assignment_id') == assignment['id']],
                                            spec.get('effects'))
            executed = operations['passed'] is True
            selected = [str(p) for p in paths] if executed else []
            checks.append({'name':'fresh_package_execution','passed':operations['passed'],
                           'operations':operations['operations'],
                           'basis':"The final verdict's rule for fresh reuse, over this attempt's live operations",
                           **({'evidence':selected} if executed else
                              {'reason':'; '.join(d['reason'] for d in live) or 'No live operation of the package'})})
        return handed(selected, checks, dropped)

    def _accept(self, run_id, assignment, checked, run_dir, workspace, progress, notes=None, *, revise=None):
        """Accept what the stage observed, even nothing, with hashed notes. A revise (its reason) commits the
        new revision, the route to interpret and the run.revision event in the same transaction."""
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
        with self._db() as db:
            active = db.execute('SELECT state FROM assignments WHERE id=? AND run_id=?', (assignment['id'],run_id)).fetchone()
            prior = db.execute('SELECT payload FROM handoffs WHERE run_id=? AND stage=?', (run_id,assignment['stage'])).fetchall()
            duplicate = any(json.loads(p['payload']).get('revision',0)==assignment.get('revision',0) for p in prior)
            if not active or active['state'] != 'active' or duplicate:
                raise IntegrityError('Stale or duplicate handoff rejected')
            if db.execute('SELECT cancelled FROM runs WHERE id=?',(run_id,)).fetchone()['cancelled']:
                raise ValueError('Cancelled assignment cannot produce an accepted handoff')
            db.execute('INSERT INTO handoffs VALUES(?,?,?,?,?)', (assignment['id'],run_id,assignment['stage'],_json(handoff),time.time()))
            db.execute('UPDATE assignments SET state=? WHERE id=?', ('accepted', assignment['id']))
            if revise:
                db.execute("UPDATE assignments SET state='superseded' WHERE run_id=? AND state='active'",(run_id,))
                db.execute('UPDATE runs SET stage=? WHERE id=?',(progress['next_stage'],run_id))
            db.execute('INSERT OR REPLACE INTO progress VALUES(?,?)',(run_id,_json(progress)))
            self._event(run_id, 'stage.accepted', handoff, db)
            if revise:
                self._event(run_id,'run.revision', {**progress,'reason':revise},db)
        return handoff

    def _skip(self, run_id, stage, attempt, revision, run_dir, workspace, progress, reason):
        """A stage with nothing to work on runs no worker; it hands off empty with the reason as a note."""
        assignment = {'id': attempt, 'stage': stage, 'workspace': str(workspace), 'inputs': {}, 'allowed_tools': [],
                      'report_required': False, 'binding': None, 'effects': [], 'revision': revision, 'skipped': reason}
        with self._db() as db:
            db.execute('INSERT INTO assignments VALUES(?,?,?,?,?,?)', (attempt, run_id, stage, 'active', _json(assignment), time.time()))
            self._event(run_id, 'stage.skipped', {'stage': stage, 'assignment_id': attempt, 'revision': revision, 'reason': reason}, db)
        self._state(run_id, 'running', stage=stage)
        notes = _notes({'status': 'skipped', 'reason': reason}, {}, Outcome('advance', reason), missing=[reason])
        return self._accept(run_id, assignment, {'artifacts': [], 'checks': [], 'validation_scope': 'No worker ran: ' + reason},
                            run_dir, workspace, progress, notes)

    def _managed_probe(self,run_id,assignment,model_dir,n):
        """Host-only evaluator callback; never exposed by the worker MCP server."""
        if assignment['stage'] not in ('probe','ground') or type(n) is not int or not 1<=n<=3:
            raise ValueError('Evaluator probe requires an assigned physical stage and one to three samples')
        source=Path(model_dir).resolve()
        if source!=Path(assignment['workspace'])/'model' or str(source) not in assignment['inputs']:
            raise ValueError('Evaluator probe must use the pinned model in the current stage')
        with self._lock:
            operation_lock=self._operations.setdefault(run_id,threading.RLock())
        with operation_lock, self._active_operation(run_id):
            self._verify_inputs(assignment['inputs'])
            # Copy checked bytes outside the worker workspace; the callback never
            # accepts a worker-supplied binding, output path or permission grant.
            workspace=self.home/'runs'/run_id/'evaluator'/assignment['id']/uuid.uuid4().hex
            snapshot=workspace/'model'
            shutil.copytree(source,snapshot)
            if digest(snapshot)!=assignment['inputs'][str(source)]:
                raise ValueError('Evaluator model changed while taking its pinned snapshot')
            return self._tool('tool',{'assignment_id':assignment['id'],'name':'probe_run',
                'arguments':{'model_dir':str(snapshot),'n':n}},self._row(run_id),evaluator_workspace=workspace)

    def _binding_state(self, run_id):
        """Per-run serial facts: whether the device was listened to and whether opening it resets it."""
        listened = resets = overridden = False
        with self._db() as db:
            rows = db.execute("SELECT kind,payload FROM events WHERE run_id=? AND kind IN ('tool.finished','operator.response') ORDER BY seq",(run_id,)).fetchall()
        for row in rows:
            data = json.loads(row['payload'])
            if row['kind'] == 'tool.finished' and data.get('name') == 'acquire_serial_listen' and data.get('ok'):
                listened = True
                resets = resets or (not overridden and bool(data.get('result',{}).get('unsolicited_output')))
            elif row['kind'] == 'operator.response':
                observed = data.get('observation') or {}
                if observed.get('binding_property') == 'resets_on_open' and observed.get('value') is False:
                    resets, overridden = False, True
        return {'listened': listened, 'resets_on_open': resets}

    def _tool(self, method, params, run, *, evaluator_workspace=None):
        """The worker gateway. A refusal before dispatch is recorded so that stage's notes can name it."""
        marker = {}
        try:
            return self._dispatch(method, params, run, marker, evaluator_workspace=evaluator_workspace)
        except Exception as error:
            if method == 'tool' and 'assignment' in marker and 'dispatched' not in marker:
                self._event(run['id'], 'tool.refused', {'stage': marker['assignment']['stage'],
                    'assignment_id': marker['assignment']['id'], 'name': params.get('name'), 'reason': str(error),
                    'actor': 'evaluator' if evaluator_workspace is not None else 'worker'})
            raise

    def _dispatch(self, method, params, run, marker, *, evaluator_workspace=None):
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
        if evaluator_workspace is not None:
            spec=json.loads(run['spec']);case=spec.get('case')
            if (case.get('id') if isinstance(case,dict) else case)!='bme280' or assignment['stage'] not in ('probe','ground'):
                raise ValueError('Managed evaluator probes are restricted to the physical case stages')
            allowed=['probe_run']
        contracts = [copy.deepcopy(t) for t in list_tools() if t['name'] in allowed]
        if 'benchmark_package_execute' in assignment['allowed_tools']:
            contracts.append({'name':'benchmark_package_execute','description':'Execute a packaged driver operation with configurator-owned binding and immediately capture the evaluator observation.',
                'inputSchema':{'type':'object','required':['package_dir','operation'],'additionalProperties':False,
                    'properties':{'package_dir':{'type':'string'},'operation':{'type':'string'},'parameters':{'type':'object'}}}})
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
        workspace = Path(evaluator_workspace or assignment['workspace'])
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
        # Facts the device diff rests on, recorded by the configurator, never taken from the worker: whether it
        # supplied the device connection (a replay gets none), and the content of every evidence path the tool reads.
        facts = {'connected': connected, 'argument_sha256': _argument_digests(arguments)}
        # The uncertain flag is persisted before dispatch. A crash or cancellation
        # cannot turn an outstanding physical effect into an automatic retry.
        with self._lock:
            with self._db() as db:
                active = db.execute('SELECT status,cancelled,uncertain FROM runs WHERE id=?',(run['id'],)).fetchone()
                if active['cancelled'] or active['status'] != 'running':
                    raise ValueError('Worker assignment became inactive before dispatch')
                if effectful and active['uncertain']:
                    raise ValueError('Outstanding effect is uncertain; another effectful operation is refused until operator reconciliation')
                if effectful:
                    db.execute('UPDATE runs SET uncertain=1 WHERE id=?', (run['id'],))
                # The facts go on the start too: a dispatch that raises before it finishes is still known to the verdict.
                dispatch_id = self._event(run['id'], 'tool.started', {'stage':assignment['stage'],'assignment_id':assignment['id'],'name':name,'effectful':effectful,
                    **facts, 'actor':'evaluator' if evaluator_workspace is not None else 'worker'}, db)
                if effectful:
                    db.execute('INSERT INTO pending_effects VALUES(?,?,?,?)',(dispatch_id,run['id'],assignment['id'],
                        'evaluator' if evaluator_workspace is not None else 'worker'))
        marker['dispatched'] = dispatch_id
        if name == 'benchmark_package_execute':
            from .benchmark import package_execute
            spec = json.loads(run['spec'])
            case = spec.get('case')
            if not case:
                raise ValueError('Benchmark package execution requires an assigned case')
            options = case_options(spec)
            options['assignment_id'] = assignment['id']
            arguments['run_dir'] = str(self.home / 'runs' / run['id'])
            result = package_execute(case['id'] if isinstance(case,dict) else case,
                                     stage=assignment['stage'],options=options,**arguments)
        else:
            spec = json.loads(run['spec'])
            if (connected and name in ('interface_execute','probe_run')
                    and spec.get('_case_pin', {}).get('manifest', {}).get('schema') == 'benchmark-case/2'):
                from .benchmark_support.emulated import worker_tool
                result = worker_tool(spec['_case_pin']['case_id'], self.home/'runs'/run['id'],
                                     name, arguments, case_options(spec))
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
                or (name in ('interface_execute', 'emit_test_live') and _declared_refusal(result)))
            effects_reconciled = bool(effectful and not cancelled and (success or refused_before_open))
            if effects_reconciled:
                db.execute('DELETE FROM pending_effects WHERE dispatch_id=?',(dispatch_id,))
                db.execute('UPDATE runs SET uncertain=EXISTS(SELECT 1 FROM pending_effects WHERE run_id=?) WHERE id=?',(run['id'],run['id']))
            evidence_id = self._event(run['id'], 'tool.finished', {'stage':assignment['stage'],'assignment_id':assignment['id'],'name':name,'ok':success,'result':result,'artifacts':observed_artifacts,
                'dispatch_id':str(dispatch_id),'effectful':effectful,'effects_reconciled':effects_reconciled,
                'refused_before_open':refused_before_open,**facts,
                'actor':'evaluator' if evaluator_workspace is not None else 'worker'}, db)
        if name == 'benchmark_package_execute':
            result = {**result, 'evidence_id': str(evidence_id)}
        return result

    def close(self, timeout=10):
        with self._lock:
            self._closing = True
            workers = list(self._workers.values())
            schedulers = list(self._suite_threads.values())
        deadline = time.monotonic() + timeout
        for wake, thread in schedulers:
            wake.set()
        for cancel, thread in workers:
            cancel.set()
        for cancel, thread in workers:
            thread.join(max(0, deadline - time.monotonic()))
        for wake, thread in schedulers:
            thread.join(max(0, deadline - time.monotonic()))
        return not any(thread.is_alive() for _, thread in workers + schedulers)
