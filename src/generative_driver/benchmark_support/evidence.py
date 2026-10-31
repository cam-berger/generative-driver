"""Sealed evaluator records and explicitly allowlisted public summaries."""
from pathlib import Path

from .grader_identity import grader_identity


COUNTS = ('verdict', 'passed', 'total')
USAGE = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens',
         'total_tokens', 'observed_total_tokens', 'reported_attempts', 'attempts', 'coverage', 'counting')
PIN = ('case_id', 'case_version', 'manifest_sha256', 'scenario_id', 'scenario_sha256',
       'case_seed', 'truth_sha256', 'evaluator_version', 'family', 'variant', 'execution', 'evidence_track', 'scope')


def _scalars(value, fields):
    return {key: value[key] for key in fields if isinstance(value, dict) and key in value
            and (value[key] is None or type(value[key]) in (str, int, float, bool))}


def public_pin(value):
    result = _scalars(value, PIN)
    from .snapshots import canonical_digest
    from .behavior import _sha
    saved = value.get('pin_sha256')
    result['pin_sha256'] = saved if _sha(saved) else canonical_digest(value)
    result['image_hashes'] = _hashes(value.get('image_hashes', {}))
    return result


def _hashes(value):
    return {key: item for key, item in value.items() if isinstance(key, str)
            and isinstance(item, str) and len(item) == 64 and all(c in '0123456789abcdef' for c in item)} if isinstance(value, dict) else {}


def _evaluation(value):
    return _scalars(value, ('phase', 'revision', 'frozen_artifact_sha256', *COUNTS))


def _executed(value):
    result = _scalars(value, ('toolchain_revision', 'skills_revision', 'evaluator_revision'))
    result['environment'] = _scalars(value.get('environment', {}), ('system', 'release', 'architecture', 'python'))
    result['runtime_configuration'] = _scalars(value.get('runtime_configuration', {}),
        ('runtime', 'model', 'provider', 'version', 'reasoning_effort', 'max_turns', 'candidate_sha256'))
    return result


def public_v2_report(payload: dict) -> dict:
    """Unknown fields, including inside known containers, never cross this boundary."""
    result = _scalars(payload, ('run_id', 'case', 'case_version', 'evaluator_version', 'scenario_id',
        'case_seed', 'execution', 'model_benchmark', 'workflow_status', 'verdict', 'snapshot_sha256',
        'toolchain_revision', 'skills_revision', 'evaluator_revision', 'elapsed_seconds', 'worker_seconds',
        'tool_seconds', 'tool_calls', 'worker_tool_seconds', 'evaluator_tool_seconds',
        'additional_attempts', 'unfinished_tool_calls', 'human_inputs', 'original_budget_seconds', 'effective_budget_seconds'))
    result['schema'] = 'benchmark-report/2'
    setup = payload.get('execution_setup')
    if isinstance(setup, dict):
        result['execution_setup'] = {**_scalars(setup, ('method', 'artifact')),
            **{key: [item for item in setup.get(key, []) if isinstance(item, str)]
               for key in ('substitutions', 'validated_behavior', 'hardware_gaps')
               if isinstance(setup.get(key), list)}}
    result['case_pin'] = public_pin(payload.get('case_pin', {}))
    result['final_evaluation'] = _scalars(payload.get('final_evaluation', {}), (*COUNTS, 'evidence_sha256'))
    result['evaluations'] = [_evaluation(row) for row in payload.get('evaluations', [])]
    result['accepted_gates'] = [_scalars(row, ('stage', 'phase', 'revision', 'assignment_id', 'artifact_sha256', *COUNTS))
                                for row in payload.get('accepted_gates', [])]
    result['evaluator_verdicts'] = [_scalars(row, ('stage', 'assignment_id', 'revision', 'fault', 'final_evaluation', *COUNTS))
                                    for row in payload.get('evaluator_verdicts', [])]
    result['progress'] = _scalars(payload.get('progress', {}), ('revision', 'repairs', 'terminal_final_failure', 'protocol', 'equivalence'))
    result['attempt_limits'] = _scalars(payload.get('attempt_limits', {}), ('max_model_repairs',))
    result['usage'] = _scalars(payload.get('usage', {}), USAGE)
    result['executed'] = _executed(payload.get('executed', {}))
    result['stages'] = {}
    from .case_assets import STAGES
    for stage in STAGES:
        if stage not in payload.get('stages', {}):
            continue
        value = payload['stages'][stage]
        row = _scalars(value, ('attempt_count', 'workflow_status', 'evaluator_status', 'worker_seconds',
                              'tool_seconds', 'tool_calls', 'worker_tool_seconds', 'evaluator_tool_seconds'))
        row['usage'] = _scalars(value.get('usage', {}), USAGE)
        row['attempts'] = [{**_scalars(attempt, ('assignment_id', 'status', 'worker_status', 'accepted',
                           'elapsed_seconds', 'revision')),
                           'usage': _scalars(attempt.get('usage', {}), USAGE)} for attempt in value.get('attempts', [])]
        result['stages'][stage] = row
    result['interventions'] = [_scalars(row, ('event_id', 'kind', 'time', 'effect_resolution',
        'scoped_tool_approval', 'old_budget_seconds', 'new_budget_seconds', 'extension_seconds',
        'old_deadline', 'new_deadline', 'stage', 'uncertain_effect', 'has_observation'))
        for row in payload.get('interventions', [])]
    return result


