"""Deterministic provider routing without cross-provider series patching."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from data.providers.base import (
    MarketDataProvider,
    ProviderError,
    SnapshotMarketDataProvider,
)
from data.schemas import DataQuality, FetchRequest, MarketBar, SnapshotRequest
from data.snapshot import (
    FreshnessAssessment,
    FreshnessStatus,
    SelectionRole,
    assess_snapshot,
)


@dataclass(frozen=True, slots=True)
class EodRouteCandidate:
    registry_key: str
    request: FetchRequest


@dataclass(frozen=True, slots=True)
class SnapshotRouteCandidate:
    registry_key: str
    request: SnapshotRequest


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    registry_key: str
    provider: str | None
    accepted: bool
    reason: str
    data_quality: DataQuality = DataQuality.MISSING
    freshness_status: FreshnessStatus = FreshnessStatus.MISSING
    coverage: float | None = None


@dataclass(frozen=True, slots=True)
class SeriesSelection:
    rows: tuple[MarketBar, ...]
    selected_registry_key: str | None
    selected_provider: str | None
    selection_role: SelectionRole
    attempts: tuple[ProviderAttempt, ...]


@dataclass(frozen=True, slots=True)
class SnapshotSelection:
    row: MarketBar | None
    selected_registry_key: str | None
    selected_provider: str | None
    selection_role: SelectionRole
    assessment: FreshnessAssessment
    attempts: tuple[ProviderAttempt, ...]


class ProviderRouter:
    """Choose one complete provider series in declared priority order."""

    def __init__(self, providers: Mapping[str, MarketDataProvider]) -> None:
        if not providers:
            raise ValueError("provider registry must not be empty")
        self._providers = dict(providers)

    def _provider(self, registry_key: str) -> MarketDataProvider:
        try:
            return self._providers[registry_key]
        except KeyError as exc:
            raise ValueError(f"unknown provider registry key: {registry_key}") from exc

    @staticmethod
    def _role(index: int) -> SelectionRole:
        return SelectionRole.PRIMARY if index == 0 else SelectionRole.FALLBACK

    def fetch_eod_series(
        self,
        candidates: Sequence[EodRouteCandidate],
        *,
        required_dates: Iterable[date],
        cutoff_at: datetime,
        minimum_coverage: float = 1.0,
        operational_run: bool = False,
        acceptable_qualities: frozenset[DataQuality] = frozenset(
            {
                DataQuality.OFFICIAL,
                DataQuality.EOD_CONFIRMED,
                DataQuality.FREE_UNVERIFIED,
            }
        ),
    ) -> SeriesSelection:
        if not 0 < minimum_coverage <= 1:
            raise ValueError("minimum_coverage must be in (0, 1]")
        required = frozenset(required_dates)
        if not required:
            raise ValueError("required_dates must not be empty")
        attempts: list[ProviderAttempt] = []
        for index, candidate in enumerate(candidates):
            provider = self._provider(candidate.registry_key)
            try:
                rows = provider.fetch_eod(candidate.request)
            except ProviderError as exc:
                attempts.append(
                    ProviderAttempt(
                        candidate.registry_key,
                        provider.name,
                        False,
                        str(exc),
                    )
                )
                continue
            if not rows:
                attempts.append(
                    ProviderAttempt(
                        candidate.registry_key,
                        provider.name,
                        False,
                        "provider returned an empty series",
                    )
                )
                continue
            if any(row.provider != provider.name for row in rows):
                raise ValueError("provider returned rows with mixed provenance")
            if any(
                row.canonical_symbol != candidate.request.canonical_symbol
                for row in rows
            ):
                raise ValueError("provider returned a different canonical series")
            rejected_quality = next(
                (
                    row.data_quality
                    for row in rows
                    if row.data_quality not in acceptable_qualities
                ),
                None,
            )
            if rejected_quality is not None:
                attempts.append(
                    ProviderAttempt(
                        candidate.registry_key,
                        provider.name,
                        False,
                        f"quality {rejected_quality.value} is not accepted",
                        rejected_quality,
                        FreshnessStatus.QUALITY_REJECTED,
                    )
                )
                continue
            visible = [
                row
                for row in rows
                if row.available_timestamp <= cutoff_at
                and (
                    not operational_run
                    or (
                        row.first_observed_at <= cutoff_at
                        and row.retrieved_at <= cutoff_at
                    )
                )
            ]
            present = {row.market_date for row in visible} & required
            coverage = len(present) / len(required)
            quality = visible[-1].data_quality if visible else rows[-1].data_quality
            if coverage < minimum_coverage:
                attempts.append(
                    ProviderAttempt(
                        candidate.registry_key,
                        provider.name,
                        False,
                        "provider does not cover the complete required window",
                        quality,
                        FreshnessStatus.STALE,
                        coverage,
                    )
                )
                continue
            selected = tuple(row for row in visible if row.market_date in required)
            attempts.append(
                ProviderAttempt(
                    candidate.registry_key,
                    provider.name,
                    True,
                    "complete provider series selected",
                    quality,
                    FreshnessStatus.FRESH,
                    coverage,
                )
            )
            return SeriesSelection(
                selected,
                candidate.registry_key,
                provider.name,
                self._role(index),
                tuple(attempts),
            )
        return SeriesSelection(
            (), None, None, SelectionRole.NONE, tuple(attempts)
        )

    def fetch_snapshot(
        self,
        candidates: Sequence[SnapshotRouteCandidate],
        *,
        cutoff_at: datetime,
        max_age: timedelta,
        acceptable_qualities: frozenset[DataQuality] = frozenset(
            {DataQuality.DELAYED, DataQuality.FREE_UNVERIFIED, DataQuality.OFFICIAL}
        ),
    ) -> SnapshotSelection:
        attempts: list[ProviderAttempt] = []
        last_assessment = assess_snapshot(
            None,
            cutoff_at=cutoff_at,
            max_age=max_age,
            acceptable_qualities=acceptable_qualities,
        )
        for index, candidate in enumerate(candidates):
            provider = self._provider(candidate.registry_key)
            if not isinstance(provider, SnapshotMarketDataProvider):
                attempts.append(
                    ProviderAttempt(
                        candidate.registry_key,
                        provider.name,
                        False,
                        "provider has no snapshot capability",
                    )
                )
                continue
            try:
                row = provider.fetch_snapshot(candidate.request)
            except ProviderError as exc:
                attempts.append(
                    ProviderAttempt(
                        candidate.registry_key,
                        provider.name,
                        False,
                        str(exc),
                    )
                )
                continue
            last_assessment = assess_snapshot(
                row,
                cutoff_at=cutoff_at,
                max_age=max_age,
                acceptable_qualities=acceptable_qualities,
            )
            attempts.append(
                ProviderAttempt(
                    candidate.registry_key,
                    row.provider,
                    last_assessment.usable,
                    last_assessment.reason,
                    row.data_quality,
                    last_assessment.status,
                )
            )
            if last_assessment.usable:
                return SnapshotSelection(
                    row,
                    candidate.registry_key,
                    row.provider,
                    self._role(index),
                    last_assessment,
                    tuple(attempts),
                )
        return SnapshotSelection(
            None,
            None,
            None,
            SelectionRole.NONE,
            last_assessment,
            tuple(attempts),
        )
