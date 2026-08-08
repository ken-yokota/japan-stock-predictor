#!/usr/bin/env python3
"""Run estimated-PIT walk-forward OOS evaluation from persisted raw data."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from backtest.walk_forward import WalkForwardConfig, walk_forward_validate
from metrics.performance import calculate_performance_metrics
from models.base import ModelTrainingConfig
from scoring.readability import score_readability
from scoring.stability import (
    aggregate_coefficient_stability,
    calculate_coefficient_stability,
)
from scripts.runtime import load_runtime
from services.dataset import PointInTimeDatasetBuilder
from trading.strategy import (
    BuySignalConfig,
    ExecutionConfig,
    simulate_prediction_frame,
)


def _years_before(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _finite_json(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _finite_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    return value


def _parser() -> argparse.ArgumentParser:
    today = date.today()
    end_date = today - timedelta(days=1)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--to-date", type=date.fromisoformat, default=end_date)
    parser.add_argument("--ticker", action="append", dest="tickers")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/backtest"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    config, _, engine, factory = load_runtime(args.config_dir)
    end_date = args.to_date
    start_date = args.from_date or _years_before(end_date, 3)
    if start_date >= end_date:
        raise ValueError("from-date must be before to-date")
    configured = {stock.ticker for stock in config.stocks.stocks if stock.enabled}
    selected = set(args.tickers or configured)
    unknown = selected - configured
    if unknown:
        raise ValueError(f"unknown configured tickers: {sorted(unknown)}")
    model_config = ModelTrainingConfig(
        window_size=config.model.training.window_jpx_sessions,
        minimum_training_sessions=config.model.training.minimum_complete_rows,
        time_series_splits=config.model.cross_validation.n_splits,
        ridge_alphas=tuple(config.model.hyperparameters.ridge_alpha),
        logistic_cs=tuple(config.model.hyperparameters.logistic_c),
        random_state=config.model.reproducibility.random_seed,
    )
    costs = config.trading.costs
    position = config.trading.position
    if (
        costs.commission_bps_per_side is None
        or costs.slippage_bps_per_side is None
        or position.lot_size is None
    ):
        raise ValueError("trading cost/lot assumptions are not confirmed")
    signal = BuySignalConfig(
        return_threshold=config.trading.signal.predicted_intraday_return_threshold,
        probability_threshold=config.trading.signal.probability_up_threshold,
    )
    execution = ExecutionConfig(
        capital_per_stock=float(position.capital_per_stock_jpy),
        lot_size=position.lot_size,
        commission_bps=costs.commission_bps_per_side,
        slippage_bps=costs.slippage_bps_per_side,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, object] = {}
    failures: dict[str, str] = {}
    try:
        with factory() as session:
            builder = PointInTimeDatasetBuilder(session, config)
            for ticker in sorted(selected):
                try:
                    dataset = builder.build_backtest_frame(
                        ticker,
                        start_date,
                        end_date,
                        initial_training_sessions=model_config.window_size,
                        minimum_feature_coverage=(
                            1.0 - float(config.model.features.max_missing_ratio or 0.0)
                        ),
                    )
                    results = walk_forward_validate(
                        dataset.frame,
                        feature_names=dataset.feature_names,
                        config=WalkForwardConfig(model_config),
                    )
                    prices = dataset.frame.loc[
                        :, ["ticker", "market_date", "open", "close"]
                    ]
                    evaluated = results.merge(
                        prices,
                        left_on=["ticker", "prediction_date"],
                        right_on=["ticker", "market_date"],
                        how="left",
                        validate="one_to_one",
                    )
                    evaluated = simulate_prediction_frame(
                        evaluated,
                        signal_config=signal,
                        execution_config=execution,
                    )
                    successful = evaluated.loc[evaluated["status"] == "OK"]
                    trades = successful.loc[
                        successful["buy_signal"] & (successful["shares"] > 0)
                    ]
                    metrics = calculate_performance_metrics(
                        trades["net_profit"].to_numpy(dtype=float),
                        trade_returns=trades["trade_return"].to_numpy(dtype=float),
                        predicted_returns=successful["predicted_return"].to_numpy(
                            dtype=float
                        ),
                        actual_returns=successful["actual_return"].to_numpy(
                            dtype=float
                        ),
                        capital_per_trade=execution.capital_per_stock,
                    )
                    stability = aggregate_coefficient_stability(
                        calculate_coefficient_stability(
                            list(successful["ridge_coefficients"])
                        )
                    )
                    readability = score_readability(
                        profit_factor=metrics.profit_factor,
                        win_rate=metrics.win_rate,
                        prediction_correlation=metrics.pearson_correlation,
                        direction_accuracy=metrics.direction_accuracy,
                        coefficient_stability=stability,
                        number_of_trades=metrics.number_of_trades,
                    )
                    output_path = args.output_dir / f"{ticker}_walk_forward.csv"
                    evaluated.to_csv(output_path, index=False)
                    summaries[ticker] = _finite_json(
                        {
                            "availability_evidence": dataset.availability_evidence,
                            "feature_count": len(dataset.feature_names),
                            "oos_predictions": len(successful),
                            "metrics": asdict(metrics),
                            "coefficient_stability": stability,
                            "readability": asdict(readability),
                            "output": str(output_path),
                        }
                    )
                except (ValueError, KeyError) as exc:
                    failures[ticker] = str(exc)[:500]
    finally:
        engine.dispose()
    report = {
        "status": "SUCCESS" if not failures else "PARTIAL",
        "from_date": start_date.isoformat(),
        "to_date": end_date.isoformat(),
        "warning": (
            "Historical EOD availability uses provider schedule estimates; "
            "live snapshots are prospective first-observed only."
        ),
        "tickers": summaries,
        "failures": failures,
    }
    report_path = args.output_dir / "summary.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0 if summaries else 2


if __name__ == "__main__":
    raise SystemExit(main())
