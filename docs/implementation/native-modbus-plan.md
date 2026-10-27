# Native Modbus execution pilot

Question: can the pinned Zephyr RTU application execute natively with the same
request/rejection/state behavior as the existing Renode case?

Use the existing benchmark run/score/compare seam and provider lifecycle. Keep
source research checkouts and existing trials unchanged. The native build uses
the same upstream commit and application; only board/UART/GPIO wiring changes.
Zephyr's native TTY driver supplies the interrupt interface RTU requires; the
native PTY driver supplies polling/asynchronous interfaces and is unsuitable.

1. Add native-sim to the existing corpus build command, test first. Build the
   unmodified upstream application twice for native_sim/native/64. Preserve the
   final executable ELF, configuration, commands, hashes and source identity.
2. Add a native execution Adapter behind the provider's session Interface. Read
   target memory and GPIO independently of protocol replies; keep native-only
   dependencies optional. Pin execution method, actual artifact, substitutions,
   verified behavior and hardware gaps in case snapshots/reports.
3. Compare identical literal RTU sequences: initial reads, register/coil writes,
   invalid addresses, bad CRC, truncated traffic, recovery, reset and
   complete subsequent state. No automatic write retries. A missing observation,
   transport failure, reply mismatch or state mismatch fails qualification.
   A changed-write negative control must be detected. Only after all pass may a
   native case enter authenticated reference/mutant calibration and one fresh
   six-stage agent trial with package-only reuse.

Decision rule: build success establishes feasibility only. Comparison success
establishes compatibility for its exact vectors, never physical timing or
general equivalence. Model performance requires the separately recorded agent
trial. Missing credentials or tools leave dependent work explicitly unrun.

Implementation tests proceed one failing behavior at a time. Public provider
tests use labelled process fixtures; authentic firmware comparisons are separate.
