# Published live model comparison (2026-09-23)

This is a dated correction to the [2026-09-19 live comparison](2026-09-19_model_comparison.md),
not a sealed holdout or a new model-selection exercise. The read-only hosted
database audit ran in [GitHub Actions run 35853706718](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35853706718)
at about 11:19 UTC on 2026-09-23. Its complete aggregate output is
[2026-09-23_live_model_metrics_corrected.json](2026-09-23_live_model_metrics_corrected.json)
(SHA-256 `28e9780daf2a9a0e4ed5c357ec674cb96d1d4b7770ceb4212eabd617e50ceeed`).
Individual prediction rows were read in a PostgreSQL read-only transaction and
discarded with the temporary workspace; only aggregate metrics were uploaded.

The source contained **638** predictions from READY sets. Requiring a MORNING
run, successful settled prediction, and `published_at <= cutoff_at`, then
retaining the last timely decision per ticker and date, left **528 predictions
across 24 dates**. The other 110 source rows were excluded. This agrees with
the independently checked [History cohort](2026-09-23_live_history_integrity.md).
The older comparison's 550 predictions across 25 dates admitted one additional
22-stock session because it checked generation before market open rather than
publication by the 08:30 JST cutoff. The original report remains a dated
record, but its figures do not describe this stricter cohort.

Champion results, kept separate by the saved configuration hash:

| Config prefix | Predictions / days | BUY trades | MAE (return points) | Direction | 0 bp expectancy | 10 bp expectancy | 20 bp expectancy | 10 bp date-bootstrap 95% CI |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `14c33a4360` | 264 / 12 | 55 | 1.33 pp | 51.9% | −0.10% | −0.20% | −0.30% | [−1.20%, +0.54%] |
| `5267622235` | 66 / 3 | 25 | 1.53 pp | 53.0% | +0.95% | +0.85% | +0.75% | [−0.55%, +2.47%] |
| `6e89aa42ec` | 88 / 4 | 35 | 1.56 pp | 53.4% | −0.08% | −0.18% | −0.28% | [−2.36%, +0.86%] |
| `76265fa4e5` | 110 / 5 | 40 | 1.33 pp | 40.9% | −0.09% | −0.19% | −0.29% | [−0.95%, +0.44%] |

Expectancy is mean simulated open-to-close return per selected BUY, minus the
stated fixed round-trip basis-point cost; it is not a broker-verified gain.
The bootstrap resamples trading dates, preserving the within-day stock
dependence. Every displayed 10 bp interval crosses zero, and each configuration
has only 3–12 observed dates. For the `14c33a4360` configuration, removing the
late-published day changed the old 286-prediction/13-day MAE and direction
figures from 1.29 pp and 53.1% to 1.33 pp and 51.9% on 264 predictions/12 days.
The other three configuration cohorts did not change.

The JSON also contains per-ticker metrics, saved alternative arms, 0/5/10/15/20
bp cost sensitivity, probability calibration and reliability bins. Those arm
records cover at most five dates per configuration except the champion's first
configuration. Some arms make no trades under the frozen champion thresholds:
a lower MAE by itself is therefore not evidence of better trading results.
Publication time proves neither email receipt nor an executable entry price.
No probability calibrator, model, feature or BUY threshold is promoted from
this observed history. Same-condition chronological OOS comparisons for the
unimplemented Huber, Extra Trees, robust, quantile and hierarchical candidates,
and the registered 20-session forward study, remain separate tasks.
