# Breakdown: build step 5, the bench harness that runs trials on generated devices

For the evaluator who runs the benchmark: a scoring suite runs end to end on hand-written fixture devices with scripted workers, through admission, the vault gate, a faulted slot's rerun and sealing, and leaves only an aggregate scorecard. Today a generated device cannot run as a trial at all. The generator (plan 5, its own Run in a private repository), model runs, the real vault and qualification are out.

01. family-seam — the core stops naming families, and each identity is seeded from its registration row
02. unlocked-keys — each password is read once and answer keys reach the grader in memory
03. generated-trials — one generated device runs as a trial, from device pin to grade, and pairs by device
04. set-admission — a suite admits a generated set only on complete native qualification, and records its scoring set
05. vault-gate — a suite reads its sets into memory and starts trials only once their roots are unreachable
06. slot-policy — a scoring suite counts a spent budget as failure, moves past faults, reruns once and acts on audit flags
07. trial-sealing — trial folders sit behind one module, and graded scoring trials are sealed with a stub left behind
08. bounded-output — package output is capped so one trial cannot exhaust the bench host
