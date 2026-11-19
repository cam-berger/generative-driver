# No required mutant gets a refusal or a silence wrong

Status: needs-triage

Checked: read (`benchmark_support/generated_device.py:17`).

Every generated device must ship three mutants: `wrong-scale`, `constant-output` and `wrong-state`. They show the hidden sequences catch a wrong value and a wrong state. A step's answer can also be a refusal or no reply, and the grader classifies each driver result as a value, a refusal, no reply or an error. No required mutant answers where the device refuses, refuses where the device answers, or stays silent, so qualification never shows that the grader fails a driver that gets those wrong.

## Direction

Add two required mutants: one that forwards a request the reference refuses and returns a value, and one that refuses a request the device accepts. The generator's interface document and the validator change together.
