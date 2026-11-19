"""The six stages of a run on a supplied device.

The stage functions in `stages` take the firmware and the mission as data, beside the device the client supplied, and
nothing else of the client (Feature 05 of the untangle Run, docs/features/untangle/05-supplied-device-stages.md).

`capabilities` holds the rules that check a capability mapping, and `package_runner` the function that runs an emitted
package. A probe worker gets `capabilities.py` as its rules file, so that file carries only its one-line doc.
"""
