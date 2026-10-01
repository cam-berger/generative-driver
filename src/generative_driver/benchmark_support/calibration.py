"""Strict evaluator-only family inventory and measured qualification boundary."""
import base64
import binascii
import hashlib
import json
from .asset_build import inventory
from .snapshots import canonical_digest


def decode_inventory(payload):
    """Authenticate the exact allowlisted bytes before interpreting private evidence."""
    try:
        family = payload['family']
        state = payload['inventory_state']
        entries = payload['inventory']
        if (payload['schema'] != 'benchmark-family-truth/1' or payload['case_id'] != family + '-v1'
                or state not in ('pending', 'qualified')
                or set(entries) != inventory(family, qualified=state == 'qualified')):
            raise ValueError('Invalid family inventory identity')
        decoded = {}
        for name, entry in entries.items():
            if set(entry) != {'sha256', 'base64'}:
                raise ValueError('Invalid inventory entry')
            data = base64.b64decode(entry['base64'], validate=True)
            if hashlib.sha256(data).hexdigest() != entry['sha256']:
                raise ValueError('Inventory bytes changed')
            decoded[name] = data
        if canonical_digest({k:v['sha256'] for k,v in entries.items()}) != payload['inventory_sha256']:
            raise ValueError('Inventory commitment changed')
        return decoded
    except (KeyError, TypeError, binascii.Error) as error:
        raise ValueError('Malformed family inventory') from error

REQUIRED_MUTANTS = {
    'sampled-sensor': {'constant','scale','signedness','stale','false-drift'},
    'parameter-store': {'constant','scale','order','wrong-bank','abort','false-drift'},
}
_REFERENCE_PHASES = {('diagnostic','original'), ('final','original'),
                     ('maintenance','semantic'), ('maintenance','control')}