# The public fields of an equivalence/1 record beside its checks, differences and mission.
EQUIVALENCE = ('schema', 'status', 'equivalent', 'basis', 'method', 'passed', 'total', 'frozen_artifact_sha256',
               'capabilities_sha256', 'evidence_sha256', 'protocol')
# What a recorded verdict's sealed evidence must agree with in the report before anything is regraded.
VERDICT_IDENTITY = ('schema', 'status', 'basis', 'method', 'protocol', 'frozen_artifact_sha256', 'capabilities_sha256')


def public_equivalence(value):
    """The public equivalence/1 record: check ids with passed flags, the failing ids with a reason and the mission check.
    Expected values, tolerances, witnesses and raw records never cross this boundary. None when there is no record."""
    if not isinstance(value, dict):
        return None
    rows = lambda key, fields: [_scalars(row, fields) for row in value.get(key) or [] if isinstance(row, dict)]
    mission = value.get('mission')
    return {**_scalars(value, EQUIVALENCE), 'checks': rows('checks', ('id', 'passed')), 'diff': rows('diff', ('id', 'reason')),
            'mission': _scalars(mission, ('name', 'passed', 'reason', 'fault')) if isinstance(mission, dict) else None}


def public_v3_report(payload: dict) -> dict:
    """An assistive run's public report, benchmark-report/3: its success is the one equivalence verdict the evaluator
    recorded after the run. Nothing graded its stages, so it carries no gates, stage evaluations or stage verdicts. Its
    verdict reads the record (equivalent, not equivalent, or unscored while there is none: awaiting its grade, unscored
    or stopped); a report found unqualified keeps that word. Its reason says why (a stop, the verdict or the unscored
    grade's reasons, with paths already scrubbed by its exporter) and `stopped_stage` names where a stopped run stopped.
    It names the protocol its payload names. Unknown fields never cross this boundary."""
    from .labels import equivalence_label
    result = public_v2_report(payload)
    for key in ('accepted_gates', 'evaluations', 'final_evaluation', 'evaluator_verdicts'):
        result.pop(key)
    for row in result['stages'].values():
        row.pop('evaluator_status', None)
    equivalence = public_equivalence(payload.get('equivalence'))
    awaiting = payload.get('equivalence_status')
    result.update(schema='benchmark-report/3', equivalence=equivalence,
                  equivalence_status=equivalence.get('status') if equivalence else awaiting if awaiting in ('pending', 'unscored') else None,
                  verdict='unqualified' if payload.get('verdict') == 'unqualified' else equivalence_label(equivalence),
                  **_scalars(payload, ('protocol', 'outcome_category', 'reason', 'stopped_stage')))
    return result


