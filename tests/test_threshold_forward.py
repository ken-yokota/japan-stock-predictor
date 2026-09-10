"""The threshold decision, and the ways its forward check can be hollowed out.

0.625 was chosen by sweeping the sessions it is now judged on, which is the one
thing the check exists to correct for. It corrects for it only if it refuses:
to read a session the sweep saw, to call a winner before there is enough
evidence, and to be re-swept on the fresh sessions and re-adopted.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_threshold import (
    after_decision,
    arm,
    band_test,
    jackknife,
    profit_factor,
    verdict,
)

# Above the return threshold, so a row's fate turns on its probability alone
# unless a test says otherwise.
CONVICTION = 0.01

DECIDED = date(2026, 9, 10)
RECORD = Path("docs/research/2026-09-10-threshold.json")


def _row(
    day: str, probability: float, profit: float, predicted: float = CONVICTION
) -> dict[str, Any]:
    return {
        "prediction_date": date.fromisoformat(day),
        "ticker": "7203",
        "probability_up": probability,
        "predicted_return": predicted,
        "net_profit": profit,
    }


# --- what may be scored -----------------------------------------------------


def test_only_sessions_after_the_decision_are_scored() -> None:
    rows = [_row(day, 0.7, 100.0) for day in ("2026-09-09", "2026-09-11")]
    assert [r["prediction_date"] for r in after_decision(rows, DECIDED)] == [
        date(2026, 9, 11)
    ]


def test_the_decision_date_itself_is_not_scored() -> None:
    # It is the last day of the sweep, so it is development by definition.
    assert after_decision([_row("2026-09-10", 0.7, 100.0)], DECIDED) == []


def test_nothing_after_the_decision_yet_scores_nothing() -> None:
    assert after_decision([_row("2026-08-20", 0.7, 100.0)], DECIDED) == []


# --- the arms ---------------------------------------------------------------


def test_the_raised_threshold_takes_a_subset_of_the_old_one() -> None:
    # The band test depends on this and would be meaningless without it.
    rows = [
        _row("2026-09-11", 0.58, -500.0),
        _row("2026-09-11", 0.61, -900.0),
        _row("2026-09-11", 0.70, 400.0),
    ]
    assert arm(rows, 0.60, 0.003)["trades"] == 2
    assert arm(rows, 0.625, 0.003)["trades"] == 1


def test_a_low_conviction_row_is_rejected_however_confident_it_looks() -> None:
    # The rule is an AND. A 0.95 probability on a predicted +0.1% move was
    # never a BUY, and admitting it here would score a different strategy.
    rows = [_row("2026-09-11", 0.95, 500.0, predicted=0.001)]
    assert arm(rows, 0.625, 0.003)["trades"] == 0


def test_the_declined_band_holds_only_rows_the_old_rule_would_have_bought() -> None:
    rows = [
        _row("2026-09-11", 0.61, -100.0, predicted=0.001),
        _row("2026-09-11", 0.61, -200.0, predicted=0.010),
    ]
    assert band_test(rows, 0.60, 0.625, 0.003)["trades"] == 1


def test_profit_factor_is_gross_profit_over_gross_loss() -> None:
    assert profit_factor([300.0, -100.0, -200.0]) == pytest.approx(1.0)
    assert profit_factor([600.0, -200.0]) == pytest.approx(3.0)


def test_an_arm_that_has_not_lost_yet_has_no_profit_factor() -> None:
    # Not infinite -- unmeasured. Printing a large number for a six-trade
    # winning streak reads as a strong result, which is the opposite of true.
    assert profit_factor([100.0, 200.0]) is None
    assert arm([_row("2026-09-11", 0.7, 100.0)], 0.625, 0.003)["profit_factor"] is None


# --- the declined band ------------------------------------------------------


def test_the_band_holds_exactly_the_trades_the_rise_declines() -> None:
    rows = [
        _row("2026-09-11", 0.599, -100.0),
        _row("2026-09-11", 0.600, -200.0),
        _row("2026-09-11", 0.624, -300.0),
        _row("2026-09-11", 0.625, 900.0),
    ]
    got = band_test(rows, 0.60, 0.625, 0.003)
    assert got["trades"] == 2


def test_a_session_with_no_band_trade_is_dropped_not_entered_as_zero() -> None:
    # A zero would claim the band broke even that day, which is a different
    # statement from the band not having traded.
    rows = [_row("2026-09-11", 0.61, -100.0), _row("2026-09-14", 0.90, 5000.0)]
    assert band_test(rows, 0.60, 0.625, 0.003)["sessions"] == 1


def test_the_band_is_measured_per_session_not_per_trade() -> None:
    # Twenty-two tickers on one morning share a market; counting them as
    # twenty-two draws is what makes a coin flip look significant.
    rows = [_row("2026-09-11", 0.61, -100.0) for _ in range(22)]
    rows += [_row("2026-09-14", 0.61, -100.0) for _ in range(22)]
    got = band_test(rows, 0.60, 0.625, 0.003)
    assert got["trades"] == 44
    assert got["sessions"] == 2
    assert got["verdict"] == "PENDING"


def test_a_band_seen_on_too_few_sessions_gives_no_verdict() -> None:
    rows = [_row(f"2026-09-1{n}", 0.61, -100.0) for n in range(1, 5)]
    assert band_test(rows, 0.60, 0.625, 0.003)["verdict"] == "PENDING"


def test_a_band_that_loses_on_every_session_is_called_a_loser() -> None:
    rows = [_row(f"2026-09-{n:02d}", 0.61, -800.0) for n in range(11, 25)]
    got = band_test(rows, 0.60, 0.625, 0.003)
    assert got["wilcoxon_p_one_sided_less"] < 0.05
    assert got["reads_as"] == "the declined band lost money"


def test_a_band_that_merely_wobbles_is_not_called_a_loser() -> None:
    profits = [-100.0, 120.0, -80.0, 90.0, -110.0, 130.0, -70.0, 60.0, 40.0, -50.0]
    rows = [
        _row(f"2026-09-{11 + n:02d}", 0.61, value) for n, value in enumerate(profits)
    ]
    got = band_test(rows, 0.60, 0.625, 0.003)
    assert got["wilcoxon_p_one_sided_less"] >= 0.05
    assert "not distinguishable" in got["reads_as"]


# --- leaving one session out -----------------------------------------------


def test_an_arm_carried_by_one_morning_is_exposed_by_removing_it() -> None:
    rows = [_row(f"2026-09-{n:02d}", 0.7, -100.0) for n in range(11, 21)]
    rows.append(_row("2026-09-21", 0.7, 5000.0))
    got = jackknife(rows, 0.625, 0.003)
    assert got is not None
    assert got["lowest_without"] == "2026-09-21"
    assert got["lowest"] == pytest.approx(0.0)
    assert got["sessions_whose_removal_drops_it_below_one"] == 1


def test_an_arm_that_wins_broadly_survives_every_removal() -> None:
    rows = [_row(f"2026-09-{n:02d}", 0.7, 300.0) for n in range(11, 21)]
    rows += [_row(f"2026-09-{n:02d}", 0.7, -100.0) for n in range(11, 21)]
    got = jackknife(rows, 0.625, 0.003)
    assert got is not None
    assert got["sessions_whose_removal_drops_it_below_one"] == 0


def test_too_few_sessions_to_leave_one_out() -> None:
    rows = [_row("2026-09-11", 0.7, 100.0), _row("2026-09-14", 0.7, -50.0)]
    assert jackknife(rows, 0.625, 0.003) is None


# --- when a verdict may be given --------------------------------------------


def _arm(sessions: int, factor: float | None) -> dict[str, Any]:
    return {"sessions": sessions, "profit_factor": factor}


def test_no_verdict_before_the_session_floor() -> None:
    got = verdict(_arm(7, 2.5), _arm(7, 0.8), {"share_above_one": 0.99}, minimum=20)
    assert got["verdict"] == "PENDING"
    assert "floor is 20" in got["reason"]


def test_a_spectacular_short_run_is_still_pending() -> None:
    # Seven good mornings is how the in-sample 0.700 arm looked, and it fell
    # apart on the second half of the very sample it was chosen from.
    got = verdict(_arm(7, 9.9), _arm(7, 0.5), {"share_above_one": 1.0}, minimum=20)
    assert got["verdict"] == "PENDING"


def test_it_holds_when_it_beats_the_old_rule_and_the_bootstrap_agrees() -> None:
    got = verdict(_arm(25, 1.21), _arm(25, 0.88), {"share_above_one": 0.94}, minimum=20)
    assert got["verdict"] == "HOLDS"


def test_ahead_but_unconvincing_is_unproven_not_a_win() -> None:
    got = verdict(_arm(25, 1.05), _arm(25, 0.99), {"share_above_one": 0.61}, minimum=20)
    assert got["verdict"] == "UNPROVEN"


def test_losing_to_the_old_rule_out_of_sample_is_reported_as_a_failure() -> None:
    got = verdict(_arm(25, 0.82), _arm(25, 1.04), {"share_above_one": 0.10}, minimum=20)
    assert got["verdict"] == "FAILS"
    assert "did not beat" in got["reason"]


def test_an_arm_with_no_losses_yet_cannot_be_judged() -> None:
    got = verdict(_arm(25, None), _arm(25, 0.9), None, minimum=20)
    assert got["verdict"] == "PENDING"


def test_a_missing_bootstrap_never_produces_a_win() -> None:
    got = verdict(_arm(25, 1.4), _arm(25, 0.8), None, minimum=20)
    assert got["verdict"] == "UNPROVEN"


# --- the record itself ------------------------------------------------------


def test_the_record_states_the_thresholds_the_check_may_score() -> None:
    check = json.loads(RECORD.read_text(encoding="utf-8"))["forward_check"]
    assert check["baseline_threshold"] == pytest.approx(0.60)
    assert check["adopted_threshold"] == pytest.approx(0.625)
    assert check["minimum_sessions_for_verdict"] >= 20


def test_the_record_forbids_re_sweeping_on_the_fresh_sessions() -> None:
    # Sweeping again and adopting the new winner would repeat the selection
    # this check exists to audit, on the only clean sample there is.
    check = json.loads(RECORD.read_text(encoding="utf-8"))["forward_check"]
    assert "no_resweep" in check


def test_the_record_keeps_the_correction_that_produced_it() -> None:
    # An earlier pass in the session reported 174 trades and a profit factor
    # of 0.840, and neither was the live record. The corrected numbers are the
    # ones the threshold rests on, and the record has to keep saying so --
    # otherwise the superseded figures come back the next time someone reads
    # a summary instead of the file.
    doc = json.loads(RECORD.read_text(encoding="utf-8"))
    assert "174" in doc["correction"]["what_was_wrong"]
    assert doc["population"]["trades_under_the_old_rule"] == 110


def test_the_record_keeps_the_number_that_argues_against_the_change() -> None:
    # 65% of bootstrap draws above 1 is a lean, not a finding, and it is the
    # figure most easily dropped from a write-up that wants to look decisive.
    doc = json.loads(RECORD.read_text(encoding="utf-8"))
    assert doc["evidence"]["bootstrap_at_0625"]["share_above_one"] < 0.70


def test_the_record_does_not_claim_the_change_is_established() -> None:
    doc = json.loads(RECORD.read_text(encoding="utf-8"))
    assert "not an established improvement" in doc["verdict_as_shipped"]


def test_the_record_keeps_its_own_in_sample_caveat() -> None:
    # The number came from the sample it is measured on. If that sentence ever
    # falls out of the record, the 1.130 starts reading as a proven result.
    limits = " ".join(json.loads(RECORD.read_text(encoding="utf-8"))["known_limits"])
    assert "in-sample" in limits


def test_the_shipped_threshold_is_the_one_the_record_adopted() -> None:
    from data.config import load_app_config

    check = json.loads(RECORD.read_text(encoding="utf-8"))["forward_check"]
    shipped = load_app_config("config").trading.signal.probability_up_threshold
    assert shipped == pytest.approx(check["adopted_threshold"])
