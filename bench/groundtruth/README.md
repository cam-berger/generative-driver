# Evaluator evidence

Installed `resources/bench/groundtruth/*.enc` bundles use AES-256-GCM with PBKDF2-HMAC-SHA256 (600,000 iterations, random salt/nonce). Manifests pin ciphertext SHA-256. Hashes establish identity, not physical truth or a sandbox.

Calibrated V2 cases contain source/build provenance, pinned inputs, two references, diagnostic/final contracts, native observations and known-wrong models. Each uses one original image. Final episodes grade the frozen package; fresh worker use is separately required. Legacy TQ9 keeps its own qualifier and mission checks. BME280 records vendor provenance and agreement with operator references within supplied uncertainty; vendor PDFs are fetched.

Store version 5 requires six transaction scenarios in both phases, full ending-state observations, five original mutants plus idle-only-update and short-rejection mutants. Each `operation_rejected` check needs private `benchmark-rejection/1` evidence with lowercase complete-response `rx_hex`, independently derived before measurement. Missing/malformed witnesses refuse I/O. Captured bytes must match; candidate framing/reject prefixes are insufficient, while equivalent requests remain valid. Freeze expected mutant failure IDs before running them. Emulator replacement proofs retain independent startup reads and original dispatch/outcome IDs.

## Obtain and handle the password

The publisher supplies passwords separately to a human evaluator. There is no bundled key or default. Smoke needs none. Store password files outside checkout/candidate workspaces with host access controls; pass paths, never contents, to the evaluator. Keep secrets out of prompts, environments and worker logs. Password possession is the trust boundary; worker filesystem isolation is unverified.

```sh
python -m generative_driver benchmark truth unlock --case tq9 --password-file "/private/evaluator/tq9.password" --output "/private/evaluator/unlocked"
python -m generative_driver benchmark truth rebuild --case tq9 --password-file "/private/evaluator/tq9.password" --compiler "/absolute/path/to/arm-none-eabi-gcc" --output "/private/evaluator/rebuilt"
```

Use empty output directories. Unlock writes `evidence.json` and top-level sources; authored inventories remain embedded. Rebuild uses pinned Arm GCC and adjacent objcopy, then verifies the image hash. Changed toolchains/contracts require reviewed versions.

## Key rotation

Create a random password in a separate evaluator-only file:

```sh
python -m generative_driver benchmark truth rekey --case tq9 --password-file "/private/evaluator/old.password" --new-password-file "/private/evaluator/new.password" --output "/private/evaluator/rekeyed"
```

Publish the reviewed ciphertext and manifest together; deliver the password separately. Installed resources are untouched. Rekeying preserves expectations. Changed inputs/acceptance require versioning and qualification. Execution-only fixes retain case versions but need qualification for the exact implementation/dependency digest.

## Native reference qualification

Use native GCC, objcopy, Renode, Ghidra and Java. Rebuild/analyze the image; run two references and required mutants through both phases. References must pass and mutants fail their predeclared checks. Missing tools, host faults and skips cannot qualify a case.

```sh
python -m generative_driver benchmark truth calibrate --case tq9-v2 --password-file "/private/evaluator/tq9-v2.password" --compiler "/absolute/path/to/arm-none-eabi-gcc" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --output "/private/evaluator/calibration"
```

`ARM_NONE_EABI_GCC` or PATH selects the compiler. Review and publish the emitted manifest/encrypted bundle together; raw evidence stays private. Admission authenticates current code, dependencies, images, inventories and evidence, not a public `passed` label.

For sampled-sensor/store, unlock into a new private directory, then recover the authenticated inventory outside checkout/worker roots. Keep its password in the authoring directory's parent. This POSIX-shell example checks hashes and the allowed file inventory:

```sh
python -m generative_driver benchmark truth unlock --case sampled-sensor-v1 --password-file "/private/evaluator/sampled-sensor-v1.password" --output "/private/evaluator/sampled-unlocked"
python - "/private/evaluator/sampled-unlocked/evidence.json" "/private/evaluator/sampled-sensor-v1-authoring" <<'PYTHON'
import json, os, sys
from pathlib import Path
from generative_driver.benchmark_support.calibration import decode_inventory, authoring_payload
os.umask(0o077)
evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
files = decode_inventory(evidence)
root = Path(sys.argv[2]).resolve()
root.mkdir(mode=0o700)  # New directory; an existing destination is refused.
for name, data in files.items():
    target = root / name  # decode_inventory permits only its exact allowlist.
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_bytes(data)
assert authoring_payload(root)["inventory_sha256"] == evidence["inventory_sha256"]
(root / "calibration/calibration.json").unlink()
assert authoring_payload(root)["inventory_state"] == "pending"
PYTHON
```

On Windows, save the Python body above as `recover_inventory.py` (omit the shell wrapper and `PYTHON` marker), then run `python recover_inventory.py EVIDENCE_JSON NEW_AUTHORING_DIRECTORY`.

Remove only the previous `calibration/calibration.json`; preserve all source, recipe, contract, reference, oracle, mutant, license and build bytes. Repeat with store-specific paths, then qualify:

```sh
python -m generative_driver.benchmark_support.calibration sampled-sensor-v1 --authoring-root "/private/evaluator/sampled-sensor-v1-authoring" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --output-dir "/private/evaluator/sampled-calibration"
```

Set `ARM_NONE_EABI_GCC` to the pinned compiler; repeat for store. The new calibration record excludes itself and its enclosing ciphertext from its input commitment.

Explicit native gate: `python -m unittest discover -s tests -p native_benchmark_families.py -v`. Set `GD_FAMILY_AUTHORING` to the private parent of both authoring directories/password files, plus `GD_NATIVE_RENODE`, `GD_NATIVE_GHIDRA_HOME`, `GD_NATIVE_JAVA_HOME` and `ARM_NONE_EABI_GCC`. Missing configuration fails. Ordinary tests use scripted contracts without credentials/native tools.

## Offline scoring and evidence

Current `assistive/1` trials use `benchmark-run-evidence/2`: one post-run grade of the accepted frozen package. The bench harness holds the password; no stage receives grades. Sidecars with six acceptance gates below describe the earlier gated protocol. Regrade those from their measured checkout; never compare the two series. See [current benchmark instructions](../README.md).

Preserve V2 encrypted sidecars and accepted artifacts. They pin execution, observations, six gates and final grades. Offline scoring verifies bytes and recomputes grades without workers/devices:

```sh
python -m generative_driver benchmark score "reports/trial.json" --evidence "/private/evaluator/run-evidence.enc" --password-file "/private/evaluator/tq9-v2.password"
```

Keep private vectors, sources and monitor recipes outside candidate inputs/version control. Public reports expose selected counts, hashes and verdicts. Qualification establishes evaluator behavior, not model performance, physical compatibility, Windows support or third-party attestation. See [verification](../../docs/verification.md).

## Third-party notice

The encrypted sensor model retains Copyright 2010–2024 Antmicro, its MIT header and modification notice. The [bundled notice](../../src/generative_driver/resources/bench/licenses/renode-MIT.txt) matches [Renode's license](https://github.com/renode/renode/blob/master/LICENSE). Owned firmware and original harness code are Apache-2.0; encryption does not remove attribution duties.
