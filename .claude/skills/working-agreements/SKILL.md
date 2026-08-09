---
name: working-agreements
description: Standing conventions the operator has set for this repository - what "ダッシュボード" refers to and how to deploy it, and the requirement to email progress while work is in flight as well as when it finishes. Use at the start of any task in this repository, and whenever a request mentions the dashboard or will take more than a few minutes.
---

# Working agreements

Conventions the operator has stated. They are not preferences to weigh; they
are how this project is run.

## 1. "ダッシュボード" means the Streamlit app

Unless the request says otherwise, **the dashboard is the Streamlit
application** — both of these, always both:

| Where | How it updates |
|---|---|
| Local | `./scripts/start_dashboard.sh --lan` on `http://localhost:8501` |
| Cloud | `https://japan-stock-predictor-ky1.streamlit.app`, auto-deployed from `main` |

Updating it therefore means:

1. Run the tests.
2. Commit and push to `main` — this is what redeploys the cloud app.
3. **Restart the local Streamlit process.** A running instance keeps serving
   the old page; this has already caused the operator to report "it's not
   there" for a change that had shipped. Do not assume a reload is enough.
4. Wait for `http://localhost:8501/` to return 200 before saying it is done.
   Startup has taken up to 110 seconds on this machine.

If an artifact under `artifacts/` is what the page displays, it must be
committed too — that directory is gitignored, so it needs `git add -f`.

## 2. Email the progress, not just the result

The operator is usually away from the machine. **Email while the work is still
in flight, not only when it ends.**

Send:

- **At the start** of anything long: the stage list, the estimate per stage,
  and the projected finish time.
- **At each stage boundary**: what finished, what it took against the estimate,
  what is running now, and the revised finish time.
- **When a stage overruns** its estimate by about half again: say plainly that
  it is still running and has not failed.
- **On failure**: which step, which error class, and the log location.
- **At the end**: the result, the numbers with their sample sizes, what the
  numbers do *not* establish, and whether anything needs the operator.

Track the stages against what the operator actually asked for, so the mail
reads as progress on their request rather than progress on your implementation.

Watch a long job from a **separate process that only reads its output file**.
Never restart a running job to add notification to it.

See `email-status-reports` for the send path, the subject-line conventions, the
certificate trap, and the rule that silence is not success.
