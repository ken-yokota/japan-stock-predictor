from datetime import UTC, date, datetime

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from data.snapshot import FreshnessStatus, SelectionRole
from database.models import (
    Base,
    IngestionBatch,
    MarketData,
    ProviderSelection,
    StockPrice,
)
from database.repository import MarketDataRepository


def test_market_data_upsert_is_idempotent(make_bar) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = MarketDataRepository(session)
        first = repository.upsert_bars([make_bar()])
        second = repository.upsert_bars([make_bar()])
        session.commit()
        assert first.inserted == 1
        assert second.reused == 1
        assert session.scalar(select(func.count()).select_from(MarketData)) == 1


def test_target_stock_is_routed_to_stock_prices(make_bar) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        MarketDataRepository(session).upsert_bars(
            [make_bar(canonical_symbol="9101")], stock_symbols={"9101"}
        )
        session.commit()
        assert session.scalar(select(func.count()).select_from(StockPrice)) == 1
        assert session.scalar(select(func.count()).select_from(MarketData)) == 0


def test_rerun_preserves_initial_retrieval_and_only_updates_last_seen(make_bar) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    later = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    with Session(engine) as session:
        repository = MarketDataRepository(session)
        repository.upsert_bars([make_bar()])
        repository.upsert_bars([make_bar(retrieved_at=later)])
        session.commit()
        row = session.scalar(select(MarketData))
        assert row is not None
        assert row.retrieved_at == datetime(2026, 8, 8, 0, 0)
        assert row.last_seen_at == later.replace(tzinfo=None)


def test_corrected_revision_is_not_backdated(make_bar) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    correction_seen = datetime(2026, 8, 9, 0, 0, tzinfo=UTC)
    with Session(engine) as session:
        repository = MarketDataRepository(session)
        repository.upsert_bars([make_bar()])
        repository.upsert_bars(
            [
                make_bar(
                    raw_hash="b" * 64,
                    first_observed_at=correction_seen,
                    retrieved_at=correction_seen,
                )
            ]
        )
        session.commit()
        rows = list(session.scalars(select(MarketData).order_by(MarketData.id)))
        assert len(rows) == 2
        assert rows[1].available_timestamp == correction_seen.replace(tzinfo=None)
        assert rows[1].availability_method == "first_observed"
        assert "corrected_revision" in rows[1].quality_flags


def test_provider_selection_is_immutable_within_run(make_bar) -> None:
    del make_bar
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = MarketDataRepository(session)
        run = repository.create_run(
            run_type="INGESTION",
            prediction_date=date(2026, 8, 10),
            cutoff_at=datetime(2026, 8, 9, 23, 30, tzinfo=UTC),
            data_version="test-config",
        )
        first = repository.save_provider_selection(
            run_id=run.run_id,
            canonical_symbol="sp500",
            interval="eod",
            selected_registry_key="yahoo_finance",
            selected_provider="yahoo_finance",
            selection_role=SelectionRole.PRIMARY,
            data_quality="FREE_UNVERIFIED",
            freshness_status=FreshnessStatus.FRESH,
            cutoff_at=datetime(2026, 8, 9, 23, 30, tzinfo=UTC),
            details={"provider_symbol": "^GSPC", "is_proxy": False},
        )
        same = repository.save_provider_selection(
            run_id=run.run_id,
            canonical_symbol="sp500",
            interval="eod",
            selected_registry_key="yahoo_finance",
            selected_provider="yahoo_finance",
            selection_role=SelectionRole.PRIMARY,
            data_quality="FREE_UNVERIFIED",
            freshness_status=FreshnessStatus.FRESH,
            cutoff_at=datetime(2026, 8, 9, 23, 30, tzinfo=UTC),
        )
        session.commit()
        assert same.selection_id == first.selection_id
        assert first.details == {"provider_symbol": "^GSPC", "is_proxy": False}
        assert session.scalar(select(func.count()).select_from(ProviderSelection)) == 1


def test_run_and_ingestion_batch_capture_partial_failure() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        repository = MarketDataRepository(session)
        run = repository.create_run(
            run_type="INGESTION",
            prediction_date=date(2026, 8, 10),
            cutoff_at=datetime(2026, 8, 9, 23, 30, tzinfo=UTC),
            data_version="test-config",
        )
        batch = repository.create_ingestion_batch(
            run_id=run.run_id, provider="eodhd", requested_symbols=2
        )
        repository.finish_ingestion_batch(
            batch,
            status="PARTIAL",
            succeeded_symbols=1,
            failed_symbols=["vix"],
            inserted_rows=5,
            reused_rows=0,
        )
        repository.finish_run(run, status="PARTIAL", failed_symbols=["vix"])
        session.commit()
        persisted = session.get(IngestionBatch, batch.batch_id)
        assert persisted is not None
        assert persisted.status == "PARTIAL"
        assert persisted.failed_symbols == ["vix"]
