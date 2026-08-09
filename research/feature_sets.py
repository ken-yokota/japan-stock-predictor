"""Named predictor sets for the research comparison.

`baseline` is exactly what `scripts/run_week_test.py` has always used. It is
frozen: any change to it makes the comparison meaningless, because the point of
the comparison is to hold everything else constant.

The other sets add candidate factors. Adding factors is not free. Each model is
fitted on a 120-session rolling window, so `extended`'s ~80 predictors are more
numerous than the observations available to fit them; Ridge will still return an
answer, and that answer can easily be worse than the smaller set's. `focused`
exists because the interesting question is not "does everything help" but
"which few of these help".

Every symbol here settles before 08:30 JST, and the caller lags all of them one
JPX session on top of that.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

Transform = Literal["return", "difference"]


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """One overseas series and how its daily move is expressed as a feature.

    Prices use a percentage change. Yields are already percentages, so their
    change in level (`difference`) is the quantity that carries meaning; a
    percentage change of a percentage would rescale with the yield level.
    """

    key: str
    symbol: str
    transform: Transform = "return"
    windows: tuple[int, ...] = (1, 5)

    def column_names(self) -> tuple[str, ...]:
        suffix = "return" if self.transform == "return" else "change"
        return tuple(f"{self.key}_{suffix}_{window}d" for window in self.windows)


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """A named, reproducible predictor configuration."""

    name: str
    label: str
    indicators: tuple[IndicatorSpec, ...]
    extra_price_features: tuple[str, ...] = ()
    adr_symbols: Mapping[str, str] = field(default_factory=dict)

    def indicator_column_names(self) -> tuple[str, ...]:
        return tuple(name for spec in self.indicators for name in spec.column_names())


# --- Building blocks ---------------------------------------------------------

# The seven series the production week-test has always used.
_BASELINE_INDICATORS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("spy", "SPY"),
    IndicatorSpec("qqq", "QQQ"),
    IndicatorSpec("vix", "^VIX"),
    IndicatorSpec("usdjpy", "JPY=X"),
    IndicatorSpec("wti", "CL=F"),
    IndicatorSpec("copper", "HG=F"),
    IndicatorSpec("gold", "GC=F"),
)

# Japanese equity risk priced overseas while Tokyo is shut. NKD=F is the CME
# Nikkei 225 contract, whose session ends 06:00 JST; EWJ is the US-listed Japan
# ETF, which ends 05:00 JST. Both are settled well before 08:30.
#
# NKD=F is quoted in dollars rather than yen, so its move contains a currency
# component. That is acceptable here only because USD/JPY is a feature in its
# own right, leaving the linear model able to separate the two. The yen-quoted
# NIY=F was rejected instead: 63% of its recent daily bars print zero volume,
# and a zero-volume bar's close is a quote, not a trade.
#
# TOPIX futures have no free symbol. Osaka's own 08:45-08:59 futures quotes have
# no free historical feed either, so the CME contract is the closest thing to a
# pre-open Japanese equity reading that can also be backtested.
_JAPAN_FUTURES: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("nikkei_futures", "NKD=F"),
    IndicatorSpec("japan_etf", "EWJ"),
)

_US_FUTURES: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("sp500_futures", "ES=F"),
    IndicatorSpec("nasdaq_futures", "NQ=F"),
)

_FX: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("eurjpy", "EURJPY=X"),
    IndicatorSpec("audjpy", "AUDJPY=X"),
    IndicatorSpec("cnyjpy", "CNYJPY=X"),
    IndicatorSpec("dollar_index", "DX-Y.NYB"),
)

_COMMODITIES: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("brent", "BZ=F"),
    IndicatorSpec("natural_gas", "NG=F"),
)

# ^SOX itself is index-licensed and not served free; SOXX tracks it and is.
_US_SECTORS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("semiconductors", "SOXX"),
    IndicatorSpec("energy_sector", "XLE"),
    IndicatorSpec("financial_sector", "XLF"),
    IndicatorSpec("industrial_sector", "XLI"),
    IndicatorSpec("regional_banks", "KRE"),
    IndicatorSpec("oil_services", "OIH"),
)

_RATES: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("us_10y_yield", "^TNX", transform="difference"),
    IndicatorSpec("us_3m_yield", "^IRX", transform="difference"),
    IndicatorSpec("us_30y_yield", "^TYX", transform="difference"),
)

_ASIA_PROXIES: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("china_etf", "FXI"),
    IndicatorSpec("korea_etf", "EWY"),
    IndicatorSpec("dry_bulk_etf", "BDRY"),
)

# NYSE-listed ADRs. These are the deep, continuously quoted ones, so their
# close is a real print from 05:00 JST rather than a stale indication.
_ADR_LISTED: dict[str, str] = {
    "7203": "TM",  # トヨタ自動車
    "7267": "HMC",  # 本田技研工業
    "8306": "MUFG",  # 三菱UFJフィナンシャル・グループ
    "8316": "SMFG",  # 三井住友フィナンシャルグループ
    "8411": "MFG",  # みずほフィナンシャルグループ
    "8604": "NMR",  # 野村ホールディングス
}

# The remaining names trade over the counter in the US. Each of these returned
# a complete, non-zero-volume history when checked, so they are usable — but an
# OTC close is thinner than a NYSE close and more likely to lag the yen tape.
# They are therefore in the wish-list set only, where their cost is measured,
# and out of the focused set, which is meant to stay small and defensible.
#
# 9107 川崎汽船, 8002 丸紅, 5019 出光興産, and 5021 コスモエネルギー have no
# verified US line and stay absent rather than proxied.
_ADR_OVER_THE_COUNTER: dict[str, str] = {
    "9101": "NPNYY",  # 日本郵船
    "9104": "MSLOY",  # 商船三井
    "1605": "IPXHY",  # INPEX
    "5020": "JXHLY",  # ENEOSホールディングス
    "7201": "NSANY",  # 日産自動車
    "7269": "SZKMY",  # スズキ
    "7270": "FUJHY",  # SUBARU
    "8766": "TKOMY",  # 東京海上ホールディングス
    "8001": "ITOCY",  # 伊藤忠商事
    "8031": "MITSY",  # 三井物産
    "8053": "SSUMY",  # 住友商事
    "8058": "MSBHF",  # 三菱商事
}

_ADR_ALL: dict[str, str] = {**_ADR_LISTED, **_ADR_OVER_THE_COUNTER}

_EXTRA_PRICE_FEATURES: tuple[str, ...] = (
    "overnight_gap",
    "volume_change_1d",
    "volume_ratio_20d",
    "atr14_ratio",
    "rsi14",
    "ma5_deviation",
    "ma60_deviation",
)


# --- Named sets --------------------------------------------------------------

BASELINE = FeatureSet(
    name="baseline",
    label="現行 (海外7指標 + 自銘柄の価格特徴量11)",
    indicators=_BASELINE_INDICATORS,
)

# One factor per requested theme, plus the own-price additions. Small enough
# that 120 training sessions still outnumber the predictors.
FOCUSED = FeatureSet(
    name="focused",
    label=(
        "現行 + 日経先物/日本ETF/ユーロ円/半導体/米10年金利"
        " + 出来高・ギャップ・ATR・RSI"
    ),
    indicators=(
        *_BASELINE_INDICATORS,
        IndicatorSpec("nikkei_futures", "NKD=F"),
        IndicatorSpec("japan_etf", "EWJ"),
        IndicatorSpec("eurjpy", "EURJPY=X"),
        IndicatorSpec("semiconductors", "SOXX"),
        IndicatorSpec("us_10y_yield", "^TNX", transform="difference"),
    ),
    extra_price_features=_EXTRA_PRICE_FEATURES,
    adr_symbols=_ADR_LISTED,
)

# The full requested wish list, so its cost is measured rather than argued.
EXTENDED = FeatureSet(
    name="extended",
    label="全部入り (先物・為替・商品・米セクター・金利・アジア・ADR・テクニカル)",
    indicators=(
        *_BASELINE_INDICATORS,
        *_JAPAN_FUTURES,
        *_US_FUTURES,
        *_FX,
        *_COMMODITIES,
        *_US_SECTORS,
        *_RATES,
        *_ASIA_PROXIES,
    ),
    extra_price_features=_EXTRA_PRICE_FEATURES,
    adr_symbols=_ADR_ALL,
)

FEATURE_SETS: dict[str, FeatureSet] = {
    set_.name: set_ for set_ in (BASELINE, FOCUSED, EXTENDED)
}

DEFAULT_FEATURE_SET = BASELINE.name


def resolve(name: str) -> FeatureSet:
    """Return a named feature set, or fail listing the valid names."""

    try:
        return FEATURE_SETS[name]
    except KeyError:
        raise SystemExit(
            f"未知の feature set: {name} / 使えるのは {', '.join(FEATURE_SETS)}"
        ) from None
