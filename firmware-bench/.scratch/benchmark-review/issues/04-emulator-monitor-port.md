# The emulator's monitor is an open local port for the whole trial

Status: needs-triage

Checked: the port and command line were read (`benchmark_support/native.py:177-186`); that a worker can reach it is inferred.

The native session starts Renode with `--port <monitor>` on a free TCP port, and the port number is in the process's command line. Renode's monitor reads and writes device memory and takes no credentials. A worker runs as the same user, so it can find the port with `ps` and read the state cells the grader reads, or change them.

The design's limits say a worker can read the device control endpoint file. They do not mention this port, and the baseline's rule that workers see only a generated device's UART depends on it. The transcript audit matches forbidden paths in shell commands, so it would not flag a connection to a port.

## Direction

- Give the monitor a pipe or a Unix socket in the session's private directory, or bind it behind a random token if Renode allows one.
- Until then, add the port to the stated limits, and have the audit flag a worker command that names the monitor or UART port number of its own trial.
- [Lifecycle issue 02](../../benchmark-lifecycle/issues/02-structured-native-monitor.md) already weighs a structured monitor adapter; settle both together.
