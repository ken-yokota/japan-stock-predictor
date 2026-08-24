#!/usr/bin/env python3
"""Mail one day's settled result: what was bought, what happened, what it means.

The morning mail says what the model expects; this says what actually occurred.
The tables themselves live in ``notifications.result_report`` because the 17:00
evening summary reports the same day and must read identically -- this script
is the manual, per-date way to send it again.

Usage:
    python -m scripts.send_result_email --prediction-date 2026-08-03
    python -m scripts.send_result_email --prediction-date 2026-08-03 --dry-run
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from data.config import load_app_config
from data.env import EnvironmentSettings
from database.connection import create_database_engine
from notifications.report_layout import page
from notifications.result_report import (
    DayResult,
    lede,
    load_day_result,
    plain_lines,
    result_sections,
    subject,
)


def _names() -> dict[str, str]:
    config = load_app_config()
    return {stock.ticker: stock.name for stock in config.stocks.stocks}


def build(result: DayResult, names: dict[str, str]) -> tuple[str, str, str]:
    """Return (subject, text, html) for one settled day."""

    html_body = page(
        f"日本株AI結果　{result.day:%Y-%m-%d}",
        lede(result),
        result_sections(result, names),
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。",
    )
    return subject(result), "\n".join(plain_lines(result, names)), html_body


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prediction-date", type=date.fromisoformat, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main() -> int:
    args = _parser().parse_args()
    environment = EnvironmentSettings()
    engine = create_database_engine(environment.require_database_url())
    try:
        result = load_day_result(engine, args.prediction_date)
    finally:
        engine.dispose()
    if result is None:
        print(
            json.dumps(
                {
                    "status": "NO_SETTLED_RESULT",
                    "prediction_date": args.prediction_date.isoformat(),
                },
                ensure_ascii=False,
            )
        )
        return 0
    subject_line, text_body, html_body = build(result, _names())
    if args.output is not None:
        args.output.write_text(html_body, encoding="utf-8")
    if args.dry_run:
        print(
            json.dumps(
                {"status": "DRY_RUN", "subject": subject_line}, ensure_ascii=False
            )
        )
        return 0

    from scripts.send_status_report import send_rendered

    provider = send_rendered(subject_line, text_body, html_body)
    print(
        json.dumps(
            {"status": "SENT", "provider": provider, "subject": subject_line},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
