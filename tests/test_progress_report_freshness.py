"""The progress mail must always describe the state at the moment it is sent.

The operator asked for this twice, because the mails kept describing work that
had already moved on: a fragment written an hour earlier, presented as if it
were current. Everything the report says about the work is therefore derived
from the task file as it is on disk at send time, the file carries its own age,
and the subject carries the fraction so the phone lock screen answers "how far
along is my request" without anything being opened.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.send_progress_report import (
    JST,
    Snapshot,
    _headline,
    _overrun_note,
    _remaining_note,
    _task_section,
    build_report,
    estimate_minutes,
)


def _write(path: Path, tasks: list[dict[str, object]]) -> Path:
    file = path / "tasks.json"
    file.write_text(json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")
    return file


def _task(
    step: str,
    tone: str,
    *,
    estimate: str = "20分",
    started_minutes_ago: float | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "step": step,
        "title": f"工程{step}",
        "estimate": estimate,
        "state": {"done": "完了", "now": "実行中", "wait": "未着手"}[tone],
        "tone": tone,
    }
    if started_minutes_ago is not None:
        entry["started_at"] = (
            datetime.now(JST) - timedelta(minutes=started_minutes_ago)
        ).isoformat(timespec="seconds")
    return entry


# --------------------------------------------------------------------------
# The fraction


def test_subject_carries_the_running_step_as_a_fraction(tmp_path: Path) -> None:
    file = _write(
        tmp_path, [_task("1/3", "done"), _task("2/3", "now"), _task("3/3", "wait")]
    )

    report = build_report(Snapshot(), file, 30)

    assert "【進捗 2/3】" in report["subject"]
    assert "工程2/3" in report["subject"]


def test_a_finished_request_still_reports_a_fraction(tmp_path: Path) -> None:
    file = _write(tmp_path, [_task("1/2", "done"), _task("2/2", "done")])

    step, headline = _headline(file)

    assert step == "2/2"
    assert "完了" in headline


def test_a_missing_task_file_does_not_lose_the_subject(tmp_path: Path) -> None:
    report = build_report(Snapshot(), tmp_path / "absent.json", 30)

    assert "【進捗】" in report["subject"]


def test_a_malformed_task_file_is_not_fatal(tmp_path: Path) -> None:
    """A broken note must degrade the mail, never prevent it."""

    file = tmp_path / "tasks.json"
    file.write_text("{not json", encoding="utf-8")

    assert _headline(file) == ("", "")
    assert build_report(Snapshot(), file, 30)["subject"]


# --------------------------------------------------------------------------
# Elapsed, remaining, and the projected finish


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("20分", 20.0),
        ("1時間", 60.0),
        ("1.5時間", 90.0),
        ("約40分", 40.0),
        ("4〜8時間", 480.0),
        ("完了", None),
        ("未定", None),
        ("—", None),
        ("", None),
    ],
)
def test_estimate_minutes_reads_what_the_note_actually_writes(
    written: str, expected: float | None
) -> None:
    assert estimate_minutes(written) == expected


def test_a_span_is_reported_at_its_upper_bound() -> None:
    """An estimate that is optimistic by default is the one that gets overtaken."""

    assert estimate_minutes("4〜8時間") == 480.0


def test_remaining_note_gives_a_time_to_come_back(tmp_path: Path) -> None:
    tasks = [
        _task("1/3", "done", estimate="完了"),
        _task("2/3", "now", estimate="20分", started_minutes_ago=5),
        _task("3/3", "wait", estimate="40分"),
    ]

    note = _remaining_note(tasks)

    assert "残り2工程" in note
    assert "完了予定" in note


def test_a_step_with_no_estimate_is_declared_rather_than_counted_as_zero(
    tmp_path: Path,
) -> None:
    tasks = [
        _task("1/2", "now", estimate="10分"),
        _task("2/2", "wait", estimate="未定"),
    ]

    note = _remaining_note(tasks)

    assert "1工程は想定を出せていません" in note


def test_an_overrun_is_named_as_still_running_not_as_a_failure() -> None:
    tasks = [_task("1/1", "now", estimate="20分", started_minutes_ago=35)]

    note = _overrun_note(tasks)

    assert "想定20分" in note
    assert "35分経過" in note
    assert "失敗ではなく" in note


def test_a_step_inside_its_estimate_is_not_flagged() -> None:
    running = [_task("1/1", "now", estimate="60分", started_minutes_ago=5)]

    assert _overrun_note(running) == ""


def test_the_elapsed_column_is_rendered_for_the_running_step(tmp_path: Path) -> None:
    file = _write(
        tmp_path,
        [
            _task("1/2", "now", estimate="20分", started_minutes_ago=13),
            _task("2/2", "wait"),
        ],
    )

    html = _task_section(file, 30)

    assert "経過" in html
    assert "13分" in html


# --------------------------------------------------------------------------
# The note's own age


def test_a_stale_note_says_so_instead_of_passing_as_current(tmp_path: Path) -> None:
    file = _write(tmp_path, [_task("1/1", "now")])
    old = (datetime.now(JST) - timedelta(minutes=90)).timestamp()
    import os

    os.utime(file, (old, old))

    html = _task_section(file, 30)

    assert "⚠" in html
    assert "更新されていません" in html


def test_a_fresh_note_reports_its_age_without_a_warning(tmp_path: Path) -> None:
    file = _write(tmp_path, [_task("1/1", "now")])

    html = _task_section(file, 30)

    assert "分前に更新されたもの" in html
    assert "更新されていません" not in html


def test_no_task_file_says_so_rather_than_an_empty_table(tmp_path: Path) -> None:
    html = _task_section(tmp_path / "absent.json", 30)

    assert "登録されていません" in html


# --------------------------------------------------------------------------
# The operator's own sentence


def test_notes_are_rendered_above_the_tables(tmp_path: Path) -> None:
    """A reordered task list hides its own cost; the sentence is where it goes."""

    file = _write(tmp_path, [_task("1/1", "now")])

    report = build_report(
        Snapshot(), file, 30, ("6/8 だった並列化は 7/9 に後ろ倒しになりました",)
    )
    sections = list(report["sections"])

    assert "この報告の要点" in sections[0]
    assert "後ろ倒し" in sections[0]


def test_every_scheduled_time_matches_its_workflows_first_cron() -> None:
    """A time nobody fires at is worse than no table: the operator waits for it.

    "18:40 日次サマリーの配信" sat in this table for weeks while the cron said
    08:00 UTC, i.e. 17:00 JST. This compares the table against the workflow
    files themselves so the next schedule change cannot drift silently.
    """

    import re

    from scripts.send_progress_report import SCHEDULE

    workflows = Path(".github/workflows")
    for clock, name, _detail, filename in SCHEDULE:
        source = (workflows / filename).read_text(encoding="utf-8")
        crons = re.findall(r'cron:\s*"([^"]+)"', source)
        assert crons, f"{filename} has no cron"
        minutes, hours = crons[0].split()[0], crons[0].split()[1]
        first_utc = int(hours.split(",")[0]) * 60 + int(minutes.split(",")[0])
        jst = (first_utc + 9 * 60) % (24 * 60)
        assert f"{jst // 60:02d}:{jst % 60:02d}" == clock, (
            f"{name}: {filename} fires at {jst // 60:02d}:{jst % 60:02d} JST, "
            f"but the progress mail says {clock}"
        )


# --------------------------------------------------------------------------
# The note must not be able to pass off a stale picture as current
#
# This is the failure it exists to catch: the working note said "1/8 実行中"
# while three of its steps had already been committed, and every mail sent in
# that window described work that had moved on. A count of commits the note has
# not seen is mechanical, so it cannot be forgotten the way the note was.


def test_a_note_older_than_recent_commits_is_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.send_progress_report as module

    file = _write(tmp_path, [_task("1/8", "now")])
    monkeypatch.setattr(module, "_commits_since", lambda _moment: 3)

    html = module._task_section(file, 30)

    assert "3 件のコミット" in html
    assert "実際の進捗が進んでいます" in html


def test_a_note_with_no_commits_behind_it_is_not_flagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.send_progress_report as module

    file = _write(tmp_path, [_task("1/8", "now")])
    monkeypatch.setattr(module, "_commits_since", lambda _moment: 0)

    html = module._task_section(file, 30)

    assert "件のコミット" not in html


def test_the_commit_section_lists_what_actually_landed() -> None:
    import scripts.send_progress_report as module

    html = module._commit_section(
        (("08/25 10:29", "4803401", "Put the evaluation on the Test page"),)
    )

    assert "4803401" in html
    assert "Put the evaluation on the Test page" in html
    assert "工程表と食い違う場合は" in html


def test_no_commits_says_so_rather_than_showing_an_empty_table() -> None:
    import scripts.send_progress_report as module

    assert "コミットはありません" in module._commit_section(())


def test_the_report_carries_the_commit_section(tmp_path: Path) -> None:
    file = _write(tmp_path, [_task("1/8", "now")])

    report = build_report(Snapshot(), file, 30)

    assert any("直近の作業（コミット）" in item for item in report["sections"])


# --------------------------------------------------------------------------
# Every work item runs all six stages, and a skipped one must be visible


def test_the_stage_cell_shows_the_whole_sequence_not_just_the_current_one() -> None:
    """"実装" alone says nothing about whether 調査 and 仮説 happened."""

    from scripts.send_progress_report import STAGES, _stage_cell

    html = _stage_cell("実装")

    for stage in STAGES:
        assert stage in html


def test_the_current_stage_is_marked_and_the_finished_ones_are_behind_it() -> None:
    from notifications.report_layout import UP
    from scripts.send_progress_report import _stage_cell

    html = _stage_cell("テスト")
    before, _, after = html.partition("テスト")

    assert UP in before  # 調査..実装 are drawn as passed
    assert "#cbd5e1" in after  # 修正 is still ahead


def test_a_missing_stage_renders_as_a_dash_rather_than_a_guess() -> None:
    from scripts.send_progress_report import _stage_cell

    assert "—" in _stage_cell("")


def test_an_unknown_stage_is_shown_verbatim_not_dropped() -> None:
    from scripts.send_progress_report import _stage_cell

    assert "待機中" in _stage_cell("待機中")


def test_the_task_table_carries_the_stage_column(tmp_path: Path) -> None:
    from scripts.send_progress_report import _task_section

    entry = _task("2/9", "now")
    entry["stage"] = "テスト"
    file = tmp_path / "tasks.json"
    file.write_text(
        json.dumps({"tasks": [entry]}, ensure_ascii=False), encoding="utf-8"
    )

    html = _task_section(file, 30)

    assert "段階" in html
    assert "テスト" in html
