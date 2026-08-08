---
name: quant-research-safeguards
description: Guardrails and working methods for building or changing a market-prediction system - look-ahead prevention, honest evaluation of a proposed feature, small-sample discipline, and the operational traps (rate limits, timeouts, stale run records) found while running this repository. Use when adding indicators, changing the model or thresholds, interpreting backtest numbers, or debugging a scheduled pipeline.
---

# Quant research safeguards

Methods proven on this repository. Each rule exists because ignoring it produced
a wrong answer here, not because it sounds prudent.

## 1. Look-ahead is the default failure, not an edge case

Any feature computed from the same row as the target is suspect. In this repo
`features/builder.py` computes `open_close_return = close / open - 1`, which
**is** the prediction target. Feeding row `t`'s features to predict row `t`
hands the model the answer.

Before trusting any backtest, answer: *for each feature, what is the latest
timestamp of data that went into it, and is it before the decision cutoff?*

Enforcement that works:

- Store three separate timestamps per raw row: when the value became public,
  when the system first observed it, when it was retrieved. One timestamp is
  never enough — "published yesterday 18:00 but we only got it this morning" is
  exactly the case that silently leaks.
- Reject at write time, not at read time. A guard that raises on persist cannot
  be forgotten by a new caller.
- Expect the guard to fire on backdated runs and treat that as success. Running
  a morning pipeline for a past date **should** fail: the data was retrieved
  after that date's cutoff.

## 2. Judge a proposed feature on the well-powered metric

A user proposal ("semiconductors must matter") deserves a measurement, not
agreement and not dismissal.

Run the same window with and without it, then compare on the metric with the
largest sample:

| Metric | Sample here | Use for judging? |
|---|---|---|
| Direction accuracy | 594 predictions | Yes |
| MAE / RMSE | 594 predictions | Yes |
| Win rate, profit factor, P&L | 13-15 trades | **No** |

On this repo, adding a semiconductor ETF moved trade P&L +9,035 JPY and win rate
+3.1pp — both looked like a win. Direction accuracy went **down** 1.35pp across
594 predictions, and a paired sign test over the same predictions gave p = 0.33.
The feature was rejected.

Pair the predictions and run a sign test. "Which one won more often on the same
inputs" is far more informative than two aggregate numbers.

## 3. Small samples: say the number, then refuse to conclude

Below ~20 trades, report the count first and state plainly that no conclusion
follows. Check concentration: on this repo 60% of profit came from one trade of
thirteen; removing it cut the total from +39,146 to +16,116 JPY.

Always compute the unfiltered control — "what if I had bought everything?" If
the filtered rule does not beat buying everything on the same window, the filter
destroyed value. Both directions showed up here on different windows, which is
itself evidence the sample is too small.

## 4. Regularization determines what "unused feature" can mean

Ridge (L2) shrinks coefficients but never sets them to exactly zero. Lasso and
ElasticNet (L1) do.

A "which indicators newly appeared" check based on a coefficient leaving zero
**cannot fire under Ridge**. Implemented here first, it found 0 events across 22
tickers and 27 sessions. The version that works under Ridge asks when a feature
first entered the top N by absolute weight.

Match the detection rule to the estimator actually in production.

## 5. Config-driven, and the config must be the displayed truth

Thresholds, universe, costs, and window belong in YAML, not code. But the rule
shown to a user must be the one stored on the saved signals, not whatever the
config says right now — an edited config that has not been through a run is not
in force. Show both and flag the mismatch.

## 6. Operational traps found here

**Rate limits are about origin, not volume.** Yahoo served this repo at 1.4 s
per series from a home connection and 25-35 s per series from GitHub Actions —
20x, same code, same request count. Waiting does not fix a datacenter-IP
throttle. Measure both origins before blaming request volume.

Corollary: shortening the requested date range does **not** reduce request
count. One series is one HTTP call whether it asks for 5 days or 550. The only
lever is skipping the series entirely when storage already covers it.

**Timeouts must clear observed duration with room.** A 35-minute job timeout
against a 34-minute fetch cancels the run. Worse, check the *ordering* of
scheduled jobs: a prediction starting 08:20 and taking 35 minutes finishes after
the 08:45 email that depends on it. Moving the start earlier fixed it at no
information cost, because the cutoff stayed fixed and no relevant data arrives
in the gap.

**Killed processes leave lying state.** A cancelled run leaves `status=RUNNING`
forever. Reconcile stale rows and delete half-built publication records before
the next scheduled run, or it will reuse or trip over them.

**Verify exit codes, not pipeline output.** `psql ... | head` reports `head`'s
status. A restore was declared successful here while every index, primary key,
and foreign key was missing; only counting constraints afterwards caught it.
After any bulk load, compare structure counts, not just row counts.

**Hosted Postgres may hand you an empty `search_path`.** Unqualified names then
resolve nowhere and DDL fails with "no schema has been selected to create in".
Set it on connect, not as a libpq `options` startup parameter — poolers reject
unknown startup parameters outright.

## 7. Publish atomically, fail closed

Publish all symbols as one set or none. A half-published day shown as "today's
prediction" is worse than no prediction. Symbols that could not be built get an
explicit `INSUFFICIENT_DATA` status; never fill with a stale or interpolated
value.

## 8. Keep the read path unable to compute

A dashboard that can fetch or train will eventually do so at the wrong moment.
Enforce it mechanically: assert on the import graph, and additionally prove in a
subprocess that importing the UI never loads the training stack. Pure arithmetic
shared with production is fine and preferable — reimplementing cost logic in the
UI lets displayed numbers drift from the strategy.

## 9. Never handle the user's secrets

Values go into `.env`, the CI secret store, and the host's secret store — typed
by the user, never pasted into a conversation. If one is pasted, say plainly
that it must be revoked now, do not use it, and do not write it anywhere.

Report symptoms instead: the failing step name, the status column, the error
class. Connection strings are never needed to debug.

Watch for shell-hostile values: a connection string containing `&` breaks
`source .env` unless quoted.
