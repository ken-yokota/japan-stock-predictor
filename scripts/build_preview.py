"""Compute a prediction preview and record which indicators actually reached it.

The morning pipeline publishes to the database on its own schedule. This builds
the same numbers on demand and writes them to an artifact instead, so a
prediction can be inspected before it is published -- and, more importantly, so
the *inputs* can be inspected at all.

The second half is the point. A prediction that quietly lost its FX and futures
inputs looks exactly like one that used them; the difference lives in per-symbol
warnings that nothing surfaced. Two categories are distinguished here because
they mean opposite things:

* ``unavailable at cutoff`` — the value was not usable at the prediction time,
  so the model never saw it.
* ``FREE_UNVERIFIED`` — a data-quality label on a value that *was* used.

Each prediction also carries its own arithmetic. A Ridge prediction on
standardized inputs is exactly ``intercept + sum(coefficient * z)``, so every
predictor's yen-and-percent share of today's number can be read off directly
rather than inferred from the coefficient alone. A large coefficient on a
feature sitting at its average contributes nothing; the product is what moves
the prediction, and the product is what gets reported.

The decomposition is checked against the model's own output and the residual is
recorded, so a mismatch surfaces instead of being presented as an explanation.

Nothing is written to the database.

    python -m cli preview --prediction-date 2026-08-10
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy.orm import Session

from data.config import load_app_config
from data.env import EnvironmentSettings
from database.connection import create_database_engine
from services.dataset import PointInTimeDatasetBuilder
from services.prediction import PredictionService

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_OUTPUT = Path("artifacts/preview/latest.json")

# A warning is "the model never saw this" or "the model saw this, flagged".
EXCLUSION_MARKERS = ("unavailable at cutoff", "snapshot unavailable")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--prediction-date", type=date.fromisoformat, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _classify(warnings: set[str]) -> dict[str, Any]:
    """Split per-symbol warnings into "never used" and "used but labelled"."""

    excluded: dict[str, set[str]] = defaultdict(set)
    labelled: dict[str, set[str]] = defaultdict(set)
    for warning in warnings:
        match = re.match(r"^(?P<symbol>[\w.]+):\s*(?P<reason>.+)$", warning)
        if match is None:
            continue
        symbol, reason = match.group("symbol"), match.group("reason").strip()
        bucket = (
            excluded
            if any(marker in reason for marker in EXCLUSION_MARKERS)
            else labelled
        )
        bucket[symbol].add(reason)
    return {
        "excluded": {key: sorted(value) for key, value in sorted(excluded.items())},
        "quality_labelled": {
            key: sorted(value)
            for key, value in sorted(labelled.items())
            if key not in excluded
        },
    }


def _contributions(computed: Any, limit: int = 6) -> dict[str, Any]:
    """Decompose one prediction into per-feature contributions.

    Ridge predicts ``intercept + sum(coefficient * z)`` where ``z`` is the
    standardized feature. Multiplying the fitted coefficient by this session's
    standardized value gives each predictor's signed share of the number, in
    the same units as the prediction itself.
    """

    model = computed.model
    dataset = computed.dataset
    if model is None or dataset.current_frame.empty:
        return {}
    statistics = model.scaler_statistics("regression")
    if statistics is None:
        return {}

    coefficients = model.regression_coefficients()
    row = dataset.current_frame.iloc[0]
    parts: list[dict[str, Any]] = []
    for name in model.feature_names:
        raw = pd.to_numeric(pd.Series([row.get(name)]), errors="coerce").iloc[0]
        scale = statistics.scales.get(name, 1.0) or 1.0
        # A missing value was median-imputed before scaling, so it standardizes
        # to roughly zero and contributes roughly nothing. Recording it as zero
        # is honest; inventing a contribution for it would not be.
        standardized = (
            0.0 if pd.isna(raw) else (raw - statistics.means.get(name, 0.0)) / scale
        )
        parts.append(
            {
                "feature": name,
                "coefficient": coefficients.get(name, 0.0),
                "standardized_value": float(standardized),
                "contribution": float(coefficients.get(name, 0.0) * standardized),
            }
        )

    intercept = model.regression_intercept()
    rebuilt = intercept + sum(part["contribution"] for part in parts)
    predicted = computed.result.predicted_return
    parts.sort(key=lambda part: abs(part["contribution"]), reverse=True)
    return {
        "intercept": float(intercept),
        "reconstructed_return": float(rebuilt),
        "residual": (None if predicted is None else float(rebuilt - predicted)),
        "top": parts[:limit],
    }


def main() -> int:
    arguments = _parse_arguments()
    environment = EnvironmentSettings()
    config = load_app_config(arguments.config_dir)
    target = arguments.prediction_date or datetime.now(JST).date()

    engine = create_database_engine(environment.require_database_url())
    session = Session(engine)
    predictions: list[dict[str, Any]] = []
    failures: dict[str, str] = {}
    warnings: set[str] = set()
    try:
        service = PredictionService(PointInTimeDatasetBuilder(session, config), config)
        for stock in config.stocks.stocks:
            if not stock.enabled:
                continue
            try:
                computed = service.compute(stock.ticker, target)
            except Exception as error:
                failures[stock.ticker] = f"{type(error).__name__}: {str(error)[:160]}"
                continue
            result = computed.result
            warnings.update(str(item) for item in (result.warnings or ()))
            explanation = _contributions(computed)
            predictions.append(
                {
                    "ticker": stock.ticker,
                    "sector": stock.sector,
                    "status": result.status,
                    "signal": result.signal,
                    "predicted_return": result.predicted_return,
                    "probability_up": result.probability_up,
                    "reference_close": getattr(result, "reference_close", None),
                    "predicted_close": getattr(result, "predicted_close", None),
                    "explanation": explanation,
                }
            )
    finally:
        # Read-only by construction: the preview must never publish.
        session.rollback()
        session.close()

    signal_config = config.trading.signal
    indicators = _classify(warnings)
    report = {
        "generated_at": datetime.now(JST).isoformat(),
        "prediction_date": target.isoformat(),
        "published_to_database": False,
        "rule": {
            "return_threshold": signal_config.predicted_intraday_return_threshold,
            "probability_threshold": signal_config.probability_up_threshold,
        },
        "predictions": sorted(
            predictions,
            key=lambda row: (
                row["predicted_return"] is None,
                -(row["predicted_return"] or 0.0),
            ),
        ),
        "indicators": indicators,
        "failures": failures,
        "caveats": [
            "本番と同じコード・DB・設定で算出し、DBには保存していません。",
            "除外された指標はモデルが一度も見ていません。品質ラベルだけの指標は使われています。",
            "売買判断には使用しないでください。優位性はまだ確認されていません。",
        ],
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    buys = [row for row in predictions if row["signal"] == "BUY"]
    print(f"対象日 {target} / 予測 {len(predictions)}銘柄 / BUY {len(buys)}銘柄")
    print(f"除外された指標 {len(indicators['excluded'])}系列")
    print(f"品質ラベルのみ {len(indicators['quality_labelled'])}系列")
    if failures:
        print(f"失敗 {len(failures)}銘柄")
    print(f"出力: {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
