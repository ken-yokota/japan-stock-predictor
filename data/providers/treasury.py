"""Official U.S. Treasury par-yield provider.

The Treasury XML feed exposes a market date, but not a historical publication
timestamp for each row.  Point-in-time availability therefore follows the
Treasury's published methodology: inputs are observed at or near 15:30 Eastern
and rates are usually available by 18:00 Eastern.  The latter is explicitly
stored as a schedule estimate, never inferred from the Atom ``updated`` field.
"""

from __future__ import annotations

import hashlib
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from datetime import time as wall_time
from decimal import Decimal, InvalidOperation
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from data.providers.base import (
    MarketDataProvider,
    ProviderFetchError,
    ProviderResponseError,
)
from data.schemas import (
    AvailabilityMethod,
    DataInterval,
    DataQuality,
    FetchRequest,
    MarketBar,
    ProviderHealth,
)

TREASURY_TENORS: dict[str, str] = {
    "2Y": "us_2y_yield",
    "10Y": "us_10y_yield",
    "30Y": "us_30y_yield",
}

TREASURY_XML_PATH = "/resource-center/data-chart-center/interest-rates/pages/xml"
TREASURY_DATASET = "daily_treasury_yield_curve"
TREASURY_MARKET_TIMEZONE = "America/New_York"
TREASURY_MARKET = "US_TREASURY"
PUBLISHED_SCHEDULE_ESTIMATE_FLAG = "published_schedule_estimate"
EARLY_FIRST_OBSERVED_FLAG = "first_observed_before_published_schedule"

_TENOR_FIELDS: dict[str, str] = {
    "BC_2YEAR": "2Y",
    "BC_10YEAR": "10Y",
    "BC_30YEAR": "30Y",
}
_TENOR_ORDER = {tenor: index for index, tenor in enumerate(TREASURY_TENORS)}
_EASTERN = ZoneInfo(TREASURY_MARKET_TIMEZONE)
_MARKET_EVENT_TIME = wall_time(15, 30)
_PUBLISHED_SCHEDULE_TIME = wall_time(18, 0)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _local_name(tag: str) -> str:
    """Return an XML local name without depending on a namespace URI."""

    return tag.rsplit("}", 1)[-1]


def _as_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _market_timestamp(market_date: date, value: wall_time) -> datetime:
    return datetime.combine(market_date, value, tzinfo=_EASTERN).astimezone(UTC)


def _parse_market_date(value: str) -> date:
    try:
        # The official feed currently uses ``YYYY-MM-DDT00:00:00``.  Parsing the
        # complete value also tolerates a future explicit offset or ``Z``.
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise ProviderResponseError(
            "U.S. Treasury XML contains an invalid NEW_DATE"
        ) from exc


