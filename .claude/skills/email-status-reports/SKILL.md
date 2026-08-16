---
name: email-status-reports
description: How to keep the operator informed in this repository when they are away from the terminal - what earns a mail, the counted-progress format they asked for, the requirement to build every report from live state rather than from memory, the layout spec, and the traps found here (silent stalls, TLS certificates, secret leakage). Use whenever work will run longer than a couple of minutes, whenever a job fails, and whenever the operator adds a task mid-flight.
---

# Status reports

The operator is usually not watching the terminal. Anything they would want to
know within the hour has to reach them, and **reporting in chat does not
discharge this** — the agreement assumes they are not reading the screen.

## 1. Pick the channel by what it is for

| Need | Use |
|---|---|
| Reaches their phone right now | `PushNotification` |
| A report they will read, keep, or share | `Artifact` (private page, one URL) |
| A file they will open elsewhere | `SendUserFile` |
| The standing progress mail below | `scripts/send_progress_report.py` |

The production morning and after-close mails are a **product feature**
(`services/email.py`, the workflows) and are not this skill's subject.

## 2. What earns a report

A stage of a long run completing; a run finishing with its result; a run
failing or a process vanishing; a stage overrunning its estimate; something
durable changing (dashboard, config, schedule); a number that looks *wrong*
rather than merely bad.

Not: log lines, percentages, or anything finishing in under a minute. A mailbox
that fills with noise stops being read, and then the one that mattered is
missed too.

## 3. Silence is not success

A watcher here grepped only for the success marker, so a stalled job and a
healthy job looked identical — both silent.

Before arming any `Monitor`, ask: *if this crashed right now, would the operator
hear?* Cover the success marker, `Traceback|Error|ERR `, the process
disappearing, **and elapsed time past the estimate** — a hang produces no output
at all, so only a clock detects it. Alert at roughly 1.5x the estimate, say
plainly that it is still running, and do not stop the job.

Watch from a **separate process that only reads the job's output file**. Never
restart a long job to add notification to it; a cold interpreter start cost 800
seconds here.

## 4. The progress mail carries a count

The operator reads the subject on a phone and must know how far along **their
request** is without opening anything.

```
件名: 【進捗 3/5】予測を計算中（完了見込み09:40）

依頼内容   ← quoted back, so there is no doubt which request this is
進捗       3/5 完了
完了したもの ← what they can now rely on
実行中      ← with an estimate and a projected finish time
残り        ← what has not started
操作の要否   ← almost always 不要; say so explicitly
```

`【進捗 3/5】` does the job; `【進捗】` does not. A one-step request is `1/1` —
say it anyway. If the work needs more steps, say `3/5 → 3/7 に増えました。理由:`
rather than renumbering quietly, which makes progress look like it went
backwards.

**One step, one mail.** `2/7` and `3/7` are two mails even minutes apart: three
mails at 01:10, 01:40 and 02:30 tell the operator the third step is dragging;
one mail at 02:30 tells them nothing. A **failure is that step's mail**, sent
then, not folded into the next.

**A new request is itself a mail.** When the operator adds work mid-flight, send
the updated picture *before* starting it, and say plainly what it displaced —
`6/8 だった並列化は 7/9 に後ろ倒し`. A reordered list without that sentence
hides the cost.

Write so someone who has forgotten the conversation knows where their request
stands. 「予測を公開しました」is progress; 「Ridgeの係数を再計算しました」is
narration.

## 5. Build it from live state, never from memory

**Every report is assembled when it is sent, from what is true then** — not
from a note written earlier, not from what you remember doing. The operator
asked for this repeatedly because the mails kept describing work that had moved
on.

`scripts/send_progress_report.py` re-reads, on every send: the production DB,
GitHub Actions, `git status`/`git log`, the JPX calendar and workflow crons, and
`.progress-tasks.json` — which a PostToolUse hook on TodoWrite
(`scripts/todo_to_progress_tasks.py`) writes automatically, and which carries
its own age so a stale note is labelled rather than presented as current.

**Do not hand-write a progress mail.** A number that did not come from one of
those sources at send time is a number you are guessing at.

## 6. Say what failed, first

A report listing only successes is not a report. Failures come first when there
are any:

| 節 | 中身 |
|---|---|
| できなかったこと | 何が失敗したか / なぜか / どう対処したか / 今どうなっているか |
| できたこと | 何が完了したか / 検証した証拠 |

Include a failure caught before it did damage (it tells the operator the guard
worked and where the edge is), **a failure you caused yourself** (they cannot
calibrate trust without it), and what is still unproven — a step that has never
once succeeded is not a step that "should work".

Do not soften. `5営業日とも10秒で失敗` is the sentence; 「一部で問題が発生」is
not.

Every failure report carries five parts: 何が起きたか (with numbers), 原因
(established by measurement, not inferred from reading code), 対応 (including
cleanup of records a killed process left), 解決策 (each option with 想定工数と
想定時間, so the operator chooses), 結果 (measured the same way). "後で直します"
is not a solution.

## 7. Numbers go in tables

Any comparison, any set of measurements, any list of stages with times. Two or
more rows sharing columns is a table. Four columns at most — a fifth wraps on a
phone. Give every number its unit, show before/after/ratio side by side, and put
totals in the table as their own row.

State the sample size beside every number, and **say what the numbers do not
prove**. If P&L looks great on 13 trades, say 13 trades cannot separate a better
model from a luckier month. That sentence is the one that prevents a bad
decision and the one most often dropped.

## 8. Layout, sending, and the traps

`docs/EMAIL_FORMAT.md` is the approved spec — read it before composing, and do
not hand-roll HTML. `notifications/report_layout.py` holds the tables, badges
and signed-and-coloured numbers; send with
`python -m scripts.send_status_report report.json` (`--dry-run` to preview).

Use the repository's sender (`notifications/senders.GmailSmtpSender`), not
hand-rolled SMTP, so retries and TLS match the morning mail. Give every message
a distinct `idempotency_key` including a timestamp, or a retry silently replaces
the previous report.

**The certificate trap**: `ssl.create_default_context()` on a python.org macOS
build has no CA bundle until `Install Certificates.command` has run. It surfaces
as the generic `Gmail SMTP delivery failed`. A connection-level failure means
certificates or network; `"Gmail SMTP rejected the message"` means auth. Check
TLS before suspecting the password.

**Never put secrets in a report** — not the password, the API key, a connection
string, or a full `.env` line. Checking whether a variable is set is fine;
printing its value is not.
