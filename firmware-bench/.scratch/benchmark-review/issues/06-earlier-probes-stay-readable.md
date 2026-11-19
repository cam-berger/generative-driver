# An earlier system's probes of the same device stay readable in the runs database

Status: needs-triage

Checked: reported; the design's limits already say the runs database stays readable.

Sealing a concluded scoring trial encrypts its folders and leaves a stub. The configurator's `runs.sqlite3` keeps that trial's events, with tool results and device exchanges, in plaintext. `_refuse_beside_readable` in `harness.py` looks only for readable trial folders of the same set. A later system that runs the same scoring set in the same configurator home can therefore read the earlier system's probes of the same firmware, which is the case sealing was added to prevent.

## Direction

- Require a new configurator home for each scoring suite, and refuse one whose database already holds a run of the same set.
- Or remove a sealed trial's events from the database when its folder is sealed, keeping only what the suite report needs.
