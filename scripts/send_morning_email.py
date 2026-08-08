#!/usr/bin/env python3
"""Send one persisted prediction email through free Gmail SMTP by default."""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from notifications.templates import render_morning_email
from scripts.runtime import load_runtime
from services.email import load_morning_email_payload, send_persisted_morning_email


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--dry-run", action="store_true")
    return parser


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
        delivery = send_persisted_morning_email(
            factory,
            config,
            environment,
            prediction_date=args.prediction_date,
            top_n=args.top_n,
        )
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
