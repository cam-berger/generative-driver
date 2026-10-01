"""Pure maintenance verdicts and evaluator-owned scenario effect journal."""
import json
import os
from pathlib import Path
import tempfile
import hashlib


def maintain_objective(scenario: str) -> str:
    if scenario not in ('control', 'semantic', 'identity'):
        raise ValueError('Unknown maintenance scenario')
    return ('Check the supplied package against the current device using assigned tools. '
            'Write maintenance.json with schema benchmark-maintenance-claim/1, claim unchanged or drift, '
            'and evidence_ids naming this assignment\'s observed tool events. Include its actual SHA-256 '
            'in the stage report artifacts. Report completed for unchanged or needs_revision for drift. '
            'Do not repair the package in this stage.')


def read_maintenance_claim(workspace: Path, report: dict, assignment_id: str, events: list[dict]) -> dict:
    """Read a claim only when its bytes and cited event IDs belong to this assignment."""
    workspace = Path(workspace).resolve()
    target = workspace / 'maintenance.json'
    if target.is_symlink() or not target.is_file():
        raise ValueError('maintenance.json missing from assigned workspace')
    claimed = [row for row in report.get('artifacts', [])
               if (workspace / row.get('path', '')).resolve() == target]
    actual_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    if len(claimed) != 1 or claimed[0].get('sha256') != actual_hash:
        raise ValueError('maintenance.json report artifact hash mismatch')
    value = json.loads(target.read_text(encoding='utf-8'))
    if value.get('schema') != 'benchmark-maintenance-claim/1' or value.get('claim') not in ('unchanged', 'drift'):
        raise ValueError('Invalid maintenance claim schema or value')
    expected_status = 'needs_revision' if value['claim'] == 'drift' else 'completed'
    if report.get('status') != expected_status:
        raise ValueError('Maintenance report status disagrees with claim')
    ids = value.get('evidence_ids')
    current_ids = {str(event['id']) for event in events if event.get('assignment_id') == assignment_id}
    if (not isinstance(ids, list) or not ids or any(not isinstance(item, str) or not item or item not in current_ids for item in ids)):
        raise ValueError('Maintenance evidence IDs must name current assignment events')
    return value


def maintenance_decision(*, scenario: str, claim: str, diagnostic: dict, repaired: bool) -> dict:
    """Combine a worker claim with separately obtained evaluator observations."""
    fault = diagnostic.get('fault')
    if not diagnostic.get('evaluable') or fault in ('host', 'operator'):
        return {'ok': False, 'fault': fault or 'host', 'route': None,
                'reason': 'Independent maintenance observation is unavailable',
                'maintenance': {'false_alarm': False}}
    evidence = diagnostic.get('evidence_ids')
    if (claim not in ('unchanged', 'drift') or not isinstance(evidence, list) or
            not evidence or any(not isinstance(item, str) or not item for item in evidence)):
        return {'ok': False, 'fault': None, 'route': None,
                'reason': 'Maintenance claim lacks evidence', 'maintenance': {'false_alarm': False}}
    if repaired:
        ok = claim == 'unchanged' and not diagnostic.get('contradiction') and bool(diagnostic.get('requalified')) and bool(diagnostic.get('fresh_reuse_passed'))
        return {'ok': ok, 'fault': None, 'route': None,
                'reason': None if ok else 'Repair needs requalification and fresh reuse',
                'maintenance': {'false_alarm': False}}
    if scenario == 'control':
        ok = claim == 'unchanged' and not diagnostic.get('contradiction') and bool(diagnostic.get('fresh_reuse_passed'))
        return {'ok': ok, 'fault': None, 'route': None,
                'reason': None if ok else 'Control maintenance claim failed',
                'maintenance': {'false_alarm': claim == 'drift' and not diagnostic.get('contradiction')}}
    if scenario not in ('semantic', 'identity'):
        raise ValueError('Unknown maintenance scenario')
    ok = claim == 'drift' and bool(diagnostic.get('contradiction'))
    return {'ok': ok, 'fault': None, 'route': 'interpret' if ok else None,
            'reason': 'Evidenced semantic drift' if ok else 'Drift claim lacks independent contradiction',
            'maintenance': {'false_alarm': claim == 'drift' and not diagnostic.get('contradiction')}}


def next_action_state(current, nonce, action_sha256):
    if current.get('status') == 'applying':
        raise ValueError('Scenario effect requires reconciliation')
    if current.get('status') in ('applied', 'observed'):
        if (current['nonce'], current['action_sha256']) != (nonce, action_sha256):
            raise ValueError('Scenario action identity changed')
        return current
    return {'status': 'applying', 'nonce': nonce, 'action_sha256': action_sha256}


class ScenarioJournal:
    """One action per evaluator run; applying state is durable before external I/O."""
    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / 'scenario-journal.json'

    def state(self) -> dict:
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def _save(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.scenario-', suffix='.json', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(state, stream, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return state

    def begin(self, nonce: str, action_sha256: str) -> dict:
        current = self.state()
        state = next_action_state(current, nonce, action_sha256)
        return current if state is current else self._save(state)

    def applied(self, nonce: str, observation: dict) -> dict:
        current = self.state()
        if current.get('status') != 'applying' or current.get('nonce') != nonce:
            raise ValueError('No matching applying scenario action')
        if not isinstance(observation, dict) or not observation:
            raise ValueError('Scenario action requires a verifiable acknowledgement')
        return self._save({**current, 'status': 'applied', 'observation': observation})

    def observed(self, nonce: str, evidence_sha256: str) -> dict:
        current = self.state()
        if current.get('nonce') != nonce or current.get('status') not in ('applied', 'observed'):
            raise ValueError('No matching applied scenario action')
        if current['status'] == 'observed':
            if current['evidence_sha256'] != evidence_sha256:
                raise ValueError('Scenario independent evidence changed')
            return current
        return self._save({**current, 'status': 'observed', 'evidence_sha256': evidence_sha256})
