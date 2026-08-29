"""The after-close mail: what it must show, and what it must not swallow.

The evening summary is the one mail the operator receives every weekday, and
for weeks it went out as a ``<pre>`` block of prose with no tables and no
colour. These tests hold the replacement to the approved layout, and they hold
the harder requirement behind it: a mail that lists only successes is not a
report, so the failures come first and a day with nothing to celebrate still
produces one.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from database.models import Base
from notifications.report_layout import DOWN, FORECAST, UP, diverging_bar, ratio_bar
from notifications.result_report import (
    DayResult,
    DaySummary,
    caveat_text,
    forecast_vs_actual_figure,
    history_caveat,
    hit_rate_figure,
    load_day_result,
    load_history,
    profit_history_figure,
    result_sections,
    skip_reason,
    subject,
    traded_table,
)
from scripts.send_daily_summary import (
    Achievement,
    Day,
    Finding,
    _dedupe,
    _findings_section,
    build_html,
    build_text,
    collect,
    subject_for,
)
from services.versioning import STRATEGY_VERSION

NAMES = {"9101": "日本郵船", "8306": "三菱UFJ", "7203": "トヨタ自動車"}


def _row(
    ticker: str,
    *,
    signal: str = "BUY",
    predicted: float = 0.01,
    actual: float = 0.01,
    profit: float | None = 5000.0,
    probability: float = 0.62,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "signal": signal,
        "predicted_intraday_return": predicted,
        "probability_up": probability,
        "return_threshold": 0.005,
        "probability_threshold": 0.6,
        "actual_intraday_return": actual,
        "actual_open": 1000.0,
        "actual_close": 1000.0 * (1 + actual),
        "net_profit_jpy": profit,
        "warnings": warnings or [],
    }


def _result(*rows: dict[str, Any], day: date = date(2026, 8, 24)) -> DayResult:
    return DayResult(day, tuple(rows))


# --------------------------------------------------------------------------
# The result tables


def test_every_signed_number_carries_its_own_colour() -> None:
    """The sign alone does not survive a glance on a phone."""

    html = traded_table(
        _result(
            _row("9101", predicted=0.0146, actual=0.0102, profit=5867.0),
            _row("8306", predicted=0.0152, actual=-0.0169, profit=-13404.0),
        ),
        NAMES,
    )

    assert UP in html and DOWN in html
    assert "+5,867円" in html and "-13,404円" in html
    # Words too, because some clients strip inline styles entirely.
    assert "的中" in html and "外れ" in html


def test_a_correct_direction_that_lost_money_still_reads_as_correct() -> None:
    """判定 is about direction; the yen column is where the loss shows."""

    html = traded_table(
        _result(_row("9101", predicted=0.01, actual=0.002, profit=-40.0)), NAMES
    )

    assert "的中" in html
    assert "-40円" in html


def test_subject_states_wins_losses_and_yen() -> None:
    line = subject(
        _result(
            _row("9101", predicted=0.01, actual=0.01, profit=5867.0),
            _row("8306", predicted=0.01, actual=-0.01, profit=-13404.0),
        )
    )

    assert "買い2銘柄" in line
    assert "1勝1敗" in line
    assert "-7,537円" in line


def test_caveat_names_the_trade_count_and_refuses_to_generalise() -> None:
    text_body = caveat_text(_result(_row("9101"), _row("8306")))

    assert "2取引" in text_body
    assert "20取引未満" in text_body


def test_skip_reason_names_the_threshold_that_was_missed() -> None:
    below_return = _row("7203", signal="NO_BUY", predicted=0.001, probability=0.9)
    below_probability = _row("7203", signal="NO_BUY", predicted=0.02, probability=0.58)

    assert "0.5%未満" in skip_reason(below_return)
    assert "58%" in skip_reason(below_probability)
    assert "60%未満" in skip_reason(below_probability)


def test_result_sections_keep_the_caveat_even_on_a_good_day() -> None:
    blocks = result_sections(_result(_row("9101", actual=0.02, profit=90000.0)), NAMES)

    assert any("この数字が示していないこと" in block for block in blocks)


# --------------------------------------------------------------------------
# Failures first


def _day(**kwargs: Any) -> Day:
    base: dict[str, Any] = {"target": date(2026, 8, 24)}
    base.update(kwargs)
    return Day(**base)


def test_failures_are_rendered_above_the_result() -> None:
    day = _day(
        result=_result(_row("9101")),
        findings=(Finding("取得が落ちた", "usdjpy", "続行した", "予測に入っていない"),),
    )

    html = build_html(day, NAMES)

    assert html.index("できなかったこと") < html.index("本日の成績")


def test_a_day_with_no_failures_still_gets_the_section() -> None:
    """A section that disappears cannot be distinguished from one never checked."""

    html = _findings_section(())

    assert "できなかったこと" in html
    assert "検出された失敗はありません" in html


def test_a_failure_that_repeated_collapses_into_one_row_with_its_count() -> None:
    repeated = Finding("データ取得が PARTIAL で終わりました", "usdjpy", "続行", "欠落")
    collapsed = _dedupe([repeated, repeated, repeated])

    assert len(collapsed) == 1
    assert "（3回）" in collapsed[0].what


def test_distinct_failures_are_not_collapsed() -> None:
    collapsed = _dedupe(
        [
            Finding("取得が落ちた", "usdjpy", "続行", "欠落"),
            Finding("取得が落ちた", "eurjpy", "続行", "欠落"),
        ]
    )

    assert len(collapsed) == 2


def test_subject_carries_the_number_of_things_needing_attention() -> None:
    day = _day(
        result=_result(_row("9101", profit=100.0)),
        findings=(
            Finding("a", "b", "c", "d"),
            Finding("e", "f", "g", "h"),
        ),
    )

    assert "要確認2件" in subject_for(day)


def test_a_clean_day_gets_no_attention_suffix() -> None:
    assert "要確認" not in subject_for(_day(result=_result(_row("9101"))))


# --------------------------------------------------------------------------
# The failure modes: a day that produced nothing must still produce a mail


def test_an_unsettled_day_still_sends_and_says_why() -> None:
    day = _day(no_result_reason="確定した実績がありません")

    line = subject_for(day)
    html = build_html(day, NAMES)

    assert "実績が確定していません" in line
    assert "確定した実績がありません" in html
    assert "本日の成績" in html


def test_text_alternative_leads_with_the_failures() -> None:
    day = _day(
        result=_result(_row("9101")),
        findings=(Finding("落ちた", "なぜ", "対処", "現状"),),
        achievements=(Achievement("予測の保存", "22銘柄"),),
    )

    body = build_text(day, NAMES)

    assert body.index("できなかったこと") < body.index("本日の結果")
    assert "落ちた" in body
    assert "なぜ: なぜ" in body


def test_missing_database_url_is_a_finding_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evening report is more useful arriving incomplete than not arriving.

    Both variables have to be cleared. Reporting reads the hosted URL when one
    is configured, so blanking only DATABASE_URL on a machine that has
    NEON_DATABASE_URL in its .env would quietly connect to production and this
    test would pass for the wrong reason.
    """

    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("NEON_DATABASE_URL", "")

    day = collect(date(2026, 8, 24), config_dir=None)  # type: ignore[arg-type]

    assert day.result is None
    assert any("DATABASE_URL" in item.why for item in day.findings)
    assert "実績が確定していません" in subject_for(day)
    assert build_html(day, NAMES)


