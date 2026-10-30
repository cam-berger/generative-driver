# Author a benchmark case

Add intake evidence first, implement executable support in trusted code, qualify its evaluator, then run actual agents. A catalog entry alone does not supply an adapter, a native backend or a correct evaluator. Use the [operator workflow](benchmark-corpus.md) for selection, authentication, execution and reporting, and the [registration decision](adr/0001-benchmark-registration-and-selection.md) for the trust boundaries.

## Choose the identities and responsibility

| Item | Public responsibility | Private evaluator/operator responsibility |
|---|---|---|
| Lineage | Catalog ID, immutable upstream commit/path/license, origin group, split, architecture/system, protocols, behavior strata, mission requirements, limitations and blockers. | Verify source ancestry, license evidence and reproducible native build. Keep related origins in one split. |
| Case | Stable case ID/version, manifest, original image hashes, ciphertext pin, six-stage inventory and effect grants. | Keep matching encrypted truth, credentials, native observations and qualification evidence. |
| Family | Trusted Python implementation of observations, contracts, evaluation actions and fresh mission checks; static registration selects it. | Independently authored references, recipes, hidden episodes and targeted mutants. |
| Public mission | `benchmark-public-mission/1`: task meanings, typed input bounds, output units, a fresh reuse objective and optional supplied operating conditions. | Hidden expected values and independent check evidence. Public task requirements must agree with both phases. |

A case identifies one pinned experiment; a family supplies its behavioral implementation; a mission names what the worker must accomplish. Reuse an existing family only when its actual semantics fit. A new protocol may require runtime/transport, backend and observer work as well as a family implementation. Registration never makes an arbitrary protocol executable.

## File checklist

Paths below are relative to the repository. Add or update only the files needed for the supported case; preserve third-party notices. The [catalog validator and `AREAS` / `STRATA` enums](../src/generative_driver/benchmark_support/corpus.py) define accepted fields and values. Start from the public [Zephyr mission JSON](../src/generative_driver/resources/bench/missions/zephyr-modbus-v1.json), adapting task requirements rather than hidden answers; the [mission validator](../src/generative_driver/benchmark_support/missions.py) requires `schema`, `case_id`, `tasks` and `reuse_objective`, and permits the optional root field `operating_conditions`. Other root fields are rejected. Each task declares `meaning`, `inputs`, `outputs` and optional `sampling`; inputs have types/bounds and every output has an explicit `unit` or `null`.

| File | Required change |
|---|---|
| [src/generative_driver/resources/bench/corpora/seven-area-pilot.json](../src/generative_driver/resources/bench/corpora/seven-area-pilot.json) or a versioned custom catalog | Add the lineage using the validator's exact fields/enums and this public catalog as a template. Start with `case: null` and explicit blockers until executable evidence exists. A custom catalog is selected with `corpus plan --catalog PATH`. |
| `src/generative_driver/benchmark_support/registrations.py` | Add a static `CaseRegistration`: `id`, `family`, `version`, `adapter_key`, `schema`, and applicable `native_module`, `mission_case`, `truth_schema`, `build_kind`, `calibration_kind`. The public manifest must agree with its trusted identity. |
| `src/generative_driver/benchmark_support/<family>.py` | Implement `contract(pin, truth, phase)`, `build_plan(pin, truth, phase)`, `observations(raw, canonical_task_result, inputs)` with `monitor_units` and any `runtime_units`, and `reuse_passed(events, capabilities)`. Declare required mutants where the truth schema uses them. Validate unavailable/malformed observations. |
| `src/generative_driver/benchmark.py` and dispatchers in `src/generative_driver/benchmark_support/` | Inspect `registry.py`, `adapters.py`, `emulated.py`, `emulator.py`, `evaluator.py`, `evaluator_cli.py`, `upstream_build.py`, `upstream_qualification.py`, `reference_calibration.py`, `calibration.py` and the affected runtime/provider for unsupported behavior. `emulator-v2` uses `emulated.py`'s `prepare_stage`, `observe_stage`, `package_call`, `record_call` and `score`; use it only when that lifecycle fits. New adapter/build/calibration keys need trusted implementations, not just strings in registration. |
| `src/generative_driver/resources/bench/missions/<case-id>.json` | New `emulator-v2` cases must provide a mission under their own case ID for interpret/probe/ground task contracts and instrument evidence. Declare canonical task names, inputs, units, fresh objective and any supplied `operating_conditions`; avoid case-name branches in prompts. Registration's `mission_case` selects only the existing fresh-reuse objective path (legacy `tq9` uses `tq9-v2`); it is not an all-stage mission alias. |
| `src/generative_driver/resources/bench/cases/<case-id>/case.json` and `firmware.bin` | Publish reviewed identity, one original image, provenance, scenario `original`, all six required stages, effects, time policy, limitations and calibrated ciphertext/evidence hashes. |
| `src/generative_driver/resources/bench/groundtruth/<case-id>.enc` | Publish encrypted, hash-pinned truth. For upstream cases it binds identity, image, source/build, recipe, disjoint phases, two references, mutants and the exact public mission. Keep native modules in code; assets and decrypted bundles never choose imports. |
| `src/generative_driver/resources/bench/platforms/`, `licenses/`, `cases/<case-id>/NOTICE.md`, `cases/<case-id>/licenses/` | Add needed platform/transport resources with hashes, provenance and notices. Backend observations must be independent of the candidate's decoded replies. |
| `tests/` | Cover public case resolution, identity tampering, family loading, mission/observation/reuse dispatch and plan/report behavior through existing seams. Observe a behavior test fail before implementing each code change, then pass. |
| Operator-private directory outside checkout/package/workers | Store source/build provenance, authoring inventory, recipes/oracles/references/mutants, raw qualification logs and separately held password files. Do not copy these into worker inputs. |

