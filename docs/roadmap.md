# Season viewer roadmap

Playoff Picture now reads the existing standalone simulator's saved artifacts:
all teams, current records, five postseason probabilities, conference filters,
sorting, model favorite and explicit freshness/blockers. Inline simulation
remains disabled in the time-sensitive live worker.

Later additions, each requiring its own bounded milestone:

- **Expected final wins:** retain per-draw win totals and show season expectations
  alongside current records, with the same final-result and cutoff guards.
- **Seed distributions:** retain all postseason seed frequencies, including no
  playoff berth, rather than deriving them from current aggregate probabilities.
- **Weekly probability changes:** compare hash-verified complete dated artifacts
  against explicit weekly cutoffs; disclose missing/blocked weeks and model changes.
- **Win/lose scenario impacts:** run conditional scenarios separately from the
  collection cycle, preserving observed finals, all draws and tiebreak guards.

Before increasing samples, measure standalone runtime. Strength sensitivity
remains uncalibrated; sampling precision does not validate the model. Missing
net-touchdown tiebreak evidence may block complete output. Refresh automation
outside the live worker and production viewer activation require separate scope.
