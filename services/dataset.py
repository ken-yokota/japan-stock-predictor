"""Build auditable ticker datasets from point-in-time raw observations."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TypeAlias

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from data.availability import prediction_cutoff
from data.config import AppConfig
from data.market_calendar import japan_sessions_before, japan_sessions_between
from database.models import MarketData, StockPrice

PriceRow: TypeAlias = MarketData | StockPrice  # noqa: UP040

_PROVIDER_PRIORITY = ("yahoo_finance", "us_treasury", "internal", "eodhd")
_PRICE_FEATURE_NAMES = (
    "return_1d",
    "return_2d",
    "return_3d",
    "return_5d",
    "return_20d",
    "log_return_1d",
    "volatility_5d",
    "volatility_20d",
    "open_close_return",
    "high_low_range",
    "ma20_deviation",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Exact raw revision contributing to a persisted feature value."""

    table_name: str
    row_id: int
    canonical_symbol: str
    market_date: date
    available_at: datetime
    first_observed_at: datetime
    retrieved_at: datetime
    raw_hash: str
    data_quality: str

    def assert_visible(self, cutoff_at: datetime, *, operational: bool) -> None:
        if _utc(self.available_at) > _utc(cutoff_at):
            raise ValueError("feature source became available after cutoff")
        if operational and (
            _utc(self.first_observed_at) > _utc(cutoff_at)
            or _utc(self.retrieved_at) > _utc(cutoff_at)
        ):
            raise ValueError("operational feature source was observed after cutoff")


@dataclass(frozen=True, slots=True)
class ModelSample:
    """One ticker/session feature row and optional realized target."""

    ticker: str
    sample_date: date
    cutoff_at: datetime
    values: dict[str, float]
    lineage: dict[str, tuple[SourceReference, ...]]
    target_return: float | None = None
    target_difference: float | None = None
    target_open: float | None = None
    target_close: float | None = None
    target_lineage: tuple[SourceReference, ...] = field(default_factory=tuple)
    reference_price: float | None = None
    reference_source: SourceReference | None = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def assert_safe(self, *, operational: bool) -> None:
        missing_lineage = set(self.values) - set(self.lineage)
        if missing_lineage:
            raise ValueError(f"feature lineage missing: {sorted(missing_lineage)}")
        for references in self.lineage.values():
            if not references:
                raise ValueError("feature lineage must not be empty")
            for reference in references:
                reference.assert_visible(self.cutoff_at, operational=operational)
        if self.reference_source is not None:
            self.reference_source.assert_visible(
                self.cutoff_at, operational=operational
            )


@dataclass(frozen=True, slots=True)
class ModelDataset:
    """Training rows plus the one current row using an identical feature order."""

    ticker: str
    feature_names: tuple[str, ...]
    training_frame: pd.DataFrame
    training_target: pd.Series
    current_frame: pd.DataFrame
    training_samples: tuple[ModelSample, ...]
    current_sample: ModelSample
    candidate_feature_count: int
    feature_coverage: float


@dataclass(frozen=True, slots=True)
class BacktestDataset:
    """Historical estimated-PIT rows and a feature schema frozen pre-OOS."""

    ticker: str
    frame: pd.DataFrame
    feature_names: tuple[str, ...]
    availability_evidence: str = "ESTIMATED_BACKFILL"


def _reference(row: PriceRow) -> SourceReference:
    return SourceReference(
        table_name=row.__tablename__,
        row_id=row.id,
        canonical_symbol=row.canonical_symbol,
        market_date=row.market_date,
        available_at=_utc(row.available_timestamp),
        first_observed_at=_utc(row.first_observed_at),
        retrieved_at=_utc(row.retrieved_at),
        raw_hash=row.raw_hash,
        data_quality=row.data_quality,
    )


def _latest_revisions(rows: Iterable[PriceRow]) -> list[PriceRow]:
    selected: dict[tuple[str, str, str, date], PriceRow] = {}
    for row in rows:
        key = (row.canonical_symbol, row.provider, row.interval, row.market_date)
        previous = selected.get(key)
        row_key = (
            _utc(row.available_timestamp),
            _utc(row.first_observed_at),
            _utc(row.retrieved_at),
            row.id,
        )
        if previous is None:
            selected[key] = row
            continue
        previous_key = (
            _utc(previous.available_timestamp),
            _utc(previous.first_observed_at),
            _utc(previous.retrieved_at),
            previous.id,
        )
        if row_key > previous_key:
            selected[key] = row
    return list(selected.values())


