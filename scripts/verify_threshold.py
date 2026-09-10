"""Re-judge the probability threshold on sessions the choice never saw.

0.625 was picked by sweeping the 174 BUYs settled up to 2026-09-10 and reading
off where the profit factor was steadiest. That is selection on the evaluation
sample, and the 1.130 it reported there is optimistic by an unknown amount --
the plateau shape and the split-half agreement argue the effect is real, but
neither is evidence from outside the sample. This provides that evidence, or
declines to.

Three rules, and they are the whole point:

* **Sessions strictly after the decision date.** Reading one earlier scores the
  choice on the record it was made from, which is what the original sweep
  already did.
* **Two thresholds, both named in the record, neither recomputed.** Sweeping
  again here and adopting the new winner would repeat the same selection on
  fresh data and destroy the only clean sample there is. The script has no
  option to sweep, deliberately.
* **No verdict below the session floor.** A handful of sessions cannot separate
  a profit factor of 1.13 from one of 0.98, and printing a winner anyway is how
  a coin flip gets promoted to a rule.

What makes this measurable at all: the close pipeline writes an actual result
for *every* prediction, not only the ones that became BUYs. So the trades the
raised threshold declines are still on the record with their open and close,
and the counterfactual stays computable for as long as the rows exist.

Raising a threshold only removes trades, so the two arms differ by exactly the
band between them. The test is therefore whether that band loses money, one
observation per session -- 22 tickers on one morning share a market and are
not 22 independent draws.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy import stats  # type: ignore[import-untyped]
from sqlalchemy import text

from database.connection import create_database_engine
from trading.strategy import ExecutionConfig, simulate_intraday_trade

# The settled P&L of every prediction that has one, whatever signal it carried
# on the day. `signal` is not filtered on: it records the decision made under
# whichever threshold was in force that morning, and reading it would let the
# old rule quietly select the sample the new rule is judged on.
#
# The lateral join takes the highest ``result_version`` and only that one. A
# corrected close leaves the superseded row in place, and joining both would
# enter that trade twice -- once at a price the market did not settle at.
QUERY = text(
    """
    SELECT s.prediction_date,
           p.ticker,
           p.probability_up,
           p.predicted_intraday_return,
           a.actual_open,
           a.actual_close
      FROM predictions AS p
      JOIN prediction_sets AS s ON s.prediction_set_id = p.prediction_set_id
      JOIN LATERAL (
            SELECT r.actual_open, r.actual_close
              FROM actual_results AS r
             WHERE r.prediction_id = p.prediction_id
               AND r.status IN ('FINAL', 'CORRECTED')
               AND r.actual_open IS NOT NULL
               AND r.actual_close IS NOT NULL
             ORDER BY r.result_version DESC
             LIMIT 1
           ) AS a ON TRUE
     WHERE p.status = 'SUCCESS'
       AND p.probability_up IS NOT NULL
       AND p.predicted_intraday_return IS NOT NULL
     ORDER BY s.prediction_date, p.ticker
    """
)


class DecisionDateViolation(RuntimeError):
    """Raised rather than quietly scoring a session the choice was made from."""


def after_decision(
    rows: list[dict[str, Any]], decided_on: date
) -> list[dict[str, Any]]:
    """Only what comes after the decision, and nothing on the day itself."""

    return [row for row in rows if row["prediction_date"] > decided_on]


def profit_factor(profits: list[float]) -> float | None:
    """Gross profit over gross loss. ``None`` when nothing lost.

    An arm that has not lost yet has no profit factor -- the ratio is not
    infinite, it is unmeasured, and reporting a large number for it would read
    as a strong result rather than as a small sample.
    """

    gains = sum(value for value in profits if value > 0)
    losses = -sum(value for value in profits if value < 0)
    if losses <= 0:
        return None
    return gains / losses


def taken_by(
    rows: list[dict[str, Any]], threshold: float, return_threshold: float
) -> list[dict[str, Any]]:
    """The rule as the pipeline applies it, which is an AND of both halves.

    Only the probability half was raised, but scoring it alone would admit
    every low-conviction prediction the return threshold has always rejected,
    and the profit factor that came back would belong to a rule the system does
    not run.
    """

    return [
        row
        for row in rows
        if row["predicted_return"] >= return_threshold
        and row["probability_up"] >= threshold
    ]


def arm(
    rows: list[dict[str, Any]], threshold: float, return_threshold: float
) -> dict[str, Any]:
    """Score one threshold over the supplied sessions."""

    taken = taken_by(rows, threshold, return_threshold)
    profits = [row["net_profit"] for row in taken]
    wins = [value for value in profits if value > 0]
    losses = [value for value in profits if value < 0]
    factor = profit_factor(profits)
    return {
        "threshold": threshold,
        "return_threshold": return_threshold,
        "trades": len(taken),
        "sessions": len({row["prediction_date"] for row in taken}),
        "profit_factor": None if factor is None else round(factor, 4),
        "net_jpy": round(sum(profits)),
        "gross_profit_jpy": round(sum(wins)),
        "gross_loss_jpy": round(-sum(losses)),
        "win_rate": round(len(wins) / len(taken), 4) if taken else None,
        "average_win_jpy": round(sum(wins) / len(wins)) if wins else None,
        "average_loss_jpy": round(sum(losses) / len(losses)) if losses else None,
    }


def band_test(
    rows: list[dict[str, Any]],
    baseline: float,
    adopted: float,
    return_threshold: float,
) -> dict[str, Any]:
    """Did the trades the raised threshold declines actually lose money?

    Paired at the session level, which is the level at which these are
    independent. A session where the band was empty carries no information
    about it and is dropped rather than entered as a zero -- a zero would say
    the band broke even there, which is a different claim from "the band did
    not trade".
    """

    by_session: dict[date, float] = defaultdict(float)
    count = 0
    for row in taken_by(rows, baseline, return_threshold):
        if row["probability_up"] < adopted:
            by_session[row["prediction_date"]] += row["net_profit"]
            count += 1
    daily = [by_session[day] for day in sorted(by_session)]
    if len(daily) < 6:
        return {
            "band": f"{baseline} <= probability_up < {adopted}",
            "trades": count,
            "sessions": len(daily),
            "verdict": "PENDING",
            "reason": "too few sessions carried a band trade to test",
        }
    values = np.asarray(daily, dtype=float)
    statistic, p_value = stats.wilcoxon(values, alternative="less")
    return {
        "band": f"{baseline} <= probability_up < {adopted}",
        "trades": count,
        "sessions": len(daily),
        "net_jpy": round(float(values.sum())),
        "median_session_jpy": round(float(np.median(values))),
        "wilcoxon_statistic": round(float(statistic), 4),
        "wilcoxon_p_one_sided_less": round(float(p_value), 4),
        "reads_as": (
            "the declined band lost money"
            if p_value < 0.05
            else "the declined band is not distinguishable from break-even"
        ),
    }


def bootstrap_interval(
    rows: list[dict[str, Any]],
    threshold: float,
    return_threshold: float,
    *,
    draws: int = 4000,
    seed: int = 42,
) -> dict[str, Any] | None:
    """A session-level bootstrap around the adopted arm's profit factor.

    Sessions are resampled whole. Resampling individual trades would treat 22
    tickers on one morning as 22 independent draws and return an interval far
    too narrow to be honest about.
    """

    taken = taken_by(rows, threshold, return_threshold)
    if not taken:
        return None
    grouped: dict[date, list[float]] = defaultdict(list)
    for row in taken:
        grouped[row["prediction_date"]].append(row["net_profit"])
    sessions = [grouped[day] for day in sorted(grouped)]
    if len(sessions) < 6:
        return None
    rng = np.random.default_rng(seed)
    samples: list[float] = []
    for _ in range(draws):
        picked = rng.integers(0, len(sessions), size=len(sessions))
        pooled = [value for index in picked for value in sessions[index]]
        factor = profit_factor(pooled)
        if factor is not None:
            samples.append(factor)
    if not samples:
        return None
    array = np.asarray(samples)
    return {
        "draws": len(samples),
        "p05": round(float(np.quantile(array, 0.05)), 4),
        "p50": round(float(np.quantile(array, 0.50)), 4),
        "p95": round(float(np.quantile(array, 0.95)), 4),
        "share_above_one": round(float((array > 1.0).mean()), 4),
    }


def jackknife(
    rows: list[dict[str, Any]], threshold: float, return_threshold: float
) -> dict[str, Any] | None:
    """Recompute the profit factor with each session left out in turn.

    Net profit is a small difference between two large numbers, so a single
    good morning can exceed 100% of it without the arm depending on that
    morning at all -- a fact that made "the best day was 109% of the net" look
    like a fragility here when it was arithmetic. The profit factor does not
    have that defect, and leaving out one session at a time asks the question
    directly: how much of this survives without the best day in it?
    """

    taken = taken_by(rows, threshold, return_threshold)
    days = sorted({row["prediction_date"] for row in taken})
    if len(days) < 3:
        return None
    scores: list[tuple[float, date]] = []
    for day in days:
        factor = profit_factor(
            [row["net_profit"] for row in taken if row["prediction_date"] != day]
        )
        if factor is not None:
            scores.append((factor, day))
    if not scores:
        return None
    scores.sort()
    return {
        "sessions_left_out_one_at_a_time": len(scores),
        "lowest": round(scores[0][0], 4),
        "lowest_without": str(scores[0][1]),
        "highest": round(scores[-1][0], 4),
        "highest_without": str(scores[-1][1]),
        "sessions_whose_removal_drops_it_below_one": sum(
            1 for factor, _ in scores if factor < 1.0
        ),
    }


def verdict(
    adopted: dict[str, Any],
    baseline: dict[str, Any],
    interval: dict[str, Any] | None,
    *,
    minimum: int,
) -> dict[str, str]:
    """One of four, and three of them are "not yet"."""

    if adopted["sessions"] < minimum:
        return {
            "verdict": "PENDING",
            "reason": (
                f"{adopted['sessions']} sessions after the decision, floor is {minimum}"
            ),
        }
    if adopted["profit_factor"] is None or baseline["profit_factor"] is None:
        return {
            "verdict": "PENDING",
            "reason": "an arm has not lost yet, so its profit factor is unmeasured",
        }
    improved = adopted["profit_factor"] > baseline["profit_factor"]
    convincing = interval is not None and interval["share_above_one"] >= 0.90
    if improved and convincing:
        return {
            "verdict": "HOLDS",
            "reason": (
                "the raised threshold beat 0.60 out of sample and its profit"
                " factor stays above 1 across the session bootstrap"
            ),
        }
    if improved:
        return {
            "verdict": "UNPROVEN",
            "reason": (
                "the raised threshold is ahead, but the bootstrap does not keep"
                " it above 1 often enough to call it"
            ),
        }
    return {
        "verdict": "FAILS",
        "reason": (
            "the raised threshold did not beat 0.60 on sessions it never saw;"
            " the in-sample plateau did not survive"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--record", type=Path, default=Path("docs/research/2026-09-10-threshold.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    record = json.loads(args.record.read_text(encoding="utf-8"))
    check = record["forward_check"]
    decided_on = date.fromisoformat(record["recorded_on"])
    baseline_threshold = float(check["baseline_threshold"])
    adopted_threshold = float(check["adopted_threshold"])
    return_threshold = float(check["return_threshold"])
    minimum = int(check["minimum_sessions_for_verdict"])
    if adopted_threshold <= baseline_threshold:
        raise DecisionDateViolation(
            "the adopted threshold must be above the baseline; the band test"
            " assumes the adopted arm is a subset of the baseline arm"
        )

    from data.config import load_app_config

    config = load_app_config("config")
    position = config.trading.position
    costs = config.settings.backtest
    if (
        position.capital_per_stock_jpy is None
        or position.lot_size is None
        or costs.commission_bps_per_side is None
        or costs.slippage_bps_per_side is None
    ):
        # The same refusal the close pipeline makes. Sizing the counterfactual
        # off defaults would produce a profit factor for a strategy nobody
        # agreed to, and it would look exactly like the real one.
        raise ValueError("paper-trading assumptions are not confirmed")
    execution = ExecutionConfig(
        capital_per_stock=float(position.capital_per_stock_jpy),
        lot_size=int(position.lot_size),
        commission_bps=float(costs.commission_bps_per_side),
        slippage_bps=float(costs.slippage_bps_per_side),
    )

    # The shared factory, not create_engine: a Neon URL arrives as
    # postgres:// and needs normalising to psycopg 3 before it will open
    # at all.
    engine = create_database_engine(args.database_url)
    with engine.connect() as connection:
        raw = connection.execute(QUERY).mappings().all()

    rows: list[dict[str, Any]] = []
    skipped = 0
    for record_row in raw:
        open_price = float(record_row["actual_open"])
        close_price = float(record_row["actual_close"])
        if not (open_price > 0 and close_price > 0):
            # INVALID_MARKET_DATA: no target, no P&L, and no quiet repair.
            skipped += 1
            continue
        trade = simulate_intraday_trade(open_price, close_price, config=execution)
        rows.append(
            {
                "prediction_date": record_row["prediction_date"],
                "ticker": record_row["ticker"],
                "probability_up": float(record_row["probability_up"]),
                "predicted_return": float(record_row["predicted_intraday_return"]),
                "net_profit": trade.net_profit,
            }
        )

    scored = after_decision(rows, decided_on)
    adopted = arm(scored, adopted_threshold, return_threshold)
    baseline = arm(scored, baseline_threshold, return_threshold)
    band = band_test(scored, baseline_threshold, adopted_threshold, return_threshold)
    interval = bootstrap_interval(scored, adopted_threshold, return_threshold)
    leave_one_out = jackknife(scored, adopted_threshold, return_threshold)
    call = verdict(adopted, baseline, interval, minimum=minimum)

    payload = {
        "evaluated_at": datetime.now(UTC).isoformat(),
        "record": str(args.record),
        "decided_on": record["recorded_on"],
        "scored_sessions_are_strictly_after": record["recorded_on"],
        "minimum_sessions_for_verdict": minimum,
        "rows_available": len(rows),
        "rows_scored": len(scored),
        "rows_skipped_invalid_market_data": skipped,
        "baseline": baseline,
        "adopted": adopted,
        "declined_band": band,
        "bootstrap": interval,
        "jackknife": leave_one_out,
        **call,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(
        f"決定日 {decided_on} より後の営業日: {adopted['sessions']}日"
        f"（判定に必要 {minimum}日）"
    )
    for label, scored_arm in (
        (f"{baseline_threshold:.3f}", baseline),
        (f"{adopted_threshold:.3f}", adopted),
    ):
        factor = scored_arm["profit_factor"]
        shown = "測定不能" if factor is None else f"{factor:.3f}"
        print(
            f"  しきい値 {label}: PF {shown}"
            f"  {scored_arm['trades']}件  純損益 {scored_arm['net_jpy']:+,}円"
        )
    if interval is not None:
        share = interval["share_above_one"]
        print(
            f"  ブートストラップ PF 90%区間 {interval['p05']:.3f}"
            f"〜{interval['p95']:.3f}（1超の割合 {share:.0%}）"
        )
    if leave_one_out is not None:
        fragile = leave_one_out["sessions_whose_removal_drops_it_below_one"]
        counted = leave_one_out["sessions_left_out_one_at_a_time"]
        print(
            f"  1営業日抜きのPF {leave_one_out['lowest']:.3f}"
            f"〜{leave_one_out['highest']:.3f}"
            f"（1を割るのは {fragile}/{counted}日）"
        )
    print(f"\n{call['verdict']}: {call['reason']}\n-> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