def test_an_unreachable_database_is_a_finding_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://nobody@127.0.0.1:1/nothing"
    )

    day = collect(date(2026, 8, 24), config_dir=None)  # type: ignore[arg-type]

    assert day.result is None
    assert day.findings
    assert build_html(day, NAMES)


# --------------------------------------------------------------------------
# Against the engine production actually uses


def _postgres_engine() -> Engine | None:
    """A real PostgreSQL engine, or None when one is not reachable.

    ``filter (where ...)``, ``at time zone`` and ``pg_database_size`` do not
    exist on SQLite, so the evening report's own queries can only be exercised
    here. A green SQLite suite has already shipped a query PostgreSQL rejects.
    """

    # Its own database, not the shared TEST_POSTGRES_URL. Two modules dropping
    # and recreating one schema raced each other into an intermittent failure.
    url = os.environ.get("TEST_SUMMARY_POSTGRES_URL") or (
        "postgresql+psycopg://yokotaken@localhost:5432/jsp_summary_test"
    )
    try:
        engine = create_engine(url)
        with engine.connect():
            pass
    except Exception:
        return None
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


def _seed(
    engine: Engine,
    day: date,
    *,
    ingestion_status: str = "SUCCESS",
    failed_symbols: list[str] | None = None,
    retrieved_at: datetime | None = None,
    settle: bool = True,
) -> None:
    """One complete day: runs, a published set, predictions, actuals, trades."""

    run_id = uuid.uuid4()
    morning_run_id = uuid.uuid4()
    close_run_id = uuid.uuid4()
    feature_set_id = uuid.uuid4()
    prediction_set_id = uuid.uuid4()
    cutoff = datetime(day.year, day.month, day.day, 0, 20, tzinfo=UTC)
    started = datetime(day.year, day.month, day.day, 0, 0, tzinfo=UTC)
    # 07:10 JST on the prediction date, which is the *previous* calendar day in
    # UTC. This is exactly the value the UTC-vs-JST bug hid.
    fetched = retrieved_at or (
        datetime(day.year, day.month, day.day, 7, 10, tzinfo=UTC)
        - timedelta(hours=9)
    )

    with engine.begin() as connection:
        for identifier, run_type, status, symbols in (
            (run_id, "INGESTION", ingestion_status, failed_symbols or []),
            (morning_run_id, "MORNING", "SUCCESS", []),
            (close_run_id, "CLOSE", "SUCCESS", []),
        ):
            connection.execute(
                text(
                    "insert into daily_runs (run_id, run_type, prediction_date,"
                    " cutoff_at, started_at, finished_at, status, current_step,"
                    " data_version, failed_symbols)"
                    " values (:id, :type, :day, :cutoff, :started, :finished,"
                    " :status, 'COMPLETE', 'test', :symbols)"
                ),
                {
                    "id": identifier,
                    "type": run_type,
                    "day": day,
                    "cutoff": cutoff,
                    "started": started,
                    "finished": started + timedelta(minutes=2),
                    "status": status,
                    "symbols": json.dumps(symbols),
                },
            )
        connection.execute(
            text(
                "insert into feature_sets (feature_set_id, run_id, ticker,"
                " prediction_date, cutoff_at, feature_version, set_kind,"
                " training_start, training_end, config_hash, status,"
                " required_feature_count, missing_feature_count, missing_ratio,"
                " created_at, details, idempotency_key)"
                " values (:fs, :run, '9101', :day, :cutoff, 'f-v1', 'MORNING',"
                " :train_start, :train_end, :hash, 'READY', 0, 0, 0, :now,"
                " '{}', 'fs/1')"
            ),
            {
                "fs": feature_set_id,
                "run": morning_run_id,
                "day": day,
                "cutoff": cutoff,
                "train_start": day - timedelta(days=200),
                "train_end": day - timedelta(days=1),
                "hash": "a" * 64,
                "now": started,
            },
        )
        model_run_ids = {}
        for task in ("REGRESSION", "CLASSIFICATION"):
            model_run_ids[task] = uuid.uuid4()
            connection.execute(
                text(
                    "insert into model_runs (model_run_id, run_id, ticker,"
                    " feature_set_id, task, algorithm, training_start,"
                    " training_end, cutoff_at, training_rows, feature_version,"
                    " model_version, random_seed, parameters, cv_results,"
                    " status, started_at, idempotency_key)"
                    " values (:id, :run, '9101', :fs, :task, 'ridge',"
                    " :train_start, :train_end, :cutoff, 120, 'f-v1',"
                    " 'ridge-logistic-v1', 42, '{}', '{}', 'SUCCESS', :now,"
                    " :key)"
                ),
                {
                    "id": model_run_ids[task],
                    "run": morning_run_id,
                    "fs": feature_set_id,
                    "task": task,
                    "train_start": day - timedelta(days=200),
                    "train_end": day - timedelta(days=1),
                    "cutoff": cutoff,
                    "now": started,
                    "key": f"m/{task}",
                },
            )
        connection.execute(
            text(
                "insert into prediction_sets (prediction_set_id, run_id,"
                " prediction_date, cutoff_at, status, feature_version,"
                " model_version, strategy_version, training_start, training_end,"
                " generated_at, published_at, warnings, idempotency_key)"
                " values (:ps, :run, :day, :cutoff, 'READY', 'f-v1',"
                " 'ridge-logistic-v1', 's-v1', :train_start, :train_end, :now,"
                " :now, :warnings, 'ps/1')"
            ),
            {
                "ps": prediction_set_id,
                "run": morning_run_id,
                "day": day,
                "cutoff": cutoff,
                "train_start": day - timedelta(days=200),
                "train_end": day - timedelta(days=1),
                "now": started,
                "warnings": json.dumps(["free ingestion status: PARTIAL"]),
            },
        )
        for index, (ticker, signal, predicted, actual, profit) in enumerate(
            (
                ("9101", "BUY", 0.0146, 0.0102, 5867.0),
                ("8306", "BUY", 0.0152, -0.0169, -13404.0),
                ("7203", "NO_BUY", 0.0010, 0.0050, None),
            )
        ):
            prediction_id = uuid.uuid4()
            connection.execute(
                text(
                    "insert into predictions (prediction_id, prediction_set_id,"
                    " ticker, feature_set_id, regression_model_run_id,"
                    " classification_model_run_id, status,"
                    " predicted_intraday_return, probability_up, reference_price,"
                    " reference_basis, predicted_close, signal, rank,"
                    " return_threshold, probability_threshold,"
                    " positive_factors, negative_factors, warnings, created_at,"
                    " idempotency_key)"
                    " values (:id, :ps, :ticker, :fs, :regression,"
                    " :classification, 'SUCCESS', :predicted, 0.62,"
                    " 1000, 'PREV_CLOSE', 1010, :signal, :rank, 0.005, 0.6,"
                    " '[]', '[]', '[]', :now, :key)"
                ),
                {
                    "id": prediction_id,
                    "ps": prediction_set_id,
                    "ticker": ticker,
                    "fs": feature_set_id,
                    "regression": model_run_ids["REGRESSION"],
                    "classification": model_run_ids["CLASSIFICATION"],
                    "predicted": predicted,
                    "signal": signal,
                    "rank": index + 1,
                    "now": started,
                    "key": f"p/{ticker}",
                },
            )
            if not settle:
                continue
            actual_id = uuid.uuid4()
            connection.execute(
                text(
                    "insert into actual_results (actual_result_id, prediction_id,"
                    " result_version, status, actual_open, actual_close,"
                    " actual_intraday_return, actual_price_difference,"
                    " observed_at, finalized_at, created_at, idempotency_key)"
                    " values (:id, :prediction, 1, 'FINAL', 1000, :close,"
                    " :ret, :difference, :now, :now, :now, :key)"
                ),
                {
                    "id": actual_id,
                    "prediction": prediction_id,
                    "close": 1000 * (1 + actual),
                    "ret": actual,
                    "difference": 1000 * actual,
                    "now": started,
                    "key": f"a/{ticker}",
                },
            )
            if profit is None:
                continue
            connection.execute(
                text(
                    "insert into simulated_trades (trade_id, prediction_id,"
                    " actual_result_id, status, is_simulated, capital_jpy, shares,"
                    " net_profit_jpy, strategy_version, created_at,"
                    " idempotency_key)"
                    " values (:id, :prediction, :actual, 'FINAL', true, 1000000,"
                    " 100, :profit, :strategy, :now, :key)"
                ),
                {
                    "id": uuid.uuid4(),
                    "prediction": prediction_id,
                    "actual": actual_id,
                    "profit": profit,
                    "now": started,
                    "strategy": STRATEGY_VERSION,
                    "key": f"t/{ticker}",
                },
            )
        connection.execute(
            text(
                "insert into market_data (canonical_symbol, symbol, provider,"
                " market, market_timezone, market_date, market_timestamp,"
                " available_timestamp, first_observed_at, retrieved_at,"
                " last_seen_at, interval, availability_method, data_quality,"
                " is_realtime, is_delayed, close, raw_hash, quality_flags)"
                " values ('usdjpy', 'USDJPY.FOREX', 'test', 'FX',"
                " 'Asia/Tokyo', :day, :ts, :ts, :ts, :retrieved, :retrieved,"
                " 'eod', 'PROVIDER_SLA_ESTIMATE', 'FREE_UNVERIFIED',"
                " false, false, 150, :hash, '[]')"
            ),
            {
                "day": day - timedelta(days=1),
                # The bar itself is observable before it is fetched; the schema
                # enforces that ordering.
                "ts": fetched - timedelta(hours=2),
                "retrieved": fetched,
                "hash": "b" * 64,
            },
        )


