from datetime import UTC, date, datetime

from data.alignment import latest_available
from data.availability import eod_availability, prediction_cutoff
from data.schemas import AvailabilityMethod

UTC = UTC


def test_new_york_dst_is_applied_to_market_close() -> None:
    observed = datetime(2026, 3, 10, 12, tzinfo=UTC)
    before, _, _ = eod_availability(
        date(2026, 3, 6),
        market_timezone="America/New_York",
        market_close="16:00",
        provider_lag_minutes=15,
        first_observed_at=observed,
    )
    after, _, _ = eod_availability(
        date(2026, 3, 9),
        market_timezone="America/New_York",
        market_close="16:00",
        provider_lag_minutes=15,
        first_observed_at=observed,
    )
    assert before.hour == 21
    assert after.hour == 20


def test_first_observation_wins_when_provider_is_earlier_than_sla() -> None:
    observed = datetime(2026, 8, 7, 20, 10, tzinfo=UTC)
    _, available, method = eod_availability(
        date(2026, 8, 7),
        market_timezone="America/New_York",
        market_close="16:00",
        provider_lag_minutes=15,
        first_observed_at=observed,
    )
    assert available == observed
    assert method is AvailabilityMethod.FIRST_OBSERVED


def test_alignment_uses_availability_not_matching_date(make_bar) -> None:
    cutoff = prediction_cutoff(date(2026, 8, 10))
    usable = make_bar(
        canonical_symbol="CHINA",
        market_date=date(2026, 8, 7),
        timestamp=datetime(2026, 8, 7, 7, tzinfo=UTC),
        available_timestamp=datetime(2026, 8, 7, 10, tzinfo=UTC),
    )
    same_date_but_future = make_bar(
        canonical_symbol="CHINA",
        market_date=date(2026, 8, 10),
        timestamp=datetime(2026, 8, 10, 7, tzinfo=UTC),
        available_timestamp=datetime(2026, 8, 10, 10, tzinfo=UTC),
        first_observed_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
        retrieved_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
        raw_hash="b" * 64,
    )
    result = latest_available([same_date_but_future, usable], cutoff)
    assert result["CHINA"].value.market_date == date(2026, 8, 7)
    assert result["CHINA"].value.available_timestamp <= cutoff


def test_exact_cutoff_is_allowed(make_bar) -> None:
    cutoff = prediction_cutoff(date(2026, 8, 10))
    exact = make_bar(
        available_timestamp=cutoff,
        first_observed_at=cutoff,
        retrieved_at=cutoff,
    )
    assert latest_available([exact], cutoff)["SPY"].value is exact


def test_one_microsecond_after_cutoff_is_rejected(make_bar) -> None:
    cutoff = prediction_cutoff(date(2026, 8, 10))
    future = make_bar(
        available_timestamp=cutoff.replace(microsecond=1),
        first_observed_at=cutoff.replace(microsecond=1),
        retrieved_at=cutoff.replace(microsecond=1),
    )
    assert latest_available([future], cutoff) == {}


def test_operational_alignment_rejects_late_first_observation(make_bar) -> None:
    cutoff = prediction_cutoff(date(2026, 8, 10))
    backdated = make_bar(
        available_timestamp=cutoff.replace(microsecond=0),
        first_observed_at=cutoff.replace(microsecond=1),
        retrieved_at=cutoff.replace(microsecond=1),
    )
    assert latest_available([backdated], cutoff)
    assert (
        latest_available(
            [backdated], cutoff, require_observed_by_cutoff=True
        )
        == {}
    )
