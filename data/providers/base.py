"""Abstract provider boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from data.schemas import FetchRequest, MarketBar, ProviderHealth, SnapshotRequest


class ProviderError(RuntimeError):
    """Base class for sanitized provider failures."""


class ProviderFetchError(ProviderError):
    """Network, authentication, rate-limit, or upstream failure."""


class ProviderResponseError(ProviderError):
    """Malformed or semantically invalid upstream response."""


class ProviderEntitlementError(ProviderError):
    """The configured account/plan does not entitle the requested operation."""


@dataclass(frozen=True, slots=True)
class SymbolResolution:
    """A mapping proven by a provider's symbol-list response."""

    canonical_symbol: str
    provider_symbol: str
    exchange_code: str
    exchange_mic: str | None
    name: str
    currency: str | None
    verified_at: datetime


class MarketDataProvider(ABC):
    """Interface implemented by all current and future market-data providers."""

    name: str

    @abstractmethod
    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        """Fetch and normalize daily history for one verified symbol."""

    @abstractmethod
    def healthcheck(self) -> ProviderHealth:
        """Perform a minimal authenticated connectivity check."""

    @abstractmethod
    def close(self) -> None:
        """Release owned resources."""

    def __enter__(self) -> MarketDataProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@runtime_checkable
class SnapshotMarketDataProvider(Protocol):
    """Optional capability for delayed or real-time point observations."""

    name: str

    def fetch_snapshot(self, request: SnapshotRequest) -> MarketBar:
        """Fetch the newest provider observation without backdating availability."""


@runtime_checkable
class SymbolCatalogProvider(Protocol):
    """Optional capability for provider-owned symbol resolution."""

    name: str

    def resolve_symbol(
        self,
        *,
        canonical_symbol: str,
        country_iso: str,
        exchange_mic: str,
    ) -> SymbolResolution | None:
        """Resolve from upstream metadata rather than suffix inference."""
