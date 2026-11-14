# 02. plugin-freshness

## Intent

Codex runs its own copy of the plugin. On the development machine that copy held the 28 September skill, which describes seven stages and the old gated protocol, while the server it starts ran today's code; nothing showed the difference. Two things cause it. `setup` always writes version `0.1.0`, and Codex reinstalls a plugin from a local marketplace only when the version string changes. `setup` also refuses to write into its own earlier output, so refreshing means a new folder and a new marketplace.

After this Feature, the version of each generated plugin names exactly what was generated, so an update is something Codex can notice. Running the same `setup` command again replaces the plugin in place, so the folder Codex already references holds the new build. `doctor` says when the plugin that started it, or the plugin `setup` last generated, is older than the installed kit, and gives the command that fixes it. `doctor` reads only the kit's own files and its own environment, never Codex's, because Codex's cache layout is internal and already differs from its documentation.

**Plugin build**: the string `<kit version>+<12 hex digits>` that `setup` writes as the plugin's version. It changes exactly when a generated file's content changes.

## Behaviours

- **02-B1** — `setup --host codex --output DIR` writes the plugin build as `version` in `plugins/generative-driver/.codex-plugin/plugin.json`, and as `GENERATIVE_DRIVER_PLUGIN_BUILD` in the server's `env` in `.mcp.json`, beside `GENERATIVE_DRIVER_HOME`. The 12 hex digits are lowercase, and `<kit version>` is `generative_driver.__version__`.
- **02-B2** — Two runs of `setup` with the same kit, interpreter, server arguments and home write the same plugin build. A change to the content of any file `setup` writes, other than the plugin build itself, gives a different one: the skill text, the manifest, the interpreter, the server arguments or the home.
- **02-B3** — `setup` into a DIR that already holds a plugin it generated replaces `plugins/generative-driver/` and `.agents/plugins/marketplace.json`, and leaves every other file in DIR as it was. A plugin it generated is one whose `.codex-plugin/plugin.json` has `name` `generative-driver` beside a marketplace file whose `name` is `generative-driver-local`; this includes output from before this Feature. The result adds `replaced`, true here and false otherwise, and `replaced_build`, the `GENERATIVE_DRIVER_PLUGIN_BUILD` of the earlier `.mcp.json`, or null when it has none, as output from before this Feature has none.
- **02-B4** — `setup` into a DIR where `plugins/generative-driver` or `.agents/plugins/marketplace.json` exists but is not a plugin it generated refuses with a `ValueError` that names the path, and changes nothing in DIR.
- **02-B5** — `setup` builds the new plugin in full in a temporary folder before moving it into place, on a first run as on a replacement. A failure raises, removes the temporary folder, and leaves DIR exactly as it was, with the earlier plugin and marketplace file when there were any and nothing new when there were not.
- **02-B6** — After it writes a plugin, `setup` records in the home the output directory, the plugin build, the interpreter and the server arguments it used, replacing any earlier record.
- **02-B7** — `doctor` reports `plugin` with:
  - `current`, the plugin build `setup` would write now with doctor's interpreter, the default server arguments and doctor's home;
  - `launched_by`, the value of `GENERATIVE_DRIVER_PLUGIN_BUILD` in doctor's environment, or null;
  - `generated`, null when the home holds no record, or the recorded `output` and `build` with `up_to_date`, which is true when `setup` would write that same build now with the recorded interpreter and server arguments and doctor's home;
  - `state` and `reason`.
