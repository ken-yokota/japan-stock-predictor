"""Free-provider market-data planning, ingestion, snapshot, and verification CLI.

Examples::

    python -m data.fetch config-check
    python -m data.fetch verify-yahoo
    python -m data.fetch verify-eodhd
    python -m data.fetch fetch-free --from-date 2025-01-01 --to-date 2026-08-07
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from sqlalchemy.orm import Session

from data.availability import prediction_cutoff
from data.config import (
    AppConfig,
    IndicatorSourceConfig,
    StockConfig,
    load_app_config,
)
from data.env import EnvironmentSettings
from data.logging import configure_logging
from data.provider_router import (
    EodRouteCandidate,
    ProviderRouter,
    SnapshotRouteCandidate,
)
from data.providers.base import MarketDataProvider, ProviderError
from data.providers.eodhd import EODHDFreeProvider
from data.providers.treasury import TreasuryProvider
from data.providers.yahoo import YahooFinanceProvider
from data.schemas import DataInterval, FetchRequest, MarketBar, SnapshotRequest
from data.snapshot import FreshnessStatus
from data.treasury_features import build_treasury_features
from database.connection import create_database_engine, create_session_factory
from database.repository import MarketDataRepository, UpsertSummary

LOGGER = logging.getLogger(__name__)
UTC = UTC
YAHOO_EOD_LAG_MINUTES = {"JP": 20, "US": 30}
EODHD_EOD_LAG_MINUTES = {"US": 15}
DEFAULT_EOD_LAG_MINUTES = 60


@dataclass(frozen=True, slots=True)
class IndicatorTarget:
    """One canonical factor with a Yahoo primary and optional EODHD fallback."""

    canonical_symbol: str
    name: str
    required: bool
    primary: IndicatorSourceConfig
    fallback: IndicatorSourceConfig | None = None


@dataclass(frozen=True, slots=True)
class SnapshotTarget:
    canonical_symbol: str
    name: str
    required: bool
    source: IndicatorSourceConfig


@dataclass(frozen=True, slots=True)
class FetchPlan:
    """Validated free-provider work, separated by provider capability."""

    stocks: tuple[StockConfig, ...]
    eod: tuple[IndicatorTarget, ...]
    snapshots: tuple[SnapshotTarget, ...]
    treasury_symbols: tuple[str, ...]
    unresolved_required: tuple[str, ...]
    stock_provider_key: str = "yahoo_finance"

    @property
    def external_target_count(self) -> int:
        return (
            len(self.stocks)
            + len(self.eod)
            + len(self.snapshots)
            + len(self.treasury_symbols)
        )


@dataclass(slots=True)
class IngestionReport:
    """Sanitized partial-success report; exclusions never become fake values."""

    requested_sources: int
    succeeded_sources: int = 0
    inserted_rows: int = 0
    reused_rows: int = 0
    failed_sources: dict[str, str] = field(default_factory=dict)
    skipped_sources: dict[str, str] = field(default_factory=dict)
    unresolved_required: list[str] = field(default_factory=list)
    selected_providers: dict[str, str] = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.failed_sources and not self.succeeded_sources:
            return "FAILED"
        if self.failed_sources or self.unresolved_required or self.skipped_sources:
            return "PARTIAL"
        return "SUCCESS"

    def add_upsert(self, summary: UpsertSummary) -> None:
        self.inserted_rows += summary.inserted
        self.reused_rows += summary.reused


def _verified_source(
    sources: list[IndicatorSourceConfig], provider: str
) -> IndicatorSourceConfig | None:
    return next(
        (
            source
            for source in sources
            if source.provider == provider and source.status == "verified"
        ),
        None,
    )


def build_fetch_plan(config: AppConfig) -> FetchPlan:
    """Build a Yahoo-first plan; unsupported factors remain explicitly absent."""

    eod: list[IndicatorTarget] = []
    snapshots: list[SnapshotTarget] = []
    treasury_symbols: list[str] = []
    unresolved: list[str] = []
    for indicator in config.indicators.indicators:
        derived = _verified_source(indicator.sources, "internal")
        treasury = _verified_source(indicator.sources, "us_treasury")
        yahoo = _verified_source(indicator.sources, "yahoo_finance")
        fallback = _verified_source(indicator.sources, "eodhd_free")

        if derived is not None:
            continue
        if treasury is not None:
            treasury_symbols.append(indicator.id)
            continue
        if yahoo is None:
            if indicator.required:
                unresolved.append(indicator.id)
            continue
        if yahoo.data_mode == "eod":
            eod.append(
                IndicatorTarget(
                    indicator.id,
                    indicator.name,
                    indicator.required,
                    yahoo,
                    fallback if fallback and fallback.data_mode == "eod" else None,
                )
            )
        if yahoo.snapshot_enabled:
            snapshots.append(
                SnapshotTarget(
                    indicator.id,
                    indicator.name,
                    indicator.required,
                    yahoo,
                )
            )
    return FetchPlan(
        stocks=tuple(stock for stock in config.stocks.stocks if stock.enabled),
        eod=tuple(eod),
        snapshots=tuple(snapshots),
        treasury_symbols=tuple(sorted(treasury_symbols)),
        unresolved_required=tuple(sorted(unresolved)),
        stock_provider_key=config.settings.provider.primary,
    )


def _store(
    repository: MarketDataRepository,
    rows: list[MarketBar],
    report: IngestionReport,
    *,
    stock_symbols: set[str] | None = None,
) -> None:
    if not rows:
        raise ValueError("provider returned no data")
    report.add_upsert(
        repository.upsert_bars(rows, stock_symbols=stock_symbols or set())
    )


def _sessions(
    start_date: date,
    end_date: date,
    *,
    market: str,
) -> tuple[date, ...]:
    calendar_name = "XTKS" if market == "JP" else "XNYS"
    try:
        calendar = xcals.get_calendar(calendar_name)
        sessions = calendar.sessions_in_range(start_date, end_date)
    except (ValueError, KeyError):
        return tuple(
            start_date + timedelta(days=offset)
            for offset in range((end_date - start_date).days + 1)
            if (start_date + timedelta(days=offset)).weekday() < 5
        )
    return tuple(stamp.date() for stamp in sessions)


def _source_request(
    target: IndicatorTarget,
    source: IndicatorSourceConfig,
    *,
    start_date: date,
    end_date: date,
) -> FetchRequest:
    if (
        source.provider_symbol is None
        or source.market is None
        or source.market_timezone is None
        or source.market_close is None
    ):
        raise ValueError("verified EOD source lacks required metadata")
    lag_map = (
        EODHD_EOD_LAG_MINUTES
        if source.provider == "eodhd_free"
        else YAHOO_EOD_LAG_MINUTES
    )
    return FetchRequest(
        canonical_symbol=target.canonical_symbol,
        provider_symbol=source.provider_symbol,
        market=source.market,
        market_timezone=source.market_timezone,
        market_close=source.market_close,
        availability_lag_minutes=lag_map.get(source.market, DEFAULT_EOD_LAG_MINUTES),
        start_date=start_date,
        end_date=end_date,
    )


def _record_selection(
    repository: MarketDataRepository,
    *,
    run_id: str | None,
    canonical_symbol: str,
    interval: str,
    cutoff_at: datetime,
    selection: Any,
    actual_session: date | None,
    details: dict[str, object] | None = None,
) -> None:
    if run_id is None:
        return
    repository.save_provider_attempts(
        run_id=run_id,
        canonical_symbol=canonical_symbol,
        interval=interval,
        attempts=selection.attempts,
        actual_session=actual_session,
    )
    if selection.selected_provider is None or selection.selected_registry_key is None:
        return
    row = selection.row if hasattr(selection, "row") else selection.rows[-1]
    repository.save_provider_selection(
        run_id=run_id,
        canonical_symbol=canonical_symbol,
        interval=interval,
        selected_registry_key=selection.selected_registry_key,
        selected_provider=selection.selected_provider,
        selection_role=selection.selection_role,
        data_quality=row.data_quality.value,
        freshness_status=(
            selection.assessment.status
            if hasattr(selection, "assessment")
            else FreshnessStatus.FRESH
        ),
        cutoff_at=cutoff_at,
        coverage=(selection.attempts[-1].coverage if selection.attempts else None),
        details=details,
    )


def execute_fetch_plan(
    plan: FetchPlan,
    *,
    market_providers: Mapping[str, MarketDataProvider],
    treasury_provider: TreasuryProvider,
    repository: MarketDataRepository,
    start_date: date,
    end_date: date,
    cutoff_at: datetime,
    include_snapshots: bool,
    operational_run: bool = False,
    run_id: str | None = None,
) -> IngestionReport:
    """Execute independent free sources and exclude failed/stale observations."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    report = IngestionReport(
        requested_sources=plan.external_target_count,
        unresolved_required=list(plan.unresolved_required),
    )
    router = ProviderRouter(market_providers)

    stock_symbols = {stock.ticker for stock in plan.stocks}
    for stock in plan.stocks:
        symbol = stock.provider_symbols.get(plan.stock_provider_key)
        if symbol is None:
            report.failed_sources[stock.ticker] = (
                f"missing {plan.stock_provider_key} stock symbol"
            )
            continue
        try:
            selection = router.fetch_eod_series(
                [
                    EodRouteCandidate(
                        plan.stock_provider_key,
                        FetchRequest(
                            canonical_symbol=stock.ticker,
                            provider_symbol=symbol,
                            market="JP",
                            market_timezone=stock.market_timezone,
                            market_close="15:30",
                            availability_lag_minutes=20,
                            start_date=start_date,
                            end_date=end_date,
                            currency="JPY",
                        ),
                    )
                ],
                required_dates=_sessions(start_date, end_date, market="JP"),
                cutoff_at=cutoff_at,
                operational_run=operational_run,
            )
            _record_selection(
                repository,
                run_id=run_id,
                canonical_symbol=stock.ticker,
                interval=DataInterval.EOD.value,
                cutoff_at=cutoff_at,
                selection=selection,
                actual_session=(
                    selection.rows[-1].market_date if selection.rows else None
                ),
                details={
                    "provider_symbol": symbol,
                    "source_kind": "direct",
                    "is_proxy": False,
                },
            )
            if not selection.rows or selection.selected_provider is None:
                reasons = "; ".join(attempt.reason for attempt in selection.attempts)
                raise ValueError(reasons or "no provider passed stock EOD gates")
            _store(
                repository,
                list(selection.rows),
                report,
                stock_symbols=stock_symbols,
            )
        except (ProviderError, ValueError) as exc:
            report.failed_sources[stock.ticker] = str(exc)
        else:
            report.succeeded_sources += 1
            report.selected_providers[stock.ticker] = selection.selected_provider

    for target in plan.eod:
        try:
            primary_request = _source_request(
                target, target.primary, start_date=start_date, end_date=end_date
            )
            if target.primary.provider is None:
                raise ValueError("primary source has no provider registry key")
            candidates = [EodRouteCandidate(target.primary.provider, primary_request)]
            fallback = target.fallback
            if (
                fallback is not None
                and fallback.provider is not None
                and fallback.provider in market_providers
            ):
                candidates.append(
                    EodRouteCandidate(
                        fallback.provider,
                        _source_request(
                            target,
                            fallback,
                            start_date=start_date,
                            end_date=end_date,
                        ),
                    )
                )
            required_dates = _sessions(
                start_date,
                end_date,
                market=target.primary.market or "US",
            )
            selection = router.fetch_eod_series(
                candidates,
                required_dates=required_dates,
                cutoff_at=cutoff_at,
                operational_run=operational_run,
            )
            selected_source = target.primary
            if (
                fallback is not None
                and selection.selected_registry_key == fallback.provider
            ):
                selected_source = fallback
            _record_selection(
                repository,
                run_id=run_id,
                canonical_symbol=target.canonical_symbol,
                interval=DataInterval.EOD.value,
                cutoff_at=cutoff_at,
                selection=selection,
                actual_session=(
                    selection.rows[-1].market_date if selection.rows else None
                ),
                details={
                    "provider_symbol": selected_source.provider_symbol,
                    "source_kind": selected_source.kind,
                    "is_proxy": bool(selected_source.is_proxy),
                },
            )
            if not selection.rows or selection.selected_provider is None:
                reasons = "; ".join(attempt.reason for attempt in selection.attempts)
                raise ValueError(reasons or "no provider passed EOD gates")
            _store(repository, list(selection.rows), report)
        except (ProviderError, ValueError) as exc:
            report.failed_sources[target.canonical_symbol] = str(exc)
        else:
            report.succeeded_sources += 1
            report.selected_providers[target.canonical_symbol] = (
                selection.selected_provider
            )

    if plan.treasury_symbols:
        try:
            treasury_rows = treasury_provider.fetch_range(start_date, end_date)
            derived_rows = build_treasury_features(treasury_rows)
            _store(repository, [*treasury_rows, *derived_rows], report)
            expected_sessions = _sessions(start_date, end_date, market="US")
            expected_latest = expected_sessions[-1] if expected_sessions else None
            for symbol in plan.treasury_symbols:
                matching = [
                    row for row in treasury_rows if row.canonical_symbol == symbol
                ]
                if not matching:
                    report.failed_sources[symbol] = "official Treasury tenor is missing"
                    continue
                if operational_run and any(
                    row.first_observed_at > cutoff_at or row.retrieved_at > cutoff_at
                    for row in matching
                ):
                    report.skipped_sources[symbol] = (
                        "Treasury value was first retrieved after the 08:30 cutoff"
                    )
                    continue
                if operational_run and (
                    expected_latest is None
                    or max(row.market_date for row in matching) != expected_latest
                ):
                    report.skipped_sources[symbol] = (
                        "Treasury latest U.S. session is not yet published"
                    )
                    continue
                report.succeeded_sources += 1
                report.selected_providers[symbol] = treasury_provider.name
        except (ProviderError, ValueError) as exc:
            for symbol in plan.treasury_symbols:
                report.failed_sources[symbol] = str(exc)

    if not include_snapshots:
        for snapshot_target in plan.snapshots:
            report.skipped_sources[snapshot_target.canonical_symbol] = (
                "08:30 snapshot fetch not requested"
            )
        return report

    for snapshot_target in plan.snapshots:
        source = snapshot_target.source
        if (
            source.provider is None
            or source.provider_symbol is None
            or source.market is None
            or source.market_timezone is None
            or source.max_age_minutes is None
        ):
            report.failed_sources[snapshot_target.canonical_symbol] = (
                "snapshot source lacks freshness metadata"
            )
            continue
        snapshot_selection = router.fetch_snapshot(
            [
                SnapshotRouteCandidate(
                    source.provider,
                    SnapshotRequest(
                        snapshot_target.canonical_symbol,
                        source.provider_symbol,
                        source.market,
                        source.market_timezone,
                    ),
                )
            ],
            cutoff_at=cutoff_at,
            max_age=timedelta(minutes=source.max_age_minutes),
        )
        _record_selection(
            repository,
            run_id=run_id,
            canonical_symbol=snapshot_target.canonical_symbol,
            interval=DataInterval.LIVE_SNAPSHOT.value,
            cutoff_at=cutoff_at,
            selection=snapshot_selection,
            actual_session=(
                snapshot_selection.row.market_date if snapshot_selection.row else None
            ),
            details={
                "provider_symbol": source.provider_symbol,
                "source_kind": source.kind,
                "is_proxy": bool(source.is_proxy),
            },
        )
        if snapshot_selection.row is None:
            report.skipped_sources[snapshot_target.canonical_symbol] = (
                snapshot_selection.assessment.reason
            )
            continue
        _store(repository, [snapshot_selection.row], report)
        report.succeeded_sources += 1
        report.selected_providers[snapshot_target.canonical_symbol] = (
            snapshot_selection.row.provider
        )
    return report


