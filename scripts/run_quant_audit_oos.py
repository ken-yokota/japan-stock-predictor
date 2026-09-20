"""Reproducible estimated-PIT comparison; never promotes a challenger.

The shared production dataset builder supplies features. Outer predictions use
120 earlier sessions, with nested sparse selection frozen for 20 predictions.
Only daily return/yield-change columns enter the compact candidate universe.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from data.config import load_app_config
from database.connection import create_database_engine
from models import train_ticker_model
from research.nested_selection import FrozenSelection, nested_select
from services.dataset import PointInTimeDatasetBuilder
from services.prediction import PredictionService


def run(
    database: str, output: Path, start: date, end: date, tickers: list[str]
) -> None:
    config = load_app_config("config")
    engine = (
        create_engine(database)
        if database.startswith("sqlite:")
        else create_database_engine(database)
    )
    output.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        builder = PointInTimeDatasetBuilder(session, config)
        settings = replace(
            PredictionService(builder, config)._model_config(),
            distribution_quantiles=(),
        )
        for ticker in tickers:
            target = output / f"{ticker}.json"
            if target.exists():
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
                if frozen is None or frozen.due(position):
                    selection = nested_select(
                        train.loc[:, candidates], train.intraday_return.to_numpy(float)
                    )
                    frozen = FrozenSelection(position, selection)
                for name, columns in (
                    ("champion_replay", all_names),
                    ("compact_nested_ridge", frozen.selection.names),
                ):
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
                        if name.startswith("compact")
                        else None,
                    }
                    if missing and name.startswith("compact"):
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
            target.write_text(
                json.dumps(
                    {
                        "ticker": ticker,
                        "evidence": dataset.availability_evidence,
                        "start": str(start),
                        "end": str(end),
                        "rows": rows,
                    },
                    indent=2,
                    allow_nan=False,
                )
            )
            summary = []
            for name, group in pd.DataFrame(rows).groupby("model"):
                good = group.loc[group.status == "OK"]
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
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--tickers", nargs="*")
    args = parser.parse_args()
    configured = [
        s.ticker for s in load_app_config("config").stocks.stocks if s.enabled
    ]
    run(args.database, args.output, args.start, args.end, args.tickers or configured)
