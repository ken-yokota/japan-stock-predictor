"""Prove that each day's mail went out and the dashboard moved with it.

The operator's standing requirement is that the morning and the evening update
are never silently skipped. Every piece of that already runs on its own
schedule, which is exactly the problem: a workflow that fails, or one whose
data never arrived, is a red square nobody is looking at.

So this asserts the outcome rather than the attempt. For a window it checks

  1. the prediction set for the day reached a terminal, publishable state,
  2. an email for it is recorded as SENT in ``email_logs``, and
  3. the public dashboard snapshot is newer than the window and describes that
     same day,

and mails an alert naming whichever of the three is missing. Silence is not
success: if this finds nothing wrong it stays quiet, but if it cannot even
reach the database it says so and fails.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from database.connection import create_database_engine

JST = ZoneInfo("Asia/Tokyo")

SNAPSHOT_URL = (
    "https://raw.githubusercontent.com/ken-yokota/japan-stock-predictor"
    "/snapshot/dashboard_snapshot.json"
)

WINDOWS = ("morning", "evening")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Outcome:
    window: str
    for_date: date
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    @property
    def ok(self) -> bool:
        return not self.failures


def _prediction_set(connection: object, for_date: date) -> dict[str, object] | None:
    row = connection.execute(  # type: ignore[attr-defined]
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
    ).mappings().first()
    return dict(row) if row else None


def _sent_email(
    connection: object, prediction_set_id: str, since: datetime
) -> dict[str, object] | None:
    row = connection.execute(  # type: ignore[attr-defined]
        text(
            """
            SELECT subject, template_version, sent_at, recipient
            FROM email_logs
            WHERE prediction_set_id = :prediction_set_id
              AND status = 'SENT'
              AND sent_at >= :since
            ORDER BY sent_at DESC
            LIMIT 1
            """
        ),
        {"prediction_set_id": prediction_set_id, "since": since},
    ).mappings().first()
    return dict(row) if row else None


def _settled_count(connection: object, prediction_set_id: str) -> int:
    return int(
        connection.execute(  # type: ignore[attr-defined]
            text(
                """
                SELECT COUNT(*)
                FROM actual_results AS ar
                JOIN predictions AS p ON p.prediction_id = ar.prediction_id
                WHERE p.prediction_set_id = :prediction_set_id
                """
            ),
            {"prediction_set_id": prediction_set_id},
        ).scalar()
        or 0
    )


def _email_detail(email: dict[str, object] | None) -> str:
    """One line saying whether the day's mail is on record as delivered."""

    if email is None:
        return "SENTの記録がありません"
    return f"sent_at={email['sent_at']}"


def _snapshot(url: str, timeout: int = 20) -> dict[str, object] | None:
    try:
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=timeout) as reply:
            if reply.status != 200:
                return None
            return json.loads(reply.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def verify(
    database_url: str,
    *,
    window: str,
    for_date: date,
    snapshot_url: str,
    max_snapshot_age_minutes: int,
) -> Outcome:
    outcome = Outcome(window=window, for_date=for_date)
    # Anything sent for this date can only count if it was sent today.
    since = datetime.combine(for_date, datetime.min.time(), JST).astimezone(UTC)

    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            published = _prediction_set(connection, for_date)
            if published is None:
                outcome.checks.append(
                    Check("prediction", False, f"{for_date} の予測セットがありません")
                )
                return outcome

            status = str(published["status"])
            set_id = str(published["prediction_set_id"])
            outcome.checks.append(
                Check(
                    "prediction",
                    status in {"READY", "INSUFFICIENT_DATA"},
                    f"status={status}",
                )
            )

            email = _sent_email(connection, set_id, since)
            outcome.checks.append(
                Check(
                    "email",
                    email is not None,
                    _email_detail(email),
                )
            )

            if window == "evening":
                settled = _settled_count(connection, set_id)
                outcome.checks.append(
                    Check("actuals", settled > 0, f"確定 {settled} 銘柄")
                )
    except SQLAlchemyError:
        # The message can carry the host and the user, so it is not repeated.
        outcome.checks.append(Check("database", False, "本番DBに接続できません"))
        return outcome

    snapshot = _snapshot(snapshot_url)
    if snapshot is None:
        outcome.checks.append(
            Check("dashboard", False, "公開スナップショットを取得できません")
        )
        return outcome

    try:
        generated = datetime.fromisoformat(str(snapshot["generated_at"]))
    except (KeyError, ValueError):
        outcome.checks.append(Check("dashboard", False, "generated_at を読めません"))
        return outcome

    age = datetime.now(UTC) - generated.astimezone(UTC)
    fresh = age <= timedelta(minutes=max_snapshot_age_minutes)
    published: object = snapshot.get("prediction_set") or {}
    snapshot_date = str(
        published.get("prediction_date") if isinstance(published, dict) else None
    )
    outcome.checks.append(
        Check(
            "dashboard",
            fresh and snapshot_date == for_date.isoformat(),
            f"generated_at={generated.astimezone(JST):%m-%d %H:%M} JST "
            f"({int(age.total_seconds() // 60)}分前) / 対象日={snapshot_date}",
        )
    )
    return outcome


def _alert_bodies(outcome: Outcome) -> tuple[str, str, str]:
    label = "朝" if outcome.window == "morning" else "夕"
    missing = "・".join(check.name for check in outcome.failures)
    subject = f"【要確認】{label}の配信が完了していません（{missing}）"
    lines = [
        f"{outcome.for_date} の{label}の更新で、以下が確認できませんでした。",
        "",
    ]
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
    parser.add_argument("--window", choices=WINDOWS, required=True)
    parser.add_argument("--for-date", default=None, help="YYYY-MM-DD (既定: JSTの今日)")
    parser.add_argument("--snapshot-url", default=SNAPSHOT_URL)
    parser.add_argument("--max-snapshot-age-minutes", type=int, default=120)
    parser.add_argument(
        "--dry-run", action="store_true", help="判定するがメールは送らない"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import os

    arguments = _parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    for_date = (
        date.fromisoformat(arguments.for_date)
        if arguments.for_date
        else datetime.now(JST).date()
    )
    outcome = verify(
        database_url,
        window=arguments.window,
        for_date=for_date,
        snapshot_url=arguments.snapshot_url,
        max_snapshot_age_minutes=arguments.max_snapshot_age_minutes,
    )

    print(
        json.dumps(
            {
                "window": outcome.window,
                "for_date": for_date.isoformat(),
                "ok": outcome.ok,
                "checks": {
                    check.name: {"ok": check.ok, "detail": check.detail}
                    for check in outcome.checks
                },
            },
            ensure_ascii=False,
        )
    )
    if outcome.ok:
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
