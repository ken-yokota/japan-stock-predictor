#!/usr/bin/env python3
"""Send one persisted prediction email through free Gmail SMTP by default.

If no prediction set exists, the operator is told so by mail rather than by an
exception, and exactly once.

Deduplicating that notice cannot use the database: ``email_logs.prediction_set_id``
is NOT NULL with a foreign key, and by definition there is no set to point at.
The schedule fires this job three times so that a transient SMTP failure still
gets the prediction out, which would otherwise mean three identical "no
prediction" mails. So the notice is gated on the clock instead — only the last
firing of the window sends it. An explicitly requested run always sends,
because someone asked. The morning job can fail for reasons that have nothing to do with
this script -- a throttled provider, a database outage, a cancelled run -- and
the reader is usually away from the machine. An unsent mail and a crashed
process look identical from a phone, and "no prediction today" is exactly the
message worth delivering.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from data.env import EnvironmentSettings
from notifications.contracts import RenderedEmail
from notifications.templates import render_morning_email
from scripts.runtime import load_runtime
from services.email import (
    _sender,
    load_morning_email_payload,
    send_persisted_morning_email,
)

# The schedule fires at 08:45, 08:50, and 08:55 JST from a single cron
# expression, so the three firings are indistinguishable to the process. Gating
# on the clock is what keeps one missing-prediction notice from becoming three.
# Change this if that cron changes.
_NOTICE_AFTER = time(8, 53)


def _should_notify(explicit_date: date | None, now: datetime | None = None) -> bool:
    """Send the notice on the last scheduled firing, or whenever asked directly."""

    if explicit_date is not None:
        return True
    current = (now or datetime.now(ZoneInfo("Asia/Tokyo"))).timetz()
    return current.replace(tzinfo=None) >= _NOTICE_AFTER


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _notify_missing(environment: EnvironmentSettings, target: date | None) -> None:
    """Mail a plain notice that no prediction exists, and never raise.

    Reuses the same sender factory the real mail uses, so provider choice,
    credentials, retries, and timeouts stay in one place. Building a client by
    hand here was the first attempt and it failed on an attribute name -- the
    fallback path silently not working is precisely the failure this exists to
    prevent.

    Best effort by design: this runs when something has already gone wrong, so
    a failure here must not replace one silent morning with a louder one.
    """

    label = target.isoformat() if target else "本日"
    stamp = datetime.now(ZoneInfo("Asia/Tokyo")).strftime("%Y-%m-%d %H:%M")
    body = (
        f"{label} の予測が見つかりませんでした。\n\n"
        "朝のpipelineが完走していない可能性があります。以下を確認してください。\n"
        "  GitHub Actions の Morning prediction 実行結果\n"
        "  17:00 の日次サマリーメール (実行状況が入ります)\n\n"
        "本メールは、予測が無いこと自体をお知らせするためのものです。\n"
        "予測が作られていれば通常の予測メールが届きます。"
    )
    try:
        sender_address, recipient = environment.require_email_addresses()
        _sender(environment).send(
            RenderedEmail(
                subject=f"【予測なし】{label} の朝の予測が見つかりません",
                text=f"{body}\n\n---\n{stamp} JST",
                html=(
                    "<pre style='font-family:ui-monospace,monospace;font-size:13px'>"
                    f"{body}</pre>"
                ),
                sender=sender_address,
                recipient=recipient,
                idempotency_key=f"missing-prediction/{label}",
            )
        )
        print(json.dumps({"status": "NO_PREDICTION_NOTIFIED"}, ensure_ascii=False))
    except Exception as error:
        print(
            json.dumps(
                {"status": "NO_PREDICTION", "notify_failed": type(error).__name__},
                ensure_ascii=False,
            )
        )


def main() -> int:
    args = _parser().parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    try:
        if args.dry_run:
            sender, recipient = environment.require_email_addresses()
            with factory() as session:
                _, payload = load_morning_email_payload(
                    session,
                    config,
                    prediction_date=args.prediction_date,
                    dashboard_url=environment.app_url,
                )
            preview = render_morning_email(
                payload,
                sender=sender,
                recipient=recipient,
                top_n=args.top_n,
            )
            print(
                json.dumps(
                    {
                        "status": "DRY_RUN",
                        "subject": preview.subject,
                        "candidates": len(payload.candidates),
                        "idempotency_key": preview.idempotency_key,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        try:
            delivery = send_persisted_morning_email(
                factory,
                config,
                environment,
                prediction_date=args.prediction_date,
                top_n=args.top_n,
            )
        except ValueError:
            # Raised only when no terminal prediction set exists. Every other
            # failure still propagates, so a genuine bug stays loud.
            if _should_notify(args.prediction_date):
                _notify_missing(environment, args.prediction_date)
            else:
                # An earlier firing: stay quiet and let a later one report, in
                # case the prediction lands in between.
                print(
                    json.dumps(
                        {"status": "NO_PREDICTION", "notice": "deferred"},
                        ensure_ascii=False,
                    )
                )
            return 0
        result_payload = {
            "status": "ALREADY_SENT" if delivery is None else "SENT",
            "provider": delivery.provider if delivery is not None else None,
            "message_id": delivery.message_id if delivery is not None else None,
        }
        print(json.dumps(result_payload, ensure_ascii=False))
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
