"""Post-open predicted close derivation.

Before 09:00 the day's Open is unknown, so the morning publication carries a
previous-close reference price (``config/trading.yaml``
``prediction_price.morning_reference``). Once the real Open is observed, the
specification's predicted close becomes:

    predicted_close = actual_open * (1 + predicted_intraday_return)

This module derives that value without touching the morning record. The
08:30 publication is the point-in-time evidence that the prediction was made
before the market opened; overwriting its reference price would destroy that
evidence and make the morning record unauditable. Callers therefore display the
derived value alongside -- not instead of -- the morning figure.

A derivation is returned only when a usable Open exists. There is no fallback
value, because a fabricated predicted close would be indistinguishable from a
real one in the dashboard and the email.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class PostOpenProjection:
    """Predicted close and difference measured from the observed Open."""

    actual_open: float
    predicted_return: float
    predicted_close: float
    predicted_price_difference: float
    reference_basis: str = "actual_open"

    @property
    def implied_return(self) -> float:
        """Return the predicted move as a fraction of the observed Open."""

        return self.predicted_price_difference / self.actual_open


def _finite(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(Decimal(str(value)))
    except (ArithmeticError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def project_predicted_close(
    actual_open: object,
    predicted_return: object,
) -> PostOpenProjection | None:
    """Return the Open-based predicted close, or ``None`` when it is unknowable.

    ``None`` means at least one input is missing, non-numeric, or a
    non-positive Open. Callers must render ``PENDING`` in that case rather than
    substituting the morning previous-close projection.
    """

    open_price = _finite(actual_open)
    expected_return = _finite(predicted_return)
    if open_price is None or expected_return is None or open_price <= 0.0:
        return None
    difference = open_price * expected_return
    return PostOpenProjection(
        actual_open=open_price,
        predicted_return=expected_return,
        predicted_close=open_price + difference,
        predicted_price_difference=difference,
    )
