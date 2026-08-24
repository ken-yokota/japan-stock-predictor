"""Send one operational summary of the day, every weekday evening after the close.

This is the mail the operator actually receives every weekday, so it is the one
that has to be readable. It answers two questions in this order: **what failed
today**, and **what the day's trading actually did**. Both are tables, both are
coloured, and neither is allowed to be prose -- the operator reads this on a
phone and asked, twice, that numbers never be buried in a sentence.

A day the pipeline had to be recovered by hand reads, from the predictions
alone, exactly like a day it ran cleanly. So the report also names which runs
came back PARTIAL, which series never reached the model, and whether the
day settled at all. Silence about a failure is how yesterday's incident becomes
invisible tomorrow, and a report that lists only successes is not a report.

Numbers are reported beside their sample size, and any figure too small to
support a conclusion says so in the mail rather than being left to the reader.
Nothing here recomputes a prediction: it reads what the pipeline already wrote.

    python -m cli daily-summary --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from notifications.report_layout import (
    BAND,
    DOWN,
    GOOD_BG,
    MUTED,
    UP,
    badge,
    cell,
    page,
    row,
    section,
    table,
)
from notifications.result_report import (
    DayResult,
    lede,
    load_day_result,
    no_result_section,
    plain_lines,
    result_sections,
)

JST = ZoneInfo("Asia/Tokyo")
RESEARCH_DIRECTORY = Path("artifacts/feature_comparison")

# The free tier's ceiling. Reported every evening because the day it is reached
# is the day the morning run stops writing, and there is no warning from Neon.
DATABASE_LIMIT_MB = 512


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing that did not work, in the shape every failure report uses."""

    what: str
    why: str
    handled: str
    now: str


@dataclass(frozen=True, slots=True)
class Achievement:
    """One thing that did, with the evidence that says so."""

    what: str
    evidence: str
    tone: str = "done"


@dataclass(frozen=True, slots=True)
class Day:
    """Everything the evening mail says, gathered fresh on each send."""

    target: date
    trading_day: bool = True
    result: DayResult | None = None
    no_result_reason: str = ""
    findings: tuple[Finding, ...] = ()
    achievements: tuple[Achievement, ...] = ()
    runs: tuple[tuple[str, str, str, str], ...] = ()
    health: tuple[tuple[str, str], ...] = ()
    research: tuple[tuple[str, str], ...] = ()


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument(
        "--for-date",
        type=date.fromisoformat,
        default=None,
        help="対象日 (既定は JST の今日)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="本文を標準出力に表示するだけで、送信しない。",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="組み上がったHTMLをこのファイルに書き出す（送信の有無とは無関係）。",
    )
    return parser.parse_args()


def _names(config_dir: Path) -> dict[str, str]:
    try:
        from data.config import load_app_config

        config = load_app_config(config_dir) if config_dir else load_app_config()
        return {stock.ticker: stock.name for stock in config.stocks.stocks}
    except Exception:
        # A missing name makes the table plainer, not wrong. It must not stop
        # the mail: the evening report is more useful incomplete than absent.
        return {}


def _elapsed(started: Any, finished: Any) -> str:
    if started is None or finished is None:
        return "—"
    try:
        seconds = (finished - started).total_seconds()
    except TypeError:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}秒"
    return f"{seconds / 60:.1f}分"


def _run_tone(status: str) -> str:
    if status == "SUCCESS":
        return "done"
    if status in {"PARTIAL", "SKIPPED"}:
        return "warn"
    return "fail"


_RUN_LABELS = {
    "INGESTION": "データ取得",
    "MORNING": "予測の計算と保存",
    "CLOSE": "実績の確定と答え合わせ",
    "OPEN": "寄り付きの観測",
}


def _is_trading_day(target: date) -> bool:
    """Whether JPX had a session. A closed market is not a failed pipeline.

    Without this, every public holiday that falls on a weekday produced a mail
    claiming six failures -- three missing runs, a missing prediction set, and
    no result -- for a day on which nothing was supposed to happen. Noise like
    that is what stops the mails being read, and then the real failure is
    missed too.
    """

    try:
        from data.market_calendar import is_japan_business_day

        return bool(is_japan_business_day(target))
    except Exception:
        # An unavailable calendar must not turn into a claim either way; treat
        # the day as a session so nothing is silently excused.
        return True


