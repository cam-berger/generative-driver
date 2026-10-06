"""Trusted process replacement; no candidate tools or transaction semantics."""
import hashlib
import json
import math
import uuid
from pathlib import Path


def observed_values(session):
    observation = session.observe()
    values = observation.get('values')
    expected = set(session.recipe.get('observations', {}))
    if not expected or not isinstance(values, dict) or set(values) != expected:
        raise RuntimeError('Incomplete independent startup observations')
    def valid(value):
        return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                or isinstance(value, list) and bool(value) and all(valid(item) for item in value))
    reads = observation.get('reads')
    if (not isinstance(reads,list) or {r.get('name') for r in reads if isinstance(r,dict)} != expected
            or any(not isinstance(r,dict) or r.get('value') != values.get(r.get('name'))
                   or not isinstance(r.get('response'),str) or not r['response'] for r in reads)):
        raise RuntimeError('Incomplete independent startup observation reads')
    if not all(valid(value) for value in values.values()):
        raise RuntimeError('Malformed independent startup observations')
    return observation


def replace_owned(case_id, run_dir, options, cancelled):
    from . import emulated
    from .cases import _state, _write
    from .native import NativeSession
    key = str(Path(run_dir).resolve())
    path, state = _state(run_dir)
    old = emulated._OWNERS.get(key)
    if old is None or old.process is None or not state.get('startup_observation'):
        raise RuntimeError('Owned emulator process handle and startup evidence are required')
    old_id = state['session'].get('session_id')
    if not old_id or cancelled():
        raise RuntimeError('Emulator recovery ownership is inactive')
    old.stop()
    if old.process.poll() is None:
        raise RuntimeError('Old emulator stop could not be confirmed')
    emulated._OWNERS.pop(key)
    state['previous_session'] = state.pop('session')
    _write(path, state)
    replacement = None
    try:
        if cancelled():
            raise RuntimeError('Emulator recovery cancelled')
        inputs, _ = emulated._inputs(case_id, run_dir)
        replacement = NativeSession.start(renode=options.get('renode'), image=inputs/'firmware.bin',
                                         recipe=emulated._truth(case_id, run_dir, options)['recipe'])
        if replacement is old or replacement.process is old.process or replacement.process is None or replacement.process.poll() is not None:
            raise RuntimeError('Replacement is not a distinct live owned emulator')
        if replacement.binding == old.binding:
            raise RuntimeError('Replacement emulator binding was reused')
        observed = observed_values(replacement)
        if observed['values'] != state['startup_observation']['values'] or cancelled():
            raise RuntimeError('Replacement initial state is unverified or recovery cancelled')
        new_id = uuid.uuid4().hex
        replacement.info['session_id'] = new_id
        receipt = {'schema':'emulator-recovery/1', 'old_session_id':old_id, 'new_session_id':new_id,
                   'old_owner_stopped':True, 'initial_state_verified':True,
                   'observation_sha256':hashlib.sha256(json.dumps(observed, sort_keys=True).encode()).hexdigest(),
                   'binding':replacement.binding}
        proof = {'receipt':receipt, 'old_startup_observation':state['startup_observation'],
                 'new_startup_observation':observed,'old_process':old.info,'new_process':replacement.info,
                 'unresolved_evidence_ids':options.get('recovery_evidence_ids',[]),
                 'unresolved_dispatch_ids':options.get('recovery_dispatch_ids',[])}
        from .emulated_evidence import _private
        payload = _private(run_dir, options)
        payload.setdefault('emulator_recoveries',[]).append(proof)
        _private(run_dir, options, payload)
        receipt['proof_sha256'] = hashlib.sha256(json.dumps(proof,sort_keys=True).encode()).hexdigest()
        state['session'] = replacement.info
        state.setdefault('emulator_recoveries', []).append(receipt)
        _write(path, state)
        emulated._OWNERS[key] = replacement
        return receipt
    except BaseException:
        if replacement is not None:
            try:
                replacement.stop()
            finally:
                if replacement.process is None or replacement.process.poll() is None:
                    # Retain handles for cleanup even when a failed replacement cannot stop.
                    emulated._OWNERS[key] = replacement
                    state['session'] = replacement.info
                    _write(path, state)
        raise