@pytest.fixture()
def postgres() -> Engine:
    engine = _postgres_engine()
    if engine is None:
        pytest.skip("no local PostgreSQL available")
    return engine


def test_collect_reads_one_day_from_postgresql(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 8, 24)
    _seed(postgres, day)
    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    collected = collect(day, config_dir=None)  # type: ignore[arg-type]

    assert collected.result is not None
    assert len(collected.result.buys) == 2
    assert collected.result.buy_hits == 1
    assert collected.result.profit == pytest.approx(-7537.0)
    assert any(label == "予測の計算と保存" for label, _, _, _ in collected.runs)
    assert any("実績の確定" in item.what for item in collected.achievements)


def test_the_days_ingestion_is_counted_in_jst_not_utc(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The morning fetch runs at 07:10 JST, which is the previous UTC date.

    Comparing ``retrieved_at::date`` in UTC made the count zero on every
    ordinary day, so the evening mail carried "本日の取り込みは0行です" as a
    standing false alarm. This is the regression guard for that.
    """

    day = date(2026, 8, 24)
    _seed(postgres, day)
    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    collected = collect(day, config_dir=None)  # type: ignore[arg-type]

    assert ("本日取り込んだ行", "1行") in collected.health
    assert not any("取り込みが0行" in item.what for item in collected.findings)
    assert any("データ取り込み" in item.what for item in collected.achievements)


def test_a_partial_ingestion_becomes_a_named_failure(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 8, 24)
    _seed(postgres, day, ingestion_status="PARTIAL", failed_symbols=["usdjpy"])
    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    collected = collect(day, config_dir=None)  # type: ignore[arg-type]

    assert any("PARTIAL" in item.what for item in collected.findings)
    assert any("usdjpy" in item.why for item in collected.findings)
    assert "要確認" in subject_for(collected)


def test_an_unsettled_day_reports_the_gap_rather_than_a_blank(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    day = date(2026, 8, 24)
    _seed(postgres, day, settle=False)
    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    collected = collect(day, config_dir=None)  # type: ignore[arg-type]

    assert collected.result is None
    assert any("確定していません" in item.what for item in collected.findings)
    assert "実績が確定していません" in subject_for(collected)
    assert build_html(collected, NAMES)


def test_a_day_that_never_ran_names_every_missing_stage(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    collected = collect(date(2026, 8, 25), config_dir=None)  # type: ignore[arg-type]

    missing = [
        item.what for item in collected.findings if "実行記録がありません" in item.what
    ]
    assert len(missing) == 3
    assert any("予測が保存されていません" in item.what for item in collected.findings)


def test_a_closed_market_is_not_reported_as_a_failed_pipeline(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A public holiday used to produce six failures for a day nothing was due.

    Noise like that is what stops the mails being read, and then the real
    failure is missed too.
    """

    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    saturday = date(2026, 8, 22)
    collected = collect(saturday, config_dir=None)  # type: ignore[arg-type]

    assert collected.trading_day is False
    assert not any("実行記録がありません" in item.what for item in collected.findings)
    assert not any(
        "予測が保存されていません" in item.what for item in collected.findings
    )
    assert "JPX休場" in subject_for(collected)
    assert "休場" in build_html(collected, NAMES)


def test_a_trading_day_with_nothing_recorded_is_still_a_failure(
    postgres: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The holiday exemption must not swallow a weekday the pipeline missed."""

    monkeypatch.setenv("DATABASE_URL", str(postgres.url.render_as_string(False)))

    tuesday = date(2026, 8, 25)
    collected = collect(tuesday, config_dir=None)  # type: ignore[arg-type]

    assert collected.trading_day is True
    assert any("実行記録がありません" in item.what for item in collected.findings)
    assert "JPX休場" not in subject_for(collected)


def test_load_day_result_returns_none_for_a_day_with_no_rows(postgres: Engine) -> None:
    assert load_day_result(postgres, date(2026, 1, 1)) is None


# --------------------------------------------------------------------------
# The figures
#
# Nothing here checks that a chart is pretty. It checks that the chart says the
# same thing as the numbers beside it, and that it will actually render in the
# client the operator reads mail in.


def _history(*rows: tuple[str, int, int, int, int, float]) -> list[DaySummary]:
    return [
        DaySummary(
            day=date.fromisoformat(day),
            buys=buys,
            buy_hits=buy_hits,
            predicted=predicted,
            hits=hits,
            profit=profit,
        )
        for day, buys, buy_hits, predicted, hits, profit in rows
    ]


# The right-hand cell is the only one without the centre rule on its border,
# so its opening tag is where the figure splits into "loss side" and "gain
# side".
_RIGHT_CELL = "<td width='50%' style='padding:0'>"


def _halves(bar: str) -> tuple[str, str]:
    left, _, right = bar.partition(_RIGHT_CELL)
    return left, right


def test_a_gain_grows_right_and_a_loss_grows_left() -> None:
    """The centre rule is the whole point; a bar on the wrong side is a lie."""

    gain_left, gain_right = _halves(diverging_bar(5.0, 10.0))
    loss_left, loss_right = _halves(diverging_bar(-5.0, 10.0))

    assert UP in gain_right and UP not in gain_left
    assert DOWN in loss_left and DOWN not in loss_right


def test_a_zero_draws_no_bar() -> None:
    assert UP not in diverging_bar(0.0, 10.0)
    assert DOWN not in diverging_bar(0.0, 10.0)


def test_a_bar_never_exceeds_its_scale() -> None:
    assert "width:100%" in diverging_bar(20.0, 10.0)


def test_a_ratio_is_green_above_the_reference_and_red_below() -> None:
    assert UP in ratio_bar(0.62, reference=0.5)
    assert DOWN in ratio_bar(0.45, reference=0.5)


def test_the_forecast_bar_is_neither_green_nor_red() -> None:
    """A forecast is a claim, not an outcome; colouring it like one misleads."""

    html = forecast_vs_actual_figure(
        _result(_row("9101", predicted=0.01, actual=-0.01, profit=-100.0)), NAMES
    )

    assert FORECAST in html
    assert "予測" in html and "実績プラス" in html


def test_every_row_of_a_figure_shares_one_ruler() -> None:
    """A figure whose rows use different scales is a figure that lies."""

    html = forecast_vs_actual_figure(
        _result(
            _row("9101", predicted=0.02, actual=0.02, profit=1.0),
            _row("8306", predicted=0.01, actual=0.01, profit=1.0),
        ),
        NAMES,
    )

    # The largest value fills its half; the half-sized one is drawn at 50%.
    assert "width:100%" in html
    assert "width:50%" in html


def test_the_profit_figure_plots_the_running_total_not_the_day() -> None:
    history = _history(
        ("2026-08-20", 2, 1, 22, 12, 100.0),
        ("2026-08-21", 2, 1, 22, 12, -300.0),
    )

    html = profit_history_figure(history)

    # +100 then -300 is a running total of +100 then -200: the second row is a
    # loss even though neither cumulative value equals a daily one.
    assert "+100円" in html and "-300円" in html
    assert "-200円" in html


def test_the_hit_rate_figure_uses_fifty_percent_as_the_threshold() -> None:
    html = hit_rate_figure(
        _history(
            ("2026-08-20", 2, 1, 20, 14, 0.0),
            ("2026-08-21", 2, 1, 20, 6, 0.0),
        )
    )

    assert "70%" in html and "30%" in html
    assert UP in html and DOWN in html


def test_the_trend_caveat_names_the_sample_it_rests_on() -> None:
    note = history_caveat(
        _history(
            ("2026-08-20", 2, 1, 20, 14, 100.0),
            ("2026-08-21", 3, 1, 20, 6, -300.0),
        )
    )

    assert "2営業日" in note
    assert "買い5取引" in note
    assert "-200円" in note
    assert "優位性の証拠ではありません" in note


def test_a_day_with_no_history_still_produces_a_mail() -> None:
    day = _day(result=_result(_row("9101")), history=())

    html = build_html(day, NAMES)

    assert "本日の成績" in html
    assert "図: 直近" not in html


def test_the_figures_use_nothing_gmail_would_strip() -> None:
    """Gmail removes <svg> and blocks remote images, leaving a blank row.

    A chart that arrives as empty space is worse than no chart, because the
    table still claims one is there.
    """

    day = _day(
        result=_result(
            _row("9101", predicted=0.01, actual=0.01, profit=100.0),
            _row("8306", predicted=0.01, actual=-0.01, profit=-100.0),
        ),
        history=_history(("2026-08-24", 2, 1, 22, 10, -38910.0)),
    )

    html = build_html(day, NAMES)

    assert "<svg" not in html
    assert "<img" not in html
    assert "src=" not in html
    assert "background-image" not in html
    assert "図: 本日の予測と実績" in html
    assert "図: 直近1営業日の損益と累積" in html
    assert "図: 方向的中率の推移" in html


# --------------------------------------------------------------------------
# A corrected close must not be counted twice


def _correct_one_result(engine: Engine, day: date, ticker: str, profit: float) -> None:
    """Supersede one prediction's result, the way a re-run close does.

    A correction writes a new actual_results row and a new simulated_trades row
    valued against it. It does not edit the old ones -- that is the point of the
    audit trail -- so anything joining both tables on prediction_id alone gets
    two results times two trades.
    """

    with engine.begin() as connection:
        original = connection.execute(
            text(
                "select a.actual_result_id, a.prediction_id"
                " from actual_results a"
                " join predictions p on p.prediction_id = a.prediction_id"
                " join prediction_sets ps"
                "   on ps.prediction_set_id = p.prediction_set_id"
                " where ps.prediction_date = :day and p.ticker = :ticker"
            ),
            {"day": day, "ticker": ticker},
        ).one()
        corrected = uuid.uuid4()
        connection.execute(
            text(
                "insert into actual_results (actual_result_id, prediction_id,"
                " supersedes_actual_result_id, result_version, status,"
                " actual_open, actual_close, actual_intraday_return,"
                " actual_price_difference, observed_at, finalized_at,"
                " created_at, idempotency_key)"
                " values (:id, :prediction, :supersedes, 2, 'CORRECTED',"
                " 1000, 1010, 0.01, 10, now(), now(), now(), :key)"
            ),
            {
                "id": corrected,
                "prediction": original.prediction_id,
                "supersedes": original.actual_result_id,
                "key": f"a2/{ticker}/{day}",
            },
        )
        connection.execute(
            text(
                "insert into simulated_trades (trade_id, prediction_id,"
                " actual_result_id, status, is_simulated, capital_jpy, shares,"
                " net_profit_jpy, strategy_version, created_at,"
                " idempotency_key)"
                " values (:id, :prediction, :actual, 'FINAL', true, 1000000,"
                " 100, :profit, :strategy, now(), :key)"
            ),
            {
                "id": uuid.uuid4(),
                "prediction": original.prediction_id,
                "actual": corrected,
                "profit": profit,
                "strategy": STRATEGY_VERSION,
                "key": f"t2/{ticker}/{day}",
            },
        )


def test_a_corrected_close_is_counted_once_not_four_times(postgres: Engine) -> None:
    """2026-08-20 was mailed as +96,081 JPY when the day made +86,170.

    One prediction had been corrected, so it carried two results and two trades,
    and the join produced four rows for it.
    """

    day = date(2026, 8, 24)
    _seed(postgres, day)
    before = load_day_result(postgres, day)
    assert before is not None

    _correct_one_result(postgres, day, "7203", profit=5000.0)
    after = load_day_result(postgres, day)

    assert after is not None
    assert len(after.items) == len(before.items)
    assert sum(1 for row in after.items if row["ticker"] == "7203") == 1


def test_the_corrected_value_replaces_the_original_rather_than_adding_to_it(
    postgres: Engine,
) -> None:
    day = date(2026, 8, 25)
    _seed(postgres, day)
    original = next(
        row for row in load_day_result(postgres, day).items if row["ticker"] == "7203"
    )

    _correct_one_result(postgres, day, "7203", profit=5000.0)
    corrected = next(
        row for row in load_day_result(postgres, day).items if row["ticker"] == "7203"
    )

    assert float(corrected["net_profit_jpy"]) == pytest.approx(5000.0)
    assert corrected["net_profit_jpy"] != original["net_profit_jpy"]


def test_the_history_table_does_not_multiply_a_corrected_day(
    postgres: Engine,
) -> None:
    day = date(2026, 8, 26)
    _seed(postgres, day)
    before = {item.day: item for item in load_history(postgres, day, limit=5)}[day]

    _correct_one_result(postgres, day, "7203", profit=5000.0)
    after = {item.day: item for item in load_history(postgres, day, limit=5)}[day]

    assert after.predicted == before.predicted
    assert after.buys == before.buys
