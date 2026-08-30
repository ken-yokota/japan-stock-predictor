"""HTML and plain-text morning prediction templates."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from collections.abc import Iterable, Sequence

from notifications.contracts import EmailCandidate, MorningEmailPayload, RenderedEmail
from notifications.report_layout import (
    BAND,
    BAND_INNER,
    BAND_OUTER,
    GOOD_BG,
    GRID,
    INK,
    MEDIAN_MARK,
    MUTED,
    badge,
    cell,
    density_axis,
    density_chart,
    density_row,
    distribution_bar,
    legend,
    page,
    section,
    signed_percent,
    table,
)

# The bands the mail reports, and what each is called in it. The outer pair is
# the 80% band and the inner the 50%; the middle level is the centre. The 5th
# and 95th percentiles are fitted and persisted but not shown as columns -- on
# a phone a seven-column table is unreadable, and the two outermost levels are
# the ones a 120-session window supports least.
LOWER_OUTER = 0.10
LOWER_INNER = 0.25
CENTRE = 0.50
UPPER_INNER = 0.75
UPPER_OUTER = 0.90

# The quantile levels the mail prints as columns. The model fits nineteen;
# printing all of them would be a spreadsheet, and these five are the ones
# a decision is read off.
REPORTED = (0.10, 0.25, 0.50, 0.75, 0.90)

# Width of the density plot in characters. Odd, so the middle column is
# exactly zero and the eye can find break-even without counting.
DENSITY_COLUMNS = 41

# What the fitted distribution's bands actually covered out of sample, and on
# how many observations. Printed in the mail rather than kept in a document,
# because the number that matters when reading an interval is how often the
# outcome really landed inside one.
COVERAGE_NOTE = (
    "実測被覆（OOS 5,500標本）: 名目80%の区間に実際に入ったのは75.5%、"
    "名目50%の区間には46.3%。分布はやや狭めに出ており、"
    "区間は実際よりわずかに楽観的です。"
)

READING_NOTE = (
    "中央値は分布の中心で、上下どちらに転ぶ確率も半々の水準です。"
    "80%区間は、当日の結果が10回のうちおよそ8回入ると見込まれる範囲です。"
    "点ではなく幅で読んでください。"
)

DENSITY_NOTE = (
    "山が高いところほど、その値で引けやすいという意味です。山が狭いほど自信のある予測、広く平たいほど当日の値動きが読めていません。"
    "山の細かいギザギザは読まないでください。学習窓は120営業日しかなく、"
    "1つの区切りあたり平均6営業日ぶんしか観測がないため、"
    "小さな凹凸は推定のばらつきであって、本当に山が2つあるという意味ではありません。"
    "読むべきなのは全体の位置と広がりです。"
    "描いてあるのは5%〜95%の範囲、つまり確率の90%分だけです。残りの上下5%ずつは120営業日の学習窓では位置を決められないため、意図的に描いていません。"
)


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2%}"


def _probability(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _number(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _yen(value: float | None) -> str:
    return "—" if value is None else f"¥{value:+,.0f}"


def _at(item: EmailCandidate, level: float) -> float | None:
    """One fitted level of a candidate's distribution, or ``None`` if absent."""

    for quantile, value in item.distribution:
        if abs(quantile - level) < 1e-9:
            return value
    return None


def _has_distribution(item: EmailCandidate) -> bool:
    return len(item.distribution) >= 2


def _band(item: EmailCandidate) -> tuple[float, float] | None:
    low, high = _at(item, LOWER_OUTER), _at(item, UPPER_OUTER)
    return None if low is None or high is None else (low, high)


def _scale(items: Sequence[EmailCandidate]) -> float:
    """One ruler for every row, taken from the widest distribution shown."""

    widest = [
        abs(value)
        for item in items
        for level in (LOWER_OUTER, UPPER_OUTER)
        if (value := _at(item, level)) is not None
    ]
    return max(widest) if widest else 0.0


def _fallback_notice(items: Sequence[EmailCandidate]) -> str:
    """Name the rows whose spread did not come from a fitted curve.

    The fallback is the same width for every ticker on every day, because it
    cannot vary the spread with the inputs. Presented without a label it would
    read as a forecast about this stock on this morning, which it is not.
    """

    fallback = [
        item for item in items if item.distribution_method == "residual_quantiles"
    ]
    if not fallback:
        return ""
    names = "、".join(f"{item.ticker} {item.company}" for item in fallback)
    return (
        f"⚠ {len(fallback)}銘柄は分位点回帰が解けず、残差から作った代替の分布です"
        f"（幅は日次・銘柄によらず一定になります）: {names}"
    )


