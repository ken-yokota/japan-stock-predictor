# First fixed robust-candidate OOS run / 2026-09-24

The preregistered read-only run at
https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35923192325
used source commit `d45ef1bd71cd6d77885ae639dc927cfd10c2414e`. The
prediction step completed for all 22 configured tickers. Its per-ticker logs
show nonzero champion and Extra Trees forecasts for all 22, but **zero valid
Huber forecasts for all 22**. The aggregate step rejected the missing model
with `ValueError: one or more models produced no forecasts`; it uploaded no
result artifact. Thus there is no common all-arm sample, cost comparison,
confidence interval, or promotion result from this run. The per-ticker log
metrics must not be treated as a paired common-cohort comparison.

The runner's per-row output recorded only the exception class, so the precise
Huber failure reason is not yet measured. A new diagnostic run will print
counts of fixed, data-free failure categories for one ticker. It will use the
same frozen candidate, inputs, and read-only query, and will skip aggregate
scoring. Any subsequent Huber parameter or model change needs a new protocol
version and a separate exploratory result; this failed run remains in the
record. No production forecast model was changed.
