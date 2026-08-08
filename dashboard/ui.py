"""Shared Streamlit shell, cache boundaries, and safe status rendering."""

from __future__ import annotations

import os
from datetime import datetime

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from dashboard.presenters import JST, Alert, AlertLevel, format_jst
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
def cached_today_predictions(_service: DashboardQueryService) -> QueryResult:
    return _service.today_predictions()


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


def render_header(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)
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
