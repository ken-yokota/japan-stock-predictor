"""Research challengers must use prior rows and a shared scoring cohort."""

from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from research.robust_candidates import fit_candidate
from scripts import summarize_robust_candidate_oos as study


def test_huber_keeps_a_single_training_outlier_from_setting_the_forecast() -> None:
    rng = np.random.default_rng(19)
    x = rng.normal(scale=0.01, size=140)
    target = 2 * x + rng.normal(scale=0.001, size=len(x))
    target[0] = 1.0
    training = pd.DataFrame({"signal": x})
    current = pd.DataFrame({"signal": [0.01]})

    result = fit_candidate("huber", training, pd.Series(target), current)

    assert result.name == "huber"
    assert result.predicted_return == pytest.approx(0.02, abs=0.01)
    assert np.isfinite(result.train_mae)


def test_extra_trees_is_deterministic_and_never_reads_the_current_target() -> None:
    rng = np.random.default_rng(29)
    x = rng.uniform(-1, 1, size=140)
    training = pd.DataFrame({"signal": x})
    target = pd.Series(np.where(x > 0, 0.02, -0.02))
    current = pd.DataFrame({"signal": [0.8]})

    first = fit_candidate("extra_trees", training, target, current)
    second = fit_candidate("extra_trees", training, target, current)

    assert first == second
    assert first.predicted_return > 0
    with pytest.raises(ValueError, match="columns differ"):
        fit_candidate("extra_trees", training, target, pd.DataFrame({"other": [0.8]}))
    with pytest.raises(ValueError, match="missing at cutoff"):
        fit_candidate(
            "extra_trees", training, target, pd.DataFrame({"signal": [np.nan]})
        )


def test_summary_restricts_every_arm_to_identical_ticker_sessions(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(
        stocks=SimpleNamespace(
            stocks=[
                SimpleNamespace(ticker=ticker, enabled=True)
                for ticker in ("A", "B")
            ]
        ),
        trading=SimpleNamespace(
            signal=SimpleNamespace(
                predicted_intraday_return_threshold=0.003,
                probability_up_threshold=0.625,
            )
        ),
    )
    monkeypatch.setattr(study, "load_app_config", lambda _: config)
    for ticker in ("A", "B"):
        rows = []
        for day in ("2026-07-01", "2026-07-02"):
            actual = 0.01 if ticker == "A" else -0.01
            if day == "2026-07-02":
                actual += 0.005
            for name in ("champion_replay", "huber", "extra_trees"):
                status = (
                    "FAILED"
                    if (ticker, day, name) == ("B", "2026-07-02", "extra_trees")
                    else "OK"
                )
                rows.append(
                    {
                        "ticker": ticker,
                        "date": day,
                        "model": name,
                        "status": status,
                        "actual_return": actual,
                        "predicted_return": actual - 0.002,
                        "probability_up": 0.7,
                        "probability_source": "champion_logistic",
                        "features": ["signal"],
                        "training_start": "2026-01-01",
                        "training_end": "2026-06-30",
                    }
                )
        (tmp_path / f"{ticker}.json").write_text(
            json.dumps(
                {
                    "ticker": ticker,
                    "evidence": "ESTIMATED_BACKFILL",
                    "candidate_names": ["huber", "extra_trees"],
                    "start": "2026-01-01",
                    "end": "2026-09-18",
                    "rows": rows,
                }
            )
        )

    report = study.summarize(tmp_path)

    assert report["common_pairs"] == 3
    assert report["dropped_from_common"] == {
        "champion_replay": 1,
        "huber": 1,
        "extra_trees": 0,
    }
    assert report["non_ok_status_counts"]["extra_trees"] == {"FAILED": 1}
    assert report["paired_mae_difference"]["huber"]["candidate_minus_champion"] == 0
    for metrics in report["metrics"].values():
        assert metrics["10"]["samples"] == 3
        assert metrics["10"]["trades"] == 2
    json.dumps(report, allow_nan=False)

    path = tmp_path / "A.json"
    altered = json.loads(path.read_text())
    for row in altered["rows"]:
        if row["model"] == "huber" and row["date"] == "2026-07-01":
            row["training_end"] = "2026-07-01"
    path.write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="did not share champion inputs"):
        study.summarize(tmp_path)
