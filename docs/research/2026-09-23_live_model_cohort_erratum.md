# Live model comparison cohort correction (2026-09-23)

The [2026-09-19 comparison](2026-09-19_model_comparison.md) is a dated
descriptive record, not an unused holdout. Its 550 predictions across 25 dates
were selected by generation before the market open. That is insufficient for
an 08:30 JST operational claim: a prediction could be generated early and
published after the cutoff.

The [read-only production integrity check](2026-09-23_live_history_integrity.md)
found 528 predictions across 24 dates in READY MORNING sets published by their
08:30 cutoff. The 22-row difference includes a session published after the
cutoff. Model and cost metrics in the old 550-row report must therefore not be
used as estimates for the 528-row pre-cutoff cohort.

`research.live_audit` now requires publication metadata, filters on
`published_at <= cutoff_at`, and keeps the last timely decision per ticker and
date. The manually dispatched read-only workflow
`live-cohort-audit.yml` recomputes aggregate arm and cost metrics from the
hosted database without uploading prediction-level rows. Until its output is
reviewed and recorded, the corrected cohort has **no replacement performance
claim** and no model or threshold promotion follows from the older table.
