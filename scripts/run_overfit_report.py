#!/usr/bin/env python3
"""Report the in-sample/out-of-sample gap and the chosen hyperparameters.

Usage:
    python -m scripts.run_overfit_report artifacts/oos/*.json
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from research.overfit import load, report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    arms = [load(str(path)) for path in args.artifacts]
    text = "\n".join(report(arms))
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
