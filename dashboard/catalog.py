"""Display-only metadata for the configured Japanese stock universe."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StockDisplay:
    ticker: str
    name: str
    sector: str


_STOCKS = (
    StockDisplay("9101", "日本郵船", "海運"),
    StockDisplay("9104", "商船三井", "海運"),
    StockDisplay("9107", "川崎汽船", "海運"),
    StockDisplay("1605", "INPEX", "石油・エネルギー"),
    StockDisplay("5020", "ENEOSホールディングス", "石油・エネルギー"),
    StockDisplay("5019", "出光興産", "石油・エネルギー"),
    StockDisplay("5021", "コスモエネルギーホールディングス", "石油・エネルギー"),
    StockDisplay("7203", "トヨタ自動車", "自動車"),
    StockDisplay("7267", "本田技研工業", "自動車"),
    StockDisplay("7201", "日産自動車", "自動車"),
    StockDisplay("7269", "スズキ", "自動車"),
    StockDisplay("7270", "SUBARU", "自動車"),
    StockDisplay("8306", "三菱UFJフィナンシャル・グループ", "金融"),
    StockDisplay("8316", "三井住友フィナンシャルグループ", "金融"),
    StockDisplay("8411", "みずほフィナンシャルグループ", "金融"),
    StockDisplay("8604", "野村ホールディングス", "金融"),
    StockDisplay("8766", "東京海上ホールディングス", "金融"),
    StockDisplay("8001", "伊藤忠商事", "商社"),
    StockDisplay("8002", "丸紅", "商社"),
    StockDisplay("8031", "三井物産", "商社"),
    StockDisplay("8053", "住友商事", "商社"),
    StockDisplay("8058", "三菱商事", "商社"),
)

STOCKS_BY_TICKER = {stock.ticker: stock for stock in _STOCKS}


def stock_label(ticker: str) -> str:
    stock = STOCKS_BY_TICKER.get(ticker)
    return f"{ticker} {stock.name}" if stock is not None else ticker


def sector_label(ticker: str) -> str:
    stock = STOCKS_BY_TICKER.get(ticker)
    return stock.sector if stock is not None else "未分類"
