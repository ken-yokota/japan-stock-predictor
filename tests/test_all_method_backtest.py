"""Scoring rules for the all-family replay.

The fitting side of the backtest cannot be unit-tested cheaply -- it needs a
database and minutes per session -- but the scoring side is pure, and it is
where a quiet arithmetic error does the most damage: a wrong win rate is a
number the operator would act on, and nothing about it looks wrong.

One of these exists because the code was written incorrectly the first time.
``a > 0.0 == b`` is a chained comparison in Python, so the direction-accuracy
count silently meant ``(a > 0.0) and (0.0 == b)`` and counted almost nothing.
It produced a plausible small number rather than an error.
"""

from __future__ import annotations

import pytest

from scripts.report_all_method_backtest import _coverage, _score, evaluate, render

LEVELS = {"q0.1": -0.01, "q0.5": 0.005, "q0.9": 0.02}


def _row(
    day: str,
    ticker: str,
    *,
    arm: str = "ridge",
    median: float = 0.005,
    low: float = -0.01,
    high: float = 0.02,
    probability: float = 0.65,
    actual: float | None = 0.01,
    status: str = "OK",
) -> dict[str, object]:
    return {
        "date": day,
        "ticker": ticker,
        "arm": arm,
        "label": "Ridge回帰",
        "status": status,
        "predicted_return": median,
        "probability_up": probability,
        "spread_kind": "residual",
        "quantiles": {"q0.1": low, "q0.5": median, "q0.9": high},
        "actual_return": actual,
    }


def _payload(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "from": "2026-08-07",
        "to": "2026-08-28",
        "sessions": ["2026-08-07", "2026-08-10"],
        "tickers": ["9101", "9104"],
        "levels": [0.1, 0.5, 0.9],
        "rows": rows,
    }


# --- direction accuracy, the one that was wrong -------------------------


def test_direction_accuracy_counts_agreement_not_a_chained_comparison() -> None:
    """Two up-calls that both rose is 100%, not the 0% chaining produced."""

    rows = [
        _row("2026-08-07", "9101", median=0.005, actual=0.01),
        _row("2026-08-10", "9104", median=0.005, actual=0.02),
    ]
    result = _score("ridge", "Ridge回帰", "r", rows)
    assert result.direction_accuracy == pytest.approx(1.0)


def test_a_down_call_that_falls_also_counts_as_a_hit() -> None:
    rows = [
        _row("2026-08-07", "9101", median=-0.005, actual=-0.01),
        _row("2026-08-10", "9104", median=-0.005, actual=-0.02),
    ]
    assert _score("r", "R", "r", rows).direction_accuracy == pytest.approx(1.0)


def test_calls_that_point_the_wrong_way_score_zero() -> None:
    rows = [
        _row("2026-08-07", "9101", median=0.005, actual=-0.01),
        _row("2026-08-10", "9104", median=-0.005, actual=0.02),
    ]
    assert _score("r", "R", "r", rows).direction_accuracy == pytest.approx(0.0)


def test_a_mix_lands_between() -> None:
    rows = [
        _row("2026-08-07", "9101", median=0.005, actual=0.01),
        _row("2026-08-10", "9104", median=0.005, actual=-0.01),
    ]
    assert _score("r", "R", "r", rows).direction_accuracy == pytest.approx(0.5)


# --- exposure ------------------------------------------------------------


def test_five_names_on_one_day_is_one_day_of_exposure() -> None:
    """Summing every position would flatter a rule that piles into one session."""

    same_day = [_row("2026-08-07", str(9100 + i), actual=0.01) for i in range(5)]
    result = _score("r", "R", "r", same_day)
    assert result.positions == 5
    assert result.sessions == 1
    assert result.total_return == pytest.approx(0.01)


def test_the_same_positions_spread_over_days_compound_into_more() -> None:
    spread = [_row(f"2026-08-{7 + i:02d}", "9101", actual=0.01) for i in range(5)]
    assert _score("r", "R", "r", spread).total_return == pytest.approx(0.05)


def test_an_unsettled_session_is_dropped_rather_than_scored_as_flat() -> None:
    rows = [
        _row("2026-08-07", "9101", actual=0.01),
        _row("2026-08-10", "9104", actual=None),
    ]
    result = _score("r", "R", "r", rows)
    assert result.positions == 1
    assert result.win_rate == pytest.approx(1.0)


def test_a_rule_that_never_fired_is_not_reported_as_break_even() -> None:
    result = _score("r", "R", "r", [])
    assert result.positions == 0
    assert result.win_rate is None
    assert result.direction_accuracy is None


# --- the rules -----------------------------------------------------------


def test_each_rule_selects_on_the_quantile_it_names() -> None:
    rows = [
        # median above the hurdle, lower tenth negative
        _row("2026-08-07", "9101", median=0.01, low=-0.02, high=0.03),
        # median below, but the whole band is positive
        _row("2026-08-10", "9104", median=0.001, low=0.0005, high=0.004),
    ]
    results = {r.rule: r for r in evaluate(_payload(rows), 0.003)}
    median_rule = next(
        k for k in results if k.startswith("P50 > ") and "P(上昇)" not in k
    )
    assert results[median_rule].positions == 1
    assert results["P90 > 0（下振れでもプラス）"].positions == 1
    # p90 clears the hurdle on both rows, which is the point of showing it.
    upper_rule = next(k for k in results if k.startswith("上振れ10%"))
    assert results[upper_rule].positions == 2


