"""The settled day's answer-check, in the layout the operator approved.

Two mails report the same trading day: the manual result mail and the 17:00
evening summary that goes out after the close. They used to build it
separately, and the evening one drifted into a ``<pre>`` block of prose while
the other kept its coloured tables -- so the mail the operator actually
receives every weekday was the unreadable one. The tables live here now, and
both mails are assembled from them, so neither can drift alone.

Nothing in this module recomputes a prediction. It reads what the close
pipeline already wrote and arranges it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from notifications.report_layout import (
    BAND,
    DOWN,
    GOOD_BG,
    MUTED,
    UP,
    cell,
    key_values,
    row,
    section,
    signed_percent,
    signed_yen,
    table,
)

# Below this many trades a win rate is a coin-count, not evidence. The same
# floor the dashboard and the comparison runner use.
MINIMUM_TRADES_FOR_EVIDENCE = 20

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


def _correct(item: dict[str, Any]) -> bool:
    """Direction agreement: the only claim a single day can support."""

    return (float(item["predicted_intraday_return"]) > 0) == (
        float(item["actual_intraday_return"]) > 0
    )


@dataclass(frozen=True, slots=True)
class DayResult:
    """One day's settled outcome, with every number beside its sample size."""

    day: date
    items: tuple[dict[str, Any], ...]

    @property
    def buys(self) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in self.items if item["signal"] == "BUY")

    @property
    def skipped(self) -> tuple[dict[str, Any], ...]:
        return tuple(item for item in self.items if item["signal"] != "BUY")

    @property
    def hits(self) -> int:
        return sum(1 for item in self.items if _correct(item))

    @property
    def buy_hits(self) -> int:
        return sum(1 for item in self.buys if _correct(item))

    @property
    def profit(self) -> float:
        return sum(float(item["net_profit_jpy"] or 0) for item in self.buys)

    @property
    def buy_return(self) -> float:
        buys = self.buys
        if not buys:
            return 0.0
        return sum(float(item["actual_intraday_return"]) for item in buys) / len(buys)

    @property
    def all_return(self) -> float:
        if not self.items:
            return 0.0
        return sum(
            float(item["actual_intraday_return"]) for item in self.items
        ) / len(self.items)

    @property
    def warnings(self) -> tuple[str, ...]:
        if not self.items:
            return ()
        raw = self.items[0].get("warnings") or []
        return tuple(str(item) for item in raw)


def load_day_result(engine: Engine, day: date) -> DayResult | None:
    """Read one day's settled rows, or ``None`` when the day has not settled."""

    with engine.connect() as connection:
        rows = [
            dict(item)
            for item in connection.execute(text(RESULT_QUERY), {"day": day}).mappings()
        ]
    return DayResult(day, tuple(rows)) if rows else None


def _label(ticker: str, names: dict[str, str]) -> str:
    return (
        f"<strong>{ticker}</strong> "
        f"<span style='color:{MUTED}'>{names.get(ticker, '')}</span>"
    )


def _verdict(correct: bool) -> str:
    """Word first, colour second: some clients strip the style and keep the text."""

    tone, word = (UP, "的中") if correct else (DOWN, "外れ")
    return f"<span style='color:{tone};font-weight:700'>{word}</span>"


def skip_reason(item: dict[str, Any]) -> str:
    """Which threshold this one missed, and by how much."""

    predicted = float(item["predicted_intraday_return"])
    probability = float(item["probability_up"])
    threshold = float(item["return_threshold"] or 0)
    probability_threshold = float(item["probability_threshold"] or 0)
    if predicted < threshold:
        return f"予測が{threshold:.1%}未満"
    if probability < probability_threshold:
        return f"確率が{probability * 100:.0f}%で閾値{probability_threshold:.0%}未満"
    return "—"


def score_table(result: DayResult) -> str:
    """The decision table: bought versus everything, side by side."""

    buys = result.buys
    return table(
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
                        f"<strong>{result.buy_hits}/{len(buys)}</strong>"
                        if buys
                        else "—",
                        align="center",
                    ),
                    cell(
                        signed_percent(result.buy_return, 3) if buys else "—",
                        align="right",
                    ),
                    cell(
                        signed_yen(result.profit) if buys else "—",
                        align="right",
                    ),
                ],
                GOOD_BG,
            ),
            row(
                [
                    cell("全銘柄", muted=True),
                    cell(str(len(result.items)), align="center", muted=True),
                    cell(
                        f"{result.hits}/{len(result.items)}",
                        align="center",
                        muted=True,
                    ),
                    cell(signed_percent(result.all_return, 3), align="right"),
                    cell("—", align="right", muted=True),
                ]
            ),
        ],
        min_width=500,
    )


def traded_table(result: DayResult, names: dict[str, str]) -> str:
    """Every bought ticker, worst loss last, with the prices it moved between."""

    buys = result.buys
    if not buys:
        return ""
    return table(
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
                    cell(_verdict(_correct(item)), align="center"),
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
                ],
                "#fff" if index % 2 == 0 else BAND,
            )
            for index, item in enumerate(
                sorted(buys, key=lambda entry: -float(entry["net_profit_jpy"] or 0))
            )
        ],
        min_width=560,
    )


