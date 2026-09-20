# Deployment validation — in progress

Evidence updated 2026-09-20 JST. **Merged to main; Cloud post-deployment validation in progress; full audit not complete.**

|Check|Result|Evidence / limits|
|---|---|---|
|Baseline pytest|PASS|1053 passed|
|Updated full pytest|PASS at tested revision|1091 passed, 19 warnings, 122.68 s; baseline 1053, +38 cases, none removed|
|Empty/unmigrated DB AppTest|PASS|All 11 discovered pages plus app.py; 24 cases; cache tests 4 cases|
|Live-data local AppTest|PASS 12/12|All 11 discovered pages plus app.py; read-only production data. Initial 10/12 results retained in streamlit_live_data.json; Company and Factor rechecks PASS in 2026-09-20_streamlit_recheck.json. All rendered tab bodies and sampled ticker/date selectors checked; not every possible filter combination.|
|Streamlit Cloud browser|CONNECTED; pre-deployment check|Connection recovered September 20 09:12 JST. Home rendered; All Methods reproduced the fixed missing-icon TypeError. Post-deployment/mobile checks pending.|
|Morning TEST|SENT_ACCEPTED|2026-09-19 23:16 JST; 22 tickers; 41,594 HTML bytes; provider gmail_smtp|
|Close TEST|SENT_ACCEPTED|2026-09-19 23:16 JST; same prediction set; 22 tickers; 36,469 HTML bytes|
|Receiver verification|NOT_VERIFIED|No mailbox receipt evidence; neither mail is DELIVERED_VERIFIED|
|TEST isolation|PASS|Read-only SQL transactions; direct sender; no production delivery claim/trade/prediction writes|
|SQLite migration|PASS|Upgrade to 0007, downgrade to 0006, upgrade again|
|PostgreSQL migration|PASS LOCAL|Separate local audit database: upgrade/check/downgrade/upgrade/check all exit 0|
|Production migration|PASS|0006 → 0007, schema check PASS. Predictions 638, simulated_trades 881, email_logs 44, model_runs 1276 unchanged; diagnostics nonnull 0. Initial preflight table-name error and unsupported pooled startup options fixed before successful SET LOCAL-limited migration.|
|Ruff|PASS|Full repository check at tested revision|
|mypy|PASS|158 application source files, plus seven new research/script files|
|Manual GitHub workflows|PASS, full PostgreSQL integration|Initial run 35478102994 skipped 26 DB cases; after isolated PostgreSQL setup, manual run 35478415072 passed all 1091 tests, Ruff, mypy (158 files), migrations and runtime isolation (17 cases). Superseded push runs were cancelled by CI concurrency, not failed assertions.|
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

Cloud desktop/mobile checks, safe deployment, at least 20 *new* sessions remain outstanding. Forward registration was saved on September 20, with first session September 24 and earliest twentieth session October 22; see 2026-09-20_forward_registration.json. This document is not a completion certificate.

## Release checkpoint

PR #1 merged at 2026-09-20 05:04:52 UTC, main commit `519577bbbce9158a2188c8d88e885ce0dfb564d1`. Application implementation `092c31f`; CI/forward registration `cf49ee6`. Rollback target before this release: `0bf16a3a00f7ab8e4ee11a81bbc02e7321b896c5`. Cloud deployment is automatic from main; a Git merge alone is not a Cloud validation PASS.

## Cloud runtime follow-up

All Methods rendered after deployment and all 14 tabs were selected successfully. Factor Analysis still raised TypeError: the traceback pointed to line 296 with source `str(feature)`, while the old function executed a scalar conversion of a duplicate-date Series there. Local live-data AppTest passed the positional-indexing fix. This mismatch suggests retained imported module state; it is not yet proof of a corrected Cloud page. Browser reload did not resolve it.

Streamlit management is not signed in in the available browser. Pinning the already-tested Streamlit 1.64.0 in both dependency manifests requests a clean automatic Cloud rebuild, without changing the version used by the successful tests. After rebuild, rerun all pages. Official mechanism: https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app (dependency changes trigger a full redeploy).