The single registration is consumed by case resolution, native-family loading, instruments, reuse and upstream truth validation. Loaders import only that code-owned module when needed. Catalogs, manifests and encrypted truth are data; adding Python paths to them cannot extend the system. An unused family's unavailable optional tools must not prevent another family from loading.

`operating_conditions`, when present, must be a list of 1–16 nonblank strings, each at most 1000 characters. Use it for public operating facts such as provider timing or required pacing. The same text reaches interpret, probe and ground in their task contract and fresh reuse in its objective. Keep hidden values, reference answers and private recipes out of this field. It describes operating conditions and grants no additional tools or effects. Changing it changes the public mission and requires matching authenticated inventory, fresh qualification and a new plan.

Dynamic `development_conditions` are a separate provider facility: probe/ground may receive a `DEVELOPMENT_CONDITIONS.json` catalog of selectable experimental inputs and use `condition_id` on live calls. A mission's `operating_conditions` does not create that catalog or select a source, and fresh reuse does not receive the development catalog.

## Follow the known Zephyr case

`zephyr-modbus` is the lineage; `zephyr-modbus-v1` is the case; `zephyr-modbus` is the native family. Its registration selects `emulator-v2`, `zephyr_modbus.py`, `benchmark-upstream-truth/1`, and the existing upstream build/calibration implementations. The manifest's `corpus_identity` must match the catalog's lineage, origin group, mission ID and immutable source commit.

The public mission asks for register/coil operations and unrelated-state preservation. Its operating conditions describe the paused-between-calls provider and a 50 ms execution delay between request exchanges, including automatic identity-to-operation exchanges. Place that delay inside the model operation while its live call is active; sleeping between tool calls cannot advance device time. This is a conservative emulator scheduling margin; acceptance checks values, refusals and effects, without measuring physical UART timing or throughput.

