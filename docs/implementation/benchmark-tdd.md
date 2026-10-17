# Benchmark verification

Test through the [four public seams](../design.md#public-test-seams), recording failure before implementation and success afterward. Required behavior: six gates ending in fresh reuse, bounded pre-final repair, terminal final failure, offline grades, stable inputs, stale-calibration rejection and nine distinct suite slots. Installed packages must behave identically outside checkout.

Keep scripted fixtures separate from native reference results. See [verification](../verification.md) and its [historical index](benchmark-final-verification.json).
