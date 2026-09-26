# Registered forward observation, day 2 / 2026-09-25

The [forward registration](2026-09-20_forward_registration.json) began on
2026-09-24 and requires at least 20 new JPX sessions. This entry combines
workflow observations with a fresh read-only query of
the saved prediction, outcome, paper-trade, and delivery rows.

The [morning prediction workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/36082016368)
finished `READY` on main commit `53e75b9476b9bc18898909e1e0efe3c3dbd50b3b`.
It reported `reused=true`, so its empty newly successful ticker list is not a
count of saved predictions. The [morning email workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/36084249410)
reported `SUCCESS`; recipient inbox delivery remains unverified.

The final delayed [scheduled close workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/36136627184)
completed at 12:50 UTC with `SUCCESS`: 22 finalized, zero pending, zero
corrections, and zero failed tickers. Two preceding scheduled close workflows
and a manually dispatched close workflow also concluded successfully. The
[evening summary workflow](https://github.com/ken-yokota/japan-stock-predictor/actions/runs/36116224118)
concluded successfully. A separate read-only production DB query confirmed
22 successful saved predictions, 3 BUY decisions, 22 latest FINAL outcomes, zero missing outcomes,
maximum outcome version 1, and 3 FINAL paper trades. All 3 BUY stocks rose
from open to close, for frozen zero-cost paper P/L of **+28,800 JPY**. The
return MAE across all 22 stocks was **0.0095143**, versus **0.0076497** for
a zero-return forecast on the same stocks. The previous day’s paper P/L was
-138,950 JPY, so the first two sessions sum to **-110,150 JPY** under the
registered zero-cost paper assumptions. These two mixed market days cannot
establish predictive or executable trading performance. The production
`email_logs` row for the 9/25 daily summary is `SENT` at 09:04:02 UTC. That
and workflow success are send-side evidence; inbox receipt remains
unverified.

On 2026-09-26, the six registered configuration files matched their frozen
SHA-256 values. This checks the local configuration files, not the running
Streamlit Cloud commit. The public Cloud version remains unverified.

## Public Cloud recheck on 2026-09-27 02:24–02:28 JST

After reloading the public [History](https://japan-stock-predictor-ky1.streamlit.app/History)
page, its one-week window showed 2 sessions, 44 predictions, 14 BUY decisions,
and -110,150 JPY in saved paper P/L. These agree with the two registered
sessions in the read-only database inspection. The full-period window displayed
994 predictions across 28 sessions. Running the current History page's
`published_prediction_history(None)` query against the locally configured
reporting database returned 572 eligible rows across 26 sessions; the separate
read-only cohort recomputation also found 572. The full-period difference is
**422 rows and 2 sessions**. It remains unresolved whether the public Cloud
app uses a different database, a different running commit, or another data
path; recent-session agreement alone does not establish deployment identity.

The public [System Status](https://japan-stock-predictor-ky1.streamlit.app/System_Status)
page showed the latest prediction date as 9/25, 3 BUY candidates and 3/3
correct BUY directions, but its header displayed **+0 JPY** for that settled
day. The read-only database paper-trade sum is **+28,800 JPY**, and the History
one-week total is consistent with that value. The conflicting header remains
an open UI/data-source finding, not a corrected result.
