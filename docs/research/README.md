# Research records

One file per measurement session, named by the date it was recorded. **These
are never edited to reflect a later run.** A rerun that disagrees gets its own
file, and the disagreement is the finding; overwriting the earlier number would
destroy the only evidence that the two differed.

Each record carries what it takes to repeat the run and to know what it does
not cover: the commit, the window and history start, the sessions and paired
prediction count, the feature set, everything held constant, and the limits
that were known at the time.

## Reading a status

| Status | Means |
|---|---|
| `CONFIRMED` | Measured, significant after multiple-testing correction |
| `STRONG EVIDENCE` | Consistent across metrics, not significant after correction |
| `INCONCLUSIVE` | The window could not resolve a difference this size |
| `REJECTED` | A degradation was detected, not merely suspected |

`INCONCLUSIVE` is not `REJECTED`. Four indicator proposals were once reported
as "not adopted" when the truth was that a test with a ~3.1pp detection floor
had measured effects of +0.19 to +0.93pp. Keeping the two apart is the reason
these files record the detectable effect alongside every p-value.

Likewise "no significant difference" is not "equivalent". The first is an
interval containing zero; the second needs an interval narrow enough to exclude
anything worth acting on, and only the second justifies choosing the smaller
option for operational reasons.

## Records

| File | Covers |
|---|---|
| `2026-08-15-formulation-experiments.json` | Sector pooling (rejected), relative target (inconclusive), feature-set comparison, ADR contribution |
| `2026-08-16-production-vs-candidates.json` | Production's own list against three candidates, with trading outcomes and paired tests |

## The finding that changes how these are read

On 2026-08-16 four predictor sets produced rank ICs that no test could tell
apart - 0.107 to 0.113, every paired difference far inside its detection floor -
while their threshold-rule results ran from +0.44 to −0.01. Rank IC is still the
primary metric, because it is the one with the power to see anything at this
sample size. But it cannot be the only one: an ordering that is right about the
middle of the cross-section and wrong about its top scores well and earns
nothing, and that is not a hypothetical here.

So a set is preferred only when the ranking metric and the trading record agree,
and the trading record is *tested*, never compared. Profit factor 2.51 against
1.77 looked decisive and did not survive a paired test.
