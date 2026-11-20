"""Relocatable validator of an interface model; dependencies are sealed beside this file.

It prints the result and writes it to `validation/result.json`. Beside `ok` and `defects` the result holds `advice`:
what a model that validates leaves undocumented. Advice changes neither `ok` nor the exit status.
"""
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent/'_toolkit'))
from interface_runtime.qualify import validate_candidate as validate_model
from interface_runtime.evidence import read_json
from interface_runtime.meanings import advice

model_dir = Path(sys.argv[1] if len(sys.argv) > 1 else '.').resolve()
try:
    model = read_json(model_dir/'model.json',1048576)
    result = validate_model(model)
    result = {**result,'advice':advice(model) if result['ok'] else []}
except (OSError,ValueError,TypeError,KeyError) as exc:
    result = {'ok':False,'defects':[{'where':'model.json','what':str(exc)}],'advice':[]}
output = model_dir/'validation'
output.mkdir(exist_ok=True)
(output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n', encoding="utf-8")
print(json.dumps(result,allow_nan=False))
raise SystemExit(0 if result['ok'] else 1)
