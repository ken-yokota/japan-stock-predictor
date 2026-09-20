"""Render and send isolated operator TEST messages from read-only saved data."""

from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from sqlalchemy import Engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from data.config import load_app_config
from data.env import EnvironmentSettings
from database.connection import create_database_engine
from notifications.contracts import MorningEmailPayload, RenderedEmail
from notifications.method_thresholds import arm_verdict
from notifications.result_report import load_day_result
from notifications.templates import render_morning_email
from scripts.send_result_email import build
from services.email import _sender, load_morning_email_payload


def _evidence(payload: MorningEmailPayload) -> tuple[str, str]:
    lines = ["Morning predictionとの照合 / Data Quality / Positive・Negative Factors"]
    rows = []
    for item in payload.candidates:
        models = (
            ", ".join(
                f"{a.get('name', 'unknown')}:"
                f"{arm_verdict(a, payload.method_thresholds)[0]}"
                for a in item.arms
            )
            or "モデル別記録なし"
        )
        parts = [
            item.ticker,
            item.company,
            item.data_quality,
            "Positive: " + (", ".join(item.positive_factors) or "—"),
            "Negative: " + (", ".join(item.negative_factors) or "—"),
            models,
        ]
        lines.append(" | ".join(parts))
        rows.append(
            "<tr>" + "".join(f"<td>{html.escape(p)}</td>" for p in parts) + "</tr>"
        )
    return "\n".join(lines), (
        "<h2>Morning照合 / Data Quality / Factors / Models</h2>"
        "<table style='font-size:11px;width:100%'>" + "".join(rows) + "</table>"
    )


def validate_message(
    message: RenderedEmail, payload: MorningEmailPayload, expected: set[str]
) -> None:
    if {c.ticker for c in payload.candidates} != expected:
        raise ValueError("TEST requires every configured ticker exactly")
    if len(payload.candidates) != len(expected):
        raise ValueError("duplicate ticker in TEST message")
    if not message.subject.startswith("[TEST]["):
        raise ValueError("TEST subject missing")
    for body in (message.text, message.html):
        if re.search(r"\b(?:NaN|None|nan)\b", body):
            raise ValueError("non-finite or missing-value literal in TEST body")
        if not all(ticker in body for ticker in expected):
            raise ValueError("TEST body omits configured ticker")
    if "<html" not in message.html or "</html>" not in message.html:
        raise ValueError("incomplete HTML document")


def enforce_read_only(engine: Engine) -> None:
    """Reject accidental delivery-state writes at the database boundary."""

    @event.listens_for(engine, "begin")
    def read_only(connection: Connection) -> None:
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        elif connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA query_only=ON")


def main(kind: Literal["Morning", "Close"] = "Morning") -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat)
    parser.add_argument("--prediction-set-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/test_email"))
    args = parser.parse_args()
    environment = EnvironmentSettings()
    engine = create_database_engine(environment.reporting_database_url())

    # Every SQL transaction in this command is mechanically read-only. In
    # particular it never claims a production delivery idempotency key.
    enforce_read_only(engine)

    try:
        config = load_app_config(args.config_dir)
        sender, recipient = environment.require_test_email_addresses()
        with Session(engine) as session:
            selected, payload = load_morning_email_payload(
                session,
                config,
                prediction_date=args.prediction_date,
                dashboard_url=environment.app_url,
                prediction_set_id=args.prediction_set_id,
            )
            set_id = selected.prediction_set_id
        rendered = render_morning_email(payload, sender=sender, recipient=recipient)
        if kind == "Close":
            result = load_day_result(
                engine, payload.prediction_date, prediction_set_id=set_id
            )
            if result is None or len(result.items) != len(payload.candidates):
                raise ValueError("matching complete settled results unavailable")
            subject, text_body, html_body = build(
                result, {s.ticker: s.name for s in config.stocks.stocks}
            )
            evidence_text, evidence_html = _evidence(payload)
            rendered = replace(
                rendered,
                subject=subject,
                text=text_body + "\n\n" + evidence_text,
                html=html_body.replace("</body>", evidence_html + "</body>"),
            )
        stamp = datetime.now(UTC).isoformat()
        message = replace(
            rendered,
            subject=f"[TEST][{kind}] {rendered.subject}",
            text=f"TEST送信 / production delivery state未変更 / {stamp}\n"
            + rendered.text,
            html=rendered.html.replace(
                "</body>", "<p>TEST送信：定時配信ではありません。</p></body>"
            ),
            idempotency_key=f"test/{kind.lower()}/{payload.prediction_date}/{uuid4()}",
        )
        validate_message(
            message, payload, {s.ticker for s in config.stocks.stocks if s.enabled}
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stem = args.output_dir / f"{kind.lower()}-{payload.prediction_date}"
        stem.with_suffix(".html").write_text(message.html)
        stem.with_suffix(".txt").write_text(message.text)
        status = "RENDERED"
        provider = None
        if not args.dry_run:
            if environment.email_provider == "dry_run":
                raise ValueError("dry_run provider cannot prove actual delivery")
            transport = _sender(environment)
            try:
                delivery = transport.send(message)
                status, provider = "SENT_ACCEPTED", delivery.provider
            finally:
                close = getattr(transport, "close", None)
                if callable(close):
                    close()
        receipt = {
            "status": status,
            "kind": kind,
            "provider": provider,
            "prediction_date": str(payload.prediction_date),
            "prediction_set_id": set_id,
            "candidates": len(payload.candidates),
            "html_bytes": len(message.html.encode()),
            "production_writes": 0,
            "recipient_source": "TEST_EMAIL_TO"
            if environment.test_email_to
            else "configured_operator_EMAIL_TO",
            "delivery_verified": False,
            "at": stamp,
        }
        stem.with_suffix(".receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        print(json.dumps(receipt))
        return 0
    except Exception as error:
        print(
            json.dumps(
                {"status": "FAILED", "kind": kind, "error_type": type(error).__name__}
            )
        )
        return 1
    finally:
        engine.dispose()
