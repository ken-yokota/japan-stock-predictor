"""When a dropped window should be fired again, and when it must not be.

Both mistakes cost something. Re-running a window that was merely early burns
a pipeline for nothing; declining to fire a genuinely missing one restores the
silence the operator asked never to have again. The cases that decide it are
pinned here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.delivery_backstop import (
    NEVER_REPAIRED_ON,
    WATCHED,
    main,
    needs_repair,
    parse_verdict,
)


def _verdict(window: str = "evening", **checks: bool) -> dict[str, Any]:
    base = {"prediction": True, "email": True, "actuals": True, "summary": True}
    base.update(checks)
    return {
        "window": window,
        "for_date": "2026-09-08",
        "verdict": "CHECKED",
        "checks": {name: {"ok": ok, "detail": ""} for name, ok in base.items()},
    }


# --- when to fire -----------------------------------------------------------


def test_a_missing_summary_is_repaired() -> None:
    assert needs_repair(_verdict(summary=False), "evening") is True


def test_missing_actuals_are_repaired() -> None:
    assert needs_repair(_verdict(actuals=False), "evening") is True


def test_a_complete_evening_is_left_alone() -> None:
    assert needs_repair(_verdict(), "evening") is False


def test_each_window_watches_its_own_outcomes() -> None:
    # The evening's summary being absent says nothing about the morning, and
    # firing the morning pipeline in the evening would republish the day.
    assert needs_repair(_verdict(summary=False), "morning") is False
    assert needs_repair(_verdict(email=False), "evening") is False
    assert needs_repair(_verdict(email=False), "morning") is True


# --- when not to fire -------------------------------------------------------


def test_a_window_that_is_not_due_yet_is_not_repaired() -> None:
    # An 12:00 run must not decide the evening failed.
    early = _verdict(summary=False) | {"verdict": "NOT_YET_DUE"}
    assert needs_repair(early, "evening") is False


def test_a_holiday_is_not_a_failure() -> None:
    holiday = _verdict(summary=False, actuals=False) | {"verdict": "HOLIDAY"}
    assert needs_repair(holiday, "evening") is False


def test_no_verdict_at_all_is_not_a_failure() -> None:
    # "I could not tell" must not become "it is broken", or a network blip
    # re-runs the pipeline.
    assert needs_repair(None, "evening") is False


def test_automation_being_off_is_never_repaired() -> None:
    # Someone switched the pipeline off on purpose. It is also always false
    # outside Actions, where the variable is simply absent -- repairing on it
    # would fire every single day.
    assert "automation" in NEVER_REPAIRED_ON
    verdict = _verdict()
    verdict["checks"]["automation"] = {"ok": False, "detail": "未設定"}
    assert needs_repair(verdict, "evening") is False


def test_capacity_and_dashboard_do_not_trigger_a_rerun() -> None:
    # Neither is fixed by running the pipeline again.
    assert {"db_capacity", "dashboard"} <= NEVER_REPAIRED_ON


def test_a_missing_check_is_treated_as_passing() -> None:
    verdict = _verdict()
    del verdict["checks"]["summary"]
    assert needs_repair(verdict, "evening") is False


def test_an_unknown_window_is_refused() -> None:
    with pytest.raises(ValueError):
        needs_repair(_verdict(), "afternoon")


# --- reading the watchdog's output ------------------------------------------


def test_the_verdict_is_found_among_ordinary_log_lines() -> None:
    raw = "\n".join(["starting up", "some warning", json.dumps(_verdict()), "done"])
    parsed = parse_verdict(raw)
    assert parsed is not None
    assert parsed["window"] == "evening"


def test_the_last_verdict_wins() -> None:
    first = json.dumps(_verdict(summary=False))
    second = json.dumps(_verdict())
    parsed = parse_verdict(f"{first}\n{second}")
    assert parsed is not None
    assert parsed["checks"]["summary"]["ok"] is True


def test_output_without_a_verdict_parses_to_nothing() -> None:
    assert parse_verdict("") is None
    assert parse_verdict("no json here") is None
    assert parse_verdict("{broken json") is None
    # JSON, but not a verdict.
    assert parse_verdict('{"hello": 1}') is None


# --- the shell contract -----------------------------------------------------


def test_exit_zero_means_fire_it_again() -> None:
    missing = json.dumps(_verdict(summary=False))
    assert main(["evening", "--verdict", missing]) == 0


def test_exit_one_means_leave_it_alone() -> None:
    complete = json.dumps(_verdict())
    assert main(["evening", "--verdict", complete]) == 1
    assert main(["evening", "--verdict", "not json"]) == 1


def test_both_windows_are_offered() -> None:
    assert set(WATCHED) == {"morning", "evening"}
