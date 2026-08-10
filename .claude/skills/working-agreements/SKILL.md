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

Every mail carries the same shape, and **the subject line carries the count**:

```
件名: 【進捗 3/5】予測を計算中（完了見込み09:40）

依頼内容        <- quoted back, so there is no doubt which request this is
進捗            3/5 完了
完了したもの     <- what they can now rely on
実行中          <- with an estimate and a projected finish time
残り            <- what has not started
操作の要否       <- almost always "不要"; say so explicitly
```

**The count is required, in the subject and in the body, on every mail.** The
operator reads the subject on a phone and must know how far along their request
is without opening anything. `【進捗 3/5】` does that; `【進捗】` does not.

### One step, one mail — never batch several steps into one

The operator has asked for this directly. **Send a mail as each numbered step
finishes, not a combined mail once several are done.** `2/7` finishing is its
own mail; so is `3/7`. A single mail reporting "2/7 and 3/7 are done" is a
batch, and it hides how long each step actually took.

This holds even when two steps finish minutes apart, and even when the second
one is small. The count is the operator's only view of pace: three mails
arriving at 01:10, 01:40 and 02:30 tell them the third step is dragging.
One mail at 02:30 saying "3/7 完了" tells them nothing about that.

It also holds when a step *fails*. A failure is that step's mail, sent then,
with its own count — not folded into the next step's report.

The only thing that shares a mail is a single step's own detail: its result,
its numbers, and what starts next.

Fix the denominator when the request arrives, by breaking it into the steps
*they* would recognise, and keep it stable. If the work turns out to need more
steps, say so — `3/5 → 3/7 に増えました。理由:` — rather than quietly
renumbering, which makes progress look like it went backwards.

A request that is one step is `1/1`. Say it anyway; the operator should never
have to guess whether a count was omitted or the work was unstructured.

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

## 4. Test the production path, by the production procedure

A check that skips a step proves nothing about that step. Two mornings were
lost here to exactly that: the pre-flight ran with `--skip-ingestion`, so the
fetch that timed out was never exercised; then it ran read-only with a
rollback, so the write path that took six hours was never exercised either.
Both times the report said "verified, it will work tomorrow".

So: **run what production runs, the way production runs it, and time it.**

- Same command, same flags, same database, same data volume. If a flag is
  dropped to make the test cheap, that flag is the untested part — name it in
  the report or do not claim coverage.
- **Measure wall-clock against the timeout**, not just success. 34 minutes
  against a 60-minute limit is not "fine", it is a job that fails on a slow
  day. Report the margin.
- Measure the cost that scales: round trips to a hosted database, requests to
  a rate-limited provider, rows written. A step that is instant on ten rows
  can be hours on twenty thousand.

### When it fails, the report is fixed in shape

Every failure report carries all five, in the mail as well as in chat:

1. **何が起きたか** — the observed behaviour, with numbers
2. **原因** — the mechanism, established by measurement rather than inferred
   from reading the code
3. **対応** — what was done immediately, including any cleanup of records a
   killed process left behind
4. **解決策** — each option with **想定工数と想定時間**, so the operator can
   choose rather than being handed a single plan
5. **結果** — what the fix actually achieved, measured the same way

Estimates are required. "後で直します" is not a solution; "バルク化: 想定4〜8
時間、往復821万回が数百回になる見込み" is one the operator can act on.

Report the result by mail whether the fix worked or not. A fix that did not
work is the more important mail.

## 5. A standing report every ten minutes while Claude Code is running

**Whenever Claude Code is working in this repository, mail the operator every
ten minutes.** Not only at stage boundaries, and not only when something
happens — every ten minutes, for as long as the session is doing work.

The operator set this deliberately. Ten minutes is short enough that a stalled
job, a wrong turn, or a silent crash is caught while it still matters, and they
never have to open a terminal to find out where things stand.

Every one of these carries three things, in a table:

- **進行中タスク** — which numbered step, and how long it has been running
- **残タスク** — what has not started, each with its 想定時間
- **想定時間** — the revised finish time for the whole request

```
件名: 【進捗 4/9】特徴量の永続化を実行中（10分ごとの定期報告 03:20）

進行中
  工程    内容                        経過      想定      完了目安
  ------  --------------------------  --------  --------  --------
   4/9    8/10の予測を本番で実行       13分      12分      03:05

残タスク
  工程    内容                        想定時間
  ------  --------------------------  ----------
   5/9    メール配信                   5分
   6/9    ダッシュボード反映確認       5分
   7/9    夕方の引け更新              10分

  残り想定 約20分 / 完了予定 03:40頃
  操作の要否  不要
```

Set the timer up as a **separate process that only reads the working log**, the
same way a job watcher is set up, so the report keeps arriving even if the main
work blocks on something long. Start it when the work starts and stop it when
the work is done — a ten-minute mail arriving after everything finished is
noise, and noise is what stops the mails being read.

If nothing has changed since the previous report, say exactly that and give the
elapsed time. "変化なし、経過22分、想定12分を超過" is a useful report; skipping
the mail because there is nothing new is the failure this rule exists to
prevent.

## 6. A standing report every three hours

While any work is in flight, mail a status every three hours even when nothing
notable has happened. Silence over a long stretch is indistinguishable from a
stall, and the operator should never have to ask "how's it going".

```
件名: 【進捗 2/5】来歴書き込みのバルク化（3時間ごとの定期報告）

いま進めているタスク
  2/5  来歴書き込みのバルク化   想定4〜8時間 / 経過2時間15分
       現状: 往復821万回 → 数百回にする変更を実装、テスト修正中

これからのタスク
  3/5  本日の予測を公開         想定30分
  4/5  取得と予測のジョブ分離   想定1時間
  5/5  全テストとpush           想定30分

残り想定 約6時間 / 完了予定 21:00頃
操作の要否  不要
```

Every one of these carries the same three things: **何分の何が終わったか**,
**それぞれの想定工数と想定時間**, and **残りの見込み**. An estimate that has
been overtaken says so and gives a new one, with the reason.

The three-hour report is a floor, not a substitute. Stage boundaries, failures,
and overruns are still mailed when they happen.
