---
name: finding-predictive-indicators
description: How to look for indicators that predict this system's target, and how to avoid the ways that search wastes months - the open-to-close distinction that invalidates most candidates, the availability cutoff, the requirement for backtestable history, and the measurement power needed before any verdict means anything. Use whenever adding, removing, or evaluating a predictor, or when asking another model for indicator suggestions.
---

# Finding indicators that actually predict

An ongoing problem, not a one-time task. This records what the search has cost
so far and what makes it cheaper.

## 1. Most candidates fail on the target, not on the data

The target is **open-to-close** (`close / open - 1`), not close-to-open. Almost
every plausible-sounding indicator predicts the *gap*, and the gap is already
in the opening price the strategy buys at.

"US equities rose overnight, so Japan rises" is true and useless. Before
evaluating any candidate, ask: *what does this say about the 9:00-15:30 session
specifically, given the market has already opened at a price that reflects it?*

Mechanisms that survive this test tend to be one of:

- **Information arriving during the Tokyo session.** Hong Kong and China open
  at 10:30 JST, so a US-listed China proxy's overnight move gets re-priced
  *after* Tokyo opens rather than at the open.
- **Slow diffusion.** Sector-specific news is priced abroad, while the Tokyo
  opening auction weighs index beta and USD/JPY first, leaving the
  sector-specific part to work through the day.
- **Mechanical flow with a known time.** SQ days settle on constituent *opening*
  prices, so the distortion is in the open and reverts intraday. Rebalances and
  ex-dividend dates concentrate flow at the close.

Anything whose story ends at "so the market opens higher" is not a candidate.

## 2. Three constraints, checked before any discussion

1. **Settled before the cutoff.** US cash closes ~05:00 JST and is safe. FX,
   CME futures, and commodity futures trade nearly around the clock, so their
   daily bar must be pinned to a boundary that is strictly in the past — never
   the still-forming current bar.
2. **Two to three years of free daily history.** A factor that cannot be
   backtested can never satisfy the adopt-only-if-it-improves rule, so it can
   never be adopted. This is what rules out the order book and PTS.
3. **The feature budget.** One indicator becomes eleven features. Sectors
   already carry 165-286 against a 120-session window. Adding is not free and
   is usually negative.

Verify availability by fetching, never by assuming. Index tickers are the
common trap: `^BCOM` and `^MOVE` both return history that silently stops
updating weeks earlier, because free redistribution of an index can be
withdrawn. ETFs tracking the same thing keep working.

## 2b. Rank IC, not direction accuracy, is the metric that can see anything

Direction accuracy treats 22 tickers on one morning as 22 observations when
they share a market. Rank IC ranks within the morning instead, so the common
move cancels and every ticker contributes. Measured over the same 63 sessions
and 1,386 predictions, the two metrics disagree about which predictor set is
better, and only one of them is aligned with how the system is used:

| set | MAE | direction | Rank IC | IC p |
|---|---|---|---|---|
| baseline (7 series) | **1.2283** | 0.5599 | 0.0593 | 0.042 |
| focused (12) | 1.2288 | **0.5722** | 0.1073 | **0.001** |
| extended (29) | 1.2638 | 0.5642 | **0.1130** | **0.001** |

`extended` has the worst MAE and the best ordering. Point estimates get noisier
as predictors are added while the *ranking* improves, and picking which stocks
to buy needs the ranking. Judging on MAE alone would have discarded both sets
that actually order the cross-section.

Paired against baseline: `focused` gains +0.048 IC (p = 0.033) at no cost in
MAE (p = 0.94); `extended` gains +0.054 IC (p = 0.075, not resolved) while MAE
gets significantly worse (p = 0.002). `focused` is the better buy - the same
ordering benefit, no divergence cost, and 12 series instead of 29.

Neither improvement clears a Bonferroni line across the six tests run, so treat
them as the direction to pursue rather than as settled. What *is* settled is
that `focused` and `extended` have real cross-sectional skill against zero
(p = 0.001 each), and baseline's does not survive correction.

## 3. Know the detectable effect size before running the test

Measured on this repository, 49 sessions x 22 tickers:

| | value |
|---|---|
| Predictions | 1,078 |
| Same-day intraclass correlation | 0.082 raw, 0.348 among discordant pairs |
| Effective sample after pairing | ~101 |
| **Smallest detectable effect** | **~3.1pp of direction accuracy** |

