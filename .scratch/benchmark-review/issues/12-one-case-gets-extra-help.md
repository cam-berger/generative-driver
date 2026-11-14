# One case's workers get an extra input file that the others do not

Status: needs-triage

Checked: read (`benchmark_support/emulated.py:25-30`).

`_development_conditions` writes `DEVELOPMENT_CONDITIONS.json` into the probe and ground workspaces only when the case is `sampled-sensor-v1`. The file holds the operator's public inputs, not answers, so nothing leaks. It is still help that one case gets by name from code on the system-under-test side, which makes that case's result harder to compare with the others, and it is a case named outside the family seam.

## Direction

Make the file part of any case's public mission data, supplied through its family, or drop it and let the case stand without it. Decide before the anchor cases are requalified.
