"""Today's eleven columns, the BUY cards, and the density plots.

Most of what matters here is what the page must *not* do: report an unsettled
session as a flat one, call a morning "data missing" on a measure that means
something else, or take the page down because a status column could not read a
log. Those are the three ways this screen has misled or broken, so they are the
three things pinned below.
"""

from __future__ import annotations

from typing import Any

import pytest

from dashboard.today_view import (
    COLUMN_WIDTH_PX,
    IDENTITY_WIDTH_PX,
    PINNED_COLUMNS,
    RESULT_COLUMNS,
    RISK_QUANTILES,
    _quantile_at,
    buy_cards,
    day_is_settled,
    density_chart,
    density_frame,
    direction_hit,
    distribution_of,
    morning_email_sent,
    pipeline_state,
    result_rows,
    shared_axis,
)


def _curve(*, low: float = -0.03, high: float = 0.03) -> dict[str, Any]:
    levels = [0.1, 0.25, 0.5, 0.75, 0.9]
    span = high - low
    return {
        "method": "quantile_regression_l1",
        "alpha": 0.1,
        "training_sessions": 120,
        "levels": [
            {"quantile": level, "return": low + span * level} for level in levels
        ],
    }


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "prediction_date": "2026-09-07",
        "ticker": "7203",
        "status": "SUCCESS",
        "signal": "BUY",
        "rank": 1,
        "predicted_intraday_return": 0.02,
        "probability_up": 0.7,
        "actual_intraday_return": None,
        "actual_open": 3000.0,
        "actual_close": None,
        "predicted_close": 3060.0,
        "feature_coverage": 0.98,
        "prediction_set_status": "READY",
        "published_at": "2026-09-06T23:19:59+00:00",
        "return_distribution": _curve(),
    }
    base.update(overrides)
    return base


# --- the eleven columns -----------------------------------------------------


def test_columns_are_exactly_the_eleven_asked_for_in_order() -> None:
    rows = result_rows([_row()])
    assert list(rows[0]) == list(RESULT_COLUMNS)
    assert RESULT_COLUMNS == (
        "予測日",
        "銘柄名",
        "判定",
        "方向",
        "予測R",
        "上昇確率",
        "実績R",
        "寄付値",
        "予測終値",
        "実績終値",
        "状態",
    )


def test_the_two_identifying_columns_are_the_pinned_ones() -> None:
    assert frozenset(RESULT_COLUMNS[:2]) == PINNED_COLUMNS


def test_the_pinned_columns_are_wide_enough_to_identify_a_row() -> None:
    # A frozen column narrow enough to truncate the stock name cannot do the
    # job it was frozen for. The longest label is 22 characters.
    for name in PINNED_COLUMNS:
        assert IDENTITY_WIDTH_PX[name] > COLUMN_WIDTH_PX


def test_the_data_columns_all_share_one_width() -> None:
    data_columns = [name for name in RESULT_COLUMNS if name not in PINNED_COLUMNS]
    assert len(data_columns) == 9
    assert {IDENTITY_WIDTH_PX.get(name, COLUMN_WIDTH_PX) for name in data_columns} == {
        COLUMN_WIDTH_PX
    }


def test_an_unsettled_row_shows_a_dash_not_a_zero() -> None:
    # A zero here would read as a flat session rather than one that has not
    # happened. The table is open before the market is.
    row = result_rows([_row(actual_intraday_return=None)])[0]
    assert row["実績R"] == "—"
    assert row["方向"] == "—"
    assert row["予測R"] == "2.00%"


def test_a_settled_row_carries_both_sides() -> None:
    row = result_rows([_row(actual_intraday_return=0.03, actual_close=3090.0)])[0]
    assert row["実績R"] == "3.00%"
    assert row["方向"] == "的中"


def test_verdict_is_never_blank() -> None:
    assert result_rows([_row(signal="BUY")])[0]["判定"] == "BUY"
    assert result_rows([_row(signal="")])[0]["判定"] == "NO BUY"
    assert result_rows([_row(signal="HOLD")])[0]["判定"] == "NO BUY"


# --- direction --------------------------------------------------------------


def test_direction_needs_both_sides() -> None:
    assert direction_hit(0.01, None) == "—"
    assert direction_hit(None, 0.01) == "—"


