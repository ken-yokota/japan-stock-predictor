# Repository invariants

- Target: Japanese cash-equity `Close / Open - 1`. Prediction cutoff is fixed
  at 08:30 Asia/Tokyo, including delayed jobs. Never admit later observations.
- Preserve the 22 configured tickers. Production, backtests and the dashboard
  must use the same explicit ticker feature registry and saved versions.
- Keep the champion unless chronological OOS supports promotion. Random splits,
  full-period preprocessing, holdout tuning and daily feature selection are
  forbidden. Fit coefficients daily; freeze feature/model/threshold choices for
  at least 20 new trading sessions. News remains research-only without licensed,
  timestamped historical availability.
- Missing/stale required inputs fail closed. Never silently replace an input
  with a different instrument or invent an observed value.
- Persist prediction provenance, current features and genuine model explanations;
  do not duplicate training matrices daily or call tree importance a coefficient.
- The dashboard is read-only. Email HTML/text and UI use shared computations.
  TEST mail must not write production delivery/trade/prediction records.
- Never print secrets, connection strings or recipient addresses. Respect existing
  uncommitted edits. Do not merge old research branches wholesale.
- Distinguish measured PASS, failure, unavailable evidence and future work.
  Provider acceptance is not proof of email receipt; 20 forward sessions cannot
  be manufactured from already inspected history.
- Preserve `.claude/` and immutable research records. See
  [research](docs/runbooks/quant-research.md) and
  [deployment](docs/runbooks/deployment-validation.md) for detailed procedures.
- Run relevant tests, full pytest, Ruff, mypy, SQLite/PostgreSQL migrations and
  all Streamlit pages before deployment. Commit/push only reviewed changes.
