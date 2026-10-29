# MHS interfaces and Generative Driver

Primary-source inspection, 2026-10-28. Architecture assessment only; no device operations, model trials or conformance tests.

## What MHS exposes

Model Hardware Standard (MHS) defines a standardized device driver with read/write primitives, discovery and descriptive metadata. Natural-language tags produce a device reference covering capabilities and enforced safety limits. Agents access that driver through **MCP, CLI or code APIs**. The driver and MCP are complementary layers. [Anthropic announcement](https://www.anthropic.com/news/model-hardware-standard-research-preview)

The official site still describes an application-only research preview ahead of open source. The inspected official pages provide no public technical schema or conformance suite, so exact MHS compliance work remains unknown. [Official MHS site](https://www.modelhardwarestandard.com/)

Model Context Protocol (MCP) standardizes discovery and invocation of tools with parameter schemas, plus resources and prompts. It can expose a hardware runtime without defining that runtime's device protocol. [MCP architecture](https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture), [tool specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)

## Current project fit

The generated package already has [`describe()` and `execute()`](../../src/generative_driver/resources/toolchain/template/interface_driver/__init__.py), plus a [CLI](../../src/generative_driver/resources/toolchain/template/interface_driver/__main__.py). These expose named operations, parameter specifications, explicit bindings, allowed effects and evidence. The existing [MCP server](../../src/generative_driver/mcp.py) manages generation runs and benchmarks; it does not expose finalized package operations as device tools.

Engineering inference: a local MCP adapter is a limited extension rather than a new driver implementation. It would map package operations to MCP tool schemas, call the shared runtime, and return structured results and evidence. It must also route execution through the device owner and preserve binding selection, effect grants, exclusive access and uncertain-write handling required by the [accepted design](../design.md). Connecting another client must not create another independent device owner.

| Scope | Assessment |
|---|---|
| Local MCP access to a finalized package | Small, bounded adapter and integration-test work; schema translation and device ownership still need implementation. |
| Remote or shared MCP service | Larger: authentication, authorization, per-caller grants, durable operation status, cancellation and concurrent-client behavior. |
| MHS compliance | Cannot estimate reliably until its specification or preview SDK is available; MCP support alone does not establish compliance. |

These are architecture estimates, not measured implementation times.

## Why retain the driver

Engineering recommendation: keep the validated driver/runtime as the reusable core and add MCP as an access surface. Scripts, applications and agents then share the same decoding, state sequencing, limits and evidence. Local code can execute repeated or time-sensitive sequences without an agent round trip for each command, while MCP supplies tool discovery and agent integration. Actual timing guarantees depend on the runtime and device.

MHS publicly describes the same use of code APIs for long-running or faster sequences. [Anthropic announcement](https://www.anthropic.com/news/model-hardware-standard-research-preview) The exact future mapping of this project's model and evidence into MHS remains unverified.
