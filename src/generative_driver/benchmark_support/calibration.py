"""Strict evaluator-only family inventory and measured qualification boundary."""
import base64
import binascii
import copy
import hashlib
import json
from .grader_identity import grader_identity
from .snapshots import canonical_digest

BUILD_FILES = {'build/build-report.json', 'build/firmware.elf', 'build/firmware.bin'}
_INPUT_FILES = {'AUTHORING.json', 'LICENSE', 'source/startup.c', 'source/board_uart.c',
    'source/application.c', 'source/linker.ld', 'build.py', 'build-recipe.json',
    'native/platform.repl', 'native/device.resc', 'native/observation-map.json',
    'diagnostic/episodes.json', 'final/episodes.json', 'oracle/vectors.json', 'mutants/manifest.json',
    *{f'reference-{ref}/{name}.json' for ref in ('a', 'b') for name in ('model', 'capabilities', 'replies')}}
_FAMILIES = {'sampled-sensor', 'parameter-store'}


def inventory(family, *, qualified=False):
    if family not in _FAMILIES:
        raise ValueError('Unsupported family')
    return (_INPUT_FILES | BUILD_FILES | ({'native/SampleSource.cs'} if family == 'sampled-sensor' else set())
            | ({'calibration/calibration.json'} if qualified else set()))


def _files(root):
    if root.is_symlink():
        raise ValueError('Inventory symlinks are forbidden')
    paths = list(root.rglob('*'))
    if any(path.is_symlink() or not (path.is_dir() or path.is_file()) for path in paths):
        raise ValueError('Inventory requires regular files and directories without symlinks')
    return {path.relative_to(root).as_posix() for path in paths if path.is_file()}


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
    'tq9': {'wrong-scale','constant-output','wrong-state','unsigned-temperature','wrong-temperature-scale',
            'wrong-temperature-unit','wrong-temperature-byteorder','wrong-crc','wrong-duty-effect'},
    'sampled-sensor': {'constant','scale','signedness','stale'},
    'parameter-store': {'constant','scale','order','wrong-bank','abort','idle-only-update','short-rejection-frame'},
    'zephyr-modbus': {'constant-read', 'wrong-byteorder', 'wrong-register', 'wrong-coil', 'omit-write', 'wrong-request-crc'},
}
_REFERENCE_PHASES = {('diagnostic','original'), ('final','original')}


