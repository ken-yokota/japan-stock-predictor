"""Small serializable contracts shared by dashboard queries and presenters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class QueryState(StrEnum):
    """A safe UI-facing result state that never contains connection details."""

    READY = "READY"
    EMPTY = "EMPTY"
    SCHEMA_PENDING = "SCHEMA_PENDING"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Rows returned by one read query, or a sanitized availability state."""

    state: QueryState
    rows: tuple[dict[str, Any], ...] = ()
    message: str = ""

    @property
    def ready(self) -> bool:
        return self.state is QueryState.READY

    @property
    def first(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    @classmethod
    def from_rows(cls, rows: tuple[dict[str, Any], ...]) -> QueryResult:
        if not rows:
            return cls(QueryState.EMPTY, message="保存済みデータはまだありません。")
        return cls(QueryState.READY, rows=rows)

    @classmethod
    def schema_pending(cls, tables: tuple[str, ...]) -> QueryResult:
        names = "、".join(tables)
        return cls(
            QueryState.SCHEMA_PENDING,
            message=f"DB migration待ちです ({names})。",
        )

    @classmethod
    def unavailable(cls) -> QueryResult:
        return cls(
            QueryState.UNAVAILABLE,
            message="DBを読み取れません。接続設定と稼働状態を確認してください。",
        )
