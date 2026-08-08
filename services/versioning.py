"""Stable hashes and public version labels for persisted computations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from data.config import AppConfig

FEATURE_VERSION = "pit-features-v1"
MODEL_VERSION = "ridge-logistic-v1"
STRATEGY_VERSION = "intraday-costed-v1"


def sha256_json(value: object) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def config_hash(config: AppConfig) -> str:
    """Hash all validated configuration, including free-provider mappings."""

    return sha256_json(config.model_dump(mode="json"))


def lineage_manifest_hash(
    rows: Sequence[Mapping[str, object]],
) -> str:
    """Hash an ordered source-row manifest for reproducibility."""

    return sha256_json(list(rows))
