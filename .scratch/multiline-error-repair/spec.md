# Complete error replies and deployment repair
Status: complete

Goal: generated drivers capture bounded complete multi-line error replies and can repair contradictory receive contracts through normal deployment behavior.

Approved seams: runtime/package and configurator/workers, as specified by AGENTS.md. Use one failing public behavior test per implementation slice. Keep evaluator expectations and final scoring unchanged. Never widen declared receive bounds silently or retry an uncertain write.

Reproduce the baseline store failure generically with an independent toy protocol: successful write reply END; rejection ERR:reason followed by END. Candidate validation and managed dispatch refuse an impossible one-line contract before contact and give actionable model defects. Keep structural/raw execution unchanged so independently wrong models remain measurable. Offline qualification must distinguish complete recognized rejection from framing failure. Exercise repair through public configurator events, using controlled workers/device boundaries rather than another model baseline.

After tests, freeze a new implementation and compare three fresh trials of each of the five baseline software profiles against preserved9df3887. Same model/runtime, scenarios, evaluator seed, case images, budgets and existing bounded repair policy. Preserve all outcomes; unavailable corpus and physical slots stay visible. No regrading or alteration of the old baseline with changed code. Independent design and result audits precede/follow expensive execution.

Outcome: implementation625f55a passed938installedtests(1optionalLinuxskip),42freshreference/control runs and15/15newprofiletrials. Store1/3→3/3(each891/891); all3locallycorrectedframingbeforeprobe. See [results](../../docs/research/2026-10-27-multiline-repair-results.md) and [index](../../docs/implementation/multiline-repair-verification.json).
