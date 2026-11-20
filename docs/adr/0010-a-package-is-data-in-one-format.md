# 10. A package is data in one format

Status: Accepted, 2026-11-19.

A package stated its driver three times: the interface model a worker wrote, and a contract and a protocol derived from it. A rendered MCP catalog was a fourth view, and five checks kept them equal. A package also carried a copy of the tool's runtime, 190 of 228 KB in a real one, which the tool itself did not run. ADR 0002 had made the contract and the protocol files of their own, and ADR 0008 had kept every package format ever written readable. The owner decided otherwise:

- A package is data. It holds no Python and runs only through the installed tool, on a supplied device too.
- A package stores each driver once, in one document a worker writes. Every operation holds its meaning beside its wire steps. The driver contract is the public part of that document and the declarative protocol its wire part; the MCP tools are rendered from it.
- There is one package format and one version of the driver format. A package of an earlier format is refused, and the reason names the format.
- A package shows three parts: the driver, the MCP side (the tools an agent gets and an entry for an MCP client) and the documentation. It can hold several drivers, one for each controller of a device. Runs that drive several controllers are not built.
- The register model family is deleted. No run could reach it, and its package carried code a worker wrote.
- One command serves a package: it opens the managed session, serves the tools and closes the session. The server and its tools are named after the device.

This replaces the compatibility rule of ADR 0008 and the packaging of ADR 0002. Two rules stay: the tool runs its installed runtime over a package's data (0008), and generated tools run through sessions and assignments the configurator owns (0002).

We considered three other ways:

- Keep the contract and the protocol as two files, so that an agent can be handed the contract alone. The two must then agree on every name, and a computed view gives the agent the same content.
- Keep the runtime copy for use without the tool. The copy has no lease on the device, no request ids and no record of an uncertain effect, and it offers no MCP server.
- Keep earlier formats readable. The owner starts fresh from the product as it is, and the drivers emitted so far are graded.

The cost: a driver emitted before this change is made again by a new run. Using a driver on another machine needs the tool installed there. The worker's instructions change, and the benchmark has to show what that does to results.
