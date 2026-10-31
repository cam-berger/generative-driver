"""Native calibration of an authored family from its private authoring root: build it, run its references and mutants,
and validate the measured record."""
import json
from .calibration import authoring_payload, safe_summary, validate_calibration


def calibrate(case_id, *, authoring_root, renode, ghidra_home, java_home, output_dir, compiler=None):
    """Evaluator Python return includes private evidence. CLI uses safe_summary only."""
    import os
    from pathlib import Path
    from . import authoring
    from .asset_build import require_private_root, _disjoint
    from .registry import resolve_case
    if case_id not in ('sampled-sensor-v1','parameter-store-v1'):raise ValueError('Unknown authored family')
    tool_paths={'renode':Path(renode),'ghidra_home':Path(ghidra_home),'java_home':Path(java_home)}
    compiler=Path(compiler or os.environ.get('ARM_NONE_EABI_GCC',''))
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