def _validate_payload(payload):
    from .behavior import validate_records, _sha
    if payload.get('schema') != 'benchmark-run-evidence/1':
        raise ValueError('Unsupported run evidence schema')
    _snapshot_identity(payload)
    seen = set()
    for evaluation in payload['evaluations']:
        key = (evaluation['phase'], evaluation['revision'])
        digest = evaluation['frozen_artifact_sha256']
        if (key in seen or key[0] not in ('diagnostic', 'final') or type(key[1]) is not int or key[1] < 0 or
                not _sha(digest) or evaluation['contract']['artifact_sha256'] != digest):
            raise ValueError('Evaluation artifact or revision identity mismatch')
        seen.add(key)
        if any(check.get('revision') != key[1] for check in evaluation['contract']['checks']):
            raise ValueError('Contract revision mismatch')
        for record in evaluation['records']:
            if record.get('artifact_sha256') != digest or record.get('revision') != key[1]:
                raise ValueError('Record artifact or revision mismatch')
        validate_records(evaluation['contract'], evaluation['records'])
        if key[0] == 'final':
            emit = [row for row in payload.get('accepted_artifacts', [])
                    if row.get('stage') == 'emit' and row.get('revision') == key[1]]
            if emit and not any(row.get('sha256') == digest for row in emit):
                raise ValueError('Frozen final artifact differs from accepted emit package')


def _snapshot_identity(payload, v2=True):
    """The sealed snapshot is the run's, for its pinned case: a v2 case for benchmark-run-evidence/1 (`v2`), any
    registered workflow case, legacy ones included, for an assistive run's evidence."""
    from .snapshots import canonical_digest
    snapshot = payload['execution_snapshot']
    from .mcp_reuse import required
    required(payload.get('mcp_reuse', {}), snapshot)
    if (snapshot.get('schema') != 'benchmark-execution-snapshot/1' or
            canonical_digest({k: v for k, v in snapshot.items() if k != 'snapshot_sha256'}) != snapshot.get('snapshot_sha256') or
            snapshot['case_pin'] != payload['case_pin']):
        raise ValueError('Execution snapshot identity mismatch')
    if v2 and payload['case_pin']['manifest'].get('schema') != 'benchmark-case/2':
        raise ValueError('Run evidence requires a v2 case')
    from .registry import validate_pinned_workflow
    validate_pinned_workflow(payload['case_pin'])


def _validate_payload_v2(payload):
    """An assistive run's evidence: its snapshot identity, every accepted artifact and notes hash, and at most one final,
    which grades the run's accepted emitted driver: the first artifact of its latest emit handoff that handed one on.
    A final graded from the run's recorded fresh reuse (`recorded-reuse`) has the same contract and records. It names the
    protocol its run was under, whichever that is."""
    from .case_assets import STAGES
    from .behavior import validate_records, _sha
    protocol = payload.get('protocol')
    if payload.get('schema') != 'benchmark-run-evidence/2' or not isinstance(protocol, str) or not protocol:
        raise ValueError('Unsupported run evidence schema or protocol')
    _snapshot_identity(payload, v2=False)
    rows = payload['accepted_artifacts']
    for row in rows:
        if (row.get('stage') not in STAGES or type(row.get('revision')) is not int or row['revision'] < 0
                or not isinstance(row.get('assignment_id'), str) or row.get('kind') not in ('artifact', 'notes')
                or not _sha(row.get('sha256'))):
            raise ValueError('Accepted artifact identity mismatch')
    emits = [row for row in rows if row['stage'] == 'emit' and row['kind'] == 'artifact']
    accepted = next((row['sha256'] for row in emits if row['assignment_id'] == emits[-1]['assignment_id']), None) if emits else None
    final = payload.get('final')
    if final is not None:
        digest, revision = final.get('frozen_artifact_sha256'), final.get('revision')
        if (final.get('phase') != 'final' or final.get('run_id') != payload.get('run_id') or type(revision) is not int
                or revision < 0 or not _sha(digest) or final['contract'].get('artifact_sha256') != digest):
            raise ValueError('Evaluation artifact or revision identity mismatch')
        if any(check.get('revision') != revision for check in final['contract']['checks']):
            raise ValueError('Contract revision mismatch')
        for record in final['records']:
            if record.get('artifact_sha256') != digest or record.get('revision') != revision:
                raise ValueError('Record artifact or revision mismatch')
        validate_records(final['contract'], final['records'])
        if digest != accepted:
            raise ValueError('Frozen final artifact differs from the accepted emit package')
    equivalence = payload.get('equivalence')
    if equivalence is not None and equivalence.get('frozen_artifact_sha256') != accepted:
        raise ValueError('Evaluated artifact differs from the accepted emit package')


