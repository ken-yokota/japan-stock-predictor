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

### The mail reports on their request, not on your work

This is the part that gets inverted. The operator asked for something; the mail
must say **how far along that thing is**, in their words, not narrate the steps
you happened to take.

Every mail carries the same shape:

```
依頼内容        <- quoted back, so there is no doubt which request this is
進捗            3/5 完了
完了したもの     <- what they can now rely on
実行中          <- with an estimate and a projected finish time
残り            <- what has not started
操作の要否       <- almost always "不要"; say so explicitly
```

Write it so someone who has forgotten the conversation can read one mail and
know where their request stands. "予測を公開しました" is progress. "Ridgeの
係数を再計算しました" is narration.

When one request spawns several tasks, one mail covers all of them. Several
mails, each about a fragment, leaves the operator assembling the picture.

**Send whether or not there is good news.** A morning that failed, a step that
is slower than estimated, a result that came back null — those are the mails
that matter most, and they are the ones most easily skipped because there is
nothing pleasant to report. Reporting progress in chat does not discharge this:
the agreement assumes the operator is not reading the screen.

**This covers background work too — every task, without exception.** A job
running unattended is the one the operator can least see, so it needs the mail
most. Anything started in the background gets its own watcher before you move
on to something else; a background task with no watcher is a task whose failure
nobody learns about until someone thinks to ask.

Watch a long job from a **separate process that only reads its output file**.
Never restart a running job to add notification to it.

When several tasks run at once, each mail must say which one it is about and
what the others are doing, or the operator receives fragments and has to
assemble the picture themselves.

See `email-status-reports` for the send path, the subject-line conventions, the
certificate trap, and the rule that silence is not success.

## 3. Enumerate the failure modes, then exercise them

Before calling anything ready, list every way it can fail, decide what each
failure should do, and **run each one**. Reading the code is not the same
check: two of the failures found this way were in code that read correctly.

What this found on a system that had already passed 234 tests and a green CI:

- The evening summary matched `state == "INVALID"` while artifacts were stamped
  `INVALID_FOR_ADOPTION`. Every retired result was being mailed out with its
  p-values intact — by the one report written to prevent exactly that.
- The morning mail raised `ValueError` when no prediction existed, so the
  morning most worth hearing about produced no mail at all, three times over.
- The fallback written to fix that failed on its first run with an
  `AttributeError`, because it built an SMTP client by hand instead of using
  the factory the real path uses. A fallback that fails silently is worse than
  no fallback: it converts a loud failure into a quiet one.

Cover at least: empty inputs, a missing upstream artifact or row, a
non-business day, a date with no data, a credential that is absent, and the
same job running twice. For anything scheduled, check what a *second* run does
— idempotency keys must be per-date, not per-invocation.

Exercise the failure branch against the real dependency where it is read-only.
A dry run that only validates configuration proves nothing about the path that
breaks.

Prefer an existing factory or helper over rebuilding one at the call site. The
provider choice, credentials, retries, and timeouts already live in one place;
a second copy is a second thing to get wrong, and it will be the copy nobody
tests.