def test_direction_reads_the_sign_not_the_size() -> None:
    assert direction_hit(0.001, 0.05) == "的中"
    assert direction_hit(0.05, -0.001) == "外れ"
    assert direction_hit(-0.02, -0.01) == "的中"


def test_a_flat_close_is_neither_a_hit_nor_a_miss() -> None:
    assert direction_hit(0.02, 0.0) == "±0"


# --- pipeline state ---------------------------------------------------------


def test_state_reaches_done_when_every_stage_did() -> None:
    assert pipeline_state(_row(), email_sent=True) == "完了"


def test_state_names_the_first_stage_that_failed() -> None:
    assert "予測不可" in pipeline_state(_row(status="FAILED"), email_sent=True)
    assert (
        pipeline_state(_row(), email_sent=True, degraded_tickers=frozenset({"7203"}))
        == "データ欠損"
    )
    assert pipeline_state(_row(prediction_set_status="BUILDING"), email_sent=True) == (
        "未公開"
    )
    assert pipeline_state(_row(published_at=None), email_sent=True) == "未公開"
    assert pipeline_state(_row(), email_sent=False) == "メール未送信"


def test_an_unreadable_mail_log_is_unknown_not_a_failure() -> None:
    # An unread log is not evidence the mail did not go, and saying it did not
    # would send the operator chasing a delivery that already happened.
    assert pipeline_state(_row(), email_sent=None) == "公開済／メール不明"


def test_feature_coverage_does_not_decide_data_completeness() -> None:
    # Feature Coverage is the share of *generated* features that had a value,
    # so an indicator that never arrived is not even in its denominator. Using
    # it here marked all 22 tickers "欠損" on a morning the completeness panel
    # on the same page called NORMAL.
    assert pipeline_state(_row(feature_coverage=0.5), email_sent=True) == "完了"


# --- BUY cards --------------------------------------------------------------


def test_only_published_successful_buys_become_cards() -> None:
    cards = buy_cards(
        [
            _row(ticker="7203", signal="BUY"),
            _row(ticker="7267", signal="HOLD"),
            _row(ticker="8306", signal="BUY", status="INSUFFICIENT_DATA"),
            _row(ticker="9101", signal="BUY", prediction_set_status="BUILDING"),
        ]
    )
    assert [card.ticker for card in cards] == ["7203"]


def test_a_card_is_unsettled_until_its_session_closes() -> None:
    (card,) = buy_cards([_row(actual_intraday_return=None)])
    assert card.settled is False
    assert card.actual_return == "—"
    assert card.direction == "—"


def test_a_settled_card_carries_the_outcome() -> None:
    (card,) = buy_cards([_row(actual_intraday_return=-0.01)])
    assert card.settled is True
    assert card.actual_return == "-1.00%"
    assert card.direction == "外れ"


def test_the_day_flips_once_any_row_settles() -> None:
    assert day_is_settled([_row(), _row()]) is False
    assert day_is_settled([_row(), _row(actual_intraday_return=0.01)]) is True


# --- densities --------------------------------------------------------------


def test_a_row_without_a_curve_draws_nothing_rather_than_raising() -> None:
    assert distribution_of(_row(return_distribution=None)) is None
    assert distribution_of(_row(return_distribution={"levels": []})) is None
    assert density_frame(_row(return_distribution=None), low=-0.05, high=0.05) is None
    assert density_chart(_row(return_distribution=None), low=-0.05, high=0.05) is None


def test_a_malformed_curve_is_skipped_not_fatal() -> None:
    assert distribution_of(_row(return_distribution={"levels": "nonsense"})) is None


def test_the_profile_has_one_column_per_requested_step() -> None:
    frame = density_frame(_row(), low=-0.05, high=0.05, columns=40)
    assert frame is not None
    assert len(frame) == 40
    assert list(frame.columns) == ["リターン (%)", "確率"]


def test_the_axis_spans_every_curve_and_every_outcome() -> None:
    # The realised return has to be inside the axis or the line drawn for it
    # falls off the chart that exists to show it.
    low, high = shared_axis(
        [
            _row(return_distribution=_curve(low=-0.01, high=0.01)),
            _row(return_distribution=None, actual_intraday_return=0.09),
        ]
    )
    assert low < -0.01
    assert high > 0.09


def test_the_axis_has_a_usable_default_when_there_is_nothing_to_span() -> None:
    low, high = shared_axis([_row(return_distribution=None)])
    assert low < high


