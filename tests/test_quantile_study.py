"""A distribution has to be checked against outcomes, not admired for existing.

The quantile arm's appeal is that it states its own uncertainty. That is only
worth anything if the statement is true, so the interval is checked against how
often it actually contained the outcome, and the buy conditions derived from it
are scored against the control they have to beat.
"""

from __future__ import annotations

import pytest

from research.quantile_study import (
    buy_rules,
    coverage,
    probability_sources,
    report,
)
from research.universe import round_trip_cost


def _row(
    day: str,
    ticker: str,
    *,
    predicted: float,
    actual: float,
    quantiles: dict[str, float] | None = None,
    probability: float = 0.6,
) -> dict[str, object]:
    return {
        "date": day,
        "ticker": ticker,
        "predicted_return": predicted,
        "actual_return": actual,
        "probability_up": probability,
        "quantiles": quantiles,
    }


def _band(centre: float, half: float) -> dict[str, float]:
    return {
        "q0.1": centre - half,
        "q0.25": centre - half / 2,
        "q0.5": centre,
        "q0.75": centre + half / 2,
        "q0.9": centre + half,
    }


# --------------------------------------------------------------------------
# Coverage


def test_an_interval_that_always_contains_the_outcome_reports_full_coverage() -> None:
    rows = [
        _row(
            f"2026-01-{i:02d}",
            "A",
            predicted=0.0,
            actual=0.001,
            quantiles=_band(0.0, 0.05),
        )
        for i in range(1, 21)
    ]

    wide, narrow = coverage(rows)

    assert wide.observed == pytest.approx(1.0)
    assert narrow.observed == pytest.approx(1.0)
    assert wide.shortfall == pytest.approx(0.20)


def test_an_interval_that_never_contains_the_outcome_is_not_rounded_up() -> None:
    """The failure worth catching: a model wrong about its own uncertainty."""

    rows = [
        _row(
            f"2026-01-{i:02d}",
            "A",
            predicted=0.0,
            actual=0.9,
            quantiles=_band(0.0, 0.01),
        )
        for i in range(1, 21)
    ]

    wide, _ = coverage(rows)

    assert wide.observed == pytest.approx(0.0)
    assert wide.shortfall == pytest.approx(-0.80)
    assert wide.count == 20


def test_the_reported_width_is_the_interval_not_the_forecast() -> None:
    rows = [
        _row("2026-01-01", "A", predicted=0.0, actual=0.0, quantiles=_band(0.0, 0.02))
    ]

    wide, narrow = coverage(rows)

    assert wide.mean_width == pytest.approx(0.04)
    assert narrow.mean_width == pytest.approx(0.02)


def test_rows_without_quantiles_are_skipped_rather_than_counted_as_misses() -> None:
    """A linear arm has no quantiles; counting it as a miss would invent a result."""

    rows = [
        _row("2026-01-01", "A", predicted=0.0, actual=0.0, quantiles=_band(0.0, 0.05)),
        _row("2026-01-02", "A", predicted=0.0, actual=0.0, quantiles=None),
    ]

    wide, _ = coverage(rows)

    assert wide.count == 1
    assert wide.observed == pytest.approx(1.0)


# --------------------------------------------------------------------------
# The buy conditions


def test_every_rule_pays_the_round_trip_cost() -> None:
    rows = [
        _row(
            f"2026-01-{i:02d}",
            "A",
            predicted=0.01,
            actual=0.01,
            quantiles=_band(0.01, 0.005),
        )
        for i in range(1, 11)
    ]

    control = next(r for r in buy_rules(rows) if r.name.startswith("対照"))

    assert control.mean_net == pytest.approx(0.01 - round_trip_cost())


def test_the_control_comes_first_so_a_rule_has_something_to_beat() -> None:
    rows = [
        _row(
            "2026-01-01",
            "A",
            predicted=0.01,
            actual=0.01,
            quantiles=_band(0.01, 0.005),
        )
    ]

    assert buy_rules(rows)[0].name.startswith("対照")


def test_a_stricter_quantile_condition_takes_fewer_positions() -> None:
    """q25>0 must be a subset of "the median is positive", or it is mislabelled."""

    rows = [
        _row(
            f"2026-01-{i:02d}",
            "A",
            # A positive median with a 25th percentile below zero: the rule the
            # classifier would wave through and the distribution would not.
            predicted=0.004,
            actual=0.001,
            quantiles={"q0.1": -0.03, "q0.25": -0.01, "q0.5": 0.004,
                       "q0.75": 0.02, "q0.9": 0.04},
        )
        for i in range(1, 11)
    ]

    by_name = {r.name: r for r in buy_rules(rows)}

    assert by_name["中央値>0.3%"].positions == 10
    assert by_name["q25>0（下側25%が正）"].positions == 0


def test_a_rule_that_never_fires_reports_zero_rather_than_a_blank_edge() -> None:
    rows = [
        _row(
            "2026-01-01",
            "A",
            predicted=-0.01,
            actual=-0.01,
            quantiles=_band(-0.01, 0.005),
        )
    ]

    by_name = {r.name: r for r in buy_rules(rows)}

    assert by_name["q25>0（下側25%が正）"].positions == 0
    assert by_name["q25>0（下側25%が正）"].mean_net == 0.0


# --------------------------------------------------------------------------
# Where P(up) comes from


def test_the_two_probability_sources_are_scored_on_the_same_pairs() -> None:
    quantile_rows = [
        _row(f"2026-02-{i:02d}", "A", predicted=0.01, actual=0.01, probability=0.8)
        for i in range(1, 11)
    ]
    logistic_rows = [
        _row(f"2026-02-{i:02d}", "A", predicted=0.01, actual=0.01, probability=0.2)
        for i in range(1, 9)
    ]

    sources = probability_sources(quantile_rows, logistic_rows)

    assert {s.count for s in sources} == {8}
    # The one that said 0.8 when the outcome rose is the better-scored one.
    by_name = {s.name: s for s in sources}
    assert by_name["分位点由来 P(up)"].brier < by_name["ロジスティック P(up)"].brier


def test_no_shared_pairs_returns_nothing_rather_than_a_one_sided_table() -> None:
    left = [_row("2026-02-01", "A", predicted=0.01, actual=0.01)]
    right = [_row("2026-03-01", "A", predicted=0.01, actual=0.01)]

    assert probability_sources(left, right) == []


def test_the_report_states_the_cost_it_charged() -> None:
    rows = [
        _row(
            "2026-01-01",
            "A",
            predicted=0.01,
            actual=0.01,
            quantiles=_band(0.01, 0.01),
        )
    ]

    text = "\n".join(report(rows))

    assert f"{round_trip_cost() * 100:.3f}%" in text
    assert "被覆" in text
