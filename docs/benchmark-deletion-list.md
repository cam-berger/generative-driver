# Benchmark deletion list

The benchmark is moving to a repository of its own. Once it has moved, the owner deletes the legacy copy from this repository. This list says what that deletion removes, and what the tool still needs until a later Feature of the [untangle Run](features/untangle/breakdown.md) frees it.

`tests/deletion_marking.py` checks the list against the repository and carries the deletion out on a copy. `tests/test_deletion_marking.py` runs the check, and runs the tool's tests in that copy when `GD_TEST_REMOVAL=1`. Nothing is deleted from the repository itself.

## How to read it

- A path is written from the repository root. A path that ends in `/` covers every file under it.
- `Freed by` is the number of the Feature that ends the tool's need. That Feature moves the item to `Ready to delete`, or takes its row out of the other tables.
- A benchmark passage in a file that stays sits between two marker lines. The marker text is `legacy-benchmark:begin` and `legacy-benchmark:end`. It is written as an HTML comment in Markdown and as a `#` comment elsewhere. The deletion removes each marked passage with its markers.
- Only whole blocks are marked. Every other line that says `bench` is counted under `Passages`.

## Ready to delete

Nothing that stays needs these. The deletion removes them.

| Item | Note |
|---|---|
| `docs/benchmark-deletion-list.md` | This list. The deletion removes it last. |
| `tests/deletion_marking.py` | The check and the removal behind this list. Removed last. |
| `tests/test_deletion_marking.py` | The tests of this list. Removed last. |
| `docs/generated-device-interface.md` | Benchmark document. Only benchmark items link to it. |
| `docs/features/bench-harness/` | Record of the bench-harness Run. |
| `docs/adr/0001-benchmark-registration-and-selection.md` | Benchmark decision record. `docs/verification.md` links to it. |
| `docs/adr/0003-generated-devices-register-by-set.md` | Benchmark decision record. |
| `docs/adr/0004-compare-systems-through-a-scoring-set.md` | Benchmark decision record. |
| `docs/adr/0005-keep-the-device-generator-private.md` | Benchmark decision record. |
| `docs/adr/0006-seal-graded-scoring-trials.md` | Benchmark decision record. |
| `docs/adr/0007-one-validator-defines-a-generated-device.md` | Benchmark decision record. |
| `docs/superpowers/plans/2026-10-28-tq9-replacement.md` | Benchmark plan, a record. |
| `docs/superpowers/plans/2026-10-30-grader-identity.md` | Benchmark plan, a record. |
| `docs/superpowers/plans/2026-10-30-scorecard.md` | Benchmark plan, a record. |
| `docs/superpowers/plans/2026-11-01-generated-grader.md` | Benchmark plan, a record. |
| `.scratch/benchmark-baseline/` | Benchmark spec and its issue, a record. |
| `.scratch/benchmark-review/` | Benchmark review notes, a record. |
| `tests/test_audit.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_benchmark_asset_build.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_benchmark_behavior.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_benchmark_ground.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_benchmark_run.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_benchmark_truth.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_generated_device.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_generated_sets.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_monitor_control.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_sampled_sensor_family.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_set_qualification.py` | Tests only the benchmark. No file that stays imports it. |
| `tests/test_upstream_modbus.py` | Tests only the benchmark. No file that stays imports it. |

## Still used by the tool

The tool, its tests or its documents need these today. Deleting one now would break something that stays.

