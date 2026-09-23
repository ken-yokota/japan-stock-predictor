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
