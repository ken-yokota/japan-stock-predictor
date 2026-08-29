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
    FORECAST,
    GOOD_BG,
    MUTED,
    UP,
    cell,
    diverging_bar,
    key_values,
    legend,
    ratio_bar,
    row,
    section,
    signed_percent,
    signed_yen,
    stacked_bars,
    table,
)
from services.versioning import STRATEGY_VERSION

# Below this many trades a win rate is a coin-count, not evidence. The same
# floor the dashboard and the comparison runner use.
MINIMUM_TRADES_FOR_EVIDENCE = 20

# A corrected close writes a *new* actual_results row rather than editing the
# old one, and a new simulated_trades row is valued against it. Joining both on
# prediction_id alone therefore returns two results times two trades -- four
# rows for one prediction -- and the day's P&L is counted four times.
#
# It happened: 2026-08-20's after-close mail reported +96,081 JPY for a day that
# actually made +86,170, because 8053 was corrected. So both queries take the
# current result -- the one no other row supersedes -- and the trade valued
# against that same result.
# A re-valuation under a new strategy label also lands beside the old one -- see
# scripts/revalue_trades.py -- so the trade has to be pinned to the running
# label as well, or zeroing the costs would double every figure the same way a
# correction did.
CURRENT_RESULT = """
          AND NOT EXISTS (
              SELECT 1 FROM actual_results AS superseding
              WHERE superseding.supersedes_actual_result_id = a.actual_result_id
          )
"""

# The strategy label belongs in the JOIN, not the WHERE. In the WHERE it drops
# the prediction entirely when its only trade carries an older label, instead of
# showing the prediction with no trade against it -- which silently hides rows
# rather than showing them unvalued.
TRADE_JOIN = """
    LEFT JOIN simulated_trades AS t
      ON t.actual_result_id = a.actual_result_id
     AND t.strategy_version = :strategy
"""


RESULT_QUERY = """
    SELECT p.ticker, p.signal, p.predicted_intraday_return, p.probability_up,
           p.return_threshold, p.probability_threshold,
           a.actual_intraday_return, a.actual_open, a.actual_close,
           t.net_profit_jpy, ps.warnings
    FROM predictions AS p
    JOIN prediction_sets AS ps ON ps.prediction_set_id = p.prediction_set_id
    JOIN actual_results AS a ON a.prediction_id = p.prediction_id
""" + TRADE_JOIN + """
    WHERE ps.prediction_date = :day
""" + CURRENT_RESULT + """
    ORDER BY p.predicted_intraday_return DESC
"""


HISTORY_QUERY = """
    SELECT ps.prediction_date AS day,
           count(*) FILTER (WHERE p.signal = 'BUY') AS buys,
           count(*) FILTER (
               WHERE p.signal = 'BUY'
                 AND (p.predicted_intraday_return > 0)
                     = (a.actual_intraday_return > 0)
           ) AS buy_hits,
           count(*) AS predicted,
           count(*) FILTER (
               WHERE (p.predicted_intraday_return > 0)
                     = (a.actual_intraday_return > 0)
           ) AS hits,
           coalesce(
               sum(t.net_profit_jpy) FILTER (WHERE p.signal = 'BUY'), 0
           ) AS profit
    FROM prediction_sets AS ps
    JOIN predictions AS p ON p.prediction_set_id = ps.prediction_set_id
    JOIN actual_results AS a ON a.prediction_id = p.prediction_id
""" + TRADE_JOIN + """
    WHERE ps.prediction_date <= :day
""" + CURRENT_RESULT + """
    GROUP BY ps.prediction_date
    ORDER BY ps.prediction_date DESC
    LIMIT :limit
"""


@dataclass(frozen=True, slots=True)
class DaySummary:
    """One settled day, reduced to the numbers a trend is drawn from."""

    day: date
    buys: int
    buy_hits: int
    predicted: int
    hits: int
    profit: float

    @property
    def hit_rate(self) -> float | None:
        return self.hits / self.predicted if self.predicted else None


def load_history(
    engine: Engine, day: date, *, limit: int = 10
) -> tuple[DaySummary, ...]:
    """The settled days up to and including ``day``, oldest first.

    One day says nothing, and the mail says so; a run of them at least shows
    the direction. Read here rather than remembered, like everything else.
    """

    with engine.connect() as connection:
        rows = connection.execute(
            text(HISTORY_QUERY),
            {"day": day, "limit": limit, "strategy": STRATEGY_VERSION},
        ).mappings()
        summaries = [
            DaySummary(
                day=item["day"],
                buys=int(item["buys"]),
                buy_hits=int(item["buy_hits"]),
                predicted=int(item["predicted"]),
                hits=int(item["hits"]),
                profit=float(item["profit"] or 0),
            )
            for item in rows
        ]
    return tuple(reversed(summaries))


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
            for item in connection.execute(
                text(RESULT_QUERY),
                {"day": day, "strategy": STRATEGY_VERSION},
            ).mappings()
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


