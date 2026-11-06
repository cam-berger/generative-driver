# 08. output-measure

## Intent

A driver prints a result on each grading call, and the grader reads all of it into memory, inside the process holding the passwords. A cap could stop a flood, but nobody knows how large real output is, and the user won't set a limit on an unmeasured quantity. The trusted runtime's 2 MiB ceiling is a rule, not a measurement. So this Feature measures: each graded trial records the largest output its driver printed, and the scorecard shows that beside the firmware's size, so real runs can show whether a limit is needed and whether it should be proportional to the firmware.

No limit is added. Until there is one, a flood from a tampered driver or a later change could crash the grader, and the docs say so. Grades don't change. Editing `emulated_actions.py` and `evaluator.py` moves every grader identity once, which costs nothing because nothing is admitted on main.

## Behaviours

- **08-B1** — A trial graded through `package_invoker` records in `trial.json` `package_output: {'largest_bytes': <int>, 'calls': <int>}`. `largest_bytes` is the largest stdout and stderr together of one call, in the UTF-8 bytes of the text as read, and `calls` counts the calls that returned. A call that timed out or couldn't start isn't counted. A trial with no counted call, such as a stopped run or a no-driver grade, holds no `package_output`. Partial figures from an unscored grading are recorded. A harness that dies before writing its record leaves the trial unmeasured, and a resume can't recover that.
- **08-B2** — Nothing is limited: a call printing several times the runtime's 2 MiB ceiling is read whole, and behaves exactly as it does today.
- **08-B3** — Measuring changes no grade. `package_invoker` gives equal results with and without a meter. `grade` with a meter returns the same record and final as without one. Unlocked evidence and `final['raw']` hold no `package_output` or `largest_bytes` key, and evidence sealed by this code, with a meter, passes `regrade_v3`.
- **08-B4** — A later grading that counts calls records its own figures in place of the earlier ones. One that counts none, such as a final rebuilt from the draft, keeps the earlier figure. A rerun has its own trial folder, so its `trial.json` holds the rerun's figures, and the first attempt's stay sealed with it.
- **08-B5** — `package_output` stays with the bench harness: it reaches no configurator call and no worker, and isn't in a stub, whose exact keys stand. On a scoring suite, it is sealed with the original `trial.json`.
- **08-B6** — A trial's metrics always hold `package_output: {'largest_bytes', 'firmware_bytes', 'ratio'}`, with `None` for what's unknown. The scorecard gains a block `package_output: {'largest_bytes': {'max', 'median'}, 'firmware_bytes': {'max', 'median'}, 'ratio': {'max', 'median'}, 'measured': <int>}`, over trials holding both figures. `ratio` is each trial's `largest_bytes` divided by its `firmware_bytes`. With none measured, each figure is `None` and `measured` is `0`. The card's text gains one line after `Reruns:` when `measured` is above zero, showing the ratio to three significant figures, so a small ratio doesn't read as `0.00`.
- **08-B7** — `firmware_bytes` is the sum of the sizes of the images in `case_pin.manifest.images`, read from the kept `benchmark/inputs/` once `read_snapshot` has checked them, including on a sealed scoring trial. An image that can't be read or doesn't match, an empty list, or a total of 0 gives `None` with no ratio, and the trial isn't measured, so a card never fails on it. A missing `trial_dir` or `trial.json` reads as unmeasured too.
- **08-B8** — A scoring suite's card holds only those aggregates, and no per-trial figure. Any other suite's card also holds each trial's `package_output` in its row's metrics.

## Interface and seams

**`emulated_actions.package_invoker`** gains a keyword `meter=None`, a small mutable object with `add(stdout, stderr)`, and `canonical_package_invoker` passes it through. It adds after `subprocess.run` returns, before any parsing, so a malformed print counts too. The default of `None` keeps every other caller as it is, including calibration and qualification. `text=True` and `errors='replace'` stay.

**Getting it to the record.** `evaluator.grade` and `reference_execute` take `meter=None`, and `reference_execute` gives it to the invoker it builds. `grade` adds nothing to its return value. `harness.conclude` alone builds the meter, since it is the only caller of `grade`, and sets `done['package_output']` straight after its outer `try`/`finally`, before its returns, only when calls were counted, so the figure survives a seal or post failure and reaches an unscored grade. `finish` and `_settle` already carry the key through. The figure can't go in `final` or `content`, since both reach the evidence. `conclude`'s signature doesn't change. `recorded_grading` doesn't run the driver and takes no meter.

