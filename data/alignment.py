"""As-of alignment that fails closed when look-ahead is detected."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from data.schemas import MarketBar


class LookaheadError(ValueError):
    """Raised when data newer than a prediction cutoff enters a feature set."""


class ProviderMixingError(ValueError):
    """Raised when one feature series would silently combine providers."""


@dataclass(frozen=True, slots=True)
class AlignedValue:
    """Selected value and its audit lineage."""

    canonical_symbol: str
    value: MarketBar
    cutoff: datetime

    def __post_init__(self) -> None:
        if self.value.available_timestamp > self.cutoff:
            raise LookaheadError(
                f"{self.canonical_symbol} became available after the cutoff"
            )


def latest_available(
    rows: Iterable[MarketBar],
    cutoff: datetime,
    *,
    selected_providers: Mapping[str, str] | None = None,
    require_observed_by_cutoff: bool = False,
) -> dict[str, AlignedValue]:
    """Select each series' latest row known by ``cutoff``.

    Selection is by availability first and event time second. Market dates are
    deliberately not joined because sessions and holidays differ by market.
    """

    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    materialized = list(rows)
    providers_by_symbol: dict[str, set[str]] = {}
    for row in materialized:
        providers_by_symbol.setdefault(row.canonical_symbol, set()).add(row.provider)
    mixed = {
        symbol: providers
        for symbol, providers in providers_by_symbol.items()
        if len(providers) > 1
    }
    if mixed and selected_providers is None:
        names = ", ".join(sorted(mixed))
        raise ProviderMixingError(
            f"provider selection is required for mixed series: {names}"
        )
    if selected_providers is not None:
        missing = sorted(set(mixed) - set(selected_providers))
        if missing:
            raise ProviderMixingError(
                f"provider selection is missing for: {', '.join(missing)}"
            )

    selected: dict[str, MarketBar] = {}
    for row in materialized:
        selected_provider = (
            selected_providers.get(row.canonical_symbol)
            if selected_providers is not None
            else None
        )
        if selected_provider is not None and row.provider != selected_provider:
            continue
        if row.available_timestamp > cutoff:
            continue
        if require_observed_by_cutoff and (
            row.first_observed_at > cutoff or row.retrieved_at > cutoff
        ):
            continue
        current = selected.get(row.canonical_symbol)
        candidate_key = (
            row.timestamp,
            row.available_timestamp,
            row.retrieved_at,
        )
        if current is None:
            selected[row.canonical_symbol] = row
            continue
        current_key = (
            current.timestamp,
            current.available_timestamp,
            current.retrieved_at,
        )
        if candidate_key > current_key:
            selected[row.canonical_symbol] = row
    return {
        symbol: AlignedValue(symbol, row, cutoff) for symbol, row in selected.items()
    }


def assert_no_lookahead(values: Iterable[AlignedValue], cutoff: datetime) -> None:
    """Validate feature lineage immediately before persistence or prediction."""

    violations = [
        item.canonical_symbol
        for item in values
        if item.value.available_timestamp > cutoff
    ]
    if violations:
        names = ", ".join(sorted(set(violations)))
        raise LookaheadError(f"look-ahead detected for: {names}")