def _marks(chart: Any) -> list[str]:
    out: list[str] = []
    for layer in chart.layer:
        mark = layer.mark
        out.append(str(getattr(mark, "type", mark)))
    return out


def test_a_settled_chart_carries_the_outcome_rule() -> None:
    chart = density_chart(_row(actual_intraday_return=0.01), low=-0.05, high=0.05)
    assert chart is not None
    marks = _marks(chart)
    assert marks.count("area") == 1
    # Predicted, the three downside levels, and the realised return.
    assert marks.count("rule") == 3
    assert "text" in marks


def test_an_unsettled_chart_has_no_outcome_rule() -> None:
    settled = density_chart(_row(actual_intraday_return=0.01), low=-0.05, high=0.05)
    unsettled = density_chart(_row(actual_intraday_return=None), low=-0.05, high=0.05)
    assert settled is not None and unsettled is not None
    assert _marks(unsettled).count("rule") == _marks(settled).count("rule") - 1


def test_the_downside_levels_are_labelled_and_ordered_leftwards() -> None:
    # Under the operator's convention P90 is the return exceeded 90% of the
    # time, so it is the *worst* of the three and sits furthest left. An
    # unlabelled rule at that end would read as a target.
    chart = density_chart(_row(), low=-0.05, high=0.05)
    assert chart is not None
    labelled = [
        layer for layer in chart.layer if str(getattr(layer.mark, "type", "")) == "text"
    ]
    assert labelled
    frame = labelled[0].data
    assert list(frame["label"]) == ["P50", "P75", "P90"]
    assert frame["x"].iloc[0] > frame["x"].iloc[1] > frame["x"].iloc[2]


def test_the_risk_levels_match_the_notification_layer() -> None:
    # dashboard/ may not import notifications, so the convention is duplicated.
    # This is what stops the two copies drifting into describing P90
    # differently -- the failure the shared constant existed to prevent.
    from notifications.risk_levels import RISK_LEVELS

    for label, level in RISK_QUANTILES:
        assert RISK_LEVELS[level] == label


def test_a_level_between_two_fitted_points_is_interpolated() -> None:
    curve = [(0.1, -0.02), (0.5, 0.0), (0.9, 0.02)]
    assert _quantile_at(curve, 0.5) == pytest.approx(0.0)
    assert _quantile_at(curve, 0.3) == pytest.approx(-0.01)


def test_a_level_outside_the_fit_is_pinned_not_extrapolated() -> None:
    # A curve fitted from P10 to P90 has said nothing about P99, and drawing a
    # confident rule out there would invent a claim the model never made.
    curve = [(0.1, -0.02), (0.5, 0.0), (0.9, 0.02)]
    assert _quantile_at(curve, 0.01) == pytest.approx(-0.02)
    assert _quantile_at(curve, 0.99) == pytest.approx(0.02)
    assert _quantile_at([], 0.5) is None


# --- the mail-log lookup ----------------------------------------------------


class _Result:
    ready = True

    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self.rows = rows


class _Service:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self._rows = rows

    def email_deliveries(self) -> _Result:
        return _Result(self._rows)


def test_a_sent_record_for_the_day_reads_as_sent() -> None:
    service = _Service(({"prediction_date": "2026-09-07", "status": "SENT"},))
    assert morning_email_sent(service, "2026-09-07") is True


def test_another_days_record_does_not_count() -> None:
    service = _Service(({"prediction_date": "2026-09-04", "status": "SENT"},))
    assert morning_email_sent(service, "2026-09-07") is False


def test_a_service_without_the_method_is_unknown_not_a_crash() -> None:
    # Streamlit Cloud can be running a DashboardQueryService imported before
    # email_deliveries existed. An AttributeError there would take the whole
    # page down over one status column.
    assert morning_email_sent(object(), "2026-09-07") is None


def test_a_failing_lookup_is_unknown_not_a_crash() -> None:
    class _Broken:
        def email_deliveries(self) -> None:
            raise RuntimeError("connection reset")

    assert morning_email_sent(_Broken(), "2026-09-07") is None


@pytest.mark.parametrize("state", ["EMPTY", "UNAVAILABLE"])
def test_an_unready_result_is_unknown(state: str) -> None:
    class _Unready:
        def email_deliveries(self) -> object:
            class _R:
                ready = False
                rows: tuple[dict[str, Any], ...] = ()

            return _R()

    assert morning_email_sent(_Unready(), "2026-09-07") is None
