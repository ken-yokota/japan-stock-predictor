#!/usr/bin/env python3
"""Send one persisted prediction email through free Gmail SMTP by default.

If no prediction set exists, the operator is told so by mail rather than by an
exception, and exactly once.  A delivered fallback notice does not turn a
missing prediction into success: the final scheduled attempt exits non-zero so
GitHub Actions remains an accurate health signal.

Deduplicating that notice cannot use the database: ``email_logs.prediction_set_id``
is NOT NULL with a foreign key, and by definition there is no set to point at.
The schedule fires this job three times so that a transient delay still gets
the prediction out. The workflow marks the first two missing-set attempts as
deferred and only the final one sends the fallback notice; this remains correct
when GitHub starts every cron late. An explicitly requested run always sends,
because someone asked. The morning job can fail for reasons unrelated to this
script -- a throttled provider, a database outage, or a cancelled run -- and
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
from data.market_calendar import is_japan_business_day
from notifications.contracts import RenderedEmail
from notifications.templates import render_morning_email
from scripts.runtime import load_runtime
from services.email import (
    _sender,
    load_morning_email_payload,
    send_persisted_morning_email,
)
from services.ingestion import today_in_application_timezone

# The workflow explicitly marks its first two firings as deferred. The clock is
# a safe fallback for direct invocations without an explicit prediction date.
_NOTICE_AFTER = time(8, 53)

EXIT_OK = 0
EXIT_NO_PREDICTION = 2
EXIT_NOTIFICATION_FAILED = 3

_MISSING_PREDICTION_PREFIX = "no terminal prediction set is available"


def _should_notify(
    explicit_date: date | None,
    now: datetime | None = None,
    *,
    defer_missing: bool = False,
) -> bool:
    """Send on a manual/final attempt; tolerate delayed scheduler starts."""

    if defer_missing:
        return False
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
    parser.add_argument(
        "--defer-missing",
        action="store_true",
        help="Do not notify/fail when no set exists; a later attempt is authoritative",
    )
    return parser


def _notify_missing(
    environment: EnvironmentSettings, target: date
) -> dict[str, str | None]:
    """Mail a plain notice that no prediction exists and return its outcome.

    Reuses the same sender factory the real mail uses, so provider choice,
    credentials, retries, and timeouts stay in one place. Building a client by
    hand here was the first attempt and it failed on an attribute name -- the
    fallback path silently not working is precisely the failure this exists to
    prevent.

    This function does not raise because the caller must always emit one
    sanitized, machine-readable result.  The caller turns a failed delivery
    into a non-zero exit code; notification failure must never look green in
    GitHub Actions.
    """

    label = target.isoformat()
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
        delivery = _sender(environment).send(
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
        return {
            "notification_status": "SENT",
            "notification_provider": delivery.provider,
            "notification_message_id": delivery.message_id,
            "notification_error_type": None,
        }
    except Exception as error:
        return {
            "notification_status": "FAILED",
            "notification_provider": None,
            "notification_message_id": None,
            "notification_error_type": type(error).__name__,
        }


def _print_result(payload: dict[str, object]) -> None:
    """Print one sanitized result line for humans and workflow tooling."""

    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _is_missing_prediction(error: ValueError) -> bool:
    """Distinguish the expected empty-set case from configuration/program errors."""

    return str(error).startswith(_MISSING_PREDICTION_PREFIX)


def _validate_delivery_configuration(environment: EnvironmentSettings) -> None:
    """Fail before a DB claim if the selected sender cannot be constructed.

    ``send_persisted_morning_email`` commits its idempotency claim before SMTP
    I/O.  Missing credentials must therefore be detected here; otherwise the
    row would remain ``SENDING`` and the next scheduled retry would incorrectly
    report ``ALREADY_SENT``.
    """

    environment.require_email_addresses()
    email_sender = _sender(environment)
    close = getattr(email_sender, "close", None)
    if callable(close):
        close()


def main() -> int:
    args = _parser().parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    prediction_date = args.prediction_date or today_in_application_timezone(config)
    try:
        if not is_japan_business_day(prediction_date):
            _print_result(
                {
                    "status": "SKIPPED",
                    "reason": "NON_BUSINESS_DAY",
                    "prediction_date": prediction_date.isoformat(),
                    "exit_code": EXIT_OK,
                }
            )
            return EXIT_OK
        if args.dry_run:
            try:
                sender, recipient = environment.require_email_addresses()
                with factory() as session:
                    _, payload = load_morning_email_payload(
                        session,
                        config,
                        prediction_date=prediction_date,
                        dashboard_url=environment.app_url,
                    )
                preview = render_morning_email(
                    payload,
                    sender=sender,
                    recipient=recipient,
                    top_n=args.top_n,
                )
            except ValueError as error:
                reason = (
                    "NO_PREDICTION_SET"
                    if _is_missing_prediction(error)
                    else "EMAIL_CONFIGURATION_FAILED"
                )
                exit_code = (
                    EXIT_NO_PREDICTION
                    if reason == "NO_PREDICTION_SET"
                    else EXIT_NOTIFICATION_FAILED
                )
                _print_result(
                    {
                        "status": "FAILED",
                        "reason": reason,
                        "prediction_date": prediction_date.isoformat(),
                        "error_type": type(error).__name__,
                        "exit_code": exit_code,
                    }
                )
                return exit_code
            except Exception as error:
                _print_result(
                    {
                        "status": "FAILED",
                        "reason": "DRY_RUN_FAILED",
                        "prediction_date": prediction_date.isoformat(),
                        "error_type": type(error).__name__,
                        "exit_code": EXIT_NOTIFICATION_FAILED,
                    }
                )
                return EXIT_NOTIFICATION_FAILED
            _print_result(
                {
                    "status": "SUCCESS",
                    "outcome": "DRY_RUN",
                    "prediction_date": prediction_date.isoformat(),
                    "subject": preview.subject,
                    "candidates": len(payload.candidates),
                    "idempotency_key": preview.idempotency_key,
                    "exit_code": EXIT_OK,
                }
            )
            return EXIT_OK
        try:
            _validate_delivery_configuration(environment)
        except Exception as error:
            _print_result(
                {
                    "status": "FAILED",
                    "reason": "EMAIL_CONFIGURATION_FAILED",
                    "prediction_date": prediction_date.isoformat(),
                    "error_type": type(error).__name__,
                    "exit_code": EXIT_NOTIFICATION_FAILED,
                }
            )
            return EXIT_NOTIFICATION_FAILED
        try:
            delivery = send_persisted_morning_email(
                factory,
                config,
                environment,
                prediction_date=prediction_date,
                top_n=args.top_n,
            )
        except ValueError as error:
            if not _is_missing_prediction(error):
                _print_result(
                    {
                        "status": "FAILED",
                        "reason": "EMAIL_CONFIGURATION_OR_DATA_FAILED",
                        "prediction_date": prediction_date.isoformat(),
                        "error_type": type(error).__name__,
                        "exit_code": EXIT_NOTIFICATION_FAILED,
                    }
                )
                return EXIT_NOTIFICATION_FAILED
            if _should_notify(
                args.prediction_date,
                defer_missing=args.defer_missing,
            ):
                notification = _notify_missing(environment, prediction_date)
                notification_failed = notification["notification_status"] == "FAILED"
                exit_code = (
                    EXIT_NOTIFICATION_FAILED
                    if notification_failed
                    else EXIT_NO_PREDICTION
                )
                _print_result(
                    {
                        "status": "FAILED",
                        "reason": "NO_PREDICTION_SET",
                        "prediction_date": prediction_date.isoformat(),
                        **notification,
                        "exit_code": exit_code,
                    }
                )
                return exit_code
            else:
                # An earlier firing: stay quiet and let a later one report, in
                # case the prediction lands in between.
                _print_result(
                    {
                        "status": "RETRY_PENDING",
                        "reason": "NO_PREDICTION_SET",
                        "prediction_date": prediction_date.isoformat(),
                        "notification_status": "DEFERRED",
                        "exit_code": EXIT_OK,
                    }
                )
                return EXIT_OK
        except Exception as error:
            _print_result(
                {
                    "status": "FAILED",
                    "reason": "EMAIL_DELIVERY_FAILED",
                    "prediction_date": prediction_date.isoformat(),
                    "error_type": type(error).__name__,
                    "exit_code": EXIT_NOTIFICATION_FAILED,
                }
            )
            return EXIT_NOTIFICATION_FAILED
        result_payload = {
            "status": "SUCCESS",
            "outcome": "ALREADY_SENT" if delivery is None else "SENT",
            "prediction_date": prediction_date.isoformat(),
            "provider": delivery.provider if delivery is not None else None,
            "message_id": delivery.message_id if delivery is not None else None,
            "exit_code": EXIT_OK,
        }
        _print_result(result_payload)
        return EXIT_OK
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
