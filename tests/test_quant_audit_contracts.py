from dataclasses import replace
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pytest

from data.config import load_app_config
from data.feature_registry import resolve_indicator_ids
from notifications.contracts import EmailCandidate, MorningEmailPayload
from notifications.method_thresholds import arm_verdict
from notifications.templates import _arms_summary_html, render_morning_email
from research.nested_selection import FrozenSelection, nested_select
from research.probability_calibration import calibrate_oos, probability_metrics
from scripts.test_email import validate_message


def test_per_ticker_indicator_resolution_preserves_champion():
    config = load_app_config("config")
    archived = config.model_copy(update={"ticker_features": None})
    assert len(config.stocks.stocks) == 22
    for stock in config.stocks.stocks:
        assert resolve_indicator_ids(config, stock.ticker) == resolve_indicator_ids(
            archived, stock.ticker
        )


def test_no_global_indicator_forcing():
    config = load_app_config("config")
    registry = config.ticker_features
    sets = dict(registry.tickers)
    sets["7203"] = sets["7203"].model_copy(update={"selected": ["usdjpy"]})
    config = config.model_copy(
        update={"ticker_features": registry.model_copy(update={"tickers": sets})}
    )
    assert resolve_indicator_ids(config, "7203") == ("usdjpy",)
    assert len(resolve_indicator_ids(config, "7267")) > 1


def test_feature_set_freeze():
    registry = load_app_config("config").ticker_features
    with pytest.raises(ValueError, match="frozen"):
        registry.assert_review_allowed(19)
    registry.assert_review_allowed(20)
    registry.assert_review_allowed(0, provider_emergency=True)


def test_nested_selection_training_only_and_freeze(monkeypatch):
    import research.nested_selection as selector

    rng = np.random.default_rng(22)
    x = pd.DataFrame(rng.normal(size=(140, 10)), columns=[f"f{i}" for i in range(10)])
    y = x.f0.to_numpy() * 0.01 + rng.normal(0, 0.002, 140)
    observed = []
    original = selector.stable_ranking

    def audited(train, target, budget):
        observed.append(tuple(train.index))
        return original(train, target, budget)

    monkeypatch.setattr(selector, "stable_ranking", audited)
    first = nested_select(x.iloc[:120], y[:120])
    assert max(max(indices) for indices in observed) == 119
    assert set(len(indices) for indices in observed) == {30, 60, 90, 120}
    x.iloc[120:] = 1e9
    y[120:] = -1e9
    second = nested_select(x.iloc[:120], y[:120])
    assert first == second
    assert len(first.names) <= 12
    frozen = FrozenSelection(120, first)
    assert not frozen.due(139)
    assert frozen.due(140)


def test_probability_calibration_no_leakage_and_version_isolation():
    dates = pd.date_range("2026-01-01", periods=24, tz="UTC")
    frame = pd.DataFrame(
        {
            "date": dates.date,
            "cutoff_at": dates + pd.Timedelta(hours=8),
            "outcome_available_at": dates + pd.Timedelta(hours=16),
            "feature_version": ["v1"] * 23 + ["v2"],
            "ticker": "7203",
            "sector": "auto",
            "probability_up": np.linspace(0.3, 0.8, 24),
            "actual_return": np.tile([-0.01, 0.01], 12),
        }
    )
    frame.loc[3, "outcome_available_at"] = dates[-1] + pd.Timedelta(days=5)
    baseline = calibrate_oos(frame, minimum_pairs=10)
    altered = frame.copy()
    altered.loc[20:, "actual_return"] = 100
    changed = calibrate_oos(altered, minimum_pairs=10)
    assert baseline.loc[20, "calibrated_probability"] == pytest.approx(
        changed.loc[20, "calibrated_probability"], abs=1e-14
    )
    assert baseline.loc[20, "calibration_pairs"] == 19
    assert baseline.loc[23, "calibration_pairs"] == 0
    assert baseline.loc[23, "calibration_status"] == "UNCALIBRATED_LOW_SAMPLE"
    assert baseline.loc[20, "calibration_training_end"] < baseline.loc[20, "date"]