def test_the_probability_gate_can_only_reduce_what_the_median_selected() -> None:
    rows = [
        _row("2026-08-07", "9101", median=0.01, probability=0.65),
        _row("2026-08-10", "9104", median=0.01, probability=0.40),
    ]
    results = {r.rule: r for r in evaluate(_payload(rows), 0.003)}
    plain = next(
        results[k] for k in results if k.startswith("P50 > ") and "P(上昇)" not in k
    )
    gated = next(results[k] for k in results if "P(上昇)" in k)
    assert plain.positions == 2
    assert gated.positions == 1


def test_a_failed_row_never_becomes_a_position() -> None:
    rows = [_row("2026-08-07", "9101", median=0.01, status="FAILED")]
    assert all(r.positions == 0 for r in evaluate(_payload(rows), 0.003))


# --- coverage ------------------------------------------------------------


def test_coverage_counts_outcomes_inside_the_eighty_percent_band() -> None:
    rows = [
        _row("2026-08-07", "9101", low=-0.01, high=0.02, actual=0.005),
        _row("2026-08-10", "9104", low=-0.01, high=0.02, actual=0.05),
    ]
    ((_, _, total, covered),) = _coverage(_payload(rows))
    assert total == 2
    assert covered == pytest.approx(0.5)


def test_the_band_edges_count_as_inside() -> None:
    rows = [_row("2026-08-07", "9101", low=-0.01, high=0.02, actual=0.02)]
    ((_, _, _, covered),) = _coverage(_payload(rows))
    assert covered == pytest.approx(1.0)


# --- the report ----------------------------------------------------------


def test_the_report_says_this_is_a_replay_and_too_small_to_conclude_from() -> None:
    """The caveat has to travel with the numbers, not sit in a commit message."""

    text = "\n".join(render(_payload([_row("2026-08-07", "9101")]), 0.003))
    assert "再現です" in text
    assert "実績ではありません" in text
    assert "偶然と区別できません" in text
    assert "被覆" in text
    # The Pxx convention must travel with the numbers.
    assert "P90 は90%の確率で上回る水準" in text
    assert "Pxx別の精度" in text
    assert "手法ごとの閾値" in text
    assert "選んだことによる下駄" in text
    # The Pxx convention must travel with the numbers.
    assert "P90 は90%の確率で上回る水準" in text
    assert "Pxx別の精度" in text
    assert "手法ごとの閾値" in text
    assert "選んだことによる下駄" in text


# --- topping up a run that lost a block ---------------------------------


def test_a_top_up_run_replaces_the_failed_pairs_rather_than_doubling_them(
    tmp_path,
) -> None:
    """A dropped connection removes a contiguous block, not a random sample.

    Tasks are submitted date by date, so a thirty-second outage takes out most
    of one session and leaves it represented by whichever tickers happened to
    fall outside the window. Reporting that is worse than reporting nothing for
    the day, so the failures are re-run and merged.
    """

    import json

    from scripts.report_all_method_backtest import _combined

    main = tmp_path / "main.json"
    main.write_text(
        json.dumps(
            {
                "from": "2026-08-07",
                "to": "2026-08-28",
                "sessions": ["2026-08-07", "2026-08-21"],
                "tickers": ["9101", "9104"],
                "levels": [0.1, 0.5, 0.9],
                "rows": [
                    _row("2026-08-07", "9101", median=0.001),
                    _row("2026-08-21", "9101", median=0.002),
                ],
            }
        ),
        encoding="utf-8",
    )
    topup = tmp_path / "topup.json"
    topup.write_text(
        json.dumps(
            {
                "from": "2026-08-21",
                "to": "2026-08-21",
                "sessions": ["2026-08-21"],
                "tickers": ["9104"],
                "levels": [0.1, 0.5, 0.9],
                "rows": [
                    # The pair that failed the first time.
                    _row("2026-08-21", "9104", median=0.009),
                    # And a re-run of one that already succeeded.
                    _row("2026-08-21", "9101", median=0.007),
                ],
            }
        ),
        encoding="utf-8",
    )

    merged = _combined([main, topup])
    keys = {(r["date"], r["ticker"]) for r in merged["rows"]}
    assert keys == {
        ("2026-08-07", "9101"),
        ("2026-08-21", "9101"),
        ("2026-08-21", "9104"),
    }
    # The later file wins, so a re-run replaces rather than duplicates.
    rerun = next(
        r for r in merged["rows"] if (r["date"], r["ticker"]) == ("2026-08-21", "9101")
    )
    assert rerun["quantiles"]["q0.5"] == pytest.approx(0.007)
    assert merged["sessions"] == ["2026-08-07", "2026-08-21"]
    assert merged["tickers"] == ["9101", "9104"]
    assert len(merged["sources"]) == 2


def test_reading_one_artifact_still_works_unchanged(tmp_path) -> None:
    import json

    from scripts.report_all_method_backtest import _combined

    path = tmp_path / "only.json"
    path.write_text(
        json.dumps(_payload([_row("2026-08-07", "9101")])), encoding="utf-8"
    )
    merged = _combined([path])
    assert len(merged["rows"]) == 1
    assert merged["from"] == "2026-08-07"
