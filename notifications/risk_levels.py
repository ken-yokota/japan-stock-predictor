"""One naming convention for the distribution, shared by every surface.

The operator's convention, stated 2026-08-30 and adopted everywhere from that
point: **Pxx is a downside level -- the return that is exceeded xx% of the
time.** P90 is therefore the bad case, not the good one.

    P95  exceeded 95% of the time   = the 5th percentile   = worst
    P90  exceeded 90% of the time   = the 10th percentile
    P75  exceeded 75% of the time   = the 25th percentile
    P50  the median                 = the 50th percentile

This is the risk-management reading, the same one a 95% VaR uses, and it is
the opposite of the statistical convention where P90 means the 90th
percentile. Both are ordinary; mixing them silently in one report is how a
risk figure gets read as an upside target. So the mapping lives here, in one
module, and every label is generated from it rather than typed.

The upside is deliberately *not* given a Pxx name. Under this convention the
90th percentile would be "P10", which reads as small when it is the optimistic
case; it is called 上振れ instead, in words.
"""

from __future__ import annotations

# The quantile levels the reports name, mapped to the operator's labels.
RISK_LEVELS: dict[float, str] = {
    0.05: "P95",
    0.10: "P90",
    0.25: "P75",
    0.50: "P50",
}

UPSIDE_LEVELS: dict[float, str] = {
    0.75: "上振れ25%",
    0.90: "上振れ10%",
    0.95: "上振れ5%",
}


def risk_label(level: float) -> str:
    """The operator's name for one quantile level."""

    for candidate, label in RISK_LEVELS.items():
        if abs(candidate - level) < 1e-9:
            return label
    for candidate, label in UPSIDE_LEVELS.items():
        if abs(candidate - level) < 1e-9:
            return label
    return f"q{level:g}"


def quantile_for(label: str) -> float | None:
    """The quantile level behind one of the operator's names."""

    for level, candidate in {**RISK_LEVELS, **UPSIDE_LEVELS}.items():
        if candidate == label:
            return level
    return None


CONVENTION_NOTE = (
    "Pxx の読み方: その確率で「これ以上になる」水準です。"
    "P90 は90%の確率で上回る水準、つまり下振れ側のリスクです（上振れではありません）。"
    "P95 はさらに悪い側、P50 は中央値です。"
)
