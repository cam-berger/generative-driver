# Triage labels

| Skill role | Local `Status:` value | Meaning |
|---|---|---|
| `needs-triage` | `needs-triage` | Maintainer needs to evaluate the issue. |
| `needs-info` | `needs-info` | Waiting for more information. |
| `ready-for-agent` | `ready-for-agent` | Fully specified and ready for agent implementation. |
| `ready-for-human` | `ready-for-human` | Requires human implementation. |
| `wontfix` | `wontfix` | Will not be implemented. |

Record the applicable role on a `Status:` line near the top of a local issue file. The wayfinding workflow additionally uses `claimed` and `resolved` for execution progress, as described in [issue tracker conventions](issue-tracker.md).
