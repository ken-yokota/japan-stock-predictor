"""Discovery of the research artifacts that several pages read.

Both the Test page and the Company Analysis page show results from
``python -m cli week-test``. They used to locate those files independently, and
one of them was still pointing at a single hard-coded path while the other had
moved on to a directory scan -- so the two pages showed different windows and
neither said which. Discovery lives here so that cannot happen again.

Reading only. Nothing in this module fetches, trains, or writes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WEEK_TEST_DIRECTORY = Path("artifacts/week_test")
COMPARISON_DIRECTORY = Path("artifacts/feature_comparison")


def load_artifact(path: Path) -> dict[str, Any] | None:
    """Return one artifact, or ``None`` if it is missing or unreadable."""

    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class ResearchRun:
    """One completed week-test: which window, trained how."""

    start: str
    end: str
    training_window: str
    half_life: int | None
    report: dict[str, Any]

    @property
    def identity(self) -> tuple[str, str, str]:
        weighting = "" if self.half_life is None else f"+hl{self.half_life}"
        return (self.start, self.end, f"{self.training_window}{weighting}")

    def label(self, *, show_training: bool) -> str:
        if not show_training:
            return f"{self.start} 〜 {self.end}"
        weighting = "" if self.half_life is None else f"/直近重視{self.half_life}日"
        return f"{self.start} 〜 {self.end}  [学習{self.training_window}日{weighting}]"


def _as_run(report: dict[str, Any]) -> ResearchRun:
    window = report.get("generated_for", {})
    training = report.get("training", {})
    sessions = window.get("training_window_sessions", training.get("window_sessions"))
    return ResearchRun(
        start=str(window.get("from", "?")),
        end=str(window.get("to", "?")),
        training_window=str(sessions),
        half_life=training.get("recency_half_life_sessions"),
        report=report,
    )


def load_runs(directory: Path = WEEK_TEST_DIRECTORY) -> list[ResearchRun]:
    """Return one run per distinct (window, training setup), earliest start first.

    The same dates trained two different ways are two results, not a duplicate;
    keying on dates alone silently dropped one of them. A window written both
    under its own name and as ``latest.json`` *is* a duplicate, and the named
    file wins so the list matches the runs that were actually requested.
    """

    runs: dict[tuple[str, str, str], ResearchRun] = {}
    for path in sorted(
        directory.glob("*.json"), key=lambda item: item.name == "latest.json"
    ):
        report = load_artifact(path)
        if report is None:
            continue
        run = _as_run(report)
        runs.setdefault(run.identity, run)
    return [runs[key] for key in sorted(runs)]


def labelled_runs(
    directory: Path = WEEK_TEST_DIRECTORY,
) -> list[tuple[str, dict[str, Any]]]:
    """Return ``(label, report)`` pairs ready to drive tabs or a selector.

    The training setup is named only when more than one was run, so the common
    single-setup case keeps the shorter label.
    """

    runs = load_runs(directory)
    show_training = len({run.identity[2] for run in runs}) > 1
    return [(run.label(show_training=show_training), run.report) for run in runs]
