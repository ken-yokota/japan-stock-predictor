# Live History integrity check (2026-09-23)

This check used a read-only transaction against the hosted database. It did not
change or delete any prediction, outcome, or trade. Counts below are a snapshot
as of 2026-09-23 05:53 UTC; they may change when later sessions are recorded.

The old History join returned **950 rows for 638 distinct prediction IDs**. A
prediction can have more than one append-only actual-result revision and more
than one simulated-trade valuation. Joining both tables only on prediction ID
multiplied rows and mixed the original strategy with later revaluations. One
prediction had two actual revisions and three trade valuations. The corrected
query selects the highest actual-result version and its trade under the
prediction set's original strategy. This reduces the same unfiltered history to
638 rows. It does not replace missing profit with zero.

Those 638 predictions belong to 29 READY sets. The 2026-08-28 date alone has
three sets: one MORNING set generated and published before its 08:30 JST cutoff,
one REFERENCE set created two days later, and one MORNING replay also created
two days later. In addition, the 2026-08-10, 08-11, and 08-13 MORNING sets were
published after their cutoff. The dashboard's live performance view now requires
`READY`, `MORNING`, and `published_at <= cutoff_at`. It therefore contains
**528 predictions across 24 dates** (22 stocks per date), including the timely
08-28 set. The five excluded sets remain in the database for audit; no claim is
made that they were tradable at 08:30. Database publication time alone does not
prove email receipt or actual executable entry prices.

The same pre-cutoff filter is applied to the read query feeding Backtest's
scenario recomputation and Sector Analysis. That query also returns 528 final
prediction/outcome pairs in the current snapshot, so those pages cannot
silently reintroduce the reference or replay sets into their live comparisons.

This makes the visible History a pre-cutoff publication record, not a causal
estimate of investment return. Its older simulated trades include zero-cost
assumptions, and the UI labels their summed profit as a recorded simulation
rather than verified net return. Further OOS and cost comparisons remain
separate from this production-history repair.
