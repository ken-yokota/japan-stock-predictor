#!/usr/bin/env python3
"""Score a prediction set in three layers, from the live database or an artifact.

One command for both sources on purpose. The live pipeline and the research
walk-forward had drifted into being judged by different code, and a comparison
between two implementations proves nothing about the thing being compared.

    python -m scripts.evaluate_predictions --live
    python -m scripts.evaluate_predictions --artifact artifacts/oos/production.json
    python -m scripts.evaluate_predictions --live --json

The trading block is printed but never used to choose between models: below
about twenty trades it cannot separate a better model from a luckier month, and
this system has forty-six.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from research.evaluation import (
    Evaluation,
    GroupQuality,
    Prediction,
    evaluate,
    from_research_rows,
    group_quality,
    quantile_is_monotonic,
)
from research.selection_rules import RuleResult, standard_rules

LIVE_QUERY = """
    SELECT ps.prediction_date AS date, p.ticker, p.signal,
           p.predicted_intraday_return AS predicted_return,
           p.probability_up,
           a.actual_intraday_return AS actual_return,
           t.gross_profit_jpy, t.net_profit_jpy,
           t.commission_cost_jpy, t.slippage_cost_jpy
    FROM prediction_sets AS ps
    JOIN predictions AS p ON p.prediction_set_id = ps.prediction_set_id
    JOIN actual_results AS a ON a.prediction_id = p.prediction_id
    LEFT JOIN simulated_trades AS t ON t.prediction_id = p.prediction_id
    ORDER BY ps.prediction_date, p.ticker
