"""Public out-of-sample backtesting API.

``walk_forward`` is re-exported lazily on purpose. It imports the training layer,
which imports scikit-learn; the read-only dashboard needs ``backtest.scenario``
to re-simulate stored predictions and must never pull the training stack into its
process. Eagerly importing ``walk_forward`` here would do exactly that through
this package's ``__init__``, so the name is resolved on first attribute access
instead (PEP 562). ``from backtest import walk_forward_validate`` keeps working
for batch callers.
"""

from typing import TYPE_CHECKING, Any

from backtest.scenario import (
    SCENARIO_INPUT_COLUMNS,
    SCENARIO_TRADE_COLUMNS,
    ScenarioConfig,
    ScenarioResult,
    evaluate_scenario,
    prepare_scenario_frame,
    recompute_scenario,
)

if TYPE_CHECKING:
    from backtest.walk_forward import (
        WALK_FORWARD_COLUMNS,
        WalkForwardConfig,
        assert_walk_forward_oos,
        run_walk_forward,
        walk_forward_validate,
    )

_LAZY_WALK_FORWARD_NAMES = frozenset(
    {
        "WALK_FORWARD_COLUMNS",
        "WalkForwardConfig",
        "assert_walk_forward_oos",
        "run_walk_forward",
        "walk_forward_validate",
    }
)

__all__ = [
    "SCENARIO_INPUT_COLUMNS",
    "SCENARIO_TRADE_COLUMNS",
    "WALK_FORWARD_COLUMNS",
    "ScenarioConfig",
    "ScenarioResult",
    "WalkForwardConfig",
    "assert_walk_forward_oos",
    "evaluate_scenario",
    "prepare_scenario_frame",
    "recompute_scenario",
    "run_walk_forward",
    "walk_forward_validate",
]


def __getattr__(name: str) -> Any:
    """Resolve walk-forward names on demand so scikit-learn stays out of imports."""

    if name in _LAZY_WALK_FORWARD_NAMES:
        from backtest import walk_forward

        return getattr(walk_forward, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