- **02-B8** — `state` is:
  - `current` when `launched_by` equals `current`;
  - `stale` when `launched_by` is set and differs from `current`, or when `launched_by` is null and `generated.up_to_date` is false;
  - `unchecked` otherwise.
  A `current` reason is null. A `stale` reason gives the command to run again and says to reinstall or refresh the plugin in Codex and start a new chat. The command starts doctor's own interpreter with `-m generative_driver`, always passes `--home <doctor's home>`, and passes `--output` the recorded output directory, or a placeholder when there is no record; paths are quoted for the platform's shell, with `&` before the quoted interpreter on Windows, as `docs/codex.md` does. An `unchecked` reason says that no plugin build reached this process, so it was started from the command line, by an MCP-only setup, or by a plugin generated before builds were recorded, and that `driver_doctor` called from Codex checks the plugin in use.
- **02-B9** — `driver_doctor`, started over MCP stdio with the `command`, `args` and `env` of a `.mcp.json` that `setup` generated, reports `state` `current`. Started the same way with another value for `GENERATIVE_DRIVER_PLUGIN_BUILD`, it reports `stale`.
- **02-B10** — `doctor` still exits 0, and returns the rest of its report, when the home's record cannot be read or is malformed. `generated` is then null and `state` follows 02-B8, but `reason` is never null: after that state's usual text, or alone in the `current` state, it says the record cannot be read and names its file.

## Interface and seams

`generative_driver.setup.integration_assets(host, output, *, command=None, arguments=None, home=None)` keeps its arguments and result keys, and adds `replaced`, `replaced_build` and `build` to the result. `generative-driver [--home H] setup --host codex --output DIR` prints that result. `generative_driver.setup.doctor(home=None)` adds the top-level `plugin` key and leaves every other key as Feature 01 left it. The record's file name and layout in the home are the builder's choice.

Tests attach at `integration_assets` and `doctor` with temporary homes and output folders, at the `setup` command as a child process, and at MCP stdio for 02-B9. A test that needs other skill text points the generator at a copy, through a seam the builder adds that callers outside tests never use. Existing tests in `tests/test_cli.py` that assert the old `.mcp.json` env exactly, or run `setup` against the real default home (which now gains a record), change with this Feature.

`docs/codex.md` says how to update the plugin: run the same `setup` command again, then reinstall or refresh Generative Driver in Codex, and start a new chat. It drops the advice to regenerate into a new folder. The `driver_doctor` tool description names what it checks: the configured runtime, the optional packages and the plugin build.

## Out of scope

- Reading `CODEX_HOME`, Codex's configuration or its plugin cache.
- Making Codex reinstall or refresh a plugin, or claiming that it does so on its own.
- The checked-in `plugins/generative-driver/`, which keeps a fixed version and a `PATH` command.
- Changing the client skill's text.
- Keeping edits a person made inside a generated plugin folder: `setup` owns that folder and replaces it whole.
- The `runtime_version` a run records, noticed in Feature 01.

## Deviations

- The plugin build is `__version__ + '+'` and the first 12 hex digits of a SHA-256 over every file `setup` writes (path, length, bytes) with the build left empty, rendered in memory — the Spec names no digest; hashing every file's content is what 02-B2 asks, and rendering in memory keeps `doctor` read-only.
- A new kit version alone changes the build, so an upgraded kit reads `stale` until `setup` runs again — the Spec's format puts the version in the build, and reporting a plugin older than the installed kit is the Intent.
- Tests patch `setup._SKILLS`, and 02-B2 also patches `_MANIFEST` and `_MARKETPLACE` — `_SKILLS` is the seam the Spec allows; 02-B2 names the manifest as content that must change the build, which a test cannot vary without a seam, and only tests use them.
- The new plugin is built in a `.setup-*` folder inside DIR; the swap moves the earlier plugin aside, the new one in, then the same for the marketplace file, and a failure undoes the renames in reverse and removes any folder `setup` created — this is how 02-B5 is met, including "nothing new" on a first run.
- A failed record write also rolls DIR back, and the earlier plugin is deleted only after the record is written — 02-B6 requires the record, and treating its failure as a 02-B5 failure keeps DIR and the record consistent.
- A hard kill between renames can leave a lone marketplace file and a `.setup-*` folder, and the next `setup` then refuses — no code can undo a SIGKILL or power loss; refusing is the safe 02-B4 outcome and destroys nothing.
- 02-B4 names the path that fails its name check, the plugin folder first, or the only one of the two that exists — a plugin folder without the marketplace file is not "a plugin it generated" by the Spec's definition, and the message names a path as required.
- `replaced_build` is null for a missing or unreadable earlier `.mcp.json` or a non-string build, and a dangling symlink counts as present and is refused — fits "or null when it has none" and errs toward refusing.
- The record is `setup.json` in the home, `{output, build, command, args}`, written atomically, and `setup` now creates a missing home with mode 0o700 — the Spec leaves the layout to the builder, and 02-B6 needs the home to exist.
- A record that cannot be read, is not JSON or an object, or has fields of the wrong type counts as unreadable, and the reason adds "The record of the last setup cannot be read: <path>." — fits 02-B10.
- An empty `GENERATIVE_DRIVER_PLUGIN_BUILD` counts as null — an empty value names no build, and `setup` never writes one.
- The command single-quotes paths on both platforms (POSIX `'"'"'` escaping, PowerShell `''` doubling, `& ` before the interpreter on Windows), with the placeholder `<directory>` — 02-B8 asks for quoting for the platform's shell with `&` on Windows; the placeholder matches Feature 01's `<path>`.
- A stale reason names both builds, then says to run the command, reinstall or refresh in Codex and start a new chat, and uses the recorded output whenever there is a record — this is what 02-B8 says.
- The 01-B9 test's exact key list now includes `plugin` — the Spec's Interface adds that key.
- The 02-B5 test that locks folders is skipped on Windows and for root; the test whose home is a file runs everywhere — a choice of coverage, and nothing is claimed about Windows.
- 02-B1 first passed with a fixed digest, 02-B6 with `up_to_date: True` and a record written outside the rollback, each replaced by a later Behaviour's test; 02-B9 has no code of its own and its RED was kept between the 02-B7 and 02-B8 code — every Behaviour still has its own RED.
