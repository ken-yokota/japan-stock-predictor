"""A late job must not see more than a punctual one.

On 2026-08-12 the morning prediction was scheduled for 08:10/08:20/08:30 JST
and GitHub started it at 08:55. Nothing went wrong - the cutoff held - but
nothing tested that it would. A run that quietly widened its own window with
the delay would produce look-ahead that no later audit could distinguish from
skill, because the published prediction would still carry an 08:30 cutoff.

These pin the three timestamps the visibility gate is built on:

    available_timestamp  - when the market made the value knowable
    first_observed_at    - when this system first saw that value
    retrieved_at         - when this particular row was fetched

An operational run requires all three to be at or before the cutoff. The last
two are the liveness claim: they are what stops a value that existed at 08:00
but was only fetched at 08:55 from posing as information held at 08:30.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from data.availability import prediction_cutoff
from data.provider_router import EodRouteCandidate, ProviderRouter
from data.providers.base import MarketDataProvider, ProviderFetchError
from data.schemas import (
    FetchRequest,
    MarketBar,
    ProviderHealth,
    SnapshotRequest,
)

JST = ZoneInfo("Asia/Tokyo")
PREDICTION_DATE = date(2026, 8, 12)
CUTOFF = prediction_cutoff(PREDICTION_DATE)

# What actually happened: three scheduled attempts, and a start 25 minutes
# after the last of them.
SCHEDULED_STARTS = (
    datetime(2026, 8, 12, 8, 10, tzinfo=JST),
    datetime(2026, 8, 12, 8, 20, tzinfo=JST),
    datetime(2026, 8, 12, 8, 30, tzinfo=JST),
)
DELAYED_START = datetime(2026, 8, 12, 8, 55, tzinfo=JST)

SESSION = date(2026, 8, 11)


class _Stub(MarketDataProvider):
    def __init__(
        self, name: str, *, rows: list[MarketBar] | None = None, error: bool = False
    ) -> None:
        self.name = name
        self.rows = rows or []
        self.error = error
        self.eod_calls = 0

    def fetch_eod(self, request: FetchRequest) -> list[MarketBar]:
        del request
        self.eod_calls += 1
        if self.error:
            raise ProviderFetchError("temporary provider failure")
        return self.rows

    def fetch_snapshot(self, request: SnapshotRequest) -> MarketBar:
        del request
        raise ProviderFetchError("not used here")

    def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(self.name, True, CUTOFF, "ok")

    def close(self) -> None:
        return None


def _request(symbol: str = "JPY=X") -> FetchRequest:
    return FetchRequest(
        canonical_symbol="usdjpy",
        provider_symbol=symbol,
        market="US",
        market_timezone="America/New_York",
        market_close="16:00",
        availability_lag_minutes=30,
        start_date=SESSION,
        end_date=SESSION,
    )


def _select(
    providers: dict[str, MarketDataProvider],
    candidates: list[EodRouteCandidate],
    *,
    operational_run: bool = True,
):
    return ProviderRouter(providers).fetch_eod_series(
        candidates,
        required_dates={SESSION},
        cutoff_at=CUTOFF,
        operational_run=operational_run,
    )


# --- Test 1 ---------------------------------------------------------------


def test_a_late_start_does_not_move_the_cutoff(make_bar) -> None:
    """The cutoff comes from the prediction date, never from the wall clock."""

    del make_bar
    assert CUTOFF == datetime(2026, 8, 12, 8, 30, tzinfo=JST)
    # Every scheduled attempt and the delayed one resolve to the same instant.
    for started_at in (*SCHEDULED_STARTS, DELAYED_START):
        assert prediction_cutoff(started_at.date()) == CUTOFF
    assert DELAYED_START > CUTOFF, "the delayed start must be after the cutoff"


# --- Test 2 ---------------------------------------------------------------


def test_a_value_that_became_available_after_the_cutoff_is_not_used(make_bar) -> None:
    """08:31 data, fetched at 08:55, must be invisible to an 08:30 cutoff."""

    row = make_bar(
        canonical_symbol="usdjpy",
        provider="yahoo_finance",
        provider_symbol="JPY=X",
        market_date=SESSION,
        timestamp=CUTOFF + timedelta(minutes=1),
        available_timestamp=CUTOFF + timedelta(minutes=1),
        first_observed_at=CUTOFF + timedelta(minutes=1),
        retrieved_at=DELAYED_START,
    )
    provider = _Stub("yahoo_finance", rows=[row])
    result = _select(
        {"yahoo_finance": provider}, [EodRouteCandidate("yahoo_finance", _request())]
    )
    assert result.rows == ()
    assert result.selected_provider is None


# --- Test 3 ---------------------------------------------------------------


def test_an_old_value_first_seen_after_the_cutoff_is_not_backdated(make_bar) -> None:
    """Fetching a 08:00 value at 08:55 does not make it information held at 08:30."""

    row = make_bar(
        canonical_symbol="usdjpy",
        provider="yahoo_finance",
        provider_symbol="JPY=X",
        market_date=SESSION,
        timestamp=CUTOFF - timedelta(minutes=30),
        available_timestamp=CUTOFF - timedelta(minutes=30),
        first_observed_at=DELAYED_START,
        retrieved_at=DELAYED_START,
    )
    provider = _Stub("yahoo_finance", rows=[row])
    result = _select(
        {"yahoo_finance": provider}, [EodRouteCandidate("yahoo_finance", _request())]
    )
    assert result.rows == ()


# --- Test 4 ---------------------------------------------------------------


def test_retrieved_after_the_cutoff_is_excluded_on_an_operational_run(make_bar) -> None:
    """The liveness claim: an operational run may only use rows it already had.

    The same row is admissible on a replay, where the system does not claim to
    have held the data at the time - that asymmetry is the documented contract,
    so both directions are pinned here.
    """

    row = make_bar(
        canonical_symbol="usdjpy",
        provider="yahoo_finance",
        provider_symbol="JPY=X",
        market_date=SESSION,
        timestamp=CUTOFF - timedelta(hours=2),
        available_timestamp=CUTOFF - timedelta(hours=2),
        first_observed_at=CUTOFF - timedelta(hours=2),
        retrieved_at=DELAYED_START,
    )
    candidates = [EodRouteCandidate("yahoo_finance", _request())]

    operational = _select(
        {"yahoo_finance": _Stub("yahoo_finance", rows=[row])}, candidates
    )
    assert operational.rows == (), "an operational run must not use a late fetch"

    replay = _select(
        {"yahoo_finance": _Stub("yahoo_finance", rows=[row])},
        candidates,
        operational_run=False,
    )
    assert replay.rows != (), "a replay may reconstruct from the same row"


# --- Test 5 ---------------------------------------------------------------


def test_a_fallback_provider_is_held_to_the_same_three_conditions(make_bar) -> None:
    """Falling back is not a way around the cutoff."""

    late_row = make_bar(
        canonical_symbol="usdjpy",
        provider="eodhd",
        provider_symbol="JPY.FOREX",
        market_date=SESSION,
        timestamp=CUTOFF + timedelta(minutes=1),
        available_timestamp=CUTOFF + timedelta(minutes=1),
        first_observed_at=CUTOFF + timedelta(minutes=1),
        retrieved_at=DELAYED_START,
        raw_hash="b" * 64,
    )
    primary = _Stub("yahoo_finance", error=True)
    fallback = _Stub("eodhd", rows=[late_row])
    candidates = [
        EodRouteCandidate("yahoo_finance", _request()),
        EodRouteCandidate("eodhd_free", _request("JPY.FOREX")),
    ]

    result = _select({"yahoo_finance": primary, "eodhd_free": fallback}, candidates)
    assert fallback.eod_calls == 1, "the fallback must have been consulted"
    assert result.rows == (), "the fallback's late rows are rejected too"

    # And the same fallback, with rows it genuinely held by the cutoff, is used.
    in_time = make_bar(
        canonical_symbol="usdjpy",
        provider="eodhd",
        provider_symbol="JPY.FOREX",
        market_date=SESSION,
        timestamp=CUTOFF - timedelta(hours=3),
        available_timestamp=CUTOFF - timedelta(hours=3),
        first_observed_at=CUTOFF - timedelta(hours=3),
        retrieved_at=CUTOFF - timedelta(hours=3),
        raw_hash="c" * 64,
    )
    healthy = _select(
        {
            "yahoo_finance": _Stub("yahoo_finance", error=True),
            "eodhd_free": _Stub("eodhd", rows=[in_time]),
        },
        candidates,
    )
    assert len(healthy.rows) == 1
    assert healthy.selected_provider == "eodhd"
