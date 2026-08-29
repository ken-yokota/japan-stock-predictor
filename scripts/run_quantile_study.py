#!/usr/bin/env python3
"""Check the fitted quantiles against outcomes, and the rules read off them.

Usage:
    python -m scripts.run_quantile_study artifacts/oos/production_quantile_*.json \
        --against artifacts/oos/production_2025-08-05_2026-08-14_w120.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from research.quantile_study import report


def _rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("predictions", []))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument(
        "--against",
        type=Path,
        default=None,
        help="ロジスティックのP(up)を持つアーム。同じ(日付,銘柄)だけで比較する。",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    text = "\n".join(
        report(_rows(args.artifact), _rows(args.against) if args.against else None)
    )
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
