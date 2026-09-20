"""Explicit, versioned ticker assignments; candidates never enter production."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TickerFeatureSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    selected: list[str]
    candidate: list[str] = Field(default_factory=list)
    excluded: list[str] = Field(default_factory=list)
    # None preserves the champion's existing transformations. A promoted compact
    # set must name columns explicitly; no Cartesian window expansion is implied.
    selected_columns: list[str] | None = None
    reason: str
    oos_improvement: float | None = None

    @model_validator(mode="after")
    def validate_sets(self) -> TickerFeatureSet:
        for names in (self.selected, self.candidate, self.excluded):
            if len(names) != len(set(names)):
                raise ValueError("duplicate registry indicator")
        if set(self.selected) & set(self.excluded):
            raise ValueError("selected indicator cannot be excluded")
        if self.selected_columns is not None:
            if not self.selected_columns or len(self.selected_columns) != len(
                set(self.selected_columns)
            ):
                raise ValueError("selected columns must be nonempty and unique")
            for column in self.selected_columns:
                prefix, separator, _ = column.partition("__")
                if not separator or prefix not in {"stock", *self.selected}:
                    raise ValueError("column must belong to this ticker's selection")
        return self


class FeatureRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1]
    feature_version: str
    registered_on: date
    minimum_forward_sessions: int = Field(default=20, ge=20)
    review_policy: Literal["monthly_or_20_new_sessions"]
    status: Literal["CHAMPION_PARITY", "OOS_APPROVED"]
    tickers: dict[str, TickerFeatureSet]

    def assert_review_allowed(
        self, new_sessions: int, *, provider_emergency: bool = False
    ) -> None:
        if not provider_emergency and new_sessions < self.minimum_forward_sessions:
            raise ValueError("feature set is frozen until 20 new trading sessions")


def resolve_indicator_ids(config: object, ticker: str) -> tuple[str, ...]:
    """The single resolver for production and historical datasets.

    The fallback exists for archived five-file configs and old test fixtures;
    the shipped config always contains the explicit registry. A registry entry
    replaces all common/sector assignments, never appends to them.
    """
    from data.config import AppConfig

    if not isinstance(config, AppConfig):
        raise TypeError("expected AppConfig")
    stock = next((s for s in config.stocks.stocks if s.ticker == ticker), None)
    if stock is None:
        raise ValueError(f"unknown configured ticker: {ticker}")
    catalog = {item.id: item for item in config.indicators.indicators}
    if config.ticker_features is not None:
        requested = config.ticker_features.tickers[ticker].selected
    else:
        requested = [
            *config.indicators.common,
            *config.indicators.sectors[stock.sector].indicators,
            *(
                item.id
                for item in catalog.values()
                if ticker in item.applies_to_tickers
            ),
        ]
    return tuple(
        dict.fromkeys(
            name
            for name in requested
            if catalog[name].enabled and catalog[name].resolution_status == "resolved"
        )
    )
