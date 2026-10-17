# Local analysis tools

Use installed tools and their own local docs only. Tool paths authorize neither neighboring directories nor image evidence. Record missing tools; do not download replacements.

## Workspace and launch

Use supplied `ghidra_run.py` with explicit Ghidra/compatible JDK directories. It selects Windows `support/analyzeHeadless.bat` / `bin/java.exe` or macOS `support/analyzeHeadless` / `bin/java`, records process policy and kills the process tree on timeout. Native Windows Ghidra remains unqualified.

The workspace must have no whitespace; Windows arguments must also avoid shell metacharacters. Create local settings/cache/temp/scripts/projects directories. Confirm installed-version JVM overrides in local docs: commonly `application.settingsdir`, `application.cachedir`, plus `user.home` and `java.io.tmpdir`, passed through `GHIDRA_HEADLESS_JAVA_OPTIONS`. Check logged paths; stop and correct any outside-workspace location. This is not a sandbox.

Derive absolute paths from the current directory, without reading parents. Pass arguments as a list through the helper; this illustration uses POSIX continuations:

```text
<absolute-ghidra>/support/analyzeHeadless <absolute-workspace>/projects analysis \
  -import <absolute-workspace>/image.bin \
  <import-options-justified-by-the-image> \
  -scriptPath <absolute-workspace> \
  -postScript ExportDecomp.java <absolute-workspace>/exports
```

Infer loader, architecture, endianness, compiler, offsets and addresses from image evidence. The helper does not infer/repair maps or seed callbacks. Retain justified scripts/logs for containers, relocations, copied data and indirect entries. Cross-check critical decompilation against disassembly/bytes.

`ExportDecomp.java` takes an absolute workspace export directory and optional per-table byte bound: default 16 MiB, range 1024–1073741824 (`ghidra_run.py --navigation-max-bytes`). Require nonempty `functions.tsv` (address/name/completion), `function_<address>.c` and `disassembly.txt`; zero exit status alone proves nothing.

## Navigation tables

Headered TSV addresses match `functions.tsv`; functions use entry addresses, empty outside a function.

| Table | Fields / limits |
|---|---|
| `references.tsv` | `from_address`, `from_function`, `to_address`, `to_function`, `type`; memory references only, excluding stack/register/external |
| `strings.tsv` | `address`, `encoding`, decoded-character `length`, `clipped`, referencing `functions`, `refs_outside_functions`, `value`; values clipped after 4096 characters |
| `switches.tsv` | `function`, `switch_address`, hexadecimal `case_value`, `target`; unassigned cases stay `unknown`, failed decompilations add no rows |

String escapes: `\\`, `\t`, `\n`, `\r`, or `\u` plus four hex digits for other controls. Follow outside-function string references in `references.tsv`. `navigation.json` records bound, `rows`, `rows_found`, `truncated`; the helper reports `exports.truncated`. Check tables against that manifest. Truncation preserves leading address-ordered rows; missing rows prove no absence. These are static facts, not reachability. Unresolved computed references may be missing; confirm dispatch in code/disassembly/bytes.

## Load mapping

Run `python image_map_check.py image.bin` with configured Python 3.11+ before choosing a base. A plausible vector table supports several base/prefix combinations. Establish `address = base + (file offset - header)` from all three:

- Reset vector resolves to startup code: data copy, BSS clearing, initialization.
- Absolute literal pointers resolve to strings/tables used by that code; compare alternative mappings.
- Header sizes/offsets/checksum identify the same mapped payload.

Record best mapping, checked pointers/targets and disagreements in `NOTES.md`; pair every provenance address with its original offset. Apply the map during import by carving or explicit base/file offset, never by later mental correction.

## Ghidra 12.1.3 details

Read installed `support/analyzeHeadlessREADME.md` before relying on these options:

| Option | Required form |
|---|---|
| `-loader` | `BinaryLoader` for raw images; neither display name `Raw Binary` nor Java class/package path |
| `-loader-baseAddr` | `[space:]offset`, hex without `0x` |
| `-loader-fileOffset`, `-loader-length` | Hex with `0x` |
| `-processor` | Exact case-sensitive `.ldefs` language `id` |
| `-preScript`, `-postScript` | Filename only; directory via `-scriptPath` |
| `-log` | Analysis log path |

If auto-selecting a loader, record its name and supporting image evidence. Judge success jointly from analyzer status, `Analysis succeeded` / `Import succeeded`, and nonempty exports. Invalid loaders have produced zero exports; `|| true` hid their failures.

Log4j's `Could not determine local host name` / `UnknownHostException` was nonfatal in examined runs, but may accompany another real error. Allow-list that diagnostic only; inspect remaining stderr and avoid undocumented suppression flags.

## Shell output

Write producer output to workspace files before previewing (`strings image.bin > strings.txt`). Early-closing readers such as `head` can cause SIGPIPE/status 141 under `pipefail`, hiding later steps. Inventory exports with a counting/sizing script. Search exit 1 means no matches; distinguish it from a producer error. Capture analysis status separately from preview/search status. Never hide both with blanket `|| true`.
