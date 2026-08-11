"""Prove that each day's mail went out and the dashboard moved with it.

The standing requirement is that the morning and the evening update are never
silently skipped. Every piece of that already runs on its own schedule, which
is exactly the problem: a workflow that fails, or one whose data never
arrived, is a red square nobody is looking at. Worse is the case where nothing
turns red at all - a disabled automation flag makes every job report success
by skipping.

So this asserts the outcome rather than the attempt, and it is deliberately
built to keep working when the rest does not:

* It never gates itself on ``AUTOMATION_ENABLED``. A watchdog that switches
  off together with the thing it watches is not a watchdog; it reads that flag
  and raises the alarm when it is false, missing or malformed.
* It knows when a window is not due yet, so an 06:00 run reports NOT_YET_DUE
  rather than shouting about mail that is not scheduled until 08:45.
* It knows a JPX holiday is not a failure.

Silence means success only when the checks actually ran and passed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError

from data.market_calendar import is_japan_business_day
from database.connection import create_database_engine

JST = ZoneInfo("Asia/Tokyo")

SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/ken-yokota/japan-stock-predictor"
    "/snapshot/dashboard_snapshot.json"
)

# When each window starts producing, and when it has finished everything it is
# going to do. Before the second time, a missing mail or an unrefreshed
# snapshot is the schedule, not a fault.
#   morning: prediction 08:10-08:30, snapshot 08:40, mail 08:45-08:55
#   evening: close 15:45-16:10, summary 17:00, snapshot 17:10
WINDOW_HOURS: dict[str, tuple[time, time]] = {
    "morning": (time(8, 0), time(9, 0)),
    "evening": (time(15, 30), time(17, 30)),
}

# Neon's free project ceiling. Crossing it aborts a write mid-transaction,
# which is how three consecutive mornings were lost before anything published.
NEON_LIMIT_BYTES = 512 * 1024 * 1024
CAPACITY_BANDS = ((0.95, "CRITICAL"), (0.85, "ALERT"), (0.70, "WARNING"))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Outcome:
    window: str
    for_date: date
    verdict: str = "CHECKED"
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def alerting(self) -> bool:
        """A failed check only alerts once the window was actually due.

        NOT_YET_DUE and NON_TRADING_DAY are answers, not alarms - except for
        the automation flag, which is wrong at any hour of any day.
        """

        if self.verdict == "CHECKED":
            return not self.ok
        return any(check.name == "automation" for check in self.failures)


def capacity_band(used_bytes: int, limit_bytes: int = NEON_LIMIT_BYTES) -> str:
    """NORMAL/WARNING/ALERT/CRITICAL, so DiskFull is never the first warning."""

    ratio = used_bytes / limit_bytes if limit_bytes else 0.0
    for threshold, label in CAPACITY_BANDS:
        if ratio >= threshold:
            return label
    return "NORMAL"


def window_is_due(window: str, now: datetime) -> bool:
    """Has the window finished everything it was scheduled to do?"""

    _, due = WINDOW_HOURS[window]
    return now.astimezone(JST).time() >= due


def window_start(window: str, for_date: date) -> datetime:
    """The instant a snapshot has to be newer than to count as this window's."""

    start, _ = WINDOW_HOURS[window]
    return datetime.combine(for_date, start, JST).astimezone(UTC)


def automation_check(raw: str | None) -> Check:
    """The one flag that silently disables every other workflow."""

    if raw is None or not raw.strip():
        return Check("automation", False, "AUTOMATION_ENABLED が未設定です")
    value = raw.strip().lower()
    if value == "true":
        return Check("automation", True, "true")
    if value == "false":
        return Check("automation", False, "AUTOMATION_ENABLED=false（自動実行が停止）")
    return Check("automation", False, f"AUTOMATION_ENABLED が不正な値です: {value!r}")


def email_check(sent: list[dict[str, object]]) -> Check:
    """Zero is a missed send and two is a double send. Both are faults."""

    if not sent:
        return Check("email", False, "当日のSENT記録がありません")
    if len(sent) > 1:
        keys = {str(row.get("idempotency_key")) for row in sent}
        return Check(
            "email", False, f"重複送信 {len(sent)}件（idempotencyキー {len(keys)}種）"
        )
    return Check("email", True, f"sent_at={sent[0]['sent_at']}")


