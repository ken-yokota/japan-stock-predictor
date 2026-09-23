"""Reproducible estimated-PIT comparison; never promotes a challenger.

The shared production dataset builder supplies features. Outer predictions use
120 earlier sessions, with nested sparse selection frozen for 20 predictions.
Only daily return/yield-change columns enter the compact candidate universe.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from data.config import load_app_config
from database.connection import create_database_engine
from models import train_ticker_model
from research.nested_selection import FrozenSelection, nested_select
from research.robust_candidates import BASELINE_NAMES, fit_candidate
from research.robust_candidates import NAMES as ROBUST_CANDIDATES
from services.dataset import PointInTimeDatasetBuilder
from services.prediction import PredictionService


def run(
    database: str,
    output: Path,
    start: date,
    end: date,
    tickers: list[str],
    candidate_names: tuple[str, ...] = (),
    include_compact: bool = True,
) -> None:
    config = load_app_config("config")
    engine = (
        create_engine(database)
        if database.startswith("sqlite:")
        else create_database_engine(database)
    )
    output.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        if engine.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        elif engine.dialect.name == "sqlite":
            session.execute(text("PRAGMA query_only = ON"))
        builder = PointInTimeDatasetBuilder(session, config)
        settings = replace(
            PredictionService(builder, config)._model_config(),
            distribution_quantiles=(),
        )
        for ticker in tickers:
            target = output / f"{ticker}.json"
            if target.exists():
                cached = json.loads(target.read_text())
                if (
                    tuple(cached.get("candidate_names", ())) != candidate_names
                    or bool(cached.get("include_compact", True)) != include_compact
                ):
                    raise ValueError(f"cached candidate list differs for {ticker}")
                print(json.dumps({"ticker": ticker, "status": "CACHED"}), flush=True)
                continue
            dataset = builder.build_backtest_frame(ticker, start, end)
            frame, all_names = dataset.frame, dataset.feature_names
            candidates = tuple(
                n
                for n in all_names
                if n.endswith("__return_1d") or n.endswith("__change_1d")
            )
            rows = []
            frozen = None
            for position in range(120, len(frame)):
                train = frame.iloc[position - 120 : position]
                current = frame.iloc[[position]]
                if train.market_date.max() >= current.iloc[0].market_date:
                    raise AssertionError("non-OOS training rows")
                if include_compact and (frozen is None or frozen.due(position)):
                    selection = nested_select(
                        train.loc[:, candidates], train.intraday_return.to_numpy(float)
                    )
                    frozen = FrozenSelection(position, selection)
                champion_probability: float | None = None
                comparison_columns = [("champion_replay", all_names)]
                if include_compact:
                    if frozen is None:
                        raise AssertionError("compact selection was not fitted")
                    comparison_columns.append(
                        ("compact_nested_ridge", frozen.selection.names)
                    )
                for name, columns in comparison_columns:
                    # Missing today's selected feature is a skipped prediction,
                    # not a median-imputed trading signal.
                    missing = bool(current.loc[:, columns].isna().any(axis=None))
                    base = {
                        "ticker": ticker,
                        "date": str(current.iloc[0].market_date),
                        "model": name,
                        "training_start": str(train.iloc[0].market_date),
                        "training_end": str(train.iloc[-1].market_date),
                        "features": list(columns),
                        "actual_return": float(current.iloc[0].intraday_return),
                        "selection_position": frozen.selected_at_position
                        if name.startswith("compact") and frozen is not None
                        else None,
                    }
                    if missing and (name.startswith("compact") or candidate_names):
                        rows.append({**base, "status": "NO_PREDICTION_MISSING"})
                        continue
                    fitted = train_ticker_model(
                        ticker,
                        train.loc[:, columns],
                        train.intraday_return,
                        feature_names=columns,
                        config=settings,
                    )
                    prediction = fitted.predict_one(current.loc[:, columns])
                    if name == "champion_replay":
                        champion_probability = prediction.probability_up
                    rows.append(
                        {
                            **base,
                            "status": "OK",
                            "predicted_return": prediction.predicted_return,
                            "probability_up": prediction.probability_up,
                            "ridge_alpha": prediction.ridge_alpha,
                            "logistic_c": prediction.logistic_c,
                        }
                    )
                for name in candidate_names:
                    base = {
                        "ticker": ticker,
                        "date": str(current.iloc[0].market_date),
                        "model": name,
                        "training_start": str(train.iloc[0].market_date),
                        "training_end": str(train.iloc[-1].market_date),
                        "features": list(all_names),
                        "actual_return": float(current.iloc[0].intraday_return),
                        "selection_position": None,
                        "probability_source": "champion_logistic",
                    }
                    if champion_probability is None:
                        rows.append({**base, "status": "NO_PREDICTION_MISSING"})
                        continue
                    try:
                        fitted_candidate = fit_candidate(
                            name,
                            train.loc[:, all_names],
                            train.intraday_return,
                            current.loc[:, all_names],
                        )
                    except (ValueError, RuntimeError) as failure:
                        rows.append(
                            {
                                **base,
                                "status": "FAILED",
                                "failure_type": type(failure).__name__,
                            }
                        )
                        continue
                    rows.append(
                        {
                            **base,
                            "status": "OK",
                            "predicted_return": fitted_candidate.predicted_return,
                            "probability_up": champion_probability,
                            "train_mae": fitted_candidate.train_mae,
                        }
                    )
                if candidate_names:
                    history = train.intraday_return.to_numpy(float)
                    historical_frequency = float((history > 0).mean())
                    baselines = {
                        "zero_return": (0.0, historical_frequency),
                        "always_up": (float(np.abs(history).mean()), 1.0),
                        "historical_frequency": (
                            float(history.mean()),
                            historical_frequency,
                        ),
                    }
                    for name in BASELINE_NAMES:
                        baseline = {
                            "ticker": ticker,
                            "date": str(current.iloc[0].market_date),
                            "model": name,
                            "training_start": str(train.iloc[0].market_date),
                            "training_end": str(train.iloc[-1].market_date),
                            "features": [],
                            "actual_return": float(current.iloc[0].intraday_return),
                            "selection_position": None,
                            "probability_source": (
                                "constant_one"
                                if name == "always_up"
                                else "prior_120_sessions"
                            ),
                        }
                        if champion_probability is None:
                            rows.append(
                                {**baseline, "status": "NO_PREDICTION_MISSING"}
                            )
                            continue
                        point, probability = baselines[name]
                        rows.append(
                            {
                                **baseline,
                                "status": "OK",
                                "predicted_return": point,
                                "probability_up": probability,
                            }
                        )
            target.write_text(
                json.dumps(
                    {
                        "ticker": ticker,
                        "evidence": dataset.availability_evidence,
                        "start": str(start),
                        "end": str(end),
                        "candidate_names": candidate_names,
                        "include_compact": include_compact,
                        "rows": rows,
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
            summary = []
            for name, group in pd.DataFrame(rows).groupby("model"):
                good = group.loc[group.status == "OK"]
                if good.empty:
                    summary.append({"model": name, "n": 0, "status": "NO_PREDICTIONS"})
                    continue
                error = good.predicted_return - good.actual_return
                summary.append(
                    {
                        "model": name,
                        "n": len(good),
                        "mae": float(np.abs(error).mean()),
                        "rmse": float(np.sqrt((error**2).mean())),
                        "direction": float(
                            (
                                (good.predicted_return > 0) == (good.actual_return > 0)
                            ).mean()
                        ),
                        "features": sorted({len(x) for x in group.features}),
                    }
                )
            print(
                json.dumps({"ticker": ticker, "status": "DONE", "summary": summary}),
                flush=True,
            )
    engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument(
        "--candidate-names",
        nargs="*",
        choices=ROBUST_CANDIDATES,
        default=(),
        help="Fixed research return challengers; production champion stays unchanged.",
    )
    parser.add_argument(
        "--skip-compact",
        action="store_true",
        help="Skip the unrelated nested feature-selection arm in return-model studies.",
    )
    args = parser.parse_args()
    database = args.database or os.environ.get("DATABASE_URL")
    if not database:
        parser.error("--database or DATABASE_URL is required")
    configured = [
        s.ticker for s in load_app_config("config").stocks.stocks if s.enabled
    ]
    run(
        database,
        args.output,
        args.start,
        args.end,
        args.tickers or configured,
        tuple(args.candidate_names),
        not args.skip_compact,
    )