def collect(target: date, config_dir: Path) -> Day:
    """Read the day, degrading each part to a recorded finding rather than raising."""

    findings: list[Finding] = []
    achievements: list[Achievement] = []
    runs: list[tuple[str, str, str, str]] = []
    health: list[tuple[str, str]] = []
    result: DayResult | None = None
    reason = ""
    trading_day = _is_trading_day(target)

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        findings.append(
            Finding(
                "本番DBを読めませんでした",
                "DATABASE_URL が未設定",
                "DBに依存しない節だけで組み立てて送信",
                "本日の成績・実行状況・健全性はすべて不明のままです",
            )
        )
        return Day(
            target=target,
            trading_day=_is_trading_day(target),
            no_result_reason="DATABASE_URL が未設定のため確認できません",
            findings=_dedupe(findings),
            research=tuple(_research_rows()),
        )

    try:
        from sqlalchemy import text

        from database.connection import create_database_engine

        engine = create_database_engine(url)
    except Exception as error:
        findings.append(
            Finding(
                "本番DBに接続できませんでした",
                f"{type(error).__name__}",
                "DBに依存しない節だけで組み立てて送信",
                "本日の成績・実行状況・健全性はすべて不明のままです",
            )
        )
        return Day(
            target=target,
            trading_day=_is_trading_day(target),
            no_result_reason=f"DB接続に失敗しました（{type(error).__name__}）",
            findings=_dedupe(findings),
            research=tuple(_research_rows()),
        )

    try:
        with engine.connect() as connection:
            run_rows = list(
                connection.execute(
                    text(
                        "select run_type, status, started_at, finished_at,"
                        " failed_symbols, error_message, current_step"
                        " from daily_runs where prediction_date = :day"
                        " order by started_at"
                    ),
                    {"day": target},
                ).mappings()
            )
            published = connection.execute(
                text(
                    "select status, model_version, published_at, warnings"
                    " from prediction_sets where prediction_date = :day"
                ),
                {"day": target},
            ).mappings().first()
            counts = connection.execute(
                text(
                    "select count(*) as predicted,"
                    " count(*) filter (where p.signal = 'BUY') as buys,"
                    " count(a.actual_result_id) as settled"
                    " from predictions as p"
                    " join prediction_sets as ps"
                    "   on ps.prediction_set_id = p.prediction_set_id"
                    " left join actual_results as a"
                    "   on a.prediction_id = p.prediction_id"
                    " where ps.prediction_date = :day"
                ),
                {"day": target},
            ).mappings().one()
            eod, snapshot = connection.execute(
                text(
                    "select count(*) filter (where interval = 'eod'),"
                    " count(*) filter (where interval = 'live_snapshot')"
                    " from market_data"
                )
            ).one()
            # retrieved_at is timestamptz and the morning fetch runs at 07:10
            # JST, which is the *previous* UTC date. Comparing it in UTC made
            # this count zero on every ordinary day, and the evening mail
            # carried "本日の取り込みは0行です" as a standing false alarm.
            fetched_today = connection.scalar(
                text(
                    "select count(*) from market_data"
                    " where (retrieved_at at time zone 'Asia/Tokyo')::date = :day"
                ),
                {"day": target},
            )
            latest_market_date = connection.scalar(
                text("select max(market_date) from market_data")
            )
            database_size = connection.scalar(
                text("select pg_database_size(current_database()) / 1024 / 1024")
            )

        # ---- what ran -------------------------------------------------------
        for item in run_rows:
            run_type = str(item["run_type"])
            status = str(item["status"])
            failed = list(item["failed_symbols"] or [])
            detail = ""
            if failed:
                detail = f"取得できなかった系列 {len(failed)}件: {', '.join(failed)}"
            elif item["error_message"]:
                detail = str(item["error_message"])[:120]
            runs.append(
                (
                    _RUN_LABELS.get(run_type, run_type),
                    status,
                    _elapsed(item["started_at"], item["finished_at"]),
                    detail or "—",
                )
            )
            if status != "SUCCESS":
                findings.append(
                    Finding(
                        f"{_RUN_LABELS.get(run_type, run_type)}が {status} "
                        "で終わりました",
                        detail or f"状態 {status}（工程 {item['current_step']}）",
                        "後続の工程はそのまま続行しています",
                        "欠けた系列は予測に入っていません",
                    )
                )

        for expected in ("INGESTION", "MORNING", "CLOSE"):
            if not trading_day:
                break
            if not any(str(item["run_type"]) == expected for item in run_rows):
                findings.append(
                    Finding(
                        f"{_RUN_LABELS[expected]}の実行記録がありません",
                        f"{target} の {expected} run が daily_runs に存在しない",
                        "自動では何も再実行していません",
                        "この工程は本日走っていない可能性があります",
                    )
                )

        # ---- what was published --------------------------------------------
        if published is None:
            if trading_day:
                findings.append(
                    Finding(
                        "本日の予測が保存されていません",
                        f"{target} の prediction_sets が存在しない",
                        "自動では何も再実行していません",
                        "JPXは開いていたので、朝の実行が失敗しています",
                    )
                )
                reason = "本日の予測セットがありません（朝の実行が失敗）"
            else:
                reason = "JPXが休場だったため、予測も実績もありません"
        else:
            status = str(published["status"])
            achievements.append(
                Achievement(
                    "予測の保存",
                    f"{counts['predicted']}銘柄 / 買い{counts['buys']}銘柄"
                    f"（{status}・{published['model_version']}）",
                    "done" if status == "READY" else "warn",
                )
            )
            if status != "READY":
                findings.append(
                    Finding(
                        f"予測セットの状態が {status} です",
                        "READY 以外の状態で保存されている",
                        "配信は止めていません",
                        "この日の予測は採用判断に使えません",
                    )
                )
            for warning in list(published["warnings"] or []):
                findings.append(
                    Finding(
                        "予測に注記が付いています",
                        str(warning),
                        "注記付きのまま公開",
                        "この注記は成績の解釈にそのまま効きます",
                    )
                )

        # ---- did it settle --------------------------------------------------
        predicted = int(counts["predicted"])
        settled = int(counts["settled"])
        if trading_day and predicted and settled < predicted:
            findings.append(
                Finding(
                    "実績が全銘柄では確定していません",
                    f"予測{predicted}銘柄に対し確定{settled}銘柄",
                    "確定した分だけで成績を出しています",
                    f"{predicted - settled}銘柄は答え合わせができていません",
                )
            )
        if settled:
            result = load_day_result(engine, target)
            if result is not None:
                achievements.append(
                    Achievement(
                        "実績の確定と答え合わせ",
                        f"{len(result.items)}銘柄が確定・買い{len(result.buys)}銘柄の"
                        f"損益 {result.profit:+,.0f}円",
                    )
                )
        if result is None and not reason:
            reason = (
                f"予測{predicted}銘柄のうち確定は{settled}銘柄で、"
                "答え合わせできる行がありません"
            )

        # ---- the data the day was built on ----------------------------------
        health.append(("保存済みEOD", f"{eod:,}行"))
        health.append(("保存済みスナップショット", f"{snapshot:,}行"))
        health.append(("最新の市場日", str(latest_market_date)))
        health.append(("本日取り込んだ行", f"{fetched_today:,}行"))
        health.append(
            (
                "データベース使用量",
                f"{int(database_size or 0):,} MB / {DATABASE_LIMIT_MB} MB",
            )
        )
        if not snapshot and trading_day:
            findings.append(
                Finding(
                    "実勢値スナップショットが0件です",
                    "live_snapshot の行が market_data に存在しない",
                    "予測は保存済みのEODだけで作られました",
                    "為替・先物・商品の12系列は予測に入っていません",
                )
            )
        if not fetched_today and trading_day:
            findings.append(
                Finding(
                    "本日の取り込みが0行です",
                    f"retrieved_at が {target} の market_data 行がない",
                    "予測は保存済みデータから作られました",
                    "当日の値は反映されていません",
                )
            )
        elif fetched_today:
            achievements.append(
                Achievement("当日のデータ取り込み", f"{fetched_today:,}行を保存")
            )
        if int(database_size or 0) > DATABASE_LIMIT_MB * 0.85:
            findings.append(
                Finding(
                    "データベースが上限に近づいています",
                    f"{int(database_size or 0):,} MB / {DATABASE_LIMIT_MB} MB",
                    "自動での削除は行っていません",
                    "上限に達した朝は書き込みが止まります",
                )
            )
    except Exception as error:
        findings.append(
            Finding(
                "本番DBの読み取りが途中で失敗しました",
                f"{type(error).__name__}",
                "読めたところまでで組み立てて送信",
                "この節より下は不完全な可能性があります",
            )
        )
    finally:
        engine.dispose()

    research = _research_rows()
    retired = [name for name, verdict in research if "INVALID" in verdict]
    if retired:
        # One row, not one per file: four identical lines push the day's actual
        # failures off the top of a phone screen.
        findings.append(
            Finding(
                f"研究成果 {len(retired)}件が無効化されたままです",
                "、".join(retired),
                "採用判断・性能評価には使っていません",
                "これらのp値は引用できません。理由は docs/RESEARCH_VALIDITY.md",
            )
        )

    return Day(
        target=target,
        trading_day=trading_day,
        result=result,
        no_result_reason=reason,
        findings=_dedupe(findings),
        achievements=tuple(achievements),
        runs=tuple(runs),
        health=tuple(health),
        research=tuple(research),
    )


