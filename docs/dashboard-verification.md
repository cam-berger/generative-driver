# Dashboard verification — 2026-10-06

The [local dashboard](dashboard.md) was tested as an observer of one real emulated run. The [verification index](implementation/dashboard-verification.json) records software results and safe aggregate evidence; raw logs, credentials, connection URLs and device observations remain private.

## Software checks

| Local macOS environment | Tests | Time | Result |
|---|---:|---:|---|
| Source Python 3.13.12 |430|66.678s|Passed|
| Installed wheel, Python 3.11.15 |430|69.195s|Passed|
| Installed wheel, Python 3.13.12 |430|76.666s|Passed|

All three suites enabled wheel/source-archive gates and had zero skips. Installed imports resolved to clean environments outside the checkout. Setup replay and relocated-driver replay passed in both installations without model inference. Browser behavior checks passed from source and the source archive on Node.js 26.0.0; CI uses Node.js 22. The dashboard itself needs only the installed Python package and a browser.

Regression coverage includes startup without a system name service, current-revision gates, a finished worker awaiting acceptance, a fresh assignment after same-revision resume, bounded activity tails, Codex/Goose text parsing, denied HTTP control requests, monitor exit without cancelling work, stalled IPC reads and browser timeout/recovery. Independent review found three defects in resumed-worker status, Goose activity and stalled refreshes; each failing regression passed after its fix.

## One live example

The live worker execution was observed with dashboard code `0b9fb96`. One fresh original-image TQ9 trial used Codex CLI 0.159.2 with `gpt-6.1-sol`, medium reasoning. It completed all six stages in 10.05 minutes, with zero repairs, retries or candidate interventions. Fresh reuse was accepted and independent sealed-evidence regrading passed 9/9 final checks. This is one descriptive outcome, not a model reliability estimate.

The actual Codex browser displayed changing worker/run logs, acquisition acceptance, active interpretation and a probe worker in “checking” before its gate passed. It later displayed “Completed” with 6/6 accepted. Nineteen connected sampled snapshots agreed with bracketing configurator states. One concurrent read received the observer's busy response and recovered on the next sample; the owner did not disconnect during candidate execution.

After completion, stopping the private configurator made the browser label retained progress as stale. Restarting that same home restored “Completed” without reloading the page or changing the candidate timestamp, handoffs or six worker reports. The Follow switch and narrower browser-pane layout were also inspected. The owned monitor/configurator were stopped after exports; all 21 accepted artifact hashes verified, and other services were untouched.

The initial private helper let its monitor child exit when its tool session returned; keeping the dashboard CLI running fixed access without restarting the candidate. A reporting-helper import was corrected before regrading already saved evidence. These harness errors were preserved.

## Hosted checks and startup correction

The first hosted macOS jobs failed the new CLI startup test: the process remained alive but had no URL within 10s. The standard HTTP server performs a synchronous reverse lookup before printing. A new no-name-service regression failed at that dependency; the loopback server now binds without it. The initial hosted failure lacked a child stack, so its exact cause remains an inference. All ten dashboard contracts passed on hosted macOS/Windows Python 3.11/3.13 after correction, with the original startup deadline retained.

At `05487a0`, the final installed observer also reattached in the actual browser to the same completed run, displaying 6/6 and zero repairs. Candidate timestamp, handoffs and six workers stayed unchanged; no further inference was run. The final source and two installed suites above passed 430 tests each, with zero skips. The earlier 429-test results remain in the index.

Hosted Python 3.11 then exposed failures in three existing suite cleanup/model-failure/reconnect tests. The unchanged tests passed four focused local repetitions and one hosted rerun. All eight push/PR matrix jobs passed at the code revision; failures and retries remain recorded. No scheduler correction is claimed. [The PR checks](https://github.com/cam-berger/generative-driver/pull/5/checks) show the current platform results.

## Scope and reproduction

The dashboard was initially qualified from published `main` at `6bacae4`, observing the separate unchanged installation of pipeline core `272c1b1`, evaluator version 5. The user subsequently authorized including that pipeline implementation and the [three-family results](research/2026-10-06-transaction-recovery-results.md) in PR #5. The measurements above retain their original identities; integration checks are [recorded separately](verification.md#pr-5-pipeline-and-dashboard-integration--2026-10-06). No model trial was rerun for this integration. No physical device, live Goose or Windows workstation browser was tested. Native IPC connection/authentication itself is outside the new reply deadline.

Use [the software verification commands](verification.md#reproduce-software-checks), then run `node tests/dashboard-client.test.js` with Node.js 22 or newer. For an actual run, use the configured Codex/Goose workflow or [benchmark setup](../bench/README.md), take its run ID and open `python -m generative_driver dashboard RUN_ID` in the same environment/home. [Dashboard usage](dashboard.md) covers explicitly selected homes and ports. Keep scripted replay and actual model results separate.
