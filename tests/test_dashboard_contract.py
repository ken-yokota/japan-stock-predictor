"""What the dashboard must satisfy before it is deployed.

Both failures the operator hit on 2026-08-12 shipped through a green CI,
because nothing in CI ever loaded the dashboard the way Streamlit loads it:

- ``pages/10_History.py`` raised ``ImportError`` on a name it imports from
  ``dashboard.ui``. CI type-checks ``dashboard`` but not ``pages``, and no
  test imports a page, so a page that cannot even be read still passed.
- ``pages/1_Today.py`` raised ``UnserializableReturnValueError`` from
  ``st.cache_data``. Every cached read must return something ``pickle`` can
  round-trip, and nothing asserted that.

A page module cannot simply be imported here: Streamlit pages call ``main()``
at module scope, so importing one would run the page and hit the database. The
import contract is therefore checked statically, against the source.
"""

from __future__ import annotations

import ast
import pickle
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from dashboard.types import QueryResult, QueryState

REPO_ROOT = Path(__file__).resolve().parent.parent
FIRST_PARTY = ("dashboard",)


def _page_sources() -> list[Path]:
    pages = sorted((REPO_ROOT / "pages").glob("*.py"))
    entrypoint = REPO_ROOT / "app.py"
    return [*pages, *( [entrypoint] if entrypoint.exists() else [] )]


def _module_exports(module: str) -> set[str] | None:
    """Top-level names a module binds, or None when it is not a repo module."""

    path = REPO_ROOT / Path(module.replace(".", "/") + ".py")
    if not path.exists():
        return None
    exports: set[str] = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            exports.add(node.name)
        elif isinstance(node, ast.Assign):
            exports.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            exports.add(node.target.id)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            exports.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return exports


@pytest.mark.parametrize("source", _page_sources(), ids=lambda path: path.name)
def test_page_imports_resolve_against_the_module_it_imports_from(source: Path) -> None:
    """Every name a page imports from first-party code exists there.

    This is the check that would have caught ``cached_prediction_history_window``
    going missing: a page is only ever executed by Streamlit, so a broken import
    is invisible until someone opens that tab.
    """

    tree = ast.parse(source.read_text(encoding="utf-8"))
    missing: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if not node.module.startswith(FIRST_PARTY):
            continue
        exports = _module_exports(node.module)
        if exports is None:
            missing.append(f"{node.module} (module not found)")
            continue
        missing.extend(
            f"{node.module}.{alias.name}"
            for alias in node.names
            if alias.name not in exports
        )

    assert not missing, (
        f"{source.relative_to(REPO_ROOT)} imports names that do not exist: "
        + ", ".join(missing)
    )


def _realistic_rows() -> tuple[dict[str, Any], ...]:
    """One row holding every value type the read queries actually return."""

    return (
        {
            "run_id": UUID("11111111-2222-3333-4444-555555555555"),
            "run_type": "MORNING",
            "prediction_date": date(2026, 8, 12),
            "cutoff_at": datetime(2026, 8, 12, 8, 30, tzinfo=UTC),
            "finished_at": None,
            "status": "READY",
            "expected_return": Decimal("0.0123"),
            "probability": 0.61,
            "failed_symbols": ["7203.T", "8306.T"],
            "notes": "",
        },
    )


@pytest.mark.parametrize(
    "result",
    [
        QueryResult.from_rows(()),
        QueryResult.from_rows(_realistic_rows()),
        QueryResult.schema_pending(("daily_runs",)),
        QueryResult.unavailable(),
        QueryResult(QueryState.READY, rows=_realistic_rows(), message="部分的"),
    ],
    ids=["empty", "rows", "schema_pending", "unavailable", "with_message"],
)
def test_query_results_round_trip_through_pickle(result: QueryResult) -> None:
    """``st.cache_data`` pickles what it caches; an unpicklable read is a crash.

    Streamlit redacts the underlying error in the browser, so a failure here is
    the only place the actual reason is visible.
    """

    restored = pickle.loads(pickle.dumps(result))
    assert restored == result
    assert restored.state is result.state
    assert restored.rows == result.rows