def verify_yahoo(config: AppConfig, provider: YahooFinanceProvider) -> dict[str, Any]:
    """Best-effort runtime verification; Yahoo does not expose an official catalog."""

    symbols: dict[str, str] = {}
    for stock in config.stocks.stocks:
        symbol = stock.provider_symbols.get("yahoo_finance")
        symbols[stock.ticker] = (
            "VERIFIED"
            if symbol and provider.validate_provider_symbol(symbol)
            else "MISSING"
        )
    for indicator in config.indicators.indicators:
        source = _verified_source(indicator.sources, "yahoo_finance")
        if source is None or source.provider_symbol is None:
            continue
        symbols[indicator.id] = (
            "VERIFIED"
            if provider.validate_provider_symbol(source.provider_symbol)
            else "NOT_FOUND"
        )
    health = provider.healthcheck()
    return {
        "provider": provider.name,
        "health": "OK" if health.ok else "ERROR",
        "symbols": symbols,
        "warning": "unofficial best-effort personal/research data",
    }


def verify_eodhd(config: AppConfig, provider: EODHDFreeProvider) -> dict[str, Any]:
    """Spend only the small configured free quota on unique EOD catalogs."""

    symbols: dict[str, str] = {}
    for indicator in config.indicators.indicators:
        source = _verified_source(indicator.sources, "eodhd_free")
        if (
            source is None
            or source.data_mode != "eod"
            or source.provider_symbol is None
        ):
            continue
        try:
            ok = provider.validate_provider_symbol(source.provider_symbol)
        except ProviderError as exc:
            symbols[indicator.id] = f"CHECK_FAILED: {exc}"
        else:
            symbols[indicator.id] = "VERIFIED" if ok else "NOT_FOUND"
    return {
        "provider": provider.name,
        "plan_variant": provider.plan_variant,
        "symbols": symbols,
        "calls_used": provider.calls_used,
    }


