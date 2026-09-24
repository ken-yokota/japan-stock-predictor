# Extra Trees post-failure OOS result / 2026-09-24

The [read-only workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35947315128)
completed successfully from source commit `97a25bd69f04eee85f48a2672d8685e5a368db83`.
It followed the [post-failure protocol](2026-09-24_extra_trees_postfailure_protocol.md), which was registered
**after** the fixed Huber comparison failed. The result is
`RESEARCH_ONLY_NO_PROMOTION`: it is an exploratory, selected comparison on estimated
historical backfills, neither live nor sealed point-in-time evidence. It did not write
to the production database or change the production model. The workflow artifact is
`robust-challenger-oos`; the extracted summary JSON has SHA-256
`931e5f6a7cc0b5a8f0e0e02f979bd71f3244488c05a162c252d93ba3568ef8cd`.

The requested window was 2026-01-01 through 2026-09-18. Across 22 tickers, all five
included arms had 1,036 shared ticker/session pairs on 49 sessions. Each arm recorded
174 `NO_PREDICTION_MISSING` statuses among 1,210 potential pairs; no otherwise valid
prediction was dropped from the common cohort. The fixed BUY rule required predicted
return at least 0.003 and probability up at least 0.625. Extra Trees reused the
champion Logistic probability, so their probability scores are identical. Hypothetical
costs below are round trip.

| Return model or control | MAE | RMSE | Direction accuracy | Pearson return correlation |
| --- | ---: | ---: | ---: | ---: |
| Production champion replay | 0.0137545 | 0.0174318 | 50.58% | 0.0303 |
| Extra Trees | 0.0127215 | 0.0159303 | 49.32% | -0.0138 |
| Zero return | 0.0125782 | 0.0157159 | 44.31% | Undefined |
| Historical frequency | 0.0126564 | 0.0158854 | 48.65% | 0.0131 |
| Always up | 0.0153541 | 0.0191160 | 55.69% | -0.0436 |

Extra Trees minus champion paired MAE was -0.00103297, with a date-bootstrap 95%
interval of [-0.00163389, -0.00048150]. On this selected historical cohort it improved
the champion's point-forecast error, but its MAE remained worse than the zero-return
and historical-frequency controls. Its direction accuracy and return correlation were
also worse than the champion's. The bootstrap interval describes this cohort and does
not undo the post-selection or estimated-backfill limitations.

| Return model | Trades | 10 bp net expectancy / trade | 10 bp profit factor | 20 bp net expectancy / trade | 20 bp profit factor | 20 bp date-bootstrap 95% expectancy interval |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Production champion replay | 196 | 0.00184179 | 1.30125 | 0.00084179 | 1.12777 | [-0.00360365, 0.00519170] |
| Extra Trees | 85 | 0.00082297 | 1.11912 | -0.00017703 | 0.97616 | [-0.00683480, 0.00680215] |

Both 20 bp expectancy intervals include zero; Extra Trees' point estimate is negative
at that cost. At 0/5/10/15/20 bp, Extra Trees expectancy per trade declines from
0.00182297 to 0.00132297, 0.00082297, 0.00032297, and -0.00017703. The zero-return and
historical-frequency controls triggered no BUY trades under the fixed positive return
threshold, so their smaller MAE is a point-forecast comparison, not a traded-strategy
result. These findings provide no basis to promote Extra Trees. The registered
20-new-session forward comparison, including data quality, costs and operational
behavior, remains pending.
