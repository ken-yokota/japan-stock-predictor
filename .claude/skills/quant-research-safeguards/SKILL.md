---
name: quant-research-safeguards
description: Everything this repository learned the hard way about measuring a trading model - which metric can see anything at this sample size, the detection threshold a result has to clear before it means something, the look-ahead traps, the indicator candidates and reductions already tested and their verdicts, and the operational traps in Yahoo, Postgres and the dashboard. Use before proposing, adding, removing or evaluating any predictor, before interpreting any backtest number, and before claiming a pipeline change is verified.
---

# Quant research safeguards

Findings measured on this repository. Each one exists because ignoring it
produced a wrong answer here. Nothing in this file is general advice; the
numbers are from this codebase and this universe of 22 tickers.

## 1. Rank IC is the primary metric. It cannot be the only one

Direction accuracy treats 22 tickers on one morning as 22 observations when
they share a market. Measured here: 1,078 predictions, same-day intraclass
correlation 0.082, **effective sample ~101**, smallest detectable difference
**~3.1pp of direction accuracy**.

Rank IC ranks within each morning instead, so the common market move cancels
and every ticker contributes. Use `research/metrics.py`.

**But on 2026-08-16 four predictor sets produced rank ICs no test could tell
apart — 0.107 to 0.113, every paired difference far inside its detection floor
— while their threshold-rule trading results ran from +0.44 to −0.01.** An
ordering that is right about the middle of the cross-section and wrong about
its top scores well and earns nothing. That is not hypothetical here.

So: **a set is preferred only when the ranking metric and the trading record
agree, and the trading record is tested, never compared.** Profit factor 2.51
against 1.77 looked decisive and did not survive a paired test (Wilcoxon
p = 0.0173 against a 0.0167 threshold fixed before the run).

| Metric | Sample | Judge on it? |
|---|---|---|
| Rank IC (daily cross-section) | 63 days | **Yes — primary** |
| Direction accuracy | 1,386 predictions | Yes, secondary |
| MAE / RMSE | 1,386 predictions | Yes, but see below |
| Win rate, profit factor, P&L | 49-107 trades | **Only as a paired test** |

MAE and rank IC **disagree about which set is better**: `extended` has the
worst divergence and the best ordering. Judging on divergence alone would have
discarded both sets that actually order the cross-section.

## 2. Compute the detectable effect before running the test

Four indicator proposals were measured at **+0.19 to +0.93pp** against a
**3.1pp** floor and reported as "not adopted". The truthful reading is that the
test could not tell. `RankICSummary` carries `detectable_ic` for this reason —
compare to it before interpreting any null.

`INCONCLUSIVE` is not `REJECTED`, and **"no significant difference" is not
"equivalent"**. The second needs an interval narrow enough to exclude anything
worth acting on, and only the second justifies preferring the smaller option
for operational reasons.

Fix the significance threshold and the multiple-testing correction **before**
seeing results, and do not move them afterwards.

## 3. Verdicts already reached. Do not re-propose these

| Proposal | Verdict | Evidence |
|---|---|---|
| **Sector pooling** | **REJECTED** | divergence +0.041pp (p=6e-05) on baseline, +0.049pp (p=9e-04) on extended; direction −4.76pp (p=6e-04) |
| **Relative (demeaned) target** | INCONCLUSIVE | every metric inside its floor |
| **`focused` 12 vs production** | REJECTED as an adoption | neither better nor equivalent; trading t p=0.07 |
| **ADR columns (18)** | contribute nothing | moved 14 of 1,386 predictions; slightly better removed |
| **Top-k selection** | worse than the threshold rule | top3 and top5 lose on all four sets |
| Semiconductor ETF | rejected | P&L +9,035 JPY and win rate +3.1pp, direction **−1.35pp**, sign test p=0.33 |

Pooling generalises: **extra rows only help when they come from the same
relationship.** Ridge selecting the maximum alpha on 1,078 of 1,078 predictions
was already saying the fit is short of signal, not short of rows.

The threshold rule's value is in **the days it declines to trade** — top-k
trades every session and loses.

## 4. The target is open-to-close, which kills most candidates

The target is `close / open - 1`, not close-to-open. Almost every plausible
indicator predicts the **gap**, and the gap is already in the opening price the
strategy buys at. "US equities rose overnight, so Japan rises" is true and
useless.

Mechanisms that survive: information arriving **during** the Tokyo session
(Hong Kong opens 10:30 JST); slow diffusion of sector news the opening auction
underweights; mechanical flow with a known time (SQ settles on opening prices).

Anything whose story ends at "so the market opens higher" is not a candidate.

Three hard constraints: settled before the 08:30 cutoff; two to three years of
free daily history (or it can never satisfy the adopt-only-if-improved rule);
and the feature budget — one indicator becomes eleven features against a
120-session window.

## 5. Reduction is a candidate, and usually the better one

Look for redundancy that is **provable**, not plausible. All of these exist in
`config/indicators.yaml` today:

- **Exact linear dependence**: `us_2y_yield` + `us_10y_yield` +
  `us_10y_minus_2y_spread`. Holding all three makes the design matrix rank
  deficient.
- **Cash dominated by its own futures**: `sp500`/`nasdaq100` stop at 05:00 JST
  while `ES=F`/`NQ=F` trade to the cutoff.
