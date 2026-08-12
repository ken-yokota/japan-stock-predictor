"""Shared Streamlit shell, cache boundaries, and safe status rendering."""

from __future__ import annotations

import os
from datetime import datetime

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from dashboard.presenters import JST, Alert, AlertLevel, format_jst, string_list
from dashboard.query_service import DashboardQueryService
from dashboard.types import QueryResult, QueryState
from database.connection import create_database_engine

_CACHE_TTL_SECONDS = 60


@st.cache_resource(show_spinner=False)
def _service_for_url(database_url: str) -> DashboardQueryService:
    engine = create_database_engine(database_url)
    return DashboardQueryService(engine)


def service_from_environment() -> DashboardQueryService | None:
    """Build the cached read service from the only dashboard environment value."""

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return None
    try:
        return _service_for_url(database_url)
    except (SQLAlchemyError, ValueError):
        return None


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_database_health(_service: DashboardQueryService) -> QueryResult:
    return _service.database_health()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_latest_run(_service: DashboardQueryService) -> QueryResult:
    return _service.latest_run()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_prediction_set(_service: DashboardQueryService) -> QueryResult:
    return _service.latest_prediction_set()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_day_scoreboard(
    _service: DashboardQueryService, prediction_date: str
) -> tuple[int, int, int, float] | None:
    """Return (buy count, settled buys, correct buys, net yen) for one day.

    Returns ``None`` when the schema or the data is not there yet, so the
    banner degrades to "未確定" rather than showing a zero that reads as a loss.
    """

    predictions = _service.today_predictions()
    if not predictions.ready or not predictions.rows:
        return None
    buys = [
        row
        for row in predictions.rows
        if row.get("signal") == "BUY"
        and str(row.get("prediction_date")) == prediction_date
    ]
    if not buys:
        return 0, 0, 0, 0.0

    actuals = _service.actual_results()
    if not actuals.ready:
        return len(buys), 0, 0, 0.0
    realized = {
        row["prediction_id"]: row["actual_intraday_return"]
        for row in actuals.rows
        if row.get("actual_intraday_return") is not None
    }
    settled = [row for row in buys if row["prediction_id"] in realized]
    if not settled:
        return len(buys), 0, 0, 0.0
    # A BUY is right when the session actually rose. Direction agreement says
    # the same thing for a BUY, whose predicted return is positive by
    # construction, but the operator asked for the plain statement.
    correct = sum(1 for row in settled if float(realized[row["prediction_id"]]) > 0)

    trades = _service.simulated_trades()
    settled_ids = {row["prediction_id"] for row in settled}
    profit = 0.0
    if trades.ready:
        profit = sum(
            float(row.get("net_profit_jpy") or 0)
            for row in trades.rows
            if row.get("prediction_id") in settled_ids
        )
    return len(buys), len(settled), correct, profit


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_latest_settled_day(
    _service: DashboardQueryService,
) -> tuple[str, int, int, float] | None:
    """Return (date, buys, correct buys, net yen) for the newest settled day."""

    history = _service.published_prediction_history(None)
    if not history.ready:
        return None
    settled = [
        row for row in history.rows if row.get("actual_intraday_return") is not None
    ]
    if not settled:
        return None
    day = max(str(row["prediction_date"]) for row in settled)
    buys = [
        row
        for row in settled
        if str(row["prediction_date"]) == day and row.get("signal") == "BUY"
    ]
    if not buys:
        return None
    correct = sum(
        1
        for row in buys
        if (float(row["predicted_intraday_return"]) > 0)
        == (float(row["actual_intraday_return"]) > 0)
    )
    profit = sum(float(row.get("net_profit_jpy") or 0) for row in buys)
    return day, len(buys), correct, profit


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_today_predictions(_service: DashboardQueryService) -> QueryResult:
    return _service.today_predictions()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_feature_completeness(_service: DashboardQueryService) -> QueryResult:
    return _service.feature_completeness()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_prediction_history_window(
    _service: DashboardQueryService, since: str | None
) -> QueryResult:
    return _service.published_prediction_history(since)


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_prediction_history(_service: DashboardQueryService) -> QueryResult:
    return _service.prediction_history()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_actual_results(_service: DashboardQueryService) -> QueryResult:
    return _service.actual_results()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_metrics(_service: DashboardQueryService) -> QueryResult:
    return _service.latest_metrics()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_coefficients(_service: DashboardQueryService) -> QueryResult:
    return _service.model_coefficients()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_trades(_service: DashboardQueryService) -> QueryResult:
    return _service.simulated_trades()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_oos_scenario_rows(_service: DashboardQueryService) -> QueryResult:
    return _service.oos_scenario_rows()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_coefficient_history(
    _service: DashboardQueryService, ticker: str, task: str
) -> QueryResult:
    return _service.coefficient_history(ticker=ticker, task=task)


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_applied_buy_thresholds(_service: DashboardQueryService) -> QueryResult:
    return _service.applied_buy_thresholds()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_selections(_service: DashboardQueryService) -> QueryResult:
    return _service.provider_selections()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_batches(_service: DashboardQueryService) -> QueryResult:
    return _service.ingestion_batches()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_run_steps(_service: DashboardQueryService) -> QueryResult:
    return _service.run_steps()


@st.cache_data(ttl=_CACHE_TTL_SECONDS, show_spinner=False)
def cached_raw_summary(_service: DashboardQueryService) -> QueryResult:
    return _service.raw_data_summary()


