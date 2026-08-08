"""EODHD REST provider with sanitized errors and point-in-time normalization."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr

from data.availability import eod_availability, live_availability
from data.providers.base import (
    MarketDataProvider,
    ProviderEntitlementError,
    ProviderFetchError,
    ProviderResponseError,
    SymbolResolution,
)
from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    FetchRequest,
    MarketBar,
    ProviderHealth,
)

LOGGER = logging.getLogger(__name__)
UTC = UTC
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:^=-]+$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _decimal(value: Any, field: str, *, required: bool = False) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise ProviderResponseError(f"EODHD response is missing {field}")
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ProviderResponseError(f"EODHD returned invalid {field}") from exc
    if not result.is_finite():
        raise ProviderResponseError(f"EODHD returned non-finite {field}")
    return result


def _integer(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError
        return int(number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderResponseError(f"EODHD returned invalid {field}") from exc


def _raw_hash(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _field(item: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in item:
            return item[name]
    return None


class EodhdProvider(MarketDataProvider):
    """Synchronous EODHD client suitable for short-lived scheduled jobs."""

    name = "eodhd"

    def __init__(
        self,
        api_key: str | SecretStr,
        *,
        base_url: str = "https://eodhd.com/api",
        timeout_seconds: float = 20.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        secret = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        if not secret.get_secret_value().strip():
            raise ValueError("EODHD API key is required")
        if timeout_seconds <= 0 or max_retries < 0 or backoff_seconds < 0:
            raise ValueError("invalid HTTP retry configuration")
        self._api_key = secret
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._symbol_cache: dict[str, frozenset[str]] = {}
        self._exchange_rows: list[dict[str, Any]] | None = None
        self._symbol_rows: dict[str, list[dict[str, Any]]] = {}
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Accept": "application/json",
                "User-Agent": "japan-stock-predictor/0.1",
            },
        )

    def __repr__(self) -> str:
        return "EodhdProvider(api_key=**********)"

    @staticmethod
    def _safe_identifier(value: str, label: str) -> str:
        if not SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError(f"invalid {label}")
        return quote(value, safe="._:^=-")

    def _request_json(
        self,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        query: dict[str, Any] = dict(params or {})
        query["api_token"] = self._api_key.get_secret_value()
        query.setdefault("fmt", "json")
        last_reason = "upstream failure"

        for attempt in range(self._max_retries + 1):
            response: httpx.Response | None = None
            self._before_request_attempt()
            try:
                received = self._client.get(endpoint, params=query)
            except (httpx.TimeoutException, httpx.TransportError):
                last_reason = "network or timeout error"
                retryable = True
            else:
                response = received
                retryable = received.status_code == 429 or received.status_code >= 500
                if not retryable:
                    if received.status_code in {401, 403}:
                        raise ProviderFetchError(
                            "EODHD authentication or plan entitlement was rejected"
                        )
                    if received.status_code >= 400:
                        raise ProviderFetchError(
                            f"EODHD request failed with HTTP {received.status_code}"
                        )
                    try:
                        return received.json()
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise ProviderResponseError(
                            "EODHD returned a non-JSON response"
                        ) from exc
                last_reason = f"retryable HTTP {received.status_code}"

            if attempt >= self._max_retries:
                break
            retry_after = 0.0
            if response is not None:
                raw_retry_after = response.headers.get("Retry-After", "")
                try:
                    retry_after = max(0.0, min(float(raw_retry_after), 60.0))
                except ValueError:
                    retry_after = 0.0
            delay = max(retry_after, self._backoff_seconds * (2**attempt))
            LOGGER.warning(
                "provider request retry",
                extra={
                    "provider": self.name,
                    "endpoint": endpoint,
                    "attempt": attempt + 1,
                },
            )
            self._sleeper(delay)

        raise ProviderFetchError(f"EODHD request exhausted retries: {last_reason}")

    def _before_request_attempt(self) -> None:
        """Hook used by plan-specific subclasses to enforce local call budgets."""

    @staticmethod
    def _require_list(payload: Any, endpoint_name: str) -> list[dict[str, Any]]:
        if not isinstance(payload, list):
            raise ProviderResponseError(f"EODHD {endpoint_name} response is not a list")
        if not all(isinstance(item, dict) for item in payload):
            raise ProviderResponseError(f"EODHD {endpoint_name} contains invalid rows")
        return payload

    def list_exchanges(self) -> list[dict[str, Any]]:
        """Return provider-declared exchanges from the official metadata API."""

        if self._exchange_rows is None:
            self._exchange_rows = self._require_list(
                self._request_json("/exchanges-list/"), "exchange list"
            )
        return [dict(item) for item in self._exchange_rows]

    def list_symbols(self, exchange_code: str) -> list[dict[str, Any]]:
        """Return provider-declared symbols for one verified exchange code."""

        code = self._safe_identifier(exchange_code, "exchange code")
        cache_key = exchange_code.upper()
        if cache_key not in self._symbol_rows:
            payload = self._request_json(f"/exchange-symbol-list/{code}")
            self._symbol_rows[cache_key] = self._require_list(payload, "symbol list")
        return [dict(item) for item in self._symbol_rows[cache_key]]

    def validate_provider_symbol(self, provider_symbol: str) -> bool:
        """Confirm a configured CODE.EXCHANGE value against EODHD's live catalog."""

        self._safe_identifier(provider_symbol, "provider symbol")
        code, separator, exchange = provider_symbol.rpartition(".")
        if not separator or not code or not exchange:
            raise ValueError("provider symbol must use CODE.EXCHANGE format")
        exchange_key = exchange.upper()
        if exchange_key not in self._symbol_cache:
            declared: set[str] = set()
            for item in self.list_symbols(exchange_key):
                item_code = str(_field(item, "Code", "code") or "")
                item_exchange = str(
                    _field(item, "Exchange", "exchange") or exchange_key
                )
                if item_code:
                    declared.add(f"{item_code}.{item_exchange}".upper())
            self._symbol_cache[exchange_key] = frozenset(declared)
        return provider_symbol.upper() in self._symbol_cache[exchange_key]

    def resolve_symbol(
        self,
        *,
        canonical_symbol: str,
        country_iso: str,
        exchange_mic: str,
    ) -> SymbolResolution | None:
        """Resolve using exchange and ticker listings returned by EODHD itself."""

        wanted_country = country_iso.upper()
        wanted_mic = exchange_mic.upper()
        candidates: list[dict[str, Any]] = []
        for exchange in self.list_exchanges():
            country = str(
                _field(exchange, "CountryISO2", "CountryISO", "country_iso2") or ""
            ).upper()
            mics = {
                value.strip().upper()
                for value in str(
                    _field(exchange, "OperatingMIC", "OperatingMic", "operating_mic")
                    or ""
                ).split(",")
                if value.strip()
            }
            if country == wanted_country and wanted_mic in mics:
                candidates.append(exchange)

        for exchange in candidates:
            exchange_code = str(_field(exchange, "Code", "code") or "")
            if not exchange_code:
                continue
            for item in self.list_symbols(exchange_code):
                code = str(_field(item, "Code", "code") or "")
                if code != canonical_symbol:
                    continue
                declared_exchange = str(
                    _field(item, "Exchange", "exchange") or exchange_code
                )
                return SymbolResolution(
                    canonical_symbol=canonical_symbol,
                    provider_symbol=f"{code}.{declared_exchange}",
                    exchange_code=declared_exchange,
                    exchange_mic=wanted_mic,
                    name=str(_field(item, "Name", "name") or canonical_symbol),
                    currency=(
                        str(_field(item, "Currency", "currency"))
                        if _field(item, "Currency", "currency")
                        else None
                    ),
                    verified_at=self._clock().astimezone(UTC),
                )
        return None

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        """Fetch raw EOD OHLCV and attach explicit availability evidence."""

        symbol = self._safe_identifier(request.provider_symbol, "provider symbol")
        payload = self._request_json(
            f"/eod/{symbol}",
            params={
                "from": request.start_date.isoformat(),
                "to": request.end_date.isoformat(),
                "period": "d",
                "order": "a",
            },
        )
        rows = self._require_list(payload, "EOD")
        observed_at = self._clock().astimezone(UTC)
        normalized: list[MarketBar] = []
        for item in rows:
            try:
                market_date = date.fromisoformat(str(item["date"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderResponseError(
                    "EODHD returned an invalid EOD date"
                ) from exc
            event_at, available_at, method = eod_availability(
                market_date,
                market_timezone=request.market_timezone,
                market_close=request.market_close,
                provider_lag_minutes=request.availability_lag_minutes,
                first_observed_at=observed_at,
            )
            try:
                normalized.append(
                    MarketBar(
                        canonical_symbol=request.canonical_symbol,
                        provider_symbol=request.provider_symbol,
                        provider=self.name,
                        market=request.market,
                        market_timezone=request.market_timezone,
                        market_date=market_date,
                        timestamp=event_at,
                        source_timestamp=None,
                        available_timestamp=available_at,
                        first_observed_at=observed_at,
                        retrieved_at=observed_at,
                        interval=DataInterval.EOD,
                        availability_method=method,
                        data_quality=DataQuality.EOD_CONFIRMED,
                        is_realtime=False,
                        is_delayed=False,
                        open=_decimal(item.get("open"), "open"),
                        high=_decimal(item.get("high"), "high"),
                        low=_decimal(item.get("low"), "low"),
                        close=_decimal(item.get("close"), "close", required=True),  # type: ignore[arg-type]
                        adjusted_close=_decimal(
                            item.get("adjusted_close"), "adjusted_close"
                        ),
                        volume=_integer(item.get("volume"), "volume"),
                        currency=request.currency,
                        raw_hash=_raw_hash(item),
                    )
                )
            except ValueError as exc:
                raise ProviderResponseError(
                    "EODHD returned invalid OHLCV semantics"
                ) from exc
        return normalized

    def fetch_live(
        self,
        *,
        canonical_symbol: str,
        provider_symbol: str,
        market: str,
        market_timezone: str,
        currency: str | None = None,
    ) -> MarketBar:
        """Fetch one delayed quote; availability is its first local observation."""

        symbol = self._safe_identifier(provider_symbol, "provider symbol")
        payload = self._request_json(f"/real-time/{symbol}")
        if not isinstance(payload, dict):
            raise ProviderResponseError("EODHD live response is not an object")
        observed_at = self._clock().astimezone(UTC)
        raw_timestamp = payload.get("timestamp")
        if not isinstance(raw_timestamp, (str, int, float)) or isinstance(
            raw_timestamp, bool
        ):
            raise ProviderResponseError("EODHD live response has invalid timestamp")
        try:
            source_at = datetime.fromtimestamp(int(raw_timestamp), tz=UTC)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProviderResponseError(
                "EODHD live response has invalid timestamp"
            ) from exc
        available_at, method = live_availability(observed_at)
        local_date = source_at.astimezone(ZoneInfo(market_timezone)).date()
        try:
            return MarketBar(
                canonical_symbol=canonical_symbol,
                provider_symbol=provider_symbol,
                provider=self.name,
                market=market,
                market_timezone=market_timezone,
                market_date=local_date,
                timestamp=source_at,
                source_timestamp=source_at,
                available_timestamp=available_at,
                first_observed_at=observed_at,
                retrieved_at=observed_at,
                interval=DataInterval.LIVE_SNAPSHOT,
                availability_method=method,
                data_quality=DataQuality.DELAYED,
                is_realtime=False,
                is_delayed=True,
                open=_decimal(payload.get("open"), "open"),
                high=_decimal(payload.get("high"), "high"),
                low=_decimal(payload.get("low"), "low"),
                close=_decimal(payload.get("close"), "close", required=True),  # type: ignore[arg-type]
                volume=_integer(payload.get("volume"), "volume"),
                currency=currency,
                raw_hash=_raw_hash(payload),
            )
        except ValueError as exc:
            raise ProviderResponseError(
                "EODHD returned invalid live OHLCV semantics"
            ) from exc

    def fetch_treasury_yields(self, year: int) -> list[dict[str, Any]]:
        """Fetch provider's official US Treasury par-yield dataset."""

        if year < 1900 or year > self._clock().year + 1:
            raise ValueError("invalid Treasury year")
        payload = self._request_json("/ust/yield-rates", params={"filter[year]": year})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderResponseError("EODHD Treasury response has invalid shape")
        rows = payload["data"]
        if not all(isinstance(item, dict) for item in rows):
            raise ProviderResponseError("EODHD Treasury response contains invalid rows")
        return cast(list[dict[str, Any]], rows)

    def fetch_treasury_yield_bars(
        self,
        year: int,
        *,
        tenor_symbols: Mapping[str, str],
    ) -> list[MarketBar]:
        """Normalize date-only Treasury observations without inventing publish times.

        Because EODHD does not return historical publication timestamps for this
        endpoint, every row is first usable when this process actually observes
        it. This is intentionally conservative for backtests.
        """

        rows = self.fetch_treasury_yields(year)
        observed_at = self._clock().astimezone(UTC)
        normalized: list[MarketBar] = []
        for item in rows:
            tenor = str(_field(item, "tenor", "Tenor") or "").upper()
            canonical_symbol = tenor_symbols.get(tenor)
            if canonical_symbol is None:
                continue
            try:
                market_date = date.fromisoformat(str(_field(item, "date", "Date")))
            except (TypeError, ValueError) as exc:
                raise ProviderResponseError(
                    "EODHD Treasury response has invalid date"
                ) from exc
            rate = _decimal(_field(item, "rate", "Rate"), "rate", required=True)
            # This timestamp represents a date-only observation, not a publish time.
            event_at = datetime.combine(market_date, datetime.min.time(), tzinfo=UTC)
            normalized.append(
                MarketBar(
                    canonical_symbol=canonical_symbol,
                    provider_symbol=f"UST:{tenor}",
                    provider=self.name,
                    market="US_TREASURY",
                    market_timezone="America/New_York",
                    market_date=market_date,
                    timestamp=event_at,
                    source_timestamp=None,
                    available_timestamp=observed_at,
                    first_observed_at=observed_at,
                    retrieved_at=observed_at,
                    interval=DataInterval.EOD,
                    availability_method=AvailabilityMethod.FIRST_OBSERVED,
                    data_quality=DataQuality.FREE_UNVERIFIED,
                    is_realtime=False,
                    is_delayed=False,
                    close=rate,  # type: ignore[arg-type]
                    currency="PERCENT",
                    raw_hash=_raw_hash(item),
                    quality_flags=("date_only", "historical_publish_time_unknown"),
                )
            )
        return normalized

    def healthcheck(self) -> ProviderHealth:
        checked_at = self._clock().astimezone(UTC)
        try:
            exchanges = self.list_exchanges()
        except (ProviderFetchError, ProviderResponseError) as exc:
            return ProviderHealth(self.name, False, checked_at, str(exc))
        return ProviderHealth(
            self.name,
            bool(exchanges),
            checked_at,
            "authenticated metadata request succeeded"
            if exchanges
            else "empty exchange list",
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class EODHDFreeProvider(EodhdProvider):
    """Free-plan EODHD adapter restricted to its documented entitlement.

    Raw provenance remains ``provider='eodhd'`` so upgrading the account does
    not create a second synthetic source.  This class only constrains account
    capabilities and per-run usage.
    """

    plan_variant = "free"

    def __init__(
        self,
        api_key: str | SecretStr,
        *,
        max_calls_per_run: int = 5,
        **kwargs: Any,
    ) -> None:
        if not 1 <= max_calls_per_run <= 20:
            raise ValueError("free-plan call budget must be between 1 and 20")
        self._max_calls_per_run = max_calls_per_run
        self._calls_used = 0
        super().__init__(api_key, **kwargs)

    @property
    def calls_used(self) -> int:
        return self._calls_used

    def _before_request_attempt(self) -> None:
        if self._calls_used >= self._max_calls_per_run:
            raise ProviderEntitlementError("EODHD Free per-run call budget exhausted")
        self._calls_used += 1

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        earliest = self._clock().astimezone(UTC).date() - timedelta(days=366)
        if request.start_date < earliest:
            raise ProviderEntitlementError(
                "EODHD Free EOD history is limited to approximately one year"
            )
        return super().fetch_eod(request)

    def fetch_live(self, **_: Any) -> MarketBar:
        raise ProviderEntitlementError(
            "generic live quotes are not enabled for EODHD Free fallback"
        )

    def fetch_treasury_yields(self, year: int) -> list[dict[str, Any]]:
        del year
        raise ProviderEntitlementError(
            "Treasury data must use the free official U.S. Treasury provider"
        )
