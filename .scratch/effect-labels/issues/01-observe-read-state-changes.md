# Observe state changes during read-labelled calls on provided emulators

Status: needs-triage

Option (c) of the [effect-label spec](../spec.md), deferred on 2026-10-30 until the next fresh native calibration. The code lives under `benchmark_support`, so it changes the evaluator implementation.

The configurator reads a provided emulator's instrument before and after each live call. When a dispatch labelled `read` changes a quantity its case family declares as state, record it: for the assigned model, a device difference that returns the model to interpret; for a worker's own model, a stage note. Nothing stops and nothing is uncertain, because the after-reading observed the effect. This generalizes sampled-sensor's `retained read acquired a new sample` (`benchmark_support/instrument_evidence.py:98`). Each family must first declare which public instrument quantities are state, since readings mix state with measurements such as temperature.

Red test: the mislabelled `H1` variant from `c817348`'s `tests/test_configurator.py`, run on a provided emulator whose state quantity changes, expecting a device difference for the assigned model or a note for a variant.
