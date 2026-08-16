"""Test whether one predictor set's trading record really beat another's.

The four sets separated by a factor of thirty in trading result while their rank
ICs were statistically indistinguishable, so the ranking metric cannot settle
this and the trading numbers have to be tested rather than compared. Aggregates
invite exactly the error this guards against: a profit factor of 2.51 against
1.77 looks decisive and rests on 87 and 104 trades over 63 sessions.

Pairing is by session. Each arm's daily return is the equally weighted mean of
what it held that day, and **a day it chose nothing is a zero, not a gap** -
sitting in cash is a decision the rule made and its return is part of the
record. Dropping those days would score each arm only on the days it liked,
which is how a selective rule flatters itself.

Two tests, because one assumption is doing work in each: a paired t-test on the
daily differences, and Wilcoxon signed-rank, which does not assume the fat tails
away. A bootstrap interval accompanies them so the size of any difference is
visible next to its significance.

Reads a dumped prediction file. It refits nothing and touches no network.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

BOOTSTRAP_DRAWS = 10_000
SEED = 20260816


def daily_returns(
    frame: pd.DataFrame,
    sessions: list[str],
    *,
    top_k: int | None,
    cost: float,
) -> pd.Series:
    """One return per session, zero on the days the rule stayed out."""

    if top_k is None:
        chosen = frame.loc[frame["signal"].astype(str).str.upper() == "BUY"]
    else:
        ranked = frame.sort_values("predicted_return", ascending=False)
        chosen = ranked.groupby("date", sort=False, group_keys=False).head(top_k)
    if chosen.empty:
        return pd.Series(0.0, index=sessions)
    net = chosen["actual_return"].astype(float) - cost
    return net.groupby(chosen["date"]).mean().reindex(sessions).fillna(0.0)


def _bootstrap(difference: np.ndarray) -> tuple[float, float]:
    generator = np.random.default_rng(SEED)
    draws = generator.choice(
        difference, size=(BOOTSTRAP_DRAWS, difference.size), replace=True
    )
    means = draws.mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def compare(left: pd.Series, right: pd.Series) -> dict[str, float | int | None]:
    """Paired comparison of two arms' daily records over the same sessions."""

    from scipy.stats import ttest_rel, wilcoxon  # type: ignore[import-untyped]

    difference = np.asarray(left - right, dtype=float)
    differing = int(np.count_nonzero(difference))
    result: dict[str, float | int | None] = {
        "sessions": int(difference.size),
        "sessions_differing": differing,
        "mean_daily_delta": float(difference.mean()),
        "total_delta": float(difference.sum()),
    }
    if differing < 2 or float(difference.std(ddof=1) or 0.0) == 0.0:
        result["t_p"] = None
        result["wilcoxon_p"] = None
        result["ci_low"] = result["ci_high"] = 0.0
        return result

    result["t_p"] = float(ttest_rel(left, right).pvalue)
    try:
        result["wilcoxon_p"] = float(wilcoxon(difference).pvalue)
    except ValueError:
        result["wilcoxon_p"] = None
    low, high = _bootstrap(difference)
    result["ci_low"], result["ci_high"] = low, high
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--baseline", default="production")
    parser.add_argument("--rule", default="threshold")
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    arguments = parser.parse_args(argv)

    frame = pd.read_csv(arguments.predictions)
    frame["date"] = frame["date"].astype(str)
    sessions = sorted(frame["date"].unique())
    cost = 2.0 * (arguments.commission_bps + arguments.slippage_bps) / 10_000.0
    arms = {
        str(name): daily_returns(
            group, sessions, top_k=arguments.top_k, cost=cost
        )
        for name, group in frame.groupby("arm")
    }
    if arguments.baseline not in arms:
        print(f"baseline {arguments.baseline} is not in the file")
        return 2

    print(f"rule      : {arguments.rule}")
    print(f"sessions  : {len(sessions)}")
    print(f"baseline  : {arguments.baseline}")
    others = [name for name in arms if name != arguments.baseline]
    threshold = 0.05 / max(1, len(others))
    print(f"補正線    : p < {threshold:.4f}  （{len(others)}比較のBonferroni）")
    print("")

    base = arms[arguments.baseline]
    print(f"  {arguments.baseline:22} 合計 {base.sum():+.4f}")
    for name in others:
        stats = compare(arms[name], base)
        t_p = stats["t_p"]
        verdict = (
            "判定不能（検定不可）"
            if t_p is None
            else (
                "有意"
                if t_p < threshold
                else "有意差なし（同等の証明ではない）"
            )
        )
        print("")
        print(f"=== {arguments.baseline} → {name} ===")
        print(f"  合計損益差   : {stats['total_delta']:+.4f}")
        print(f"  日次平均差   : {stats['mean_daily_delta']:+.6f}")
        print(f"  95%CI(bootstrap): [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}]")
        print(f"  対応t検定    : p={t_p}")
        print(f"  Wilcoxon     : p={stats['wilcoxon_p']}")
        print(f"  差の出た日   : {stats['sessions_differing']} / {stats['sessions']}")
        print(f"  判定         : {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
