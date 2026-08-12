"""An empty list of misses is not the same as no misses.

Feature sets written before the completeness fields existed carry nothing, and
reading that absence as "complete" would manufacture exactly the reassurance
the audit exists to withdraw. These pin the three states apart.
"""

from __future__ import annotations

from scripts.audit_morning_completeness import CLEAN, DEGRADED, UNKNOWN, classify


def test_a_run_that_recorded_no_misses_is_clean() -> None:
    status, required, optional, coverage = classify(
        {
            "missing_required_indicators": [],
            "missing_optional_indicators": [],
            "indicator_coverage": 1.0,
        }
    )
    assert status == CLEAN
    assert required == [] and optional == []
    assert coverage == 1.0


def test_a_run_that_recorded_a_required_miss_is_degraded() -> None:
    status, required, _optional, coverage = classify(
        {
            "missing_required_indicators": ["usdjpy", "eurjpy"],
            "missing_optional_indicators": [],
            "indicator_coverage": 0.9,
        }
    )
    assert status == DEGRADED
    assert required == ["usdjpy", "eurjpy"]
    assert coverage == 0.9


def test_an_optional_miss_alone_does_not_degrade() -> None:
    status, required, optional, _coverage = classify(
        {
            "missing_required_indicators": [],
            "missing_optional_indicators": ["iron_ore"],
            "indicator_coverage": 0.95,
        }
    )
    assert status == CLEAN
    assert required == []
    assert optional == ["iron_ore"]


def test_a_feature_set_from_before_the_fields_existed_is_unknown() -> None:
    """The regression that matters: [] must not be read as COMPLETE."""

    status, required, optional, coverage = classify(
        {"feature_names": ["a", "b"], "feature_coverage": 1.0}
    )
    assert status == UNKNOWN
    assert required == [] and optional == []
    assert coverage is None


def test_an_empty_details_blob_is_unknown_not_clean() -> None:
    assert classify({})[0] == UNKNOWN


def test_a_null_list_is_treated_as_recorded_and_empty() -> None:
    """Postgres can hand back JSON null; that is still a recorded run."""

    status, required, optional, _coverage = classify(
        {
            "missing_required_indicators": None,
            "missing_optional_indicators": None,
            "indicator_coverage": 1.0,
        }
    )
    assert status == CLEAN
    assert required == [] and optional == []
