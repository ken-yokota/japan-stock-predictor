from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.availability import prediction_cutoff
from data.config import load_app_config
from data.market_calendar import japan_session_close, japan_sessions_before
from database.models import Base, MarketData, StockPrice
from services.dataset import PointInTimeDatasetBuilder


def _stock_row(session_date: date, index: int) -> StockPrice:
    event_at = japan_session_close(session_date)
    available_at = event_at + timedelta(minutes=20)
    opening = Decimal(1000 + index)
    closing = opening * (Decimal("1.01") if index % 2 else Decimal("0.995"))
    return StockPrice(
        canonical_symbol="1605",
        symbol="1605.T",
        provider="yahoo_finance",
        market="JP",
        market_timezone="Asia/Tokyo",
        market_date=session_date,
        timestamp=event_at,
        source_timestamp=event_at,
        available_timestamp=available_at,
        first_observed_at=available_at,
        retrieved_at=available_at,
        last_seen_at=available_at,
        interval="eod",
        availability_method="provider_sla_estimate",
        data_quality="FREE_UNVERIFIED",
        is_realtime=False,
        is_delayed=True,
        open=opening,
        high=max(opening, closing) * Decimal("1.005"),
        low=min(opening, closing) * Decimal("0.995"),
        close=closing,
        adjusted_close=closing,
        volume=1_000_000,
        currency="JPY",
        raw_hash=f"{index + 1:064x}",
        quality_flags=[],
    )


def _indicator_row(session_date: date, index: int) -> MarketData:
    event_date = session_date - timedelta(days=1)
    event_at = datetime.combine(event_date, time(20), UTC)
    available_at = event_at + timedelta(minutes=15)
    close = Decimal(4000 + index * 2)
    return MarketData(
        canonical_symbol="sp500",
        symbol="^GSPC",
        provider="yahoo_finance",
        market="US_INDEX",
        market_timezone="America/New_York",
        market_date=event_date,
        timestamp=event_at,
        source_timestamp=event_at,
        available_timestamp=available_at,
        first_observed_at=available_at,
        retrieved_at=available_at,
        last_seen_at=available_at,
        interval="eod",
        availability_method="provider_sla_estimate",
        data_quality="FREE_UNVERIFIED",
        is_realtime=False,
        is_delayed=True,
        open=close - Decimal(5),
        high=close + Decimal(10),
        low=close - Decimal(10),
        close=close,
        adjusted_close=close,
        volume=10_000_000,
        currency="USD",
        raw_hash=f"{index + 10_000:064x}",
        quality_flags=[],
    )


def test_dataset_uses_120_prior_sessions_and_excludes_future_revision() -> None:
    prediction_date = date(2026, 8, 10)
    sessions = japan_sessions_before(prediction_date, 145)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index, session_date in enumerate(sessions):
            session.add(_stock_row(session_date, index))
            session.add(_indicator_row(session_date, index))
        session.commit()

        config = load_app_config()
        before = PointInTimeDatasetBuilder(session, config).build(
            "1605", prediction_date
        )
        assert len(before.training_frame) == 120
        assert "stock__return_1d" in before.feature_names
        assert "sp500__return_1d" in before.feature_names
        assert before.current_sample.target_return is None
        for references in before.current_sample.lineage.values():
            for reference in references:
                assert reference.available_at <= before.current_sample.cutoff_at

        cutoff = prediction_cutoff(prediction_date).astimezone(UTC)
        late = _indicator_row(prediction_date + timedelta(days=1), 999)
        late.timestamp = cutoff + timedelta(microseconds=1)
        late.source_timestamp = late.timestamp
        late.available_timestamp = late.timestamp
        late.first_observed_at = late.timestamp
        late.retrieved_at = late.timestamp
        late.last_seen_at = late.timestamp
        session.add(late)
        session.commit()

        after = PointInTimeDatasetBuilder(session, config).build(
            "1605", prediction_date
        )
        assert after.current_frame.equals(before.current_frame)
