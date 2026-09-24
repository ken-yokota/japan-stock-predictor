# Registered forward observation, day 1 / 2026-09-24

The [forward registration](2026-09-20_forward_registration.json) names 9/24
as the first of at least 20 new JPX sessions, with the earliest twentieth
session on 10/22. On 9/24, all six registered configuration files still
matched their registered SHA-256 values. That confirms the configuration
files, not the complete running Cloud commit.

The [morning prediction workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35942447952)
completed successfully and reported an existing READY prediction set with
`reused=true`; the workflow's empty newly successful ticker list therefore
does not mean zero saved predictions. A separate [read-only database
inspection](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35946561064)
found feature sets for all 22 enabled tickers: 22 CLEAN, zero DEGRADED, and
11 BUY candidates. The public Backtest and Sector pages also displayed the
9/24 READY set and 11 BUY candidates. The [morning email
workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35943979539)
completed its send step with a provider success result and message ID; inbox
delivery has not been independently confirmed.

At the 11:28 JST observation, the 9/24 market session had not closed and its
BUY outcomes were unsettled. This is an operational first-day observation,
not a scored forward result or evidence of profitability. The complete
20-session evaluation and the Cloud running-version check remain open.

## Close settlement observed at 16:22 JST

The scheduled close-update runs had not appeared by 16:14 JST, so the existing
retryable workflow was [dispatched with an explicit 9/24 prediction date](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35968542157).
It ran source commit `f822ed8eff7a2909aa32ccf9e160804e85ccaa0c` and returned
`SUCCESS`: 22 finalized predictions, zero pending, zero corrections and zero
failed tickers. A separate read-only query against the latest READY MORNING set
and each prediction's latest outcome confirmed 22 successful predictions, 22
FINAL outcomes, 11 BUY decisions and 11 FINAL simulated trades.

All 11 BUY stocks had negative observed open-to-close returns; the same-day
BUY direction count was **0/11**. Across all 22 stocks, 21 fell and one rose.
The model's same-day direction count was 4/22 and its return MAE was
0.0254729, versus 0.0186578 for a zero-return point forecast on these same
22 stocks. The 11 BUY paper trades totaled **-138,950 JPY**. The frozen
paper-trading configuration assigns zero commission and slippage, so this
reported net amount equals gross P/L and is not an estimate of executable
after-cost performance. No broker orders were placed.

This is one highly negative market session in a 20-session registration. It
does not establish a persistent model or strategy effect, and no predictive
settings are changed in response. The scheduled 17:00 JST result email and
its inbox delivery require separate verification.