def _parse_rate(value: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ProviderResponseError(
            f"U.S. Treasury XML contains an invalid {field} rate"
        ) from exc
    if not parsed.is_finite():
        raise ProviderResponseError(
            f"U.S. Treasury XML contains a non-finite {field} rate"
        )
    return parsed


def _raw_hash(market_date: date, field: str, rate: Decimal) -> str:
    # Deliberately excludes Atom ``updated``: it is feed metadata and is not
    # evidence of the original row's publication time.
    value = f"{market_date.isoformat()}|{field}|{rate}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class TreasuryDataProvider(Protocol):
    """Structural capability implemented by Treasury-yield providers."""

    name: str

    def fetch_treasury_yield_bars(
        self,
        year: int,
        *,
        tenor_symbols: Mapping[str, str],
    ) -> list[MarketBar]:
        """Fetch point-in-time normalized yield observations for one year."""


class TreasuryProvider(MarketDataProvider):
    """Synchronous client for the free official Treasury XML feed."""

    name = "us_treasury"

    def __init__(
        self,
        *,
        base_url: str = "https://home.treasury.gov",
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or backoff_seconds < 0:
            raise ValueError("invalid Treasury HTTP retry configuration")
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "Accept": "application/xml, text/xml",
                "User-Agent": "japan-stock-predictor/0.1",
            },
        )

    def close(self) -> None:
        """Release the internally owned HTTP client."""

        if self._owns_client:
            self._client.close()

    def __enter__(self) -> TreasuryProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request_year_xml(self, year: int) -> bytes:
        last_reason = "upstream failure"
        for attempt in range(self._max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._client.get(
                    TREASURY_XML_PATH,
                    params={
                        "data": TREASURY_DATASET,
                        "field_tdr_date_value": str(year),
                    },
                )
            except (httpx.TimeoutException, httpx.TransportError):
                retryable = True
                last_reason = "network or timeout error"
            else:
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable:
                    if response.status_code >= 400:
                        raise ProviderFetchError(
                            "U.S. Treasury request failed with "
                            f"HTTP {response.status_code}"
                        )
                    return bytes(response.content)
                last_reason = f"retryable HTTP {response.status_code}"

            if attempt >= self._max_retries:
                break
            retry_after = 0.0
            if response is not None:
                try:
                    retry_after = min(
                        60.0,
                        max(0.0, float(response.headers.get("Retry-After", "0"))),
                    )
                except ValueError:
                    retry_after = 0.0
            delay = max(retry_after, self._backoff_seconds * (2**attempt))
            self._sleeper(delay)

        raise ProviderFetchError(f"U.S. Treasury request failed: {last_reason}")

    def fetch_treasury_yield_bars(
        self,
        year: int,
        *,
        tenor_symbols: Mapping[str, str] | None = None,
    ) -> list[MarketBar]:
        """Fetch one calendar year's named 2Y/10Y/30Y observations.

        Unknown XML columns and absent requested tenors are allowed.  Values are
        selected by XML property name, so property or entry ordering is not
        meaningful.
        """

        if year < 1900 or year > 9999:
            raise ValueError("invalid Treasury year")
        configured_tenors = TREASURY_TENORS if tenor_symbols is None else tenor_symbols
        requested = {
            tenor.upper(): canonical for tenor, canonical in configured_tenors.items()
        }
        if any(not symbol.strip() for symbol in requested.values()):
            raise ValueError("Treasury canonical symbols must not be blank")
        if len(set(requested.values())) != len(requested):
            raise ValueError("Treasury canonical symbols must be unique")

        payload = self._request_year_xml(year)
        observed_at = _as_utc(self._clock(), "clock")
        return self._parse_xml(payload, year, requested, observed_at)

    def fetch_treasury_yield_bars_for_range(
        self,
        start_date: date,
        end_date: date,
        *,
        tenor_symbols: Mapping[str, str] | None = None,
    ) -> list[MarketBar]:
        """Fetch an inclusive date range, including ranges crossing a year."""

        if start_date > end_date:
            raise ValueError("start_date must not be after end_date")
        rows: list[MarketBar] = []
        for year in range(start_date.year, end_date.year + 1):
            rows.extend(
                self.fetch_treasury_yield_bars(
                    year,
                    tenor_symbols=tenor_symbols,
                )
            )
        return [row for row in rows if start_date <= row.market_date <= end_date]

    # Short, unsurprising alias for callers that are not bound to the protocol.
    fetch_range = fetch_treasury_yield_bars_for_range

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        """Implement the common EOD contract for one configured Treasury tenor."""

        matches = [
            tenor
            for tenor, canonical_symbol in TREASURY_TENORS.items()
            if canonical_symbol == request.canonical_symbol
        ]
        if len(matches) != 1:
            raise ValueError("unsupported Treasury canonical symbol")
        tenor = matches[0]
        expected_symbols = {
            tenor,
            f"BC_{tenor.removesuffix('Y')}YEAR",
            f"TREASURY:BC_{tenor.removesuffix('Y')}YEAR",
        }
        if request.provider_symbol.upper() not in expected_symbols:
            raise ValueError("Treasury provider symbol does not match canonical tenor")
        return self.fetch_treasury_yield_bars_for_range(
            request.start_date,
            request.end_date,
            tenor_symbols={tenor: request.canonical_symbol},
        )

    def healthcheck(self) -> ProviderHealth:
        """Check the public official feed without requiring an API key."""

        checked_at = _as_utc(self._clock(), "clock")
        try:
            payload = self._request_year_xml(checked_at.year)
            root = ET.fromstring(payload)
        except (ProviderFetchError, ProviderResponseError, ET.ParseError) as exc:
            return ProviderHealth(self.name, False, checked_at, str(exc))
        if _local_name(root.tag) != "feed":
            return ProviderHealth(
                self.name, False, checked_at, "official XML root is not an Atom feed"
            )
        return ProviderHealth(
            self.name, True, checked_at, "official Treasury XML feed is reachable"
        )

    def _parse_xml(
        self,
        payload: bytes,
        year: int,
        tenor_symbols: Mapping[str, str],
        observed_at: datetime,
    ) -> list[MarketBar]:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise ProviderResponseError("U.S. Treasury returned malformed XML") from exc
        if _local_name(root.tag) != "feed":
            raise ProviderResponseError("U.S. Treasury XML root is not an Atom feed")

        normalized: dict[tuple[date, str], MarketBar] = {}
        for entry in root.iter():
            if _local_name(entry.tag) != "entry":
                continue
            properties = next(
                (
                    candidate
                    for candidate in entry.iter()
                    if _local_name(candidate.tag) == "properties"
                ),
                None,
            )
            if properties is None:
                continue
            fields = {
                _local_name(child.tag): (child.text or "").strip()
                for child in properties
            }
            raw_date = fields.get("NEW_DATE", "")
            if not raw_date:
                raise ProviderResponseError(
                    "U.S. Treasury XML entry is missing NEW_DATE"
                )
            market_date = _parse_market_date(raw_date)
            if market_date.year != year:
                continue

            selected_rates = [
                (field, tenor, tenor_symbols[tenor], fields[field])
                for field, tenor in _TENOR_FIELDS.items()
                if tenor in tenor_symbols and fields.get(field, "")
            ]
            if not selected_rates:
                continue

            event_at = _market_timestamp(market_date, _MARKET_EVENT_TIME)
            scheduled_at = _market_timestamp(market_date, _PUBLISHED_SCHEDULE_TIME)
            if observed_at < event_at:
                raise ProviderResponseError(
                    "U.S. Treasury observation predates its market event"
                )
            if observed_at < scheduled_at:
                available_at = observed_at
                availability_method = AvailabilityMethod.FIRST_OBSERVED
                availability_flag = EARLY_FIRST_OBSERVED_FLAG
            else:
                available_at = scheduled_at
                availability_method = AvailabilityMethod.PUBLISHED_SCHEDULE
                availability_flag = PUBLISHED_SCHEDULE_ESTIMATE_FLAG

            for field, tenor, canonical_symbol, raw_rate in selected_rates:
                rate = _parse_rate(raw_rate, field)
                bar = MarketBar(
                    canonical_symbol=canonical_symbol,
                    provider_symbol=f"TREASURY:{field}",
                    provider=self.name,
                    market=TREASURY_MARKET,
                    market_timezone=TREASURY_MARKET_TIMEZONE,
                    market_date=market_date,
                    timestamp=event_at,
                    source_timestamp=None,
                    available_timestamp=available_at,
                    first_observed_at=observed_at,
                    retrieved_at=observed_at,
                    interval=DataInterval.EOD,
                    availability_method=availability_method,
                    data_quality=DataQuality.OFFICIAL,
                    is_realtime=False,
                    is_delayed=False,
                    close=rate,
                    currency="PERCENT",
                    raw_hash=_raw_hash(market_date, field, rate),
                    quality_flags=(
                        "official_us_treasury",
                        availability_flag,
                    ),
                )
                key = (market_date, tenor)
                previous = normalized.get(key)
                if previous is not None and previous.close != rate:
                    raise ProviderResponseError(
                        "U.S. Treasury XML contains conflicting duplicate rates"
                    )
                normalized[key] = bar

        symbol_order = {
            symbol: _TENOR_ORDER.get(tenor, len(_TENOR_ORDER))
            for tenor, symbol in tenor_symbols.items()
        }
        return sorted(
            normalized.values(),
            key=lambda row: (
                row.market_date,
                symbol_order.get(row.canonical_symbol, len(_TENOR_ORDER)),
                row.canonical_symbol,
            ),
        )