def _one_provider(rows: Sequence[PriceRow]) -> list[PriceRow]:
    by_provider: dict[str, list[PriceRow]] = defaultdict(list)
    for row in rows:
        by_provider[row.provider].append(row)
    for provider in _PROVIDER_PRIORITY:
        if provider in by_provider:
            return by_provider[provider]
    if not by_provider:
        return []
    first = sorted(by_provider)[0]
    return by_provider[first]


def _visible(
    rows: Sequence[PriceRow], cutoff_at: datetime, *, operational: bool
) -> list[PriceRow]:
    cutoff_utc = _utc(cutoff_at)
    visible = [
        row
        for row in rows
        if _utc(row.available_timestamp) <= cutoff_utc
        and _utc(row.timestamp) <= cutoff_utc
        and (
            not operational
            or (
                _utc(row.first_observed_at) <= cutoff_utc
                and _utc(row.retrieved_at) <= cutoff_utc
            )
        )
    ]
    return sorted(
        _latest_revisions(visible),
        key=lambda row: (row.market_date, _utc(row.timestamp), row.id),
    )


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0.0 or not math.isfinite(numerator + denominator):
        return None
    value = numerator / denominator - 1.0
    return value if math.isfinite(value) else None


def _price_features(
    rows: Sequence[PriceRow], *, prefix: str, include_level: bool = False
) -> tuple[dict[str, float], dict[str, tuple[SourceReference, ...]]]:
    """Calculate the configured price features with full window lineage."""

    if not rows:
        return {}, {}
    ordered = list(rows)
    closes = np.asarray(
        [float(row.adjusted_close or row.close) for row in ordered], dtype=float
    )
    values: dict[str, float] = {}
    lineage: dict[str, tuple[SourceReference, ...]] = {}

    def add(name: str, value: float | None, used: Sequence[PriceRow]) -> None:
        if value is None or not math.isfinite(value):
            return
        feature_name = f"{prefix}__{name}"
        values[feature_name] = float(value)
        lineage[feature_name] = tuple(_reference(row) for row in used)

    for window in (1, 2, 3, 5, 20):
        if len(ordered) > window:
            add(
                f"return_{window}d",
                _ratio(closes[-1], closes[-1 - window]),
                (ordered[-1 - window], ordered[-1]),
            )
    if len(ordered) >= 2 and closes[-1] > 0.0 and closes[-2] > 0.0:
        add(
            "log_return_1d",
            float(np.log(closes[-1] / closes[-2])),
            ordered[-2:],
        )
    one_day_returns = np.diff(closes) / closes[:-1]
    for window in (5, 20):
        if len(one_day_returns) >= window:
            sample = one_day_returns[-window:]
            add(
                f"volatility_{window}d",
                float(np.std(sample, ddof=1)),
                ordered[-(window + 1) :],
            )
    latest = ordered[-1]
    if latest.open is not None:
        add(
            "open_close_return",
            _ratio(float(latest.close), float(latest.open)),
            (latest,),
        )
    if latest.high is not None and latest.low is not None:
        add(
            "high_low_range",
            _ratio(float(latest.high), float(latest.low)),
            (latest,),
        )
    if len(ordered) >= 20:
        average = float(np.mean(closes[-20:]))
        add("ma20_deviation", _ratio(closes[-1], average), ordered[-20:])
    if include_level:
        add("level", float(latest.close), (latest,))
    return values, lineage


