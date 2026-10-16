# Contributing

Read [CONTEXT.md](CONTEXT.md) and the [design](docs/design.md). Work through the public CLI/MCP, configurator/workers, runtime/package or benchmark interface.

## Change and check

1. Write one behavior test with an independent expected result; observe it fail.
2. Implement the change; observe it pass. Repeat for the next behavior.
3. Build and test the installed wheel outside the checkout. Native CI must pass before claiming Windows support.

```sh
python -m unittest discover -s tests -v
python -m build
```

Scripted workers and device replies test contracts. Model runs require explicit selection and retain configuration, budgets, usage, failures and evidence hashes.

## Add device support

Extend the toolkit facade and packaged manifest, or an existing transport. Keep device facts in supplied evidence and evaluator-owned truth. Load optional dependencies at use time, launch processes with argument arrays, require explicit bindings, and write only to caller-owned output directories.

State what acquisition actually captured. Separate reply framing from physical meaning. Each benchmark needs versioned inputs, independent accepted/rejected observations and one final evaluator verdict after stage handoffs. Keep evaluator passwords outside candidate inputs.

## Compare and publish

For performance claims, use at least three fresh runs per configuration. Record case, evaluator, seed, runtime, provider/model/settings, toolchain, skills and budget. Report success counts, stage failures and time/usage distributions; missing usage stays unknown. Label comparisons that change several dimensions.

Inspect the built wheel and source archive before publishing. Include only original or redistributable assets, preserve third-party notices, and exclude credentials, recordings, local paths and personal run history.
