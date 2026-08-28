"""Arms must be scored on the same predictions, and the ordering must be stated.

Two arms scored over different date sets are not comparable, and the difference
is not academic: an arm that failed on the twelve worst sessions and was scored
on the remaining 238 beats one that predicted all 250, for a reason that has
nothing to do with the model. The estimator arms fail independently, so the
overlap is taken rather than assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.comparison import (
    Arm,
    common_keys,
    compare,
    load_arm,
    ranked,
    report,
    restrict,
)
from research.evaluation import Prediction


def _prediction(
    day: str,
    ticker: str,
    *,
    predicted: float = 0.01,
    actual: float = 0.01,
    probability: float = 0.7,
    net: float | None = 1000.0,
) -> Prediction:
    return Prediction(
        date=day,
        ticker=ticker,
        predicted_return=predicted,
        actual_return=actual,
        probability_up=probability,
        signal="BUY" if net is not None else "NO_BUY",
        net_profit_jpy=net,
        gross_profit_jpy=net,
    )


def _arm(label: str, rows: list[Prediction]) -> Arm:
    return Arm(label=label, path=Path(label), predictions=rows)


DAYS = [f"2026-03-{day:02d}" for day in range(1, 21)]


def _full(label: str, *, actual: float = 0.01, net: float = 1000.0) -> Arm:
    return _arm(
        label,
        [
            _prediction(day, ticker, actual=actual, net=net)
            for day in DAYS
            for ticker in ("A", "B", "C")
        ],
    )


# --------------------------------------------------------------------------
# The common set


def test_only_the_pairs_every_arm_produced_are_scored() -> None:
    wide = _full("wide")
    narrow = _arm(
        "narrow",
        [row for row in wide.predictions if row.date != DAYS[0]],
    )

    keys = common_keys([wide, narrow])

    assert len(keys) == len(narrow.predictions)
    assert all(date != DAYS[0] for date, _ in keys)


def test_an_arm_that_skipped_the_worst_sessions_cannot_win_on_that() -> None:
    """The failure mode the restriction exists for, made explicit."""

    losers = {DAYS[0], DAYS[1]}
    complete = _arm(
        "complete",
        [
            _prediction(
                day,
                ticker,
                actual=(-0.05 if day in losers else 0.01),
                net=(-5000.0 if day in losers else 1000.0),
            )
            for day in DAYS
            for ticker in ("A", "B", "C")
        ],
    )
    skipped = _arm(
        "skipped",
        [row for row in complete.predictions if row.date not in losers],
    )

    result = compare([complete, skipped])
    by_label = {item.label: item for item in result.evaluations}

    assert result.common_pairs == len(skipped.predictions)
    assert by_label["complete"].trading.net_jpy == pytest.approx(
        by_label["skipped"].trading.net_jpy
    )


def test_how_many_predictions_each_arm_lost_to_the_restriction_is_reported() -> None:
    wide = _full("wide")
    narrow = _arm("narrow", [row for row in wide.predictions if row.date != DAYS[0]])

    result = compare([wide, narrow])

    assert result.dropped["wide"] == 3
    assert result.dropped["narrow"] == 0
    assert "共通集合外として除外" in "\n".join(report(result))


def test_no_overlap_at_all_is_stated_rather_than_scored_as_an_empty_tie() -> None:
    first = _arm("first", [_prediction(DAYS[0], "A")])
    second = _arm("second", [_prediction(DAYS[1], "A")])

    result = compare([first, second])

    assert result.common_pairs == 0
    assert result.underpowered
    assert "比較ではなく別の実験" in "\n".join(report(result))


def test_restrict_keeps_the_rows_and_drops_nothing_else() -> None:
    arm = _full("arm")
    keys = {(DAYS[0], "A"), (DAYS[1], "B")}

    kept = restrict(arm, keys)

    assert {(row.date, row.ticker) for row in kept} == keys


# --------------------------------------------------------------------------
# The ordering


def test_the_ranking_follows_expectancy_before_anything_else() -> None:
    """MAE last is deliberate: predicting a flat zero has the lowest MAE here."""

    profitable = _full("profitable", actual=0.02, net=2000.0)
    accurate = _arm(
        "accurate",
        [
            _prediction(day, ticker, predicted=0.0001, actual=0.0001, net=-500.0)
            for day in DAYS
            for ticker in ("A", "B", "C")
        ],
    )

    order = [item.label for item in ranked(compare([accurate, profitable]))]

    assert order[0] == "profitable"
    assert accurate.predictions[0].predicted_return != pytest.approx(0.02)


def test_an_arm_that_never_traded_does_not_rank_as_break_even() -> None:
    """No expectancy is not zero expectancy; zero would beat every real loss."""

    idle = _arm(
        "idle",
        [
            _prediction(day, ticker, net=None)
            for day in DAYS
            for ticker in ("A", "B", "C")
        ],
    )
    losing = _full("losing", actual=-0.01, net=-1000.0)

    order = [item.label for item in ranked(compare([idle, losing]))]

    assert order[-1] == "idle"


# --------------------------------------------------------------------------
# Loading


def test_an_artifact_is_labelled_by_what_varied_in_it(tmp_path: Path) -> None:
    path = tmp_path / "arm.json"
    path.write_text(
        json.dumps(
            {
                "feature_set": "production",
                "estimator": "quantile",
                "predictions": [
                    {
                        "date": DAYS[0],
                        "ticker": "A",
                        "predicted_return": 0.01,
                        "actual_return": 0.02,
                        "probability_up": 0.7,
                        "signal": "BUY",
                        "net_profit_jpy": 100.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    arm = load_arm(path)

    assert arm.label == "production / quantile"
    assert arm.estimator == "quantile"
    assert len(arm.predictions) == 1


def test_a_row_with_no_settled_actual_is_not_loaded_as_a_zero(
    tmp_path: Path,
) -> None:
    path = tmp_path / "arm.json"
    path.write_text(
        json.dumps(
            {
                "feature_set": "production",
                "predictions": [
                    {
                        "date": DAYS[0],
                        "ticker": "A",
                        "predicted_return": 0.01,
                        "actual_return": None,
                        "probability_up": 0.7,
                        "signal": "BUY",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_arm(path).predictions == []