def _dedupe(findings: list[Finding]) -> tuple[Finding, ...]:
    """Collapse a failure that repeated into one row that says how often.

    The close and ingestion jobs retry, and each retry writes its own run, so
    one broken FX feed produced three identical rows. Three copies of the same
    sentence push the day's other failures off a phone screen, and they
    overstate how many distinct things went wrong.
    """

    seen: dict[tuple[str, str], list[Finding]] = {}
    for item in findings:
        seen.setdefault((item.what, item.why), []).append(item)
    collapsed: list[Finding] = []
    for group in seen.values():
        first = group[0]
        if len(group) == 1:
            collapsed.append(first)
            continue
        collapsed.append(
            Finding(
                f"{first.what}（{len(group)}回）",
                first.why,
                first.handled,
                first.now,
            )
        )
    return tuple(collapsed)


def _research_rows() -> list[tuple[str, str]]:
    """What the comparisons currently conclude, INVALID ones included."""

    rows: list[tuple[str, str]] = []
    for path in sorted(RESEARCH_DIRECTORY.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        state = str(report.get("validity", {}).get("state", "")).upper()
        # Any state carrying INVALID is retired. Matching the exact string is
        # how INVALID_FOR_ADOPTION slipped through and had its p-values quoted
        # in this very report, which is the one place they must not appear.
        if "INVALID" in state:
            rows.append((path.name, f"{state} — 採用判断・性能評価に使用不可"))
            continue
        window = report.get("generated_for", {})
        verdicts = [
            f"{item['candidate']} p={item['p_value']:.3f}"
            for item in report.get("comparisons", [])
            if item.get("p_value") is not None
        ]
        if verdicts:
            sessions = window.get("training_window_sessions", "?")
            rows.append(
                (f"{path.name}（学習窓{sessions}日）", " / ".join(verdicts))
            )
    return rows


def _findings_section(findings: tuple[Finding, ...]) -> str:
    """Failures first, and never softened into "一部で問題が発生"."""

    if not findings:
        return section(
            "できなかったこと",
            table(
                [("内容", "left")],
                [
                    row(
                        [
                            cell(
                                f"<span style='color:{UP};font-weight:700'>"
                                "本日は検出された失敗はありません。</span>"
                            )
                        ]
                    )
                ],
                min_width=420,
            ),
            "失敗がない日もこの節は出します。節ごと消えると、"
            "失敗がなかったのか確認しなかったのかが区別できません。",
        )
    return section(
        "できなかったこと",
        table(
            [
                ("何が", "left"),
                ("なぜ", "left"),
                ("どう対処したか", "left"),
                ("いまどうなっているか", "left"),
            ],
            [
                row(
                    [
                        cell(
                            f"<span style='color:{DOWN};font-weight:700'>"
                            f"{item.what}</span>",
                            nowrap=False,
                        ),
                        cell(item.why, muted=True, nowrap=False),
                        cell(item.handled, muted=True, nowrap=False),
                        cell(item.now, nowrap=False),
                    ],
                    "#fff" if index % 2 == 0 else BAND,
                )
                for index, item in enumerate(findings)
            ],
            min_width=620,
        ),
        f"{len(findings)}件。運用者の操作が要るものはこの表の"
        "「いまどうなっているか」に書いてあります。",
    )


def _achievements_section(day: Day) -> str:
    rows = [
        row(
            [
                cell(item.what),
                cell(item.evidence, muted=True, nowrap=False),
                cell(badge("完了" if item.tone == "done" else "注意", item.tone),
                     align="center"),
            ],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, item in enumerate(day.achievements)
    ]
    if not rows:
        return section(
            "できたこと",
            "",
            "本日、完了を確認できた工程はありません。",
        )
    return section(
        "できたこと",
        table(
            [("内容", "left"), ("確認した証拠", "left"), ("状態", "center")],
            rows,
            min_width=520,
        ),
    )


def _runs_section(day: Day) -> str:
    if not day.runs:
        return section(
            "本日の実行状況",
            "",
            f"{day.target} の実行記録が1件もありません。"
            + (
                "JPXが休場だったため、これは想定どおりです。"
                if not day.trading_day
                else "JPXは開いていたので、自動実行そのものが動いていません。"
            ),
        )
    return section(
        "本日の実行状況",
        table(
            [("工程", "left"), ("状態", "center"), ("所要", "right"), ("内訳", "left")],
            [
                row(
                    [
                        cell(label),
                        cell(badge(status, _run_tone(status)), align="center"),
                        cell(elapsed, align="right", muted=True),
                        cell(detail, muted=True, nowrap=False),
                    ],
                    GOOD_BG if status == "SUCCESS" else BAND,
                )
                for label, status, elapsed, detail in day.runs
            ],
            min_width=560,
        ),
        "同じ工程が複数回あるのは、失敗時のリトライが別runとして"
        "記録されるためです。",
    )


def _health_section(day: Day) -> str:
    if not day.health:
        return ""
    return section(
        "データの健全性",
        table(
            [("項目", "left"), ("値", "right")],
            [
                row(
                    [cell(name), cell(value, align="right")],
                    "#fff" if index % 2 == 0 else BAND,
                )
                for index, (name, value) in enumerate(day.health)
            ],
            min_width=460,
        ),
        "欠けている系列は予測に入りません。ここが0行の日は、"
        "予測が保存済みデータだけで作られています。",
    )


def _research_section(day: Day) -> str:
    if not day.research:
        return section(
            "研究: 予測要素の採否",
            "",
            "比較結果がありません。採用は方向精度と検定で判断し、"
            "勝率・損益では判断していません。",
        )
    return section(
        "研究: 予測要素の採否",
        table(
            [("比較", "left"), ("判定", "left")],
            [
                row(
                    [
                        cell(name, nowrap=False),
                        cell(
                            f"<span style='color:{DOWN};font-weight:700'>"
                            f"{verdict}</span>"
                            if "INVALID" in verdict
                            else f"<span style='color:{MUTED}'>{verdict}</span>",
                            nowrap=False,
                        ),
                    ],
                    "#fff" if index % 2 == 0 else BAND,
                )
                for index, (name, verdict) in enumerate(day.research)
            ],
            min_width=520,
        ),
        "採用は方向精度と検定で判断し、勝率・損益では判断していません。",
    )


def subject_for(day: Day) -> str:
    """Triage from the subject alone: the result, then how much needs attention."""

    tail = f"／要確認{len(day.findings)}件" if day.findings else ""
    if day.result is None and not day.trading_day:
        return f"【大引け後】{day.target:%Y-%m-%d} JPX休場につき取引なし{tail}"
    if day.result is None:
        return f"【大引け後】{day.target:%Y-%m-%d} 実績が確定していません{tail}"
    result = day.result
    buys = result.buys
    return (
        f"【大引け後】{day.target:%Y-%m-%d} 買い{len(buys)}銘柄 "
        f"{result.buy_hits}勝{len(buys) - result.buy_hits}敗 "
        f"{result.profit:+,.0f}円{tail}"
    )


def build_html(day: Day, names: dict[str, str]) -> str:
    blocks = [_findings_section(day.findings)]
    if day.result is not None:
        blocks.extend(result_sections(day.result, names))
    else:
        blocks.append(
            no_result_section(
                day.target, day.no_result_reason or "理由を特定できませんでした"
            )
        )
    blocks.append(_achievements_section(day))
    blocks.append(_runs_section(day))
    health = _health_section(day)
    if health:
        blocks.append(health)
    blocks.append(_research_section(day))
    if day.result is not None:
        head = lede(day.result)
    elif not day.trading_day:
        head = f"{day.target:%Y-%m-%d}　|　JPX休場のため取引はありません"
    else:
        head = f"{day.target:%Y-%m-%d}　|　実績が確定していません"
    if day.findings:
        head += (
            f"　|　<span style='color:{DOWN};font-weight:700'>"
            f"要確認 {len(day.findings)}件</span>"
        )
    return page(
        f"大引け後サマリー　{day.target:%Y-%m-%d}（JST）",
        head,
        blocks,
        "この内容は研究・情報提供であり、投資助言ではありません。"
        "売買判断は必ずご自身で行ってください。",
    )


def build_text(day: Day, names: dict[str, str]) -> str:
    """The text/plain alternative, built from the same values as the tables."""

    lines = [f"{day.target:%Y-%m-%d} (JST) 大引け後サマリー", ""]
    lines.append(f"■ できなかったこと（{len(day.findings)}件）")
    if day.findings:
        for item in day.findings:
            lines.append(f"  - {item.what}")
            lines.append(f"      なぜ: {item.why}")
            lines.append(f"      対処: {item.handled}")
            lines.append(f"      現状: {item.now}")
    else:
        lines.append("  本日は検出された失敗はありません。")
    lines.append("")
    lines.append("■ 本日の結果")
    if day.result is not None:
        lines.extend(f"  {line}" for line in plain_lines(day.result, names))
    else:
        lines.append(f"  実績が確定していません: {day.no_result_reason}")
    lines.append("")
    lines.append("■ できたこと")
    for done in day.achievements:
        lines.append(f"  - {done.what}: {done.evidence}")
    if not day.achievements:
        lines.append("  完了を確認できた工程はありません。")
    lines.append("")
    lines.append("■ 本日の実行状況")
    for label, status, elapsed, detail in day.runs:
        lines.append(f"  {label:<16} {status:<8} {elapsed:>7}  {detail}")
    if not day.runs:
        lines.append("  実行記録がありません。")
    lines.append("")
    lines.append("■ データの健全性")
    for name, value in day.health:
        lines.append(f"  {name}: {value}")
    lines.append("")
    lines.append("■ 研究: 予測要素の採否")
    for name, verdict in day.research:
        lines.append(f"  {name}: {verdict}")
    if not day.research:
        lines.append("  比較結果がありません。")
    lines.append("")
    lines.append("この内容は研究・情報提供であり、投資助言ではありません。")
    return "\n".join(lines)


def build_report(
    target: date, config_dir: Path = Path("config")
) -> tuple[str, str, str]:
    """Return (subject, text, html) for one day, reading live state as it goes."""

    day = collect(target, config_dir)
    names = _names(config_dir)
    return subject_for(day), build_text(day, names), build_html(day, names)


def main() -> int:
    arguments = _parse_arguments()
    target = arguments.for_date or datetime.now(JST).date()
    subject, text_body, html_body = build_report(target, arguments.config_dir)

    if arguments.output is not None:
        arguments.output.write_text(html_body, encoding="utf-8")

    if arguments.dry_run:
        print(subject)
        print()
        print(text_body)
        return 0

    from notifications.contracts import RenderedEmail
    from notifications.senders import GmailSmtpSender, ResendSender

    recipient = os.environ.get("EMAIL_TO", "").strip()
    if not recipient:
        print("EMAIL_TO が未設定のため送信できません。", flush=True)
        return 1

    provider = os.environ.get("EMAIL_PROVIDER", "gmail_smtp")
    sender: Any
    if provider == "resend":
        sender = ResendSender(api_key=os.environ["RESEND_API_KEY"])
    else:
        sender = GmailSmtpSender(
            username=os.environ["SMTP_USERNAME"],
            app_password=os.environ["SMTP_PASSWORD"],
            host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.environ.get("SMTP_PORT", "587")),
            timeout_seconds=60.0,
        )
    sender.send(
        RenderedEmail(
            subject=subject,
            text=text_body,
            html=html_body,
            sender=os.environ.get("EMAIL_FROM") or os.environ["SMTP_USERNAME"],
            recipient=recipient,
            # One report per day: a retried workflow must not mail twice.
            idempotency_key=f"daily-summary-{target.isoformat()}",
        )
    )
    print(f"送信しました: {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
