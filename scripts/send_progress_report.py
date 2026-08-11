#!/usr/bin/env python3
"""Send the periodic progress report, built from live state every time.

The ten-minute report used to read a fragment someone had to remember to
update, and it went stale within the hour -- it was still describing "tonight"
the next morning. Everything here is therefore re-derived on each send:

* the published prediction set and its settled result, read from the database
* the most recent scheduled runs, read from GitHub Actions
* what changed in the working tree, read from git

The one part a session must supply is what it is working on right now, and
that is the part that can go stale, so it carries its own age: past
``--stale-after`` minutes the mail says the note is old instead of presenting
it as current. A report that admits it does not know is useful; one that
quietly repeats yesterday is not.

Usage:
    python -m scripts.send_progress_report --task tasks.json
    python -m scripts.send_progress_report --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data.env import EnvironmentSettings
from notifications.report_layout import BAND, GOOD_BG, badge, cell, row, section

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_STALE_AFTER = 30


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the report says, gathered fresh."""

    prediction_date: str | None = None
    prediction_status: str | None = None
    buys: int | None = None
    settled: tuple[str, int, int, float] | None = None
    warnings: tuple[str, ...] = ()
    database_size: str | None = None
    runs: tuple[tuple[str, str, str], ...] = ()
    changed_files: int = 0
    last_commit: str | None = None
    errors: tuple[str, ...] = ()


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _workflow_runs(limit: int = 5) -> tuple[tuple[str, str, str], ...]:
    """Recent scheduled runs. Absent gh is not an error worth failing on."""

    try:
        result = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--limit",
                str(limit),
                "--json",
                "name,status,conclusion,createdAt",
            ],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0 or not result.stdout.strip():
        return ()
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return ()
    rows: list[tuple[str, str, str]] = []
    for item in payload:
        stamp = str(item.get("createdAt", ""))[:16].replace("T", " ")
        rows.append(
            (
                str(item.get("name", "")),
                str(item.get("conclusion") or item.get("status") or ""),
                stamp,
            )
        )
    return tuple(rows)


def collect(environment: EnvironmentSettings) -> Snapshot:
    """Read the live state, degrading to a recorded error rather than raising."""

    errors: list[str] = []
    prediction_date = status = database_size = None
    buys: int | None = None
    settled: tuple[str, int, int, float] | None = None
    warnings: tuple[str, ...] = ()

    try:
        from sqlalchemy import text

        from dashboard.query_service import DashboardQueryService
        from database.connection import create_database_engine

        engine = create_database_engine(environment.require_database_url())
        try:
            service = DashboardQueryService(engine)
            published = service.latest_prediction_set()
            row_data = published.first
            if published.ready and row_data is not None:
                prediction_date = str(row_data.get("prediction_date"))
                status = str(row_data.get("status"))
                raw = row_data.get("warnings")
                if isinstance(raw, list):
                    warnings = tuple(str(item) for item in raw)
            history = service.published_prediction_history(None)
            if history.ready and prediction_date is not None:
                buys = sum(
                    1
                    for item in history.rows
                    if str(item.get("prediction_date")) == prediction_date
                    and item.get("signal") == "BUY"
                )
                closed = [
                    item
                    for item in history.rows
                    if item.get("actual_intraday_return") is not None
                    and item.get("signal") == "BUY"
                ]
                if closed:
                    day = max(str(item["prediction_date"]) for item in closed)
                    same = [
                        item for item in closed if str(item["prediction_date"]) == day
                    ]
                    correct = sum(
                        1
                        for item in same
                        if (float(item["predicted_intraday_return"]) > 0)
                        == (float(item["actual_intraday_return"]) > 0)
                    )
                    profit = sum(
                        float(item.get("net_profit_jpy") or 0) for item in same
                    )
                    settled = (day, len(same), correct, profit)
            with engine.connect() as connection:
                database_size = str(
                    connection.execute(
                        text(
                            "select pg_size_pretty("
                            "pg_database_size(current_database()))"
                        )
                    ).scalar()
                )
        finally:
            engine.dispose()
    except Exception as exc:
        errors.append(f"データベース参照に失敗: {type(exc).__name__}")

    changed = _git("status", "--porcelain")
    return Snapshot(
        prediction_date=prediction_date,
        prediction_status=status,
        buys=buys,
        settled=settled,
        warnings=warnings,
        database_size=database_size,
        runs=_workflow_runs(),
        changed_files=len([line for line in changed.splitlines() if line.strip()]),
        last_commit=_git("log", "-1", "--format=%h %s") or None,
        errors=tuple(errors),
    )


