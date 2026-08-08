"""Read-only presentation boundary for the Streamlit dashboard."""

from dashboard.query_service import DashboardQueryService
from dashboard.types import QueryResult, QueryState

__all__ = ["DashboardQueryService", "QueryResult", "QueryState"]