`zephyr_modbus.py` projects independent emulator memory and GPIO readings, produces diagnostic/final evaluation plans and checks package-only reuse. Its six mutants target constant reads, byte order, register/coil selection, omitted writes and request CRC. The UART bridge is protocol-independent; it does not implement Modbus for the candidate. The [operator guide](benchmark-corpus.md#rebuild-and-requalify-zephyr) gives the native build and qualification commands.

## Create the first encrypted inventory

The existing `truth calibrate` command reads an already registered manifest and encrypted inventory. For a new upstream case, implement its trusted support and place its public mission in the selected asset tree first. Privately author `upstream-truth.json` against [`upstream.hydrate_truth`](../src/generative_driver/benchmark_support/upstream.py): `schema: "benchmark-upstream-truth/1"`, `case_id`, `family`, `inventory_state: "pending"`, `images`, `source`, `build`, `recipe`, `phases` (`diagnostic` and `final`), `references` (`a` and `b`), `mutations`, and the exact `public_mission`. Author native episodes/references/oracles for that device; copying Zephyr's hidden expectations cannot qualify a different device. Author a matching public manifest using the [Zephyr manifest](../src/generative_driver/resources/bench/cases/zephyr-modbus-v1/case.json) as a field example, with the new trusted identity, provenance, image hashes and `calibration.status: "pending"`.

Use the existing [`truth.seal`](../src/generative_driver/benchmark_support/truth.py) emitter to create the initial ciphertext and pin its returned digest in that manifest. For example, run this as an operator-private Python script after authoring both input JSON files; it creates pending assets, not qualification evidence:

```python
import json
from pathlib import Path
from generative_driver.benchmark_support.truth import seal
from generative_driver.benchmark_support.upstream import hydrate_truth

private = Path("/operator/private/new-case-authoring")
payload = json.loads((private / "upstream-truth.json").read_text(encoding="utf-8"))
manifest = json.loads((private / "case-template.json").read_text(encoding="utf-8"))
assert payload["inventory_state"] == "pending"
assert (payload["case_id"], payload["family"]) == (manifest["id"], manifest["family"])
hydrate_truth(payload)  # Validates against trusted registration and public mission.
case = manifest["id"]
digest = seal(payload, private / (case + ".enc"),
              (private.parent / (case + ".password")).read_text(encoding="utf-8").strip())
manifest["truth"] = {"path": "groundtruth/" + case + ".enc", "sha256": digest}
manifest["calibration"] = {"status": "pending"}
(private / "case.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
```

Copy the pending pair and its matching image into a complete operator asset tree; select that tree to run the implemented native qualifier. Keep its catalog lineage blocked until qualification succeeds. The allowlisted synthetic authoring format instead uses [`asset_build.inventory` and `assemble_release`](../src/generative_driver/benchmark_support/asset_build.py); that emitter currently supports sampled-sensor and parameter-store, so another family needs reviewed trusted support. There is no asset-defined plugin or universal new-protocol bootstrap command.

## Qualify, activate, then measure

1. Record immutable source/license evidence and build twice to the same firmware bytes. Verify backend startup/reset and independent observations. Define separate diagnostic and final episodes and their complete ending-state checks; do not infer an oracle from the candidate's own replies.
2. Run native image analysis and two independently valid references. Both must pass their required episodes; final episodes must execute emitted packages. Freeze each mutant's targeted failed check IDs before running it, then demonstrate rejection of every required mutant. Host faults, missing tools, skipped episodes or public `calibration.status: passed` text cannot qualify a case.
3. Use the matching trusted qualification command to emit the replacement manifest and encrypted bundle. For the three synthetic families, follow [native groundtruth qualification](../bench/groundtruth/README.md#native-reference-qualification), including private authoring inventory recovery for sampled-sensor/store. Qualify against the exact implementation/dependency closure; code changes can invalidate existing evidence without changing the case version.
4. Review the pair and explicitly copy it into a complete operator asset tree using the [activation example](benchmark-corpus.md#rebuild-and-requalify-zephyr). Publish a new catalog revision that changes the row from `case: null` to the registered case ID and removes each blocker only when its evidence is resolved; any remaining blocker keeps the lineage blocked. The case manifest's `corpus_identity` must match the promoted catalog row. `GENERATIVE_DRIVER_BENCH_ROOT` selects the tree but neither installs outputs nor requalifies them. Preserve matching images, missions, platforms, catalogs and notices; keep raw authoring evidence private. Restart the owner with the selected tree, freeze a fresh plan and run `benchmark corpus check` with operator-only password handles.
5. Run the normal actual-agent six-stage pipeline: acquire → interpret → probe → ground → emit → reuse. Reuse starts a fresh worker with only the package, objective and permitted operating data. Retain all outcomes and obtain the harness's final equivalence and separate mission evidence. Native reference calibration and scripted/replay tests do not measure model performance.

Changing the image, mission/acceptance behavior, evaluator/runtime closure, native recipes, references or mutant inventory requires qualification again. Version changed expectations and preserve old evidence/checkouts so historical reports retain their meaning. Software qualification leaves physical transport, timing and plant behavior open until measured separately.

The current evaluator identity includes every Python module under `benchmark_support`. Adding a registration or family therefore also invalidates existing software-case qualification. Refresh those cases before running the default development suite; `corpus check` authenticates only the selected upstream cases. Per-case qualification closures are a future design change, not an assumption this workflow makes.

## Add, unselect or retire

**Add** a lineage by updating a versioned catalog, with blockers until admission is complete; implement and qualify its registered case before claiming execution. Never substitute a synthetic development case for an upstream lineage.

**Unselect** a lineage for one experiment with repeated `--include LINEAGE` or `--exclude LINEAGE REASON` while creating a new plan. Its catalog slots remain visible as excluded, and full-catalog demonstrated coverage retains them. Preserve the original plan/report when a completed trial failed.

**Retire** a case from future experiments by publishing a new catalog/selection and documenting why. Preserve its historical case/lineage/mission IDs, static registration and retained assets/evidence needed to read old pins and reports. Do not rename another case into its ID or delete rows from saved plans. Removing a registry entry is a compatibility change, not an experiment-selection operation.


## Native Zephyr execution

Prefer authentic host-compiled application/protocol code when a qualified board backend is unavailable. For the Zephyr pilot, the interrupt-capable native TTY driver and GPIO emulation replace board I/O; application handlers, parsing and state logic remain upstream. The final `zephyr.exe` (retained as `firmware.elf`) is the executable, whereas the intermediate `zephyr.elf` is not. Supply the agent with the bytes actually executed and disclose its symbols/DWARF, substitutions and hardware gaps.

The optional backend requires Linux (a Linux VM is suitable on macOS/Windows). Its clock policy is `host-clock-with-process-pauses`: the process pauses between calls, while `--rt` catches up to the continuing host clock on resume. The adapter verifies a private executable copy against the pinned source before launch/reset. It preserves source bytes and does not retry uncertain writes. [Measured results and setup failures](verification.md#native-modbus-pilot--2026-10-27) remain distinct from these operating instructions.

Run these commands in a writable evaluator directory with an installed package,
the clean pinned Zephyr source, host GCC, west and Zephyr build dependencies.
`pyelftools` is needed only when authoring the native observation recipe. Password
files belong to the evaluator; workers receive neither their paths nor contents.

```sh
python -m generative_driver benchmark corpus build --case zephyr-modbus-v1 \
  --backend native-sim --source /path/to/zephyr --compiler /path/to/gcc \
  --west /path/to/west --output native-build

python -m generative_driver benchmark corpus prepare-execution --build native-build \
  --password-file /path/to/evaluator.password --output native-intake

GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-intake" \
  python -m generative_driver benchmark corpus exercise-execution --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --output native-observations

python -m generative_driver benchmark corpus exercise-execution --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --renode /path/to/renode --output renode-observations

python -m generative_driver benchmark corpus compare-execution \
  native-observations/observations.json renode-observations/observations.json --output comparison.json

python -m generative_driver benchmark corpus prepare-execution --build native-build \
  --password-file /path/to/evaluator.password --native-record native-observations/observations.json \
  --reference-record renode-observations/observations.json --output native-qualified-inputs

GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-qualified-inputs" \
  python -m generative_driver benchmark truth calibrate --case zephyr-modbus-v1 \
  --password-file /path/to/evaluator.password --ghidra-home /path/to/ghidra \
  --java-home /path/to/jdk --output native-calibration
```

Stop if comparison or calibration fails. Native calibration requires the measured
comparison records, checks the backend and executed image, runs two distinct
reference drivers through diagnostic/final episodes, and rejects the declared
mutants. Pending metadata cannot admit an agent.

After successful calibration, copy its `case.json` to
`native-qualified-inputs/cases/zephyr-modbus-v1/case.json` and its
`zephyr-modbus-v1.enc` to
`native-qualified-inputs/groundtruth/zephyr-modbus-v1.enc`. These are external
resources; installed resources and previous results stay unchanged.

Use the existing runtime configuration and trial command with this resource root:

Install the complete matching Codex distribution, including its tool-host helper
and Bubblewrap resource. The Linux environment must support the worker sandbox's
namespaces and mounts. Verify these before spending model inference on a trial.

```sh
GENERATIVE_DRIVER_BENCH_ROOT="$PWD/native-qualified-inputs" \
  python -m generative_driver benchmark run --case zephyr-modbus-v1 --executor codex \
  --password-file /path/to/evaluator.password --home "$PWD/native-state" \
  --output "$PWD/native-trial" --ghidra-home /path/to/ghidra --java-home /path/to/jdk \
  --approve-emulator-tools --wait
```

The case keeps the same lineage and mission. `execution_setup.method=native-sim`
and the native ELF hash distinguish its execution from the ARM variant. The
generic software-emulation execution category alone does not identify a backend.
All six handoffs, a fresh package-only mission and the final evaluator verdict
remain separately reviewable. One trial establishes feasibility for that trial;
it does not estimate a success rate across firmware families.

Implementation references: [native simulator](https://docs.zephyrproject.org/4.2.0/boards/native/native_sim/doc/index.html),
[pinned native TTY driver](https://github.com/zephyrproject-rtos/zephyr/blob/413b789deb391d3a37d06b463288a5fe765ee57e/drivers/serial/uart_native_tty.c),
[pinned RTU application](https://github.com/zephyrproject-rtos/zephyr/blob/413b789deb391d3a37d06b463288a5fe765ee57e/samples/subsys/modbus/rtu_server/src/main.c).
