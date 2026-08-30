"""Each model family's own buy hurdle, derived from the replay.

A single 0.3% hurdle across ten families assumes they are on the same scale,
and they are not: the linear arms answer with a conditional mean that runs wide
of the median, the boosting arms answer with a fitted quantile, and the two
sequence models answer with a memorised one. The same number means a different
thing to each.

So the hurdles are derived per family by
``scripts.report_all_method_backtest`` and written here as a document the
morning mail reads. Two properties matter more than the numbers themselves:

* they are chosen on earlier sessions and scored on later ones, so a hurdle
  that only worked because it was picked after seeing the outcome shows up as
  a gap between the two columns rather than as a good-looking single figure
* until that file exists, every family reports "閾値未導出" and no verdict is
  printed. A default hurdle would be a number nobody measured, presented in
  the column where a measured one belongs
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path("docs/all_methods/thresholds.json")

# Below this many out-of-sample positions the hurdle rests on too little to be
# worth acting on, and the mail says so rather than printing a verdict.
MINIMUM_EVALUATION_POSITIONS = 10


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, dict[str, Any]]:
    """Every family's hurdle and the evidence behind it, or an empty mapping."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = payload.get("thresholds")
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        arm = entry.get("arm")
        threshold = entry.get("threshold")
        if not isinstance(arm, str) or not isinstance(threshold, int | float):
            continue
        out[arm] = {
            "threshold": float(threshold),
            "evaluation_positions": int(entry.get("evaluation_positions") or 0),
            "evaluation_win_rate": entry.get("evaluation_win_rate"),
            "derived_from": payload.get("from"),
            "derived_to": payload.get("to"),
        }
    return out


def verdict(
    arm: str,
    median: float | None,
    thresholds: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """This family's own call today, and what it rests on.

    Returns ``(判定, 根拠)``. A family with no derived hurdle gets no verdict:
    inventing one would put an unmeasured number in the column a measured one
    belongs in.
    """

    entry = thresholds.get(arm)
    if entry is None:
        return "—", "閾値未導出"
    if median is None:
        return "—", "予測なし"
    hurdle = float(entry["threshold"])
    positions = int(entry.get("evaluation_positions") or 0)
    call = "買い" if median > hurdle else "見送り"
    basis = f"閾値 {hurdle:+.1%}"
    if positions < MINIMUM_EVALUATION_POSITIONS:
        basis += f"（検証{positions}件・根拠薄い）"
    return call, basis
