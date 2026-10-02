# Evaluator evidence

The installed `resources/bench/groundtruth/*.enc` bundles use AES-256-GCM authenticated encryption with PBKDF2-HMAC-SHA256 (600,000 iterations, random salt and nonce). Each case manifest pins its ciphertext SHA-256. Hashes identify bytes; they are not physical truth or a sandbox.

TQ9 contains owned firmware source and linker script, source/build/input hashes, exact build flags, the preserved MIT Renode sensor model, independent reference models/vectors, monitor observations, positive and wrong-decoder examples, and the original/revised-package calibration evidence. No plaintext reference source or reference model is copied into candidate kits. Physical BME280 version 2 contains official document provenance, required independent reference criteria, expected gates and known incorrect behaviors. Maintain requires an operator-measured ambient change beyond combined uncertainty on at least one channel and a matching fresh sensor response; constant output and unchanged-reference examples are rejected. Vendor PDFs are fetched from their source rather than redistributed here.

## Obtain and handle the password

The publisher supplies evaluator credentials out of band to a human evaluator. This repository intentionally contains no default password, password hint or recoverable key. Request the password from the person who supplied the case bundle. A public user can run setup smoke without it; actual independently scored profiles require access. Place each password alone in a file outside the checkout and any candidate workspace. Restrict that file to the evaluator account using host file permissions; never paste its contents into a chat, assignment or environment variable.

This is password-based separation with sealed input hashes. Workers are instructed to use their supplied inputs. No mandatory OS sandbox is claimed, and a local human who obtains a password can inspect the evidence.

```sh
python -m generative_driver benchmark truth unlock --case tq9 --password-file "/private/evaluator/tq9.password" --output "/private/evaluator/unlocked"
python -m generative_driver benchmark truth rebuild --case tq9 --password-file "/private/evaluator/tq9.password" --compiler "/absolute/path/to/arm-none-eabi-gcc" --output "/private/evaluator/rebuilt"
```

Use an empty output directory. `unlock` writes plaintext evidence and owned sources for human review. Never use that directory as a candidate workspace. `rebuild` requires Arm GNU Toolchain 14.2.Rel1 (GCC 14.2.1) plus its adjacent `objcopy`; it links the toolchain's `libgcc` arithmetic helpers, rebuilds both firmware revisions and checks byte hashes. Other compiler versions may change binary bytes; record a new case version when adopting different inputs. Candidate-generated code is never compared with reference source as a quality score.

## Rotate or publish your own evaluator key

Create a new random password in a separate evaluator-only file using your password manager, then:

```sh
python -m generative_driver benchmark truth rekey --case tq9 --password-file "/private/evaluator/old.password" --new-password-file "/private/evaluator/new.password" --output "/private/evaluator/rekeyed"
```

The command emits a new ciphertext and `pin.json`; it does not replace installed resources. A case maintainer updates the packaged ciphertext and case manifest pin together, reviews the change, and delivers the new password separately. Rekeying does not change behavioral expectations. Changing firmware, oracle criteria or evidence scope needs a new case/evaluator version.

The encrypted calibration is scripted reference execution, not model performance. It established correct reference outputs/effects, relocated-package fresh calls, refusal on seeded identity drift, revised-model qualification and reuse, and rejection of a deliberate decoder scale error on macOS arm64. Physical effects and native Windows Renode execution remain separately unverified until their corresponding runs are recorded.

## Third-party notice