def seal_run_evidence_v2(payload: dict, path: Path, password_file: Path) -> dict:
    """Seal benchmark-run-evidence/2 after re-hashing every accepted artifact it names. An identity that does not hold or
    an accepted record whose bytes changed raises IntegrityError: no retry seals it."""
    from .truth import seal
    from .integrity import IntegrityError, digest as artifact_digest
    try:
        _validate_payload_v2(payload)
    except ValueError as error:
        raise IntegrityError(str(error)) from None
    for row in payload['accepted_artifacts']:
        try:
            observed = artifact_digest(Path(row['path']))
        except (OSError, ValueError):
            observed = None
        if observed != row['sha256']:
            raise IntegrityError('Accepted artifact bytes changed before sealing: ' + Path(row['path']).name)
    import json
    from .mcp_reuse import required, verified
    emits = [row for row in payload['accepted_artifacts'] if row['stage'] == 'emit' and row['kind'] == 'artifact']
    emitted = next((row for row in emits if row['assignment_id'] == emits[-1]['assignment_id']), None) if emits else None
    manifest = Path(emitted['path'])/'manifest.json' if emitted else None
    package_schema = json.loads(manifest.read_text()).get('schema') if manifest and manifest.is_file() else None
    proof = payload.get('mcp_reuse', {})
    if package_schema == 'interface-package/2' and proof.get('package_schema') != package_schema:
        raise IntegrityError('Generated MCP proof must retain the frozen package format')
    final = payload.get('final')
    if final is not None and required(proof, payload['execution_snapshot']):
        if (final.get('generated_reuse_required') is not True or
                final.get('generated_reuse_verified') is not verified(proof, final['frozen_artifact_sha256'])):
            raise IntegrityError('Generated MCP proof differs from the final evaluation')
    digest = seal(payload, path, Path(password_file).read_text(encoding='utf-8').strip())
    return {'path': Path(path).name, 'sha256': digest}


def seal_run_evidence(payload: dict, path: Path, password_file: Path) -> dict:
    from .truth import seal
    _validate_payload(payload)
    from .integrity import digest as artifact_digest
    for artifact in payload.get('accepted_artifacts', []):
        if 'path' in artifact and artifact_digest(Path(artifact['path'])) != artifact['sha256']:
            raise ValueError('Accepted artifact bytes changed before sealing')
    digest = seal(payload, path, Path(password_file).read_text(encoding='utf-8').strip())
    return {'path': Path(path).name, 'sha256': digest}


def _gate_results(payload, evaluations):
    """Sealed evaluator checks establish structural gates; referenced behavior is regraded."""
    from .case_assets import STAGES
    from .behavior import _sha
    artifacts = payload.get('accepted_artifacts', [])
    identities = {(row.get('stage'), row.get('revision'), row.get('assignment_id'), row.get('sha256'))
                  for row in artifacts}
    final = {row['revision']: row for row in evaluations if row['phase'] == 'final'}
    rows, seen = [], set()
    for gate in payload['accepted_gates']:
        key = (gate.get('stage'), gate.get('revision'))
        identity = (*key, gate.get('assignment_id'), gate.get('artifact_sha256'))
        checks = gate.get('checks', [])
        refs = gate.get('evaluation_refs', [])
        ok = (key not in seen and key[0] in STAGES and type(key[1]) is int and key[1] >= 0
              and isinstance(identity[2], str) and bool(identity[2]) and _sha(identity[3])
              and identity in identities and bool(checks)
              and len({row.get('id') for row in checks}) == len(checks)
              and all(isinstance(row.get('id'), str) and row['id'] and row.get('passed') is True for row in checks))
        seen.add(key)
        for ref in refs:
            matches = [row for row in evaluations if (row['phase'], row['revision']) == (ref.get('phase'), ref.get('revision'))]
            ok = ok and ref.get('revision') == key[1] and len(matches) == 1 and matches[0]['verdict'] == 'passed'
        if key[0] in ('emit', 'reuse') and key[1] in final:
            ok = ok and identity[3] == final[key[1]]['frozen_artifact_sha256']
        if key[0] == 'reuse':
            ok = (ok and {'phase': 'final', 'revision': key[1]} in refs
                  and {'fresh_worker_mission', 'frozen_final_behavior'} <= {c.get('id') for c in checks})
        rows.append({**_scalars(gate, ('stage', 'revision', 'assignment_id', 'artifact_sha256')),
                     'verdict': 'passed' if ok else 'failed', 'passed': int(bool(ok)), 'total': 1})
    required = payload['case_pin']['manifest'].get('required_stages', [])
    inventory_ok = required == list(STAGES) and len(final) == 1
    for revision in final:
        for stage in required:
            target = 0 if stage == 'acquire' else revision
            matches = [row for row in rows if (row['stage'], row['revision']) == (stage, target)]
            inventory_ok = inventory_ok and len(matches) == 1 and matches[0]['verdict'] == 'passed'
    return rows, bool(inventory_ok and all(row['verdict'] == 'passed' for row in rows))


