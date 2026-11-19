# Earlier package

`package/` is an `interface-package/2` that the kit emitted before it ran the installed runtime over a package's data
([ADR 0008](../../../docs/adr/0008-run-the-installed-runtime-over-package-data.md)): the smoke's stand-in model (`resources/smoke/model.json`) with live evidence of `measure` from the
stand-in device. Tests of 05-B5 check that a later kit still uses it. Never rewrite it.

It was emitted at commit `d9617ab7d5241a41f286566ffbe1906eb4cb4bdd` from a clean copy of that commit's `src`:

```sh
git archive d9617ab7d5241a41f286566ffbe1906eb4cb4bdd src | tar -x -C base
cd base && PYTHONPATH="$PWD/src" python - ../package <<'EOF'
import json, shutil, sys, tempfile
from pathlib import Path
from generative_driver import stand_in_device
from generative_driver.toolkit import call_tool, resources_root

out = Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory() as work, stand_in_device.serving() as binding:
    work = Path(work).resolve()
    (work / 'model').mkdir()
    shutil.copyfile(resources_root() / 'smoke/model.json', work / 'model/model.json')
    probe = call_tool('probe_run', {'run_dir': str(work / 'run'), 'model_dir': str(work / 'model'),
                                    'operation': 'measure', 'n': 1, 'binding': binding})
    assert probe['ok'] and probe['evidence_kind'] == 'live', probe
    emitted = call_tool('emit_package', {'run_dir': str(work / 'run'), 'model_dir': str(work / 'model'),
                                         'probe': probe['probe']})
    assert emitted['ok'], emitted
    shutil.copytree(emitted['package_dir'], out)
    print(json.dumps({'package': str(out), 'manifest_sha256': emitted['manifest_sha256']}))
EOF
```

Its manifest SHA-256 is `8a217584ae5881ec3e81b2cd3dfc10a1419be9a26769115e7e05a099f5163b53`.