The encrypted Renode sensor model retains its original Copyright 2010–2024 Antmicro MIT header and modification notice. The complete notice is shipped at [`resources/bench/licenses/renode-MIT.txt`](../../src/generative_driver/resources/bench/licenses/renode-MIT.txt), matching [Renode's upstream license](https://github.com/renode/renode/blob/master/LICENSE). The owned firmware and original benchmark harness are Apache-2.0; encrypting evaluator evidence does not remove third-party attribution obligations.

## V2 calibration

Run native GCC, Renode, Ghidra and Java against two distinct reference models and the required behavioral mutants. All original, semantic, control and identity scenarios must qualify. Missing tools, host faults, malformed candidates and skipped checks leave calibration pending. Use an empty evaluator directory outside the checkout, installed resources and worker inputs:

```sh
python -m generative_driver benchmark truth calibrate --case tq9-v2 --password-file "/private/evaluator/tq9-v2.password" --compiler "/absolute/path/to/arm-none-eabi-gcc" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --output "/private/evaluator/calibration"
```

`ARM_NONE_EABI_GCC` or a compiler on PATH may replace `--compiler`. The command emits a private measured record, a new encrypted bundle and a matching public manifest; it never edits installed resources. Publish those two release assets together after reviewing the record. The record commits source, image, recipe, contract, reference, mutation and evaluator identities without hashing its enclosing ciphertext. Admission authenticates that record and its public commitment. Password possession is the local evaluator trust boundary, not third-party attestation or a filesystem sandbox.

Final run sidecars contain actual accepted controller artifact paths and hashes, evaluator action records, and separate worker/evaluator maintenance evidence IDs. Preserve accepted artifacts when archiving a run. Offline scoring verifies those bytes and recomputes grades. Private final vectors, source and monitor recipes must remain outside candidate inputs, logs shown to workers and version control.

## Authored family calibration

`sampled-sensor-v1` and `parameter-store-v1` use exact encrypted inventories. Pending bundles contain source, native recipes, two reference implementations, diagnostic/final/maintenance contracts, mutants and reproducible build evidence. Qualified bundles add only `calibration/calibration.json`; that record excludes itself and its enclosing ciphertext from its input commitment.

Keep the exact authoring directory outside the checkout, installed libraries and worker roots. Place its existing password handle beside it as `<case_id>.password`. Set `ARM_NONE_EABI_GCC` to the absolute GCC executable. The wrapper requires explicit absolute Renode, Ghidra and Java paths and checks the handle before launching native tools:

```sh
python -m generative_driver.benchmark_support.calibration sampled-sensor-v1 --authoring-root "/private/evaluator/sampled-sensor-v1-authoring" --renode "/absolute/path/to/renode" --ghidra-home "/absolute/path/to/ghidra" --java-home "/absolute/path/to/java-home" --output-dir "/private/evaluator/sampled-calibration"
```

Repeat with `parameter-store-v1` and its own authoring directory. The compiler must report Arm GNU Toolchain 14.2.Rel1 GCC 14.2.1 (20241119); adjacent objcopy must report 2.43.1.20241119. The measured environment uses Renode 1.16.1, Ghidra 12.1.3 and OpenJDK 21. Native Windows qualification remains unobserved.

The CLI prints only case, execution, outcome, counts and a record commitment. The Python `calibrate(...)` return and output directory contain private evidence and belong only to the evaluator. Failures retain `calibration-record.json`, build diagnostics, Ghidra logs and completed native runs. A failed or stale record cannot admit an agent run. Review and publish the resulting `case.json` and ciphertext together; the command never changes installed assets.

The explicitly selected test gate is `python -m unittest discover -s tests -p native_benchmark_families.py -v`. It requires `GD_FAMILY_AUTHORING` pointing to the private parent containing both `<case_id>-authoring` directories and password handles, plus `GD_NATIVE_RENODE`, `GD_NATIVE_GHIDRA_HOME`, `GD_NATIVE_JAVA_HOME`, and `ARM_NONE_EABI_GCC`. Missing configuration fails rather than skipping. Ordinary discovery uses scripted toy contracts and requires none of these tools or credentials.

## Supplementary TQ9 phase evidence

The final release also carries `groundtruth/tq9-v2-phases.enc`. This separately encrypted reference evidence measures both correct references on diagnostic and maintenance inventories for original, unchanged control, semantic drift and identity drift variants. Ordinary TQ9 admission still authenticates its exact 32-run main calibration; it does not consume or require this supplement. The [final verification index](../../docs/implementation/benchmark-final-verification.json) records the supplement's ciphertext hash, measured phase counts and matching input, evaluator, image and tool commitments.

An authorized evaluator can inspect the supplement through the existing authenticated `unlock` API, using the separately supplied TQ9 evaluator handle and the published ciphertext SHA-256. Keep the returned payload and any extracted records outside the checkout, installed resources and candidate workspace. For example, with operator-selected arguments:

```python
from pathlib import Path
from generative_driver.benchmark_support.truth import unlock
from generative_driver.toolkit import resources_root

# handle_path and expected_sha256 are supplied separately by the evaluator.
payload = unlock(
    resources_root() / "bench/groundtruth/tq9-v2-phases.enc",
    Path(handle_path).read_text(encoding="utf-8").strip(),
    expected_sha256,
)
# Inspect payload privately; do not expose raw records to candidate workers.
```

The main calibration and supplement are native reference measurements. Neither is a model-performance trial, physical measurement, Windows qualification or independent attestation of the password holder.
