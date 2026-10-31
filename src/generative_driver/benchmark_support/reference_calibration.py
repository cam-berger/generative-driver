"""Measured native reference calibration, stamped with the grader identity it ran under. Running candidate models and
emitting reference packages is the system's side of calibration, through this checkout's toolkit and runtime; the
grader grades each emitted package as a frozen one.

The password holder is the local trust authority; records are not third-party attestation.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .grader_identity import grader_identity
from .snapshots import canonical_digest
# The evaluator admits tq9-v2 by these, so they are defined in its calibration module and imported here.
from .calibration import (CORE_MUTANTS, REQUIRED_SCENARIOS, input_hashes, tq9_candidate, tq9_execution_inputs,
                          validate_record)


def model_invoker(model_dir, capabilities, binding, output_dir, probes):
    """Use the toolkit's normal probe operation; retain executable probe evidence."""
    import json
    from pathlib import Path
    from ..toolkit import call_tool
    from .behavior import bind_task
    from .emulated_actions import _binding_failure
    model = json.loads((Path(model_dir)/'model.json').read_text())
    def invoke(task, inputs, grants):
        try:
            request = bind_task(capabilities, task, inputs, model)
        except ValueError as error:
            return _binding_failure(task, error)
        probed = call_tool('probe_run', {'run_dir': str(output_dir), 'model_dir': str(model_dir),
            **request, 'n': 1, 'binding': binding, 'allow_effects': grants})
        results = probed.get('results')
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return {'ok': False, 'error': {'fault': 'host', 'code': 'probe_execution_unavailable',
                'message': 'Probe tool returned no runtime execution evidence'},
                'outputs': {}, 'units': {}, 'transcript': []}
        if probed.get('probe'):
            probes.append(probed['probe'])
        result = results[0]
        outputs = capabilities['tasks'][task]['outputs']
        return {**result, 'ok': bool(probed.get('ok') and result.get('ok')),
            'outputs': {key: result.get('outputs', {}).get(mapping['output']) for key, mapping in outputs.items()},
            'units': {key: result.get('units', {}).get(mapping['output']) for key, mapping in outputs.items()}}
    return invoke


