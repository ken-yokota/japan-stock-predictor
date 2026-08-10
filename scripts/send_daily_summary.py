"""Send one operational summary of the day, every weekday evening.

This is a status report, not a recommendation. It answers "did the system do
what it was supposed to today, and does anything need attention", so it leads
with what ran and what failed rather than with profit.

A day the pipeline had to be recovered by hand reads, from the database alone,
exactly like a day it ran cleanly: the predictions are there either way. So the
report also names which series never reached the model and whether the
scheduled run actually produced what is on screen. Silence about a manual
recovery is how yesterday's incident becomes invisible tomorrow.

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

JST = ZoneInfo("Asia/Tokyo")
RESEARCH_DIRECTORY = Path("artifacts/feature_comparison")

# Below this many trades, win rate and profit are reported and then explicitly
# disclaimed. The same floor the dashboard and the comparison runner use.
MINIMUM_TRADES_FOR_EVIDENCE = 20


@dataclass(frozen=True, slots=True)
class Section:
    """One titled block of the report."""

    title: str
    lines: list[str]

    def render(self) -> str:
        body = "\n".join(f"  {line}" for line in self.lines) or "  (なし)"
        return f"■ {self.title}\n{body}"


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
    return parser.parse_args()


def _database_sections(target: date) -> list[Section]:
    """Summarize what the pipeline persisted, or say plainly that it could not.

    A missing DATABASE_URL is reported as a fact rather than raising: the
    evening report is more useful arriving incomplete than not arriving.
    """

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return [
            Section(
                "本番pipeline",
                ["DATABASE_URL が未設定のため、DBの状況を読めませんでした。"],
            )
        ]
    try:
        from dashboard.query_service import DashboardQueryService
        from dashboard.types import QueryState
        from database.connection import create_database_engine

        engine = create_database_engine(url)
        service = DashboardQueryService(engine)
    except Exception as error:
        return [
            Section("本番pipeline", [f"DB接続に失敗: {type(error).__name__}"]),
        ]

    sections: list[Section] = []
    try:
        run = service.latest_run()
        if run.state is QueryState.READY and run.rows:
            row = run.rows[0]
            same_day = str(row.get("prediction_date")) == target.isoformat()
            lines = [
                f"種別 {row.get('run_type')} / 状態 {row.get('status')} / "
                f"対象日 {row.get('prediction_date')}",
                f"開始 {row.get('started_at')} / 終了 {row.get('finished_at')}",
            ]
            failed = row.get("failed_symbols") or []
            if failed:
                lines.append(
                    f"失敗した銘柄 {len(failed)}件: {', '.join(map(str, failed))[:120]}"
                )
            if not same_day:
                # Silence here would read as "today ran fine".
                lines.append(
                    f"※ これは {row.get('prediction_date')} の記録です。"
                    f"{target} の実行記録はまだありません。"
                )
        else:
            lines = ["直近のrun記録がありません。"]
        sections.append(Section(f"{target} の実行状況", lines))

        predictions = service.today_predictions()
        if predictions.state is QueryState.READY and predictions.rows:
            rows = predictions.rows
            buys = [row for row in rows if str(row.get("signal")) == "BUY"]
            buy_tickers = ", ".join(str(row.get("ticker")) for row in buys)
            sections.append(
                Section(
                    "本日の予測",
                    [
                        f"予測 {len(rows)}銘柄 / BUY {len(buys)}銘柄",
                        *(
                            [f"BUY: {buy_tickers}"]
                            if buys
                            else ["BUY条件を満たした銘柄はありません。"]
                        ),
                    ],
                )
            )
        else:
            sections.append(
                Section(
                    "本日の予測", ["保存済みの予測がありません(未実行または祝日)。"]
                )
            )

        results = service.actual_results()
        if results.state is QueryState.READY and results.rows:
            traded = [
                row for row in results.rows if row.get("net_profit_jpy") is not None
            ]
            wins = [row for row in traded if float(row["net_profit_jpy"]) > 0]
            total = sum(float(row["net_profit_jpy"]) for row in traded)
            lines = [
                f"実績確定 {len(traded)}件 / 勝ち {len(wins)}件 / 損益 {total:,.0f}円"
            ]
            if len(traded) < MINIMUM_TRADES_FOR_EVIDENCE:
                lines.append(
                    f"※ {len(traded)}件では勝率も損益も有効性の証拠になりません。"
                )
            sections.append(Section("実績", lines))
    except Exception as error:
        sections.append(
            Section("本番pipeline", [f"読み取り失敗: {type(error).__name__}"])
        )
    return sections


def _pipeline_health_section(target: date) -> Section:
    """Say what the day's data actually consisted of, and what was missing.

    A prediction built without FX, futures, and commodities looks identical to
    one built with them. The difference lives in rows nobody counts, so they
    are counted here.
    """

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return Section("データの健全性", ["DATABASE_URL 未設定のため確認できません。"])
    try:
        from sqlalchemy import text

        from database.connection import create_database_engine

        lines: list[str] = []
        with create_database_engine(url).connect() as connection:
            eod, snapshot = connection.execute(
                text(
                    "select count(*) filter (where interval = 'eod'),"
                    " count(*) filter (where interval = 'live_snapshot')"
                    " from market_data"
                )
            ).one()
            fetched_today = connection.scalar(
                text(
                    "select count(*) from market_data where retrieved_at::date = :day"
                ),
                {"day": target},
            )
            latest = connection.scalar(text("select max(market_date) from market_data"))
            lines.append(f"保存済み: EOD {eod:,}行 / スナップショット {snapshot:,}行")
            lines.append(
                f"最新の市場日: {latest} / 本日取り込んだ行: {fetched_today:,}"
            )
            if not snapshot:
                # These are the series the operator asked for first.
                lines.append(
                    "※ スナップショットが0件です。為替・先物・商品の12系列は"
                    "予測に入っていません。"
                )
            if not fetched_today:
                lines.append(
                    "※ 本日の取り込みは0行です。"
                    "予測は保存済みデータから作られています。"
                )
        return Section("データの健全性", lines)
    except Exception as error:
        return Section("データの健全性", [f"確認失敗: {type(error).__name__}"])


def _research_section() -> Section:
    """Report what the research comparisons currently conclude, INVALID included."""

    lines: list[str] = []
    for path in sorted(RESEARCH_DIRECTORY.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        status = report.get("validity", {})
        state = str(status.get("state", "")).upper()
        # Any state carrying INVALID is retired. Matching the exact string is
        # how INVALID_FOR_ADOPTION slipped through and had its p-values quoted
        # in this very report, which is the one place they must not appear.
        if "INVALID" in state:
            # The full reasons live in the artifact and in
            # docs/RESEARCH_VALIDITY.md; repeating them for every file each
            # evening buries the one line that matters.
            lines.append(f"{path.name}: {state} — 採用判断・性能評価に使用不可")
            continue
        window = report.get("generated_for", {})
        verdicts = [
            f"{row['candidate']} p={row['p_value']:.3f}"
            for row in report.get("comparisons", [])
            if row.get("p_value") is not None
        ]
        if verdicts:
            lines.append(
                f"{path.name} (学習窓{window.get('training_window_sessions', '?')}日): "
                + " / ".join(verdicts)
            )
    if not lines:
        lines.append("比較結果がありません。")
    elif all("INVALID" in line for line in lines):
        lines.append(
            "全ての研究結果が無効化されています。"
            "理由は docs/RESEARCH_VALIDITY.md を参照。"
        )
    lines.append("採用は方向精度と検定で判断し、勝率・損益では判断していません。")
    return Section("研究: 予測要素の採否", lines)


def build_report(target: date) -> str:
    sections = [
        *_database_sections(target),
        _pipeline_health_section(target),
        _research_section(),
    ]
    header = f"{target:%Y-%m-%d} (JST) 日次サマリー"
    footer = (
        "この内容は研究・情報提供であり、投資助言ではありません。\n"
        "売買判断は必ずご自身で行ってください。"
    )
    return "\n\n".join([header, *(section.render() for section in sections), footer])


def main() -> int:
    arguments = _parse_arguments()
    target = arguments.for_date or datetime.now(JST).date()
    body = build_report(target)

    if arguments.dry_run:
        print(body)
        return 0

    recipient = os.environ.get("EMAIL_TO", "").strip()
    if not recipient:
        print("EMAIL_TO が未設定のため送信できません。", flush=True)
        return 1

    from notifications.contracts import RenderedEmail
    from notifications.senders import GmailSmtpSender, ResendSender

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
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    sender.send(
        RenderedEmail(
            subject=f"【日次サマリー】{target:%Y-%m-%d}",
            text=body,
            html=(
                "<pre style='font-family:ui-monospace,monospace;font-size:13px'>"
                f"{escaped}</pre>"
            ),
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
