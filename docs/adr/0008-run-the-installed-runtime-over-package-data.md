# Run the installed runtime over a package's data

Status: accepted in [Feature 05, driver-package](../features/kit-fixes/05-driver-package.md).

An interface package carries the data that describes its driver: `driver/model.json`, `probe.json` and, in `interface-package/2`, `driver/contract.json`, `driver/protocol.json` and `mcp-tools.json`. It also carries a copy of the kit's runtime (`interface_runtime/*.py`) and driver entry (`driver/*.py`), so that it runs on its own. Until this decision the kit refused to use a package whose copy differed by a byte from its installed runtime. A kit upgrade, even a comment edit, therefore stopped every earlier driver in managed sessions, fresh reuse, `emit_test_live` and the `emit_check` replay.

## Decision

Wherever the kit runs an interface package, it runs its installed runtime and driver entry over the package's data files and never imports the Python the package carries:

- The kit checks the whole package as before. Every file, bundled Python included, must match the manifest, and the manifest must match `manifest.sha256` and any pin the caller gives.
- It copies the package into a temporary folder, then builds a second folder from the installed `interface_runtime` and driver template plus the package's data files, with a manifest that keeps the data files' hashes. A child Python process runs that folder, with the same time limits as before.
- The installed runtime reads every data format a kit has written into a package: `interface-model/3` to `/6`, `driver-contract/1`, `driver-protocol/1` and MCP catalogs of renderer version `2` and `3`. A package is verified under the formats and renderer version it names, so an earlier catalog still verifies and fresh reuse serves it unchanged. A format or renderer version the kit does not know is refused, and the reason names it.

The package's own copy still serves standalone use, through its own command line. `emit_check` still reports `runtime_current`, and the emit gates still require it for a run's own new package.

## Considered

- **Keep refusing a differing copy.** Simple, but every upgrade ends every earlier driver, and the operator can only rebuild it.
- **Run the package's bundled copy in a child process.** Earlier drivers would keep working, but the kit would run code that only the package's own manifest vouches for. A pin proves the package is unchanged, not that its code is the kit's. The driver would also miss fixes made to the runtime since.
- **Upgrade a package's data on read.** Re-rendering an earlier catalog with new wording changes what an agent was shown when the package was accepted.

## Consequences

A runtime change must keep each format it has written readable with its earlier meaning. A rendering change gets a new renderer version beside the old one. A package newer than the installed kit is refused. A kit session and a standalone run of the same package may run different runtime code. When the copies differ, `emit_check` replays on the installed runtime and says so in `replay_reason`. Register packages (`interface-model/1`, which carry worker-written conversion code) keep the earlier currency check.
