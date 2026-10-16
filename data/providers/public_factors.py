"""Public MOF JGB yields and FRED corporate option-adjusted spreads.

MOF publishes the previous Japanese business day's 15:00 yields at 09:30 JST.
FRED's keyless CSV has observation dates, not original release times or vintages.
Its newest observation is therefore available only when fetched. Older values
use an explicitly estimated, conservative two-US-federal-business-day lag, at
00:00 Central. That estimate is not evidence of a historical vintage: every
historical FRED row is marked accordingly. Neither source is backdated to close.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx
from pandas.tseries.holiday import USFederalHolidayCalendar

from data.market_calendar import MarketCalendarError, is_japan_business_day
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

MOF_HISTORY_URL = (
    "https://www.mof.go.jp/jgbs/reference/interest_rate/data/jgbcm_all.csv"
)
MOF_CURRENT_URL = "https://www.mof.go.jp/jgbs/reference/interest_rate/jgbcm.csv"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
JGB_SYMBOLS = {
    "jgb_2y_yield": "MOF:2Y",
    "jgb_10y_yield": "MOF:10Y",
    "jgb_20y_yield": "MOF:20Y",
}
FRED_SYMBOLS = {
    "us_ig_credit_oas": "BAMLC0A0CM",
    "us_hy_credit_oas": "BAMLH0A0HYM2",
}
_JAPAN = ZoneInfo("Asia/Tokyo")
_CENTRAL = ZoneInfo("America/Chicago")
_ERA_DATES = {
    "S": (1925, date(1926, 12, 25), date(1989, 1, 7)),
    "H": (1988, date(1989, 1, 8), date(2019, 4, 30)),
    "R": (2018, date(2019, 5, 1), date.max),
}
_MISSING_VALUES = frozenset({"", "-", "--", ".", "NA", "N/A"})


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must be timezone-aware")
    return value.astimezone(UTC)


def _rate(value: str) -> Decimal | None:
    value = value.strip()
    if value in _MISSING_VALUES:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ProviderResponseError("public factor CSV has an invalid value") from exc
    if not result.is_finite():
        raise ProviderResponseError("public factor CSV has a non-finite value")
    return result


def _mof_date(value: str) -> date:
    value = value.strip()
    match = re.fullmatch(r"([SHR])(\d+)\.(\d+)\.(\d+)", value)
    try:
        if match:
            offset, minimum, maximum = _ERA_DATES[match[1]]
            year = int(match[2])
            parsed = date(offset + year, int(match[3]), int(match[4]))
            if year < 1 or not minimum <= parsed <= maximum:
                raise ValueError("date outside named Japanese era")
            return parsed
        return date.fromisoformat(value.replace("/", "-"))
    except ValueError as exc:
        raise ProviderResponseError("MOF CSV has an invalid observation date") from exc


def _decode_mof(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ProviderResponseError("MOF CSV has an unsupported text encoding")


def parse_mof_csv(payload: bytes, tenor: str) -> dict[date, Decimal]:
    """Parse by the named tenor column, including the official era dates."""

    if tenor not in {"2Y", "10Y", "20Y"}:
        raise ValueError("unsupported MOF tenor")
    column = f"{tenor.removesuffix('Y')}年"
    rows = csv.reader(io.StringIO(_decode_mof(payload)))
    header: list[str] | None = None
    result: dict[date, Decimal] = {}
    for row in rows:
        if not row or not row[0].strip() or row[0].startswith("※"):
            continue
        if header is None:
            if row[0].strip() == "基準日":
                header = [item.strip() for item in row]
                if header.count(column) != 1:
                    raise ProviderResponseError("MOF CSV lacks a unique tenor column")
            continue
        index = header.index(column)
        if len(row) <= index:
            raise ProviderResponseError("MOF CSV has a truncated observation row")
        observation_date = _mof_date(row[0])
        value = _rate(row[index])
        if value is None:
            continue
        if observation_date in result and result[observation_date] != value:
            raise ProviderResponseError("MOF CSV has conflicting duplicate values")
        result[observation_date] = value
    if header is None:
        raise ProviderResponseError("MOF response is missing the CSV header")
    return result


def parse_fred_csv(payload: bytes, symbol: str) -> dict[date, Decimal]:
    """Parse FRED's named series; missing holiday observations remain missing."""

    if symbol not in FRED_SYMBOLS.values():
        raise ValueError("unsupported FRED series")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProviderResponseError("FRED CSV is not UTF-8") from exc
    rows = csv.DictReader(io.StringIO(text))
    columns = rows.fieldnames or []
    date_column = "observation_date" if "observation_date" in columns else "DATE"
    if columns.count(date_column) != 1 or columns.count(symbol) != 1:
        raise ProviderResponseError("FRED response is missing the requested CSV series")
    result: dict[date, Decimal] = {}
    for row in rows:
        raw_date, raw_value = row.get(date_column), row.get(symbol)
        if raw_date is None or raw_value is None:
            raise ProviderResponseError("FRED CSV has a truncated observation row")
        try:
            observation_date = date.fromisoformat(raw_date.strip())
        except ValueError as exc:
            raise ProviderResponseError(
                "FRED CSV has an invalid observation date"
            ) from exc
        value = _rate(raw_value)
        if value is None:
            continue
        if observation_date in result and result[observation_date] != value:
            raise ProviderResponseError("FRED CSV has conflicting duplicate values")
        result[observation_date] = value
    return result


