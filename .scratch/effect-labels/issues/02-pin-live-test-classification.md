# Bind a live test's effect classification to the package that runs

Status: needs-triage

Found on 2026-10-30 while implementing option (a) of the [effect-label spec](../spec.md). For `emit_test_live` the gateway classifies effects from `package_dir/driver/model.json`, or `model_dir/model.json` when given, and the tool then copies the package and verifies the copy against the package's own manifest. A worker that swaps in another consistent package between the two runs operations the gateway classified from other bytes: with a write grant, a write can run unmarked. The verdict already credits only a snapshot carrying the frozen manifest, so this concerns the uncertain-write record, not grading.

Proposed: read the manifest once, check that the model bytes the gateway classified match its `driver/model.json` entry, and pass that manifest's digest as `expected_manifest_sha256`, refusing a worker pin that differs. Nothing is copied, so fresh reuse's verdict gains no new host failure before contact.

Red test: a `call_tool` stand-in that swaps in a valid package with a write-labelled operation after the gateway's read, expecting the tool to refuse before contact.
