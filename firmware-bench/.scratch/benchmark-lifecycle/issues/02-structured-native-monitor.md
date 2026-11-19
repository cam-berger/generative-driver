# Evaluate a structured native monitor adapter

Status: needs-triage

An empty-machine Renode 1.16.1 prototype verified `run_keyword("ExecuteCommand", [command])` through its built-in Robot/XML-RPC endpoint: ten exact echo outputs, empty successful results, typed command failures and protocol faults. This can remove terminal echo/prompt framing behind the existing native-session interface. It did not qualify firmware or measure driver performance.

Before implementation, resolve the stock non-Windows wildcard listener and preserve serialized, single command dispatch. Python's default `xmlrpc.client.Transport.request` retries some connection failures; an effectful command cannot use that behavior. The prototype used a private network namespace and an explicit single-dispatch transport; it is outside the checkout.

Use the immutable sources and findings in the [registration decision](../../../docs/adr/0001-benchmark-registration-and-selection.md). Prove timeout/disconnection uncertainty, cleanup, cross-platform binding, raw evidence retention and unchanged functional verdicts before replacing the existing transport. A loopback client URL alone does not restrict a wildcard server.


Pinned prototype sources: [Renode ExecuteCommand endpoint](https://github.com/renode/renode/blob/d66b0c2aa3d420408eccecfd1d3bab0fd702a6db/src/Renode/RobotFrameworkEngine/XmlRpcServer.cs#L27-L74) and [non-Windows wildcard listener](https://github.com/renode/renode/blob/d66b0c2aa3d420408eccecfd1d3bab0fd702a6db/src/Renode/RobotFrameworkEngine/HttpServer.cs#L93-L106). Ten warm echo calls had a local median near 1.4 ms; these empty-machine calls are not a firmware/driver speed comparison. A separate 2,000-command read-only monitor experiment took about 337 seconds without reproducing an earlier timeout. Record request/reply timestamps and virtual-time progress before attributing latency to generated logic. Evaluate batched state observations without dropping any checks or redispatching effectful commands.
