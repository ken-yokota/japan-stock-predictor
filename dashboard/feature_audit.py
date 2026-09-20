"""Read-only feature registry presentation, independent of model dependencies."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def configured_selection(ticker: str) -> dict[str, Any]:
    # Imported lazily: the dashboard SQL-only contract can inspect this module
    # without installing the ingestion/training stack.
    import yaml

    path = Path(__file__).resolve().parents[1] / "config" / "ticker_feature_sets.yaml"
    try:
        registry = yaml.safe_load(path.read_text())
        selection = registry["tickers"].get(ticker)
        if not selection:
            return {}
        return {
            **selection,
            "feature_version": registry["feature_version"],
            "registered_on": str(registry["registered_on"]),
            "status": registry["status"],
            "minimum_forward_sessions": registry["minimum_forward_sessions"],
        }
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        return {}