def preserve_failure(run_dir, workspace, accepted, report, feedback):
    from .candidate_context import remember
    from .cases import _state
    _, state = _state(run_dir)
    files = {'capabilities.json':Path(workspace)/'capabilities.json',
             'live-observations.jsonl':Path(run_dir)/'benchmark'/f'live-observations-{state.get("revision",0)}.jsonl'}
    if state.get('adapted_model_dir'):
        files['model.json'] = Path(state['adapted_model_dir'])/'model.json'
    for handoff in reversed(accepted):
        if handoff['stage'] == 'interpret':
            for artifact in handoff['artifacts']:
                path = Path(artifact['path'])
                for name in ('replies.json','NOTES.md'):
                    if path.name.endswith(name):
                        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                            raise ValueError('Accepted candidate evidence changed')
                        files[name] = path
            break
    remember(run_dir, 'probe', files, feedback, report, workspace=workspace)


def diagnostic_faults(finishes, assignment):
    """Other observed host/operator/unknown runtime failures still block repair."""
    faults = []
    for _, event in finishes:
        if (event.get('actor') != 'worker' or event.get('assignment_id') != assignment['id']
                or event.get('ok') is not False
                or event.get('name') not in ('interface_execute','probe_run','benchmark_package_execute','emit_test_live')):
            continue
        result = event.get('result')
        if not isinstance(result,dict):
            faults.append('unknown')
            continue
        items = result.get('results') if 'results' in result else [result]
        if not isinstance(items,list) or not items:
            faults.append('unknown')
            continue
        if all(isinstance(item,dict) and item.get('transport_open_attempted') is False for item in items):
            continue  # Trusted refusals before opening cannot establish outstanding effects.
        outer_error = result.get('error') if isinstance(result.get('error'),dict) else {}
        outer_fault = result.get('fault') or outer_error.get('fault')
        if outer_fault in ('host','operator'):
            faults.append(outer_fault)
        elif outer_fault not in (None,'model'):
            faults.append('unknown')
        for item in items:
            if not isinstance(item,dict):
                faults.append('unknown')
                continue
            if item.get('ok') is True or item.get('transport_open_attempted') is False:
                continue
            error = item.get('error') if isinstance(item.get('error'),dict) else {}
            fault = error.get('fault')
            if fault in ('host','operator'):
                faults.append(fault)
            elif fault != 'model' or item.get('schema') != 'interface-result/1':
                faults.append('unknown')
    return faults


def unresolved_outcomes(db, run_id, assignment):
    """Attribute every pending effect to exactly one trusted dispatch completion."""
    pending = db.execute('SELECT * FROM pending_effects WHERE run_id=? ORDER BY dispatch_id',(run_id,)).fetchall()
    rows = db.execute("SELECT seq,kind,payload FROM events WHERE run_id=? AND kind IN ('tool.started','tool.finished') ORDER BY seq",(run_id,)).fetchall()
    starts = {row['seq']:json.loads(row['payload']) for row in rows if row['kind']=='tool.started'}
    finishes = [(row['seq'],json.loads(row['payload'])) for row in rows if row['kind']=='tool.finished']
    faults, failed, dispatches = diagnostic_faults(finishes,assignment), [], []
    for attempt in pending:
        dispatch = str(attempt['dispatch_id'])
        dispatches.append(dispatch)
        started = starts.get(attempt['dispatch_id'],{})
        matches = [(identifier,event) for identifier,event in finishes if event.get('dispatch_id')==dispatch]
        if (attempt['actor'] != 'worker' or attempt['assignment_id'] != assignment['id']
                or started.get('effectful') is not True or started.get('actor') != attempt['actor']
                or started.get('assignment_id') != attempt['assignment_id'] or len(matches) != 1):
            faults.append('unknown')
            continue
        identifier, event = matches[0]
        if (event.get('actor') != started['actor'] or event.get('assignment_id') != started['assignment_id']
                or event.get('name') != started.get('name') or event.get('effectful') is not True
                or event.get('effects_reconciled') is not False or event.get('ok') is not False):
            faults.append('unknown')
            continue
        result = event.get('result')
        items = result.get('results') if isinstance(result,dict) and 'results' in result else [result]
        if not isinstance(items,list) or not items:
            faults.append('unknown')
            continue
        failures = []
        for item in items:
            if not isinstance(item,dict) or item.get('schema') != 'interface-result/1' or type(item.get('ok')) is not bool:
                faults.append('unknown')
                continue
            if item['ok']:
                continue
            failures.append(item)
            error = item.get('error') if isinstance(item.get('error'),dict) else {}
            fault = error.get('fault')
            if fault in ('host','operator'):
                faults.append(fault)
            elif (fault == 'model' and item.get('identity_verified') is True
                  and item.get('transport_open_attempted') is True and item.get('transcript')):
                faults.append('model')
                failed.append((identifier,item))
            else:
                faults.append('unknown')
        if not failures:
            faults.append('unknown')
    category = next((fault for fault in ('operator','host','unknown') if fault in faults),
                    'model' if pending and faults and all(fault=='model' for fault in faults) else 'unknown')
    return category, failed, dispatches