def _task_section(path: Path | None, stale_after: int) -> str:
    """The working note, always stamped with its own age."""

    if path is None or not path.exists():
        return section(
            "いま進めているタスク",
            "",
            "作業中のタスクは登録されていません。定時の自動実行のみが動いています。",
        )
    age = (datetime.now(UTC).timestamp() - path.stat().st_mtime) / 60
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for index, item in enumerate(payload.get("tasks", [])):
        rows.append(
            row(
                [
                    cell(str(item.get("step", "")), align="center"),
                    cell(str(item.get("title", "")), nowrap=False),
                    cell(str(item.get("estimate", "—")), align="right"),
                    cell(
                        badge(
                            str(item.get("state", "未着手")),
                            str(item.get("tone", "wait")),
                        ),
                        align="center",
                    ),
                ],
                "#fff" if index % 2 == 0 else BAND,
            )
        )
    from notifications.report_layout import table

    body = (
        table(
            [
                ("工程", "center"),
                ("内容", "left"),
                ("想定", "right"),
                ("状態", "center"),
            ],
            rows,
            min_width=480,
        )
        if rows
        else ""
    )
    note = f"この記録は{age:.0f}分前に更新されました。"
    if age > stale_after:
        note = (
            f"⚠ この記録は{age:.0f}分前のもので、{stale_after}分以上"
            "更新されていません。現在の作業を反映していない可能性があります。"
        )
    return section("いま進めているタスク", body, note)


def build_report(
    snapshot: Snapshot, task_file: Path | None, stale_after: int
) -> dict[str, Any]:
    now = datetime.now(JST)
    published_rows = [
        row(
            [cell("最新の予測日"), cell(snapshot.prediction_date or "—", align="right")]
        ),
        row(
            [cell("状態"), cell(snapshot.prediction_status or "—", align="right")], BAND
        ),
        row(
            [
                cell("買い候補"),
                cell(
                    "—" if snapshot.buys is None else f"{snapshot.buys}銘柄",
                    align="right",
                ),
            ]
        ),
    ]
    if snapshot.settled is not None:
        day, count, correct, profit = snapshot.settled
        published_rows.append(
            row(
                [
                    cell("直近で実績が確定した日"),
                    cell(
                        f"{day}　買い{count}銘柄　{correct}/{count}的中　"
                        f"{profit:+,.0f}円",
                        align="right",
                    ),
                ],
                GOOD_BG,
            )
        )
    else:
        published_rows.append(
            row(
                [cell("直近で実績が確定した日"), cell("まだありません", align="right")],
                BAND,
            )
        )
    if snapshot.database_size:
        published_rows.append(
            row(
                [
                    cell("データベース使用量"),
                    cell(f"{snapshot.database_size} / 512 MB", align="right"),
                ]
            )
        )

    from notifications.report_layout import table

    sections = [
        _task_section(task_file, stale_after),
        _upcoming_section(),
        section(
            "本番の最新状態",
            table([("項目", "left"), ("値", "right")], published_rows, min_width=460),
            "この表は送信のたびにデータベースを読み直しています。",
        ),
    ]
    if snapshot.warnings:
        sections.append(
            section(
                "予測に付いている警告",
                table(
                    [("警告", "left")],
                    [
                        row(
                            [cell(item, nowrap=False)],
                            "#fff" if index % 2 == 0 else BAND,
                        )
                        for index, item in enumerate(snapshot.warnings)
                    ],
                    min_width=440,
                ),
            )
        )
    if snapshot.runs:
        sections.append(
            section(
                "直近の自動実行",
                table(
                    [("ジョブ", "left"), ("結果", "center"), ("開始", "right")],
                    [
                        row(
                            [
                                cell(name, nowrap=False),
                                cell(
                                    badge(
                                        conclusion or "実行中",
                                        "done"
                                        if conclusion == "success"
                                        else "fail"
                                        if conclusion == "failure"
                                        else "now",
                                    ),
                                    align="center",
                                ),
                                cell(stamp, align="right", muted=True),
                            ],
                            "#fff" if index % 2 == 0 else BAND,
                        )
                        for index, (name, conclusion, stamp) in enumerate(snapshot.runs)
                    ],
                    min_width=460,
                ),
            )
        )
    sections.append(
        section(
            "コードの状態",
            table(
                [("項目", "left"), ("値", "right")],
                [
                    row(
                        [
                            cell("未コミットの変更"),
                            cell(f"{snapshot.changed_files}ファイル", align="right"),
                        ]
                    ),
                    row(
                        [
                            cell("最新のコミット"),
                            cell(
                                snapshot.last_commit or "—", align="right", nowrap=False
                            ),
                        ],
                        BAND,
                    ),
                ],
                min_width=440,
            ),
        )
    )
    if snapshot.errors:
        sections.append(
            section(
                "この報告で取得できなかったもの",
                table(
                    [("内容", "left")],
                    [row([cell(item, nowrap=False)]) for item in snapshot.errors],
                    min_width=420,
                ),
                "取得できなかった項目は「—」ではなくここに理由を出しています。",
            )
        )
    return {
        "subject": f"【進捗】定期報告 {now:%m/%d %H:%M}",
        "title": f"定期進捗報告　{now:%Y-%m-%d %H:%M}",
        "lede": _lede(snapshot),
        "sections": sections,
        "footer": "この報告は送信のたびに本番DB・GitHub Actions・gitから"
        "組み直しています。操作は不要です。",
    }


