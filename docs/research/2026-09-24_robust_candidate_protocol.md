# Fixed return challenger comparison protocol / 2026-09-24

This protocol was written before the new 22-ticker result was measured. The
Huber and Extra Trees regressors are research-only return challengers. They do
not change the production champion, its probability model, feature registry,
BUY thresholds, or any saved forecast. A lower historical error alone cannot
promote a model. Promotion still requires a separately registered, frozen
20-new-session forward comparison and operational review.

## Prespecified design

- Use the existing point-in-time backtest builder and each ticker's frozen
  production feature columns. Query the hosted database in a read-only
  transaction. Never write predictions or trades during this study.
- Request 2026-01-01 through 2026-09-18 inclusive. Fit on the preceding 120
  usable sessions for each outer prediction. Do not tune a feature set or
  threshold using the scored session. Skip the older nested compact arm in this
  return-model study.
- The champion is the existing Ridge return forecast plus Logistic probability.
  Challengers replace only its return forecast, using the same columns and
  training rows. Both reuse the *same scored-session champion probability*.
  The Huber pipeline fits a training-only median imputer, standard scaler and
  `HuberRegressor(epsilon=1.35, alpha=0.01, max_iter=1000)`. Extra Trees fits
  a training-only median imputer and
  `ExtraTreesRegressor(n_estimators=100, max_depth=4, min_samples_leaf=10,
  max_features="sqrt", random_state=42)`.
- A missing frozen input at the scored cutoff produces no forecast for all
  three arms. Fit failures are recorded without substituting another model.
  The aggregate compares only identical ticker/session pairs with valid
  forecasts from every arm, and reports excluded and failed counts.
- Apply the production return and probability BUY thresholds without changing
  them for each arm. Report point-forecast MAE, RMSE, direction accuracy,
  probability metrics, trade counts, expectancy, profit factor and drawdown at
  hypothetical fixed round-trip costs of 0, 5, 10, 15 and 20 basis points.
  Report candidate-minus-champion paired MAE and its 95% date-bootstrap
  interval, resampling whole dates to retain within-day stock dependence.

The historical source is labelled `ESTIMATED_BACKFILL`: publication and
first-observed times in backfilled EOD data are estimates, and this is neither
an exact replay of historical production decisions nor an unused sealed
holdout. The post-selection comparison is descriptive. Transaction costs are
scenarios, not broker fills. Statistical intervals on this inspected history
cannot establish future profitability.

The manually dispatched `robust-challenger-oos.yml` workflow produces
per-ticker JSON only in the ephemeral runner and uploads one aggregate JSON.
Its source commit, run URL, measured sample sizes, exclusions, results and any
failure will be recorded after the run. No result or passing status is claimed
in this preregistration.
