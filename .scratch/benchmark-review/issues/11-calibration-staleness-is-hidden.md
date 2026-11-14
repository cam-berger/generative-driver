# A stale calibration reads as passed until the password is supplied

Status: needs-triage

Checked: read (`benchmark_support/registry.py:318-345`; `benchmark cases` output on 2026-11-11).

`benchmark cases` lists `tq9-v2` and the other hand-made cases with `calibration.status: passed` and `authenticated_admission: not-checked`. Whether that calibration still matches the installed grader is decided only at admission, after the answer key is unlocked. The design says every shipped calibration has been stale since `52eb018`, so an operator learns it at the start of a run, with the vault mounted.

## Direction

Publish the grader identity hash each calibration was taken under in the case's public pin, and have `benchmark cases` and `doctor` compare it with the installed identity. The hash reveals nothing about the answer key.