def result_sections(
    result: DayResult,
    names: dict[str, str],
    history: Sequence[DaySummary] = (),
) -> list[str]:
    """The day's answer-check: score, the figures, what was bought, the caveat."""

    blocks = [section("本日の成績", score_table(result))]
    forecast = forecast_vs_actual_figure(result, names)
    if forecast:
        blocks.append(
            section(
                "図: 本日の予測と実績",
                forecast,
                "上段が予測、下段が実績です。同じ中心線・同じ目盛りなので、"
                "2本の差がそのまま外し方の大きさになります。",
            )
        )
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
    profit_figure = profit_history_figure(history)
    if profit_figure:
        blocks.append(
            section(
                f"図: 直近{len(history)}営業日の損益と累積",
                profit_figure,
                "棒は累積損益です。最下行が本日。"
                "日次の損益は数字の列に出しています。",
            )
        )
    rate_figure = hit_rate_figure(history)
    if rate_figure:
        blocks.append(
            section(
                "図: 方向的中率の推移（全銘柄・基準50%）",
                rate_figure,
                "全銘柄の方向が合っていた割合です。"
                "50%はコイン投げで、それを下回る日は方向自体が逆でした。",
            )
        )
    if history:
        blocks.append(
            section("この推移が示していないこと", "", history_caveat(history))
        )
    blocks.append(section("この数字が示していないこと", "", caveat_text(result)))
    return blocks


# --- Figures -------------------------------------------------------------


def forecast_vs_actual_figure(result: DayResult, names: dict[str, str]) -> str:
    """Where the model was wrong, at a glance.

    The forecast sits directly above what happened, both measured from the same
    centre rule and the same ruler, so the gap between the two bars is the
    error. The forecast is slate rather than green or red: it is a claim, not
    an outcome, and colouring it like an outcome invites reading it as one.
    """

    items = result.buys or result.items
    if not items:
        return ""
    scale = max(
        (
            max(
                abs(float(item["predicted_intraday_return"])),
                abs(float(item["actual_intraday_return"])),
            )
            for item in items
        ),
        default=0.0,
    )
    rows = [
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
                cell(
                    stacked_bars(
                        [
                            (float(item["predicted_intraday_return"]), FORECAST),
                            (float(item["actual_intraday_return"]), None),
                        ],
                        scale,
                    ),
                    nowrap=False,
                ),
            ],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, item in enumerate(
            sorted(items, key=lambda entry: -float(entry["actual_intraday_return"]))
        )
    ]
    key = legend([("予測", FORECAST), ("実績プラス", UP), ("実績マイナス", DOWN)])
    return key + table(
        [("銘柄", "left"), ("予測", "right"), ("実績", "right"), ("図", "left")],
        rows,
        min_width=520,
    )


def profit_history_figure(history: Sequence[DaySummary]) -> str:
    """The day's loss beside every other settled day, and the running total."""

    if not history:
        return ""
    running = 0.0
    cumulative: list[float] = []
    for item in history:
        running += item.profit
        cumulative.append(running)
    scale = max((abs(value) for value in cumulative), default=0.0)
    rows = [
        row(
            [
                cell(f"{item.day:%m/%d}", align="center"),
                cell(
                    f"{item.buys}件" if item.buys else "—",
                    align="right",
                    muted=not item.buys,
                ),
                cell(signed_yen(item.profit), align="right"),
                cell(signed_yen(total), align="right"),
                cell(diverging_bar(total, scale), nowrap=False),
            ],
            GOOD_BG
            if index == len(history) - 1
            else ("#fff" if index % 2 == 0 else BAND),
        )
        for index, (item, total) in enumerate(zip(history, cumulative, strict=True))
    ]
    return legend([("累積プラス", UP), ("累積マイナス", DOWN)]) + table(
        [
            ("日付", "center"),
            ("買い", "right"),
            ("日次損益", "right"),
            ("累積", "right"),
            ("累積の図", "left"),
        ],
        rows,
        min_width=580,
    )


def hit_rate_figure(history: Sequence[DaySummary]) -> str:
    """Direction accuracy against the only threshold that means anything: 50%."""

    if not history:
        return ""
    rows = [
        row(
            [
                cell(f"{item.day:%m/%d}", align="center"),
                cell(f"{item.hits}/{item.predicted}", align="right"),
                cell(
                    f"{item.hit_rate:.0%}" if item.hit_rate is not None else "—",
                    align="right",
                ),
                cell(ratio_bar(item.hit_rate), nowrap=False),
            ],
            GOOD_BG
            if index == len(history) - 1
            else ("#fff" if index % 2 == 0 else BAND),
        )
        for index, item in enumerate(history)
    ]
    return legend([("50%以上", UP), ("50%未満", DOWN)]) + table(
        [("日付", "center"), ("的中", "right"), ("的中率", "right"), ("図", "left")],
        rows,
        min_width=520,
    )


def history_caveat(history: Sequence[DaySummary]) -> str:
    """What the trend does not establish, stated with the sample it rests on."""

    if not history:
        return "確定した日がまだありません。"
    trades = sum(item.buys for item in history)
    total = sum(item.profit for item in history)
    predicted = sum(item.predicted for item in history)
    hits = sum(item.hits for item in history)
    rate = hits / predicted if predicted else 0.0
    return (
        f"{len(history)}営業日・買い{trades}取引の累積で {total:+,.0f}円、"
        f"方向的中は{hits}/{predicted}（{rate:.0%}）です。"
        f"{len(history)}日では相場の地合いとモデルの優劣を分離できません。"
        "この図は方向を見るためのもので、優位性の証拠ではありません。"
    )


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
