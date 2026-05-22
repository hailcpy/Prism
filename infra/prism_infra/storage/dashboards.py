from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from prism_infra.models import Dashboard, TopConversationByCost


class DashboardStore(Protocol):
    def list_dashboards(self, owner_id: str | None = None) -> list[Dashboard]: ...

    def get_dashboard(self, dashboard_id: str) -> Dashboard | None: ...

    def create_dashboard(
        self, name: str, layout: list[dict[str, Any]], owner_id: str | None = None
    ) -> Dashboard: ...

    def update_dashboard(
        self,
        dashboard_id: str,
        *,
        name: str | None = None,
        layout: list[dict[str, Any]] | None = None,
    ) -> Dashboard | None: ...

    def delete_dashboard(self, dashboard_id: str) -> bool: ...


class PostgresDashboardStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def list_dashboards(self, owner_id: str | None = None) -> list[Dashboard]:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            if owner_id is None:
                cur.execute(
                    """
                    SELECT id::text, name, owner_id::text, layout_jsonb, created_at, updated_at
                    FROM dashboards
                    ORDER BY updated_at DESC
                    """
                )
            else:
                cur.execute(
                    """
                    SELECT id::text, name, owner_id::text, layout_jsonb, created_at, updated_at
                    FROM dashboards
                    WHERE owner_id = %s
                    ORDER BY updated_at DESC
                    """,
                    (owner_id,),
                )
            return [_row_to_dashboard(row) for row in cur.fetchall()]

    def get_dashboard(self, dashboard_id: str) -> Dashboard | None:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT id::text, name, owner_id::text, layout_jsonb, created_at, updated_at
                FROM dashboards
                WHERE id = %s
                """,
                (dashboard_id,),
            )
            row = cur.fetchone()
            return _row_to_dashboard(row) if row else None

    def create_dashboard(
        self, name: str, layout: list[dict[str, Any]], owner_id: str | None = None
    ) -> Dashboard:
        dashboard_id = str(uuid.uuid4())
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                INSERT INTO dashboards (id, name, owner_id, layout_jsonb)
                VALUES (%s, %s, %s, %s)
                RETURNING id::text, name, owner_id::text, layout_jsonb, created_at, updated_at
                """,
                (dashboard_id, name, owner_id, Jsonb(layout)),
            )
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("dashboard insert returned no row")
            return _row_to_dashboard(row)

    def update_dashboard(
        self,
        dashboard_id: str,
        *,
        name: str | None = None,
        layout: list[dict[str, Any]] | None = None,
    ) -> Dashboard | None:
        if name is None and layout is None:
            return self.get_dashboard(dashboard_id)
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            if name is not None and layout is not None:
                cur.execute(
                    """
                    UPDATE dashboards
                    SET name = %s, layout_jsonb = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING id::text, name, owner_id::text, layout_jsonb,
                              created_at, updated_at
                    """,
                    (name, Jsonb(layout), dashboard_id),
                )
            elif name is not None:
                cur.execute(
                    """
                    UPDATE dashboards
                    SET name = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING id::text, name, owner_id::text, layout_jsonb,
                              created_at, updated_at
                    """,
                    (name, dashboard_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE dashboards
                    SET layout_jsonb = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING id::text, name, owner_id::text, layout_jsonb,
                              created_at, updated_at
                    """,
                    (Jsonb(layout), dashboard_id),
                )
            row = cur.fetchone()
            return _row_to_dashboard(row) if row else None

    def delete_dashboard(self, dashboard_id: str) -> bool:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM dashboards WHERE id = %s", (dashboard_id,))
            return cur.rowcount > 0


def get_top_conversations_by_cost(
    database_url: str,
    *,
    start: datetime,
    end: datetime,
    limit: int,
    models: tuple[str, ...] = (),
    providers: tuple[str, ...] = (),
) -> list[TopConversationByCost]:
    sql = """
        SELECT
          conversation_id::text AS conversation_id,
          COALESCE(SUM(cost_usd), 0)::double precision AS cost_usd,
          COUNT(*)::int AS calls,
          COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
          COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens
        FROM inference_logs
        WHERE created_at >= %(start)s AND created_at < %(end)s
          AND conversation_id IS NOT NULL
    """
    params: dict[str, Any] = {"start": start, "end": end, "limit": limit}
    if models:
        sql += " AND model = ANY(%(models)s)"
        params["models"] = list(models)
    if providers:
        sql += " AND provider = ANY(%(providers)s)"
        params["providers"] = list(providers)
    sql += """
        GROUP BY conversation_id
        ORDER BY cost_usd DESC
        LIMIT %(limit)s
    """
    with psycopg.connect(database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [
            TopConversationByCost(
                conversation_id=row["conversation_id"],
                cost_usd=float(row["cost_usd"]),
                calls=int(row["calls"]),
                prompt_tokens=int(row["prompt_tokens"]),
                completion_tokens=int(row["completion_tokens"]),
            )
            for row in cur.fetchall()
        ]


def _row_to_dashboard(row: dict[str, Any]) -> Dashboard:
    layout = row["layout_jsonb"] or []
    if not isinstance(layout, list):
        layout = []
    created_at = row["created_at"]
    updated_at = row["updated_at"]
    if isinstance(created_at, datetime) and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return Dashboard(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        layout=list(layout),
        created_at=created_at,
        updated_at=updated_at,
    )