def reconcile(controller, run_id, spec, assignment, run_dir, workspace, accepted, report, progress):
    """Consume trusted runtime evidence once the worker has exited, under its lease."""
    import threading
    import time
    from ..configurator import case_options
    from ..benchmark import recover_emulator
    with controller._lock:
        lock = controller._operations.setdefault(run_id, threading.RLock())
    with lock, controller._active_operation(run_id):
        with controller._db() as db:
            category, failed, dispatches = unresolved_outcomes(db,run_id,assignment)
            allowed = (failed and category == 'model' and assignment['stage'] in ('interpret','probe','ground')
                       and spec.get('_case_pin',{}).get('manifest',{}).get('schema') == 'benchmark-case/2'
                       and spec.get('_case_pin',{}).get('manifest',{}).get('approval_scope') == 'emulator'
                       and not progress.get('terminal_final_failure'))
            if not allowed:
                return {'ok':False,'fault':category,'reason':'Outstanding effect requires operator reconciliation'}
            if progress['repairs'] >= int(spec.get('max_revisions',2)):
                return {'ok':False,'fault':'model','reason':'Model repair budget exhausted with unresolved effect'}
            feedback = {'runtime_failures':[{'evidence_id':str(identifier),'result':item} for identifier,item in failed],
                        'unresolved_dispatch_ids':dispatches, 'effects_uncertain':True, 'reason':'Runtime contradicts candidate model; previous write was not retried'}
            db.execute("UPDATE assignments SET state='superseded' WHERE id=?",(assignment['id'],))
            controller._event(run_id,'emulator.recovery_started',{'assignment_id':assignment['id'],
                'unresolved_evidence_ids':sorted({str(i) for i,_ in failed}),'unresolved_dispatch_ids':dispatches,'revision':progress['revision']},db)
        def cancelled():
            return bool(controller._closing or controller._row(run_id)['cancelled']
                        or time.time() >= controller._row(run_id)['created']+spec['budget_seconds'])
        try:
            case = spec['case']
            receipt = recover_emulator(case['id'] if isinstance(case,dict) else case, run_dir,
                workspace, accepted, report, feedback, {**case_options(spec),
                    'recovery_evidence_ids':sorted({str(i) for i,_ in failed}), 'recovery_dispatch_ids':dispatches}, cancelled)
            with controller._lock, controller._db() as db:
                if cancelled():
                    raise RuntimeError('Recovery cancelled before durable reconciliation')
                controller._claim_binding(receipt['binding'],run_id,db)
                progress.update(revision=progress['revision']+1,next_stage='interpret',
                    repairs=progress['repairs']+1,feedback=feedback)
                controller._route(run_id,progress,'Runtime model discrepancy after emulator recovery',db)
                db.execute('DELETE FROM pending_effects WHERE run_id=?',(run_id,))
                db.execute('UPDATE runs SET uncertain=0 WHERE id=?',(run_id,))
                controller._event(run_id,'emulator.recovered',{'assignment_id':assignment['id'],
                    'unresolved_evidence_ids':sorted({str(i) for i,_ in failed}),'unresolved_dispatch_ids':dispatches, 'receipt':receipt,
                    'revision':progress['revision'],'repairs':progress['repairs']},db)
            return {'ok':False,'fault':'model','route':'interpret','reason':'Runtime model discrepancy after emulator recovery',
                    'feedback':feedback,'revision_committed':True}
        except Exception as error:
            controller._event(run_id,'emulator.recovery_failed',{'assignment_id':assignment['id'],
                'reason':type(error).__name__+': '+str(error)})
            return {'ok':False,'fault':'host','reason':'Emulator recovery unverified: '+str(error)}
