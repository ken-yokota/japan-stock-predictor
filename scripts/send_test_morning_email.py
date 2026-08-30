#!/usr/bin/env python3
"""Send one morning prediction mail as a test, without touching delivery state.

The real morning send is deduplicated on ``morning/<date>/<recipient>``, and it
has to be: two identical prediction mails on one morning is the failure that
key exists to prevent. That also means the real path cannot be used to look at
a message for a date it has already sent -- ``claim_email`` refuses, correctly,
and nothing arrives.

So this renders the same payload through the same renderer, stamps the subject
as a test, and sends it under its own timestamped key. It deliberately writes
no ``email_logs`` row: this delivery is not the morning delivery, and recording
it as one would make the operational record claim something that did not
happen.

The sender itself comes from ``services.email._sender`` rather than being built
here. A hand-rolled SMTP client at a call site was tried once in this
repository and failed on its first real run with an ``AttributeError``; the
provider choice, credentials, retries and timeouts belong in one place.

Usage:
    python -m scripts.send_test_morning_email --prediction-date 2026-08-28
    python -m scripts.send_test_morning_email --dry-run
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from notifications.contracts import RenderedEmail
from notifications.templates import render_morning_email
from scripts.runtime import load_runtime
from services.email import _sender, load_morning_email_payload

EXIT_OK = 0
EXIT_NO_PREDICTION = 2
EXIT_SEND_FAILED = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument(
        "--prediction-set-id",
        help=(
            "Render one set exactly, including a REFERENCE or replayed "
            "one that the scheduled path correctly refuses to deliver. "
            "Read-only: a preview never needs a temporary write to the "
            "live record."
        ),
    )
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config, environment, engine, factory = load_runtime(args.config_dir)
    try:
        sender_address, recipient = environment.require_email_addresses()
        try:
            with factory() as session:
                prediction_set, payload = load_morning_email_payload(
                    session,
                    config,
                    prediction_date=args.prediction_date,
                    dashboard_url=environment.app_url,
                    prediction_set_id=args.prediction_set_id,
                )
        except ValueError as error:
            print(
                json.dumps(
                    {
                        "status": "FAILED",
                        "reason": "NO_PREDICTION_SET",
                        "error_type": type(error).__name__,
                        "exit_code": EXIT_NO_PREDICTION,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return EXIT_NO_PREDICTION

        rendered = render_morning_email(
            payload,
            sender=sender_address,
            recipient=recipient,
            top_n=args.top_n,
        )
        stamp = datetime.now(ZoneInfo("Asia/Tokyo"))
        with_distribution = sum(1 for item in payload.candidates if item.distribution)
        message = RenderedEmail(
            subject=f"【テスト送信】{rendered.subject}",
            text=(
                "これはテスト送信です。定時の予測メールではありません。\n"
                f"送信時刻 {stamp:%Y-%m-%d %H:%M} JST\n"
                f"分布を持つ銘柄 {with_distribution} / {len(payload.candidates)}\n"
                f"{'-' * 60}\n\n{rendered.text}"
            ),
            html=(
                "<p style='margin:0 0 14px;padding:10px 12px;background:#fef3c7;"
                "border-radius:6px;color:#92400e;font-size:13px'>"
                "これは<strong>テスト送信</strong>です。定時の予測メールではありません。"
                f"（{stamp:%Y-%m-%d %H:%M} JST／分布を持つ銘柄 "
                f"{with_distribution} / {len(payload.candidates)}）</p>"
                f"{rendered.html}"
            ),
            sender=sender_address,
            recipient=recipient,
            idempotency_key=f"morning-test/{payload.prediction_date}/{stamp:%Y%m%d%H%M%S}",
        )

        if args.dry_run:
            print(message.text)
            return EXIT_OK

        email_sender = _sender(environment)
        try:
            delivery = email_sender.send(message)
        except Exception as error:
            print(
                json.dumps(
                    {
                        "status": "FAILED",
                        "reason": "EMAIL_DELIVERY_FAILED",
                        "error_type": type(error).__name__,
                        "exit_code": EXIT_SEND_FAILED,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return EXIT_SEND_FAILED
        finally:
            close = getattr(email_sender, "close", None)
            if callable(close):
                close()

        print(
            json.dumps(
                {
                    "status": "SUCCESS",
                    "outcome": "TEST_SENT",
                    "prediction_date": payload.prediction_date.isoformat(),
                    "prediction_set_id": prediction_set.prediction_set_id,
                    "model_version": payload.model_version,
                    "candidates": len(payload.candidates),
                    "with_distribution": with_distribution,
                    "provider": delivery.provider,
                    "message_id": delivery.message_id,
                    "exit_code": EXIT_OK,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return EXIT_OK
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
