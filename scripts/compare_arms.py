#!/usr/bin/env python3
"""Score several walk-forward arms against each other on their common sessions.

Usage:
    python -m scripts.compare_arms artifacts/oos/production_*.json
    python -m scripts.compare_arms --json out.json a.json b.json
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from research.comparison import compare, load_arm, ranked, report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument(
        "--label",
        action="append",
        default=None,
        help="アーム名を明示する（成果物と同じ順で指定）",
    )
    args = parser.parse_args(argv)

    labels = args.label or []
    arms = [
        load_arm(path, label=labels[index] if index < len(labels) else None)
        for index, path in enumerate(args.artifacts)
    ]
    result = compare(arms)
    print("\n".join(report(result)))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "common_pairs": result.common_pairs,
                    "common_sessions": result.common_sessions,
                    "dropped": result.dropped,
                    "ranking": [item.label for item in ranked(result)],
                    "arms": [
                        {
                            "label": item.label,
                            "model": asdict(item.model),
                            "selection": asdict(item.selection),
                            "probability": {
                                "count": item.probability.count,
                                "brier": item.probability.brier,
                                "log_loss": item.probability.log_loss,
                                "base_rate": item.probability.base_rate,
                                "bins": [asdict(b) for b in item.probability.bins],
                            },
                            "trading": asdict(item.trading),
                        }
                        for item in result.evaluations
                    ],
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
