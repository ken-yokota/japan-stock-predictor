"""The universe the morning run actually predicts.

`stocks.yaml` keeps every name it has ever been asked to cover, so the size of
the file says nothing about what runs. Only `enabled` does, and every consumer
filters on it: ingestion, the morning and close pipelines, the walk-forward
runner and the earnings fetch. This pins the operator's stated universe so a
name cannot drift back in - or quietly drop out - unnoticed.
"""

from __future__ import annotations

from data.config import load_stocks_config

# 2026-08-12: 海運・石油・金融 のみ。自動車と商社は enabled: false。
EXPECTED_ENABLED: dict[str, str] = {
    "9101": "shipping",
    "9104": "shipping",
    "9107": "shipping",
    "1605": "oil_energy",
    "5019": "oil_energy",
    "5020": "oil_energy",
    "5021": "oil_energy",
    "8306": "financial",
    "8316": "financial",
    "8411": "financial",
    "8604": "financial",
    "8766": "financial",
}

EXPECTED_DISABLED_SECTORS = frozenset({"automotive", "trading_company"})


def test_enabled_universe_is_shipping_oil_and_financial_only() -> None:
    config = load_stocks_config()
    enabled = {stock.ticker: stock.sector for stock in config.stocks if stock.enabled}
    assert enabled == EXPECTED_ENABLED


def test_every_disabled_stock_is_one_of_the_dropped_sectors() -> None:
    """A name switched off for any other reason should be noticed here."""

    config = load_stocks_config()
    disabled = {
        stock.ticker: stock.sector for stock in config.stocks if not stock.enabled
    }
    unexpected = {
        ticker: sector
        for ticker, sector in disabled.items()
        if sector not in EXPECTED_DISABLED_SECTORS
    }
    assert not unexpected, f"disabled outside the dropped sectors: {unexpected}"


def test_the_dropped_sectors_are_fully_off() -> None:
    """Half a sector left on would skew the ranking without being obvious."""

    config = load_stocks_config()
    still_on = [
        stock.ticker
        for stock in config.stocks
        if stock.enabled and stock.sector in EXPECTED_DISABLED_SECTORS
    ]
    assert not still_on, f"dropped sector still enabled: {still_on}"