Four proposals were measured at +0.19 to +0.93pp and reported as "not
adopted". Every one of them was **below a third of the detection threshold**.
Those results do not mean the factors were useless; they mean the test could
not tell. Reporting them in the same words as a real null was a mistake.

So: **compute the detectable effect first.** If the expected improvement is
below it, the experiment answers nothing and the effort is better spent on
raising measurement power — a longer window, a cross-sectional formulation that
removes market beta, or Rank IC instead of a sign test.

## 4. Reduction is a candidate too, and usually the better one

Ridge alpha selected the grid maximum in 1,078 of 1,078 predictions, and
logistic C the grid minimum. Cross-validation is asking for more shrinkage than
the grid allows: the model does not trust the features it has. Adding to that
set is pushing against the direction the optimizer is already pointing.

Look for redundancy that is provable rather than plausible:

- **Exact linear dependence.** `2Y = 10Y - (10Y - 2Y)`. Holding all three makes
  the design matrix rank deficient; one is free to remove.
- **Composition.** BDI is a weighted average of Capesize and Panamax; holding
  all three spends 33 features on two dimensions.
- **Domination by a fresher series.** Futures trade until the cutoff while cash
  indices stopped at 05:00, so once futures work, cash indices add nothing.
- **No mechanism at all.** Gold against Japanese equity intraday returns has no
  path. "Just in case" is how the budget gets spent.

## 5. What to do before asking another model

Give it the constraints that bind, or it returns twenty gap-predictors. State
the open-to-close distinction, the cutoff, the history requirement, the feature
budget, and the measured detection threshold. Ask for a **maximum of three per
sector** and demand a causal account for each.

Ask it directly whether the premise is wrong. Two independent models both
answered that reduction and reformulation matter more than any new indicator,
which was more valuable than any suggestion either made.

Then verify every proposed symbol by fetching it before discussing whether it
belongs.

## 6. Where the real ceiling probably is

Individual daily returns decompose as `alpha + beta * market + epsilon`, and
for large caps the beta term is most of the variance. The market's intraday
move is not knowable at 08:20. A large share of what is being predicted is
structurally unpredictable from the available information set, and no indicator
fixes that.

The changes that address it are formulation changes, not data changes:
predicting relative performance instead of direction removes the beta term and
multiplies the effective sample; moving the prediction after the opening auction
makes the realized gap available as a feature. Search for indicators inside
whichever formulation is in force, but do not expect indicators to substitute
for it.

## 7. Sector pooling was measured and lost. Do not re-propose it

It was the obvious move — three to five tickers per sector, so three to five
times the training rows, without adding a series. On 63 sessions and 1,386
paired predictions it made everything worse:

| | per ticker | pooled | p |
|---|---|---|---|
| Divergence from outcome (MAE, pp) | **1.2283** | 1.2692 | 0.00006 |
| Direction accuracy | **0.5599** | 0.5123 | 0.0006 |
| Rank IC | **+0.0593** | −0.0002 | 0.13 |

−4.76pp of direction accuracy is larger than the ~3.1pp this window resolves,
so this is a detected loss and not another underpowered null.

It was re-run on `extended`, where pooling should do best - 76 predictors
against 120 rows is where extra rows are worth most - and it lost there too:
divergence +0.049pp (p = 0.0009), direction −2.74pp, Rank IC −0.068. The first
measurement had been made on the set where pooling had least to offer, which
was worth correcting before generalising from it.

A control ran alongside it, because a pool can only use the columns every member
shares and therefore drops ticker-specific ones at the same moment it shares the
fit. Fitting per ticker *on the same reduced columns* reproduced the per-ticker
arm exactly on baseline — 0 discordant predictions — and on `extended`, where 18
ADR columns really are dropped, it moved only 14 of 1,386 predictions and was
very slightly *better* on all three metrics. So **the shared fit accounts for the
entire loss**, and separately: the ADR columns are earning nothing and are the
first thing to cut.

The lesson generalizes past pooling: more rows only help when the added rows come
from the same relationship. Tickers in a sector do not share coefficients closely
enough for that, and forcing them to costs more in bias than the extra rows
return in variance. Ridge already selects the maximum alpha on every prediction,
which says the fit is short of signal, not short of rows.

Keep the control habit. Whenever a change moves two things at once, run the
middle arm; without it this result would have been blamed on the lost columns.