| Item | Used by | Freed by |
|---|---|---|
| `src/generative_driver/benchmark.py` | The six modules under `Tool modules that import the benchmark`, the commands under `Commands` and the still-used tests | 09 |
| `src/generative_driver/benchmark_support/` | The six modules under `Tool modules that import the benchmark`, the commands under `Commands` and the still-used tests | 09 |
| `src/generative_driver/resources/bench/` | `benchmark.py` and `benchmark_support/` read it; `tests/test_service.py`, `tests/test_toolkit.py` and `tests/test_cli.py` use its smoke case; `tests/test_distribution.py` requires it in the wheel; `.github/workflows/test.yml` runs its smoke case | 09 |
| `src/generative_driver/resources/registrations/` | `benchmark_support/` reads it on import; `tests/test_distribution.py` requires it in the wheel | 09 |
| `bench/README.md` | `README.md`, `docs/codex.md`, `docs/codex-acceptance.md` and `docs/design.md` link to it; `.github/scripts/tool_releases.py` reads its pins; `tests/test_assistive_wording.py` requires its wording | 09 |
| `bench/groundtruth/README.md` | `docs/codex-acceptance.md` links to it; `NOTICE` names it | 09 |
| `docs/benchmark-case-authoring.md` | `docs/design.md` links to it | 09 |
| `docs/benchmark-corpus.md` | `README.md` links to it | 09 |
| `.scratch/benchmark-lifecycle/` | `MANIFEST.in` puts it in the source archive, because `docs/verification.md` links to it | 09 |
| `tests/benchmark_family_fixtures.py` | `tests/test_benchmark_family_calibration.py` and 6 other test files import it | 09 |
| `tests/generated_fixtures.py` | `tests/test_generated_family.py` and 5 other test files import it | 09 |
| `tests/native_benchmark_families.py` | `tests/test_distribution.py` requires it in the source archive | 09 |
| `tests/native_codex_pipeline.py` | `docs/codex-acceptance.md` gives it as the acceptance command | 09 |
| `tests/suite_fixtures.py` | `tests/test_benchmark_scenarios.py` and 21 other test files import it | 09 |
| `tests/test_assistive_reports.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_assistive_wording.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_family_calibration.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_family_runtime.py` | Tests the tool's runtime with the benchmark's wire fixtures | 09 |
| `tests/test_benchmark_mcp_reuse.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_native.py` | `tests/test_benchmark_tq9_v2.py` imports it | 09 |
| `tests/test_benchmark_physical.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_reuse_endpoint.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_scenarios.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_score.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_snapshots.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_stages.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_tq9_v2.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_benchmark_v2_evidence.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_case_registration.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_case_registry.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_corpus.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_corpus_preflight.py` | `tests/test_unlocked_keys.py` imports it | 09 |
| `tests/test_corpus_workflow.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_development_conditions.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_device_provider.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_device_recovery.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_emulator_recovery.py` | `tests/test_benchmark_tq9_v2.py` imports it | 09 |
| `tests/test_evaluation.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_family_seam.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_generated_family.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_generated_trials.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_grader_identity.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_harness.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_legacy_cases.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_mcp.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_native_rehosting.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_outcomes.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_output_measure.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_owner_generated_integration.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_package_benchmark.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_pairing.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_parameter_store_family.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_pipeline_repair.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_public_missions.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_quantity_repair.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_rejection_framing.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_replay_executor.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_reporting.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_reuse_controller.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_scorecard.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_service_log.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_service_repair.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_slot_policy.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_slot_policy_flags.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_suite_harness.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_suite_interfaces.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_suite_manifest.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_suite_reporting.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_suite_service.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_suite_store.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_tq9_replacement.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_trial_sealing.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_unlocked_keys.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_upstream_authoring.py` | Tests the tool and the benchmark together | 09 |
| `tests/test_vault_gate.py` | Tests the tool and the benchmark together | 09 |
| `tests/tq9_workflow_fixture.py` | `tests/suite_fixtures.py` and 13 other test files import it | 09 |
| `tests/upstream_fixture.py` | `tests/test_native_rehosting.py` and `tests/test_upstream_authoring.py` import it | 09 |

## Tool modules that import the benchmark

These modules stay. Each stops importing the benchmark package in the Feature named.

| Module | Freed by |
|---|---|
| `src/generative_driver/cli.py` | 08 |
| `src/generative_driver/configurator.py` | 07 |
| `src/generative_driver/mcp.py` | 08 |
| `src/generative_driver/replay.py` | 02 |
| `src/generative_driver/reporting.py` | 07 |
| `src/generative_driver/scripted_worker.py` | 02 |

## Commands

The benchmark commands the tool offers today. Feature 08 gives the benchmark its own entry point.

| Command | Where | Freed by |
|---|---|---|
| `generative-driver benchmark` | `src/generative_driver/cli.py` | 08 |
| `driver_benchmark_run` | `src/generative_driver/mcp.py` | 08 |
| `driver_benchmark_suite_start` | `src/generative_driver/mcp.py` | 08 |
| `driver_benchmark_suite_status` | `src/generative_driver/mcp.py` | 08 |
| `driver_benchmark_suite_events` | `src/generative_driver/mcp.py` | 08 |
| `driver_benchmark_suite_result` | `src/generative_driver/mcp.py` | 08 |
| `driver_benchmark_suite_cancel` | `src/generative_driver/mcp.py` | 08 |
| `driver_benchmark_suite_resume` | `src/generative_driver/mcp.py` | 08 |