def _sha(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def validate_calibration(manifest, record):
    """Validate measured coverage, not candidate success flags. Local trust only."""
    from .reference_calibration import calibration_identity
    from .scenarios import maintenance_decision
    failures=[]
    try:
        expected={'schema':'benchmark-calibration/1','status':'passed','case':manifest['id'],
            'execution':'reference-calibration','backend':'native-renode',
            'evaluator_version':manifest['evaluator_version'],'images':manifest['images'],
            'input_hashes':manifest['calibration']['input_hashes'],
            'evaluator_identity':calibration_identity(),'native_process_observed':True}
        for key,value in expected.items():
            if record.get(key)!=value:failures.append('identity:'+key)
        if record.get('native_process_observed') is not True:
            failures.append('native process type')
        if set(manifest['images'])!={'firmware.bin','firmware-drift.bin'}:
            failures.append('image inventory')
        if (set(record['input_hashes'])!={'source','contract','recipe','references','mutations','build','inventory'}
                or any(not _sha(v) for v in record['input_hashes'].values())):
            failures.append('input commitment inventory')
        runtime=canonical_digest({k.split('/',1)[1]:v for k,v in expected['evaluator_identity']['files'].items()
                                  if k.startswith('interface_runtime/') and k.count('/')==1})
        required=REQUIRED_MUTANTS[manifest['family']]
        if set(record['required_mutants'])!=required or len(record['required_mutants'])!=len(required):
            failures.append('family mutant inventory')
        runs={r['id']:r for r in record['runs']}
        if not runs or len(runs)!=len(record['runs']):failures.append('run inventory')
        for run in runs.values():
            process=run['process']
            expected_image=manifest['images']['firmware-drift.bin' if run['scenario']=='semantic' else 'firmware.bin']
            if (run['phase'],run['scenario']) not in _REFERENCE_PHASES or type(run['passed']) is not bool:
                failures.append('run phase:'+run['id'])
            if (run['native_process_observed'] is not True or type(process.get('pid')) is not int
                    or process['pid']<=0 or not process.get('executable')
                    or process.get('image_sha256')!=run['image_sha256']
                    or run['image_sha256'] != expected_image
                    or any(not _sha(run.get(k)) for k in ('evidence_sha256','contract_sha256','model_sha256','runtime_sha256'))
                    or run['runtime_sha256']!=runtime or not run['checks']):failures.append('run evidence:'+run['id'])
            if (len(run['check_ids'])!=len(set(run['check_ids']))
                    or {c['id'] for c in run['checks']} != set(run['check_ids']) |
                        ({'maintenance/false-drift'} if run['id']=='false-drift' else set())
                    or len(run['checks']) != len({c['id'] for c in run['checks']})):
                failures.append('run check inventory:'+run['id'])
            if run['phase']=='final' and not _sha(run.get('package_sha256')):
                failures.append('frozen package:'+run['id'])
        refs=record['references']
        if (len(refs)!=2 or len({r['id'] for r in refs})!=2
                or len({r['model_sha256'] for r in refs})!=2):failures.append('distinct references')
        used=[]
        for ref in refs:
            selected=[runs[name] for name in ref['runs']];used.extend(ref['runs'])
            if (ref['passed'] is not True or not _sha(ref['model_sha256'])
                    or len(selected)!=4 or {(r['phase'],r['scenario']) for r in selected}!=_REFERENCE_PHASES
                    or any(r['passed'] is not True or any(c['passed'] is not True for c in r['checks']) for r in selected)):
                failures.append('reference coverage:'+ref['id'])
        mutants=record['mutants']
        if {r['id'] for r in mutants}!=required or len(mutants)!=len(required):failures.append('mutant inventory')
        for row in mutants:
            run=runs[row['run']];used.append(row['run'])
            failed={c['id'] for c in run['checks'] if not c['passed']}
            reasons = {'false drift claim'} if row['id']=='false-drift' else {'value mismatch','unit mismatch','invalid observation'}
            if (row['rejected'] is not True or not row['expected_failed_checks']
                    or not set(row['expected_failed_checks'])<=set(row['failed_checks'])
                    or len(row['expected_failed_checks'])!=len(set(row['expected_failed_checks']))
                    or len(row['failed_checks'])!=len(set(row['failed_checks']))
                    or failed!=set(row['failed_checks'])
                    or any(c.get('reason') not in reasons
                           for c in run['checks'] if not c['passed'])
                    or not failed <= set(run['check_ids']) | ({'maintenance/false-drift'} if row['id']=='false-drift' else set())
                    or not _sha(row['source_sha256']) or not _sha(row['mutated_sha256'])
                    or row['source_sha256']==row['mutated_sha256']):
                failures.append('mutant evidence:'+row['id'])
            if row['id']!='false-drift' and run['passed'] is not False:
                failures.append('mutant did not fail its native run')
            if row['id']=='false-drift':
                decision=maintenance_decision(scenario='control',claim='drift',repaired=False,
                    diagnostic={'evaluable':True,'contradiction':False,'evidence_ids':['native-control'],'fresh_reuse_passed':True})
                if (run['phase']!='maintenance' or run['scenario']!='control'
                        or run.get('native_control_passed') is not True
                        or run.get('assessment_decision')!=decision or run['passed'] is not True
                        or row['failed_checks']!=['maintenance/false-drift']
                        or row.get('source_assessment_sha256')!=canonical_digest({'maintenance_claim':'unchanged'})
                        or row.get('mutated_assessment_sha256')!=canonical_digest({'maintenance_claim':'drift'})):
                    failures.append('false-drift control evidence')
        if len(used)!=len(set(used)) or set(used)!=set(runs):failures.append('run coverage')
        for scenario in ('semantic','control'):
            if record['scenarios'][scenario]['passed'] is not True:failures.append('scenario:'+scenario)
        if (set(record['tools'])!={'compiler','objcopy','renode','ghidra','java'}
                or any(not isinstance(v,str) or not v.strip() for v in record['tools'].values())):
            failures.append('native tools')
        if set(record['analysis'])!=set(manifest['images']):failures.append('binary analysis coverage')
        for analysis in record['analysis'].values():
            if type(analysis['returncode']) is not int or analysis['returncode']!=0 or not analysis['export_hashes'] or any(not _sha(v) for v in analysis['export_hashes'].values()):
                failures.append('binary analysis evidence')
    except (KeyError,TypeError,ValueError,AttributeError):
        failures.append('malformed calibration evidence')
    return {'ok':not failures,'failures':failures}


def apply_mutation(model, capabilities, mutation):
    """Apply bounded data patches; assessment mutations have one exact meaning."""
    from copy import deepcopy
    source={'model':model,'capabilities':capabilities,'assessment':{'maintenance_claim':'unchanged'}}
    changed=deepcopy(source)
    patches=mutation['patches']
    if not patches:raise ValueError('Empty mutation')
    if mutation['id']=='false-drift':
        if (mutation.get('reference') not in ('a','b') or mutation.get('phase')!='maintenance'
                or mutation.get('scenario')!='control'
                or mutation.get('expected_failed_checks')!=['maintenance/false-drift']
                or patches!=[{'target':'assessment','op':'replace','path':'/maintenance_claim','value':'drift'}]):
            raise ValueError('Invalid false-drift assessment mutation')
    for patch in patches:
        kind=patch['target']; op=patch['op']
        if kind not in ('model','capabilities') and not (kind=='assessment' and mutation['id']=='false-drift'):
            raise ValueError('Invalid mutation target')
        if op not in ('replace','add','remove') or set(patch)!={'target','op','path'} | ({'value'} if op!='remove' else set()):
            raise ValueError('Invalid mutation patch')
        path=patch['path']
        if not isinstance(path,str) or not path.startswith('/') or path=='/':raise ValueError('Invalid patch path')
        parts=[p.replace('~1','/').replace('~0','~') for p in path[1:].split('/')]
        target=changed[kind]
        try:
            for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
            key=int(parts[-1]) if isinstance(target,list) else parts[-1]
            if op=='remove':del target[key]
            elif op=='add' and isinstance(target,list):target.insert(key,deepcopy(patch['value']))
            else:
                if op=='replace':target[key]
                target[key]=deepcopy(patch['value'])
        except (KeyError,IndexError,TypeError,ValueError) as error:raise ValueError('Invalid patch location') from error
    before=canonical_digest(source);after=canonical_digest(changed)
    if before==after:raise ValueError('Mutation did not change behavior inputs')
    return {**changed,'source_sha256':before,'mutated_sha256':after}


def hydrate_truth(payload):
    """Project authenticated byte inventory into the existing evaluator engine."""
    from copy import deepcopy
    files=decode_inventory(payload)
    def read(name):return json.loads(files[name])
    hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()
            if name!='calibration/calibration.json'}
    groups={'source':('source/',),'contract':('diagnostic/','final/','maintenance/','oracle/'),
            'recipe':('native/',),'references':('reference-',),'mutations':('mutants/',),
            'build':('build/','build-recipe.json','build.py','AUTHORING.json','LICENSE')}
    inputs={key:canonical_digest({n:h for n,h in hashes.items() if n.startswith(prefixes)})
            for key,prefixes in groups.items()}
    inputs['inventory']=canonical_digest(hashes)
    recipe=read('native/observation-map.json')
    sources=recipe.pop('source_file_paths')
    if any(name not in files or not name.startswith('native/') for name in sources.values()):
        raise ValueError('Recipe source outside authenticated native inventory')
    recipe['source_files']={local:files[name].decode('utf-8') for local,name in sources.items()}
    phases={'diagnostic':read('diagnostic/episodes.json'),'final':read('final/episodes.json')}
    scenario_phases={name:{**deepcopy(phases),'maintenance':read('maintenance/'+name+'.json')}
                     for name in ('semantic','control')}
    for selected in scenario_phases.values():
        for phase in selected.values():
            actions=phase['actions']; checks=phase['contract']['checks']
            required={c['id'] for c in checks}; observed=[i for a in actions if a['kind']=='observe' for i in a['checks']]
            if (not required or set(observed)!=required or len(observed)!=len(set(observed))
                    or not any(a['kind']=='call' for a in actions)
                    or not actions or actions[0]['kind']!='reset'):
                raise ValueError('Scored phase lacks complete reset/call/observe coverage')
    return {**payload,'recipe':recipe,'input_hashes':inputs,'phases':phases,'scenario_phases':scenario_phases,
        'images':{name:hashes['build/'+name] for name in ('firmware.bin','firmware-drift.bin')},
        'references':{ref:{key:read('reference-'+ref+'/'+key+'.json') for key in ('model','capabilities')}
                      for ref in ('a','b')},'mutations':read('mutants/manifest.json'),
        'oracle':read('oracle/vectors.json'),
        'contracts':{'time_policy':{'sample_settle_seconds':recipe.get('settle_seconds',0)}},
        **({'calibration':read('calibration/calibration.json')} if payload['inventory_state']=='qualified' else {})}


