# Extra Trees post-failure analysis / 2026-09-24

The fixed all-arm Huber/Extra Trees comparison failed because Huber produced
zero valid forecasts across all 22 configured tickers. A same-parameter
single-ticker diagnostic found 39/39 Huber nonconvergences. This follow-up is
registered **after that failure** and is exploratory, not the result of the
original fixed all-arm protocol.

Run the existing read-only estimated-PIT pipeline for 2026-01-01 through
2026-09-18, with the preceding 120 usable sessions for each outer prediction.
Retain the production frozen feature columns, Ridge return champion, Logistic
probability, BUY thresholds, and the Extra Trees return regressor settings
from the original protocol. Compare champion, Extra Trees, zero-return,
always-up, and historical-frequency controls only on ticker/session pairs
where every included arm produced a valid forecast. Report the sample and
excluded counts, MAE/RMSE, direction accuracy, 0/5/10/15/20 bp hypothetical
round-trip cost scenarios, and the paired date-bootstrap MAE interval. No
Huber result is implied. The runner must not write to the database.

The source data are estimated historical backfills, the analysis follows an
observed failure, and the comparison is therefore descriptive and selected.
Even a better metric cannot promote Extra Trees to production. The separately
registered 20-new-session forward comparison and operational review remain
required. Keep the original failed run and this new run identified by source
commit and workflow URL in the final audit record.
