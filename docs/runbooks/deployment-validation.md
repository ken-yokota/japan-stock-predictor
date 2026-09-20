# Deployment and evidence

1. Audit status/remotes/all refs and preserve user edits in a separate worktree.
2. Record actual production config and DB health using read-only transactions.
3. Run unit/full pytest, Ruff and mypy. Keep existing tests. Migrate a scratch
   SQLite database and a scratch PostgreSQL database before production migration.
4. Compare exact shared PIT datasets in time-series OOS. No challenger promotion
   when improvement or historical availability is unresolved.
5. Enumerate `pages/*.py` dynamically; run AppTest with empty and populated data,
   record rendered tabs, and exercise ticker/date filters. Test mobile separately.
6. Render Morning/Close TEST emails with the production DTO/renderers. Require
   explicit TEST subjects, finite numbers, all configured stocks, correct arm
   summaries, quality and factors. Use TEST_EMAIL_TO or the existing operator
   recipient; never print it. Send without production email_logs/idempotency.
   Record SENT_ACCEPTED separately from DELIVERED_VERIFIED.
7. Run safe workflow_dispatch checks on the branch; test executions use scratch
   data or read-only/dry-run paths. Never run ingestion or migration against
   production merely to test a CLI. Verify what dry-run actually does.
8. Review/commit/push. Deploy only green changes, then inspect Streamlit Cloud
   all pages/tabs/filters and the business result, not only workflow success.
9. Register a fresh forward period; report each unverified check explicitly.

Rollback: revert the deployment commit and restore its saved registry/config.
Additive nullable migrations can remain while rolling application code back;
do not drop diagnostic evidence from production during rollback.

Progress for this audit uses `.progress-tasks.json` and
`python -m scripts.send_progress_report --task .progress-tasks.json`.
Read current DB/Git/run state at send time. Report stage n/8, outcomes and gaps;
do not describe a killed process as running. Stop periodic reporting at completion.
