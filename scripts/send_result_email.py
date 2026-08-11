#!/usr/bin/env python3
"""Mail one day's settled result: what was bought, what happened, what it means.

The morning mail says what the model expects; this says what actually
occurred. Both are built from notifications/report_layout so they read the
same way, and both refuse to let a day's numbers stand without the sentence
that says how little a single day proves.

Usage:
    python -m scripts.send_result_email --prediction-date 2026-08-03
    python -m scripts.send_result_email --prediction-date 2026-08-03 --dry-run
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from data.config import load_app_config
from data.env import EnvironmentSettings
from database.connection import create_database_engine
from notifications.report_layout import (
    BAND,
    DOWN,
    GOOD_BG,
    MUTED,
    UP,
    cell,
    key_values,
    page,
    row,
    section,
    signed_percent,
    signed_yen,
    table,
)

RESULT_QUERY = """
    SELECT p.ticker, p.signal, p.predicted_intraday_return, p.probability_up,
           p.return_threshold, p.probability_threshold,
           a.actual_intraday_return, a.actual_open, a.actual_close,
           t.net_profit_jpy, ps.warnings
    FROM predictions AS p
    JOIN prediction_sets AS ps ON ps.prediction_set_id = p.prediction_set_id
    JOIN actual_results AS a ON a.prediction_id = p.prediction_id
    LEFT JOIN simulated_trades AS t ON t.prediction_id = p.prediction_id
    WHERE ps.prediction_date = :day
    ORDER BY p.predicted_intraday_return DESC
