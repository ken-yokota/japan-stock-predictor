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

The first runner's per-row output recorded only the exception class. A second
read-only [single-ticker diagnostic run](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35946507873)
used the same frozen candidate and inputs, skipped aggregate scoring, and
reported a fixed, data-free reason category: Huber had **39 NONCONVERGENCE
failures in 39 scored opportunities** for ticker 9101. The champion and Extra
Trees each had 39 forecasts there. This diagnoses one ticker; it does not
prove every failure in the original 22-ticker run had the same cause. Any
subsequent Huber parameter or model change needs a new protocol version and a
separate exploratory result; this failed run remains in the record. No
production forecast model was changed.
