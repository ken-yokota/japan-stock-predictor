"""BUY-rule display and rolling coefficient summarization for the UI.

Two questions this answers for a reader of the dashboard:

* *What actually counts as a BUY?* The configured rule lives in
  ``config/trading.yaml``, but the rule that produced the saved signals is the
  one stored on each prediction row. Both are surfaced, and a mismatch is
  reported rather than hidden -- an edited config that has not been through a
  morning run must not be displayed as if it were already in force.
* *How did each indicator behave over roughly the last six months?* Rolling fits
  are summarized with the same ``scoring.stability`` code the pipeline uses, so
  the dashboard cannot drift away from the stored stability scores.

Everything here is pure: it reads a local YAML file and does arithmetic over
rows the caller already fetched. No provider, model fitting, or delivery.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from dashboard.presenters import as_number
from scoring.stability import CoefficientStability, calculate_coefficient_stability

TRADING_CONFIG_PATH = Path("config/trading.yaml")


@dataclass(frozen=True, slots=True)
class BuyRule:
    """The BUY condition as configured, in display units."""

    return_threshold: float
    probability_threshold: float
    capital_per_stock: float
    lot_size: int
    commission_bps_per_side: float
    slippage_bps_per_side: float
    source: str

    @property
    def summary(self) -> str:
        """Return the one-line rule a reader can check a signal against."""

        return (
            f"予測リターン > {self.return_threshold * 100:.2f}%"
            f" かつ 上昇確率 >= {self.probability_threshold * 100:.0f}%"
        )


def load_configured_buy_rule(path: Path | None = None) -> BuyRule | None:
    """Read the configured BUY rule for display, or ``None`` if unreadable.

    This is a display-only read. ``data.config`` owns strict validation for the
    pipeline; duplicating that here would give the dashboard a second opinion
    about what the config means. If the file is missing or malformed the caller
    shows the stored thresholds alone rather than guessing.
    """

    config_path = path or TRADING_CONFIG_PATH
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, Mapping):
        return None

    signal = raw.get("signal")
    position = raw.get("position")
    costs = raw.get("costs")
    if not all(isinstance(part, Mapping) for part in (signal, position, costs)):
        return None
    assert isinstance(signal, Mapping)
    assert isinstance(position, Mapping)
    assert isinstance(costs, Mapping)

    values = (
        as_number(signal.get("predicted_intraday_return_threshold")),
        as_number(signal.get("probability_up_threshold")),
        as_number(position.get("capital_per_stock_jpy")),
        as_number(position.get("lot_size")),
        as_number(costs.get("commission_bps_per_side")),
        as_number(costs.get("slippage_bps_per_side")),
    )
    if any(value is None for value in values):
        return None
    return BuyRule(
        return_threshold=float(values[0] or 0.0),
        probability_threshold=float(values[1] or 0.0),
        capital_per_stock=float(values[2] or 0.0),
        lot_size=int(values[3] or 0),
        commission_bps_per_side=float(values[4] or 0.0),
        slippage_bps_per_side=float(values[5] or 0.0),
        source=str(config_path),
    )


def buy_rule_mismatches(
    rule: BuyRule | None,
    applied_rows: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Return human-readable differences between config and stored signals."""

    if rule is None:
        return []
    messages: list[str] = []
    for row in applied_rows:
        stored_return = as_number(row.get("return_threshold"))
        stored_probability = as_number(row.get("probability_threshold"))
        if stored_return is None or stored_probability is None:
            continue
        if (
            abs(stored_return - rule.return_threshold) < 1e-12
            and abs(stored_probability - rule.probability_threshold) < 1e-12
        ):
            continue
        messages.append(
            f"{row.get('first_date', '—')}〜{row.get('last_date', '—')}の"
            f"{row.get('prediction_count', 0)}件は "
            f"予測リターン > {stored_return * 100:.2f}% / "
            f"上昇確率 >= {stored_probability * 100:.0f}% で判定されています。"
        )
    return messages


def coefficient_matrix(rows: Sequence[Mapping[str, Any]]) -> pd.DataFrame:
    """Pivot coefficient rows into one row per fit, one column per feature.

    Rows arrive newest-first. ``scoring.stability`` takes the *tail* of a
    history, so the frame is returned oldest-first to make ``lookback`` mean
    "the most recent N fits".
    """

    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(
        [
            {
                "model_run_id": str(row.get("model_run_id", "")),
                "training_end": row.get("training_end"),
                "feature_name": str(row.get("feature_name", "")),
                "coefficient": as_number(row.get("coefficient")),
            }
            for row in rows
        ]
    )
    frame = frame.loc[frame["coefficient"].notna()]
    if frame.empty:
        return pd.DataFrame()
    wide = frame.pivot_table(
        index=["training_end", "model_run_id"],
        columns="feature_name",
        values="coefficient",
        aggfunc="last",
    )
    return wide.sort_index()


def summarize_coefficients(
    rows: Sequence[Mapping[str, Any]], *, lookback: int = 120
) -> tuple[dict[str, CoefficientStability], int]:
    """Summarize the newest ``lookback`` fits, returning the fits actually used.

    The second element is the number of distinct fits available. A summary over
    3 fits and a summary over 120 look identical otherwise, and the difference
    decides whether the numbers mean anything.
    """

    matrix = coefficient_matrix(rows)
    if matrix.empty:
        return {}, 0
    used = matrix.tail(lookback)
    return calculate_coefficient_stability(used, lookback=lookback), len(used)


def coefficient_summary_rows(
    report: Mapping[str, CoefficientStability],
    *,
    top: int = 30,
) -> list[dict[str, object]]:
    """Order features by mean absolute influence for display."""

    ordered = sorted(
        report.values(),
        key=lambda entry: abs(entry.mean_coefficient),
        reverse=True,
    )
    return [
        {
            "指標 (Feature)": entry.feature_name,
            "平均係数": f"{entry.mean_coefficient:+.5f}",
            "向き": (
                "上げ要因"
                if entry.mean_coefficient > 0
                else "下げ要因"
                if entry.mean_coefficient < 0
                else "中立"
            ),
            "標準偏差": f"{entry.standard_deviation:.5f}",
            "符号一致率": f"{entry.sign_consistency * 100:.0f}%",
            "安定性": f"{entry.stability_score:.2f}",
            "観測回数": entry.observation_count,
        }
        for entry in ordered[:top]
    ]