**Scorecard.** `trial_metrics` reads `package_output` from its `trial_dir`'s `trial.json`, which is the opened copy on a scoring suite, and `firmware_bytes` from `home/runs/<id>/benchmark/`, since those kept paths are never in `opened`'s `run/`. Any `OSError`, `ValueError`, `KeyError` or `TypeError` there gives `None`. `_scoring_metrics` and the aggregate use them like any other figure, and the median follows the card's existing helper. Byte figures print through `_cell`, with its `n/N` marking. The text reads the block with `card.get`, as `reruns` does. No schema changes, as Feature 06's `reruns` didn't change one. `blank_metrics` (`tests/test_scorecard.py:918`) gains the block.

**Tests** stay cheap: the Done limit is 540 s and a full run takes about 381 s. The meter and the invoker are tested in a new module on a temp folder holding `driver/__init__.py`, `driver/__main__.py` and, for the pass-through, `driver/model.json`, with no `qualified_set`: a finite print of a few times 2 MiB, a stderr print, a malformed print, a timeout and a spawn failure. `grade`, `conclude` and `resume` are tested on `PendingRunFixture` and the `reference` double in `tests/test_evaluation.py`, where the double adds known sizes to the meter, which shows "in place of" and the kept figure. That double (`tests/test_evaluation.py:74`) gains the keyword, which otherwise fails misleadingly as an unscored grade. Add at most two end-to-end tests inside `tests/test_slot_policy_flags.py`: one scoring suite with a rerun, read through `opened` and including `store`, and one development suite. Assertions added inside `test_07_B14` cost no new suite. Tests attach at `package_invoker`, `grade`, `conclude`, `resume`, `trial_metrics`, the scorecard and the stub's keys.

**Docs.**
- `docs/design.md` "Limits of this protocol": nothing limits what a driver prints or a worker's disk or memory use. A flood could crash the grader, which holds the passwords. The figures on the card exist to show whether a limit is needed.
- `docs/benchmark-case-authoring.md`: what `package_output` on the card means, that the 2 MiB is the runtime's ceiling, and that first attempts and timed-out or crashed calls aren't counted.

## Out of scope

- Any new limit, cap or kill on output, in any path. The user chose to measure first. An external worker's existing 32 MiB output cap stays as it is.
- Whether a limit should be proportional to the firmware: that is what these figures are for, and a later decision.
- Measuring fresh reuse, reference replay, `emit_check`, qualification or any other launch. Only grading's calls count.
- A first attempt's figures on the card, as with every other figure in Feature 06. The card can therefore understate the largest output when a first attempt printed the most.
- Limits on a worker's disk or memory, which have no portable form.
- Any real run, password, vault, Renode, set or model.

## Deviations

- `OutputMeter` is a new public class in `emulated_actions.py` — the Interface asks for a small mutable object with `add(stdout, stderr)` without naming a home; it sits beside the invoker and adds no import to grading code.
- The docs say a crashed call is counted, against this Spec's Docs bullet — 08-B1 ("the calls that returned") and the Interface both require it, the docs match the code, and leaving out a crash that printed a flood would work against the Intent. Only timed-out and could-not-start calls are uncounted.
- The scorecard's read fallback also catches `AttributeError` and `RecursionError` — both are real ways to read a tampered or over-deep record, 08-B7 says "a card never fails on it", and each is reached by a test.
- `_firmware_bytes` gives None when `manifest.images` and `public_files` disagree or the image is missing, and images are a dict, not a list — a fair reading of "doesn't match", since `read_snapshot` checks only `public_files`; honest pins can't trip it.
- `_largest_output` treats a non-integer `largest_bytes` as unmeasured — serves 08-B7's "reads as unmeasured" and "never fails", and only a tampered record can reach it.
- The card line's wording, `.3g` exponent form at 1000 or above, and no `n/N` on the ratio — 08-B6 fixes neither the wording nor the placement beyond one line after `Reruns:`, and the Interface asks for `_cell` on byte figures only.
- Test harness additions: the `printed` argument on `reference`, the `meter` keyword on `StepGradingTests.grade`, and the `linked` subtest left out on Windows — the Spec asks the double to add known sizes, and the Windows skip is only for symlink privilege.
- 08-B2's final test names weren't re-run under their mutation, and `test_08_B1_a_harness_that_dies...` can't catch a figure written early, since a dead process isn't simulated — process only; that path was confirmed by reading `conclude`, and 08-B1 and 08-B2 have fitting RED.
