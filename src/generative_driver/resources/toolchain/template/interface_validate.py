"""Relocatable validator for interface-model/3, /4 and /5; dependencies are sealed beside this file."""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent/'_toolkit'))
from interface_runtime.engine import validate_model
from interface_runtime.evidence import read_json

model_dir = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
try:
    result = validate_model(read_json(model_dir/'model.json',1048576))
except (OSError,ValueError,TypeError,KeyError) as exc:
    result = {'ok':False,'defects':[{'where':'model.json','what':str(exc)}]}
output = model_dir/'validation'
output.mkdir(exist_ok=True)
(output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n', encoding="utf-8")
print(json.dumps(result,allow_nan=False))
raise SystemExit(0 if result['ok'] else 1)
