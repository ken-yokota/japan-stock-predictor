---
name: email-status-reports
description: Send the operator an email whenever something changes, finishes, fails, or looks abnormal in this repository - long research runs, pipeline errors, dashboard/config updates, and delays against an estimate. Covers when to send, what a report must contain to be useful, the send path that works on this machine, and the failure modes found here (TLS certificates, silent stalls, secret leakage). Use whenever work runs longer than a couple of minutes, whenever a job fails, or whenever the operator will not be watching the terminal.
---

# Email status reports

The operator is usually not watching the terminal. Anything they would want to
know within the hour has to reach them by mail, not by scrollback.

## 1. What earns an email

Send one for each of these, and nothing else:

| Event | Example here |
|---|---|
| A stage of a long run completed | 「株価データ取得完了 69系列」 |
| A run finished, with its result | 「学習窓250日の判定: 採用なし」 |
| A run failed or the process vanished | Traceback, killed process, empty artifact |
| A stage overran its estimate | 想定22分の工程が35分経過 |
| Something durable changed | ダッシュボード更新、設定変更、スケジュール変更 |
| A number looks wrong, not just bad | BUY件数が4倍、的中率が突然70% |

Do **not** send for: every log line, progress percentages, or work that finishes
in under a minute. A mailbox that fills with noise stops being read, and then
the one email that mattered is missed too.

## 2. Silence is not success

The failure that actually happened here: a watcher grepped only for the success
marker, so a stalled job and a healthy job looked identical — both silent.

Before arming any watcher, ask: *if this crashed right now, would the operator
get an email?* If not, widen it. Always cover, at minimum:

- the success marker
- `Traceback`, `Error`, `ERR `
- the process disappearing (`pgrep` returning nothing)
- **elapsed time exceeding the estimate** (a hang produces no output at all, so
  only a clock can detect it)

The time-based alert is the one people forget. Send it at roughly 1.5x the
estimate, say plainly that the job is still running, and do not stop the job.

## 3. What a report must contain

A subject line the operator can triage without opening it:

```
【3/4完了】予測要素セットの比較
【遅延】株価データ取得 が想定の1.5倍を超えました
【失敗】リサーチ実行が途中で止まりました
```

The body, in this order:

1. **The result, stated plainly.** 「採用なし。現行のまま変更しません」
2. **The numbers that support it**, with the sample size beside each one.
3. **What the numbers do not prove.** This is the part that gets dropped and
   the part that prevents a bad decision. If the P&L looks great on 13 trades,
   say that 13 trades cannot separate a better model from a luckier month.
4. **Where things stand now** — which stage is running, estimate, projected
   finish time.
5. **What, if anything, the operator needs to do.** Usually nothing. Say so.

### Put numbers in a table, never in a paragraph

The operator asked for this directly, twice. Any comparison, any set of
measurements, any list of stages with times — it goes in a table. Prose that
buries three numbers in a sentence forces the reader to rebuild the table in
their head, and on a phone they simply will not.

Use a table whenever the content has **two or more rows sharing the same
columns**: before/after, option A/B/C, stage/estimate/actual, per-symbol
results. One number in one sentence is fine as prose; two are not.

```
工程            所要時間    状態
------------  ----------  ----------
データ取得        2.1秒     完了
特徴量構築        6.6分     完了
学習              1.0分     実行中
永続化           10.0分     未着手
------------  ----------  ----------
合計            約18分
```

Rules that keep a table readable in a monospace mail on a phone:

- **Four columns at most.** A fifth column wraps and the table stops being one.
- **Align the numbers, and give every one its unit.** `6.6分`, not `6.6`.
- **Name what changed in the last column**, not in a footnote below.
- **Always show the comparison side by side** when reporting an improvement:
  before, after, and the ratio. A lone "after" number proves nothing.
- **Totals go in the table**, as their own row, not in the sentence after it.

Options the operator has to choose between are also a table, and each option
needs 想定工数 and 想定効果 as columns. A recommendation goes in one sentence
underneath, not inside the table.

Everything else in the mail stays plain sentences. A table of prose is worse
than prose.

Include the stage table with estimates and a projected finish time when a run is
still in flight; it is the difference between "it's running" and "I know when to
come back".

## 4. Sending

Use the repository's own sender rather than hand-rolled SMTP, so retries and
TLS behave the same as the morning mail:

```python
from notifications.contracts import RenderedEmail
from notifications.senders import GmailSmtpSender
```

Credentials come from `.env` (`SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`,
`EMAIL_TO`, `EMAIL_PROVIDER`). `EMAIL_TO` may hold several comma-separated
addresses. Give every message a distinct `idempotency_key`, including a
timestamp, or a retry silently replaces the previous report.

Plain text first; wrap it in `<pre>` for the HTML part. Tables of numbers are
unreadable in proportional fonts on a phone.

### The certificate trap

`ssl.create_default_context()` on a python.org macOS build has no CA bundle
until `/Applications/Python 3.11/Install Certificates.command` has been run.
Symptom: `SSLCertVerificationError: unable to get local issuer certificate`,
which this repo's sender reports as the generic `Gmail SMTP delivery failed`.

That command has been run on this machine, so system-wide sending works. If a
send starts failing again, check TLS **before** suspecting the password: the
sender raises `NotificationError` for both, but a connection-level failure means
certificates or network, while `"Gmail SMTP rejected the message"` means auth.

## 5. Never put secrets in a report

Report the failing step, the status column, and the exception class. Never the
password, the API key, or a connection string — and never a full `.env` line.
Checking whether a variable is *set* is fine; printing its value is not.

If a secret is ever pasted into a conversation, say plainly that it must be
revoked now, do not use it, and do not write it anywhere.

## 6. Watch a run without disturbing it

Run the watcher as a **separate process that only reads the job's output file**.
Never restart a long job to add notifications to it — on this machine a cold
interpreter start cost 800 seconds, and the job's progress is not worth that.

Track which milestones have already fired so a marker appearing again does not
resend. Poll every 20-30 seconds; anything faster only burns the disk the job is
already competing for.