def skipped_table(result: DayResult, names: dict[str, str]) -> str:
    """What was passed over, best actual first, each with the threshold it missed."""

    skipped = result.skipped
    if not skipped:
        return ""
    return table(
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
                    cell(_verdict(_correct(item)), align="center"),
                    cell(skip_reason(item), muted=True, nowrap=False),
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


def caveat_text(result: DayResult) -> str:
    """The sentence that stops one good day being read as an edge."""

    count = len(result.buys)
    line = (
        f"1日・{count}取引です。これだけでは優位性の有無を判断できません。"
        "モデルが良いのか、その日の相場が良かったのかを区別する方法が"
        "この標本数にはありません。"
    )
    if count < MINIMUM_TRADES_FOR_EVIDENCE:
        line += (
            f"勝率も損益も、{MINIMUM_TRADES_FOR_EVIDENCE}取引未満では"
            "証拠になりません。"
        )
    return line + "意味のある判断には最低でも数十営業日の蓄積が必要です。"


def result_sections(result: DayResult, names: dict[str, str]) -> list[str]:
    """The day's answer-check: score, what was bought, what was not, the caveat."""

    blocks = [section("本日の成績", score_table(result))]
    traded = traded_table(result, names)
    if traded:
        blocks.append(
            section(
                "買った銘柄の結果",
                traded,
                "損益の大きい順です。判定は方向が合っていたかどうかで、"
                "損益の正負とは一致しないことがあります。",
            )
        )
    skipped = skipped_table(result, names)
    if skipped:
        blocks.append(
            section(
                "買わなかった銘柄",
                skipped,
                "実績の良い順です。見送りの理由は、どの閾値に届かなかったかを"
                "示しています。",
            )
        )
    if result.warnings:
        blocks.append(
            section(
                "この予測に付いている注記",
                key_values([("注記", item) for item in result.warnings]),
            )
        )
    blocks.append(section("この数字が示していないこと", "", caveat_text(result)))
    return blocks


def subject(result: DayResult) -> str:
    """Win, loss and yen in the subject: the operator decides without opening it."""

    buys = result.buys
    return (
        f"【日本株AI結果】{result.day:%Y-%m-%d} 答え合わせ／買い{len(buys)}銘柄 "
        f"{result.buy_hits}勝{len(buys) - result.buy_hits}敗 {result.profit:+,.0f}円"
    )


def lede(result: DayResult) -> str:
    buys = result.buys
    return (
        f"買い{len(buys)}銘柄 {result.buy_hits}勝{len(buys) - result.buy_hits}敗"
        f"　|　合計 {result.profit:+,.0f}円　|　確定{len(result.items)}銘柄"
    )


def plain_lines(result: DayResult, names: dict[str, str]) -> list[str]:
    """The text/plain alternative, from the same numbers the tables use."""

    buys = result.buys
    lines = [
        f"対象日: {result.day:%Y-%m-%d}",
        f"買った銘柄: {len(buys)}件 {result.buy_hits}勝"
        f"{len(buys) - result.buy_hits}敗 合計 {result.profit:+,.0f}円",
        f"全銘柄の方向的中: {result.hits}/{len(result.items)}",
        f"全銘柄の平均実績: {result.all_return:+.3%}",
    ]
    if buys:
        lines.append("")
        lines.append("  銘柄      予測      実績      判定  損益")
        lines.append("  --------  --------  --------  ----  ----------")
        for item in sorted(
            buys, key=lambda entry: -float(entry["net_profit_jpy"] or 0)
        ):
            ticker = str(item["ticker"])
            name = names.get(ticker, "")
            lines.append(
                f"  {ticker:<8}  "
                f"{float(item['predicted_intraday_return']):+7.2%}  "
                f"{float(item['actual_intraday_return']):+7.2%}  "
                f"{'的中' if _correct(item) else '外れ':<4}  "
                f"{float(item['net_profit_jpy'] or 0):+9,.0f}円  {name}"
            )
    for note in result.warnings:
        lines.append(f"注記: {note}")
    lines.append("")
    lines.append(caveat_text(result))
    return lines


def no_result_section(day: date, reason: str) -> str:
    """Say plainly that the day did not settle, rather than sending nothing.

    A missing result is the report most worth receiving, and it is the one a
    mail keyed on "if there are rows" silently drops.
    """

    return section(
        "本日の成績",
        table(
            [("項目", "left"), ("内容", "right")],
            [
                row([cell("対象日"), cell(f"{day:%Y-%m-%d}", align="right")]),
                row(
                    [
                        cell("確定した実績"),
                        cell(
                            f"<span style='color:{DOWN};font-weight:700'>"
                            "ありません</span>",
                            align="right",
                        ),
                    ],
                    BAND,
                ),
                row([cell("理由"), cell(reason, align="right", nowrap=False)]),
            ],
            min_width=440,
        ),
        "答え合わせができていません。実績が確定していない日は、"
        "成績を空欄にするのではなくこの節でそう述べます。",
    )


def names_by_ticker(stocks: Sequence[Any]) -> dict[str, str]:
    return {stock.ticker: stock.name for stock in stocks}
