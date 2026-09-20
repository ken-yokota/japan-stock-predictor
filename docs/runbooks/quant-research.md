# Quant research protocol

Freeze the champion commit, config hash, actual input identities, transformations,
training window and cost rule before running a challenger. Research mirrors that
use different symbols, time boundaries or columns are not production baselines.

For every outer chronological fold, perform screening, clustering, coefficient
stability, preprocessing and hyperparameter selection inside training only.
Inner validation must itself fit its selector and scaler on the inner training
slice. Prefer 3–8 columns, cap candidates at floor(training_rows / 10), and
freeze selected columns for at least 20 sessions. A daily coefficient fit does
not authorize daily selection. Never relabel inspected data as sealed holdout.

Assess MAE/RMSE, Pearson/Spearman, direction, daily cross-sectional rank IC,
Brier/log loss/ECE/reliability, interval coverage and trading performance.
Pair common dates and report missing prediction coverage. Bootstrap whole dates
so stocks on the same date are not treated as independent. Include zero-return,
always-up and historical-frequency baselines, and 0/5/10/15/20bp round-trip costs.
Fewer than 20 trades is LOW SAMPLE. No significant difference is not equivalence.

Naive sector pooling was a measured negative control; preserve stock identity
when testing hierarchical shrinkage. More features worsened the 20/54/162/216
column study. These old results guide hypotheses, not current promotion claims.
US equities close at 04:00 JST in EDT and 05:00 in EST; use exchange calendars.
Maruti normal close is 19:00 JST. Its full daily return is not an isolated
post-Tokyo-close return. BDRY is a futures ETF, not the cash Baltic Dry Index;
BDI includes Supramax as well as Capesize/Panamax. Henry Hub is not JKM LNG.

Calibrators see only past OOF/live OOS probabilities whose outcomes were available
before the new cutoff. Separate feature versions and preserve uncalibrated raw
probabilities. Do not silently call a fallback calibrated. News NLP requires
publication/availability/retrieval timestamps, source URL/hash, timezone and
license. Derived spreads inherit the latest input timestamp and matched contracts.

Register feature/model/calibration/threshold hashes before the first new forward
session. Changes restart registration. Review after at least 20 new sessions;
do not promote during development because one selected PF looks attractive.