## Passages

Lines that say `bench` outside the marked passages, in files that stay. `stays` is for a line that is not about the benchmark.

| File | Unmarked lines | Why | Freed by |
|---|---|---|---|
| `.github/workflows/test.yml` | 1 | The hosted tests run the benchmark's smoke case | 08 |
| `.github/workflows/tool-releases.yml` | 1 | Names the pins in `bench/README.md` | 09 |
| `.gitignore` | 3 | Keeps benchmark runs and plaintext answers out of Git while the legacy copy is here | 09 |
| `AGENTS.md` | 3 | Project rules whose lines cover the tool and the benchmark together | 09 |
| `CONTEXT.md` | 6 | Terms of the benchmark in the shared glossary. Only the owner edits this file | 09 |
| `CONTRIBUTING.md` | 3 | Sentences that cover the tool and the benchmark together | 09 |
| `MANIFEST.in` | 3 | Puts a benchmark record in the source archive and keeps benchmark runs and plaintext answers out of it | 09 |
| `MANIFEST.in` | 1 | Puts `bench/evidence/`, which is kept, in the source archive | stays |
| `NOTICE` | 1 | Names `bench/groundtruth/README.md` and the benchmark evidence | 09 |
| `README.md` | 3 | Command lines that run the benchmark's smoke case | 08 |
| `README.md` | 3 | Two rows of a table that also holds tool rows, and one mixed paragraph | 09 |
| `README.md` | 1 | Links to the published record under `bench/evidence/`, which is kept | stays |
| `STANDARDS.md` | 2 | Rules about the bench harness. Only the owner edits this file | 09 |
| `docs/agents/domain.md` | 1 | One sentence names the benchmark beside the tool's parts | 09 |
| `docs/codex-acceptance.md` | 3 | Describes acceptance through the benchmark's suite. Feature 09 settles what moves | 09 |
| `docs/codex.md` | 4 | Mixed paragraphs. `tests/test_assistive_wording.py` requires the wording of one | 09 |
| `docs/dashboard.md` | 1 | One sentence of a tool paragraph | 09 |
| `docs/design.md` | 16 | The accepted design describes the tool and the benchmark together | 09 |
| `docs/design.md` | 1 | Links to the published record under `bench/evidence/`, which is kept | stays |
| `docs/extending-drivers-and-mcp.md` | 1 | One sentence of a tool paragraph | 09 |
| `docs/setup.md` | 1 | A paragraph about line endings whose reason is benchmark hashes | 09 |
| `docs/support.md` | 2 | A table row and a paragraph that cover the tool and the benchmark together | 09 |
| `plugins/generative-driver/skills/generative-driver/SKILL.md` | 5 | The client skill covers benchmark runs in steps it shares with tool runs. Not marked: an agent reads the file as text, so a marker would change what it sees | 09 |
| `src/generative_driver/resources/client/skills/generative-driver/SKILL.md` | 5 | The packaged copy of the client skill. A test keeps the two copies the same | 09 |

## Kept

These stay. The deletion does not touch them, and no passage in them is marked.

| Item | Why |
|---|---|
| `docs/verification.md` | The published record of past results. |
| `bench/evidence/` | The evidence files of the published record. |
| `docs/features/untangle/` | This Run's own folder. |
| `docs/features/kit-fixes/` | Record of the kit-fixes Run. |
| `docs/adr/0002-driver-contract-and-generated-mcp.md` | Decision record of the tool. |
| `docs/superpowers/plans/2026-10-28-driver-contract-mcp.md` | Plan of the tool, a record. |
| `docs/superpowers/specs/2026-10-28-driver-contract-mcp-design.md` | Design of the tool, a record. `docs/design.md` links to it. |
| `docs/research/` | Research notes, a record. |
| `.scratch/effect-labels/` | Spec and issues of the tool, a record. |
| `.scratch/learning-loop/` | Parked spec of the tool, a record. |
