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
