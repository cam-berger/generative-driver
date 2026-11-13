# Executable device interface

This package executes its bundled interface-model/3 with the same shared runtime as the toolchain. Provenance entries are model claims. Replay confirms internal consistency, not physical correctness.

Source evidence: **live**. Model SHA-256: `4da4191bc401322ff7317f250695cd0e9b1eecbc0b8a97d98d5c5822c2db456f`. No accuracy, full device coverage or formal standard conformance is implied.

Use Python 3.11+ from this directory. Offline replay needs only the standard library. Live transports require the optional packages listed in requirements.txt and OS/backend drivers.

```sh
python -m driver describe
python -m driver replay
python -m driver execute OPERATION --parameters '{}' --binding '{}'
```

The channel is tcp. Supply its trusted physical binding as JSON:

```sh
python -m driver execute identify --binding '{"host": "<host name or address>", "port": <int>}'
```

port is a JSON integer. The binding is the operator's choice; models cannot select host paths, devices or network destinations.

Read effects are allowed by default; write and actuate each require an explicit grant, `--allow-effect write` or `--allow-effect actuate`, repeated once per effect. Every operation is a read. These are grants to declarations, not proof arbitrary bytes are safe.

## Bundled probe coverage

| Operation | Effect | Bundled status | Evidence | Samples attempted / passed / failed | Parameters |
| --- | --- | --- | --- | --- | --- |
| identify | read | tested | live | 1 / 1 / 0 | `{}` |
| measure | read | tested | live | 1 / 1 / 0 | `{}` |

Coverage refers only to the samples in the bundled probe, with the listed parameters. Untested means no bundled execution evidence, not absence of testing elsewhere. Live means recorded device execution; replay means supplied transactions, not a new device observation. Connections follow the model: one per call, or one for all samples when the channel declares resets_on_open. Sequence safety and physical semantics are not established.

Identity is checked before the selected operation, which runs once without whole-command retry.

Manifest and file hashes detect alteration. Retain the emitted manifest SHA-256 outside the package to verify against a trusted pin; an attacker replacing every file and checksum cannot be detected by a self-contained checksum alone. Hardware verification requires a separate physical test.
