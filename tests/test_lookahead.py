from datetime import UTC, date, datetime, timedelta

from data.alignment import assert_no_lookahead, latest_available
from data.availability import prediction_cutoff


def test_every_selected_feature_is_available_by_prediction_cutoff(make_bar) -> None:
    cutoff = prediction_cutoff(date(2026, 8, 10))
    rows = [
        make_bar(canonical_symbol="SPY"),
        make_bar(
            canonical_symbol="USDJPY",
            timestamp=cutoff - timedelta(minutes=2),
            available_timestamp=cutoff - timedelta(minutes=1),
            first_observed_at=cutoff - timedelta(minutes=1),
            retrieved_at=cutoff - timedelta(minutes=1),
            raw_hash="b" * 64,
        ),
        make_bar(
            canonical_symbol="CHINA",
            market_date=date(2026, 8, 10),
            timestamp=datetime(2026, 8, 10, 7, tzinfo=UTC),
            available_timestamp=cutoff + timedelta(hours=8),
            first_observed_at=cutoff + timedelta(hours=8),
            retrieved_at=cutoff + timedelta(hours=8),
            raw_hash="c" * 64,
        ),
    ]
    aligned = latest_available(rows, cutoff)
    assert set(aligned) == {"SPY", "USDJPY"}
    assert_no_lookahead(aligned.values(), cutoff)


def test_old_provider_event_observed_late_cannot_be_backdated(make_bar) -> None:
    cutoff = prediction_cutoff(date(2026, 8, 10))
    late_observation = make_bar(
        canonical_symbol="USDJPY",
        timestamp=cutoff - timedelta(minutes=5),
        available_timestamp=cutoff + timedelta(minutes=1),
        first_observed_at=cutoff + timedelta(minutes=1),
        retrieved_at=cutoff + timedelta(minutes=1),
        raw_hash="d" * 64,
    )
    assert latest_available([late_observation], cutoff) == {}
