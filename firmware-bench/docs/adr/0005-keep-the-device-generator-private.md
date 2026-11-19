# Keep the device generator private

Status: proposed in the [benchmark baseline spec](../../.scratch/benchmark-baseline/spec.md).

Workers run as the operator's user and are not read-isolated, and the generator defines the structure of every generated device: command vocabularies, reply formats, checksum choices and how state machines are built. A worker that read it could match firmware against a template instead of reverse-engineering it, on every scoring device at once. So the generator lives in its own private repository, in its own encrypted volume outside this checkout, the installed package and run folders, as today's synthetic authoring sources stay outside them ([asset_build.py](../../src/generative_driver/benchmark_support/asset_build.py)). Each set's secret seed stays in the separate answer vault, so reading the generator never reveals a scoring device. Only its outputs enter the benchmark: images, missions, encrypted answer keys and set manifests. This repository keeps the generic set adapter, which learns a device's design only by decrypting its answer key.

## Consequences

Generator review and tests happen in the private repository. Public tests of the set adapter use hand-written fixture devices, never generator output. Neither volume is mounted while workers run, since placement alone would not stop a worker that searches the disk. Generated devices also stay out of future training data.
