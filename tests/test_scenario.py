from __future__ import annotations

import pandas as pd
import pytest

from backtest.scenario import (
    ScenarioConfig,
    evaluate_scenario,
    prepare_scenario_frame,
)


def _rows() -> list[dict[str, object]]:
    return [
        {
            "ticker": "9101",
            "prediction_date": "2026-08-03",
            "predicted_return": 0.01,
            "probability_up": 0.70,
            "actual_open": 1_000.0,
            "actual_close": 1_020.0,
        },
        {
            "ticker": "7203",
            "prediction_date": "2026-08-03",
            "predicted_return": 0.005,
            "probability_up": 0.65,
            "actual_open": 2_000.0,
            "actual_close": 1_980.0,
        },
        {
            "ticker": "8306",
            "prediction_date": "2026-08-04",
            "predicted_return": 0.0001,
            "probability_up": 0.90,
            "actual_open": 1_500.0,
            "actual_close": 1_600.0,
        },
    ]


def test_prepare_drops_rows_without_a_usable_price() -> None:
    rows = _rows()
    rows.append(
        {
            "ticker": "5020",
            "prediction_date": "2026-08-04",
            "predicted_return": 0.02,
            "probability_up": 0.80,
            "actual_open": None,
            "actual_close": 900.0,
        }
    )
    prepared = prepare_scenario_frame(rows)

    assert len(prepared) == 3
    assert "5020" not in set(prepared["ticker"])


def test_threshold_excludes_a_below_threshold_prediction() -> None:
    # 8306 predicted only +0.01% so it must not trade despite a 90% probability
    # and a large realized gain.
    outcome = evaluate_scenario(
        _rows(), ScenarioConfig(commission_bps=0.0, slippage_bps=0.0)
    )

    traded = set(outcome.trades.loc[outcome.trades["selected"], "ticker"])
    assert traded == {"9101", "7203"}
    assert outcome.portfolio.number_of_trades == 2


def test_lowering_the_threshold_admits_the_excluded_prediction() -> None:
    outcome = evaluate_scenario(
        _rows(),
        ScenarioConfig(return_threshold=0.0, commission_bps=0.0, slippage_bps=0.0),
    )

    traded = set(outcome.trades.loc[outcome.trades["selected"], "ticker"])
    assert traded == {"9101", "7203", "8306"}


def test_top_n_keeps_only_the_highest_ranked_candidate_per_day() -> None:
    outcome = evaluate_scenario(
        _rows(), ScenarioConfig(top_n=1, commission_bps=0.0, slippage_bps=0.0)
    )

    selected = outcome.trades.loc[outcome.trades["selected"]]
    assert selected["ticker"].tolist() == ["9101"]
    assert int(selected.iloc[0]["rank"]) == 1


def test_costs_reduce_net_profit_without_changing_the_gross() -> None:
    free = evaluate_scenario(
        _rows(), ScenarioConfig(commission_bps=0.0, slippage_bps=0.0)
    )
    costed = evaluate_scenario(
        _rows(), ScenarioConfig(commission_bps=10.0, slippage_bps=10.0)
    )

    assert costed.portfolio.net_profit < free.portfolio.net_profit
    assert costed.portfolio.number_of_trades == free.portfolio.number_of_trades


def test_larger_capital_buys_more_board_lots() -> None:
    small = evaluate_scenario(
        _rows(),
        ScenarioConfig(
            capital_per_stock=1_000_000.0, commission_bps=0.0, slippage_bps=0.0
        ),
    )
    large = evaluate_scenario(
        _rows(),
        ScenarioConfig(
            capital_per_stock=5_000_000.0, commission_bps=0.0, slippage_bps=0.0
        ),
    )

    small_shares = small.trades.loc[small.trades["selected"], "shares"].sum()
    large_shares = large.trades.loc[large.trades["selected"], "shares"].sum()
    assert large_shares > small_shares
    assert all(
        int(shares) % 100 == 0
        for shares in large.trades.loc[large.trades["selected"], "shares"]
    )


def test_low_sample_is_flagged_and_never_silently_passed() -> None:
    outcome = evaluate_scenario(_rows())

    assert outcome.is_low_sample
    assert any("LOW_SAMPLE" in warning for warning in outcome.warnings)


def test_repeated_scenarios_warn_about_selection_bias() -> None:
    outcome = evaluate_scenario(_rows(), scenarios_evaluated=4)

    assert any("selection bias" in warning for warning in outcome.warnings)


def test_empty_input_produces_no_trades_rather_than_an_error() -> None:
    outcome = evaluate_scenario([])

    assert outcome.portfolio.number_of_trades == 0
    assert outcome.trades.empty
    assert outcome.rows_considered == 0


def test_scenario_never_trades_a_zero_or_negative_price() -> None:
    rows = [
        {
            "ticker": "9101",
            "prediction_date": "2026-08-03",
            "predicted_return": 0.02,
            "probability_up": 0.9,
            "actual_open": 0.0,
            "actual_close": 1_000.0,
        }
    ]
    outcome = evaluate_scenario(rows)

    assert outcome.rows_considered == 0
    assert outcome.rows_skipped == 1


def test_per_ticker_metrics_cover_each_traded_symbol() -> None:
    outcome = evaluate_scenario(
        _rows(),
        ScenarioConfig(return_threshold=0.0, commission_bps=0.0, slippage_bps=0.0),
    )

    assert set(outcome.per_ticker["ticker"]) == {"9101", "7203", "8306"}
    assert "mean_absolute_error" in outcome.per_ticker.columns


def test_probability_threshold_is_inclusive_at_the_boundary() -> None:
    rows = [
        {
            "ticker": "9101",
            "prediction_date": "2026-08-03",
            "predicted_return": 0.01,
            "probability_up": 0.60,
            "actual_open": 1_000.0,
            "actual_close": 1_010.0,
        }
    ]
    outcome = evaluate_scenario(rows, ScenarioConfig(probability_threshold=0.60))

    assert bool(outcome.trades.iloc[0]["selected"]) is True


def test_missing_columns_are_rejected() -> None:
    with pytest.raises(ValueError, match="missing scenario columns"):
        prepare_scenario_frame(pd.DataFrame({"ticker": ["9101"]}))


def test_invalid_top_n_is_rejected() -> None:
    with pytest.raises(ValueError, match="top_n"):
        ScenarioConfig(top_n=0)
