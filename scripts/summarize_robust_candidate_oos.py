"""Score prespecified challengers only on shared estimated-PIT sessions.

This reads the per-ticker output of ``run_quant_audit_oos``. It is a
development comparison of historical backfills, not a sealed forward sample
or a model-promotion decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.config import load_app_config
from research.live_audit import evaluate


def summarize(directory: Path) -> dict[str, object]:
    config = load_app_config("config")
    tickers = [stock.ticker for stock in config.stocks.stocks if stock.enabled]
    source = []
    candidate_names: tuple[str, ...] | None = None
    window: tuple[str, str] | None = None
    for ticker in tickers:
        path = directory / f"{ticker}.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload["ticker"] != ticker or payload["evidence"] != "ESTIMATED_BACKFILL":
            raise ValueError(f"invalid estimated-PIT source for {ticker}")
        names = tuple(payload.get("candidate_names", ()))
        if candidate_names is None:
            candidate_names = names
        elif candidate_names != names:
            raise ValueError("candidate lists differ across tickers")
        dated = (payload["start"], payload["end"])
        if window is None:
            window = dated
        elif window != dated:
            raise ValueError("study windows differ across tickers")
        source.extend(payload["rows"])
    if not candidate_names:
        raise ValueError("no research challengers were requested")
    if window is None:
        raise ValueError("no study window was found")

    models = ("champion_replay", *candidate_names)
    frame = pd.DataFrame(source)
    frame = frame.loc[frame.model.isin(models)].copy()
    valid = frame.loc[frame.status == "OK"].copy()
    if valid.duplicated(["model", "ticker", "date"]).any():
        raise ValueError("duplicate model/ticker/session rows")
    keys = {
        name: set(zip(group.ticker, group.date, strict=True))
        for name, group in valid.groupby("model")
    }
    if set(keys) != set(models):
        raise ValueError("one or more models produced no forecasts")
    shared = set.intersection(*(keys[name] for name in models))
    if not shared:
        raise ValueError("no common model/ticker/session rows")
    paired = {
        (row["model"], row["ticker"], row["date"]): row
        for row in valid.to_dict("records")
    }
    for ticker, day in shared:
        champion = paired[("champion_replay", ticker, day)]
        if str(champion["training_end"]) >= str(day):
            raise ValueError("champion training reaches the scored session")
        for name in candidate_names:
            candidate = paired[(name, ticker, day)]
            if (
                candidate["features"] != champion["features"]
                or candidate["training_start"] != champion["training_start"]
                or candidate["training_end"] != champion["training_end"]
                or candidate["actual_return"] != champion["actual_return"]
                or candidate["probability_up"] != champion["probability_up"]
                or candidate["probability_source"] != "champion_logistic"
            ):
                raise ValueError("challenger did not share champion inputs")

    return_threshold = config.trading.signal.predicted_intraday_return_threshold
    probability_threshold = config.trading.signal.probability_up_threshold
    if return_threshold is None or probability_threshold is None:
        raise ValueError("frozen BUY thresholds are required")

    metrics: dict[str, object] = {}
    for name in models:
        group = valid.loc[valid.model == name].copy()
        selected = [
            (ticker, day) in shared
            for ticker, day in zip(group.ticker, group.date, strict=True)
        ]
        group = group.loc[selected].copy()
        group["buy"] = (group.predicted_return > return_threshold) & (
            group.probability_up >= probability_threshold
        )
        group["interval_low"] = np.nan
        group["interval_high"] = np.nan
        metrics[name] = {
            str(cost_bp): evaluate(group, cost_bp=cost_bp)
            for cost_bp in (0, 5, 10, 15, 20)
        }

    paired_mae: dict[str, object] = {}
    for name in candidate_names:
        differences = pd.DataFrame(
            [
                {
                    "date": day,
                    "mae_difference": abs(
                        paired[(name, ticker, day)]["predicted_return"]
                        - paired[(name, ticker, day)]["actual_return"]
                    )
                    - abs(
                        paired[("champion_replay", ticker, day)]["predicted_return"]
                        - paired[("champion_replay", ticker, day)]["actual_return"]
                    ),
                }
                for ticker, day in sorted(shared)
            ]
        )
        daily = (
            differences.groupby("date").mae_difference.agg(["sum", "count"])
        )
        interval = None
        if len(daily) >= 2:
            rng = np.random.default_rng(42)
            indices = rng.integers(0, len(daily), size=(1000, len(daily)))
            sampled_sum = daily["sum"].to_numpy()[indices].sum(axis=1)
            sampled_count = daily["count"].to_numpy()[indices].sum(axis=1)
            interval = np.quantile(
                sampled_sum / sampled_count, [0.025, 0.975]
            ).tolist()
        paired_mae[name] = {
            "candidate_minus_champion": float(differences.mae_difference.mean()),
            "date_bootstrap_ci95": interval,
        }

    return {
        "evidence": "ESTIMATED_BACKFILL_RESEARCH_NOT_LIVE_AND_NOT_SEALED",
        "models": models,
        "tickers": len(tickers),
        "window": {"start": window[0], "end": window[1]},
        "common_pairs": len(shared),
        "common_sessions": len({day for _, day in shared}),
        "dropped_from_common": {
            name: len(keys[name] - shared) for name in models
        },
        "non_ok_status_counts": {
            name: {
                str(status): int(count)
                for status, count in frame.loc[
                    (frame.model == name) & (frame.status != "OK"), "status"
                ]
                .value_counts()
                .items()
            }
            for name in models
        },
        "frozen_buy_thresholds": {
            "predicted_return": return_threshold,
            "probability_up": probability_threshold,
        },
        "candidate_probability_source": "champion_logistic",
        "cost_unit": "hypothetical_round_trip_basis_points",
        "metrics": metrics,
        "paired_mae_difference": paired_mae,
        "decision": "RESEARCH_ONLY_NO_PROMOTION",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = summarize(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "evidence": report["evidence"],
                "common_pairs": report["common_pairs"],
                "common_sessions": report["common_sessions"],
                "decision": report["decision"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
