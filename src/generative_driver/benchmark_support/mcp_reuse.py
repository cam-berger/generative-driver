"""Bind fresh-use observations to owner-recorded generated-tool dispatches."""
from .snapshots import canonical_digest

PROFILE = 'generated-mcp/1'
PROOF_FIELDS = ('schema', 'tool_name', 'catalog_sha256', 'manifest_sha256', 'contract_sha256',
                'package_sha256', 'session_id', 'assignment_id', 'dispatch_id', 'request_id')


def check_events(events, worker_events, catalogs, configured, assignment_id, package_sha256):
    """Worker files are claims; the configurator journal supplies the matching evidence."""
    served = [event for event in catalogs if event.get('assignment_id') == assignment_id
              and all(event.get(key) == configured[key] for key in
                      ('catalog_sha256', 'manifest_sha256', 'contract_sha256'))]
    owned = {event['dispatch_id']: event for event in worker_events
             if event.get('assignment_id') == assignment_id and event.get('stage') == 'reuse'
             and event.get('actor') == 'worker' and event.get('result', {}).get('mcp')}
    if not served or not events:
        return False
    if [event.get('mcp', {}).get('dispatch_id') for event in events] != list(owned):
        return False  # Grade the complete owner sequence, including its actual ending state.
    seen = set()
    for event in events:
        proof = event.get('mcp', {})
        dispatch = proof.get('dispatch_id')
        record = owned.get(dispatch)
        if not record or dispatch in seen:
            return False
        seen.add(dispatch)
        result = record['result']
        if (proof != result['mcp'] or proof.get('assignment_id') != assignment_id
                or proof.get('schema') != 'driver-mcp-call/1' or proof.get('package_sha256') != package_sha256
                or proof.get('tool_name') != record.get('name')
                or event.get('parameters', {}) != record.get('parameters', {})
                or event.get('operation') != result.get('operation')
                or event.get('assignment_id') != assignment_id
                or any(proof.get(key) != configured[key] for key in
                       ('catalog_sha256', 'manifest_sha256', 'contract_sha256'))
                or event.get('observation') != result.get('observation')
                or event.get('before_observation') != result.get('before_observation')
                or canonical_digest(event.get('result')) != canonical_digest({k:v for k,v in result.items()
                    if k not in ('observation', 'before_observation', 'mcp')})):
            return False
    return True


def collect(worker_events, catalogs, accepted):
    assignments = {handoff['assignment_id'] for handoff in accepted if handoff['stage'] == 'reuse'}
    calls = [{**{key: event['result']['mcp'].get(key) for key in PROOF_FIELDS},
              'evidence_id': event['id'], 'result_sha256': canonical_digest(event['result'])}
             for event in worker_events if event.get('assignment_id') in assignments
             and event.get('result', {}).get('mcp')]
    served = [{key: event.get(key) for key in ('assignment_id', 'catalog_sha256', 'manifest_sha256', 'contract_sha256')}
              for event in catalogs if event.get('assignment_id') in assignments]
    return {'schema': 'benchmark-mcp-reuse/1', 'calls': calls, 'catalogs': served}


def gate_passed(payload, gate):
    from .behavior import _sha
    evidence = payload.get('mcp_reuse', {})
    if evidence.get('schema') != 'benchmark-mcp-reuse/1':
        return False
    calls = [call for call in evidence.get('calls', []) if call.get('assignment_id') == gate['assignment_id']]
    if not calls or len({call.get('dispatch_id') for call in calls}) != len(calls):
        return False
    for call in calls:
        if (call.get('schema') != 'driver-mcp-call/1' or call.get('package_sha256') != gate['artifact_sha256']
                or not all(_sha(call.get(key)) for key in
                           ('catalog_sha256', 'manifest_sha256', 'contract_sha256', 'result_sha256'))
                or any(not isinstance(call.get(key), str) or not call[key] for key in
                       ('tool_name', 'session_id', 'dispatch_id', 'evidence_id', 'request_id'))
                or not any(all(catalog.get(key) == call[key] for key in
                    ('assignment_id', 'catalog_sha256', 'manifest_sha256', 'contract_sha256'))
                           for catalog in evidence.get('catalogs', []))):
            return False
    return True


def owner_evidence(run_dir, accepted):
    """Read the owner's durable journal, separately from worker-writable package events."""
    import json
    from pathlib import Path
    import sqlite3
    run_dir = Path(run_dir).resolve()
    package = next((Path(a['path']) for h in reversed(accepted) if h.get('stage') == 'emit'
                    for a in h.get('artifacts', []) if (Path(a['path'])/'manifest.json').is_file()), None)
    manifest = json.loads((package/'manifest.json').read_text()) if package else {}
    path = run_dir/'benchmark/package-events.jsonl'
    records = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    result = {'schema': 'benchmark-owner-mcp/1', 'run_id': run_dir.name, 'package_schema': manifest.get('schema'),
              'reuse_profile': PROFILE if manifest.get('schema') == 'interface-package/2' else None,
              'assignments': [], 'catalogs': [], 'started': [], 'finished': [], 'package_events': records}
    database = run_dir.parent.parent/'runs.sqlite3'
    if not database.is_file():
        return result
    # Read-only URI avoids creating or repairing an owner database in evaluator code.
    from contextlib import closing
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
        for sequence, kind, payload in db.execute('SELECT seq,kind,payload FROM events WHERE run_id=? ORDER BY seq', (run_dir.name,)):
            item = json.loads(payload)
            key = {'stage.assigned':'assignments','mcp.catalog_served':'catalogs',
                   'tool.started':'started','tool.finished':'finished'}.get(kind)
            if key:
                if key == 'started':
                    item['dispatch_id'] = str(sequence)
                result[key].append(item)
    return result


def required(evidence, snapshot=None):
    profile = ((snapshot or {}).get('executed') or {}).get('reuse_profile')
    if profile not in (None, PROFILE):
        raise ValueError('Unsupported fresh reuse profile: '+str(profile))
    return profile == PROFILE or evidence.get('package_schema') == 'interface-package/2'


def verified(evidence, frozen):
    """The complete ordered call sequence, including the actual final device state, matches the owner."""
    if evidence.get('schema') != 'benchmark-owner-mcp/1' or not frozen:
        return False
    assignments = [a for a in evidence.get('assignments', []) if a.get('stage') == 'reuse'
                   and frozen in a.get('inputs', {}).values()]
    if not assignments:
        return False
    identities = {a['id'] for a in assignments}
    started = [e for e in evidence.get('started', []) if e.get('assignment_id') in identities]
    finished = [e for e in evidence.get('finished', []) if e.get('assignment_id') in identities]
    records = [e for e in evidence.get('package_events', []) if e.get('actor') == 'worker'
               and e.get('stage') == 'reuse' and e.get('package_sha256') == frozen]
    if (not started or len(started) != len(finished) or len(records) != len(finished)
            or [e.get('mcp', {}).get('dispatch_id') for e in records] != [e.get('dispatch_id') for e in finished]
            or [e.get('dispatch_id') for e in started] != [e.get('dispatch_id') for e in finished]
            or any(e.get('mcp', {}).get('session_id') != evidence.get('run_id') for e in records)):
        return False
    for assignment in assignments:
        configured = assignment.get('device_mcp')
        if not configured:
            return False
        events = [e for e in records if e.get('assignment_id') == assignment['id']]
        if not events:  # A resumed assignment may finish before making a call; it adds no claimed evidence.
            continue
        if not check_events(events, finished, evidence.get('catalogs', []), configured, assignment['id'], frozen):
            return False
    return True
