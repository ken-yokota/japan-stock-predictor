"""The dashboard's reads must not depend on being picklable.

Streamlit Community Cloud raised UnserializableReturnValueError on these reads
three times, each time with the underlying reason redacted, and each time the
values pickled cleanly when exercised against the production database on the
same interpreter and Streamlit version. The failure was never in the data.

``cache_resource`` does not serialise what it stores, so the class of failure
is gone rather than the instance of it. This pins the choice: a future edit
that reaches for ``cache_data`` on a read brings the whole failure mode back,
and would do it silently until the next deploy.
"""

from __future__ import annotations

import ast
import pathlib

UI = pathlib.Path(__file__).resolve().parent.parent / "dashboard" / "ui.py"


def _decorators() -> dict[str, list[str]]:
    tree = ast.parse(UI.read_text(encoding="utf-8"))
    found: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        names: list[str] = []
        for decorator in node.decorator_list:
            call = decorator.func if isinstance(decorator, ast.Call) else decorator
            attribute = getattr(call, "attr", None)
            if attribute:
                names.append(str(attribute))
        if names:
            found[node.name] = names
    return found


def test_no_cached_read_uses_cache_data() -> None:
    offenders = [
        name
        for name, decorators in _decorators().items()
        if name.startswith("cached_") and "cache_data" in decorators
    ]
    assert not offenders, (
        "these reads would be pickled again, which is what kept breaking the "
        f"deployed app: {offenders}"
    )


def test_every_cached_read_is_cached_somehow() -> None:
    """A read that lost its decorator would hit the database on every rerun."""

    uncached = [
        name
        for name, decorators in _decorators().items()
        if name.startswith("cached_")
        and not {"cache_resource", "cache_data"} & set(decorators)
    ]
    assert not uncached


def test_the_refresh_button_clears_the_cache_that_holds_the_reads() -> None:
    source = UI.read_text(encoding="utf-8")
    assert "st.cache_resource.clear()" in source
