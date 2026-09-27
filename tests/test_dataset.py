from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.availability import prediction_cutoff
from data.config import load_app_config
from data.market_calendar import (
    japan_session_close,
    japan_sessions_before,
    latest_completed_indicator_session,
)
from database.models import Base, MarketData, StockPrice
from services.dataset import PointInTimeDatasetBuilder
from services.prediction import PredictionService


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
        canonical_symbol="sp500_futures",
        symbol="ES=F",
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


def _treasury_row(session_date: date, index: int) -> MarketData:
    event_at = datetime.combine(
        session_date, time(15, 30), ZoneInfo("America/New_York")
    )
    available_at = datetime.combine(
        session_date, time(18), ZoneInfo("America/New_York")
    )
    rate = Decimal("4.00") + Decimal(index) / 100
    return MarketData(
        canonical_symbol="us_10y_yield",
        symbol="TREASURY:US_10Y_YIELD",
        provider="us_treasury",
        market="US_TREASURY",
        market_timezone="America/New_York",
        market_date=session_date,
        timestamp=event_at,
        source_timestamp=event_at,
        available_timestamp=available_at,
        first_observed_at=available_at,
        retrieved_at=available_at,
        last_seen_at=available_at,
        interval="eod",
        availability_method="scheduled_publication_estimate",
        data_quality="OFFICIAL",
        is_realtime=False,
        is_delayed=True,
        open=rate,
        high=rate,
        low=rate,
        close=rate,
        adjusted_close=rate,
        volume=None,
        currency="PERCENT",
        raw_hash=f"{index + 20_000:064x}",
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
        # The futures series, not the cash index. sp500 was disabled on
        # 2026-09-08: its close stops at 05:00 JST, so it is strictly older
        # information about the same thing ES=F carries to the cutoff.
        assert "sp500_futures__return_1d" in before.feature_names
        assert not any(name.startswith("sp500__") for name in before.feature_names)
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


def test_live_prediction_refuses_stale_stock_close() -> None:
    prediction_date = date(2026, 8, 12)  # 8/11 is a JPX holiday; 8/10 is required.
    sessions = japan_sessions_before(prediction_date, 145)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index, session_date in enumerate(sessions):
            if session_date != sessions[-1]:
                session.add(_stock_row(session_date, index))
            session.add(_indicator_row(session_date, index))
        session.commit()

        config = load_app_config()
        service = PredictionService(PointInTimeDatasetBuilder(session, config), config)
        stale = service.compute("1605", prediction_date)
        assert stale.result.status == "INSUFFICIENT_DATA"
        assert (
            "previous JPX session stock close unavailable at cutoff"
            in stale.result.warnings
        )
        assert stale.dataset.current_sample.reference_source is not None
        assert stale.dataset.current_sample.reference_source.market_date == sessions[-2]

        session.add(_stock_row(sessions[-1], len(sessions) - 1))
        session.commit()
        fresh = PredictionService(
            PointInTimeDatasetBuilder(session, config), config
        ).compute("1605", prediction_date)
        assert (
            "previous JPX session stock close unavailable at cutoff"
            not in fresh.result.warnings
        )


def test_live_prediction_refuses_stale_required_eod_despite_old_features() -> None:
    prediction_date = date(2026, 8, 12)
    sessions = japan_sessions_before(prediction_date, 145)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index, session_date in enumerate(sessions):
            session.add(_stock_row(session_date, index))
            session.add(_indicator_row(session_date, index))
        session.commit()

        config = load_app_config()
        service = PredictionService(PointInTimeDatasetBuilder(session, config), config)
        stale = service.compute("1605", prediction_date)
        assert stale.dataset.current_sample.reference_source is not None
        assert stale.dataset.current_sample.reference_source.market_date == sessions[-1]
        assert "sp500_futures" in stale.dataset.observed_indicators
        assert "sp500_futures" not in stale.dataset.missing_required_indicators
        assert stale.result.status == "INSUFFICIENT_DATA"
        assert any(
            "required EOD indicators stale at cutoff: " in warning
            and "sp500_futures" in warning
            for warning in stale.result.warnings
        )

        # 8/11 is a JPX holiday but a completed US session by the 8/12
        # 08:30 JST cutoff. Restoring that daily bar clears this specific gate.
        restored = _indicator_row(prediction_date, 999)
        restored.timestamp = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
        restored.source_timestamp = restored.timestamp
        restored.available_timestamp = restored.timestamp + timedelta(hours=1)
        restored.first_observed_at = restored.available_timestamp
        restored.retrieved_at = restored.available_timestamp
        restored.last_seen_at = restored.available_timestamp
        session.add(restored)
        session.commit()
        fresh = PredictionService(
            PointInTimeDatasetBuilder(session, config), config
        ).compute("1605", prediction_date)
        assert not any(
            "required EOD indicators stale at cutoff:" in warning
            and "sp500_futures" in warning
            for warning in fresh.result.warnings
        )


def test_required_us_eod_skips_labor_day_at_tokyo_morning_cutoff() -> None:
    cutoff = datetime(2026, 9, 8, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert latest_completed_indicator_session(
        cutoff,
        market="FUTURES",
        market_timezone="America/New_York",
        market_close="17:00",
        availability_lag_minutes=60,
    ) == date(2026, 9, 4)


def test_required_treasury_skips_labor_day_at_tokyo_morning_cutoff() -> None:
    cutoff = datetime(2026, 9, 8, 8, 30, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert latest_completed_indicator_session(
        cutoff,
        market="US_TREASURY",
        market_timezone="America/New_York",
        market_close="18:00",
        availability_lag_minutes=0,
    ) == date(2026, 9, 4)


def test_live_prediction_refuses_stale_required_treasury_despite_old_level() -> None:
    prediction_date = date(2026, 8, 12)
    sessions = japan_sessions_before(prediction_date, 145)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        for index, session_date in enumerate(sessions):
            session.add(_stock_row(session_date, index))
            session.add(_indicator_row(session_date, index))
        current_futures = _indicator_row(prediction_date, 999)
        current_futures.timestamp = datetime(2026, 8, 11, 21, 0, tzinfo=UTC)
        current_futures.source_timestamp = current_futures.timestamp
        current_futures.available_timestamp = current_futures.timestamp + timedelta(
            hours=1
        )
        current_futures.first_observed_at = current_futures.available_timestamp
        current_futures.retrieved_at = current_futures.available_timestamp
        current_futures.last_seen_at = current_futures.available_timestamp
        session.add(current_futures)
        session.add(_treasury_row(date(2026, 8, 10), 1))
        session.commit()

        config = load_app_config()
        # Isolate this source's freshness gate from unrelated indicators that
        # are absent in the small synthetic dataset.
        config = config.model_copy(
            update={
                "indicators": config.indicators.model_copy(
                    update={
                        "indicators": [
                            item.model_copy(
                                update={"required": item.id == "us_10y_yield"}
                            )
                            for item in config.indicators.indicators
                        ]
                    }
                )
            }
        )
        service = PredictionService(PointInTimeDatasetBuilder(session, config), config)
        stale = service.compute("1605", prediction_date)
        assert "us_10y_yield" in stale.dataset.observed_indicators
        assert "us_10y_yield" not in stale.dataset.missing_required_indicators
        assert stale.result.status == "INSUFFICIENT_DATA"
        assert any(
            "required Treasury indicators stale at cutoff:" in warning
            and "us_10y_yield" in warning
            for warning in stale.result.warnings
        ), stale.result.warnings

        session.add(_treasury_row(date(2026, 8, 11), 2))
        session.commit()
        fresh = PredictionService(
            PointInTimeDatasetBuilder(session, config), config
        ).compute("1605", prediction_date)
        assert not any(
            "required Treasury indicators stale at cutoff:" in warning
            and "us_10y_yield" in warning
            for warning in fresh.result.warnings
        )
