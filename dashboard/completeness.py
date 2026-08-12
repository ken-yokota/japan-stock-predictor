"""One definition of "did this morning have the data it needed".

Three surfaces answer that question - the Today page, the morning email and
the audit command - and they must not answer it differently. A dashboard that
says CLEAN while the mail says DEGRADED is worse than either being wrong on
its own, because there is then no way to know which to believe.

Two coverage numbers exist and they measure different things:

``feature_coverage``
    Of the features that were actually built, how many carried a value. Its
    denominator comes from the data, so an indicator that produced nothing at
    all never enters it and cannot lower it. This is why three production days
    reported 1.000 while five required series were failing.

``indicator_coverage``
    Of the indicators the configuration says this ticker needs, how many
    arrived. The denominator is fixed before any fetch happens, so a series
    that produced nothing is still counted as owed.

The third state matters as much as the other two: a feature set written before
completeness was recorded carries no misses, and reading that absence as
"complete" manufactures the reassurance this module exists to withdraw.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

# Per-stock completeness.
CLEAN = "CLEAN"
DEGRADED = "DEGRADED"
LEGACY_UNKNOWN = "LEGACY_UNKNOWN"

# The day as a whole.
NORMAL = "NORMAL"
WARNING = "WARNING"
UNKNOWN = "UNKNOWN"

# How a BUY reads once its inputs are taken into account.
CLEAN_BUY = "CLEAN_BUY"
DEGRADED_BUY = "DEGRADED_BUY"
NON_BUY = "NON_BUY"

# The series under active investigation. Kept here so the dashboard, the mail
# and the audit all watch the same list, and so changing it is one edit.
WATCHED_INDICATORS: tuple[str, ...] = ("usdjpy", "eurjpy", "audjpy", "oih", "kre")

# Coverage is stored as a float; treat anything at or above this as complete
# rather than comparing to 1.0 exactly.
_COMPLETE = 0.9999


def _as_list(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


@dataclass(frozen=True, slots=True)
class StockCompleteness:
    """What one ticker was owed on one morning, and what arrived."""

    ticker: str
    status: str
    indicator_coverage: float | None = None
    feature_coverage: float | None = None
    missing_required: tuple[str, ...] = ()
    missing_optional: tuple[str, ...] = ()
    signal: str = ""

    @property
    def is_buy(self) -> bool:
        return self.signal.upper() == "BUY"

    @property
    def buy_class(self) -> str:
        if not self.is_buy:
            return NON_BUY
        return DEGRADED_BUY if self.status == DEGRADED else CLEAN_BUY

    @property
    def hidden_by_feature_coverage(self) -> bool:
        """Feature coverage says complete while indicator coverage does not.

        This is the exact shape of the failure that went unnoticed, so it is
        surfaced rather than left to be inferred from two numbers side by side.
        """

        return (
            self.feature_coverage is not None
            and self.feature_coverage >= _COMPLETE
            and self.indicator_coverage is not None
            and self.indicator_coverage < _COMPLETE
        )

    @property
    def label(self) -> str:
        """Short text state. Never colour alone - this is what gets printed."""

        if self.status == DEGRADED:
            return "⚠ DEGRADED"
        if self.status == LEGACY_UNKNOWN:
            return "UNKNOWN"
        return "CLEAN"


def classify_details(details: object, *, signal: str = "", **extra: Any) -> str:
    """CLEAN only when the run recorded that it had everything.

    An absent ``missing_required_indicators`` key means completeness was never
    written, which is not the same as nothing being missing.
    """

    del signal, extra
    if not isinstance(details, dict) or "missing_required_indicators" not in details:
        return LEGACY_UNKNOWN
    return DEGRADED if _as_list(details.get("missing_required_indicators")) else CLEAN


def stock_from_details(
    ticker: str,
    details: object,
    *,
    feature_coverage: object = None,
    signal: object = "",
) -> StockCompleteness:
    """Build one ticker's completeness from its stored feature-set details."""

    status = classify_details(details)
    blob = details if isinstance(details, dict) else {}
    return StockCompleteness(
        ticker=str(ticker),
        status=status,
        indicator_coverage=_as_float(blob.get("indicator_coverage")),
        feature_coverage=_as_float(feature_coverage),
        missing_required=_as_list(blob.get("missing_required_indicators")),
        missing_optional=_as_list(blob.get("missing_optional_indicators")),
        signal=str(signal or ""),
    )


@dataclass(frozen=True, slots=True)
class MorningCompletenessSummary:
    """The day in the numbers every surface has to agree on."""

    stocks: tuple[StockCompleteness, ...] = ()
    missing_required_ranking: tuple[tuple[str, int], ...] = ()
    missing_optional_ranking: tuple[tuple[str, int], ...] = ()
    _counts: dict[str, int] = field(default_factory=dict)

    @property
    def stock_count(self) -> int:
        return len(self.stocks)

    @property
    def clean_count(self) -> int:
        return sum(1 for item in self.stocks if item.status == CLEAN)

    @property
    def degraded_count(self) -> int:
        return sum(1 for item in self.stocks if item.status == DEGRADED)

    @property
    def unknown_count(self) -> int:
        return sum(1 for item in self.stocks if item.status == LEGACY_UNKNOWN)

    @property
    def buy_count(self) -> int:
        return sum(1 for item in self.stocks if item.is_buy)

    @property
    def clean_buy_count(self) -> int:
        return sum(1 for item in self.stocks if item.buy_class == CLEAN_BUY)

    @property
    def degraded_buy_count(self) -> int:
        return sum(1 for item in self.stocks if item.buy_class == DEGRADED_BUY)

    @property
    def degraded_buys(self) -> tuple[StockCompleteness, ...]:
        return tuple(item for item in self.stocks if item.buy_class == DEGRADED_BUY)

    @property
    def hidden_by_feature_coverage(self) -> tuple[str, ...]:
        return tuple(
            item.ticker for item in self.stocks if item.hidden_by_feature_coverage
        )

    @property
    def data_status(self) -> str:
        """WARNING wins over UNKNOWN: a known miss outranks an unknown day."""

        if not self.stocks:
            return UNKNOWN
        if self.degraded_count:
            return WARNING
        if self.clean_count == 0 and self.unknown_count:
            return UNKNOWN
        if self.unknown_count:
            return UNKNOWN
        return NORMAL

    def watched(
        self, indicators: tuple[str, ...] = WATCHED_INDICATORS
    ) -> tuple[tuple[str, int], ...]:
        """Affected-stock count for each watched series, in the given order."""

        tally = dict(self.missing_required_ranking)
        return tuple((name, tally.get(name, 0)) for name in indicators)


def summarise(stocks: list[StockCompleteness]) -> MorningCompletenessSummary:
    """Aggregate per-stock completeness into the one shared view."""

    required: Counter[str] = Counter()
    optional: Counter[str] = Counter()
    for item in stocks:
        required.update(item.missing_required)
        optional.update(item.missing_optional)
    return MorningCompletenessSummary(
        stocks=tuple(stocks),
        missing_required_ranking=tuple(required.most_common()),
        missing_optional_ranking=tuple(optional.most_common()),
    )
