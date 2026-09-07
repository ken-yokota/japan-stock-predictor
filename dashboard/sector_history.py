"""Sector-level predicted-vs-realised history.

This lives in its own module rather than beside ``sector_rows`` in
``presenters`` for a deployment reason, not a tidiness one. Streamlit Community
Cloud re-reads a changed *page* file on every rerun but keeps already-imported
modules in ``sys.modules``, so adding a new name to a module the running app had
already imported makes the new page fail with an ImportError until someone
reboots the container by hand -- which is exactly what happened on 2026-09-07.
A module the old process never imported is always loaded fresh from disk, so
new surface area added this way deploys without an intervention.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from dashboard.catalog import sector_label
from dashboard.presenters import as_number


@dataclass(frozen=True, slots=True)
class SectorDay:
    """One sector's averages for one settled session."""

    date: str
    sector: str
    predicted_mean: float
    actual_mean: float
    count: int


def sector_timeseries(
    rows: Iterable[Mapping[str, Any]],
) -> list[SectorDay]:
    """Mean predicted and mean realised return per sector, per session.

    The realised figure is open-to-close (``close / open - 1``), the same
    quantity ``predicted_intraday_return`` forecasts. Comparing the settled
    close against the *previous* close instead would fold in the overnight gap,
    which this system never predicts, and would flatter or penalise every
    sector by whatever the market did while it was shut.

    Rows arrive from ``oos_scenario_rows``, which has already restricted them to
    SUCCESS predictions whose outcome reached FINAL or CORRECTED, so a session
    still awaiting settlement cannot appear here as a flat line.
    """

    buckets: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for row in rows:
        predicted = as_number(row.get("predicted_return"))
        open_price = as_number(row.get("actual_open"))
        close_price = as_number(row.get("actual_close"))
        if predicted is None or open_price is None or close_price is None:
            continue
        if open_price <= 0.0 or close_price <= 0.0:
            continue
        date = str(row.get("prediction_date", "")).strip()
        if not date:
            continue
        sector = sector_label(str(row.get("ticker", "")))
        buckets.setdefault((date, sector), []).append(
            (predicted, close_price / open_price - 1.0)
        )

    return [
        SectorDay(
            date=date,
            sector=sector,
            predicted_mean=sum(pair[0] for pair in pairs) / len(pairs),
            actual_mean=sum(pair[1] for pair in pairs) / len(pairs),
            count=len(pairs),
        )
        for (date, sector), pairs in sorted(buckets.items())
    ]