"""


def _round_trip_cost() -> float:
    """Round-trip cost from the config production actually uses.

    Never softened to make a result look better: the same number the simulated
    trades were charged is the number every counterfactual is charged.
    """

    try:
        from data.config import load_app_config

        costs = load_app_config().trading.costs
        commission = costs.commission_bps_per_side
        slippage = costs.slippage_bps_per_side
        if commission is None or slippage is None:
            # An unset cost is not a zero cost. Refuse rather than flatter.
            raise ValueError("transaction costs are not configured")
        return (float(commission) + float(slippage)) * 2 / 10_000
    except Exception:
        return 0.0020


def _sectors() -> dict[str, str]:
    try:
        from data.config import load_app_config

        return {s.ticker: s.sector for s in load_app_config().stocks.stocks}
    except Exception:
        return {}


def load_live() -> list[Prediction]:
    """Every settled live prediction. Never rewritten, only read."""

    from sqlalchemy import text

    from database.connection import create_database_engine

    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise SystemExit("DATABASE_URL が未設定です。")
    engine = create_database_engine(url)
    try:
        with engine.connect() as connection:
            rows = [dict(r) for r in connection.execute(text(LIVE_QUERY)).mappings()]
    finally:
        engine.dispose()
    sectors = _sectors()
    out = []
    for row in rows:
        commission = row["commission_cost_jpy"]
        cost = None
        if commission is not None:
            cost = float(commission) + float(row["slippage_cost_jpy"] or 0)
        out.append(
            Prediction(
                date=str(row["date"]),
                ticker=str(row["ticker"]),
                predicted_return=float(row["predicted_return"]),
                actual_return=float(row["actual_return"]),
                probability_up=(
                    None
                    if row["probability_up"] is None
                    else float(row["probability_up"])
                ),
                signal=str(row["signal"]),
                net_profit_jpy=(
                    None
                    if row["net_profit_jpy"] is None
                    else float(row["net_profit_jpy"])
                ),
                gross_profit_jpy=(
                    None
                    if row["gross_profit_jpy"] is None
                    else float(row["gross_profit_jpy"])
                ),
                cost_jpy=cost,
                sector=sectors.get(str(row["ticker"])),
            )
        )
    return out


def load_artifact(path: Path) -> list[Prediction]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return from_research_rows(payload.get("predictions", []), sectors=_sectors())


def _pct(value: float | None, digits: int = 3) -> str:
    return "—" if value is None else f"{value:+.{digits}%}"


def _num(value: float | None, digits: int = 4) -> str:
    return "—" if value is None else f"{value:+.{digits}f}"


def _yen(value: float | None) -> str:
    return "—" if value is None else f"{value:+,.0f}円"


def render(result: Evaluation) -> str:
    """A plain-text report. Every number carries the sample it came from."""

    model, selection, probability, trading = (
        result.model,
        result.selection,
        result.probability,
        result.trading,
    )
    lines: list[str] = []
    add = lines.append
    add(f"=== {result.label} ===")
    add("")
    add(f"■ Model Layer（標本 {model.count} 予測）")
    add(f"  MAE                  {model.mae:.4%}")
    add(f"  RMSE                 {model.rmse:.4%}")
    add(f"  Pearson              {_num(model.pearson)}")
    add(f"  Spearman             {_num(model.spearman)}")
    add(f"  Bias(予測-実績)      {model.bias:+.4%}")
    add(f"  Calibration slope    {_num(model.calibration_slope)}  (理想 1.0)")
    add(f"  Calibration intercept{_num(model.calibration_intercept, 5)}  (理想 0)")
    add(f"  Direction accuracy   {model.direction_accuracy:.2%}"
        f"  ({model.direction_edge_pp:+.2f}pp vs コイン投げ)")
    add(f"  平均 予測 {model.predicted_mean:+.3%} / 平均 実績 {model.actual_mean:+.3%}")
    add("")
    add("■ 予測値の分位（予測が高いほど実績も高いか）")
    add("  分位  件数   予測平均    実績平均    上昇率")
    for quantile in result.quantiles:
        add(
            f"   Q{quantile.quantile}  {quantile.count:>4}"
            f"  {quantile.predicted_mean:+8.3%}"
            f"  {quantile.actual_mean:+8.3%}  {quantile.win_rate:6.1%}"
        )
    add(f"  単調か: {'はい' if quantile_is_monotonic(result.quantiles) else 'いいえ'}")
    add("")
    add(f"■ Selection Layer（標本 {selection.sessions} 営業日）")
    add(f"  Daily Rank IC 平均   {_num(selection.rank_ic_mean)}"
        f"  sd {_num(selection.rank_ic_sd)}  t {_num(selection.rank_ic_t, 2)}")
    add(f"  Universe 平均実績    {_pct(selection.universe_mean)}")
    add(f"  Top1 - Universe      {_pct(selection.top1_alpha)}")
    add(f"  Top3 - Universe      {_pct(selection.top3_alpha)}")
    add(f"  Top5 - Universe      {_pct(selection.top5_alpha)}"
        f"  t {_num(selection.top5_alpha_t, 2)}")
    add(f"  Top5 - Bottom5       {_pct(selection.top_bottom_spread)}")
    add("")
    add(f"■ Probability Layer（標本 {probability.count} 予測）")
    add(f"  Brier    {_num(probability.brier)}   Log loss {_num(probability.log_loss)}")
    add(f"  実際の上昇率（全体） {probability.base_rate:.2%}")
    add("  確率帯        件数   平均予測確率   実際の上昇率")
    for band in probability.bins:
        add(
            f"  {band.low:5.0%}-{band.high:5.0%}  {band.count:>5}   "
            f"{band.mean_predicted:11.1%}   {band.actual_up_rate:11.1%}"
        )
    add("")
    add(f"■ Trading Layer（取引 {trading.trades}件 / {trading.sessions}営業日）")
    add(f"  Gross {_yen(trading.gross_jpy)}  Cost {_yen(-abs(trading.cost_jpy))}"
        f"  Net {_yen(trading.net_jpy)}")
    add(f"  Win rate {trading.win_rate:.1%}" if trading.win_rate is not None
        else "  Win rate —")
    add(f"  Payoff {_num(trading.payoff_ratio, 2)}   "
        f"Profit factor {_num(trading.profit_factor, 2)}   "
        f"Expectancy {_yen(trading.expectancy_jpy)}")
    add(f"  勝ち日 {trading.winning_days} / 負け日 {trading.losing_days}")
    add(f"  日次平均 {_yen(trading.daily_mean_jpy)}  sd {_yen(trading.daily_sd_jpy)}")
    add(f"  日次 Sharpe {_num(trading.daily_sharpe, 2)}  "
        f"Sortino {_num(trading.daily_sortino, 2)}")
    add(f"  最大ドローダウン {_yen(trading.max_drawdown_jpy)}  "
        f"最悪日 {_yen(trading.worst_day_jpy)}  最良日 {_yen(trading.best_day_jpy)}")
    if trading.underpowered:
        add(f"  ※ {trading.trades}取引では優位性の判定に使えません。参考値です。")
    return "\n".join(lines)


def as_dict(result: Evaluation) -> dict[str, Any]:
    from dataclasses import asdict

    return {
        "label": result.label,
        "model": asdict(result.model),
        "quantiles": [asdict(row) for row in result.quantiles],
        "selection": asdict(result.selection),
        "probability": asdict(result.probability),
        "trading": asdict(result.trading),
        "sessions": [asdict(row) for row in result.sessions],
    }


def render_rules(rules: Sequence[RuleResult]) -> str:
    """The trading-layer comparison, control first.

    This table is computed on the same sessions it reports, so picking its best
    row is an in-sample choice. It is a diagnostic, not a recommendation.
    """

    lines = ["■ Trading Layer: 選別ルールの比較（コスト控除後・営業日単位）"]
    lines.append(
        "  ルール                              建玉  1建玉平均   日次平均"
        "   日次t   累積      勝ち日/負け日  最大DD"
    )
    for rule in rules:
        lines.append(
            f"  {rule.name:<34} {rule.positions:>4}"
            f"  {_pct(rule.mean_position_return, 3):>9}"
            f"  {_pct(rule.daily_mean_return, 3):>9}"
            f"  {_num(rule.daily_t, 2):>6}"
            f"  {rule.total_return:+8.2%}"
            f"  {rule.winning_sessions:>5}/{rule.losing_sessions:<5}"
            f"  {rule.max_drawdown:+7.2%}"
        )
    lines.append(
        "  ※ 同じ営業日の上で計算した表です。最良の行を選ぶのは in-sample な選択で、"
        "推奨ではありません。"
    )
    return "\n".join(lines)


def render_groups(rows: Sequence[GroupQuality], *, title: str) -> str:
    """Where the signal is, if it is anywhere. Worst realised P&L first.

    The z column is unadjusted for having split the sample: with five sectors,
    one of them clearing |z| = 2 by chance is ordinary. Read it as a place to
    look, not as a finding.
    """

    lines = [f"■ {title}"]
    lines.append(
        "  名前                 予測数 営業日  方向的中     z   Spearman"
        "     MAE   平均予測   平均実績  取引  損益"
    )
    for row in rows:
        lines.append(
            f"  {row.name:<20} {row.count:>5} {row.sessions:>5}"
            f"  {row.direction_accuracy:7.1%}"
            f"  {_num(row.direction_z, 2):>6}"
            f"  {_num(row.spearman, 3):>8}"
            f"  {row.mae:6.2%}"
            f"  {row.predicted_mean:+8.3%}"
            f"  {row.actual_mean:+8.3%}"
            f"  {row.traded:>4}"
            f"  {row.net_jpy:+10,.0f}円"
        )
    lines.append(
        "  ※ z は標本を分割したことを補正していません。5分割なら1つが |z|=2 を"
        "超えるのは普通に起こります。"
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--live", action="store_true", help="本番DBの確定済み予測")
    source.add_argument("--artifact", type=Path, help="walk-forward の出力JSON")
    parser.add_argument("--label", default=None)
    parser.add_argument(
        "--from-date", default=None, help="この日以降の予測だけを採点する (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--to-date", default=None, help="この日以前の予測だけを採点する (YYYY-MM-DD)"
    )
    parser.add_argument("--json", action="store_true", help="JSONで出力")
    parser.add_argument(
        "--rules", action="store_true", help="選別ルールの比較表も出す"
    )
    parser.add_argument(
        "--groups", action="store_true", help="銘柄別・セクター別の内訳も出す"
    )
    parser.add_argument(
        "--cost",
        type=float,
        default=None,
        help="1建玉あたりの往復コスト。既定は config/trading.yaml から算出。",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.live:
        predictions = load_live()
        label = args.label or "Live（本番・確定済み）"
    else:
        predictions = load_artifact(args.artifact)
        label = args.label or f"OOS {args.artifact.name}"
    if args.from_date:
        predictions = [p for p in predictions if p.date >= args.from_date]
    if args.to_date:
        predictions = [p for p in predictions if p.date <= args.to_date]
    if not predictions:
        print("採点できる予測がありません。")
        return 1
    result = evaluate(predictions, label=label)
    cost = args.cost if args.cost is not None else _round_trip_cost()
    if args.json or args.output:
        payload = json.dumps(as_dict(result), ensure_ascii=False, indent=2)
        if args.output:
            args.output.write_text(payload, encoding="utf-8")
        if args.json:
            print(payload)
            return 0
    print(render(result))
    if args.groups:
        print()
        print(
            render_groups(
                group_quality(predictions, by="sector"), title="セクター別"
            )
        )
        print()
        print(render_groups(group_quality(predictions, by="ticker"), title="銘柄別"))
    if args.rules:
        print()
        print(render_rules(standard_rules(predictions, cost_per_position=cost)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
