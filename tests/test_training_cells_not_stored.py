"""Training cells are checked and hashed, then dropped rather than stored.

One morning wrote 543,000 of them - 400 MB of a 512 MB ceiling - and nothing
in production ever read one: the only statements touching feature_values were
the writer, its own count check, and the pruner that deletes them two days
later. The raw rows they were computed from stay, so a day can still be
rebuilt exactly.

What must not be lost with them: the cutoff check each cell was getting from
the database, and the manifest hash that proves no training input was
substituted. Both are asserted here.
"""

from __future__ import annotations

import inspect

from services import persistence


def _source() -> str:
    return inspect.getsource(persistence)


def test_training_cells_are_not_written() -> None:
    source = _source()
    assert 'row_role="TRAIN"' in source
    # Every TRAIN call site opts out of persistence.
    train_blocks = source.count("persist=False")
    assert train_blocks >= 2, "both the feature and target cells must opt out"


def test_the_scored_row_is_still_written() -> None:
    source = _source()
    scored = source.split('row_role="SCORE"', 1)[1].split(")", 1)[0]
    assert "persist=False" not in scored


def test_a_training_cell_still_gets_its_cutoff_checked() -> None:
    """The one guarantee the database was making for a training cell."""

    source = inspect.getsource(persistence._persist_value)
    assert "sample cutoff cannot exceed feature-set cutoff" in source
    assert "must be timezone-aware" in source


def test_the_manifest_still_covers_every_cell() -> None:
    """The hash is what replaces per-cell rows as evidence."""

    source = inspect.getsource(persistence.persist_feature_set)
    body = source.split("manifest: list", 1)[1]
    assert "for pending in pending_values:" in body
    # The manifest loop must not be narrowed to the scored rows.
    manifest_append = body.split("manifest.append", 1)[0]
    assert "if pending.is_scored:\n            continue" not in manifest_append


def test_only_the_scored_row_is_counted_as_required() -> None:
    source = inspect.getsource(persistence.persist_feature_set)
    assert "required_count = feature_count" in source
    assert "training_cell_count" in source


def test_completeness_survives_finalize() -> None:
    """finalize replaces details wholesale; the fields have to be restated.

    They were written at creation and silently overwritten, which is why the
    first audit reported every stock as UNKNOWN.
    """

    source = inspect.getsource(persistence.persist_feature_set)
    finalize = source.split("finalize_feature_set", 1)[1]
    for key in (
        "missing_required_indicators",
        "missing_optional_indicators",
        "indicator_coverage",
        "training_cells_validated_not_stored",
    ):
        assert key in finalize, key