def authoring_payload(root):
    """Read only exact private inputs; qualification excludes its own record hash."""
    from pathlib import Path
    from .asset_build import _files
    root=Path(root)
    metadata=json.loads((root/'AUTHORING.json').read_text())
    family=metadata['family']
    actual=_files(root)
    qualified=actual==inventory(family,qualified=True)
    if (actual!=inventory(family) and not qualified) or metadata.get('id')!=family+'-v1':
        raise ValueError('Invalid private family authoring inventory')
    if metadata.get('schema')!='benchmark-authoring/1' or metadata.get('execution')!='reference-build':
        raise ValueError('Native calibration requires independently authored native assets')
    entries={name:{'sha256':hashlib.sha256((root/name).read_bytes()).hexdigest(),
                   'base64':base64.b64encode((root/name).read_bytes()).decode('ascii')} for name in sorted(actual)}
    return {'schema':'benchmark-family-truth/1','case_id':metadata['id'],'family':family,
        'inventory_state':'qualified' if qualified else 'pending','inventory':entries,
        'inventory_sha256':canonical_digest({n:e['sha256'] for n,e in entries.items()})}


def calibrate(case_id, *, authoring_root, renode, ghidra_home, java_home, output_dir):
    """Evaluator Python return includes private evidence. CLI uses safe_summary only."""
    import os
    from pathlib import Path
    from . import authoring
    from .asset_build import require_private_root, _disjoint
    from .registry import resolve_case
    if case_id not in ('sampled-sensor-v1','parameter-store-v1'):raise ValueError('Unknown authored family')
    tool_paths={'renode':Path(renode),'ghidra_home':Path(ghidra_home),'java_home':Path(java_home)}
    compiler=Path(os.environ.get('ARM_NONE_EABI_GCC',''))
    if (any(not p.is_absolute() or not p.exists() for p in tool_paths.values())
            or not compiler.is_absolute() or not compiler.is_file()):
        raise ValueError('Explicit absolute native tool configuration required')
    if (not tool_paths['renode'].is_file()
            or not (tool_paths['ghidra_home']/'support/analyzeHeadless').is_file()
            or not (tool_paths['java_home']/'bin'/('java.exe' if os.name=='nt' else 'java')).is_file()):
        raise ValueError('Complete native tool installations required')
    root=require_private_root(authoring_root,[]);output=require_private_root(output_dir,[])
    _disjoint(root,output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):raise ValueError('Calibration output must be empty')
    payload=authoring_payload(root)
    if payload['case_id']!=case_id:raise ValueError('Authoring case mismatch')
    password=root.parent/(case_id+'.password')
    if not password.is_file() or not password.read_text().strip():raise ValueError('Evaluator password handle required')
    authoring.calibrate(case_id,{'authoring_root':str(root),**{k:str(v.resolve()) for k,v in tool_paths.items()},
        'output_dir':str(output),'output':str(output),'compiler':str(compiler),'evaluator_password_file':str(password)})
    record_path=output/'calibration-record.json'
    if record_path.is_symlink() or not record_path.is_file():raise ValueError('Measured private calibration record missing')
    record=json.loads(record_path.read_text())
    manifest=dict(resolve_case(case_id).manifest)
    manifest['calibration']={'input_hashes':record.get('input_hashes')}
    return {**record,**validate_calibration(manifest,record)}


def safe_summary(record):
    return {'ok':record.get('ok') is True,'case':record.get('case'),
        'execution':record.get('execution'),'backend':record.get('backend'),
        'status':record.get('status'),'references':len(record.get('references',[])),
        'mutants':len(record.get('mutants',[])),'evidence_sha256':canonical_digest(
            {k:v for k,v in record.items() if k not in ('ok','failures')})}


def main():
    import argparse
    from pathlib import Path
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('case_id',choices=('sampled-sensor-v1','parameter-store-v1'))
    for name in ('authoring-root','renode','ghidra-home','java-home','output-dir'):
        parser.add_argument('--'+name,required=True,type=Path)
    args=parser.parse_args()
    try:
        result=calibrate(**vars(args))
    except (ValueError,OSError,KeyError,RuntimeError):
        parser.exit(1,'Native family calibration failed; inspect private evaluator diagnostics.\n')
    print(json.dumps(safe_summary(result),sort_keys=True))
    if not result['ok']:parser.exit(1)


if __name__=='__main__':main()
