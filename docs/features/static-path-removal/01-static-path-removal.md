# 01. static-path-removal

## Intent

The tool ships two interpretation paths that disagree. Every run prepares interpretation with the skill `interpret-interface`, whose worker writes an `interface-model/7` that the run probes, grounds and emits. The older path is the skill `interpret-firmware-binary` with the tools `firmware_prepare`, `firmware_collect` and `firmware_validate`. Its worker writes an `interface-model/2-experimental`, a static description with no executable driver. Nothing probes, grounds or emits that format, and nothing converts it to format 7. No run reaches the older path, but it still ships: the tool list offers its tools, the acquire result of a raw image points at it, and `model_validate` still passes its format on to `firmware_validate`.

This Feature deletes the older path and nothing else. After it, nothing in the tool names or offers a route that cannot end in `interface-model/7`. A run behaves as before. The two Ghidra scripts that share the old skill's folder are used by every firmware run; they move, unchanged, and keep working.

One rule decides whatever the lists below miss: code, text or a test that exists only for the older path goes. Anything a run uses stays.

## Behaviours

- **01-B1**: The tool list, as `list_tools()` and as the command `generative-driver tools` give it, holds none of `firmware_prepare`, `firmware_collect` and `firmware_validate`. `call_tool` refuses each as it refuses any unknown tool: not ok, exit 1, error `Unknown tool: <name>`.
- **01-B2**: `model_validate`, given a model directory whose `model.json` names the schema `interface-model/2-experimental`, answers as it does for any other format the tool does not read: not ok, exit 1, one defect at `schema` whose text names `interface-model/2-experimental` and `interface-model/7`, and empty `advice`. This holds for a model the old validator accepted. It writes no file into the directory.
- **01-B3**: A raw image's acquire result names what a run does with it. The result of `acquire_firmware_artifact` has `front_end` `interpret-interface` and `next_tool` `interpret_run`. The result of `acquire_stm32_flash`, and the result of `acquire_classify` for a flash dump that is not an ELF image, have `front_end` `interpret-interface`. Every other field of the three results is as before. `interpret_run` in mode `prepare`, given the `front_end` and the `inputs_for_interpret` of the `acquire_firmware_artifact` result, prepares a workspace and returns ok.
- **01-B4**: No stage's list in `STAGE_TOOLS` names a tool that `list_tools()` does not hold. The interpret list is `model_validate`, `ghidra_run` and `image_map_check`.
- **01-B5**: `ExportDecomp.java` and `SeedCortexM.java` are in the installed resources under `tools/workspace/`, beside `ghidra_run.py`, and `toolchain/skills/` holds only `find-datasheet`, `interpret-interface` and `interpret-selfdescribing`. `ghidra_run` called with no `script_path` gives the analyzer `tools/workspace/` as its script folder. `firmware_analysis_inputs` names the two scripts there, and both files exist.
- **01-B6**: No text under the installed resources, no tool description and no current document names the removed path. The names are `interpret-firmware-binary`, `firmware_prepare`, `firmware_collect`, `firmware_validate`, `interface-model/2` and `schema 2` or `schema-2`. The current documents are those `tests/test_guides.py` lists as `CURRENT_DOCUMENTS`, `docs/support.md` among them.

## Interface and seams

The public interface is the tool facade, `generative_driver.toolkit.call_tool` and `list_tools`, with the command line over it. The installed resources, `resources_root()`, are what a worker reads.

- Tests of 01-B1, 01-B2, 01-B3 and 01-B5 attach at `call_tool` and `list_tools`, beside their neighbours in `tests/test_toolkit.py`, `tests/test_stm32_flash_tool.py` and `tests/test_ghidra_exports.py`. The test of `ghidra_run` uses that file's scripted analyzer and reads the script folder from the arguments the analyzer received; no Ghidra runs.
- The test of 01-B4 reads `STAGE_TOOLS`, as `tests/test_package_reuse.py` does. No run assigns the interpret list today: a run's interpret worker gets no kit tool, or the device-first tools. The list is kept true, not put to use.
- Tests of 01-B6 use the helpers that exist: `texts_of` and the patterns of `tests/worker_texts.py` for the resources and the tool descriptions, and the list `GONE` of `tests/test_guides.py` for the documents.
- Tests of earlier Runs are already named `test_01_B1_...` and so on, so the kit's check by name cannot tell them from this Feature's. The implementer's report names each Behaviour's own tests, and the Reviewer checks them by those names.

Deleted:

- `src/generative_driver/_toolchain/firmware.py`, with its entry in `_MODULE_TOOLS` of `toolkit.py` and the path argument `artifact`, which only `firmware_prepare` took.
- `resources/toolchain/skills/interpret-firmware-binary/`: `SKILL.md`, `OUTPUT_SCHEMA.json` and `TOOLING.md`. Only `firmware_prepare` gave `TOOLING.md` to a worker.
- The three entries of `resources/toolchain/tools.json`, and the sentence about schema-2 evidence in the descriptions of `interpret_collect` and `model_validate`.
- The branch of `model_validate` in `_toolchain/interpret.py` that hands a schema-2 model to `firmware_validate`, with its sentence in the docstring.
- The row for `interface-model/2-experimental` in `docs/support.md`.
- `test_firmware_import_prepares_neutral_inputs_and_freezes_evidence` in `tests/test_toolkit.py`.

Moved with `git mv`, bytes unchanged: `ExportDecomp.java` and `SeedCortexM.java`, to `src/generative_driver/resources/tools/workspace/`. That folder holds the helpers a worker carries into its workspace, and `ghidra_run.py`, which runs both scripts, is already there. What names the old folder follows: `_toolchain/ghidra.py` (the default script folder and its docstring), `analysis.py`, `kit_analysis.py`, the docstring of `ghidra_run.py`, the path filter of `.github/workflows/real-ghidra.yml`, `tests/native_ghidra.py` and `tests/test_ghidra_exports.py`.

Tests that asserted the old names follow 01-B3: the two assertions of `front_end` in `tests/test_toolkit.py`, with the comment that says schema 2's input stays, and the result in `tests/test_stm32_flash_tool.py`.

Comments and docstrings under `src/` that name the removed path are corrected with the code they describe. The Reviewer checks this with a search of `src/`, `plugins/` and `.github/` for the names of 01-B6.

## Out of scope

- The ELF, datasheet and bus-scan results of `acquire_classify`. They name no `front_end` since the package-format Run and stay so, although `interpret-interface` reads an ELF image too.
- `tests/test_package_engine.py`, which uses `interface-model/2-experimental` as one more schema the runtime refuses. It stays as it is.
- The interpret list of `STAGE_TOOLS` beyond the three names it loses. That no run assigns it is reported, not changed.
- `image_map_check.py`, `ghidra_run.py` and the two Java scripts themselves. No line of their code changes; only the one docstring sentence of `ghidra_run.py` that names the old folder.
- The Specs of finished Runs under `docs/features/` and the dated note `docs/research/2026-10-30-firmware-learning-practice.md`. They are records and keep their text. The note's one link to the deleted `SKILL.md` is repointed at that file as of commit `0f8a4e4` on the repository's host, so it still resolves.
- `docs/design.md`, `CONTEXT.md` and the ADRs. None names the removed path.
- No new tool, no new format, and no code in `src/` that exists only for a test.

## Deviations
