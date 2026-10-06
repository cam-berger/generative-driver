# Dashboard verification — 2026-10-06

The [local dashboard](dashboard.md) was tested as an observer of one real emulated run. The [verification index](implementation/dashboard-verification.json) records software results and safe aggregate evidence; raw logs, credentials, connection URLs and device observations remain private.

## Software checks

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 |429|60.652s|Passed|
| Installed wheel, Python 3.11.15 |429|64.800s|Passed|
| Installed wheel, Python 3.13.12 |429|70.403s|Passed|

All three suites enabled wheel/source-archive gates and had zero skips. Installed imports resolved to clean environments outside the checkout. Setup replay and relocated-driver replay passed in both installations without model inference. Browser behavior checks passed from source and the source archive on Node.js 26.0.0; CI uses Node.js 22. The dashboard itself needs only the installed Python package and a browser.

Regression coverage includes current-revision gates, a finished worker awaiting acceptance, a fresh assignment after same-revision resume, bounded activity tails, Codex/Goose text parsing, denied HTTP control requests, monitor exit without cancelling work, stalled IPC reads and browser timeout/recovery. Independent review found three defects in resumed-worker status, Goose activity and stalled refreshes; each failing regression passed after its fix.

## One live example

One fresh original-image TQ9 trial used Codex CLI 0.159.2 with `gpt-6.1-sol`, medium reasoning. It completed all six stages in 10.05 minutes, with zero repairs, retries or candidate interventions. Fresh reuse was accepted and independent sealed-evidence regrading passed 9/9 final checks. This is one descriptive outcome, not a model reliability estimate.

The actual Codex browser displayed changing worker/run logs, acquisition acceptance, active interpretation and a probe worker in “checking” before its gate passed. It later displayed “Completed” with 6/6 accepted. Nineteen connected sampled snapshots agreed with bracketing configurator states. One concurrent read received the observer's busy response and recovered on the next sample; the owner did not disconnect during candidate execution.

After completion, stopping the private configurator made the browser label retained progress as stale. Restarting that same home restored “Completed” without reloading the page or changing the candidate timestamp, handoffs or six worker reports. The Follow switch and narrower browser-pane layout were also inspected. The owned monitor/configurator were stopped after exports; all 21 accepted artifact hashes verified, and other services were untouched.

The initial private helper let its monitor child exit when its tool session returned; keeping the dashboard CLI running fixed access without restarting the candidate. A reporting-helper import was corrected before regrading already saved evidence. These harness errors were preserved.

## Scope and reproduction

The dashboard branch starts from published `main` at `6bacae4`. The live observer connected to the separately qualified, unchanged local pipeline core at `272c1b1`, evaluator version 5. This PR does not qualify firmware generation on published `main`. No physical device, live Goose or Windows workstation browser was tested. Native IPC connection/authentication itself is outside the new reply deadline.

Use [the software verification commands](verification.md#reproduce-software-checks), then run `node tests/dashboard-client.test.js` with Node.js 22 or newer. For an actual run, use the configured Codex/Goose workflow or [benchmark setup](../bench/README.md), take its run ID and open `python -m generative_driver dashboard RUN_ID` in the same environment/home. [Dashboard usage](dashboard.md) covers explicitly selected homes and ports. Keep scripted replay and actual model results separate.
