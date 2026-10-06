# Evaluator evidence

Installed `resources/bench/groundtruth/*.enc` bundles use AES-256-GCM authenticated encryption with PBKDF2-HMAC-SHA256 (600,000 iterations, random salt and nonce). Each manifest pins ciphertext SHA-256. Hashes identify bytes; they do not establish physical truth or a sandbox.

Calibrated V2 firmware groundtruth contains owned sources, exact build recipes, source/image/input hashes, two distinct reference implementations, diagnostic/final contracts, native observations and known incorrect models. Each firmware case evaluates one stable original image. V2 final episodes execute frozen emitted packages; fresh worker use is separately required. Legacy TQ9 retains its separate reference/mutant qualifier and recorded mission checks. Physical BME280 truth contains vendor document provenance, operator reference agreement criteria within supplied uncertainty and expected discovery-to-reuse gates. Vendor PDFs are fetched rather than redistributed.

Parameter-store version 4 inventories require all six named transaction scenarios in both phases and the idle-only update mutant alongside the five existing mutants. Ending observations follow each reset-delimited scenario’s final call and cover all committed and pending cells, generation and pending state. Expected rejection also requires complete runtime framing evidence. Native qualification authenticates exact expected failure IDs frozen before measurement; predictions cannot be copied from measured failures. Owned-emulator replacement proofs retain independent startup reads and original dispatch/outcome IDs in encrypted evaluator evidence.

## Obtain and handle the password

The case publisher supplies credentials separately to a human evaluator. No default password, hint or recoverable key is included. Setup smoke requires none; independently scored agent profiles require evaluator access. Store each password alone in a file outside the checkout and candidate workspace, protected by host permissions. Supply its path to the evaluator; keep its contents out of prompts, environments and worker logs.

Password possession is the local evaluator trust boundary. Candidate kits contain neither passwords nor plaintext reference answers. Worker filesystem isolation remains unverified.

```sh
python -m generative_driver benchmark truth unlock --case tq9 --password-file "/private/evaluator/tq9.password" --output "/private/evaluator/unlocked"
python -m generative_driver benchmark truth rebuild --case tq9 --password-file "/private/evaluator/tq9.password" --compiler "/absolute/path/to/arm-none-eabi-gcc" --output "/private/evaluator/rebuilt"
```

Use an empty evaluator output directory. Unlock writes `evidence.json` and any top-level `source_files` for review. Authored-family inventories remain embedded in `evidence.json`; recover them as described below. Rebuild requires the pinned Arm GCC and adjacent objcopy, rebuilds the original image and checks its byte hash. A different toolchain or evidence contract requires reviewed version changes. Candidate quality is scored by functional behavior.

## Key rotation

Create a random password in a separate evaluator-only file, then:

```sh
python -m generative_driver benchmark truth rekey --case tq9 --password-file "/private/evaluator/old.password" --new-password-file "/private/evaluator/new.password" --output "/private/evaluator/rekeyed"
```

This emits a new ciphertext and pin without changing installed resources. Publish the reviewed ciphertext and case manifest together; deliver the password separately. Rekeying retains behavioral expectations. Changed inputs, evidence scope or behavioral acceptance require matching qualification and case/evaluator versioning. Execution fixes with unchanged behavioral contracts retain case versions but require new authenticated qualification against the exact implementation and dependency digest; stale qualifications cannot admit a run.

## Native reference qualification

For calibrated V2 families, use native GCC, objcopy, Renode, Ghidra and Java to rebuild/analyze the original image and execute two independent references plus required behavioral mutants. Correct references must pass diagnostic/final contracts; wrong models must fail their declared checks. Missing tools, host faults or skipped checks cannot qualify a case. The record commits actual execution inputs and raw observation evidence.

```sh
python -m generative_driver benchmark truth calibrate --case tq9-v2 --password-file "/private/evaluator/tq9-v2.password" --compiler "/absolute/path/to/arm-none-eabi-gcc" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --output "/private/evaluator/calibration"
```

`ARM_NONE_EABI_GCC` or PATH may select the compiler. The command emits private raw evidence and a matching public manifest/encrypted bundle, without editing installed resources. Publish those two assets together after review. Admission authenticates current evaluator code, dependencies, images, private inventories and qualification evidence; a public `passed` label alone cannot admit a run.

For `sampled-sensor-v1` and `parameter-store-v1`, first unlock the selected case into a new private directory. The authenticated `evidence.json` contains the full byte inventory. Recover its allowlisted files into a new private authoring directory outside the checkout and worker roots; keep the password file in that directory's parent. This evaluator-only snippet verifies every inventory hash before writing and confirms the recovered authoring input is accepted:

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

Qualified inventories include `calibration/calibration.json`; remove only that prior qualification record before recalibration. Preserve every remaining source, recipe, contract, reference, oracle, mutation, license and build byte. Repeat recovery with the store case and its own paths, then run:

```sh
python -m generative_driver.benchmark_support.calibration sampled-sensor-v1 --authoring-root "/private/evaluator/sampled-sensor-v1-authoring" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --output-dir "/private/evaluator/sampled-calibration"
```

Set `ARM_NONE_EABI_GCC` to the pinned compiler executable; repeat with the store case and its own authoring directory. Exact inventories include source, native recipes, two references, diagnostic/final contracts, behavioral mutants and reproducible build evidence. Qualified inventories add `calibration/calibration.json`; that record excludes itself and its enclosing ciphertext from its input commitment.

The explicitly selected native test gate is `python -m unittest discover -s tests -p native_benchmark_families.py -v`. Set `GD_FAMILY_AUTHORING` to the private parent of both authoring directories/password files, plus `GD_NATIVE_RENODE`, `GD_NATIVE_GHIDRA_HOME`, `GD_NATIVE_JAVA_HOME` and `ARM_NONE_EABI_GCC`. Missing native configuration fails explicitly. Ordinary discovery uses scripted toy contracts and needs no credentials or native tools.

## Offline scoring and evidence

V2 runs produce encrypted sidecars with the frozen execution snapshot, accepted artifact identities, evaluator action/observation records, six accepted stage gates and final package grades. Preserve accepted artifacts with the sidecar. Offline scoring verifies their bytes and recomputes grades without worker inference or device access:

```sh
python -m generative_driver benchmark score "reports/trial.json" --evidence "/private/evaluator/run-evidence.enc" --password-file "/private/evaluator/tq9-v2.password"
```

Keep private final vectors, source and monitor recipes outside candidate inputs and version control. Public reports disclose selected counts, hashes and verdicts. Native reference qualification measures evaluator behavior; it is separate from actual model performance, physical measurement, Windows qualification and third-party attestation. See [current verification](../../docs/verification.md).

## Third-party notice

The encrypted Renode sensor model retains its original Copyright 2010–2024 Antmicro MIT header and modification notice. The complete notice is shipped at [`resources/bench/licenses/renode-MIT.txt`](../../src/generative_driver/resources/bench/licenses/renode-MIT.txt), matching [Renode's upstream license](https://github.com/renode/renode/blob/master/LICENSE). Owned firmware and original harness code are Apache-2.0; encrypted distribution retains attribution obligations.
