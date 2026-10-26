"""Native qualification of authenticated upstream references, never candidate feedback."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

from .snapshots import canonical_digest


def _write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def qualify(payload, manifest, image, output, *, renode, ghidra_home, java_home):
    from .authoring import evaluator_directory
    from .calibration import apply_mutation, execution_input_identity, validate_calibration, REQUIRED_MUTANTS
    from .evaluator import reference_execute
    from .reference_calibration import calibration_identity
    from .upstream import hydrate_truth
    from ..toolkit import resources_root
    truth = hydrate_truth(payload)
    image, output = Path(image).resolve(), evaluator_directory(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Qualification requires an empty private output directory')
    if truth['images'] != manifest['images'] or hashlib.sha256(image.read_bytes()).hexdigest() != truth['images']['firmware.bin']:
        raise ValueError('Qualification image differs from the authenticated upstream image')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    identity = calibration_identity()
    paths = {key: Path(value).expanduser().resolve() for key, value in
             (('renode', renode), ('ghidra', ghidra_home), ('java', java_home))}
    java = paths['java'] / 'bin' / ('java.exe' if os.name == 'nt' else 'java')
    if not paths['renode'].is_file() or not (paths['ghidra']/'support/analyzeHeadless').is_file() or not java.is_file():
        raise ValueError('Complete native Renode, Ghidra and Java tools are required')
    tools = copy.deepcopy(truth['build']['tool_versions'])
    tools['renode'] = subprocess.check_output([str(paths['renode']), '--version'], text=True, timeout=30).strip()
    version = subprocess.run([str(java), '-version'], capture_output=True, text=True, timeout=30, check=True)
    tools['java'] = (version.stderr or version.stdout).strip()
    properties = (paths['ghidra']/'Ghidra/application.properties').read_text(encoding='utf-8')
    tools['ghidra'] = re.search(r'^application.version=(.+)$', properties, re.M).group(1).strip()
    record = {'schema': 'benchmark-calibration/1', 'status': 'pending', 'case': manifest['id'],
        'evaluator_version': manifest['evaluator_version'], 'execution': 'reference-calibration',
        'backend': 'native-renode', 'images': manifest['images'], 'input_hashes': truth['input_hashes'],
        'evaluator_identity': identity, 'native_process_observed': False, 'references': [], 'mutants': [],
        'scenarios': {}, 'required_mutants': sorted(REQUIRED_MUTANTS[truth['family']]),
        'tools': tools, 'runs': [], 'analysis': {}}
    def save():
        _write(output/'calibration-record.json', record)
    save()
    try:
        exports = output/'exports'
        with tempfile.TemporaryDirectory(prefix='gd-upstream-analysis-') as temp:
            command = [sys.executable, str(resources_root()/'tools/workspace/ghidra_run.py'),
                '--workspace', temp, '--image', str(image), '--base-address', '0x08000000',
                '--processor', 'ARM:LE:32:Cortex', '--ghidra-path', str(paths['ghidra']),
                '--java-home', str(paths['java']), '--export-dir', str(exports),
                '--script-path', str(resources_root()/'toolchain/skills/interpret-firmware-binary'),
                '--prescript', 'SeedCortexM.java', '--timeout-s', '600']
            analyzed = subprocess.run(command, capture_output=True, text=True, timeout=660)
            (output/'analysis.stdout').write_text(analyzed.stdout, encoding='utf-8')
            (output/'analysis.stderr').write_text(analyzed.stderr, encoding='utf-8')
            result = json.loads(analyzed.stdout)
            if analyzed.returncode or not result.get('ok') or result.get('markers', {}).get('script_error'):
                raise ValueError('Native binary analysis failed; inspect private logs')
        record['analysis']['firmware.bin'] = {'returncode': 0, 'export_hashes': {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in exports.iterdir() if p.is_file()}}
        save()
        def run(model, capabilities, name, phase):
            print(json.dumps({'reference_calibration': name, 'phase': phase}), file=sys.stderr, flush=True)
            pin = {'case_id': manifest['id'], 'family': manifest['family'], 'scenario_id': 'original',
                   'case_seed': 0, 'revision': 0}
            result = reference_execute(pin, truth, model, capabilities, renode=paths['renode'], image=image,
                                       output_dir=output/'runs'/name, phase=phase, private_dir=output/'native'/name)
            record['runs'].append({'id': name, 'scenario': 'original', 'phase': phase,
                'native_process_observed': result['native_process_observed'], 'process': result['process'],
                'image_sha256': manifest['images']['firmware.bin'], 'evidence_sha256': hashlib.sha256(
                    (output/'runs'/name/'evaluation.json').read_bytes()).hexdigest(),
                'contract_sha256': canonical_digest(result['contract']), 'checks': result['grade']['checks'],
                'check_ids': [c['id'] for c in result['contract']['checks']],
                'passed': result['grade']['verdict'] == 'passed',
                **{k: result[k] for k in ('model_sha256', 'capabilities_sha256', 'package_sha256',
                                         'runtime_sha256', 'artifact_execution')}})
            save()
            return result
        for name, reference in truth['references'].items():
            entry = {'id': name, 'model_sha256': canonical_digest(reference['model']),
                     'passed': False, 'runs': [], 'execution_inputs': {}}
            record['references'].append(entry)
            for phase in ('diagnostic', 'final'):
                run_id = name + '-' + phase + '-original'
                result = run(reference['model'], reference['capabilities'], run_id, phase)
                entry['runs'].append(run_id)
                entry['execution_inputs'][run_id] = execution_input_identity(reference['model'], reference['capabilities'])
                save()
                if result['grade']['verdict'] != 'passed':
                    raise ValueError('Positive reference failed: ' + run_id + '; mutant qualification is unavailable')
            entry['passed'] = True
            save()
        for mutant in truth['mutations']['mutants']:
            reference = truth['references'][mutant['reference']]
            changed = apply_mutation(reference['model'], reference['capabilities'], mutant)
            result = run(changed['model'], changed['capabilities'], mutant['id'], mutant['phase'])
            failed = [c['id'] for c in result['grade']['checks'] if c['passed'] is not True]
            record['mutants'].append({'id': mutant['id'], 'run': mutant['id'], 'rejected': bool(failed),
                'failed_checks': failed, 'expected_failed_checks': mutant['expected_failed_checks'],
                'execution_input': execution_input_identity(changed['model'], changed['capabilities']),
                'source_sha256': changed['source_sha256'], 'mutated_sha256': changed['mutated_sha256']})
            save()
        record['native_process_observed'] = all(r['native_process_observed'] for r in record['runs'])
        record['scenarios'] = {'original': {'passed': all(r['passed'] for r in record['references'])}}
        record['status'] = 'passed'
        checked = validate_calibration({**manifest, 'calibration': {'input_hashes': truth['input_hashes']}}, record)
        if not checked['ok']:
            record.update(status='pending', failures=checked['failures'])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        record.update(status='pending', failure=type(error).__name__ + ': ' + str(error))
    if calibration_identity() != identity:
        record.update(status='pending', failure='Evaluator changed during qualification')
    save()
    return record


def calibrate(case, options):
    from ..benchmark import case_root
    from .registry import resolve_case
    from .emulator import truth_for_case
    from .truth import seal
    definition = resolve_case(case)
    payload = truth_for_case(case_root(), definition.manifest, options)
    output = Path(options['output']).expanduser().resolve()
    record = qualify(payload, definition.manifest, definition.root/'firmware.bin', output,
                     **{key: options[key] for key in ('renode', 'ghidra_home', 'java_home')})
    payload['calibration'] = record
    payload['inventory_state'] = 'qualified' if record['status'] == 'passed' else 'pending'
    manifest = copy.deepcopy(definition.manifest)
    manifest['calibration'] = {'status': record['status']}
    if record['status'] == 'passed':
        manifest['calibration'].update(input_hashes=record['input_hashes'], evidence_sha256=canonical_digest(record))
    manifest['truth']['sha256'] = seal(payload, output/(case + '.enc'),
        Path(options['evaluator_password_file']).read_text(encoding='utf-8').strip())
    _write(output/'case.json', manifest)
    return {'ok': record['status'] == 'passed', 'execution': 'reference-calibration', 'model_benchmark': False,
            'case': case, 'status': record['status'], 'evidence_sha256': canonical_digest(record),
            'references': len(record['references']), 'mutants': len(record['mutants']), 'output': str(output)}