def run_reference(pin, truth, model, capabilities, *, renode, image, output_dir, phase, private_dir=None):
    """Run a reference or mutant candidate model on a fresh emulator it starts and stops, through this checkout's toolkit
    and runtime. The diagnostic phase executes the model with the toolkit's probe operation and grades its records. The
    final phase probes the model's identity and emits its package with `emit_package`, which calibration then has
    `evaluator.reference_execute` grade as a frozen package. The result has reference_execute's shape, with `package_dir`
    and `package_sha256` when it emitted a package; an emission runs no behavior plan, so its contract, records, grade and
    artifact_execution are None. The emulator session keeps its files in `private_dir` when given."""
    if pin.get('scenario_id') != 'original':
        raise ValueError('Unknown case scenario')
    if phase not in ('diagnostic', 'final'):
        raise ValueError('Unknown behavior phase')
    import interface_runtime
    from interface_runtime.engine import validate_model
    from ..toolkit import call_tool
    from .behavior import validate_records
    from .calibration import reference_model_bytes
    from .case_assets import write_json
    from .emulated_actions import execute_plan, run_call
    from .execution import start_session
    from .instruments import family_for
    from .integrity import digest
    candidate = {**model, 'channel': {'type': 'tcp'}}
    if not validate_model(candidate)['ok']:
        raise ValueError('Reference candidates must be structurally valid before native calibration')
    family = family_for(pin.get('case_id', 'tq9-v2'))
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    model_dir = output/'model'
    model_dir.mkdir(parents=True, exist_ok=True)
    # A model with no package is written in the portable form; an emitted package copies these bytes.
    (model_dir/'model.json').write_bytes(reference_model_bytes(candidate))
    model_hash = hashlib.sha256((model_dir/'model.json').read_bytes()).hexdigest()
    probes = []
    session = start_session(renode=renode, image=image, recipe=truth['recipe'], private_dir=private_dir)
    try:
        package = contract = records = grade = None
        if phase == 'final':
            probed = run_call(session, lambda operation, parameters: call_tool('probe_run', {
                'run_dir': str(output/'tools'), 'model_dir': str(model_dir), 'operation': operation,
                'parameters': {}, 'binding': session.binding, 'n': 1}),
                {'operation': candidate['identity']['operation'], 'parameters': {}})
            if not probed.get('ok') or not probed.get('probe'):
                raise ValueError('Reference identity probe could not support package emission')
            probes.append(probed['probe'])
            emitted = call_tool('emit_package', {'run_dir': str(output/'tools'),
                'model_dir': str(model_dir), 'probe': probed['probe']})
            if not emitted.get('ok'):raise ValueError('Reference package emission failed')
            package = Path(emitted['package_dir'])
        else:
            invoke = model_invoker(model_dir, capabilities, session.binding, output/'tools', probes)
            local = {**truth, 'artifact_sha256': model_hash}
            contract = family.contract(pin, local, phase)
            records = execute_plan(session, invoke, family.build_plan(pin, local, phase), contract,
                output/'raw.json', family.observations, truth['contracts']['time_policy']['sample_settle_seconds'])
            grade = validate_records(contract, records)
        runtime_root = Path(interface_runtime.__file__).parent
        runtime_hash = canonical_digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in sorted(runtime_root.glob('*.py'))})
        result = {'execution': 'reference-calibration',
            'backend': 'native-sim' if truth['recipe'].get('backend') == 'native-sim' else 'native-renode',
            'native_process_observed': session.process is not None and session.process.poll() is None,
            'process': session.info, 'contract': contract, 'records': records, 'grade': grade, 'probes': probes,
            'model_sha256': model_hash, 'capabilities_sha256': canonical_digest(capabilities),
            'package_sha256': digest(package) if package is not None else None,
            'artifact_execution': None if package is not None else 'model-runtime',
            'package_dir': str(package) if package is not None else None, 'runtime_sha256': runtime_hash}
        write_json(output/'evaluation.json', result)
        return result
    finally:
        session.stop()


