# Noticed during the kit-fixes Run

- 01: `src/generative_driver/agents.py` — a run's `runtime_version` is read from `config.get('version')`, a key `configure` never writes, so it is always null.
- 01: `src/generative_driver/setup.py` — `core.platform` comes from `platform.platform()`, which on macOS runs `uname -p` and `file -b <python>` from `PATH`, so `doctor` still runs two programs the operator did not configure and the value changes with `PATH`.
- 01: `src/generative_driver/setup.py` (`configure`) — raises `JSONDecodeError` on a `config.json` that is not valid JSON and reads it without an encoding, so a file doctor now names has to be fixed or removed by hand.
- 01: `src/generative_driver/configurator.py` (`_configured_executors`) — raises on a `config.json` that is not valid JSON or not an object, so a start fails with an unhandled error where doctor now gives a reason.
- 01: `src/generative_driver/mcp.py` — the `driver_doctor` tool description does not mention the configured runtime's answer or `extras`; possibly Feature 02's.
- 01: `tests/test_family_seam.py` — an earlier Run's tests already use the names `test_01_B1` to `test_01_B17`, so the Done check's lookup of `01-B1`..`01-B12` would be satisfied by them even if a kit-fixes test were missing.
- 01: `tests/test_cli.py` (several tests) — children started with `python -m generative_driver` get the inherited environment, not `package_env()` as STANDARDS.md asks.
- 01: `config/` in the project root — the folder the unrelated `codex` on `PATH` writes, hidden by `.git/info/exclude`; it predates this build.
- 02: `src/generative_driver/cli.py:76-78` — the `setup` command prints a raw traceback for the 02-B4 `ValueError`, as it did before for `FileExistsError`; a one-line message would be friendlier.
- 02: `src/generative_driver/setup.py:33-58` — `configure` writes `config.json` with the same temp-file-then-`os.replace` pattern as the new `_record`; they could share a helper.
- 02: `docs/verification.md:150-153` — the release check still says to use new output directories for `setup`, which is no longer needed.
- 02: lines over 120 columns that predate this Feature — `tests/test_cli.py:87, 91, 97, 317` and `src/generative_driver/mcp.py:47, 65`.
- 02: the checked-in `plugins/generative-driver/.codex-plugin/plugin.json` keeps version `0.1.0` and carries no build, so `doctor` reports `unchecked` for it (out of scope by the Spec).
- 02: `src/generative_driver/setup.py:232-233` — the new result keys use double quotes, following the older return dict, where the rest of the new code uses single quotes.