def _missing_distribution_notice(items: Sequence[EmailCandidate]) -> str:
    missing = [item for item in items if not _has_distribution(item)]
    if not missing:
        return ""
    names = "、".join(f"{item.ticker} {item.company}" for item in missing)
    return f"⚠ {len(missing)}銘柄は分布がありません（点推定のみ）: {names}"


def _factor_text(values: Iterable[str]) -> str:
    escaped = [html.escape(value) for value in values]
    return ", ".join(escaped) if escaped else "—"


def _display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in value)


def _pad(value: str, width: int) -> str:
    """Pad to a *display* width, so a column of Japanese names still lines up."""

    return value + " " * max(width - _display_width(value), 0)


def _name(item: EmailCandidate) -> str:
    return _pad(f"{item.ticker} {item.company}", 22)


def _price(value: float | None) -> str:
    return "—".rjust(9) if value is None else f"{value:>9,.1f}"


def _as_price(reference: float | None, value: float | None) -> float | None:
    """Turn one quantile of the return distribution into a yen price."""

    if reference is None or value is None:
        return None
    return reference * (1.0 + value)


def _returntable(items: Sequence[EmailCandidate]) -> str:
    """The distribution of today's return, per buy candidate.

    Five columns rather than one number: the 80% band, the 50% band inside it,
    and the centre. The point forecast has its own table below, because it is
    the number the buy rule thresholds on and conflating the two would hide
    which of them made the decision.
    """

    header = (
        "  順位  銘柄                  下位10%   下位25%    中央値   上位25%   上位10%"
        "  P(上昇)\n"
        "  ----  --------------------  --------  --------  --------  --------  --------"
        "  -------"
    )
    rows = [
        f"  {(item.rank or index):>4}  {_name(item)}"
        f"{_percent(_at(item, LOWER_OUTER)):>8}  {_percent(_at(item, LOWER_INNER)):>8}"
        f"  {_percent(item.distribution_median):>8}"
        f"  {_percent(_at(item, UPPER_INNER)):>8}"
        f"  {_percent(_at(item, UPPER_OUTER)):>8}"
        f"  {_probability(item.distribution_probability_up):>7}"
        for index, item in enumerate(items, 1)
    ]
    return "\n".join([header, *rows])


def _density_peak(items: Sequence[EmailCandidate]) -> float:
    """The tallest column across every row drawn together.

    One peak for the figure, not one per row. Normalising each row to its own
    maximum would make every distribution look equally confident, which is the
    single most misleading thing a density plot can do.
    """

    return max((max(item.density, default=0.0) for item in items), default=0.0)


def _densitytext(items: Sequence[EmailCandidate]) -> str:
    """The forecast density per candidate, drawn in block characters."""

    drawable = [item for item in items if item.density and item.density_scale]
    if not drawable:
        return "  （分布がないため密度は描けません）"
    scale = max(item.density_scale or 0.0 for item in drawable)
    peak = _density_peak(drawable)
    ticks, labels = density_axis(scale, DENSITY_COLUMNS)
    lines = [f"  {ticks}", f"  {labels}"]
    for item in drawable:
        lines.append(f"  {density_row(item.density, peak)}")
        lines.append(
            f"  {item.ticker} {item.company}"
            f"（中央値 {_percent(item.distribution_median)} / "
            f"80%区間 {_band_text(item)} / "
            f"P(上昇) {_probability(item.distribution_probability_up)}）"
        )
        lines.append("")
    return "\n".join(lines).rstrip()


def _quantiletable(items: Sequence[EmailCandidate]) -> str:
    """Every reported quantile, as a column, for every candidate."""

    header = (
        "  銘柄                  "
        + "".join(f"{level:>8.0%}" + "  " for level in REPORTED).rstrip()
        + "\n  --------------------  "
        + "  ".join("--------" for _ in REPORTED)
    )
    rows = [
        "  "
        + _name(item)
        + "  ".join(f"{_percent(_at(item, level)):>8}" for level in REPORTED)
        for item in items
    ]
    return "\n".join([header, *rows])


