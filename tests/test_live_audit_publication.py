"""The live comparison cohort must reflect what existed by the 08:30 cutoff."""

from __future__ import annotations

import pandas as pd
import pytest

from research.live_audit import eligible_publications


def _record(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "prediction_date": "2026-08-28",
        "ticker": "7203",
        "generated_at": "2026-08-27T23:00:00Z",
        "published_at": "2026-08-27T23:10:00Z",
        "cutoff_at": "2026-08-27T23:30:00Z",
        "run_type": "MORNING",
        "prediction_set_status": "READY",
        "status": "SUCCESS",
        "actual_intraday_return": 0.01,
        "prediction_id": "timely",
    }
    row.update(changes)
    return row


def test_live_cohort_uses_publication_not_generation_time() -> None:
    source = pd.DataFrame(
        [
            _record(),
            _record(
                ticker="7267",
                prediction_id="generated-early-published-late",
                published_at="2026-08-27T23:31:00Z",
            ),
            _record(ticker="7269", prediction_id="reference", run_type="REFERENCE"),
            _record(
                ticker="7270", prediction_id="missing-publication", published_at=None
            ),
        ]
    )

    eligible = eligible_publications(source)

    assert eligible.prediction_id.tolist() == ["timely"]


def test_live_cohort_keeps_latest_pre_cutoff_decision() -> None:
    source = pd.DataFrame(
        [
            _record(),
            _record(
                prediction_id="replacement",
                published_at="2026-08-27T23:20:00Z",
            ),
        ]
    )

    assert eligible_publications(source).prediction_id.tolist() == ["replacement"]


def test_live_cohort_fails_closed_without_publication_metadata() -> None:
    source = pd.DataFrame([_record()]).drop(columns="published_at")

    with pytest.raises(ValueError, match="published_at"):
        eligible_publications(source)