def compare_free_eod(
    plan: FetchPlan,
    *,
    market_providers: Mapping[str, MarketDataProvider],
    start_date: date,
    end_date: date,
    max_series: int = 5,
) -> dict[str, Any]:
    """Compare a small quota-safe sample of equivalent Yahoo/EODHD instruments."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    if max_series < 1:
        raise ValueError("max_series must be positive")
    results: dict[str, Any] = {}
    compared = 0
    for target in plan.eod:
        fallback = target.fallback
        primary_key = target.primary.provider
        fallback_key = fallback.provider if fallback is not None else None
        primary_symbol = target.primary.provider_symbol
        fallback_symbol = fallback.provider_symbol if fallback is not None else None
        if (
            fallback is None
            or primary_key is None
            or fallback_key is None
            or primary_symbol is None
            or fallback_symbol is None
        ):
            continue
        # Compare only the same listed instrument. Index-to-ETF proxy prices are
        # not commensurate and must not be presented as a feed discrepancy.
        fallback_base = fallback_symbol.rsplit(".", maxsplit=1)[0]
        if primary_symbol.upper() != fallback_base.upper():
            continue
        if primary_key not in market_providers or fallback_key not in market_providers:
            continue
        if compared >= max_series:
            break
        compared += 1
        try:
            primary_rows = market_providers[primary_key].fetch_eod(
                _source_request(
                    target,
                    target.primary,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            fallback_rows = market_providers[fallback_key].fetch_eod(
                _source_request(
                    target,
                    fallback,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
            primary_by_date = {row.market_date: row for row in primary_rows}
            fallback_by_date = {row.market_date: row for row in fallback_rows}
            common_dates = sorted(primary_by_date.keys() & fallback_by_date.keys())
            if not common_dates:
                raise ValueError("providers returned no common market date")
            latest_date = common_dates[-1]
            primary_close = primary_by_date[latest_date].close
            fallback_close = fallback_by_date[latest_date].close
            relative_difference = (
                None if primary_close == 0 else (fallback_close / primary_close) - 1
            )
        except (ProviderError, ValueError) as exc:
            results[target.canonical_symbol] = {
                "status": "CHECK_FAILED",
                "reason": str(exc),
            }
        else:
            results[target.canonical_symbol] = {
                "status": "COMPARED",
                "market_date": latest_date,
                "primary_provider": market_providers[primary_key].name,
                "primary_symbol": primary_symbol,
                "primary_close": primary_close,
                "fallback_provider": market_providers[fallback_key].name,
                "fallback_symbol": fallback_symbol,
                "fallback_close": fallback_close,
                "relative_close_difference": relative_difference,
            }
    return {
        "comparison_scope": "same listed instruments only",
        "max_series": max_series,
        "results": results,
    }


def _config_hash(config: AppConfig) -> str:
    payload = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("config-check", help="validate YAML without network")
    subparsers.add_parser("verify-yahoo", help="best-effort Yahoo symbol check")
    subparsers.add_parser("verify-eodhd", help="optional EODHD Free EOD check")
    compare = subparsers.add_parser(
        "compare-eod", help="quota-safe Yahoo/EODHD Free close comparison"
    )
    compare.add_argument("--from-date", type=date.fromisoformat, required=True)
    compare.add_argument("--to-date", type=date.fromisoformat, required=True)
    compare.add_argument("--max-series", type=int, default=5)
    fetch = subparsers.add_parser(
        "fetch-free", help="fetch Yahoo, Treasury, and optional EODHD fallback"
    )
    fetch.add_argument("--from-date", type=date.fromisoformat, required=True)
    fetch.add_argument("--to-date", type=date.fromisoformat, required=True)
    fetch.add_argument("--include-snapshots", action="store_true")
    fetch.add_argument("--prediction-date", type=date.fromisoformat)
    return parser


def _providers(
    config: AppConfig,
    environment: EnvironmentSettings,
) -> tuple[YahooFinanceProvider, TreasuryProvider, EODHDFreeProvider | None]:
    settings = config.settings.provider
    yahoo = YahooFinanceProvider(
        timeout_seconds=environment.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_initial_seconds,
    )
    treasury = TreasuryProvider(
        timeout_seconds=environment.http_timeout_seconds,
        max_retries=settings.max_retries,
        backoff_seconds=settings.backoff_initial_seconds,
    )
    secret = (
        environment.eodhd_api_key.get_secret_value()
        if environment.eodhd_api_key is not None
        else ""
    )
    eodhd = (
        EODHDFreeProvider(
            secret,
            base_url=environment.eodhd_base_url,
            timeout_seconds=environment.http_timeout_seconds,
            max_retries=settings.max_retries,
            backoff_seconds=settings.backoff_initial_seconds,
            max_calls_per_run=settings.eodhd_free_max_calls_per_run,
        )
        if secret
        else None
    )
    return yahoo, treasury, eodhd


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_app_config(args.config_dir)
    environment = EnvironmentSettings()
    configure_logging(environment.log_level)
    plan = build_fetch_plan(config)

    if args.command == "config-check":
        _print_json(
            {
                "status": "OK",
                "primary_provider": config.settings.provider.primary,
                "stocks": len(plan.stocks),
                "historical_indicators": len(plan.eod),
                "snapshot_indicators": len(plan.snapshots),
                "treasury_tenors": len(plan.treasury_symbols),
                "unresolved_required_indicators": list(plan.unresolved_required),
            }
        )
        return 0

    yahoo, treasury, eodhd = _providers(config, environment)
    try:
        if args.command == "verify-yahoo":
            _print_json(verify_yahoo(config, yahoo))
            return 0
        if args.command == "verify-eodhd":
            if eodhd is None:
                raise ValueError("EODHD_API_KEY is not configured")
            _print_json(verify_eodhd(config, eodhd))
            return 0
        if args.command == "compare-eod":
            if eodhd is None:
                raise ValueError("EODHD_API_KEY is not configured")
            if args.max_series > config.settings.provider.eodhd_free_max_calls_per_run:
                raise ValueError("max-series exceeds the configured EODHD Free budget")
            _print_json(
                compare_free_eod(
                    plan,
                    market_providers={
                        "yahoo_finance": yahoo,
                        "eodhd_free": eodhd,
                    },
                    start_date=args.from_date,
                    end_date=args.to_date,
                    max_series=args.max_series,
                )
            )
            return 0

        engine = create_database_engine(environment.require_database_url())
        factory = create_session_factory(engine)
        session: Session = factory()
        run = None
        batch = None
        try:
            repository = MarketDataRepository(session)
            today_jst = datetime.now(
                ZoneInfo(config.settings.application.timezone)
            ).date()
            prediction_date = args.prediction_date or today_jst
            cutoff_at = prediction_cutoff(prediction_date)
            run = repository.create_run(
                run_type="INGESTION",
                prediction_date=prediction_date,
                cutoff_at=cutoff_at,
                data_version=_config_hash(config),
            )
            batch = repository.create_ingestion_batch(
                run_id=run.run_id,
                provider="free_provider_stack",
                requested_symbols=plan.external_target_count,
            )
            session.commit()
            report = execute_fetch_plan(
                plan,
                market_providers={
                    "yahoo_finance": yahoo,
                    **({"eodhd_free": eodhd} if eodhd is not None else {}),
                },
                treasury_provider=treasury,
                repository=repository,
                start_date=args.from_date,
                end_date=args.to_date,
                cutoff_at=cutoff_at,
                include_snapshots=args.include_snapshots,
                operational_run=args.include_snapshots,
                run_id=run.run_id,
            )
            repository.finish_ingestion_batch(
                batch,
                status=report.status,
                succeeded_symbols=report.succeeded_sources,
                failed_symbols=list(report.failed_sources),
                inserted_rows=report.inserted_rows,
                reused_rows=report.reused_rows,
            )
            repository.finish_run(
                run,
                status=report.status,
                failed_symbols=list(report.failed_sources),
            )
            session.commit()
            _print_json(
                asdict(report) | {"status": report.status, "run_id": run.run_id}
            )
            return 0 if report.status in {"SUCCESS", "PARTIAL"} else 2
        except Exception as exc:
            session.rollback()
            if run is not None:
                run = session.merge(run)
                if batch is not None:
                    batch = session.merge(batch)
                    repository.finish_ingestion_batch(
                        batch,
                        status="FAILED",
                        succeeded_symbols=0,
                        failed_symbols=[],
                        inserted_rows=0,
                        reused_rows=0,
                    )
                repository.finish_run(run, status="FAILED", error_message=str(exc))
                session.commit()
            raise
        finally:
            session.close()
            engine.dispose()
    finally:
        yahoo.close()
        treasury.close()
        if eodhd is not None:
            eodhd.close()


if __name__ == "__main__":
    raise SystemExit(main())