def configure_page(title: str, icon: str) -> None:
    st.set_page_config(
        page_title=f"{title} | 日本株短期予測",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """
        <style>
          .block-container {padding-top: 1.5rem; padding-bottom: 3rem;}
          [data-testid="stMetric"] {
            border: 1px solid rgba(128, 128, 128, 0.22);
            border-radius: .75rem;
            padding: .75rem;
          }
          [data-testid="stDataFrame"] {max-width: 100%; overflow-x: auto;}
          @media (max-width: 700px) {
            .block-container {padding-left: .8rem; padding-right: .8rem;}
            h1 {font-size: 1.75rem !important;}
            h2 {font-size: 1.3rem !important;}
          }
        </style>
        """,
        unsafe_allow_html=True,
    )
    with st.sidebar:
        st.caption("READ ONLY • Asia/Tokyo")
        st.caption(f"表示時刻: {datetime.now(JST):%Y-%m-%d %H:%M JST}")
        if st.button("DB表示を更新", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.divider()
        st.caption(
            "本アプリは研究・情報提供用です。投資助言、利益保証、自動発注を行いません。"
        )


def render_latest_day_banner() -> None:
    """Show the newest published day on every tab, with its settled result.

    The operator opens whichever tab answers their question and expects to see
    where the system stands without navigating first. Reading the newest set
    here -- rather than naming a date -- keeps the banner correct on the days
    after this one, and shows plainly when nothing new has been published.
    """

    service = service_from_environment()
    if service is None:
        return
    published = cached_prediction_set(service)
    row = published.first
    if not published.ready or row is None:
        return
    prediction_date = row.get("prediction_date")
    status = str(row.get("status", "—"))
    warnings = string_list(row.get("warnings"))

    summary = cached_day_scoreboard(service, str(prediction_date))
    columns = st.columns(4)
    columns[0].metric("最新の予測日", str(prediction_date))
    columns[1].metric("状態", status)
    if summary is None:
        columns[2].metric("買い候補", "—")
        columns[3].metric("実績", "未確定")
    else:
        buys, settled, hits, profit = summary
        columns[2].metric("買い候補", f"{buys}銘柄")
        # The denominator is every BUY issued, not only the settled ones.
        # Counting settled days alone quietly improves the ratio whenever a
        # close is missing, which is the opposite of what it is read for.
        columns[3].metric(
            "買いの的中",
            "—" if buys == 0 else f"{hits}/{buys}",
            delta=None if settled == 0 else f"{profit:+,.0f}円",
            help=(
                "実際にプラスになった日 / 買いシグナルが出た日。"
                "分母は出したシグナル全部で、実績が未確定の日も含みます。"
            ),
        )
        if buys and settled < buys:
            st.caption(
                f"買い{buys}銘柄のうち{buys - settled}銘柄は実績が未確定です"
                "（分母には含めています）。"
            )
    for warning in warnings:
        st.warning(warning)
    _render_settled_day(service, str(prediction_date))


def _render_settled_day(service: DashboardQueryService, newest: str) -> None:
    """Show the newest day whose actuals exist, when that is not the newest set.

    A holiday publishes a reference prediction that can never settle, so the
    banner above would otherwise show 未確定 with no way to see the last day
    that did settle. This keeps the most recent real result one glance away.
    """

    settled = cached_latest_settled_day(service)
    if settled is None:
        return
    day, buys, hits, profit = settled
    if day == newest:
        return
    st.caption(
        f"直近で実績が確定した日: **{day}**　買い{buys}銘柄　"
        f"方向的中 {hits}/{buys}　仮の損益 {profit:+,.0f}円"
    )


def render_header(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)
    render_latest_day_banner()
    st.info(
        "研究用の参考情報であり、投資助言ではありません。予測値・順位・BUY表示だけで"
        "売買判断をしないでください。"
    )


def require_service() -> DashboardQueryService | None:
    service = service_from_environment()
    if service is None:
        st.warning("PENDING: DATABASE_URLが未設定、またはDB接続を初期化できません。")
        st.caption(
            "DashboardはProvider/APIや学習処理を起動しません。DB設定後、保存済み結果だけを表示します。"
        )
    return service


def render_query_state(
    result: QueryResult,
    *,
    empty_message: str = "保存済みデータはまだありません。",
) -> bool:
    if result.state is QueryState.READY:
        return True
    if result.state is QueryState.EMPTY:
        st.info(f"PENDING: {empty_message}")
    elif result.state is QueryState.SCHEMA_PENDING:
        st.warning(result.message)
    else:
        st.error(result.message)
    return False


def render_alerts(alerts: tuple[Alert, ...]) -> None:
    for alert in alerts:
        body = f"**{alert.title}** — {alert.detail}"
        if alert.level is AlertLevel.ERROR:
            st.error(body)
        elif alert.level is AlertLevel.WARNING:
            st.warning(body)
        else:
            st.info(body)


def render_cutoff_summary(
    *,
    cutoff_at: object,
    generated_at: object,
    status: object,
    run_id: object,
) -> None:
    columns = st.columns(4)
    columns[0].metric("Data Cutoff", format_jst(cutoff_at))
    columns[1].metric("Generated At", format_jst(generated_at))
    columns[2].metric("Status", str(status or "PENDING"))
    columns[3].metric("Run ID", str(run_id or "—")[:12])


def display_rows(
    rows: list[dict[str, object]],
    *,
    height: int | None = None,
) -> None:
    """Render a horizontally scrollable table without exposing an index."""

    if height is None:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    else:
        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
            height=height,
        )
