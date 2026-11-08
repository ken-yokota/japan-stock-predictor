# Recover delayed close updates

Close settlement resolves the most recently closed JPX session from
`config.settings.application.timezone`. A calendar lookup failure stops the job;
it must not silently substitute the current date, which can be an unopened
session when a scheduled job crosses midnight.

The scheduled close workflow also checks the last 10 JPX sessions. It selects
only each day's latest timely MORNING publication and settles missing outcomes
before processing the latest session. Existing prediction values are never
recomputed by this path. Repeated runs use the outcome, trade and metric
idempotency keys already implemented by `ClosePipeline`.

Preview the selected recovery dates without fetching or writing:

```bash
python -m scripts.run_close_update --catch-up-sessions 10 --dry-run
```

For a specific date whose final raw prices are already stored:

```bash
python -m scripts.run_close_update --prediction-date YYYY-MM-DD --skip-fetch
```

Catch-up is bounded to 1-20 JPX sessions. An incomplete older session remains
PARTIAL in the final workflow result even if the latest session succeeds.
Prediction cutoff, training and model selection are unaffected.

## SMTP diagnostics

The sender records the failing operation (connection, TLS, authentication or
delivery), exception class and numeric SMTP response code. Raw provider text,
credentials and recipient addresses are excluded, including exception chains.
The morning and daily-summary paths retain the same sanitized details.

SMTP 4xx responses and network/disconnection errors have bounded retries.
Permanent SMTP 5xx responses, certificate errors and unsupported operations
fail immediately. Authentication failures require checking the existing Gmail
credentials; improved diagnostics alone do not establish restored delivery.
