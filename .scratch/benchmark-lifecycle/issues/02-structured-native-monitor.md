# Evaluate a structured native monitor adapter

Status: needs-triage

An empty-machine Renode 1.16.1 prototype verified `run_keyword("ExecuteCommand", [command])` through its built-in Robot/XML-RPC endpoint: ten exact echo outputs, empty successful results, typed command failures and protocol faults. This can remove terminal echo/prompt framing behind the existing native-session interface. It did not qualify firmware or measure driver performance.

Before implementation, resolve the stock non-Windows wildcard listener and preserve serialized, single command dispatch. Python's default `xmlrpc.client.Transport.request` retries some connection failures; an effectful command cannot use that behavior. The prototype used a private network namespace and an explicit single-dispatch transport; it is outside the checkout.

Use the immutable sources and findings in the [architecture research](../../../docs/research/2026-10-26-benchmark-architecture.md). Prove timeout/disconnection uncertainty, cleanup, cross-platform binding, raw evidence retention and unchanged functional verdicts before replacing the existing transport. A loopback client URL alone does not restrict a wildcard server.
