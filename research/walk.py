"""One rolling walk-forward pass over a date window for one feature set.

Extracted so that the week-test and the feature comparison run *the same* loop.
If the comparison had its own copy, a difference between two feature sets could
turn out to be a difference between two implementations, and the comparison
would prove nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from models.base import InsufficientTrainingData, ModelTrainingConfig
from models.training import train_ticker_model
from research.dataset import build_indicator_frame, build_stock_frame
from research.feature_sets import FeatureSet
from research.history import DEFAULT_CACHE_DIR
from trading.strategy import BuySignalConfig, ExecutionConfig, simulate_intraday_trade


@dataclass(slots=True)
class WindowResult:
    """Every prediction, coefficient, and exclusion from one pass."""

    predictions: list[dict[str, Any]] = field(default_factory=list)
    coefficients: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    feature_names: dict[str, tuple[str, ...]] = field(default_factory=dict)
    missing_series: list[str] = field(default_factory=list)

    def distinct_features(self) -> list[str]:
        seen: dict[str, None] = {}
        for names in self.feature_names.values():
            for name in names:
                seen.setdefault(name, None)
        return list(seen)


def default_history_start(from_date: date, training_window: int) -> date:
    """Return how far back to fetch so the first test day has a full window."""

    return from_date - timedelta(days=int(training_window * 2.2) + 90)


def require_complete_data(
    result: WindowResult, feature_set: FeatureSet, *, allow_missing: bool
) -> None:
    """Stop unless every configured series actually arrived.

    Yahoo answers a throttled request with an empty frame rather than an error,
    so a rate-limited run silently produces a *smaller* feature set wearing the
    larger set's name. Comparing against that would measure the throttle, not
    the factors. Fail closed instead, and let an operator opt out deliberately.
    """

    if not result.missing_series or allow_missing:
        return
    symbols = ", ".join(sorted(set(result.missing_series)))
    raise SystemExit(
        f"取得できなかった系列があります ({feature_set.name}): {symbols}\n"
        "Yahooのレート制限中はこれが起きます。時間をおいて再実行してください。"
        "欠けたまま進めるなら --allow-missing-indicators を付けてください。"
    )


def _pool_key(stock: Any) -> str:
    """Which tickers share a fitted model. Sector, because they share drivers."""

    return str(getattr(stock, "sector", "") or "unknown")


def run_pooled_window(
    *,
    stocks: list[Any],
    feature_set: FeatureSet,
    from_date: date,
    to_date: date,
    history_start: date,
    training_config: ModelTrainingConfig,
    signal_config: BuySignalConfig,
    execution_config: ExecutionConfig,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> WindowResult:
    """One model per sector per session, instead of one per ticker.

    The measured ceiling on this problem is not the indicator list. A daily
    return is mostly beta times a market move nobody can know at 08:30, and the
    residual that is predictable has to be found in 120 observations against
    more predictors than that. Pooling a sector multiplies the training rows by
    the number of tickers in it - three to five here - without adding a single
    series, and a paired test needs that power before it can tell any indicator
    change from noise.

    The walk-forward boundary is identical to the per-ticker path: every row
    used for fitting comes from a session strictly before the one predicted,
    for every ticker in the pool.
    """

    indicators = build_indicator_frame(
        feature_set.indicators, history_start, to_date, cache_dir=cache_dir
    )
    result = WindowResult(missing_series=list(indicators.missing))

    built_frames: dict[str, Any] = {}
    pools: dict[str, list[Any]] = {}
    for stock in stocks:
        if not stock.enabled:
            continue
        symbol = stock.provider_symbols.get("yahoo_finance")
        if symbol is None:
            result.failures[stock.ticker] = "Yahoo symbol is unresolved"
            continue
        try:
            built = build_stock_frame(
                stock.ticker,
                symbol,
                history_start,
                to_date,
                feature_set=feature_set,
                indicators=indicators,
                cache_dir=cache_dir,
            )
        except Exception as error:
            result.failures[stock.ticker] = (
                f"{type(error).__name__}: {str(error)[:160]}"
            )
            continue
        if built.is_empty:
            result.failures[stock.ticker] = "no price history returned"
            result.missing_series.extend(built.missing)
            continue
        built_frames[stock.ticker] = built
        result.feature_names[stock.ticker] = built.feature_names
        result.missing_series.extend(built.missing)
        pools.setdefault(_pool_key(stock), []).append(stock)

    for pool_name, members in pools.items():
        present = [s for s in members if s.ticker in built_frames]
        if not present:
            continue
        # Ticker-specific columns - an ADR of one company - cannot be pooled,
        # so the shared schema is the intersection. Losing them is part of what
        # the comparison is measuring, not an accident to be hidden.
        shared = set(built_frames[present[0].ticker].feature_names)
        for stock in present[1:]:
            shared &= set(built_frames[stock.ticker].feature_names)
        feature_names = tuple(
            name
            for name in built_frames[present[0].ticker].feature_names
            if name in shared
        )
        if not feature_names:
            for stock in present:
                result.failures[stock.ticker] = "no shared features in pool"
            continue

        sessions = sorted(
            {
                day
                for stock in present
                for day in built_frames[stock.ticker].frame["market_date"]
                if from_date <= day <= to_date
            }
        )
        for target_date in sessions:
            training_rows = []
            targets = []
            for stock in present:
                frame = built_frames[stock.ticker].frame
                history = frame.loc[frame["market_date"] < target_date]
                usable = history.loc[history["intraday_return"].notna()]
                if usable.empty:
                    continue
                training_rows.append(usable.loc[:, list(feature_names)])
                targets.append(usable["intraday_return"])
            if not training_rows:
                continue
            pooled_features = pd.concat(training_rows, ignore_index=True)
            pooled_target = pd.concat(targets, ignore_index=True)
            if len(pooled_features) < training_config.minimum_training_sessions:
                continue
            try:
                model = train_ticker_model(
                    pool_name,
                    pooled_features,
                    pooled_target,
                    feature_names=feature_names,
                    config=training_config,
                )
            except InsufficientTrainingData as error:
                for stock in present:
                    result.failures.setdefault(stock.ticker, str(error)[:160])
                continue

            for stock in present:
                frame = built_frames[stock.ticker].frame
                rows = frame.loc[frame["market_date"] == target_date]
                if rows.empty:
                    continue
                current = rows.iloc[[0]]
                prediction = model.predict_one(current.loc[:, list(feature_names)])
                actual_open = float(current.iloc[0]["open"])
                actual_close = float(current.iloc[0]["close"])
                previous = current.iloc[0]["prev_close"]
                previous_close = float(previous) if pd.notna(previous) else None
                is_buy = (
                    prediction.predicted_return > signal_config.return_threshold
                    and prediction.probability_up >= signal_config.probability_threshold
                )
                trade = simulate_intraday_trade(
                    actual_open, actual_close, execute=is_buy, config=execution_config
                )
                actual_return = actual_close / actual_open - 1.0
                result.predictions.append(
                    {
                        "date": target_date.isoformat(),
                        "ticker": stock.ticker,
                        "pool": pool_name,
                        "predicted_return": prediction.predicted_return,
                        "probability_up": prediction.probability_up,
                        "training_sessions": model.training_sessions,
                        "ridge_alpha": prediction.ridge_alpha,
                        "logistic_c": prediction.logistic_c,
                        "reference_close": previous_close,
                        "actual_open": actual_open,
                        "actual_close": actual_close,
                        "actual_return": actual_return,
                        "signal": "BUY" if is_buy else "NO_BUY",
                        "direction_correct": bool(
                            (prediction.predicted_return > 0.0)
                            == (actual_return > 0.0)
                        ),
                        "shares": trade.shares,
                        "net_profit_jpy": trade.net_profit,
                    }
                )
    return result


def run_window(
    *,
    stocks: list[Any],
    feature_set: FeatureSet,
    from_date: date,
    to_date: date,
    history_start: date,
    training_config: ModelTrainingConfig,
    signal_config: BuySignalConfig,
    execution_config: ExecutionConfig,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
) -> WindowResult:
    """Predict every enabled ticker on every session in ``[from_date, to_date]``.

    Each prediction refits the ticker's own model on the sessions strictly
    before it, so the ``position`` slice below is the walk-forward boundary and
    the only thing standing between this and a look-ahead result.
    """

    indicators = build_indicator_frame(
        feature_set.indicators, history_start, to_date, cache_dir=cache_dir
    )
    result = WindowResult(missing_series=list(indicators.missing))

    for stock in stocks:
        if not stock.enabled:
            continue
        symbol = stock.provider_symbols.get("yahoo_finance")
        if symbol is None:
            result.failures[stock.ticker] = "Yahoo symbol is unresolved"
            continue
        try:
            built = build_stock_frame(
                stock.ticker,
                symbol,
                history_start,
                to_date,
                feature_set=feature_set,
                indicators=indicators,
                cache_dir=cache_dir,
            )
        except Exception as error:
            result.failures[stock.ticker] = (
                f"{type(error).__name__}: {str(error)[:160]}"
            )
            continue
        if built.is_empty:
            result.failures[stock.ticker] = "no price history returned"
            result.missing_series.extend(built.missing)
            continue
        frame = built.frame
        feature_names = built.feature_names
        result.feature_names[stock.ticker] = feature_names
        result.missing_series.extend(built.missing)

        test_positions = frame.index[
            (frame["market_date"] >= from_date) & (frame["market_date"] <= to_date)
        ]
        for position in test_positions:
            target_date = frame.at[position, "market_date"]
            history = frame.iloc[:position]
            usable = history.loc[history["intraday_return"].notna()]
            if len(usable) < training_config.minimum_training_sessions:
                result.failures.setdefault(
                    stock.ticker, f"insufficient history at {target_date}"
                )
                continue
            try:
                model = train_ticker_model(
                    stock.ticker,
                    usable.loc[:, list(feature_names)],
                    usable["intraday_return"],
                    feature_names=feature_names,
                    config=training_config,
                )
            except InsufficientTrainingData as error:
                result.failures.setdefault(stock.ticker, str(error)[:160])
                continue

            current = frame.iloc[[position]]
            prediction = model.predict_one(current.loc[:, list(feature_names)])
            actual_open = float(current.iloc[0]["open"])
            actual_close = float(current.iloc[0]["close"])
            previous_close = current.iloc[0]["prev_close"]
            previous_close = float(previous_close) if pd.notna(previous_close) else None
            is_buy = (
                prediction.predicted_return > signal_config.return_threshold
                and prediction.probability_up >= signal_config.probability_threshold
            )
            trade = simulate_intraday_trade(
                actual_open, actual_close, execute=is_buy, config=execution_config
            )
            actual_return = actual_close / actual_open - 1.0
            result.predictions.append(
                {
                    "date": target_date.isoformat(),
                    "ticker": stock.ticker,
                    "predicted_return": prediction.predicted_return,
                    "probability_up": prediction.probability_up,
                    "training_sessions": model.training_sessions,
                    "ridge_alpha": prediction.ridge_alpha,
                    "logistic_c": prediction.logistic_c,
                    # Morning view: the Open is unknown, so the reference is the
                    # previous close. Post-open view uses the realized Open.
                    "reference_close": previous_close,
                    "morning_predicted_close": (
                        previous_close * (1.0 + prediction.predicted_return)
                        if previous_close is not None
                        else None
                    ),
                    "actual_open": actual_open,
                    "post_open_predicted_close": actual_open
                    * (1.0 + prediction.predicted_return),
                    "actual_close": actual_close,
                    "actual_return": actual_return,
                    # Spec's auxiliary target: the same move stated in yen
                    # rather than as a ratio, measured from the realized Open.
                    "predicted_price_difference": actual_open
                    * prediction.predicted_return,
                    "actual_price_difference": actual_close - actual_open,
                    "signal": "BUY" if is_buy else "NO_BUY",
                    "direction_correct": bool(
                        (prediction.predicted_return > 0.0) == (actual_return > 0.0)
                    ),
                    "shares": trade.shares,
                    "gross_profit_jpy": trade.gross_profit,
                    "cost_jpy": trade.commission_cost + trade.slippage_cost,
                    "net_profit_jpy": trade.net_profit,
                }
            )
            for feature_name, value in model.regression_coefficients().items():
                result.coefficients.append(
                    {
                        "date": target_date.isoformat(),
                        "ticker": stock.ticker,
                        "feature": feature_name,
                        "coefficient": value,
                    }
                )
    return result