"""


def _rows(engine: Engine, day: date) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        result = connection.execute(text(RESULT_QUERY), {"day": day})
        return [dict(item) for item in result.mappings()]


def _names() -> dict[str, str]:
    config = load_app_config()
    return {stock.ticker: stock.name for stock in config.stocks.stocks}


def _label(ticker: str, names: dict[str, str]) -> str:
    return (
        f"<strong>{ticker}</strong> "
        f"<span style='color:{MUTED}'>{names.get(ticker, '')}</span>"
    )


def _verdict(correct: bool) -> str:
    tone, word = (UP, "的中") if correct else (DOWN, "外れ")
    return f"<span style='color:{tone};font-weight:700'>{word}</span>"


def _skip_reason(item: dict[str, Any]) -> str:
    predicted = float(item["predicted_intraday_return"])
    probability = float(item["probability_up"])
    threshold = float(item["return_threshold"] or 0)
    probability_threshold = float(item["probability_threshold"] or 0)
    if predicted < threshold:
        return f"予測が{threshold:.1%}未満"
    if probability < probability_threshold:
        return f"確率が{probability * 100:.0f}%で閾値{probability_threshold:.0%}未満"
    return "—"


def build(day: date, items: list[dict[str, Any]]) -> tuple[str, str, str] | None:
    """Return (subject, text, html), or None when the day has not settled."""

    if not items:
        return None
    names = _names()
    buys = [item for item in items if item["signal"] == "BUY"]

    def correct(item: dict[str, Any]) -> bool:
        return (float(item["predicted_intraday_return"]) > 0) == (
            float(item["actual_intraday_return"]) > 0
        )

    hits = sum(1 for item in items if correct(item))
    buy_hits = sum(1 for item in buys if correct(item))
    profit = sum(float(item["net_profit_jpy"] or 0) for item in buys)
    buy_return = (
        sum(float(item["actual_intraday_return"]) for item in buys) / len(buys)
        if buys
        else 0.0
    )
    all_return = sum(float(item["actual_intraday_return"]) for item in items) / len(
        items
    )
    warnings = items[0].get("warnings") or []

    score = table(
        [
            ("区分", "left"),
            ("銘柄数", "center"),
            ("方向的中", "center"),
            ("平均実績", "right"),
            ("合計損益", "right"),
        ],
        [
            row(
                [
                    cell("<strong>買った銘柄</strong>"),
                    cell(str(len(buys)), align="center"),
                    cell(
                        f"<strong>{buy_hits}/{len(buys)}</strong>" if buys else "—",
                        align="center",
                    ),
                    cell(signed_percent(buy_return, 3) if buys else "—", align="right"),
                    cell(signed_yen(profit) if buys else "—", align="right"),
                ],
                GOOD_BG,
            ),
            row(
                [
                    cell("全銘柄", muted=True),
                    cell(str(len(items)), align="center", muted=True),
                    cell(f"{hits}/{len(items)}", align="center", muted=True),
                    cell(signed_percent(all_return, 3), align="right"),
                    cell("—", align="right", muted=True),
                ]
            ),
        ],
        min_width=500,
    )

    traded = (
        table(
            [
                ("銘柄", "left"),
                ("予測", "right"),
                ("実績", "right"),
                ("判定", "center"),
                ("始値 → 終値", "right"),
                ("損益", "right"),
            ],
            [
                row(
                    [
                        cell(_label(item["ticker"], names)),
                        cell(
                            signed_percent(float(item["predicted_intraday_return"])),
                            align="right",
                        ),
                        cell(
                            signed_percent(float(item["actual_intraday_return"])),
                            align="right",
                        ),
                        cell(_verdict(correct(item)), align="center"),
                        cell(
                            f"{float(item['actual_open']):,.1f} → "
                            f"{float(item['actual_close']):,.1f}",
                            align="right",
                            muted=True,
                        ),
                        cell(
                            signed_yen(float(item["net_profit_jpy"] or 0)),
                            align="right",
                        ),
                    ]
                )
                for item in sorted(
                    buys, key=lambda entry: -float(entry["net_profit_jpy"] or 0)
                )
            ],
            min_width=560,
        )
        if buys
        else ""
    )

    skipped = [item for item in items if item["signal"] != "BUY"]
    passed = (
        table(
            [
                ("銘柄", "left"),
                ("予測", "right"),
                ("実績", "right"),
                ("判定", "center"),
                ("見送りの理由", "left"),
            ],
            [
                row(
                    [
                        cell(_label(item["ticker"], names)),
                        cell(
                            signed_percent(float(item["predicted_intraday_return"])),
                            align="right",
                        ),
                        cell(
                            signed_percent(float(item["actual_intraday_return"])),
                            align="right",
                        ),
                        cell(_verdict(correct(item)), align="center"),
                        cell(_skip_reason(item), muted=True, nowrap=False),
                    ],
                    "#fff" if index % 2 == 0 else BAND,
                )
                for index, item in enumerate(
                    sorted(
                        skipped,
                        key=lambda entry: -float(entry["actual_intraday_return"]),
                    )
                )
            ],
            min_width=540,
        )
        if skipped
        else ""
    )

    blocks = [section("成績", score)]
    if traded:
        blocks.append(section("買った銘柄の結果", traded))
    if passed:
        blocks.append(section("買わなかった銘柄", passed))
    if warnings:
        blocks.append(
            section(
                "この予測に付いている注記",
                key_values([("注記", str(item)) for item in warnings]),
            )
        )
    blocks.append(
        section(
            "この数字が示していないこと",
            "",
            f"1日・{len(buys)}取引です。これだけでは優位性の有無を判断できません。"
            "モデルが良いのか、その日の相場が良かったのかを区別する方法が"
            "この標本数にはありません。意味のある判断には最低でも"
            "数十営業日の蓄積が必要です。",
        )
    )

    subject = (
        f"【日本株AI結果】{day:%Y-%m-%d} 答え合わせ／買い{len(buys)}銘柄 "
        f"{buy_hits}勝{len(buys) - buy_hits}敗 {profit:+,.0f}円"
    )
    html_body = page(
        f"日本株AI結果　{day:%Y-%m-%d}",
        f"買い{len(buys)}銘柄 {buy_hits}勝{len(buys) - buy_hits}敗"
        f"　|　合計 {profit:+,.0f}円　|　確定{len(items)}銘柄",
        blocks,
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。",
    )
    text_body = "\n".join(
        [
            f"対象日: {day:%Y-%m-%d}",
            f"買った銘柄: {len(buys)}件 {buy_hits}勝{len(buys) - buy_hits}敗 "
            f"合計 {profit:+,.0f}円",
            f"全銘柄の方向的中: {hits}/{len(items)}",
            f"全銘柄の平均実績: {all_return:+.3%}",
            "",
            *(f"注記: {item}" for item in warnings),
            "",
            f"1日・{len(buys)}取引では優位性の有無を判断できません。",
        ]
    )
    return subject, text_body, html_body


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
        built = build(args.prediction_date, _rows(engine, args.prediction_date))
    finally:
        engine.dispose()
    if built is None:
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
    subject, text_body, html_body = built
    if args.output is not None:
        args.output.write_text(html_body, encoding="utf-8")
    if args.dry_run:
        print(json.dumps({"status": "DRY_RUN", "subject": subject}, ensure_ascii=False))
        return 0

    from scripts.send_status_report import send_rendered

    provider = send_rendered(subject, text_body, html_body)
    print(
        json.dumps(
            {"status": "SENT", "provider": provider, "subject": subject},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