def mof_available_at(observation_date: date) -> datetime:
    """The documented next Japanese business day at 09:30 JST, never t+0."""

    publication_date = observation_date + timedelta(days=1)
    for _ in range(15):
        if is_japan_business_day(publication_date):
            return datetime.combine(
                publication_date, wall_time(9, 30), _JAPAN
            ).astimezone(UTC)
        publication_date += timedelta(days=1)
    raise ProviderResponseError("unable to determine MOF next publication day")


@lru_cache(maxsize=16)
def _us_federal_holidays(year: int) -> frozenset[date]:
    holidays = USFederalHolidayCalendar().holidays(
        start=f"{year}-01-01", end=f"{year}-12-31"
    )
    return frozenset(value.date() for value in holidays)


def fred_estimated_available_at(observation_date: date) -> datetime:
    """Conservative fallback estimate, explicitly not a FRED publication SLA.

    Allow a whole following US federal business day for the usual next-day
    publication, then date availability at the start of the second such day.
    A weekend month-end observation is handled identically. DST follows Central
    time. Actual historical revisions/publication delays cannot be inferred.
    """

    publication_date = observation_date
    remaining = 2
    while remaining:
        publication_date += timedelta(days=1)
        if (
            publication_date.weekday() < 5
            and publication_date not in _us_federal_holidays(publication_date.year)
        ):
            remaining -= 1
    return datetime.combine(publication_date, wall_time.min, _CENTRAL).astimezone(UTC)


def latest_expected_observation(cutoff: datetime, symbol: str) -> date:
    """Return a conservative freshness baseline at an aware prediction cutoff.

    Accept a canonical or provider symbol. MOF uses the documented JP calendar;
    FRED uses the same conservative estimated lag as historical normalization.
    The FRED date is not a publication guarantee. Federal-holiday observations
    may exist, but are not required; weekend month-end adjustments may exist and
    are likewise not required by this freshness baseline.
    """

    cutoff_utc = _aware_utc(cutoff)
    if symbol in JGB_SYMBOLS or symbol in JGB_SYMBOLS.values():
        day = cutoff.astimezone(_JAPAN).date()
        for _ in range(30):
            if is_japan_business_day(day) and mof_available_at(day) <= cutoff_utc:
                return day
            day -= timedelta(days=1)
    elif symbol in FRED_SYMBOLS or symbol in FRED_SYMBOLS.values():
        day = cutoff.astimezone(_CENTRAL).date()
        for _ in range(30):
            if (
                day.weekday() < 5
                and day not in _us_federal_holidays(day.year)
                and fred_estimated_available_at(day) <= cutoff_utc
            ):
                return day
            day -= timedelta(days=1)
    else:
        raise ValueError("unsupported public-factor symbol")
    raise ProviderResponseError("unable to determine expected public observation date")


