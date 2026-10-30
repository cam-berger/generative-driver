# Bind a live test's effect classification to the package that runs

Status: resolved

Found on 2026-10-30 while implementing option (a) of the [effect-label spec](../spec.md). For `emit_test_live` the gateway classified effects from `package_dir/driver/model.json`, or `model_dir/model.json` when given, and the tool then copied the package and verified the copy against the package's own manifest. A worker that swapped in another consistent package between the two ran operations the gateway had classified from other bytes: with a write grant, a write could run unmarked. The verdict already credited only a snapshot carrying the frozen manifest, so this concerned the uncertain-write record, not grading.

Scope: a format-2 package's fresh reuse goes through the generated MCP gateway, which already snapshots the package under a manifest pin taken at preparation. Through the worker gateway, `emit_test_live` serves format-1 interface packages and register packages.

## Answer

Resolved on 2026-10-30, at the user's request. For a live `emit_test_live` the gateway reads the package manifest and the package's own model once each, classifies effects and the serial channel from that model, and passes the manifest's digest as `expected_manifest_sha256`. It refuses before dispatch when the manifest names other model bytes, or when the worker's own pin names another manifest. A manifest that names no model verifies no package, so it is pinned as read. Nothing is copied, so fresh reuse's verdict gains no new host failure before contact. With a `model_dir` given, the tool still refuses a package whose model differs from it.

`RealToolVerdictTests.test_a_package_the_worker_swaps_in_during_a_live_test_never_runs` rewrites the worker's package copy, with a consistent manifest, after the gateway's read. Without the pin the device received the swapped-in write `H1`; with it the tool refuses before contact with `manifest checksum/pin mismatch`.

Full suite on macOS with Python 3.13: 1017 tests in 493 s, OK, with the four skips of 41469aa.