def _upcoming_section() -> str:
    """What happens next, so the mail answers "今後" without opening anything.

    Derived from the calendar and the workflow crons rather than written down,
    so it stays right across holidays and across a schedule change.
    """

    from data.market_calendar import is_japan_business_day
    from notifications.report_layout import table

    now = datetime.now(JST)
    today = now.date()
    upcoming: list[tuple[str, str, str]] = []
    schedule = (
        ("07:15", "履歴データの取得", "日足51系列と米国金利"),
        ("08:20", "予測の計算と保存", "実勢値12系列を取得して予測"),
        ("08:45", "予測メールの配信", "買い候補と根拠"),
        ("16:10", "実績の確定と答え合わせ", "始値・終値から成績を確定"),
        ("18:40", "日次サマリーの配信", "その日の運用状況"),
    )
    day = today
    for _ in range(8):
        if is_japan_business_day(day):
            label = (
                "本日"
                if day == today
                else "翌営業日"
                if not upcoming
                else f"{day:%m/%d}"
            )
            for clock, name, detail in schedule:
                hour, minute = (int(part) for part in clock.split(":"))
                when = datetime.combine(day, now.time()).replace(
                    hour=hour, minute=minute, second=0, microsecond=0, tzinfo=JST
                )
                if when <= now:
                    continue
                upcoming.append((f"{label} {clock}", name, detail))
            if upcoming:
                break
        day = date.fromordinal(day.toordinal() + 1)

    if not upcoming:
        return section("今後の予定", "", "次の営業日の予定を算出できませんでした。")
    rows = [
        row(
            [
                cell(when, align="center"),
                cell(name, nowrap=False),
                cell(detail, muted=True, nowrap=False),
            ],
            "#fff" if index % 2 == 0 else BAND,
        )
        for index, (when, name, detail) in enumerate(upcoming[:6])
    ]
    holiday_note = (
        ""
        if is_japan_business_day(today)
        else "本日はJPX休場のため、定時の予測はスキップされます。"
    )
    return section(
        "今後の予定",
        table(
            [("時刻", "center"), ("内容", "left"), ("詳細", "left")],
            rows,
            min_width=480,
        ),
        holiday_note or "すべて自動で実行されます。操作は不要です。",
    )


def _lede(snapshot: Snapshot) -> str:
    parts = []
    if snapshot.prediction_date:
        parts.append(
            f"最新予測 {snapshot.prediction_date}（{snapshot.prediction_status}）"
        )
    if snapshot.buys is not None:
        parts.append(f"買い候補 {snapshot.buys}銘柄")
    if snapshot.database_size:
        parts.append(f"DB {snapshot.database_size}")
    return "　|　".join(parts) if parts else "本番状態を取得できませんでした"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, default=None)
    parser.add_argument("--stale-after", type=int, default=DEFAULT_STALE_AFTER)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def render(report: dict[str, Any]) -> str:
    """Render the report; its sections are already HTML."""

    from notifications.report_layout import page

    return page(
        str(report["title"]),
        str(report["lede"]),
        list(report["sections"]),
        str(report["footer"]),
    )


def plain_text(snapshot: Snapshot, report: dict[str, Any]) -> str:
    """The text/plain alternative, from the same snapshot."""

    lines = [str(report["title"]), str(report["lede"]), ""]
    lines.append(
        f"最新の予測日: {snapshot.prediction_date or '—'}"
        f" ({snapshot.prediction_status or '—'})"
    )
    lines.append(f"買い候補: {'—' if snapshot.buys is None else snapshot.buys}")
    if snapshot.settled is not None:
        day, count, correct, profit = snapshot.settled
        lines.append(
            f"直近の確定実績: {day} 買い{count}銘柄 {correct}/{count}的中 "
            f"{profit:+,.0f}円"
        )
    if snapshot.database_size:
        lines.append(f"DB使用量: {snapshot.database_size} / 512 MB")
    for warning in snapshot.warnings:
        lines.append(f"警告: {warning}")
    for problem in snapshot.errors:
        lines.append(f"取得できず: {problem}")
    lines.append("")
    lines.append(str(report["footer"]))
    return "\n".join(lines)


def main() -> int:
    args = _parser().parse_args()
    environment = EnvironmentSettings()
    snapshot = collect(environment)
    report = build_report(snapshot, args.task, args.stale_after)
    html_body = render(report)
    text_body = plain_text(snapshot, report)
    if args.dry_run:
        print(html_body)
        return 0

    from scripts.send_status_report import send_rendered

    provider = send_rendered(str(report["subject"]), text_body, html_body)
    print(
        json.dumps(
            {
                "status": "SENT",
                "provider": provider,
                "prediction_date": snapshot.prediction_date,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
