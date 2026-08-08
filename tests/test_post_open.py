from __future__ import annotations

from decimal import Decimal

import pytest

from dashboard.presenters import today_table_rows
from trading.post_open import project_predicted_close


def test_predicted_close_is_derived_from_the_observed_open() -> None:
    projection = project_predicted_close(1_000.0, 0.01)

    assert projection is not None
    assert projection.predicted_close == pytest.approx(1_010.0)
    assert projection.predicted_price_difference == pytest.approx(10.0)
    assert projection.reference_basis == "actual_open"
    assert projection.implied_return == pytest.approx(0.01)


def test_negative_predicted_return_lowers_the_projected_close() -> None:
    projection = project_predicted_close(Decimal("2000"), Decimal("-0.005"))

    assert projection is not None
    assert projection.predicted_close == pytest.approx(1_990.0)


@pytest.mark.parametrize(
    ("open_price", "predicted_return"),
    [
        (None, 0.01),
        (1_000.0, None),
        (0.0, 0.01),
        (-100.0, 0.01),
        ("not-a-price", 0.01),
        (float("nan"), 0.01),
        (1_000.0, float("inf")),
    ],
)
def test_unknowable_inputs_return_none_instead_of_a_guess(
    open_price: object, predicted_return: object
) -> None:
    assert project_predicted_close(open_price, predicted_return) is None


def test_today_table_shows_pending_before_the_open_is_observed() -> None:
    rows = today_table_rows(
        [
            {
                "ticker": "9101",
                "status": "SUCCESS",
                "predicted_intraday_return": 0.01,
                "probability_up": 0.7,
                "signal": "BUY",
                "actual_open": None,
            }
        ],
        [],
    )

    assert rows[0]["予測終値(Open基準)"] == "PENDING"
    assert rows[0]["実績Open"] == "—"


def test_today_table_shows_the_open_based_close_once_available() -> None:
    rows = today_table_rows(
        [
            {
                "ticker": "9101",
                "status": "SUCCESS",
                "predicted_intraday_return": 0.01,
                "probability_up": 0.7,
                "signal": "BUY",
                "actual_open": 1_000.0,
            }
        ],
        [],
    )

    assert rows[0]["予測終値(Open基準)"] == "1,010.0"
    assert rows[0]["実績Open"] == "1,000.00"