def _pricetable(items: Sequence[EmailCandidate]) -> str:
    """The same distribution as prices, which is what the operator reads."""

    header = (
        "  銘柄                  前日終値   下位10%    中央値    上位10%\n"
        "  --------------------  ---------  ---------  ---------  ---------"
    )
    rows = []
    for item in items:
        reference = item.reference_price
        band = _band(item)
        rows.append(
            f"  {_name(item)}{_price(reference)}"
            f"  {_price(_as_price(reference, band[0] if band else None))}"
            f"  {_price(_as_price(reference, item.distribution_median))}"
            f"  {_price(_as_price(reference, band[1] if band else None))}"
        )
    return "\n".join([header, *rows])


def _decisiontable(items: Sequence[EmailCandidate]) -> str:
    """The two numbers the buy rule actually thresholds on.

    Kept separate and named, because they are not read off the distribution:
    the point forecast is the Ridge conditional mean and the probability is the
    logistic classifier's. The whole scored history is defined against these
    two, so they stay the decision variables and the distribution describes the
    uncertainty around them rather than silently replacing them.
    """

    header = (
        "  銘柄                  点予測(平均)  上昇確率  前日終値   予測終値\n"
        "  --------------------  ------------  --------  ---------  ---------"
    )
    rows = [
        f"  {_name(item)}{_percent(item.predicted_return):>12}"
        f"  {_probability(item.probability_up):>8}"
        f"  {_price(item.reference_price)}  {_price(item.predicted_close)}"
        for item in items
    ]
    return "\n".join([header, *rows])


def _band_text(item: EmailCandidate) -> str:
    band = _band(item)
    if band is None:
        return "—"
    return f"{band[0]:+.2%} 〜 {band[1]:+.2%}"


def _alltable(items: Sequence[EmailCandidate]) -> str:
    """Every ticker, so a missing name is visible rather than merely absent."""

    header = (
        "  銘柄                  判定      中央値  80%区間              P(上昇)\n"
        "  --------------------  ------  --------  -------------------  -------"
    )
    rows = [
        f"  {_name(item)}{item.signal:<8}"
        f"{_percent(item.distribution_median):>8}  {_band_text(item):<19}"
        f"  {_probability(item.distribution_probability_up):>7}"
        for item in items
    ]
    return "\n".join([header, *rows])