class PublicFactorsProvider(MarketDataProvider):
    """Keyless public-series client; external HTTP clients remain caller-owned."""

    name = "public_factors"

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        client: httpx.Client | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or backoff_seconds < 0:
            raise ValueError("invalid public-factor HTTP retry configuration")
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "Accept": "text/csv, application/csv, text/plain",
                # Keep httpx's truthful default User-Agent. Live FRED probes
                # timed out with the custom application User-Agent while the
                # identical request with the library default returned promptly.
                "Cache-Control": "no-cache",
            },
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _get(self, url: str, *, params: dict[str, str] | None = None) -> bytes:
        reason = "upstream failure"
        for attempt in range(self._max_retries + 1):
            response: httpx.Response | None = None
            try:
                response = self._client.get(url, params=params)
            except (httpx.TimeoutException, httpx.TransportError):
                reason = "network or timeout error"
            else:
                if response.status_code < 400:
                    return bytes(response.content)
                if response.status_code != 429 and response.status_code < 500:
                    raise ProviderFetchError(
                        f"public-factor request failed with HTTP {response.status_code}"
                    )
                reason = f"retryable HTTP {response.status_code}"
            if attempt == self._max_retries:
                break
            retry_after = 0.0
            if response is not None:
                with suppress(ValueError):
                    retry_after = min(
                        60.0, max(0.0, float(response.headers.get("Retry-After", "0")))
                    )
            self._sleeper(max(retry_after, self._backoff_seconds * 2**attempt))
        raise ProviderFetchError(f"public-factor request failed: {reason}")

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        """Fetch an inclusive range with source-specific publication rules.

        ``market_close`` and ``availability_lag_minutes`` intentionally cannot
        override these rules. The current-vintage history is suitable only for
        explicitly labelled estimated-PIT training/research, not proof of the
        exact values actually published in the past.
        """

        if request.canonical_symbol in JGB_SYMBOLS:
            expected = JGB_SYMBOLS[request.canonical_symbol]
            if request.provider_symbol != expected:
                raise ValueError("MOF provider symbol does not match canonical tenor")
            tenor = expected.removeprefix("MOF:")
            # The archive ends at the previous month; the small file carries
            # the current month. Reading only either silently loses observations.
            history = parse_mof_csv(self._get(MOF_HISTORY_URL), tenor)
            current = parse_mof_csv(self._get(MOF_CURRENT_URL), tenor)
            history.update(current)
            observed_at = _aware_utc(self._clock())
            return self._mof_bars(request, history, observed_at)
        if request.canonical_symbol in FRED_SYMBOLS:
            expected = FRED_SYMBOLS[request.canonical_symbol]
            if request.provider_symbol != expected:
                raise ValueError("FRED provider symbol does not match canonical series")
            payload = self._get(
                FRED_CSV_URL,
                # The stable series-only URL returns the public rolling history.
                # This request was verified with the standard httpx headers.
                # Filter locally and identify the actual newest observation
                # before filtering, independently of the requested date window.
                params={"id": expected},
            )
            values = parse_fred_csv(payload, expected)
            observed_at = _aware_utc(self._clock())
            return self._fred_bars(request, values, observed_at)
        raise ValueError("unsupported public-factor canonical symbol")

    def _mof_bars(
        self, request: FetchRequest, values: dict[date, Decimal], observed_at: datetime
    ) -> list[MarketBar]:
        bars = []
        for day, value in sorted(values.items()):
            if not request.start_date <= day <= request.end_date:
                continue
            try:
                available = mof_available_at(day)
            except MarketCalendarError as exc:
                raise ProviderResponseError(
                    "MOF publication calendar unavailable"
                ) from exc
            # Do not claim a pre-09:30 HTTP response proves the final daily
            # publication; the public documented schedule remains the lower bound.
            if available > observed_at:
                continue
            event = datetime.combine(day, wall_time(15), _JAPAN).astimezone(UTC)
            bars.append(
                self._bar(
                    request,
                    day,
                    value,
                    event,
                    available,
                    observed_at,
                    market="JP_JGB",
                    timezone="Asia/Tokyo",
                    method=AvailabilityMethod.PUBLISHED_SCHEDULE,
                    flags=(
                        "official_mof_jgb",
                        "next_japan_business_day_0930_jst",
                        "historical_vintage_unverified",
                        "current_vintage_backfill",
                    ),
                )
            )
        return bars

    def _fred_bars(
        self, request: FetchRequest, values: dict[date, Decimal], observed_at: datetime
    ) -> list[MarketBar]:
        newest = max(values, default=None)
        bars = []
        for day, value in sorted(values.items()):
            if not request.start_date <= day <= request.end_date:
                continue
            # FRED supplies a date only. This midnight anchor is not a claimed
            # market-close timestamp; availability is independently delayed.
            event = datetime.combine(day, wall_time.min, _CENTRAL).astimezone(UTC)
            if event > observed_at:
                raise ProviderResponseError("FRED observation is dated in the future")
            estimated = fred_estimated_available_at(day)
            actual_observation = day == newest or observed_at < estimated
            available = observed_at if actual_observation else estimated
            bars.append(
                self._bar(
                    request,
                    day,
                    value,
                    event,
                    available,
                    observed_at,
                    market="US_CREDIT",
                    timezone="America/Chicago",
                    method=(
                        AvailabilityMethod.FIRST_OBSERVED
                        if actual_observation
                        else AvailabilityMethod.PROVIDER_SLA_ESTIMATE
                    ),
                    flags=(
                        "fred_ice_bofa",
                        "observation_date_only_event_time",
                        "actual_first_observation"
                        if actual_observation
                        else "estimated_two_us_federal_business_day_release_lag",
                        "historical_vintage_unverified",
                        "current_vintage_backfill",
                        "ice_internal_use_only",
                    ),
                )
            )
        return bars

    def _bar(
        self,
        request: FetchRequest,
        day: date,
        value: Decimal,
        event: datetime,
        available: datetime,
        observed_at: datetime,
        *,
        market: str,
        timezone: str,
        method: AvailabilityMethod,
        flags: tuple[str, ...],
    ) -> MarketBar:
        digest = hashlib.sha256(
            f"{request.provider_symbol}|{day.isoformat()}|{value}".encode()
        ).hexdigest()
        return MarketBar(
            canonical_symbol=request.canonical_symbol,
            provider_symbol=request.provider_symbol,
            provider=self.name,
            market=market,
            market_timezone=timezone,
            market_date=day,
            timestamp=event,
            source_timestamp=None,
            available_timestamp=available,
            first_observed_at=observed_at,
            retrieved_at=observed_at,
            interval=DataInterval.EOD,
            availability_method=method,
            data_quality=DataQuality.OFFICIAL,
            is_realtime=False,
            is_delayed=True,
            close=value,
            currency="PERCENT",
            raw_hash=digest,
            quality_flags=flags,
        )

    def healthcheck(self) -> ProviderHealth:
        checked_at = _aware_utc(self._clock())
        try:
            mof = parse_mof_csv(self._get(MOF_CURRENT_URL), "2Y")
            fred = parse_fred_csv(
                self._get(
                    FRED_CSV_URL,
                    params={"id": FRED_SYMBOLS["us_ig_credit_oas"]},
                ),
                FRED_SYMBOLS["us_ig_credit_oas"],
            )
            if not mof or not fred:
                raise ProviderResponseError("public CSV contains no observations")
        except (ProviderFetchError, ProviderResponseError) as exc:
            return ProviderHealth(self.name, False, checked_at, str(exc))
        return ProviderHealth(
            self.name, True, checked_at, "MOF and FRED public CSV feeds are reachable"
        )
