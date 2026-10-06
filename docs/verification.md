# Verification

All workflows end at acquire, interpret, probe, ground, emit, reuse. Scripted tests, installation replay, native reference qualification, model execution and physical observations retain separate labels.

## Dashboard — 2026-10-06

The [dashboard verification](dashboard-verification.md) records 429 passing tests in each source/installed environment and one successful observed TQ9 run. The observer branch and separately qualified local pipeline installation are identified explicitly.

## PR #1 follow-up — 2026-10-03

The [review](research/2026-10-03-pr1-review.md) found an offline historical-scoring defect and an existing Windows checkout defect. The follow-up rejects incompatible historical evidence and preserves the bytes hashed by benchmark manifests and evaluator identities.

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 (final) | 420 | 66.792 s | Passed |
| Installed wheel, Python 3.11.15 | 420 | 62.015 s | Passed |
| Installed wheel, Python 3.13.14 | 420 | 68.856 s | Passed |

The final source run includes the native path assertion correction. Installed runs preceded that test-only correction; all 125 final wheel entries are byte-identical to the installed-tested wheel. The source archive audit resolved all 72 local Markdown links. All three runs enabled wheel/source-archive gates and had zero skips, failures or errors. Installed suites ran from the extracted source archive outside the checkout, with installed imports verified and checkout/credential/native environment overrides removed. Both installations passed setup replay, relocated standalone replay and all three authenticated family admissions. Dependencies were mcp 2.2.0 and cryptography 50.0.1.

Fresh native qualification executed 12 correct-reference and 18 mutant sessions across the three families, plus two legacy sessions. All 32 saved grades reproduced, current authenticated admissions passed and stale qualifications were rejected. The evaluator identity is `612b1a83d061e848e5596917b6c0776fbebfc77302deac998bfef6039d16ea33`. One earlier TQ9 attempt stopped on a Renode monitor-port collision after eight sessions; its complete unchanged retry passed, with the original partial record and startup log retained. These observations qualify the evaluator, not a model's ability to recover a driver.

The [follow-up index](implementation/pr1-followup-verification.json) records counts, log hashes, input/evidence identities and preserved failures. PR #1's earlier 417-test qualification remains in its [historical index](implementation/benchmark-final-verification.json). Raw native evidence, passwords, machine paths and exact local archives stay evaluator-owned. The reviewed growth roadmap and source review are now public documentation.

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

## CI and limits

The initial fix commit's [CI run](https://github.com/cam-berger/generative-driver/actions/runs/37155861839) passed macOS Python 3.11 and 3.13 and eliminated Windows truth-hash failures. Both Windows jobs then exposed one existing test that assumed POSIX path separators. The assertion now checks native path containment; the focused local suite passes. A later Windows 3.13 job exposed a gateway test that raced the transition to running; the test now waits for both assignment and public running state. A delayed-transition reproduction failed before and passed after correction, and all ten configurator tests pass. These later changes affect tests only. The follow-up PR checks provide the final platform results. Initial failed jobs and temporary GitHub log-access failures are retained in the review record.

PR #1 also recorded an intermittent macOS suite reconnect failure. The unchanged 11-test interface suite and 12 consecutive repetitions of the affected test passed locally. No scheduler fix is claimed. Earlier failures and retries remain part of the evidence.

The nine-trial model study has not run. These checks do not establish physical-device performance, native Windows Renode/Ghidra, Windows 11 workstation UI setup or live Goose behavior. Worker filesystem isolation remains unverified. Earlier observations retain their original revisions and meaning.