def _reasons_text(items: Sequence[EmailCandidate]) -> str:
    """Why each buy was chosen, in the model's own terms."""

    blocks: list[str] = []
    for item in items:
        lines = [
            f"  {item.ticker} {item.company}"
            f"（中央値 {_percent(item.distribution_median)} / "
            f"80%区間 {_band_text(item)}）"
        ]
        lines.append(f"    押し上げた要因: {', '.join(item.positive_factors) or '—'}")
        lines.append(f"    押し下げた要因: {', '.join(item.negative_factors) or '—'}")
        if item.warnings:
            lines.append(f"    注意: {', '.join(item.warnings)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _candidate_text(rank: int, item: EmailCandidate) -> str:
    warnings = f"\n  注意: {', '.join(item.warnings)}" if item.warnings else ""
    return (
        f"{rank}. {item.company} ({item.ticker}) [{item.signal}]\n"
        f"  予測リターン: {_percent(item.predicted_return)}\n"
        f"  上昇確率: {_probability(item.probability_up)}\n"
        f"  Readability: {_number(item.readability_score)}\n"
        f"  Profit Factor: {_number(item.profit_factor, 2)}\n"
        f"  Expectancy: {_yen(item.expectancy_jpy)}\n"
        f"  プラス要因: {', '.join(item.positive_factors) or '—'}\n"
        f"  マイナス要因: {', '.join(item.negative_factors) or '—'}"
        f"{warnings}"
    )


def _price_html(value: float | None, *, bold: bool = False) -> str:
    if value is None:
        return f"<span style='color:{MUTED}'>—</span>"
    return f"<strong>{value:,.1f}</strong>" if bold else f"{value:,.1f}"


def _chip(signal: str) -> str:
    """BUY is the only signal worth a colour; everything else is quiet."""

    if signal == "BUY":
        return (
            "<span style='background:#15803d;color:#fff;border-radius:4px;"
            "padding:2px 8px;font-size:12px;font-weight:700'>買い</span>"
        )
    return f"<span style='color:{MUTED};font-size:12px'>見送り</span>"


def _name_html(item: EmailCandidate) -> str:
    return (
        f"<strong>{html.escape(item.ticker)}</strong> "
        f"<span style='color:{MUTED}'>{html.escape(item.company)}</span>"
    )


def _bar_html(item: EmailCandidate, scale: float) -> str:
    """This candidate's distribution, drawn against the table's shared ruler."""

    band = _band(item)
    inner_low, inner_high = _at(item, LOWER_INNER), _at(item, UPPER_INNER)
    centre = item.distribution_median
    if band is None or inner_low is None or inner_high is None or centre is None:
        return f"<span style='color:{MUTED}'>分布なし</span>"
    return distribution_bar(band[0], inner_low, centre, inner_high, band[1], scale)


def _distribution_legend() -> str:
    return legend(
        [
            ("50%区間", BAND_INNER),
            ("80%区間", BAND_OUTER),
            ("中央値", MEDIAN_MARK),
            ("0%（前日終値）", GRID),
        ]
    )


def _buy_rows_html(items: Sequence[EmailCandidate]) -> str:
    """The buy candidates as distributions, one shared ruler down the column."""

    scale = _scale(items)
    rows = [
        f"<tr style='background:{GOOD_BG}'>"
        + cell(f"<strong>{item.rank or index}</strong>", align="center")
        + cell(_name_html(item))
        + cell(_bar_html(item, scale), nowrap=False)
        + cell(signed_percent(_at(item, LOWER_OUTER)), align="right", muted=True)
        + cell(signed_percent(item.distribution_median), align="right")
        + cell(signed_percent(_at(item, UPPER_OUTER)), align="right", muted=True)
        + cell(_probability(item.distribution_probability_up), align="right")
        + "</tr>"
        for index, item in enumerate(items, 1)
    ]
    return _distribution_legend() + table(
        [
            ("順位", "center"),
            ("銘柄", "left"),
            ("予測の分布", "left"),
            ("下位10%", "right"),
            ("中央値", "right"),
            ("上位10%", "right"),
            ("P(上昇)", "right"),
        ],
        rows,
    )


def _density_rows_html(items: Sequence[EmailCandidate]) -> str:
    """One density plot per candidate, all on one axis and one height scale."""

    drawable = [item for item in items if item.density and item.density_scale]
    if not drawable:
        return (
            "<p style='margin:0;padding:14px;background:#f9fafb;border-radius:8px'>"
            "分布がないため密度は描けません。</p>"
        )
    scale = max(item.density_scale or 0.0 for item in drawable)
    peak = _density_peak(drawable)
    rows = []
    for index, item in enumerate(drawable):
        rows.append(
            f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
            + cell(_name_html(item))
            + cell(density_chart(item.density, peak), nowrap=False)
            + cell(signed_percent(item.distribution_median), align="right")
            + cell(html.escape(_band_text(item)), align="right", muted=True)
            + cell(_probability(item.distribution_probability_up), align="right")
            + "</tr>"
        )
    axis = (
        "<table role='presentation' style='width:100%;border-collapse:collapse'>"
        f"<tr><td style='color:{MUTED};font-size:11px;text-align:left'>"
        f"{-scale:+.1%}</td>"
        f"<td style='color:{MUTED};font-size:11px;text-align:center'>0</td>"
        f"<td style='color:{MUTED};font-size:11px;text-align:right'>"
        f"{scale:+.1%}</td></tr></table>"
    )
    return (
        table(
            [
                ("銘柄", "left"),
                ("確率密度", "left"),
                ("中央値", "right"),
                ("80%区間", "right"),
                ("P(上昇)", "right"),
            ],
            rows,
        )
        + axis
    )


def _quantile_rows_html(items: Sequence[EmailCandidate]) -> str:
    rows = [
        f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
        + cell(_name_html(item))
        + "".join(
            cell(
                signed_percent(_at(item, level)),
                align="right",
                muted=level != 0.50,
            )
            for level in REPORTED
        )
        + "</tr>"
        for index, item in enumerate(items)
    ]
    return table(
        [("銘柄", "left"), *((f"{level:.0%}", "right") for level in REPORTED)],
        rows,
    )


def _price_rows_html(items: Sequence[EmailCandidate]) -> str:
    """The same distribution in yen, which is the unit a decision is made in."""

    rows = []
    for index, item in enumerate(items):
        band = _band(item)
        rows.append(
            f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
            + cell(_name_html(item))
            + cell(_price_html(item.reference_price), align="right", muted=True)
            + cell(
                _price_html(_as_price(item.reference_price, band[0] if band else None)),
                align="right",
                muted=True,
            )
            + cell(
                _price_html(
                    _as_price(item.reference_price, item.distribution_median), bold=True
                ),
                align="right",
            )
            + cell(
                _price_html(_as_price(item.reference_price, band[1] if band else None)),
                align="right",
                muted=True,
            )
            + "</tr>"
        )
    return table(
        [
            ("銘柄", "left"),
            ("前日終値", "right"),
            ("下位10%", "right"),
            ("中央値", "right"),
            ("上位10%", "right"),
        ],
        rows,
    )


def _decision_rows_html(items: Sequence[EmailCandidate]) -> str:
    """The two numbers the buy rule thresholds on, named as such."""

    rows = [
        f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
        + cell(_name_html(item))
        + cell(signed_percent(item.predicted_return), align="right")
        + cell(_probability(item.probability_up), align="right")
        + cell(_price_html(item.reference_price), align="right", muted=True)
        + cell(_price_html(item.predicted_close, bold=True), align="right")
        + "</tr>"
        for index, item in enumerate(items)
    ]
    return table(
        [
            ("銘柄", "left"),
            ("点予測（平均）", "right"),
            ("上昇確率", "right"),
            ("前日終値", "right"),
            ("予測終値", "right"),
        ],
        rows,
    )


def _all_rows_html(items: Sequence[EmailCandidate]) -> str:
    scale = _scale(items)
    rows = [
        f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
        + cell(_name_html(item))
        + cell(_chip(item.signal), align="center")
        + cell(_bar_html(item, scale), nowrap=False)
        + cell(signed_percent(item.distribution_median), align="right")
        + cell(
            html.escape(_band_text(item)),
            align="right",
            muted=True,
        )
        + cell(_probability(item.distribution_probability_up), align="right")
        + "</tr>"
        for index, item in enumerate(items)
    ]
    return _distribution_legend() + table(
        [
            ("銘柄", "left"),
            ("判定", "center"),
            ("予測の分布", "left"),
            ("中央値", "right"),
            ("80%区間", "right"),
            ("P(上昇)", "right"),
        ],
        rows,
    )


def _reasons_html(items: Sequence[EmailCandidate]) -> str:
    blocks: list[str] = []
    for item in items:
        rows = [
            f"<tr>{cell('押し上げた要因', muted=True)}"
            f"{cell(_factor_text(item.positive_factors), nowrap=False)}</tr>",
            f"<tr>{cell('押し下げた要因', muted=True)}"
            f"{cell(_factor_text(item.negative_factors), nowrap=False)}</tr>",
        ]
        if item.warnings:
            rows.append(
                f"<tr>{cell('注意', muted=True)}"
                f"{cell(_factor_text(item.warnings), nowrap=False)}</tr>"
            )
        blocks.append(
            f"<p style='margin:16px 0 6px;font-size:14px;color:{INK}'>"
            f"{_name_html(item)} <span style='color:{MUTED}'>中央値 "
            f"{_percent(item.distribution_median)} / 80%区間 "
            f"{html.escape(_band_text(item))}</span></p>"
            + table([("区分", "left"), ("要因", "left")], rows)
        )
    return "".join(blocks)


def _quality_label(item: EmailCandidate) -> str:
    """Short text state - never colour alone, since mail clients strip styles."""

    if item.data_quality == "DEGRADED":
        return "⚠ DEGRADED"
    if item.data_quality == "CLEAN":
        return "CLEAN"
    return "UNKNOWN"


def _quality_summary_text(candidates: Sequence[EmailCandidate]) -> str:
    """The day's completeness, counted from the same field the table shows."""

    clean = sum(1 for item in candidates if item.data_quality == "CLEAN")
    degraded = sum(1 for item in candidates if item.data_quality == "DEGRADED")
    unknown = len(candidates) - clean - degraded
    lines = [
        f"データ品質  CLEAN {clean} / DEGRADED {degraded} / UNKNOWN {unknown}"
        f"  （全{len(candidates)}銘柄）",
    ]
    missing: dict[str, int] = {}
    for item in candidates:
        for name in item.missing_required:
            missing[name] = missing.get(name, 0) + 1
    if missing:
        ranked = sorted(missing.items(), key=lambda pair: (-pair[1], pair[0]))
        lines.append(
            "欠損した必須指標: "
            + "、".join(f"{name}（{count}銘柄）" for name, count in ranked)
        )
    return "\n".join(lines)


def _degraded_buy_notice(selected: Sequence[EmailCandidate]) -> str:
    """Named explicitly: a BUY on incomplete inputs must not read as a clean one."""

    degraded = [item for item in selected if item.data_quality == "DEGRADED"]
    if not degraded:
        return ""
    parts = []
    for item in degraded:
        missing = "・".join(item.missing_required) or "必須指標"
        parts.append(f"{item.ticker} {item.company}（{missing}）")
    rows = "、".join(parts)
    return f"⚠ {len(degraded)}件のBUY候補は必須指標が欠けた状態で作られています: {rows}"


def render_morning_email(
    payload: MorningEmailPayload,
    *,
    sender: str,
    recipient: str,
    top_n: int = 5,
) -> RenderedEmail:
    """Render a deterministic message from a persisted prediction set."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    selected = tuple(
        item
        for item in payload.candidates
        if item.signal == "BUY" and item.status == "READY"
    )[:top_n]
    date_text = payload.prediction_date.isoformat()
    subject = f"【日本株AI予測】{date_text} 寄り付き→大引け／買い{len(selected)}銘柄"
    everything = tuple(payload.candidates)
    distribution_notes = "\n".join(
        note
        for note in (
            _fallback_notice(everything),
            _missing_distribution_notice(everything),
        )
        if note
    )
    if selected:
        text_body = "\n".join(
            [
                "■ 本日の要約",
                "",
                f"BUY候補 {len(selected)}銘柄",
                _quality_summary_text(everything),
                _degraded_buy_notice(selected),
                distribution_notes,
                "",
                "■ 買い候補の確率密度分布",
                "",
                _densitytext(selected),
                "",
                f"  {DENSITY_NOTE}",
                "",
                "",
                "■ 買い候補の分位点",
                "",
                _quantiletable(selected),
                "",
                f"  {READING_NOTE}",
                f"  {COVERAGE_NOTE}",
                "",
                "",
                "■ 買い候補の予測分布（要約）",
                "",
                _returntable(selected),
                "",
                "",
                "■ 買い候補の予測分布（株価）",
                "",
                _pricetable(selected),
                "",
                "",
                "■ 判定に使った数値",
                "",
                _decisiontable(selected),
                "",
                "  買い判定は上の2つの数値で行っています。分布は判定そのものでは",
                "  なく、その判定がどれだけ不確かかを示すものです。",
                "",
                "",
                "■ 全モデル系統の予測",
                "",
                _arms_text(selected),
                "",
                f"  {ARM_NOTE}",
                "",
                f"  {ARM_WIDTH_NOTE}",
                "",
                "",
                "■ なぜその予測になったか",
                "",
                _reasons_text(selected),
            ]
        )
        html_body = (
            section(
                "買い候補の確率密度分布",
                _density_rows_html(selected),
                DENSITY_NOTE,
            )
            + section(
                "買い候補の分位点",
                _quantile_rows_html(selected),
                "各パーセンタイルの予測リターンです。50%が中央値にあたります。",
            )
            + section(
                "買い候補の予測分布（要約）",
                _buy_rows_html(selected),
                READING_NOTE + " " + COVERAGE_NOTE,
            )
            + section(
                "予測終値の分布",
                _price_rows_html(selected),
                "同じ分布を株価で表したものです。前日終値を起点にしています。",
            )
            + section(
                "判定に使った数値",
                _decision_rows_html(selected),
                "買い判定はこの2つの数値で行っています。分布は判定そのものでは"
                "なく、その判定がどれだけ不確かかを示すものです。",
            )
            + section(
                "全モデル系統の予測",
                _arms_html(selected),
                ARM_NOTE + " " + ARM_WIDTH_NOTE,
            )
            + section(
                "なぜその予測になったか",
                _reasons_html(selected),
                "モデルが押し上げた要因と押し下げた要因です。",
            )
        )
    else:
        text_body = "\n".join(
            [
                "本日は条件を満たすBUY候補なし",
                "（予測は生成されています。BUYなしと予測失敗は別です）",
                "",
                _quality_summary_text(everything),
                distribution_notes,
            ]
        )
        html_body = section(
            "買い候補",
            "<p style='margin:0;padding:14px;background:#f9fafb;border-radius:8px'>"
            "<strong>本日は条件を満たすBUY候補はありません。</strong></p>",
        )
    if everything:
        text_body += "\n\n\n■ 全銘柄の予測分布\n\n" + _alltable(everything)
        html_body += section(
            "全銘柄の予測分布",
            _all_rows_html(everything),
            "買わなかった銘柄も載せています。欠けている銘柄は予測自体がありません。",
        )
    warning_text = "\n".join(f"- {item}" for item in payload.warnings)
    methods = {
        item.distribution_method for item in everything if item.distribution_method
    }
    method_text = "、".join(sorted(methods)) or "なし"
    text = (
        f"予測日          {date_text}（寄り付きに買い、大引けに売る前提）\n"
        f"買い候補        {len(selected)}銘柄 / 全{len(everything)}銘柄\n"
        f"Cutoff          {payload.cutoff_at.isoformat()}\n"
        f"取得状態        {payload.provider_status}\n"
        f"モデル          {payload.model_version}\n"
        f"分布の推定法    {method_text}\n\n\n"
        f"{text_body}\n\n\n"
        f"■ 警告\n\n{warning_text or '- なし'}\n\n"
        f"Dashboard: {payload.dashboard_url}\n\n"
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。"
    )
    safe_url = html.escape(payload.dashboard_url, quote=True)
    warnings_html = (
        "".join(
            f"<li style='margin-bottom:4px'>{html.escape(item)}</li>"
            for item in payload.warnings
        )
        or "<li>なし</li>"
    )
    html_text = page(
        f"日本株AI予測　{date_text}",
        f"寄り付きに買い、大引けに売る前提　|　買い候補 {len(selected)}銘柄 / "
        f"全{len(everything)}銘柄　|　取得状態 "
        f"{html.escape(payload.provider_status)}　|　モデル "
        f"{html.escape(payload.model_version)}　|　分布 "
        f"{html.escape(method_text)}",
        [
            html_body,
            section(
                "警告",
                f"<ul style='margin:0;padding-left:20px;color:{INK};"
                f"font-size:13.5px'>{warnings_html}</ul>",
            ),
            f"<p style='margin:22px 0 0'><a href='{safe_url}' "
            f"style='color:#1d4ed8;font-size:14px'>ダッシュボードを開く</a></p>",
        ],
        "本メールは個人用の分析情報であり、投資助言や収益保証ではありません。"
        "売買判断には使用しないでください。優位性はまだ確認されていません。",
    )
    recipient_hash = hashlib.sha256(recipient.strip().lower().encode()).hexdigest()[:16]
    key = f"morning/{date_text}/{recipient_hash}"
    return RenderedEmail(subject, text, html_text, sender, recipient, key)


# How a family arrived at its spread, in the operator's words. The distinction
# is the point: a width that reacts to today's inputs and one that cannot are
# not the same claim, and a single "区間" column would hide that.
SPREAD_LABEL = {
    "conditional": "当日の入力で変わる",
    "ensemble": "木の意見の割れ",
    "residual": "過去の誤差幅（一定）",
}

ARM_NOTE = (
    "毎朝この全系統を同じ行・同じ特徴量で学習させて記録しています。"
    "ただし売買の判定は従来どおりRidgeの点予測とロジスティックの確率だけで行っており、"
    "ここの系統は判定に一切関与していません。"
    "どれかを採用するかどうかは、実績が溜まってから別途ご判断いただく話です。"
)

# Measured 2026-08-30 over eight tickers: each family's mean 80% band against
# the width a zero-skill forecast would need at the realised volatility of the
# same window (+/-1.28 sigma). This system's predictive power has been measured
# at close to zero, so a band far under that ratio is not skill -- it is the
# window being memorised, and the number it prints is tighter than the outcome
# will be. Printed because a reader comparing two 80% columns has no other way
# to know that one of them is four times too confident.
ARM_WIDTH_NOTE = (
    "区間の広さの注意: 2026-08-30に8銘柄で実測したところ、"
    "予測力ゼロなら妥当な幅を1.00倍として、"
    "Ridge 1.33倍 / Lasso・ElasticNet・MLP 約0.94倍 / XGBoost 0.65倍 / "
    "LightGBM 0.56倍 / ランダムフォレスト 0.53倍 でした。"
    "1を大きく下回る系統は区間が狭すぎます。"
    "木系・ブースティング系の80%区間は、額面より実際は外れやすいと読んでください。"
)


def _arm_rows(item: EmailCandidate) -> list[dict[str, object]]:
    return [dict(arm) for arm in item.arms]


def _arm_number(value: object, kind: str = "percent") -> str:
    if not isinstance(value, int | float):
        return "—"
    return f"{float(value):+.2%}" if kind == "percent" else f"{float(value):.2f}"


def _arm_band(arm: dict[str, object]) -> str:
    payload = arm.get("distribution")
    if not isinstance(payload, dict):
        return "—"
    rows = payload.get("levels")
    if not isinstance(rows, list):
        return "—"
    levels = {
        round(float(r["quantile"]), 6): float(r["return"])
        for r in rows
        if isinstance(r, dict) and "quantile" in r and "return" in r
    }
    low, high = levels.get(0.10), levels.get(0.90)
    if low is None or high is None:
        return "—"
    return f"{low:+.2%} 〜 {high:+.2%}"


def _armtable(item: EmailCandidate) -> str:
    """Every family's answer for one ticker, side by side."""

    rows = _arm_rows(item)
    if not rows:
        return "  （この銘柄では全系統の記録がありません）"
    header = (
        "  系統                      状態        点予測   P(上昇)  80%区間"
        "               幅の作り方\n"
        "  ------------------------  --------  --------  --------  "
        "--------------------  --------------------"
    )
    lines = []
    for arm in rows:
        label = str(arm.get("label") or arm.get("name") or "—")
        status = str(arm.get("status") or "—")
        spread = SPREAD_LABEL.get(str(arm.get("spread_kind")), "—")
        detail = str(arm.get("detail") or "")
        lines.append(
            f"  {_pad(label, 24)}  {status:<8}"
            f"  {_arm_number(arm.get('predicted_return')):>8}"
            f"  {_arm_number(arm.get('probability_up'), 'plain'):>8}"
            f"  {_pad(_arm_band(arm), 20)}  {spread}"
            + (f"\n      {detail}" if detail else "")
        )
    return "\n".join([header, *lines])


def _arms_text(items: Sequence[EmailCandidate]) -> str:
    blocks = []
    for item in items:
        blocks.append(f"  ▼ {item.ticker} {item.company}\n\n{_armtable(item)}")
    return "\n\n".join(blocks) if blocks else "  （買い候補がありません）"


def _arm_rows_html(item: EmailCandidate) -> str:
    rows = []
    for index, arm in enumerate(_arm_rows(item)):
        status = str(arm.get("status") or "—")
        tone = (
            "done" if status == "OK" else "warn" if status == "UNAVAILABLE" else "fail"
        )
        predicted = arm.get("predicted_return")
        rows.append(
            f"<tr style='background:{'#fff' if index % 2 == 0 else BAND}'>"
            + cell(html.escape(str(arm.get("label") or arm.get("name") or "—")))
            + cell(badge(status, tone), align="center")
            + cell(
                signed_percent(
                    predicted if isinstance(predicted, int | float) else None
                ),
                align="right",
            )
            + cell(
                _arm_number(arm.get("probability_up"), "plain"),
                align="right",
                muted=True,
            )
            + cell(html.escape(_arm_band(arm)), align="right", muted=True)
            + cell(
                html.escape(SPREAD_LABEL.get(str(arm.get("spread_kind")), "—")),
                muted=True,
                nowrap=False,
            )
            + "</tr>"
        )
    if not rows:
        return (
            "<p style='margin:0;padding:14px;background:#f9fafb;border-radius:8px'>"
            "この銘柄では全系統の記録がありません。</p>"
        )
    return table(
        [
            ("系統", "left"),
            ("状態", "center"),
            ("点予測", "right"),
            ("P(上昇)", "right"),
            ("80%区間", "right"),
            ("幅の作り方", "left"),
        ],
        rows,
    )


def _arms_html(items: Sequence[EmailCandidate]) -> str:
    blocks = []
    for item in items:
        blocks.append(
            f"<p style='margin:16px 0 6px;font-size:14px;color:{INK}'>"
            f"{_name_html(item)}</p>" + _arm_rows_html(item)
        )
    return "".join(blocks)
