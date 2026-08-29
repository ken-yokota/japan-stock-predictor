#!/usr/bin/env python3
"""Score every ablation and incremental arm against its control.

Usage:
    python -m scripts.run_ablation_report --directory artifacts/oos
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from research.evaluation import Prediction, from_research_rows
from research.incremental import compare, report

SUFFIX = "_2025-08-05_2026-08-14_w120.json"


def _load(directory: Path, name: str) -> list[Prediction] | None:
    path = directory / f"{name}{SUFFIX}"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return from_research_rows(payload.get("predictions", []))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("artifacts/oos"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    from research import feature_sets

    compact = _load(args.directory, "production_compact")
    price_only = _load(args.directory, "price_only")
    if compact is None or price_only is None:
        print("対照アーム（production_compact / price_only）がありません。")
        return 1

    keys = sorted(spec.key for spec in feature_sets.PRODUCTION_COMPACT.indicators)

    ablations = []
    increments = []
    for key in keys:
        removed = _load(args.directory, f"compact_no_{key}")
        if removed is not None:
            ablations.append(compare(removed, compact, group=key, kind="ablation"))
        added = _load(args.directory, f"price_plus_{key}")
        if added is not None:
            increments.append(
                compare(added, price_only, group=key, kind="incremental")
            )

    no_price = _load(args.directory, "compact_no_price")
    if no_price is not None:
        ablations.append(
            compare(no_price, compact, group="自銘柄テクニカル", kind="ablation")
        )

    text = "\n".join(report(ablations, increments))
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