def calibrate(case_id, options):
    """Build, analyze and execute references/mutants; emit an evaluator-only release.

    Required options: evaluator_password_file, renode, ghidra_home, java_home,
    output. Compiler comes from compiler, ARM_NONE_EABI_GCC, or PATH.
    """
    import re
    import shutil
    from .authoring import evaluator_directory, build_case
    from .registry import resolve_case, pin_case
    from .emulator import truth_for_case
    from .evaluator import reference_execute
    from .truth import seal
    from .cases import _write
    from ..benchmark import case_root
    from ..toolkit import resources_root
    definition=resolve_case(case_id)
    from .registrations import registration_for
    registration = registration_for(case_id)
    family_case = registration.calibration_kind == 'family'
    if family_case:
        from .calibration import (authoring_payload, hydrate_truth, apply_mutation,
                                  validate_calibration, REQUIRED_MUTANTS, execution_input_identity)
        from .asset_build import build_family, inventory
        truth = hydrate_truth(authoring_payload(evaluator_directory(options['authoring_root'])))
    elif registration.calibration_kind == 'reference':
        truth=truth_for_case(case_root(),definition.manifest,options)
    else:
        raise ValueError('No reference calibration recipe registered for this case')
    output=evaluator_directory(options['output'])
    if output.exists() and any(output.iterdir()):
        raise ValueError('Calibration requires an empty evaluator output directory')
    output.mkdir(parents=True,mode=0o700,exist_ok=True)
    identity=grader_identity()
    record={'schema':'benchmark-calibration/1','status':'pending','case':case_id,'evaluator_version':definition.evaluator_version,
        'execution':'reference-calibration','backend':'native-renode','images':definition.manifest['images'],
        'input_hashes':truth['input_hashes'] if family_case else input_hashes(truth),'evaluator_identity':identity,'native_process_observed':False,
        'references':[],'mutants':[],'scenarios':{},'required_mutants':sorted(REQUIRED_MUTANTS[truth['family']] if family_case else set(CORE_MUTANTS)|set(truth['mutations']['required'])),
        'tools':{},'runs':[]}
    def save():_write(output/'calibration-record.json',record)
    save()
    try:
        if not family_case:record['execution_inputs']=tq9_execution_inputs(truth)
        compiler=options.get('compiler') or os.environ.get('ARM_NONE_EABI_GCC') or shutil.which('arm-none-eabi-gcc')
        if not compiler:
            raise ValueError('Set --compiler or ARM_NONE_EABI_GCC to the pinned native compiler')
        if any(not options.get(name) for name in ('renode','ghidra_home','java_home')):
            raise ValueError('Native Renode, Ghidra and Java paths are required')
        tool_paths={name:Path(options[name]).expanduser().resolve() for name in ('renode','ghidra_home','java_home')}
        if not tool_paths['renode'].is_file() or not (tool_paths['ghidra_home']/'support/analyzeHeadless').is_file():
            raise ValueError('Native Renode and Ghidra installations are required')
        java=tool_paths['java_home']/'bin'/('java.exe' if os.name=='nt' else 'java')
        if not java.is_file():raise ValueError('Native Java installation is required')
        author=output/'authoring';author.mkdir()
        if family_case:
            source = evaluator_directory(options['authoring_root'])
            for name in sorted(inventory(truth['family'])):
                target = author/name; target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source/name,target)
            built=build_family(author,Path(compiler),output/'build')
            build_dir=output/'build/build-1'
            built={**built,'images':{name:built['images'][name] for name in record['images']}}
            truth=hydrate_truth(authoring_payload(author))
            record['input_hashes']=truth['input_hashes']
        else:
            _write(author/'build.json',truth['build'])
            for name,expected in truth['source_hashes'].items():
                from .authoring import _within
                target=_within(author,name);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(truth['source_files'][Path(name).name],encoding='utf-8')
                if hashlib.sha256(target.read_bytes()).hexdigest()!=expected:raise ValueError('Authoring source identity mismatch')
            built=build_case(author,Path(compiler),output/'build')
            build_dir=output/'build'
        if built['images']!=record['images']:raise ValueError('Calibration rebuild differs from distributed images')
        record['tools'].update(built['tool_versions'])
        renode_version=subprocess.run([str(tool_paths['renode']),'--version'],capture_output=True,text=True,check=True,timeout=30)
        java_version=subprocess.run([str(java),'-version'],capture_output=True,text=True,check=True,timeout=30)
        properties=(tool_paths['ghidra_home']/'Ghidra/application.properties').read_text()
        version=re.search(r'^application.version=(.+)$',properties,re.M)
        if not version:raise ValueError('Ghidra version was not observed')
        record['tools'].update(renode=renode_version.stdout.strip(),java=(java_version.stderr or java_version.stdout).strip(),ghidra=version.group(1).strip())
        analysis = {'base_address':'0x08000000','processor':'ARM:LE:32:Cortex'} if family_case else truth['analysis']
        record['analysis']={}
        for image_name in record['images']:
            exports=output/('exports-'+Path(image_name).stem)
            analyze=[sys.executable,str(resources_root()/'tools/workspace/ghidra_run.py'),
                '--workspace',str(output/'analysis'), '--image',str(build_dir/image_name),
                '--base-address',analysis['base_address'],'--processor',analysis['processor'],
                '--ghidra-path',str(tool_paths['ghidra_home']),'--java-home',str(tool_paths['java_home']),
                '--export-dir',str(exports),'--script-path',str(resources_root()/'toolchain/skills/interpret-firmware-binary'),
                '--timeout-s','600']
            if family_case:
                analyze.extend(['--prescript','SeedCortexM.java'])
            with tempfile.TemporaryDirectory(prefix='gd-calibration-') as analysis_workspace:
                analyze[analyze.index('--workspace')+1] = analysis_workspace
                analyzed=subprocess.run(analyze,capture_output=True,text=True,timeout=660)
                for name in ('logs', 'projects'):
                    source = Path(analysis_workspace)/name
                    if source.exists():shutil.copytree(source,output/(Path(image_name).stem+'-analysis-'+name))
            (output/(image_name+'.ghidra.stdout')).write_text(analyzed.stdout)
            (output/(image_name+'.ghidra.stderr')).write_text(analyzed.stderr)
            analysis_result=json.loads(analyzed.stdout)
            if analyzed.returncode or analysis_result.get('markers',{}).get('script_error'):
                raise RuntimeError('Native Ghidra analysis failed; see private logs')
            record['analysis'][image_name]={'argv':analyze,'returncode':analyzed.returncode,
                'export_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in exports.iterdir() if p.is_file()}}
            save()
        def run(model, capabilities, scenario, name, phase='final'):
            image='firmware.bin'
            pin=pin_case(case_id,'original',0)
            result=run_reference(pin,truth,model,capabilities,renode=tool_paths['renode'],phase=phase,
                image=build_dir/image,output_dir=output/'runs'/name)
            if phase=='final':
                # The grader grades the emitted reference package as it grades a trial's frozen one, on a fresh emulator.
                result=reference_execute(pin,truth,capabilities,renode=tool_paths['renode'],image=build_dir/image,
                    output_dir=output/'runs'/name,package=Path(result['package_dir']),
                    expected_package_sha256=result['package_sha256'])
            record['runs'].append({'id':name,'scenario':scenario,'phase':phase,
                'native_process_observed':result['native_process_observed'],'process':result['process'],
                'image_sha256':record['images'][image], 'evidence_sha256':hashlib.sha256((output/'runs'/name/'evaluation.json').read_bytes()).hexdigest(),
                'contract_sha256':canonical_digest(result['contract']), 'checks':result['grade']['checks'],
                'check_ids':[c['id'] for c in result['contract']['checks']],
                'passed':result['grade']['verdict']=='passed',
                **{k:result[k] for k in ('model_sha256','capabilities_sha256','package_sha256','runtime_sha256','artifact_execution')}})
            save()
            return result
        if family_case:
            phases=(('diagnostic','original'),('final','original'))
            for name,reference in truth['references'].items():
                run_ids=[];passes=[];execution_inputs={}
                for phase,scenario in phases:
                    model=reference['model']
                    run_id=name+'-'+phase+'-'+scenario
                    execution_inputs[run_id]=execution_input_identity(model,reference['capabilities'])
                    result=run(model,reference['capabilities'],scenario,run_id,phase)
                    run_ids.append(run_id);passes.append(result['grade']['verdict']=='passed')
                record['references'].append({'id':name,'model_sha256':canonical_digest(reference['model']),
                    'passed':all(passes),'runs':run_ids,'execution_inputs':execution_inputs})
                save()
            mutants=truth['mutations']['mutants']
            if {m['id'] for m in mutants}!=REQUIRED_MUTANTS[truth['family']] or len(mutants)!=len(REQUIRED_MUTANTS[truth['family']]):
                raise ValueError('Private required mutant inventory mismatch')
            for mutation in mutants:
                reference=truth['references'][mutation['reference']]
                changed=apply_mutation(reference['model'],reference['capabilities'],mutation)
                scenario=mutation.get('scenario','original')
                result=run(changed['model'],changed['capabilities'],scenario,mutation['id'],mutation['phase'])
                failures=[c for c in result['grade']['checks'] if not c['passed']]
                record['mutants'].append({'id':mutation['id'],'run':mutation['id'],'rejected':bool(failures),
                    'failed_checks':[c['id'] for c in failures],'expected_failed_checks':mutation['expected_failed_checks'],
                    'execution_input':execution_input_identity(changed['model'],changed['capabilities']),
                    'source_sha256':changed['source_sha256'],'mutated_sha256':changed['mutated_sha256']})
                save()
        else:
            from .calibration import execution_input_identity
            for name,reference in truth['references'].items():
                run_ids=[];passes=[]
                for phase in ('diagnostic','final'):
                    run_id=name+'-'+phase+'-original'
                    result=run(reference['model'],reference['capabilities'],'original',run_id,phase)
                    run_ids.append(run_id);passes.append(result['grade']['verdict']=='passed')
                record['references'].append({'id':name,'model_sha256':canonical_digest(reference['model']),
                    'passed':all(passes),'runs':run_ids,
                    'execution_inputs':{n:record['execution_inputs'][n] for n in run_ids}})
                save()
            reference=truth['references']['reference-a']
            for mutation in [*CORE_MUTANTS,*truth['mutations']['mutations']]:
                name=mutation+'-original' if isinstance(mutation,str) else mutation['id']
                identifier=mutation if isinstance(mutation,str) else mutation['id']
                candidate,caps=tq9_candidate(truth,'reference-a','original',mutation)
                result=run(candidate,caps,'original',name,phase='final')
                failures=[c for c in result['grade']['checks'] if not c['passed']]
                record['mutants'].append({'id':identifier,'run':name,'rejected':bool(failures),
                    'failed_checks':[c['id'] for c in failures],
                    'expected_failed_checks':truth['mutations']['expected_failed_checks'][identifier],
                    'execution_input':execution_input_identity(candidate,caps),
                    'source_sha256':canonical_digest({'model':reference['model'],'capabilities':reference['capabilities']}),
                    'mutated_sha256':canonical_digest({'model':candidate,'capabilities':caps})})
                save()
        record['native_process_observed']=all(r['native_process_observed'] for r in record['runs']) and bool(record['runs'])
        record['scenarios']={name:{'passed':all(r['passed'] for r in record['runs'] if r['scenario']==name and (r['id'].startswith(('a-','b-')) if family_case else r['id'].startswith('reference-')))} for name in REQUIRED_SCENARIOS}
        record['status']='passed'
        manifest=copy.deepcopy(definition.manifest)
        manifest['calibration']={'input_hashes':record['input_hashes']}
        checked=(validate_calibration if family_case else validate_record)(manifest,record)
        if not checked['ok']:record.update(status='pending',failures=checked['failures'])
    except (OSError,ValueError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
        record.update(status='pending',failure=type(error).__name__+': '+str(error))
    if grader_identity()!=identity:record.update(status='pending',failure='Evaluator implementation changed during calibration')
    save()
    if family_case:
        payload=authoring_payload(author if 'author' in locals() and (author/'AUTHORING.json').exists() else evaluator_directory(options['authoring_root']))
        payload['inventory'].pop('calibration/calibration.json',None)
        payload['inventory_state']='pending'
        if record['status']=='passed':
            encoded=json.dumps(record,sort_keys=True,allow_nan=False).encode()
            payload['inventory']['calibration/calibration.json']={'sha256':hashlib.sha256(encoded).hexdigest(),
                'base64':__import__('base64').b64encode(encoded).decode('ascii')}
            payload['inventory_state']='qualified'
        payload['inventory_sha256']=canonical_digest({n:e['sha256'] for n,e in payload['inventory'].items()})
        truth=payload
    else:
        truth['calibration']=record
    manifest=copy.deepcopy(definition.manifest)
    manifest['calibration']={'status':record['status']}
    if record['status']=='passed':
        manifest['calibration'].update(evidence_sha256=canonical_digest(record),input_hashes=record['input_hashes'])
        manifest['limitations']=[x for x in manifest['limitations'] if x not in ('Calibration and actual-agent admission pending','Reference calibration pending')]
    manifest['truth']['sha256']=seal(truth,output/(case_id+'.enc'),Path(options['evaluator_password_file']).read_text().strip())
    _write(output/'case.json',manifest)
    return {'ok':record['status']=='passed','verdict':'passed' if record['status']=='passed' else 'failed',
            'execution':'reference-calibration','model_benchmark':False,'output':str(output),
            'evidence_sha256':canonical_digest(record),'calibration':record['status']}
