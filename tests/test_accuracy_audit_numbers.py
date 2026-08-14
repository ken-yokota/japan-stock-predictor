"""A report that silently rounds every number to zero is worse than none.

Numeric columns arrive from the database as ``Decimal``, which is neither
``int`` nor ``float``. The first version of this audit narrowed with an
isinstance check that forgot that, and reported every prediction and every
realised return as 0.00% - a result that looked like a catastrophic model
failure and was a type error.
"""

from __future__ import annotations

from decimal import Decimal

from scripts.audit_prediction_accuracy import _num


def test_a_decimal_is_a_number() -> None:
    assert _num(Decimal("0.0065")) == 0.0065
    assert _num(Decimal("-0.0031")) == -0.0031


def test_floats_and_ints_still_work() -> None:
    assert _num(0.42) == 0.42
    assert _num(3) == 3.0


def test_none_stays_none() -> None:
    assert _num(None) is None


def test_a_bool_is_not_a_measurement() -> None:
    assert _num(True) is None


def test_a_numeric_string_is_read() -> None:
    assert _num("1.25") == 1.25


def test_nonsense_is_none_rather_than_zero() -> None:
    """Zero is a value. Unknown must not be reported as one."""

    assert _num("n/a") is None
    assert _num(object()) is None
