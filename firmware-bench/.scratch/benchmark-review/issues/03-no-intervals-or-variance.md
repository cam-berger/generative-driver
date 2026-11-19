# No rate carries an interval, and run-to-run variance is never measured

Status: needs-triage

Checked: read (a search of `scorecard.py`, `outcomes.py`, `pairing.py` and `suite_reporting.py` finds no interval code).

- The scorecard prints pass rates overall and per level and framing with no interval. A level has about 30 devices and a level-and-framing cell about 10, where a rate moves ten points on one device.
- `suite compare` outside a scoring set prints raw differences.
- Each scoring device runs once per system, and repeats are excluded from pairing (reported: `harness.py` and `pairing.py`), so nothing estimates how often the same system passes the same device twice. McNemar's test then reads that noise as a difference between systems.
- The leak check compares the scoring set with a small fresh batch by a one-sided Fisher test with no stratification by level (reported), so it has little power.

## Direction

- Add an exact or Wilson interval to every printed rate. Reporting code only.
- Run a subset of development devices three times with the pinned system once, to measure agreement between repeats, and state it beside every comparison.
- Stratify the leak check by level, or say plainly how large a leak it can detect at the planned batch size.