def snapshot_check(
    snapshot: dict[str, object] | None,
    *,
    for_date: date,
    since: datetime,
) -> Check:
    """A snapshot counts as current only if it is new *and* about the right day.

    Checking only the date lets yesterday's healthy file stand in for today's
    missing one, which is the failure this whole script exists to prevent.
    """

    if snapshot is None:
        return Check("dashboard", False, "公開スナップショットを取得できません")
    try:
        generated = datetime.fromisoformat(str(snapshot["generated_at"]))
    except (KeyError, ValueError):
        return Check("dashboard", False, "generated_at を読めません")

    published = snapshot.get("prediction_set")
    snapshot_date = (
        str(published.get("prediction_date")) if isinstance(published, dict) else "—"
    )
    fresh = generated.astimezone(UTC) >= since
    right_day = snapshot_date == for_date.isoformat()
    detail = (
        f"generated_at={generated.astimezone(JST):%m-%d %H:%M} JST / "
        f"対象日={snapshot_date}"
    )
    if not fresh:
        detail += "（この窓より古い）"
    if not right_day:
        detail += "（日付が一致しない）"
    return Check("dashboard", fresh and right_day, detail)


def _prediction_set(connection: Connection, for_date: date) -> dict[str, object] | None:
    row = (
        connection.execute(
            text(
                """
                SELECT prediction_set_id, status, generated_at, published_at
                FROM prediction_sets
                WHERE prediction_date = :for_date
                ORDER BY generated_at DESC
                LIMIT 1
                """
            ),
            {"for_date": for_date},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _prediction_counts(connection: Connection, set_id: str) -> dict[str, int]:
    """Per-status counts: a BUY-less day is healthy, a prediction-less day is not."""

    rows = connection.execute(
        text(
            """
            SELECT status, COUNT(*) AS n
            FROM predictions
            WHERE prediction_set_id = :set_id
            GROUP BY status
            """
        ),
        {"set_id": set_id},
    ).all()
    return {str(row[0]): int(row[1]) for row in rows}


def _sent_emails(
    connection: Connection, set_id: str, since: datetime
) -> list[dict[str, object]]:
    rows = (
        connection.execute(
            text(
                """
                SELECT template_version, sent_at, idempotency_key
                FROM email_logs
                WHERE prediction_set_id = :set_id
                  AND status = 'SENT'
                  AND sent_at >= :since
                ORDER BY sent_at DESC
                """
            ),
            {"set_id": set_id, "since": since},
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


def _settled_count(connection: Connection, set_id: str) -> int:
    return int(
        connection.execute(
            text(
                """
                SELECT COUNT(*)
                FROM actual_results AS ar
                JOIN predictions AS p ON p.prediction_id = ar.prediction_id
                WHERE p.prediction_set_id = :set_id
                """
            ),
            {"set_id": set_id},
        ).scalar()
        or 0
    )


def _database_bytes(connection: Connection) -> int | None:
    try:
        size = connection.execute(
            text("SELECT pg_database_size(current_database())")
        ).scalar()
    except SQLAlchemyError:
        return None
    return int(size or 0)


def _snapshot(url: str, timeout: int = 20) -> dict[str, object] | None:
    try:
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            if reply.status != 200:
                return None
            payload = json.loads(reply.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def verify(
    database_url: str,
    *,
    window: str,
    for_date: date,
    now: datetime,
    snapshot_url: str,
    automation_flag: str | None,
) -> Outcome:
    outcome = Outcome(window=window, for_date=for_date)

    # Checked first and always: this is the one failure that makes every other
    # workflow report success by never running.
    outcome.checks.append(automation_check(automation_flag))

    if not is_japan_business_day(for_date):
        outcome.verdict = "NON_TRADING_DAY"
        return outcome
    if not window_is_due(window, now):
        outcome.verdict = "NOT_YET_DUE"
        return outcome

    since = window_start(window, for_date)
    day_start = datetime.combine(for_date, time(0, 0), JST).astimezone(UTC)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            used = _database_bytes(connection)
            if used is not None:
                band = capacity_band(used)
                outcome.checks.append(
                    Check(
                        "db_capacity",
                        band in {"NORMAL", "WARNING"},
                        f"{band} {used / 1024 / 1024:.0f}MB / "
                        f"{NEON_LIMIT_BYTES / 1024 / 1024:.0f}MB",
                    )
                )

            published = _prediction_set(connection, for_date)
            if published is None:
                outcome.checks.append(
                    Check("prediction", False, f"{for_date} の予測セットがありません")
                )
                return outcome

            status = str(published["status"])
            set_id = str(published["prediction_set_id"])
            counts = _prediction_counts(connection, set_id)
            outcome.checks.append(
                Check(
                    "prediction",
                    status in {"READY", "INSUFFICIENT_DATA"},
                    f"status={status} 銘柄内訳={counts}",
                )
            )
            sent = _sent_emails(connection, set_id, day_start)
            outcome.checks.append(email_check(sent))

            if window == "evening":
                settled = _settled_count(connection, set_id)
                outcome.checks.append(
                    Check("actuals", settled > 0, f"確定 {settled} 銘柄")
                )
    except SQLAlchemyError:
        # The message can carry the host and the user, so it is never repeated.
        outcome.checks.append(Check("database", False, "本番DBに接続できません"))
        return outcome

    outcome.checks.append(
        snapshot_check(_snapshot(snapshot_url), for_date=for_date, since=since)
    )
    return outcome


def _alert_bodies(outcome: Outcome) -> tuple[str, str, str]:
    label = "朝" if outcome.window == "morning" else "夕"
    missing = "・".join(check.name for check in outcome.failures)
    subject = f"【ALERT】{label}の配信が完了していません（{missing}）"
    lines = [f"{outcome.for_date} の{label}の更新で、以下が確認できませんでした。", ""]
    for check in outcome.checks:
        lines.append(f"{'OK ' if check.ok else 'NG '} {check.name}: {check.detail}")
    lines += [
        "",
        "この通知は、メール配信とダッシュボード更新が飛んでいないことを",
        "確かめる自動チェックが出しています。NGの項目を確認してください。",
    ]
    text_body = "\n".join(lines)
    rows = "".join(
        f"<tr><td>{'OK' if check.ok else '<b>NG</b>'}</td>"
        f"<td>{check.name}</td><td>{check.detail}</td></tr>"
        for check in outcome.checks
    )
    html_body = (
        f"<p>{outcome.for_date} の{label}の更新で、以下が確認できませんでした。</p>"
        f"<table border='1' cellpadding='6' cellspacing='0'>{rows}</table>"
    )
    return subject, text_body, html_body


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", choices=tuple(WINDOW_HOURS), required=True)
    parser.add_argument("--for-date", default=None, help="YYYY-MM-DD (既定: JSTの今日)")
    parser.add_argument("--snapshot-url", default=SNAPSHOT_URL)
    parser.add_argument(
        "--dry-run", action="store_true", help="判定するがメールは送らない"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    now = datetime.now(JST)
    for_date = (
        date.fromisoformat(arguments.for_date) if arguments.for_date else now.date()
    )
    outcome = verify(
        database_url,
        window=arguments.window,
        for_date=for_date,
        now=now,
        snapshot_url=arguments.snapshot_url,
        automation_flag=os.environ.get("AUTOMATION_ENABLED"),
    )

    print(
        json.dumps(
            {
                "window": outcome.window,
                "for_date": for_date.isoformat(),
                "verdict": outcome.verdict,
                "alerting": outcome.alerting,
                "checks": {
                    check.name: {"ok": check.ok, "detail": check.detail}
                    for check in outcome.checks
                },
            },
            ensure_ascii=False,
        )
    )
    if not outcome.alerting:
        return 0

    subject, text_body, html_body = _alert_bodies(outcome)
    if arguments.dry_run:
        print(subject)
        print(text_body)
        return 1

    from scripts.send_status_report import send_rendered

    provider = send_rendered(subject, text_body, html_body)
    print(json.dumps({"alert": "SENT", "provider": provider}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
