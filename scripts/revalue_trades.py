#!/usr/bin/env python3
"""Re-value every settled prediction under the current cost configuration.

Costs went to zero on 2026-08-29 because no orders are being placed. The
predictions themselves do not change -- nothing in the BUY rule reads a cost, so
which names were bought and on what evidence is exactly as published, and this
does not rewrite a single prediction. What changes is only the arithmetic that
turns a settled open and close into yen.

The old valuations are not deleted. A trade's identity is
(prediction_id, actual_result_id, strategy_version), so a re-valuation under a
new strategy label lands beside the costed one and the costed history stays
readable. That matters here: at 5 + 5 bps a side the round trip was about four
fifths of the recorded loss, and that fact should remain checkable after the
number it explains has been replaced.

Only the current result is re-valued. A corrected close supersedes its
predecessor, and valuing a superseded result would put a second live trade
against the same prediction.

    python -m scripts.revalue_trades --dry-run
    python -m scripts.revalue_trades
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

from data.config import load_app_config
from data.env import EnvironmentSettings
from database.connection import create_database_engine
from database.repository import PredictionPipelineRepository
from services.versioning import STRATEGY_VERSION
from trading.strategy import ExecutionConfig, simulate_intraday_trade

# Every settled prediction whose current result has not yet been valued under
# the running strategy label.
PENDING = """
    SELECT p.prediction_id, p.signal, ps.prediction_date, p.ticker,
           a.actual_result_id, a.actual_open, a.actual_close
    FROM predictions AS p
    JOIN prediction_sets AS ps ON ps.prediction_set_id = p.prediction_set_id
    JOIN actual_results AS a ON a.prediction_id = p.prediction_id
    WHERE ps.status = 'READY'
      AND a.actual_open IS NOT NULL
      AND a.actual_close IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM actual_results AS superseding
          WHERE superseding.supersedes_actual_result_id = a.actual_result_id
      )
      AND NOT EXISTS (
          SELECT 1 FROM simulated_trades AS existing
          WHERE existing.prediction_id = p.prediction_id
            AND existing.actual_result_id = a.actual_result_id
            AND existing.strategy_version = :strategy
      )
    ORDER BY ps.prediction_date, p.ticker
"""


def _execution_config(config_dir: Path | None) -> ExecutionConfig:
    config = load_app_config(config_dir) if config_dir else load_app_config()
    costs = config.trading.costs
    position = config.trading.position
    return ExecutionConfig(
        capital_per_stock=float(position.capital_per_stock_jpy or 0.0),
        lot_size=int(position.lot_size or 0),
        commission_bps=float(costs.commission_bps_per_side or 0.0),
        slippage_bps=float(costs.slippage_bps_per_side or 0.0),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    execution = _execution_config(args.config_dir)
    round_trip = 2.0 * (execution.commission_bps + execution.slippage_bps) / 10_000.0
    print(
        f"strategy_version={STRATEGY_VERSION}"
        f"  往復コスト {round_trip * 100:.4f}%"
        f"  (手数料 {execution.commission_bps} bps/side,"
        f" スリッページ {execution.slippage_bps} bps/side)"
    )

    engine = create_database_engine(EnvironmentSettings().reporting_database_url())
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(PENDING), {"strategy": STRATEGY_VERSION}
            ).mappings().all()
        print(f"再評価の対象: {len(rows)}件")
        if not rows:
            return 0

        written = 0
        total_net = 0.0
        with Session(engine) as session:
            repository = PredictionPipelineRepository(session)
            for row in rows:
                is_buy = row["signal"] == "BUY"
                trade = simulate_intraday_trade(
                    float(row["actual_open"]),
                    float(row["actual_close"]),
                    execute=is_buy,
                    config=execution,
                )
                total_net += trade.net_profit
                if args.dry_run:
                    written += 1
                    continue
                repository.save_simulated_trade(
                    prediction_id=str(row["prediction_id"]),
                    actual_result_id=str(row["actual_result_id"]),
                    status="FINAL",
                    capital_jpy=Decimal(str(execution.capital_per_stock)),
                    shares=int(trade.shares),
                    entry_price=Decimal(str(trade.execution_open)),
                    exit_price=Decimal(str(trade.execution_close)),
                    gross_profit_jpy=Decimal(str(trade.gross_profit)),
                    commission_cost_jpy=Decimal(str(trade.commission_cost)),
                    slippage_cost_jpy=Decimal(str(trade.slippage_cost)),
                    net_profit_jpy=Decimal(str(trade.net_profit)),
                    realized_return=Decimal(str(trade.return_on_capital)),
                    opened_at=datetime.now(UTC),
                    closed_at=datetime.now(UTC),
                    strategy_version=STRATEGY_VERSION,
                    idempotency_key=(
                        f"trade/{row['prediction_id']}/{row['actual_result_id']}"
                        f"/{STRATEGY_VERSION}"
                    ),
                )
                written += 1
            if not args.dry_run:
                session.commit()

        verb = "書き込む予定" if args.dry_run else "書き込みました"
        print(f"{written}件を{verb}。買いの純損益合計 {total_net:+,.0f}円")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
