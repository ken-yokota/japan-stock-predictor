#!/usr/bin/env python3
"""Compare walk-forward arms side by side, on one window, with one metric set.

Two arms differing by 0.02 in rank IC is only interesting if everything else
about them was identical: the same sessions, the same tickers, the same model
code, the same trading rule. This refuses to print a comparison whose arms do
not agree on the window, because a difference between windows wearing the label
of a difference between feature sets is worse than no comparison.

The columns are ordered by how much sample stands behind them, and the report
says so: the prediction layer decides, the selection layer decides, and the
trading layer is printed and disclaimed.

    python -m scripts.compare_oos_arms artifacts/oos/*.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from research.evaluation import Evaluation, evaluate, from_research_rows
from research.selection_rules import standard_rules


def _sectors() -> dict[str, str]:
    """Ticker to sector, so a sector cap has something to cap on."""

    try:
        from data.config import load_app_config

        return {s.ticker: s.sector for s in load_app_config().stocks.stocks}
    except Exception:
        return {}


@dataclass(frozen=True, slots=True)
class Arm:
    """One walk-forward run, with the shape of its inputs kept beside its score."""

    name: str
    window: tuple[str, str]
    sessions: int
    training_rows: int
    feature_counts: tuple[int, ...]
    ridge_alphas: Counter[float]
    logistic_cs: Counter[float]
    evaluation: Evaluation
    rows: list[Any]

    @property
    def features(self) -> float:
        return float(np.mean(self.feature_counts)) if self.feature_counts else 0.0

    @property
    def feature_row_ratio(self) -> float:
        return self.features / self.training_rows if self.training_rows else 0.0


def load_arm(path: Path) -> Arm:
    payload = json.loads(path.read_text(encoding="utf-8"))
    predictions = payload.get("predictions", [])
    rows = from_research_rows(predictions, sectors=_sectors())
    counts = tuple(
        int(item["feature_count"])
        for item in predictions
        if item.get("feature_count") is not None
    )
    if not counts:
        # Runs made before the fit recorded its own column count. The
        # coefficients are one row per feature per fit, so counting the
        # distinct names per ticker recovers it rather than reporting zero.
        per_ticker: dict[str, set[str]] = {}
        for item in payload.get("coefficients", []):
            per_ticker.setdefault(str(item["ticker"]), set()).add(str(item["feature"]))
        counts = tuple(len(names) for names in per_ticker.values())
    training = [
        int(item["training_sessions"])
        for item in predictions
        if item.get("training_sessions") is not None
    ]
    top_k = payload.get("top_k")
    name = str(payload.get("feature_set", path.stem))
    if top_k:
        name = f"{name} k={top_k}"
    return Arm(
        name=name,
        window=(str(payload.get("from_date")), str(payload.get("to_date"))),
        sessions=int(payload.get("sessions", 0)),
        training_rows=int(np.median(training)) if training else 0,
        feature_counts=counts,
        ridge_alphas=Counter(
            float(item["ridge_alpha"])
            for item in predictions
            if item.get("ridge_alpha") is not None
        ),
        logistic_cs=Counter(
            float(item["logistic_c"])
            for item in predictions
            if item.get("logistic_c") is not None
        ),
        evaluation=evaluate(rows, label=name),
        rows=rows,
    )


def _dominant(counter: Counter[float]) -> str:
    """The value chosen most often, and how often. A pin at a grid edge shows here."""

    if not counter:
        return "—"
    value, count = counter.most_common(1)[0]
    total = sum(counter.values())
    return f"{value:g} ({count / total:.0%})"


def _num(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:+.{digits}f}"


def _pct(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:+.{digits}%}"


ROWS: tuple[tuple[str, str], ...] = (
    ("── 入力の形 ──", ""),
    ("Sessions", "sessions"),
    ("Training rows", "training_rows"),
    ("Features", "features"),
    ("Feature / Row", "ratio"),
    ("Ridge alpha", "alpha"),
    ("Logistic C", "logistic_c"),
    ("── Model Layer ──", ""),
    ("Predictions", "count"),
    ("MAE", "mae"),
    ("RMSE", "rmse"),
    ("Pearson", "pearson"),
    ("Spearman", "spearman"),
    ("Direction accuracy", "direction"),
    ("Prediction mean", "pred_mean"),
    ("Prediction SD", "pred_sd"),
    ("Actual mean", "actual_mean"),
    ("Actual SD", "actual_sd"),
    ("Calibration intercept", "intercept"),
    ("Calibration slope", "slope"),
    ("── Selection Layer ──", ""),
    ("Daily Rank IC", "rank_ic"),
    ("Rank IC t", "rank_ic_t"),
    ("Universe 平均", "universe"),
    ("Top1 - Universe", "top1"),
    ("Top3 - Universe", "top3"),
    ("Top5 - Universe", "top5"),
    ("Top5 alpha t", "top5_t"),
    ("Top5 - Bottom5", "spread"),
    ("── Trading Layer ──", ""),
    ("Trades", "trades"),
    ("Gross", "gross"),
    ("Net", "net"),
    ("Profit factor", "pf"),
    ("Expectancy", "expectancy"),
    ("Max drawdown", "drawdown"),
)


def _value(arm: Arm, key: str) -> str:
    model = arm.evaluation.model
    selection = arm.evaluation.selection
    trading = arm.evaluation.trading
    predicted = np.array([r.predicted_return for r in arm.rows], dtype=float)
    actual = np.array([r.actual_return for r in arm.rows], dtype=float)
    return {
        "sessions": f"{arm.sessions:,}",
        "training_rows": f"{arm.training_rows:,}",
        "features": f"{arm.features:.0f}",
        "ratio": f"{arm.feature_row_ratio:.2f}",
        "alpha": _dominant(arm.ridge_alphas),
        "logistic_c": _dominant(arm.logistic_cs),
        "count": f"{model.count:,}",
        "mae": f"{model.mae:.4%}",
        "rmse": f"{model.rmse:.4%}",
        "pearson": _num(model.pearson),
        "spearman": _num(model.spearman),
        "direction": f"{model.direction_accuracy:.2%}",
        "pred_mean": _pct(model.predicted_mean),
        "pred_sd": f"{predicted.std(ddof=1):.3%}" if len(predicted) > 1 else "—",
        "actual_mean": _pct(model.actual_mean),
        "actual_sd": f"{actual.std(ddof=1):.3%}" if len(actual) > 1 else "—",
        "intercept": _num(model.calibration_intercept, 5),
        "slope": _num(model.calibration_slope),
        "rank_ic": _num(selection.rank_ic_mean),
        "rank_ic_t": _num(selection.rank_ic_t, 2),
        "universe": _pct(selection.universe_mean),
        "top1": _pct(selection.top1_alpha),
        "top3": _pct(selection.top3_alpha),
        "top5": _pct(selection.top5_alpha),
        "top5_t": _num(selection.top5_alpha_t, 2),
        "spread": _pct(selection.top_bottom_spread),
        "trades": f"{trading.trades:,}",
        "gross": f"{trading.gross_jpy:+,.0f}",
        "net": f"{trading.net_jpy:+,.0f}",
        "pf": _num(trading.profit_factor, 2),
        "expectancy": f"{trading.expectancy_jpy:+,.0f}"
        if trading.expectancy_jpy is not None
        else "—",
        "drawdown": f"{trading.max_drawdown_jpy:+,.0f}",
    }.get(key, "")


def render(arms: Sequence[Arm]) -> str:
    width = max((len(a.name) for a in arms), default=10)
    width = max(width, 14)
    lines = [
        "同一期間・同一walk-forward条件での比較",
        f"期間: {arms[0].window[0]} 〜 {arms[0].window[1]}",
        "",
        f"{'':<24}" + "".join(f"{a.name:>{width + 2}}" for a in arms),
    ]
    for label, key in ROWS:
        if not key:
            lines.append(label)
            continue
        lines.append(
            f"  {label:<22}" + "".join(f"{_value(a, key):>{width + 2}}" for a in arms)
        )
    lines.append("")
    lines.append(
        "採否は Model / Selection で判定します。Trading は標本が最も小さく、"
        "参考値です。"
    )
    return "\n".join(lines)


def render_rules(arms: Sequence[Arm], cost: float) -> str:
    lines = ["", "選別ルール別の累積（コスト控除後・営業日単位）", ""]
    width = max(max((len(a.name) for a in arms), default=10), 14)
    names = [r.name for r in standard_rules(arms[0].rows, cost_per_position=cost)]
    tables = {
        arm.name: {
            r.name: r for r in standard_rules(arm.rows, cost_per_position=cost)
        }
        for arm in arms
    }
    lines.append(f"{'':<36}" + "".join(f"{a.name:>{width + 2}}" for a in arms))
    for name in names:
        lines.append(
            f"  {name:<34}"
            + "".join(
                f"{tables[a.name][name].total_return:>{width + 1}.2%} " for a in arms
            )
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--cost", type=float, default=0.00165)
    parser.add_argument("--rules", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    arms = [load_arm(path) for path in args.artifacts]
    windows = {arm.window for arm in arms}
    if len(windows) > 1:
        # A difference between windows wearing the label of a difference
        # between feature sets is worse than no comparison at all.
        print("比較できません。アームの期間が一致していません:")
        for arm in arms:
            print(f"  {arm.name}: {arm.window[0]} 〜 {arm.window[1]}")
        return 1
    print(render(arms))
    if args.rules:
        print(render_rules(arms, args.cost))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
