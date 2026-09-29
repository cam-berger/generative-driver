# Toolkit extraction checks

Approved seams: installed toolchain CLI/MCP facade and device runtime/emitted package. Source research files remain untouched. Tests use invented examples, temporary directories and replay; no devices or actual model calls.

## Red → green record

| Slice | Observed red | Implementation and observed green |
|---|---|---|
| Installed catalogue/resources | `generative_driver.toolkit` missing | Resource lookup and generic catalogue. |
| Firmware handoff | `call_tool` missing | Extracted acquisition/firmware tools; import a pinned synthetic image, prepare neutral inputs, collect evidence, refuse a second collection. |
| Portable register workspace | Legacy validator imports absent `tc` | Sealed validator kit includes its required modules and resources; runs with isolated Python outside the checkout. |
| Generic register package | Synthetic checker acknowledges only the old target address | Shim explicitly receives the model's register address; invented address 0x20 passes conversion and bus-footprint checks. |
| Executable package | Missing extracted interpreter/emit modules and advertised `probe_json` mismatch | Real shared runtime and facade alias; replay, emit, relocate and standalone replay return independently specified 21.5. |
| Usage concurrency | Missing usage facade/Unix-specific lock | SQLite lock, lazy dependencies and idempotent usage record; four processes count one delivered record once with Unix/hardware imports unavailable. |
| Optional analysis/adapter tools | Missing Ghidra/image-map/bus facade modules | Packaged helpers return explicit missing-analyzer, ambiguous-map and unsupported-controller diagnostics. |
| Explicit storage | Relative artifact path accepted | Facade rejects relative artifact paths before creating run storage. |
| Explicit binding/effects | Default FTDI adapter or writes accepted without grant | Discovery, register probe, emitted CLI and live test require selected binding; writes require explicit grant before transport open. |
| Native analyzer process | Command record lacks native process policy | External scripted launcher times out and terminates, argv records native launcher, shell=false and process-group policy. Windows branch uses analyzeHeadless.bat/CREATE_NEW_PROCESS_GROUP/taskkill; macOS uses sessions/killpg. |
| Register package integrity | A changed driver executes and creates a marker before rejection | Manifest/pin and template checks run first; package executes only from a fresh verified copy; tampered marker never created. |
| Non-UTF-8 host locale | UnicodeEncodeError emitting a Unicode package name under ASCII locale | Explicit UTF-8 text I/O; same package pipeline passes under a forced non-UTF-8 locale. |
| Installed optional flash tooling | Tool searches resources/.venv/bin/esptool | Current Python invokes optional esptool/espefuse modules; external scripted module records correct argument vector without hardware. |

The retained 23 engine tests exercise its public execution/validation interfaces: literal binary/temperature/USB results, pre-I/O validation, binding/effect grants, identity checks, error classification, bounded operations, cleanup and no whole-command retry. They are extracted regression coverage, not newly claimed TDD cycles.

## Verification

Local macOS verification: 11 toolkit tests and 25 package/runtime tests pass with Python 3.14. Tests locate the imported installation for child processes, so the same suite can run against a wheel from an unrelated working directory. Python 3.11/3.13 and native Windows results remain the CI release gate; the scripted analyzer test does not qualify a real Windows Ghidra installation. Actual hardware, vendor libraries and model benchmarks were not run by this extraction task.

```
python -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_toolkit.py' -v
python -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_package*.py' -v
```

Resource paths use the installed package. Run storage is caller-selected and absolute; inventories use portable slash-separated names. Optional hardware imports stay lazy. Generic tool catalogue has 29 implemented entries. ESP32 flash acquisition retains a dump when automatic application carving is unavailable and reports that limitation explicitly.

Schema-1 conversion is candidate Python and remains trusted caller-supplied code; hashes do not make it a sandbox. Emitted packages record replay/live/unlabelled source evidence separately from consistency checks. A package's self-contained checksum establishes integrity against its own manifest; retain the emitted manifest hash separately to detect replacement of both.

## Distribution inventory

Extracted original modules: generic acquisition, interpret preparation/collection, model validation, register and executable probing, package emission/checking, usage accounting, firmware workspace, image mapping and Ghidra wrappers; shared interface runtime; generic schema documents, interpret skills, original exporter scripts and synthetic PyFtdi shim. No research runs, private captures, printer/HiDock driver, vendor firmware, datasheet PDF, Ghidra/JDK executable or hardware library is copied.

