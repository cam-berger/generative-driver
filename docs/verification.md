# Verification

All current workflows end at acquire, interpret, probe, ground, emit, reuse. A new agent receives the written driver and its objective, uses the desired device functions, and supplies observations for independent final acceptance. Scripted tests, installation replay, native reference qualification, model execution and physical observations retain separate labels.

## Observed qualification — 2026-10-03

Native macOS checks passed 417 source tests on Python 3.13.12 in 60.103 seconds, 417 installed-wheel tests on Python 3.11.15 in 56.058 seconds, 417 installed-wheel tests on Python 3.13.14 in 60.973 seconds. Each complete suite enabled wheel and source-archive gates and had zero skips, failures or errors. Installed tests ran outside the checkout with checkout `PYTHONPATH` and credential/native environments removed; imported modules and resources came from the new installations. Both used mcp 2.2.0 and cryptography 50.0.1, matching qualified evaluator identity `7e4e7bbaf79c6eca2a0330d18c6eb493a609631fac7bac09e196a6c91bd616d5`.

Both installed environments passed installation and standalone replay, six-case discovery, Codex/Goose setup generation, all three original v2-family authenticated admissions, stale-calibration rejection and two evaluator-only authoring inventory recoveries. Each independently reproduced all 30 saved final native family grades and two legacy grades, and passed explicit-sidecar six-gate scripted workflow scoring plus compatible offline comparison. The default suite expands to nine slots; no model worker or new native session was launched by these integration checks.

Fresh qualification after the final review fixes executed 12 correct-reference and 18 mutant sessions across the three firmware families, plus two separate legacy sessions. These qualify evaluator behavior, not fresh model-worker success. Positive references pass diagnostic/final contracts and mutants fail their predeclared checks. Earlier complete records fail the current identity and authenticated admission. The [verification index](implementation/benchmark-final-verification.json) records actual counts, times, identities and log hashes. Full logs, machine paths and final archive hashes remain evaluator-owned outside the distribution.

Wheel and source archives were built from a clean tracked export. Their local document paths and fragments resolve; checked-in/generated client skills agree. Published bytes contain four original firmware images, current six-stage assets and encrypted qualification evidence. Private plaintext, credentials, obsolete binaries and scratch files are absent. The other owner's untracked research draft remains outside publication. Source archives include supporting JSON records and test helpers. Final documentation rebuilds are checked against all 118 tested wheel module/resource files.

## Reproduce software checks

Build from a clean revision so unrelated untracked files cannot enter the source archive:

```sh
python -m build
```

Set `GD_TEST_WHEEL` and `GD_TEST_SDIST` to the absolute paths of those archives, then run `python -m unittest discover -s tests -v`. In each clean Python 3.11/3.13 verification environment install the wheel and the qualified evaluator dependencies (`mcp==2.2.0`, `cryptography==50.0.1`); these versions describe the measured verification environment, not new project dependency pins. Extract the source archive to a separate directory, change to it, remove checkout `PYTHONPATH` and evaluator credential/native variables, then run the same test command with that environment's Python. Keep the archive gate variables set to the same built bytes. Confirm `generative_driver.__file__` and `interface_runtime.__file__` resolve to the new installation.

```sh
python -m generative_driver benchmark cases
python -m generative_driver benchmark run --case setup-smoke --output "smoke output"
python -m generative_driver setup --host codex --output "codex setup"
python -m generative_driver setup --host goose --output "goose setup"
```

Use new output directories. From `smoke output/relocated package`, run `python -m driver replay`. These commands perform no model inference or physical operations. [Groundtruth setup](../bench/groundtruth/README.md) describes password-holder recovery, authenticated native evidence and explicit-sidecar offline score commands; [the benchmark guide](../bench/README.md) describes final acceptance and compatible comparisons.

## Limits and review

The current nine-trial model study has not run. These local checks do not establish physical-device performance, native Windows Renode/Ghidra, Windows 11 workstation UI setup or live Goose execution. Historical Windows CI retains its original source identity in [support](support.md). Worker filesystem isolation remains unverified; encrypted truth establishes password-based separation at rest. Earlier observations remain in Git at their original revisions.

The whole-branch review completed with a With fixes verdict. Legacy terminal failure, portable physical scoring, the retained repair limit and evidence prose are corrected; covering regressions and fresh native/distribution qualification passed. The parent's scoped rereview and the user's integration decision remain pending. Benign source-archive pattern warnings are deferred; protective exclusions remain. No merge or push was performed.
