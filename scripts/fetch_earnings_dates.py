"""Cache each stock's scheduled earnings-announcement dates.

Predictions around an earnings release are dominated by the release itself
rather than by the overseas indicators this system models, so those sessions are
excluded from trading and from evaluation. That exclusion needs a list of dates.

Why a cached file instead of a live lookup: Yahoo's earnings endpoint takes
20-30 seconds per ticker, and the morning pipeline already spends most of its
budget fetching 59 price series. Scraping 22 more tickers every morning would
push the run past its timeout for information that changes a few times a year.

Using these dates is not look-ahead. Japanese issuers publish their reporting
date weeks in advance, so the schedule is known long before the 08:30 cutoff of
any session it affects. What must never be used is the *content* of a release,
and nothing here reads that.

Results are written incrementally, so a partial run still leaves usable data.

    python -m scripts.fetch_earnings_dates
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from data.config import load_app_config

DEFAULT_OUTPUT = Path("config/earnings_dates.json")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=40)
    return parser.parse_args()


def _write(path: Path, dates: dict[str, list[str]], failed: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "source": "yfinance Ticker.get_earnings_dates",
                "note": (
                    "Scheduled announcement dates are published weeks ahead, so "
                    "using them is not look-ahead. Release contents are never read."
                ),
                "dates": dates,
                "unavailable": failed,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def main() -> int:
    """Fetch and cache earnings dates for every enabled stock."""

    arguments = _parse_arguments()
    config = load_app_config(arguments.config_dir)
    import yfinance as yf

    dates: dict[str, list[str]] = {}
    failed: list[str] = []
    stocks = [stock for stock in config.stocks.stocks if stock.enabled]

    for position, stock in enumerate(stocks, start=1):
        symbol = stock.provider_symbols.get("yahoo_finance")
        if symbol is None:
            failed.append(f"{stock.ticker}: no Yahoo symbol")
            continue
        try:
            frame = yf.Ticker(symbol).get_earnings_dates(limit=arguments.limit)
            found = (
                sorted({index.date().isoformat() for index in frame.index})
                if frame is not None and len(frame)
                else []
            )
        except Exception as error:
            found = []
            failed.append(f"{stock.ticker}: {type(error).__name__}")

        if found:
            dates[stock.ticker] = found
        elif stock.ticker not in {entry.split(":")[0] for entry in failed}:
            failed.append(f"{stock.ticker}: no dates returned")

        print(f"[{position}/{len(stocks)}] {stock.ticker}: {len(found)}件", flush=True)
        # Persist as we go so an interrupted run still leaves usable data.
        _write(arguments.output, dates, failed)

    print(f"\n取得 {len(dates)}/{len(stocks)} 銘柄 -> {arguments.output}")
    if failed:
        print(f"未取得: {', '.join(failed)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