Optional externally distributed dependencies: pypdf (datasheets), PySerial (UART), PyUSB/libusb (USB), PyFtdi/libusb (I2C/SPI), esptool/espefuse (selected ESP32), Ghidra/JDK (analysis). Their licenses remain with their distributions. Public Bosch/CMSIS-DAP registry entries are URLs and pinned hashes only; benchmark third-party evidence is inventoried separately by its maintainer.

## Independent integration review

The public benchmark preparation/check seam exposed a handoff defect: accepted artifacts are prefixed by the configurator, but benchmark emission still read mutable working paths. The focused `test_package_benchmark.py` first failed with `KeyError: adapted_model_dir` despite a valid accepted handoff. Emission now reads accepted model/probe bytes, and reuse receives the accepted package. The same seam test verifies that a valid package for a different model and an integrity-only historical runtime cannot pass benchmark emission. Actual current-runtime replay and the handed-off model hash are required. The focused test passes; 26 package/runtime tests and nine benchmark tests pass after integration.

## Post-snapshot review

The existing register-package seam reproduced unclosed-file `ResourceWarning`s in model loading, validation, emission and replay. Review cleanup now closes those files deterministically while preserving UTF-8 and generated LF bytes. The same 26 package/runtime and 11 toolkit tests pass with `-W default::ResourceWarning`, with no file warnings. This is maintenance verified by the existing public seams, not a newly claimed TDD cycle. Worker skills now identify the configured Python interpreter on Windows and describe supplied-workspace instructions without claiming enforced OS isolation. These source updates follow the frozen baseline source tree represented by commit `1090a6a` in the release history. Updating author metadata preserved that tree and its code and skill hashes.

Review also corrected a nested-quote f-string that depended on Python 3.12 syntax despite the declared Python 3.11 floor. Native Python 3.11 reproduces the frozen file's `SyntaxError` and compiles all 59 updated source and packaged Python files successfully. Only source quoting changed; emitted text is identical. Newer Python's `ast.parse(feature_version=(3, 11))` did not reject that lexical construct. This syntax check is distinct from the full installed native-version test matrix.

The physical callback review exposed a second input-integrity issue: schema-1 validation imported `convert.py` and created bytecode within the pinned model directory. A new public `model_validate` seam test first failed because a valid model's file inventory gained `__pycache__/convert.cpython-313.pyc`. The loader now executes the supplied source bytes without reading or writing bytecode caches; integrity checks retain their full inventory. The test passes. All 27 package/runtime tests, including synthetic register conversion and relocated executable replay, and 11 toolkit tests pass. Native Python 3.11 source compilation also passes. No hardware or model inference was used.

## Advertised probe evidence argument

An actual benchmark worker supplied the advertised `probe_json` argument to `probe_diff` and received a missing-`probe` error. The immutable run retains those failed calls and its independent probe verdict; this source repair does not change that execution environment or its evidence.

Two public-seam TDD cycles reproduced and repaired the defect:

| Seam | Observed red | Observed green |
|---|---|---|
| Toolkit catalogue and dispatch | `test_advertised_probe_diff_argument_compares_saved_replay_evidence` reproduced `TypeError: probe_diff() missing 1 required positional argument: 'probe'` with a schema-correct call against saved synthetic replay evidence. | The facade translates `probe_json` for `probe_diff`, as it already did for package emission. Comparison passes; legacy `probe` remains supported, conflicting aliases are rejected, and relative evidence paths are refused before run storage is created. |
| Configurator worker gateway | `test_probe_json_cannot_read_outside_the_assigned_workspace` reached an assigned probe stage through scripted acquisition and interpretation. The external probe file was incorrectly read, returning an invalid-envelope diagnostic. | The shared path-argument set now includes `probe_json`. Absolute external and relative paths are rejected at the workspace boundary, with no `tool.started` event. The same guard applies to package emission. |

Each named test was run alone for its red and green cycle with `PYTHONPATH=src:tests .venv/bin/python -W ignore::ResourceWarning -m unittest <module>.<class>.<test> -v`. The complete focused command below passes 20 tests on native macOS Python 3.13. It includes existing package-emission compatibility coverage. The new tests use synthetic replay and an external scripted agent, with no model inference or device access.

```sh
PYTHONPATH=src:tests .venv/bin/python -W ignore::ResourceWarning -m unittest test_toolkit test_service test_package -v
```
