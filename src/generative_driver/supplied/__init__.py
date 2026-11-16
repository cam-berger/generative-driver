"""The six stages of a run on a supplied device.

The stage functions in `stages` take the firmware and the mission as data, beside the device the client supplied, and
never see the name of a benchmark case. The code lived in the benchmark's folder until Feature 05 of the untangle Run
(docs/features/untangle/05-supplied-device-stages.md) made it the tool's.

`capabilities` and `package_runner` are the tool's own copies of two things the stage code calls and the benchmark's
grader also has: the rules that check a capability mapping, and the function that runs an emitted package. Grading
code may not import the tool, and the tool may not import the benchmark, so each keeps its own. A probe worker gets
`capabilities.py` as its rules file, so that file carries only its one-line doc and this doc says why the tool has it.
"""
