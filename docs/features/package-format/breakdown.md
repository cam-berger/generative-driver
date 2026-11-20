# Breakdown: a package is data in one format

For the operator who uses an emitted driver and the agent that reads it. A package becomes a small folder of data with three parts, the driver, the MCP side and the documentation. Each driver is stated once, with its meanings. One command serves it. This builds the six decisions of [ADR 0010](../../adr/0010-a-package-is-data-in-one-format.md).

Not in this Run: runs that drive several controllers, the layout of the run folder, the acquire side, and anything in the benchmark's repository.

The order has a reason. The register family goes first, because the checks it needs are the ones the later Features delete. The driver document comes before the container, and the serve command before the container too, so that the container is written once, with the MCP file that names that command.

01. register-family — Delete the register model family: its package form, template, validator and conversion code, the three skills that write it and the tools only it uses, each named in the Spec.
02. driver-document — One driver format in one version: each operation holds its meaning beside its wire steps, the driver contract and the declarative protocol are computed views, workers get one document, and an earlier driver format is refused by name.
03. serve — One command, `generative-driver serve PACKAGE --binding ...`, opens the managed session, serves the tools over MCP and closes the session; the server and its tools are named after the device, and one module renders the tools for every path.
04. data-package — The container: data only, in one format, with the driver, the MCP side and the documentation as its parts, shaped to hold several drivers; an earlier package format is refused by name, and every path runs the installed runtime, `package_execute` on a supplied device included.
05. documents — The guides, the design and the decision records describe what is built, and what they show can be run.
