"""Audit stored forecasts that were published by the 08:30 JST cutoff.

This is a descriptive audit of already observed history, never a sealed holdout
or a permission to select new thresholds. Configuration versions stay separate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from data.config import load_app_config
from research.probability_calibration import calibrate_oos, probability_metrics


def eligible_publications(raw: pd.DataFrame) -> pd.DataFrame:
    """Keep the last available morning decision per ticker and session.

    Generation time is insufficient: a forecast created before the open but
    published after the operational cutoff was unavailable at 08:30. Missing
    publication metadata fails closed rather than inheriting the older cohort.
    """

    required = {
        "prediction_date",
        "published_at",
        "cutoff_at",
        "run_type",
        "prediction_set_status",
        "status",
        "actual_intraday_return",
        "ticker",
    }
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"publication audit source missing {sorted(missing)}")

    published = pd.to_datetime(raw.published_at, utc=True, errors="coerce")
    cutoff = pd.to_datetime(raw.cutoff_at, utc=True, errors="coerce")
    valid = published.notna() & cutoff.notna() & (published <= cutoff)
    valid &= (raw.run_type == "MORNING") & (raw.prediction_set_status == "READY")
    valid &= raw.status == "SUCCESS"
    candidates = raw.loc[valid].copy()
    candidates["_publication_order"] = published.loc[valid]
    order = ["_publication_order"] + [
        column
        for column in ("prediction_set_id", "prediction_id")
        if column in candidates.columns
    ]
    latest = (
        candidates.sort_values(order, kind="stable")
        .drop_duplicates(["ticker", "prediction_date"], keep="last")
    )
    # A later published forecast supersedes an older one even while its
    # outcome is pending. The old labelled row must not re-enter the cohort.
    return latest.loc[latest.actual_intraday_return.notna()].drop(
        columns="_publication_order"
    )


def evaluate(group: pd.DataFrame, cost_bp: int = 0) -> dict[str, object]:
    y = group.actual_return.to_numpy(float)
    prediction = group.predicted_return.to_numpy(float)
    p = group.probability_up.to_numpy(float)
    good = np.isfinite(prediction) & np.isfinite(y)
    selected = group.loc[group.buy & np.isfinite(y)].copy()
    net = selected.actual_return.to_numpy(float) - cost_bp / 10000
    gross = selected.actual_return.to_numpy(float)
    gains, losses = net[net > 0].sum(), -net[net < 0].sum()
    daily = selected.assign(net=net).groupby("date").net.mean()
    # Include no-trade dates as cash for the equal-weight active-day portfolio.
    daily = daily.reindex(sorted(group.date.unique()), fill_value=0)
    equity = (1 + daily).cumprod()
    drawdown = equity / equity.cummax().clip(lower=1) - 1
    sd = float(daily.std(ddof=1)) if len(daily) > 1 else 0
    downside = float(np.sqrt(np.mean(np.minimum(daily, 0) ** 2))) if len(daily) else 0
    errors = prediction[good] - y[good]
    corr = (
        pd.Series(prediction[good]).corr(pd.Series(y[good]))
        if good.sum() > 2
        else np.nan
    )
    rank = (
        pd.Series(prediction[good]).corr(pd.Series(y[good]), method="spearman")
        if good.sum() > 2
        else np.nan
    )
    low = group.interval_low.to_numpy(float)
    high = group.interval_high.to_numpy(float)
    band = np.isfinite(low) & np.isfinite(high) & (low <= high)
    result = {
        "samples": len(group),
        "sessions": group.date.nunique(),
        "return_predictions": int(good.sum()),
        "mae": float(np.mean(np.abs(errors))) if len(errors) else None,
        "rmse": float(np.sqrt(np.mean(errors**2))) if len(errors) else None,
        "pearson": float(corr) if np.isfinite(corr) else None,
        "spearman": float(rank) if np.isfinite(rank) else None,
        "direction_accuracy": float(np.mean((prediction[good] > 0) == (y[good] > 0)))
        if good.any()
        else None,
        **probability_metrics((y > 0).astype(float), p),
        "interval_samples": int(band.sum()),
        "interval80_coverage": float(
            np.mean((y[band] >= low[band]) & (y[band] <= high[band]))
        )
        if band.any()
        else None,
        "interval80_mean_width": float(np.mean(high[band] - low[band]))
        if band.any()
        else None,
        "cost_bp": cost_bp,
        "trades": len(net),
        "wins": int((net > 0).sum()),
        "losses": int((net < 0).sum()),
        "win_rate": float(np.mean(net > 0)) if len(net) else None,
        "gross_return_sum": float(gross.sum()),
        "net_return_sum": float(net.sum()),
        "pf": float(gains / losses) if losses > 0 else None,
        "pf_status": "FINITE" if losses > 0 else "NO_LOSSES_OR_NO_TRADES",
        "expectancy": float(net.mean()) if len(net) else None,
        "sharpe": float(daily.mean() / sd * np.sqrt(252)) if sd > 0 else None,
        "sortino": float(daily.mean() / downside * np.sqrt(252))
        if downside > 0
        else None,
        "max_drawdown": float(drawdown.min()) if len(drawdown) else None,
        "sample_status": "LOW_SAMPLE" if len(net) < 20 else "DESCRIPTIVE_ONLY",
    }
    # Resample entire trading days, retaining within-day cross-stock dependence.
    days = selected.groupby("date").actual_return.agg(["sum", "count"])
    ci = None
    if len(days) >= 2:
        rng = np.random.default_rng(42)
        indices = rng.integers(0, len(days), size=(1000, len(days)))
        counts = days["count"].to_numpy()[indices].sum(axis=1)
        means = days["sum"].to_numpy()[indices].sum(axis=1) / counts - cost_bp / 10000
        ci = np.quantile(means, [0.025, 0.975]).tolist()
    result["expectancy_date_bootstrap_ci95"] = ci
    return result


def audit(source: Path, destination: Path) -> dict[str, object]:
    raw = pd.DataFrame(json.loads(source.read_text()))
    raw["date"] = raw.prediction_date
    live = eligible_publications(raw)
    if live.empty:
        raise ValueError("no settled pre-cutoff morning predictions in audit source")
    sectors = {s.ticker: s.sector for s in load_app_config("config").stocks.stocks}
    rows = []
    for item in live.to_dict("records"):
        base = {
            "date": item["date"],
            "ticker": item["ticker"],
            "sector": sectors[item["ticker"]],
            "feature_version": item["config_hash"],
            "cutoff_at": item["cutoff_at"],
            "outcome_available_at": item["created_at"],
            "actual_return": float(item["actual_intraday_return"]),
        }
        arms = [
            {
                "name": "champion",
                "status": "OK",
                "predicted_return": item["predicted_intraday_return"],
                "probability_up": item["probability_up"],
                "distribution": item.get("return_distribution"),
            },
            *(item.get("arm_predictions") or []),
        ]
        for arm in arms:
            if arm["status"] != "OK":
                continue

            def number(value: object) -> float:
                if value is not None and not isinstance(value, int | float | str):
                    raise ValueError("non-numeric prediction record")
                return float(value) if value is not None else float("nan")

            prediction, probability = (
                number(arm.get("predicted_return")),
                number(arm.get("probability_up")),
            )
            levels = {
                q["quantile"]: q["return"]
                for q in (arm.get("distribution") or {}).get("levels", [])
            }
            # Generic saved bounds can be 90% or 95%; only explicit P10/P90
            # support an 80% coverage claim. Missing quantiles remain unknown.
            low, high = number(levels.get(0.1)), number(levels.get(0.9))
            rows.append(
                {
                    **base,
                    "model": arm["name"],
                    "predicted_return": prediction,
                    "probability_up": probability,
                    "interval_low": low,
                    "interval_high": high,
                    "buy": (
                        prediction > float(item["return_threshold"])
                        and probability >= float(item["probability_threshold"])
                    ),
                }
            )
    frame = pd.DataFrame(rows)
    groups = []
    for keys, group in frame.groupby(["feature_version", "ticker", "model"]):
        groups.append(
            dict(zip(["feature_version", "ticker", "model"], keys, strict=True))
            | evaluate(group)
        )
    aggregate = []
    for keys, group in frame.groupby(["feature_version", "model"]):
        for cost in (0, 5, 10, 15, 20):
            aggregate.append(
                {"feature_version": keys[0], "model": keys[1], **evaluate(group, cost)}
            )
    calibrations = []
    champion = frame.loc[frame.model == "champion"]
    for method in ("sigmoid", "isotonic"):
        for scope in ("global", "sector", "ticker"):
            calibrated = calibrate_oos(champion, method=method, scope=scope)
            for version, group in calibrated.groupby("feature_version"):
                changed = group.calibration_pairs >= 100
                subset = group.loc[changed]
                calibrations.append(
                    {
                        "feature_version": version,
                        "method": method,
                        "scope": scope,
                        "calibrated_rows": int(changed.sum()),
                        "raw_matched": probability_metrics(
                            (subset.actual_return.to_numpy(float) > 0).astype(float),
                            subset.probability_up.to_numpy(float),
                        ),
                        "calibrated_matched": probability_metrics(
                            (subset.actual_return.to_numpy(float) > 0).astype(float),
                            subset.calibrated_probability.to_numpy(float),
                        ),
                    }
                )
    result = {
        "evidence": "OBSERVED_LIVE_HISTORY_NOT_UNUSED_HOLDOUT",
        "source_rows": len(raw),
        "eligible_live_rows": len(live),
        "eligible_sessions": int(live.date.nunique()),
        "excluded_rows": len(raw) - len(live),
        "exclusion": (
            "not published by 08:30 cutoff, non-MORNING, non-READY, "
            "unsettled, failed, or duplicate ticker/date"
        ),
        "cohort_rule": (
            "READY MORNING, published_at <= cutoff_at, latest pre-cutoff ticker/date"
        ),
        "cost_basis": "fixed round-trip bp; equal weight active positions; no leverage",
        "arm_trade_rule": (
            "common frozen dated champion thresholds for comparison; "
            "not optimized per arm"
        ),
        "per_ticker_model": groups,
        "aggregate_costs": aggregate,
        "calibration": calibrations,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.source, args.output)
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "source_rows",
                    "eligible_live_rows",
                    "eligible_sessions",
                    "excluded_rows",
                )
            }
        )
    )