- **Composition**: BDI is a weighted average of Capesize and Panamax.
- **Duplicate exposure**: `fxi` and `mchi` are the same China large-cap trade.
- **No mechanism**: gold against Japanese equity intraday returns.

Redundancy being real does not mean removing it helps. Ablate from production
as the baseline, one group at a time, and judge on section 1's rule. Removing
five of these plus ADR (27→22) measured better on every trading metric and
**still failed the pre-registered threshold**.

## 6. Look-ahead is the default failure

`features/builder.py` computes `open_close_return = close / open - 1`, which
**is** the target. `research/dataset.py` shifts every price feature by one
session; verify that shift before trusting any research number.

Store three timestamps per raw row — when the value became public, when the
system first observed it, when it was retrieved. One is never enough. Reject at
**write** time, not read time. Expect the guard to fire on backdated runs and
treat that as success.

Ridge never sets a coefficient to exactly zero, so a "newly used indicator"
check based on leaving zero **cannot fire** — it found 0 events across 22
tickers and 27 sessions. Ask instead when a feature first entered the top N by
absolute weight.

## 7. Enumerate the failure modes, then exercise them

Reading the code is not the check. On a system with 234 passing tests and green
CI, exercising failures found: the evening summary matched `state == "INVALID"`
while artifacts were stamped `INVALID_FOR_ADOPTION`, so every retired result
was mailed with its p-values intact; the morning mail raised `ValueError` when
no prediction existed, so the three mornings most worth hearing about produced
no mail; and the fallback written to fix that died with `AttributeError`
because it built an SMTP client by hand instead of using the factory.

Cover at minimum: empty inputs, a missing upstream row, a non-business day, a
date with no data, an absent credential, and **the same job running twice** —
idempotency keys must be per-date, not per-invocation.

## 8. Test the production path, by the production procedure

Two mornings were lost to checks that skipped a step: the pre-flight ran with
`--skip-ingestion`, so the fetch that timed out was never exercised; then it ran
read-only with a rollback, so the write path that took six hours was never
exercised either. Both times the report said "verified".

Same command, same flags, same database, same data volume. **Measure wall-clock
against the timeout** and report the margin — 34 minutes against a 60-minute
limit is a job that fails on a slow day. If a flag was dropped to make the test
cheap, name it or do not claim coverage.

## 9. Operational traps measured here

**Yahoo blocks below Python.** `yfinance` reaches `curl_cffi`, which blocks
inside C. A `SIGALRM` set to 20s did **not** interrupt a `^GSPC` request that
then sat 4m40s on 1.1s of CPU. Only a parent holding SIGKILL can end it — use
`research/isolated_fetch.py`, never a bare `download_daily` in a loop.

**Index tickers get withdrawn, ETFs keep working.** `^GSPC` timed out at 90s,
180s and 300s while `^DJI` answered in 3.1s between attempts — the symbol, not
a rate limit. Same shape as `^BCOM`, `^MOVE`, `^SOX`. Use SPY, SOXX.

**Rate limits are about origin.** 1.4s per series from home, 25-35s from GitHub
Actions — 20x, same code, same request count. Moving a slow fetch to Actions
makes it worse. Shortening the date range does **not** reduce request count;
one series is one call. The only lever is skipping series already cached
(`research/cache_state.py`).

**A near-zero CPU-time-to-elapsed ratio means I/O, not work.** This diagnosed
three separate stalls in one day: a sandboxed process with no network, the
Yahoo hang, and a virtualenv living on iCloud that made a 45-second test suite
take 23 minutes.

**Killed processes leave lying state.** A cancelled run leaves `status=RUNNING`
forever. Reconcile before the next scheduled run.

**Verify exit codes, not pipeline output.** `psql ... | head` reports `head`'s
status. A restore was declared successful here with every index, primary key
and foreign key missing.

**Hosted Postgres may hand you an empty `search_path`.** Set it on connect, not
as a libpq startup parameter — poolers reject unknown startup parameters.

## 10. Publish atomically; keep the read path unable to compute

All symbols as one set or none. A half-published day shown as "today's
prediction" is worse than no prediction. Symbols that could not be built get
`INSUFFICIENT_DATA`; never a stale or interpolated value.

A dashboard that *can* fetch or train eventually will. Assert on the import
graph, and prove in a subprocess that importing the UI never loads the training
stack. Sharing pure arithmetic with production is fine and preferable.

Streamlit: `cache_data` pickles its return value and `cache_resource` does not.
Anything holding a connection or a non-picklable object needs `cache_resource`.

## 11. Config is the truth, but the *saved* config is the truth

Thresholds, universe, costs and window belong in YAML. The rule shown to the
operator must be the one stored on the saved signals, not whatever the config
says now — an edited config that has not been through a run is not in force.
Show both and flag the mismatch.

## 12. Never handle the operator's secrets

Values go into `.env`, the CI secret store, and the host's secret store, typed
by the operator. If one is pasted into a conversation, say plainly it must be
revoked now, do not use it, do not write it anywhere. Report the failing step,
the status column and the exception class instead; a connection string is never
needed to debug.

## 13. Preserve results where a rerun cannot replace them

`docs/research/` holds one immutable file per measurement session. A rerun that
disagrees gets its **own** file — overwriting destroys the only evidence the
two differed. Record the commit, window, sessions, paired count, everything
held constant, and the limits known at the time.