def _execution_identity(report, payload):
    """The report names the sealed run's case pin, execution snapshot and executed identity, and no other."""
    snapshot = payload['execution_snapshot']
    if (report.get('case_pin') != public_pin(payload['case_pin']) or
            report.get('snapshot_sha256') != snapshot['snapshot_sha256'] or
            report.get('executed') != _executed(snapshot['executed'])):
        raise ValueError('Report and sealed execution identity mismatch')
    expected_aliases = {key: payload['case_pin'].get(key) for key in
                        ('case_version', 'evaluator_version', 'scenario_id', 'case_seed')}
    # A replayed candidate's label comes from the sealed snapshot's runtime, so a relabelled replay never matches it.
    from .labels import execution_label
    execution = execution_label(payload['case_pin'].get('execution', 'unclassified'),
                                (snapshot['executed'].get('runtime_configuration') or {}).get('runtime'))
    expected_aliases.update(case=payload['case_pin']['case_id'], execution=execution,
        model_benchmark=execution in ('actual-agent-emulation', 'actual-agent-physical'),
        **{key: snapshot['executed'][key] for key in
           ('evaluator_revision', 'toolchain_revision', 'skills_revision')})
    if any(key in report and report[key] != value for key, value in expected_aliases.items()):
        raise ValueError('Conflicting public execution identity')
    return expected_aliases


def _installed_evaluator(snapshot):
    """A sealed run regrades under the grader it was sealed under, whatever has changed in the system since."""
    if snapshot['executed']['evaluator_revision'] != grader_identity()['sha256']:
        raise ValueError('Installed evaluator implementation hash mismatch')


def regrade_v2(report: dict, evidence_path: Path, password_file: Path) -> dict:
    """Regrade an earlier-protocol report (benchmark-report/2) from its sealed benchmark-run-evidence/1 by its gates and
    final evaluations. A run that names its protocol, as an assistive run does, is reported as benchmark-report/3 and is
    never scored here; no run of the earlier protocol names one."""
    from .truth import unlock
    from .behavior import validate_records
    if (report.get('progress') or {}).get('protocol') is not None:
        raise ValueError('A run that names its protocol is reported and scored as benchmark-report/3; reports never mix protocols')
    if not evidence_path or not password_file:
        raise ValueError('V2 scoring requires evidence and password-file paths')
    payload = unlock(evidence_path, Path(password_file).read_text(encoding='utf-8').strip(),
                     report['final_evaluation']['evidence_sha256'])
    _validate_payload(payload)
    _installed_evaluator(payload['execution_snapshot'])
    if report.get('schema') != 'benchmark-report/2':
        raise ValueError('Report and sealed execution identity mismatch')
    _execution_identity(report, payload)
    identity_fields = ('phase', 'revision', 'frozen_artifact_sha256')
    if ([_scalars(row, identity_fields) for row in report.get('evaluations', [])] !=
            [_scalars(row, identity_fields) for row in payload['evaluations']]):
        raise ValueError('Report evaluation artifact identity mismatch')
    evaluations = [{**_scalars(row, identity_fields), **_scalars(validate_records(row['contract'], row['records']), COUNTS)}
                   for row in payload['evaluations']]
    gates, gates_ok = _gate_results(payload, evaluations)
    from .mcp_reuse import required, gate_passed
    if required(payload.get('mcp_reuse', {}), payload['execution_snapshot']):
        gates_ok = gates_ok and any(g.get('stage') == 'reuse' and gate_passed(payload, g)
                                   for g in payload.get('accepted_gates', []))
    finals = [row for row in evaluations if row['phase'] == 'final']
    passed, total = sum(row['passed'] for row in finals), sum(row['total'] for row in finals)
    ok = bool(finals) and all(row['verdict'] == 'passed' for row in finals) and gates_ok
    verdict, final = ('passed' if ok else 'failed'), ('passed' if finals and passed == total else 'failed')
    return public_v2_report({**report, 'evaluations': evaluations, 'accepted_gates': gates, 'verdict': verdict,
        'final_evaluation': {'verdict': final, 'passed': passed, 'total': total,
                             'evidence_sha256': report['final_evaluation']['evidence_sha256']}})