class PointInTimeDatasetBuilder:
    """Construct current and historical-as-of model rows from raw DB revisions."""

    def __init__(self, session: Session, config: AppConfig) -> None:
        self._session = session
        self._config = config

    def _load_rows(
        self, ticker: str, cutoff_at: datetime
    ) -> tuple[list[StockPrice], list[MarketData]]:
        stocks = list(
            self._session.scalars(
                select(StockPrice).where(
                    StockPrice.canonical_symbol == ticker,
                    StockPrice.available_timestamp <= cutoff_at,
                )
            )
        )
        market = list(
            self._session.scalars(
                select(MarketData).where(MarketData.available_timestamp <= cutoff_at)
            )
        )
        return stocks, market

    def _indicator_ids(self, ticker: str) -> tuple[str, ...]:
        stock = next(
            (item for item in self._config.stocks.stocks if item.ticker == ticker),
            None,
        )
        if stock is None:
            raise ValueError(f"unknown configured ticker: {ticker}")
        catalog = {item.id: item for item in self._config.indicators.indicators}
        requested = [
            *self._config.indicators.common,
            *self._config.indicators.sectors[stock.sector].indicators,
        ]
        requested.extend(
            item.id
            for item in self._config.indicators.indicators
            if ticker in item.applies_to_tickers
        )
        return tuple(
            dict.fromkeys(
                indicator_id
                for indicator_id in requested
                if catalog[indicator_id].resolution_status == "resolved"
            )
        )

    def _snapshot_max_age(self, indicator_id: str) -> timedelta | None:
        indicator = next(
            item
            for item in self._config.indicators.indicators
            if item.id == indicator_id
        )
        minutes = next(
            (
                source.max_age_minutes
                for source in indicator.sources
                if source.status == "verified"
                and source.snapshot_enabled
                and source.max_age_minutes is not None
            ),
            None,
        )
        return timedelta(minutes=minutes) if minutes is not None else None

    def _sample(
        self,
        *,
        ticker: str,
        sample_date: date,
        stock_rows: Sequence[StockPrice],
        market_rows: Sequence[MarketData],
        indicator_ids: Sequence[str],
        operational: bool,
        target_cutoff_at: datetime,
    ) -> ModelSample:
        cutoff_at = prediction_cutoff(sample_date)
        prior_stock = [
            row
            for row in stock_rows
            if row.market_date < sample_date and row.interval == "eod"
        ]
        visible_stock = _one_provider(
            _visible(prior_stock, cutoff_at, operational=operational)
        )
        values, lineage = _price_features(visible_stock, prefix="stock")
        warnings: set[str] = set()
        for indicator_id in indicator_ids:
            candidates = [
                row
                for row in market_rows
                if row.canonical_symbol == indicator_id and row.interval == "eod"
            ]
            selected = _one_provider(
                _visible(candidates, cutoff_at, operational=operational)
            )
            indicator_values, indicator_lineage = _price_features(
                selected,
                prefix=indicator_id,
                include_level="yield" in indicator_id or "spread" in indicator_id,
            )
            values.update(indicator_values)
            lineage.update(indicator_lineage)
            if not selected:
                warnings.add(f"{indicator_id}: unavailable at cutoff")
            elif selected[-1].data_quality in {"FREE_UNVERIFIED", "DELAYED"}:
                warnings.add(f"{indicator_id}: {selected[-1].data_quality}")

            # Snapshot features are a separate semantic series.  They are
            # never mixed into EOD windows, and historical rows require proof
            # that this system actually observed the snapshot by that day's
            # cutoff.  Consequently these features enter training only after
            # enough prospective daily observations have accumulated.
            max_age = self._snapshot_max_age(indicator_id)
            if max_age is not None:
                cutoff_utc = _utc(cutoff_at)
                snapshot_candidates = [
                    row
                    for row in market_rows
                    if row.canonical_symbol == indicator_id
                    and row.interval == "live_snapshot"
                    and _utc(row.timestamp) <= cutoff_utc
                    and _utc(row.available_timestamp) <= cutoff_utc
                    and _utc(row.first_observed_at) <= cutoff_utc
                    and _utc(row.retrieved_at) <= cutoff_utc
                    and cutoff_utc - _utc(row.timestamp) <= max_age
                ]
                snapshot_rows = _one_provider(_latest_revisions(snapshot_candidates))
                if snapshot_rows:
                    latest_snapshot = max(
                        snapshot_rows,
                        key=lambda row: (_utc(row.timestamp), row.id),
                    )
                    snapshot_name = f"{indicator_id}__snapshot_level"
                    values[snapshot_name] = float(latest_snapshot.close)
                    lineage[snapshot_name] = (_reference(latest_snapshot),)
                elif operational:
                    warnings.add(f"{indicator_id}: fresh 08:30 snapshot unavailable")

        target_candidates = [
            row
            for row in stock_rows
            if row.market_date == sample_date
            and _utc(row.available_timestamp) <= _utc(target_cutoff_at)
            and row.open is not None
        ]
        target_rows = _one_provider(_latest_revisions(target_candidates))
        target = target_rows[-1] if target_rows else None
        target_return = None
        target_difference = None
        target_open = None
        target_close = None
        target_lineage: tuple[SourceReference, ...] = ()
        if target is not None and target.open is not None:
            opening = float(target.open)
            closing = float(target.close)
            target_open = opening
            target_close = closing
            target_return = _ratio(closing, opening)
            target_difference = closing - opening
            target_lineage = (_reference(target),)
        reference_price = float(visible_stock[-1].close) if visible_stock else None
        reference_source = _reference(visible_stock[-1]) if visible_stock else None
        sample = ModelSample(
            ticker=ticker,
            sample_date=sample_date,
            cutoff_at=cutoff_at,
            values=values,
            lineage=lineage,
            target_return=target_return,
            target_difference=target_difference,
            target_open=target_open,
            target_close=target_close,
            target_lineage=target_lineage,
            reference_price=reference_price,
            reference_source=reference_source,
            warnings=tuple(sorted(warnings)),
        )
        sample.assert_safe(operational=operational)
        return sample

    def build(
        self,
        ticker: str,
        prediction_date: date,
        *,
        training_sessions: int = 120,
        minimum_feature_coverage: float = 0.80,
    ) -> ModelDataset:
        """Build the newest rolling window and current operational feature row."""

        if not 0.0 < minimum_feature_coverage <= 1.0:
            raise ValueError("minimum_feature_coverage must be in (0, 1]")
        main_cutoff = prediction_cutoff(prediction_date)
        stock_rows, market_rows = self._load_rows(ticker, main_cutoff)
        indicators = self._indicator_ids(ticker)
        sessions = japan_sessions_before(prediction_date, training_sessions)
        training = tuple(
            self._sample(
                ticker=ticker,
                sample_date=session_date,
                stock_rows=stock_rows,
                market_rows=market_rows,
                indicator_ids=indicators,
                operational=False,
                target_cutoff_at=main_cutoff,
            )
            for session_date in sessions
        )
        usable = tuple(item for item in training if item.target_return is not None)
        current = self._sample(
            ticker=ticker,
            sample_date=prediction_date,
            stock_rows=stock_rows,
            market_rows=market_rows,
            indicator_ids=indicators,
            operational=True,
            target_cutoff_at=main_cutoff,
        )
        candidates = sorted({name for item in usable for name in item.values})
        selected = tuple(
            name
            for name in candidates
            if name in current.values
            and sum(name in item.values for item in usable) / max(len(usable), 1)
            >= minimum_feature_coverage
        )
        training_frame = pd.DataFrame(
            [
                {name: item.values.get(name, np.nan) for name in selected}
                for item in usable
            ],
            index=[item.sample_date for item in usable],
            columns=selected,
            dtype=float,
        )
        target = pd.Series(
            [item.target_return for item in usable],
            index=training_frame.index,
            name="intraday_return",
            dtype=float,
        )
        current_frame = pd.DataFrame(
            [{name: current.values[name] for name in selected}],
            index=[prediction_date],
            columns=selected,
            dtype=float,
        )
        feature_coverage = len(selected) / len(candidates) if candidates else 0.0
        return ModelDataset(
            ticker=ticker,
            feature_names=selected,
            training_frame=training_frame,
            training_target=target,
            current_frame=current_frame,
            training_samples=usable,
            current_sample=current,
            candidate_feature_count=len(candidates),
            feature_coverage=feature_coverage,
        )

    def build_backtest_frame(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        *,
        initial_training_sessions: int = 120,
        minimum_feature_coverage: float = 0.80,
    ) -> BacktestDataset:
        """Build an honest estimated-PIT frame for walk-forward evaluation.

        EOD provider publication times from historical backfills are estimates,
        so this result is deliberately labelled ``ESTIMATED_BACKFILL``.  Live
        snapshots still require their real first-observed/retrieved evidence by
        each historical cutoff and therefore accumulate prospectively only.
        Feature coverage is frozen using only the initial training block.
        """

        if initial_training_sessions < 2:
            raise ValueError("initial_training_sessions must be at least 2")
        if not 0.0 < minimum_feature_coverage <= 1.0:
            raise ValueError("minimum_feature_coverage must be in (0, 1]")
        sessions = japan_sessions_between(start_date, end_date)
        if len(sessions) <= initial_training_sessions:
            raise ValueError("backtest range needs a training window plus OOS rows")
        as_of = datetime.now(UTC)
        stock_rows, market_rows = self._load_rows(ticker, as_of)
        indicators = self._indicator_ids(ticker)
        samples = tuple(
            self._sample(
                ticker=ticker,
                sample_date=session_date,
                stock_rows=stock_rows,
                market_rows=market_rows,
                indicator_ids=indicators,
                operational=False,
                target_cutoff_at=as_of,
            )
            for session_date in sessions
        )
        usable = tuple(
            sample
            for sample in samples
            if sample.target_return is not None
            and sample.target_open is not None
            and sample.target_close is not None
        )
        if len(usable) <= initial_training_sessions:
            raise ValueError("backtest has too few realized target rows")
        initial = usable[:initial_training_sessions]
        candidates = sorted({name for sample in initial for name in sample.values})
        selected = tuple(
            name
            for name in candidates
            if sum(name in sample.values for sample in initial) / len(initial)
            >= minimum_feature_coverage
        )
        if not selected:
            raise ValueError(
                "initial backtest window has no sufficiently covered features"
            )
        frame = pd.DataFrame.from_records(
            [
                {
                    "ticker": sample.ticker,
                    "market_date": sample.sample_date,
                    "intraday_return": sample.target_return,
                    "open": sample.target_open,
                    "close": sample.target_close,
                    **{name: sample.values.get(name, np.nan) for name in selected},
                }
                for sample in usable
            ]
        )
        return BacktestDataset(ticker, frame, selected)
