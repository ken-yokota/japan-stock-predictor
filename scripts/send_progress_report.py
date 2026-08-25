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
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from data.env import EnvironmentSettings
from notifications.report_layout import (
    BAND,
    GOOD_BG,
    badge,
    cell,
    row,
    section,
    table,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_STALE_AFTER = 30

# The first cron of each workflow, converted from UTC to JST, paired with the
# workflow it belongs to. A time written here that no workflow fires at is
# worse than no table -- the operator waits for a mail that was never
# scheduled, which is exactly what "18:40" did for the evening summary.
SCHEDULE: tuple[tuple[str, str, str, str], ...] = (
    ("07:10", "履歴データの取得", "日足51系列と米国金利", "morning_prefetch.yml"),
    (
        "08:10",
        "予測の計算と保存",
        "実勢値12系列を取得して予測",
        "morning_prediction.yml",
    ),
    ("08:45", "予測メールの配信", "買い候補と根拠", "morning_email.yml"),
    (
        "15:45",
        "実績の確定と答え合わせ",
        "始値・終値から成績を確定（失敗時 15:55 / 16:10 に再試行）",
        "close_update.yml",
    ),
    (
        "17:00",
        "大引け後メールの配信",
        "その日の答え合わせと運用状況",
        "daily_summary.yml",
    ),
)


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


def _recent_commits(limit: int = 8) -> tuple[tuple[str, str, str], ...]:
    """What was actually committed, whatever the working note says.

    The note is written by hand and has already been left behind twice while
    work carried on. Commits are not: they carry their own timestamp and they
    cannot be forgotten. Printing them beside the note means a stale note is
    visible as stale rather than read as current.
    """

    raw = _git("log", f"-{limit}", "--format=%h\x1f%cI\x1f%s")
    rows: list[tuple[str, str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3:
            continue
        digest, stamp, subject = parts
        try:
            when = datetime.fromisoformat(stamp).astimezone(JST)
        except ValueError:
            continue
        rows.append((f"{when:%m/%d %H:%M}", digest, subject))
    return tuple(rows)


def _commits_since(moment: datetime) -> int:
    """How many commits landed after the working note was last written."""

    raw = _git("log", "--since", moment.isoformat(), "--format=%h")
    return len([line for line in raw.splitlines() if line.strip()])


def _commit_section(rows: Sequence[tuple[str, str, str]]) -> str:
    if not rows:
        return section(
            "直近の作業（コミット）", "", "この期間のコミットはありません。"
        )
    return section(
        "直近の作業（コミット）",
        table(
            [("時刻", "center"), ("SHA", "center"), ("内容", "left")],
            [
                row(
                    [
                        cell(when, align="center"),
                        cell(digest, align="center", muted=True),
                        cell(subject, nowrap=False),
                    ],
                    "#fff" if index % 2 == 0 else BAND,
                )
                for index, (when, digest, subject) in enumerate(rows)
            ],
            min_width=520,
        ),
        "この節は送信時に git から読み直しています。工程表と食い違う場合は"
        "こちらが実際に起きたことです。",
    )


def _note_section(notes: Sequence[str]) -> str:
    """The sentences the tables cannot carry.

    A reordered task list hides its own cost: "6/8 だった並列化は 7/9 に
    後ろ倒し" is the line the operator needs, and no table produces it.
    """

    return section(
        "この報告の要点",
        table(
            [("内容", "left")],
            [
                row([cell(note, nowrap=False)], "#fff" if index % 2 == 0 else BAND)
                for index, note in enumerate(notes)
            ],
            min_width=420,
        ),
    )


def _tasks(path: Path | None) -> list[dict[str, Any]]:
    """The working note's task rows, or an empty list when there is no note."""

    if path is None or not path.exists():
        return []
    try:
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in payload.get("tasks", []) if isinstance(item, dict)]


def _headline(path: Path | None) -> tuple[str, str]:
    """Return the subject's ``n/m`` count and the step it belongs to.

    The operator reads the subject on a phone and has to know how far along
    their request is without opening anything, so the count is not optional.
    It is taken from the step that is running; if nothing is running it falls
    back to how many of the steps are finished, which is still a fraction.
    """

    tasks = _tasks(path)
    if not tasks:
        return "", ""
    running = next(
        (item for item in tasks if str(item.get("tone")) == "now"),
        None,
    )
    if running is not None:
        return str(running.get("step") or ""), str(running.get("title") or "")
    done = sum(1 for item in tasks if str(item.get("tone")) == "done")
    if done == len(tasks):
        return f"{done}/{len(tasks)}", "全工程が完了しました"
    return f"{done}/{len(tasks)}", "実行中の工程はありません"


_ESTIMATE_UNITS = (
    ("時間", 60.0),
    ("h", 60.0),
    ("分", 1.0),
    ("m", 1.0),
)


def estimate_minutes(text_value: str) -> float | None:
    """Read "40分", "1時間", "4〜8時間" as minutes; return None when it is not a span.

    "完了" and "未定" are not durations, and turning them into zero would make
    the remaining-time line quietly optimistic. They come back as None and the
    note says how many steps could not be estimated.
    """

    cleaned = str(text_value).strip().replace("約", "")
    if not cleaned or cleaned in {"完了", "未定", "—", "-"}:
        return None
    for separator in ("〜", "~", "-"):
        if separator in cleaned:
            # An "4〜8時間" span is reported at its upper bound: an estimate
            # that is optimistic by default is the one that gets overtaken.
            cleaned = cleaned.split(separator)[-1]
    for suffix, scale in _ESTIMATE_UNITS:
        if cleaned.endswith(suffix):
            head = cleaned[: -len(suffix)].strip()
            try:
                return float(head) * scale
            except ValueError:
                return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _elapsed_minutes(item: dict[str, Any]) -> float | None:
    """How long this step has been running, from the stamp the note carries."""

    raw = item.get("started_at")
    if not raw:
        return None
    try:
        started = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=JST)
    return (datetime.now(JST) - started).total_seconds() / 60


def _minutes_text(minutes: float | None) -> str:
    if minutes is None:
        return "—"
    if minutes < 90:
        return f"{minutes:.0f}分"
    return f"{minutes / 60:.1f}時間"


def _remaining_note(tasks: list[dict[str, Any]]) -> str:
    """Remaining estimate and a projected finish, both required by the agreement.

    "It is running" and "I know when to come back" are different reports, and
    only the second one lets the operator stop watching.
    """

    outstanding = [item for item in tasks if str(item.get("tone")) != "done"]
    if not outstanding:
        return "全工程が完了しました。"
    known = [estimate_minutes(str(item.get("estimate", ""))) for item in outstanding]
    minutes = sum(value for value in known if value is not None)
    unknown = sum(1 for value in known if value is None)
    running = next((item for item in tasks if str(item.get("tone")) == "now"), None)
    if running is not None:
        # The running step's estimate is already partly spent; counting it in
        # full would keep the projected finish sliding away from the clock.
        elapsed = _elapsed_minutes(running)
        running_estimate = estimate_minutes(str(running.get("estimate", "")))
        if elapsed is not None and running_estimate is not None:
            minutes = max(minutes - min(elapsed, running_estimate), 0.0)
    finish = datetime.now(JST) + timedelta(minutes=minutes)
    parts = [
        f"残り{len(outstanding)}工程 / 残り想定 約{_minutes_text(minutes)} / "
        f"完了予定 {finish:%H:%M}頃"
    ]
    if unknown:
        parts.append(f"うち{unknown}工程は想定を出せていません。")
    return "　".join(parts)


def _overrun_note(tasks: list[dict[str, Any]]) -> str:
    """Name an overrun rather than letting a silent one read as on schedule."""

    running = next((item for item in tasks if str(item.get("tone")) == "now"), None)
    if running is None:
        return ""
    elapsed = _elapsed_minutes(running)
    estimate = estimate_minutes(str(running.get("estimate", "")))
    if elapsed is None or estimate is None or elapsed <= estimate:
        return ""
    return (
        f"⚠ {running.get('step', '')} は想定{_minutes_text(estimate)}に対し"
        f"{_minutes_text(elapsed)}経過しています。失敗ではなく、まだ実行中です。"
    )


def _task_section(path: Path | None, stale_after: int) -> str:
    """The working note: every column re-read from the file at send time.

    The operator asked that a progress mail always carry the *current* picture.
    Nothing here is remembered between sends -- the rows, the elapsed times and
    the projected finish are all derived from the file as it is on disk now,
    and the file's own age is printed so a note nobody updated cannot pass
    itself off as current.
    """

    if path is None or not path.exists():
        return section(
            "いま進めているタスク",
            "",
            "作業中のタスクは登録されていません。定時の自動実行のみが動いています。",
        )
    age = (datetime.now(UTC).timestamp() - path.stat().st_mtime) / 60
    tasks = _tasks(path)
    rows = []
    for index, item in enumerate(tasks):
        tone = str(item.get("tone", "wait"))
        elapsed = _elapsed_minutes(item) if tone == "now" else None
        rows.append(
            row(
                [
                    cell(str(item.get("step", "")), align="center"),
                    cell(str(item.get("title", "")), nowrap=False),
                    cell(str(item.get("estimate", "—")), align="right"),
                    cell(_minutes_text(elapsed), align="right", muted=elapsed is None),
                    cell(
                        badge(str(item.get("state", "未着手")), tone),
                        align="center",
                    ),
                ],
                GOOD_BG if tone == "now" else ("#fff" if index % 2 == 0 else BAND),
            )
        )

    body = (
        table(
            [
                ("工程", "center"),
                ("内容", "left"),
                ("想定", "right"),
                ("経過", "right"),
                ("状態", "center"),
            ],
            rows,
            min_width=540,
        )
        if rows
        else ""
    )
    lines = [_remaining_note(tasks)]
    overrun = _overrun_note(tasks)
    if overrun:
        lines.insert(0, overrun)
    written = datetime.fromtimestamp(path.stat().st_mtime, tz=JST)
    newer = _commits_since(written)
    if newer:
        # The failure this catches: the note said 1/8 while three of the steps
        # had already been committed. A count of commits the note has not seen
        # is mechanical, so it cannot be forgotten the way the note itself was.
        lines.append(
            f"⚠ この工程表の更新後に {newer} 件のコミットがあります。"
            "表より実際の進捗が進んでいます。下の「直近の作業」を参照してください。"
        )
    if age > stale_after:
        lines.append(
            f"⚠ この記録は{age:.0f}分前のもので、{stale_after}分以上"
            "更新されていません。現在の作業を反映していない可能性があります。"
        )
    else:
        lines.append(f"この記録は{age:.0f}分前に更新されたものです。")
    return section("いま進めているタスク", body, "<br>".join(lines))


def build_report(
    snapshot: Snapshot,
    task_file: Path | None,
    stale_after: int,
    notes: Sequence[str] = (),
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

    sections = [
        *( [_note_section(notes)] if notes else [] ),
        _task_section(task_file, stale_after),
        _commit_section(_recent_commits()),
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
    step, headline = _headline(task_file)
    fraction = f" {step}" if step else ""
    headline_text = f"{headline}／" if headline else ""
    return {
        "subject": f"【進捗{fraction}】{headline_text}定期報告 {now:%m/%d %H:%M}",
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

    now = datetime.now(JST)
    today = now.date()
    upcoming: list[tuple[str, str, str]] = []
    schedule = SCHEDULE
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
            for clock, name, detail, _workflow in schedule:
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
        holiday_note
        or "すべて自動で実行されます。操作は不要です。"
        "GitHub Actions のcronは定刻より30〜60分遅れて起動することがあり、"
        "実測でもその範囲で遅れています。",
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
    parser.add_argument(
        "--note",
        action="append",
        default=None,
        help="この報告で伝えたい一文。新しい依頼が何を後ろ倒しにしたか等。",
    )
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
    report = build_report(snapshot, args.task, args.stale_after, args.note or ())
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
