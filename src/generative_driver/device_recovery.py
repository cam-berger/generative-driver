"""Trusted attribution of uncertain device writes, and their recovery through the device handle: the bench harness's
provider replaces a provided emulator, and the configurator commits the replacement with the stage's handoff.

It holds no password, recipe or truth and imports nothing that decrypts one or grades: the provider keeps the
replacement proof in its private journal, and the configurator receives and records only the receipt."""
import json
import threading
import time

RECEIPT = 'emulator-recovery/2'
LIVE_TOOLS = ('interface_execute', 'probe_run', 'benchmark_package_execute', 'emit_test_live')


def _generated(event, assignment):
    """A generated call is trusted only through its assignment and owner dispatch facts."""
    configured = assignment.get('device_mcp') or {}
    proof = (event.get('result') or {}).get('mcp') or {}
    return bool(configured and event.get('connected') is True
        and proof.get('schema') == 'driver-mcp-call/1'
        and proof.get('assignment_id') == assignment['id']
        and proof.get('dispatch_id') == event.get('dispatch_id')
        and proof.get('tool_name') == event.get('name')
        and proof.get('package_sha256') == assignment.get('inputs', {}).get(configured.get('package_dir'))
        and (event.get('argument_sha256') or {}).get('package_dir') == proof.get('package_sha256')
        and all(proof.get(key) == configured.get(key) for key in
                ('manifest_sha256', 'contract_sha256', 'catalog_sha256')))


def diagnostic_faults(finishes, assignment):
    """Other observed host/operator/unknown runtime failures still block repair."""
    faults = []
    for _, event in finishes:
        if (event.get('actor') != 'worker' or event.get('assignment_id') != assignment['id']
                or event.get('ok') is not False or (event.get('name') not in LIVE_TOOLS and not _generated(event, assignment))):
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
            elif fault != 'model' or (item.get('schema') != 'interface-result/1' and not
                    (item.get('schema') == 'driver-result/1' and _generated(event, assignment))):
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
        generated = _generated(event, assignment) and (result.get('mcp') or {}).get('session_id') == run_id
        items = result.get('results') if isinstance(result,dict) and 'results' in result else [result]
        if not isinstance(items,list) or not items:
            faults.append('unknown')
            continue
        failures = []
        for item in items:
            if (not isinstance(item,dict) or (item.get('schema') != 'interface-result/1' and not
                    (generated and item.get('schema') == 'driver-result/1' and item.get('requested_command_dispatched') is True))
                    or type(item.get('ok')) is not bool):
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


def _verified(receipt, held):
    """A receipt the configurator may commit: the provider stopped the session the run holds, confirmed, and verified a
    distinct replacement at its initial state on a new binding."""
    return (isinstance(receipt, dict) and receipt.get('schema') == RECEIPT and receipt.get('old_owner_stopped') is True
            and receipt.get('initial_state_verified') is True and bool(held.get('session_id'))
            and receipt.get('old_session_id') == held['session_id'] and isinstance(receipt.get('new_session_id'), str)
            and receipt['new_session_id'] not in ('', held['session_id']) and isinstance(receipt.get('binding'), dict)
            and bool(receipt['binding']) and receipt['binding'] != held.get('binding'))


def reconcile(controller, run_id, spec, assignment, run_dir, progress):
    """Consume trusted runtime evidence once the worker has exited, under its lease, and have the provider replace the
    run's provided emulator.

    Only a write the runtime attributed to the model, after it verified the device's identity and opened it, on an
    emulator the bench harness provides (a `provided-emulator` descriptor; a physical device has none), is replaced, at
    any stage. A verified replacement is returned as `recovery` for the stage's handoff to commit, in one transaction
    with the new binding and session, the cleared uncertainty and any route its device diff takes. During emit or fresh
    reuse the failed write is evidence about the frozen driver: the handoff advances and the driver is graded after the
    run, never repaired. This never routes the run or clears uncertainty itself: until that commit the failed write
    stays uncertain, and it is never re-executed. A provider that cannot be reached, a failed or cancelled replacement
    and an unverified receipt are recorded as a failed recovery and stop for safety. The repair budget plays no part:
    replacing the emulator is as safe with no repair left as with one."""
    with controller._lock:
        lock = controller._operations.setdefault(run_id, threading.RLock())
    with lock, controller._active_operation(run_id):
        with controller._db() as db:
            category, failed, dispatches = unresolved_outcomes(db,run_id,assignment)
            allowed = failed and category == 'model' and (spec.get('device') or {}).get('kind') == 'provided-emulator'
            if not allowed:
                return {'stop':{'category':'safety','reason':'Outstanding effect requires operator reconciliation'}}
            evidence = sorted({str(i) for i,_ in failed})
            controller._event(run_id,'emulator.recovery_started',{'assignment_id':assignment['id'],
                'unresolved_evidence_ids':evidence,'unresolved_dispatch_ids':dispatches,'revision':progress['revision']},db)
        def cancelled():
            return bool(controller._closing or controller._row(run_id)['cancelled']
                        or time.time() >= controller._row(run_id)['created']+spec['budget_seconds'])
        try:
            from .device_client import open_device
            held = progress.get('device') or {}
            remaining = controller._row(run_id)['created'] + spec['budget_seconds'] - time.time()
            receipt = open_device(spec['device']).replace(held.get('session_id'), evidence, dispatches,
                                                          deadline_seconds=max(0.0, remaining))
            if not _verified(receipt, held):
                raise RuntimeError('The provider\'s receipt is not a verified replacement of the run\'s device session')
            if cancelled():
                raise RuntimeError('Recovery cancelled before durable reconciliation')
            return {'recovery':{'assignment_id':assignment['id'],'receipt':receipt,
                                'unresolved_evidence_ids':evidence,'unresolved_dispatch_ids':dispatches}}
        except Exception as error:
            controller._event(run_id,'emulator.recovery_failed',{'assignment_id':assignment['id'],
                'reason':type(error).__name__+': '+str(error)})
            return {'stop':{'category':'safety','reason':'Emulator recovery unverified: '+str(error)}}