def _sha(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def reference_model_bytes(model):
    """Portable reference input: explicit UTF-8 and LF, independent of host I/O."""
    normalized={**model,'channel':{'type':'tcp'}}
    return (json.dumps(normalized,indent=2,allow_nan=False)+'\n').encode('utf-8')


def execution_input_identity(model, capabilities):
    """Predict the exact model file bytes and canonical task-map identity."""
    return {'model_sha256':hashlib.sha256(reference_model_bytes(model)).hexdigest(),
            'capabilities_sha256':canonical_digest(capabilities)}


def validate_calibration(manifest, record):
    """Validate measured coverage, not candidate success flags. Local trust only. A record admits only under the
    grader identity it was measured with; a run's `runtime_sha256` is provenance, not a check."""
    failures=[]
    try:
        from .execution import artifact_name
        artifact = artifact_name(manifest)
        native = manifest.get('execution_setup', {}).get('method') == 'native-sim'
        expected={'schema':'benchmark-calibration/1','status':'passed','case':manifest['id'],
            'execution':'reference-calibration','backend':'native-sim' if native else 'native-renode',
            'evaluator_version':manifest['evaluator_version'],'images':manifest['images'],
            'input_hashes':manifest['calibration']['input_hashes'],
            'evaluator_identity':grader_identity(),'native_process_observed':True}
        for key,value in expected.items():
            if record.get(key)!=value:failures.append('identity:'+key)
        if record.get('native_process_observed') is not True:
            failures.append('native process type')
        if set(manifest['images'])!={artifact}:
            failures.append('image inventory')
        if (set(record['input_hashes'])!=({'source','contract','recipe','references','mutations','build'} |
                ({'analysis'} if manifest['family']=='tq9' else {'inventory'}))
                or any(not _sha(v) for v in record['input_hashes'].values())):
            failures.append('input commitment inventory')
        required=REQUIRED_MUTANTS[manifest['family']]
        if set(record['required_mutants'])!=required or len(record['required_mutants'])!=len(required):
            failures.append('family mutant inventory')
        runs={r['id']:r for r in record['runs']}
        if not runs or len(runs)!=len(record['runs']):failures.append('run inventory')
        for run in runs.values():
            process=run['process']
            if native and (run.get('backend') != 'native-sim' or process.get('backend') != 'native-sim'):
                failures.append('run execution backend:'+run['id'])
            if manifest['family']=='parameter-store':
                from .parameter_store import REQUIRED_SCENARIOS
                if not REQUIRED_SCENARIOS <= {c.get('scenario') for c in run['checks']}:
                    failures.append('transaction scenario coverage:'+run['id'])
            expected_image=manifest['images'][artifact]
            if (run['phase'],run['scenario']) not in _REFERENCE_PHASES or type(run['passed']) is not bool:
                failures.append('run phase:'+run['id'])
            if (run['native_process_observed'] is not True or type(process.get('pid')) is not int
                    or process['pid']<=0 or not process.get('executable')
                    or process.get('image_sha256')!=run['image_sha256']
                    or run['image_sha256'] != expected_image
                    or any(not _sha(run.get(k)) for k in ('evidence_sha256','contract_sha256','model_sha256','capabilities_sha256','runtime_sha256'))
                    or not run['checks']):failures.append('run evidence:'+run['id'])
            if (len(run['check_ids'])!=len(set(run['check_ids']))
                    or {c['id'] for c in run['checks']} != set(run['check_ids'])
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
            if (set(ref['execution_inputs'])!=set(ref['runs'])
                    or any(ref['execution_inputs'][r['id']]!={k:r[k] for k in ('model_sha256','capabilities_sha256')} for r in selected)):
                failures.append('reference execution inputs:'+ref['id'])
            if (ref['passed'] is not True or not _sha(ref['model_sha256'])
                    or len(selected)!=2 or {(r['phase'],r['scenario']) for r in selected}!=_REFERENCE_PHASES
                    or any(r['passed'] is not True or any(c['passed'] is not True for c in r['checks']) for r in selected)):
                failures.append('reference coverage:'+ref['id'])
        mutants=record['mutants']
        if {r['id'] for r in mutants}!=required or len(mutants)!=len(required):failures.append('mutant inventory')
        for row in mutants:
            run=runs[row['run']];used.append(row['run'])
            failed={c['id'] for c in run['checks'] if not c['passed']}
            reasons = {'value mismatch','unit mismatch','invalid observation'}
            if row['execution_input']!={k:run[k] for k in ('model_sha256','capabilities_sha256')}:
                failures.append('mutant execution inputs:'+row['id'])
            if (row['rejected'] is not True or not row['expected_failed_checks']
                    or not (set(row['expected_failed_checks']) <= failed if manifest['family'] == 'zephyr-modbus'
                            else set(row['expected_failed_checks']) == set(row['failed_checks']))
                    or len(row['expected_failed_checks'])!=len(set(row['expected_failed_checks']))
                    or len(row['failed_checks'])!=len(set(row['failed_checks']))
                    or failed!=set(row['failed_checks'])
                    or any(c.get('reason') not in reasons
                           for c in run['checks'] if not c['passed'])
                    or not failed <= set(run['check_ids'])
                    or not _sha(row['source_sha256']) or not _sha(row['mutated_sha256'])
                    or row['source_sha256']==row['mutated_sha256']):
                failures.append('mutant evidence:'+row['id'])
            if run['passed'] is not False:
                failures.append('mutant did not fail its native run')
        if len(used)!=len(set(used)) or set(used)!=set(runs):failures.append('run coverage')
        for scenario in ('original',):
            if record['scenarios'][scenario]['passed'] is not True:failures.append('scenario:'+scenario)
        tool_names = {'compiler', 'ghidra', 'java'} if native else {'compiler','objcopy','renode','ghidra','java'}
        if (set(record['tools'])!=tool_names
                or any(not isinstance(v,str) or not v.strip() for v in record['tools'].values())):
            failures.append('native tools')
        if set(record['analysis'])!=set(manifest['images']):failures.append('binary analysis coverage')
        for analysis in record['analysis'].values():
            if type(analysis['returncode']) is not int or analysis['returncode']!=0 or not analysis['export_hashes'] or any(not _sha(v) for v in analysis['export_hashes'].values()):
                failures.append('binary analysis evidence')
    except (KeyError,TypeError,ValueError,AttributeError):
        failures.append('malformed calibration evidence')
    return {'ok':not failures,'failures':failures}


# Admission of tq9-v2: the mutants a record must cover, the input identities it must carry and its check.
REQUIRED_SCENARIOS = ('original',)
CORE_MUTANTS = ('wrong-scale', 'constant-output', 'wrong-state')


def input_hashes(truth):
    return {'source':canonical_digest(truth['source_hashes']), 'contract':canonical_digest(truth['contracts']),
            'recipe':canonical_digest(truth['recipe']), 'references':canonical_digest(truth['references']),
            'mutations':canonical_digest(truth['mutations']), 'build':canonical_digest(truth['build']),
            'analysis':canonical_digest(truth['analysis'])}


def tq9_candidate(truth, reference_name, scenario, mutation=None):
    """Derive the declared legacy reference variant without executing it."""
    reference=truth['references'][reference_name]
    if scenario != 'original':raise ValueError('Unknown reference scenario')
    candidate=copy.deepcopy(reference['model'])
    caps=copy.deepcopy(reference['capabilities'])
    if isinstance(mutation,str):
        task=caps['tasks']['temperature']
        decoder=candidate['operations'][task['operation']]['outputs'][task['outputs']['temperature']['output']]
        if mutation=='wrong-scale':decoder['scale']=decoder.get('scale',1)*10
        elif mutation=='constant-output':decoder.update(scale=0,offset_value=0)
        elif mutation=='wrong-state':candidate['operations'][caps['tasks']['arm']['operation']]=copy.deepcopy(candidate['operations'][caps['tasks']['disarm']['operation']])
        else:raise ValueError('Unknown legacy mutation')
    elif mutation is not None:
        for patch in mutation['patch']:
            if patch['op']!='replace':raise ValueError('Unsupported reference mutant patch')
            parts=patch['path'].split('/')[1:];target=candidate
            for part in parts[:-1]:target=target[int(part)] if isinstance(target,list) else target[part]
            target[int(parts[-1]) if isinstance(target,list) else parts[-1]]=copy.deepcopy(patch['value'])
        for mapping in caps['tasks'].values():
            for output_mapping in mapping['outputs'].values():
                output_mapping['unit']=candidate['operations'][mapping['operation']]['outputs'][output_mapping['output']].get('unit')
    return candidate,caps


def tq9_execution_inputs(truth):
    """Expected identities from authenticated reference and mutation definitions."""
    result={}
    for name in truth['references']:
        for phase in ('diagnostic','final'):
            result[name+'-'+phase+'-original']=execution_input_identity(*tq9_candidate(truth,name,'original'))
    for scenario in REQUIRED_SCENARIOS:
        for mutation in CORE_MUTANTS:
            result[mutation+'-'+scenario]=execution_input_identity(*tq9_candidate(truth,'reference-a',scenario,mutation))
    for mutation in truth['mutations']['mutations']:
        scenario='original'
        result[mutation['id']]=execution_input_identity(*tq9_candidate(truth,'reference-a',scenario,mutation))
    return result


def validate_record(manifest, record):
    checked=validate_calibration({**manifest,'family':'tq9'},record)
    try:
        measured={run['id']:{k:run[k] for k in ('model_sha256','capabilities_sha256')} for run in record['runs']}
        if len(measured)!=len(record['runs']) or measured!=record['execution_inputs']:
            checked['failures'].append('executed input identities')
    except (KeyError,TypeError):checked['failures'].append('missing executed input identities')
    return {'ok':not checked['failures'],'failures':checked['failures']}


def apply_mutation(model, capabilities, mutation):
    """Apply bounded model or capability patches with input commitments."""
    from copy import deepcopy
    source={'model':model,'capabilities':capabilities}
    changed=deepcopy(source)
    patches=mutation['patches']
    if not patches:raise ValueError('Empty mutation')
    for patch in patches:
        kind=patch['target']; op=patch['op']
        if kind not in ('model','capabilities'):
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
    groups={'source':('source/',),'contract':('diagnostic/','final/','oracle/'),
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
    scenario_phases={'original':deepcopy(phases)}
    for selected in scenario_phases.values():
        for phase in selected.values():
            if payload['family']=='parameter-store':
                from .parameter_store import validate_phase
                validate_phase(phase)
            actions=phase['actions']; checks=phase['contract']['checks']
            required={c['id'] for c in checks}; observed=[i for a in actions if a['kind']=='observe' for i in a['checks']]
            if (not required or set(observed)!=required or len(observed)!=len(set(observed))
                    or not any(a['kind']=='call' for a in actions)
                    or not actions or actions[0]['kind']!='reset'):
                raise ValueError('Scored phase lacks complete reset/call/observe coverage')
    return {**payload,'recipe':recipe,'input_hashes':inputs,'phases':phases,'scenario_phases':scenario_phases,
        'images':{name:hashes['build/'+name] for name in ('firmware.bin',)},
        'references':{ref:{key:read('reference-'+ref+'/'+key+'.json') for key in ('model','capabilities')}
                      for ref in ('a','b')},'mutations':read('mutants/manifest.json'),
        'oracle':read('oracle/vectors.json'),
        'contracts':{'time_policy':{'sample_settle_seconds':recipe.get('settle_seconds',0)}},
        **({'calibration':read('calibration/calibration.json')} if payload['inventory_state']=='qualified' else {})}


def authoring_payload(root):
    """Read only exact private inputs; qualification excludes its own record hash."""
    from pathlib import Path
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


def safe_summary(record):
    return {'ok':record.get('ok') is True,'case':record.get('case'),
        'execution':record.get('execution'),'backend':record.get('backend'),
        'status':record.get('status'),'references':len(record.get('references',[])),
        'mutants':len(record.get('mutants',[])),'evidence_sha256':canonical_digest(
            {k:v for k,v in record.items() if k not in ('ok','failures')})}
