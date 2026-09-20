# Deployment validation — in progress

Evidence updated 2026-09-20 JST. **Not deployed / not complete.**

|Check|Result|Evidence / limits|
|---|---|---|
|Baseline pytest|PASS|1053 passed|
|Updated full pytest|PASS at tested revision|1091 passed, 19 warnings, 122.68 s; baseline 1053, +38 cases, none removed|
|Empty/unmigrated DB AppTest|PASS|All 11 discovered pages plus app.py; 24 cases; cache tests 4 cases|
|Live-data local AppTest|PASS 12/12|All 11 discovered pages plus app.py; read-only production data. Initial 10/12 results retained in streamlit_live_data.json; Company and Factor rechecks PASS in 2026-09-20_streamlit_recheck.json. All rendered tab bodies and sampled ticker/date selectors checked; not every possible filter combination.|
|Streamlit Cloud browser|BLOCKED|Browser webview did not attach; no page/mobile PASS claimed|
|Morning TEST|SENT_ACCEPTED|2026-09-19 23:16 JST; 22 tickers; 41,594 HTML bytes; provider gmail_smtp|
|Close TEST|SENT_ACCEPTED|2026-09-19 23:16 JST; same prediction set; 22 tickers; 36,469 HTML bytes|
|Receiver verification|NOT_VERIFIED|No mailbox receipt evidence; neither mail is DELIVERED_VERIFIED|
|TEST isolation|PASS|Read-only SQL transactions; direct sender; no production delivery claim/trade/prediction writes|
|SQLite migration|PASS|Upgrade to 0007, downgrade to 0006, upgrade again|
|PostgreSQL migration|PASS LOCAL|Separate local audit database: upgrade/check/downgrade/upgrade/check all exit 0|
|Production migration|NOT_APPLIED|Nullable additive model_runs.diagnostics pending deployment|
|Ruff|PASS|Full repository check at tested revision|
|mypy|PASS|158 application source files, plus seven new research/script files|
|Manual GitHub workflows|PENDING|No write-capable production workflow used for a TEST run|
|Production database|READ CHECK PASS|238,338,048 bytes at audit; ~227.3 MiB; aggregate inventory in database_audit.json|
|Provider|PARTIAL / NOT FULLY VERIFIED|Saved-data inventory; no claim that all new candidate feeds have historical availability|
|OOS|COMPLETED LIMITED COMPARISON|22×55 predictions per model; challenger not promoted; see nested_oos_metrics.json|

## Fixed failures

- HTML model agreement previously read an absent verdict field; now uses the same arm verdict computation as text.
- Morning HTML now includes all configured tickers, including NO_BUY.
- Positive/negative factors use saved daily contributions, not raw coefficient sign.
- All Methods page omitted a required icon argument and raised TypeError on render; fixed.
- Dashboard read caches excluded the service identity from their keys; different services now have distinct cache entries.
- Missing required indicators now cause NO PREDICTION, rather than a READY/BUY built from incomplete requirements.
- Factor Analysis crashed when repeated fits shared a training-end date; positional lookup now handles duplicate dates. Company Analysis recheck passed after the AppTest harness stopped retaining stale widget objects across reruns.
- Decimal probability-bin boundaries now belong to exactly one reliability bin.

## Compatibility and rollback

The explicit registry preserves current per-ticker indicator assignments. Compact selection and calibrators are research-only. The model/threshold champion is retained; unproven sequence arms are disabled under the current audit instruction. Prior observed forecasts remain intact.

Migration 0007 is nullable/additive and does not backfill historical explanations. An application rollback can leave the column in place. If removing it is necessary, first archive diagnostic JSON and stop writers, then downgrade to 0006; do not drop recorded explanations during an emergency code rollback. Daily diagnostics follow the existing 90-date coefficient retention with monthly anchors.

Cloud desktop/mobile checks, safe deployment, current-version forward registration and at least 20 *new* sessions remain outstanding. This document is not a completion certificate.
