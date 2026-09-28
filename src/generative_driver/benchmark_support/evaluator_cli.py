"""Human evaluator commands. Output belongs outside candidate workspaces."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def manage(action, case, password_file, output, new_password_file=None, compiler=None):
    from ..benchmark import case_root
    from .truth import seal
    from .emulator import truth_for_case
    from .cases import _write
    root = case_root()
    manifest = json.loads((root/'cases'/case/'case.json').read_text(encoding='utf-8'))
    truth = truth_for_case(root, manifest, {'evaluator_password_file':password_file})
    destination = Path(output).expanduser().resolve()
    if destination.is_relative_to(Path(__file__).resolve().parents[2]):
        raise ValueError('Evaluator output must be outside installed package and candidate resources')
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Choose an empty evaluator output directory')
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if action == 'unlock':
        _write(destination/'evidence.json', truth)
        for name, content in truth.get('source_files', {}).items():
            if Path(name).name != name:
                raise ValueError('Invalid evaluator source filename')
            (destination/name).write_text(content, encoding='utf-8')
        return {'ok':True,'evidence':str(destination/'evidence.json'),
                'warning':'Evaluator-only plaintext; exclude this directory from candidate inputs and environments'}
    if action == 'rekey':
        if not new_password_file:
            raise ValueError('Provide --new-password-file outside candidate inputs')
        path = destination/(case+'.enc')
        digest = seal(truth, path, Path(new_password_file).read_text(encoding='utf-8').strip())
        _write(destination/'pin.json', {'case':case,'ciphertext':path.name,'sha256':digest})
        return {'ok':True,'ciphertext':str(path),'sha256':digest,
                'instruction':'Distribute ciphertext and pin; deliver the new password through a separate evaluator channel'}
    if action != 'rebuild' or case != 'tq9':
        raise ValueError('Rebuild supports the owned tq9 firmware')
    compiler = Path(compiler or '')
    if not compiler.is_file():
        raise ValueError('Provide --compiler as the arm-none-eabi-gcc executable')
    objcopy = compiler.with_name(compiler.name.replace('gcc', 'objcopy'))
    recipe = truth['build']
    for name in ('main.c','link.ld'):
        (destination/name).write_text(truth['source_files'][name],encoding='utf-8')
    actual = {}
    for image in ('firmware.bin','firmware-next.bin'):
        source = truth['source_files']['main.c']
        if image.endswith('-next.bin'):
            source = source.replace(*recipe['drift_replace'])
        (destination/'main.c').write_text(source,encoding='utf-8')
        elf = destination/(image+'.elf')
        built = subprocess.run([str(compiler),*recipe['flags'],'main.c','-o',str(elf)],cwd=destination,capture_output=True,text=True)
        if built.returncode:
            raise RuntimeError('Reference build failed: '+built.stderr[-2000:])
        subprocess.run([str(objcopy),'-O','binary',str(elf),str(destination/image)],check=True,capture_output=True)
        actual[image] = hashlib.sha256((destination/image).read_bytes()).hexdigest()
    passed = actual == manifest['images']
    result={'ok':passed,'execution':'reference-build','model_benchmark':False,'images':actual,
            'expected_images':manifest['images'],'compiler_version':subprocess.check_output([str(compiler),'--version'],text=True).splitlines()[0]}
    _write(destination/'build-report.json',result)
    return result