def test_probability_metrics_perfect_and_finite():
    metrics = probability_metrics(np.array([0.0, 1.0]), np.array([0.0, 1.0]))
    assert metrics["brier"] < 1e-14
    assert np.isfinite(metrics["log_loss"])


def test_email_arm_summary_uses_real_predictions():
    arms = (
        {"name": "ridge", "label": "Ridge", "status": "OK", "predicted_return": 0.01},
        {"name": "lasso", "label": "Lasso", "status": "OK", "predicted_return": -0.01},
        {"name": "tree", "status": "UNAVAILABLE", "predicted_return": 0.1},
    )
    thresholds = {
        n: {"threshold": 0.003, "evaluation_positions": 20}
        for n in ("ridge", "lasso", "tree")
    }
    candidate = EmailCandidate("7203", "Toyota", 0.01, 0.7, "BUY", arms=arms)
    rendered = _arms_summary_html([candidate], thresholds)
    assert "1 / 2" in rendered
    assert arm_verdict(arms[0], thresholds)[0] == "買い"
    assert arm_verdict(arms[2], thresholds)[0] == "—"
    assert (
        arm_verdict({**arms[0], "predicted_return": float("nan")}, thresholds)[0] == "—"
    )
    assert "未判定" in _arms_summary_html([candidate], {})


def test_test_email_contains_every_ticker_in_both_alternatives():
    config = load_app_config("config")
    payload = MorningEmailPayload(
        date(2026, 9, 18),
        datetime(2026, 9, 17, 23, 30, tzinfo=UTC),
        datetime(2026, 9, 17, 23, 30, tzinfo=UTC),
        tuple(
            EmailCandidate(s.ticker, s.name, 0.001, 0.52, "NO_BUY")
            for s in config.stocks.stocks
        ),
        "https://example.test",
    )
    message = render_morning_email(
        payload, sender="sender@example.test", recipient="test@example.test"
    )
    message = replace(message, subject="[TEST][Morning] " + message.subject)
    expected = {s.ticker for s in config.stocks.stocks}
    validate_message(message, payload, expected)
    assert len(message.html.encode()) < 102 * 1024
    with pytest.raises(ValueError, match="omits"):
        validate_message(replace(message, html="<html></html>"), payload, expected)


def test_daily_coefficients_contribution_and_sign_stability():
    from services.linear_diagnostics import linear_diagnostics

    result = linear_diagnostics(
        {"a": 0.04, "b": -0.02},
        {"a": 2.0, "b": 4.0},
        {"a": 2.0, "b": 1.0},
        {"a": 4.0, "b": 4.0},
        [{"a": -0.04, "b": -0.01}, {"a": 0.02, "b": -0.03}],
    )
    a, b = result["features"]
    assert a["raw_coefficient"] == 0.02
    assert a["today_contribution"] == 0.04
    assert a["sign_stability"] == pytest.approx(2 / 3)
    assert b["today_contribution"] == 0
    assert b["sign_stability"] == 1
    assert a["rolling_fits"] == 3
    assert a["stability_status"] == "LOW_SAMPLE"


def test_calibration_bins_cover_decimal_boundaries_once():
    metrics = probability_metrics(np.zeros(11), np.linspace(0, 1, 11))
    assert sum(row["n"] for row in metrics["reliability"]) == 11


def test_test_email_isolation_rejects_production_state_writes():
    from sqlalchemy import create_engine
    from sqlalchemy.exc import OperationalError

    from scripts.test_email import enforce_read_only

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE email_logs (id TEXT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO email_logs VALUES ('production')")
    enforce_read_only(engine)
    with engine.begin() as connection:
        assert (
            connection.exec_driver_sql("SELECT count(*) FROM email_logs").scalar() == 1
        )
        with pytest.raises(OperationalError, match="readonly"):
            connection.exec_driver_sql("INSERT INTO email_logs VALUES ('test')")
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql("SELECT id FROM email_logs").scalar()
            == "production"
        )
    engine.dispose()
