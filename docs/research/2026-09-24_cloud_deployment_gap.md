# Cloud deployment gap observed 2026-09-24 JST

At 2026-09-24 01:00 JST, a fresh browser session at the public Streamlit
Community Cloud app opened History → 全期間. After using the page's DB refresh,
the page still displayed **950 predictions**. The same value appeared in a
separate fresh browser session about four hours earlier. The UI rendered, but
its population is inconsistent with the merged live-history query.

The [read-only database integrity check](2026-09-23_live_history_integrity.md)
found that the former joins produced 950 rows from 638 prediction IDs. The
query merged in PR #4 selects one final outcome/trade revision and requires a
READY MORNING set published before its 08:30 cutoff. It returned **528
predictions across 24 dates** in that check. A read-only GitHub Actions run
independently recomputed the same 528-row cohort for the
[corrected model comparison](2026-09-23_live_model_comparison_corrected.md).
No new prediction date had been added between that run and this UI check.

PRs #4–#8 were merged into `main`; at the UI check, `main` was
`550008f268c9032a0d0caf77986244422d0f901c`. GitHub recorded 200 responses
from the configured Streamlit push webhook for the merges. A 200 webhook
response confirms receipt of the event, **not** which commit the Cloud app is
executing. The available Streamlit management browser session was not signed
in, so the app's configured branch, deployment log, running commit and reboot
state could not be inspected. The 950-row match is evidence of an old query,
but does not identify the Cloud deployment's exact commit.

Before accepting this deployment, inspect the app's configured repository and
branch in Streamlit Community Cloud, verify the latest successful build and
running commit, and restart or redeploy the app if its runtime is stale. Then
open a fresh History session, refresh its DB cache, and confirm 528 predictions
for the same database snapshot. Recheck Backtest and Sector Analysis because
their live comparisons depend on the same pre-cutoff cohort. Until this passes,
the public History metrics must not be cited as the corrected live result.

## Later check on 2026-09-24

At 11:21 JST, a new public browser session opened History → 全期間 and used
`DB表示を更新`. The page showed **972 all-period predictions** before and after
refresh: the previous 950 plus the 22 predictions displayed for 9/24. The
[read-only recomputation](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/35947062727)
completed at 11:25 JST against the current database and found **660 source
rows, 528 eligible rows, and 24 settled sessions** after its published-before-
cutoff, READY MORNING, latest-ticker/date and outcome rules. The new 9/24
session was not yet settled, so the eligible count stayed 528. The UI total
and the recomputation use different outcome filters, but the difference cannot
be explained by the 22 newly published predictions alone. This reinforces
the deployment/query mismatch; it still does not identify the running commit.

Backtest and Sector Analysis both rendered and showed the 9/24 READY set with
11 BUY candidates. They did not verify the corrected historical cohort. On
this unsettled morning they displayed a `0/11` buy-hit label; PR #15 changed
the source to say `未確定` until an outcome settles, but its Cloud deployment
has not been verified. The configured Cloud branch, build log and runtime
commit still require a signed-in management check before deployment can pass.