def regrade_v3(report: dict, evidence_path: Path, password_file: Path) -> dict:
    """Regrade an assistive run's report (benchmark-report/3) from the benchmark-run-evidence/2 its recorded verdict
    names, sealed by the evaluator in the trial directory. The evidence must hold the report's run, protocol, case pin,
    snapshot and executed identity, an installed evaluator of the snapshot's revision, and the run's single final
    evaluation of its accepted emitted driver, the package and mapping the verdict names. The protocol is the one the
    evidence names, whichever that is; the report must name the same. The final's records are graded again:
    equivalent only when every check passes. A verdict sealed without a final (no driver emitted, no capability
    mapping, an invalid model) is not equivalent. A report with no recorded verdict (awaiting its grade, unscored or
    stopped) names no evidence: it stays unscored and nothing is unlocked."""
    from .truth import unlock
    from .evaluator import final_record
    from .behavior import _sha
    if report.get('schema') != 'benchmark-report/3' or not isinstance(report.get('protocol'), str) or not report['protocol']:
        raise ValueError('Unsupported assistive report schema or protocol')
    recorded = public_equivalence(report.get('equivalence'))
    if recorded is None:
        return public_v3_report({**report, 'verdict': None})
    if not evidence_path or not password_file:
        raise ValueError('Assistive scoring requires evidence and password-file paths')
    if not _sha(recorded.get('evidence_sha256')):
        raise ValueError('The recorded verdict names no sealed evidence')
    payload = unlock(evidence_path, Path(password_file).read_text(encoding='utf-8').strip(), recorded['evidence_sha256'])
    _validate_payload_v2(payload)
    _installed_evaluator(payload['execution_snapshot'])
    # The regraded report says how the run executed as the sealed snapshot does, whether or not the report said so.
    aliases = _execution_identity(report, payload)
    report = {**report, **{key: aliases[key] for key in ('execution', 'model_benchmark')}}
    if (report.get('run_id'), report['protocol']) != (payload['run_id'], payload['protocol']):
        raise ValueError('Report and sealed run identity mismatch')
    sealed, final = public_equivalence(payload.get('equivalence')), payload.get('final')
    ids = lambda record: [row.get('id') for row in record['checks']]
    if (sealed is None or sealed.get('status') not in ('graded', 'no-driver') or ids(recorded) != ids(sealed)
            or any(recorded.get(key) != sealed.get(key) for key in VERDICT_IDENTITY)):
        raise ValueError('Report and sealed verdict identity mismatch')
    if final is None:
        if sealed.get('equivalent') is not False:
            raise ValueError('A verdict sealed without a final evaluation is never equivalent')
        regraded = sealed
    else:
        from .mcp_reuse import required, verified
        proof = payload.get('mcp_reuse', {})
        if required(proof, payload['execution_snapshot']) or final.get('generated_reuse_required'):
            final = {**final, 'generated_reuse_required': True,
                     'generated_reuse_verified': verified(proof, final['frozen_artifact_sha256'])}
        regraded = final_record(final, sealed['capabilities_sha256'], sealed['mission'], protocol=payload['protocol'])
        if (sealed['status'] != 'graded' or final.get('capabilities_sha256') != sealed['capabilities_sha256']
                or ids(regraded) != ids(sealed)):
            raise ValueError('Sealed verdict differs from its final evaluation')
    from .labels import verdict_reason
    return public_v3_report({**report, 'protocol': payload['protocol'], 'verdict': None, 'equivalence_status': None,
                             'reason': verdict_reason(regraded),
                             'equivalence': {**regraded, 'evidence_sha256': recorded['evidence_sha256']}})


def frozen_final_decision(contract: dict, records: list[dict]) -> dict:
    """Terminal evaluator result; private check IDs and values never become feedback."""
    from .behavior import validate_records
    grade = validate_records(contract, records)
    ok = grade['verdict'] == 'passed'
    summary = _scalars(grade, COUNTS)
    return {'ok': ok, 'fault': None if ok else 'model', 'final_evaluation': True,
            'reason': None if ok else 'Frozen final evaluation failed',
            'evaluator': {**summary, 'fault': None if ok else 'model', 'final_evaluation': True}}
