"""Unofficial Yahoo Finance adapter isolated behind provider contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from data.availability import eod_availability, live_availability
from data.providers.base import (
    MarketDataProvider,
    ProviderFetchError,
    ProviderResponseError,
    SymbolResolution,
)
from data.schemas import (
    DataInterval,
    DataQuality,
    FetchRequest,
    MarketBar,
    ProviderHealth,
    SessionOpenRequest,
    SnapshotRequest,
)

UTC = UTC
SAFE_SYMBOL = re.compile(r"^[A-Za-z0-9.^=_-]+$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


class YahooBackend(Protocol):
    """Small injectable boundary around yfinance's public surface."""

    def history(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
        """Return one-symbol history."""

    def search(self, query: str, *, max_results: int) -> list[Mapping[str, Any]]:
        """Return Yahoo search quote records."""


class YFinanceBackend:
    """Production backend; no business module imports yfinance directly."""

    def history(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
        return cast(pd.DataFrame, yf.Ticker(symbol).history(**kwargs))

    def search(self, query: str, *, max_results: int) -> list[Mapping[str, Any]]:
        quotes = yf.Search(query, max_results=max_results).quotes
        return [item for item in quotes if isinstance(item, Mapping)]


def _decimal(value: Any, field_name: str, *, required: bool = False) -> Decimal | None:
    if value is None or bool(pd.isna(value)):
        if required:
            raise ProviderResponseError(f"Yahoo response is missing {field_name}")
        return None
    try:
        result = Decimal(str(float(value)))
    except (InvalidOperation, TypeError, ValueError, OverflowError) as exc:
        raise ProviderResponseError(f"Yahoo returned invalid {field_name}") from exc
    if not result.is_finite():
        raise ProviderResponseError(f"Yahoo returned non-finite {field_name}")
    return result


def _volume(value: Any) -> int | None:
    if value is None or bool(pd.isna(value)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderResponseError("Yahoo returned invalid volume") from exc
    if not math.isfinite(result) or result < 0:
        raise ProviderResponseError("Yahoo returned invalid volume")
    return int(result)


def _raw_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class _RejectedRow:
    """One daily row Yahoo published in an unusable state."""

    market_date: date
    reason: str


def _column(row: pd.Series, name: str) -> Any:
    for candidate in (name, name.lower(), name.upper()):
        if candidate in row.index:
            return row[candidate]
    return None


class YahooFinanceProvider(MarketDataProvider):
    """Best-effort personal/research provider for free Yahoo market data."""

    name = "yahoo_finance"

    def __init__(
        self,
        *,
        backend: YahooBackend | None = None,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        clock: Callable[[], datetime] = _utc_now,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or backoff_seconds < 0:
            raise ValueError("invalid Yahoo retry configuration")
        self._backend = backend or YFinanceBackend()
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._last_eod_rejections: tuple[_RejectedRow, ...] = ()

    @property
    def last_eod_rejections(self) -> tuple[tuple[date, str], ...]:
        """Rows dropped by the most recent ``fetch_eod`` call, for reporting."""

        return tuple(
            (item.market_date, item.reason) for item in self._last_eod_rejections
        )

    @staticmethod
    def _safe_symbol(value: str) -> str:
        if not value or not SAFE_SYMBOL.fullmatch(value):
            raise ValueError("invalid Yahoo provider symbol")
        return value

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("provider clock must be timezone-aware")
        return now.astimezone(UTC)

    def _history(self, symbol: str, **kwargs: Any) -> pd.DataFrame:
        last_reason = "upstream failure"
        for attempt in range(self._max_retries + 1):
            try:
                frame = self._backend.history(
                    symbol,
                    timeout=self._timeout_seconds,
                    **kwargs,
                )
            except Exception as exc:  # yfinance exposes several unstable exceptions
                last_reason = type(exc).__name__
            else:
                if not isinstance(frame, pd.DataFrame):
                    raise ProviderResponseError("Yahoo history response is not a table")
                return frame.copy()
            if attempt < self._max_retries:
                self._sleeper(self._backoff_seconds * (2**attempt))
        raise ProviderFetchError(f"Yahoo request exhausted retries: {last_reason}")

    @staticmethod
    def _daily_market_date(index_value: Any, timezone_name: str) -> date:
        try:
            stamp = pd.Timestamp(index_value)
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError("Yahoo returned invalid daily index") from exc
        if stamp.tzinfo is not None:
            stamp = stamp.tz_convert(ZoneInfo(timezone_name))
        return cast(date, stamp.date())

    @staticmethod
    def _source_time(index_value: Any, timezone_name: str) -> datetime:
        try:
            stamp = pd.Timestamp(index_value)
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError(
                "Yahoo returned invalid intraday index"
            ) from exc
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(ZoneInfo(timezone_name))
        return cast(datetime, stamp.to_pydatetime().astimezone(UTC))

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        symbol = self._safe_symbol(request.provider_symbol)
        frame = self._history(
            symbol,
            start=request.start_date.isoformat(),
            end=(request.end_date + timedelta(days=1)).isoformat(),
            interval="1d",
            auto_adjust=False,
            actions=False,
            repair=False,
            prepost=False,
        )
        if frame.empty:
            raise ProviderResponseError("Yahoo returned no EOD data")

        observed_at = self._now()
        normalized: list[MarketBar] = []
        rejected: list[_RejectedRow] = []
        self._last_eod_rejections = ()
        for index_value, row in frame.iterrows():
            market_date = self._daily_market_date(index_value, request.market_timezone)
            if not request.start_date <= market_date <= request.end_date:
                continue
            event_at, available_at, method = eod_availability(
                market_date,
                market_timezone=request.market_timezone,
                market_close=request.market_close,
                provider_lag_minutes=request.availability_lag_minutes,
                first_observed_at=observed_at,
            )
            payload = {
                "symbol": symbol,
                "date": market_date.isoformat(),
                "open": _column(row, "Open"),
                "high": _column(row, "High"),
                "low": _column(row, "Low"),
                "close": _column(row, "Close"),
                "adjusted_close": _column(row, "Adj Close"),
                "volume": _column(row, "Volume"),
            }
            flags = ["yahoo_unofficial", "historical_availability_estimated"]
            if symbol.endswith("=F"):
                flags.append("continuous_futures_contract")
            try:
                normalized.append(
                    MarketBar(
                        canonical_symbol=request.canonical_symbol,
                        provider_symbol=symbol,
                        provider=self.name,
                        market=request.market,
                        market_timezone=request.market_timezone,
                        market_date=market_date,
                        timestamp=event_at,
                        available_timestamp=available_at,
                        first_observed_at=observed_at,
                        retrieved_at=observed_at,
                        interval=DataInterval.EOD,
                        availability_method=method,
                        close=_decimal(payload["close"], "close", required=True),  # type: ignore[arg-type]
                        data_quality=DataQuality.FREE_UNVERIFIED,
                        is_realtime=False,
                        is_delayed=False,
                        open=_decimal(payload["open"], "open"),
                        high=_decimal(payload["high"], "high"),
                        low=_decimal(payload["low"], "low"),
                        adjusted_close=_decimal(
                            payload["adjusted_close"], "adjusted close"
                        ),
                        volume=_volume(payload["volume"]),
                        currency=request.currency,
                        raw_hash=_raw_hash(payload),
                        quality_flags=tuple(flags),
                    )
                )
            except (ValueError, ProviderResponseError) as exc:
                # Yahoo publishes individually defective daily rows: a Japanese
                # equity whose close has not been consolidated yet, or an FX
                # pair whose low rounds above its close. Discarding the whole
                # symbol for one of them threw away 365 usable sessions out of
                # 366, which is what left every series unfetched. Drop the row,
                # keep the evidence, and let the caller judge the coverage.
                rejected.append(_RejectedRow(market_date, str(exc)))
        self._last_eod_rejections = tuple(rejected)
        if not normalized:
            if rejected:
                reasons = ", ".join(sorted({item.reason for item in rejected}))
                raise ProviderResponseError(
                    f"Yahoo returned no usable EOD rows ({len(rejected)} rejected: "
                    f"{reasons})"
                )
            raise ProviderResponseError("Yahoo returned no rows in requested range")
        return normalized

    def fetch_snapshot(self, request: SnapshotRequest) -> MarketBar:
        symbol = self._safe_symbol(request.provider_symbol)
        frame = self._history(
            symbol,
            period="1d",
            interval="1m",
            auto_adjust=False,
            actions=False,
            repair=False,
            prepost=True,
        ).dropna(how="all")
        if frame.empty:
            raise ProviderResponseError("Yahoo returned no snapshot data")
        index_value = frame.index[-1]
        row = frame.iloc[-1]
        observed_at = self._now()
        source_at = self._source_time(index_value, request.market_timezone)
        available_at, method = live_availability(observed_at)
        payload = {
            "symbol": symbol,
            "source_timestamp": source_at.isoformat(),
            "open": _column(row, "Open"),
            "high": _column(row, "High"),
            "low": _column(row, "Low"),
            "close": _column(row, "Close"),
            "volume": _column(row, "Volume"),
        }
        flags = ["yahoo_unofficial", "delay_not_guaranteed"]
        if symbol.endswith("=F"):
            flags.append("continuous_futures_contract")
        try:
            return MarketBar(
                canonical_symbol=request.canonical_symbol,
                provider_symbol=symbol,
                provider=self.name,
                market=request.market,
                market_timezone=request.market_timezone,
                market_date=source_at.astimezone(
                    ZoneInfo(request.market_timezone)
                ).date(),
                timestamp=source_at,
                source_timestamp=source_at,
                available_timestamp=available_at,
                first_observed_at=observed_at,
                retrieved_at=observed_at,
                interval=DataInterval.LIVE_SNAPSHOT,
                availability_method=method,
                close=_decimal(payload["close"], "close", required=True),  # type: ignore[arg-type]
                data_quality=DataQuality.DELAYED,
                is_realtime=False,
                is_delayed=True,
                open=_decimal(payload["open"], "open"),
                high=_decimal(payload["high"], "high"),
                low=_decimal(payload["low"], "low"),
                volume=_volume(payload["volume"]),
                currency=request.currency,
                raw_hash=_raw_hash(payload),
                quality_flags=tuple(flags),
            )
        except ValueError as exc:
            raise ProviderResponseError(
                "Yahoo returned invalid snapshot OHLCV semantics"
            ) from exc

    def fetch_session_open(self, request: SessionOpenRequest) -> MarketBar:
        """Return the first regular-session minute bar, observed prospectively."""

        symbol = self._safe_symbol(request.provider_symbol)
        frame = self._history(
            symbol,
            start=request.session_date.isoformat(),
            end=(request.session_date + timedelta(days=1)).isoformat(),
            interval="1m",
            auto_adjust=False,
            actions=False,
            repair=False,
            prepost=False,
        ).dropna(how="all")
        if frame.empty:
            raise ProviderResponseError("Yahoo returned no intraday session data")
        try:
            opening_hour, opening_minute = (
                int(part) for part in request.session_open.split(":")
            )
        except (TypeError, ValueError) as exc:
            raise ProviderResponseError("invalid configured session open") from exc
        selected_index: Any | None = None
        selected_row: pd.Series | None = None
        for index_value, row in frame.sort_index().iterrows():
            source_at = self._source_time(index_value, request.market_timezone)
            local = source_at.astimezone(ZoneInfo(request.market_timezone))
            if local.date() != request.session_date:
                continue
            if (local.hour, local.minute) < (opening_hour, opening_minute):
                continue
            selected_index = index_value
            selected_row = row
            break
        if selected_index is None or selected_row is None:
            raise ProviderResponseError("Yahoo returned no regular-session open bar")
        observed_at = self._now()
        source_at = self._source_time(selected_index, request.market_timezone)
        if source_at > observed_at:
            raise ProviderResponseError("Yahoo open-bar timestamp is in the future")
        available_at, method = live_availability(observed_at)
        payload = {
            "symbol": symbol,
            "source_timestamp": source_at.isoformat(),
            "open": _column(selected_row, "Open"),
            "high": _column(selected_row, "High"),
            "low": _column(selected_row, "Low"),
            "close": _column(selected_row, "Close"),
            "volume": _column(selected_row, "Volume"),
        }
        try:
            return MarketBar(
                canonical_symbol=request.canonical_symbol,
                provider_symbol=symbol,
                provider=self.name,
                market=request.market,
                market_timezone=request.market_timezone,
                market_date=request.session_date,
                timestamp=source_at,
                source_timestamp=source_at,
                available_timestamp=available_at,
                first_observed_at=observed_at,
                retrieved_at=observed_at,
                interval=DataInterval.ONE_MINUTE,
                availability_method=method,
                close=_decimal(payload["close"], "close", required=True),  # type: ignore[arg-type]
                data_quality=DataQuality.DELAYED,
                is_realtime=False,
                is_delayed=True,
                open=_decimal(payload["open"], "open", required=True),
                high=_decimal(payload["high"], "high"),
                low=_decimal(payload["low"], "low"),
                volume=_volume(payload["volume"]),
                currency=request.currency,
                raw_hash=_raw_hash(payload),
                quality_flags=(
                    "yahoo_unofficial",
                    "session_open_from_first_minute_bar",
                    "delay_not_guaranteed",
                ),
            )
        except ValueError as exc:
            raise ProviderResponseError(
                "Yahoo returned invalid open-bar OHLCV semantics"
            ) from exc

    def validate_provider_symbol(self, provider_symbol: str) -> bool:
        symbol = self._safe_symbol(provider_symbol)
        try:
            frame = self._history(
                symbol,
                period="5d",
                interval="1d",
                auto_adjust=False,
                actions=False,
                repair=False,
                prepost=False,
            )
        except (ProviderFetchError, ProviderResponseError):
            return False
        return not frame.dropna(how="all").empty

    def resolve_symbol(
        self,
        *,
        canonical_symbol: str,
        country_iso: str,
        exchange_mic: str,
    ) -> SymbolResolution | None:
        del country_iso
        candidates = [
            item
            for item in self._backend.search(canonical_symbol, max_results=10)
            if str(item.get("symbol", "")).split(".", maxsplit=1)[0] == canonical_symbol
        ]
        if len(candidates) != 1:
            return None
        item = candidates[0]
        symbol = str(item.get("symbol", ""))
        if not symbol:
            return None
        return SymbolResolution(
            canonical_symbol=canonical_symbol,
            provider_symbol=symbol,
            exchange_code=str(item.get("exchange", "UNKNOWN")),
            exchange_mic=exchange_mic,
            name=str(item.get("shortname") or item.get("longname") or canonical_symbol),
            currency=(str(item["currency"]) if item.get("currency") else None),
            verified_at=self._now(),
        )

    def healthcheck(self) -> ProviderHealth:
        checked_at = self._now()
        try:
            ok = self.validate_provider_symbol("SPY")
        except (ValueError, ProviderFetchError, ProviderResponseError) as exc:
            return ProviderHealth(self.name, False, checked_at, str(exc))
        return ProviderHealth(
            self.name,
            ok,
            checked_at,
            "Yahoo history request succeeded" if ok else "Yahoo returned no SPY data",
        )

    def close(self) -> None:
        """yfinance owns its process-wide session; this adapter owns no resource."""
