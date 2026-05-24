from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from prism_infra.models import (
    ConversationCost,
    ErrorInfo,
    InferenceEvent,
    LogsQuery,
    MetricsQuery,
    MetricsRow,
    ToolInvocationEvent,
    Usage,
)


class PostgresLogStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def write_logs_batch(self, events: list[InferenceEvent]) -> None:
        if not events:
            return

        rows_by_id = {event.inference_id: self._event_to_row(event) for event in events}
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            for inference_id in sorted(rows_by_id):
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (inference_id,)
                )
            cur.execute(
                """
                SELECT id::text
                FROM inference_logs
                WHERE id = ANY(%(ids)s::uuid[])
                """,
                {"ids": list(rows_by_id)},
            )
            existing_ids = {row[0] for row in cur.fetchall()}
            rows = [
                row for inference_id, row in rows_by_id.items() if inference_id not in existing_ids
            ]
            if not rows:
                return

            cur.executemany(
                """
                INSERT INTO inference_logs (
                  id, created_at, ts_start, ts_end, conversation_id, message_id,
                  model, provider, status, error_type, error_message,
                  provider_error_code, latency_ms, ttft_ms, prompt_tokens,
                  completion_tokens, total_tokens, cached_prompt_tokens,
                  reasoning_tokens, cost_usd, prompt_preview,
                  response_preview, raw_payload_uri, raw_payload_jsonb,
                  metadata_jsonb, sdk_version, schema_version
                )
                VALUES (
                  %(id)s, %(created_at)s, %(ts_start)s, %(ts_end)s,
                  %(conversation_id)s, %(message_id)s, %(model)s, %(provider)s,
                  %(status)s, %(error_type)s, %(error_message)s,
                  %(provider_error_code)s, %(latency_ms)s, %(ttft_ms)s,
                  %(prompt_tokens)s, %(completion_tokens)s, %(total_tokens)s,
                  %(cached_prompt_tokens)s, %(reasoning_tokens)s, %(cost_usd)s,
                  %(prompt_preview)s, %(response_preview)s, %(raw_payload_uri)s,
                  %(raw_payload_jsonb)s, %(metadata_jsonb)s, %(sdk_version)s,
                  %(schema_version)s
                )
                ON CONFLICT (id, created_at) DO NOTHING
                """,
                rows,
            )

    def write_tool_events_batch(self, events: list[ToolInvocationEvent]) -> None:
        if not events:
            return

        rows_by_id = {event.tool_invocation_id: self._tool_event_to_row(event) for event in events}
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            for tool_invocation_id in sorted(rows_by_id):
                cur.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (tool_invocation_id,),
                )
            cur.execute(
                """
                SELECT id::text
                FROM tool_invocations
                WHERE id = ANY(%(ids)s::uuid[])
                """,
                {"ids": list(rows_by_id)},
            )
            existing_ids = {row[0] for row in cur.fetchall()}
            rows = [
                row
                for tool_invocation_id, row in rows_by_id.items()
                if tool_invocation_id not in existing_ids
            ]
            if not rows:
                return

            cur.executemany(
                """
                INSERT INTO tool_invocations (
                  id, created_at, ts_start, ts_end, conversation_id, inference_id,
                  tool_name, status, error_type, error_message, latency_ms,
                  arguments_preview, result_preview, metadata_jsonb, sdk_version,
                  schema_version
                )
                VALUES (
                  %(id)s, %(created_at)s, %(ts_start)s, %(ts_end)s,
                  %(conversation_id)s, %(inference_id)s, %(tool_name)s,
                  %(status)s, %(error_type)s, %(error_message)s, %(latency_ms)s,
                  %(arguments_preview)s, %(result_preview)s, %(metadata_jsonb)s,
                  %(sdk_version)s, %(schema_version)s
                )
                ON CONFLICT (id, created_at) DO NOTHING
                """,
                rows,
            )

    def get_metrics(self, query: MetricsQuery) -> list[MetricsRow]:
        sql = """
            SELECT minute_bucket, model, provider, count, error_count,
                   prompt_tokens_sum, completion_tokens_sum, cost_usd_sum
            FROM metrics_minute
            WHERE minute_bucket >= %(start)s AND minute_bucket < %(end)s
        """
        params: dict[str, Any] = {"start": query.start, "end": query.end}
        if query.models:
            sql += " AND model = ANY(%(models)s)"
            params["models"] = list(query.models)
        if query.providers:
            sql += " AND provider = ANY(%(providers)s)"
            params["providers"] = list(query.providers)
        sql += " ORDER BY minute_bucket ASC, model ASC, provider ASC"

        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [
                MetricsRow(
                    latency_p50_ms=0,
                    latency_p95_ms=0,
                    ttft_p50_ms=None,
                    ttft_p95_ms=None,
                    **row,
                )
                for row in cur.fetchall()
            ]

    _PERCENTILE_COLUMNS: tuple[str, ...] = ("latency_ms", "ttft_ms")

    def get_log_percentile(
        self,
        *,
        start: datetime,
        end: datetime,
        percentile: float,
        column: str = "latency_ms",
        models: tuple[str, ...] = (),
        providers: tuple[str, ...] = (),
    ) -> float:
        self._check_percentile(percentile)
        col = self._check_column(column)
        sql_text = f"""
            SELECT COALESCE(
                percentile_cont(%(p)s) WITHIN GROUP (ORDER BY {col}),
                0
            )
            FROM inference_logs
            WHERE created_at >= %(start)s AND created_at < %(end)s
              AND status = 'ok'
              AND {col} IS NOT NULL
        """
        params: dict[str, Any] = {"start": start, "end": end, "p": percentile}
        sql_text, params = self._apply_dim_filters(sql_text, params, models, providers)
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(cast(Any, sql_text), params)
            row = cur.fetchone()
            return float(row[0]) if row and row[0] is not None else 0.0

    def get_log_percentile_series(
        self,
        *,
        start: datetime,
        end: datetime,
        percentile: float,
        column: str = "latency_ms",
        models: tuple[str, ...] = (),
        providers: tuple[str, ...] = (),
        by_bucket: bool = False,
        bucket_seconds: int = 60,
        group_by: str | None = None,
    ) -> list[tuple[datetime | None, str | None, float]]:
        """Bucket/group-aware true percentile over inference_logs.

        Each returned tuple is `(bucket_or_none, group_label_or_none, value)`.
        Use this for timeseries/pie/table latency or TTFT widgets — averaging
        per-minute p50/p95 across buckets/groups is mathematically wrong.
        """
        self._check_percentile(percentile)
        col = self._check_column(column)
        if group_by is not None and group_by not in {"model", "provider"}:
            raise ValueError("group_by must be one of: model, provider")

        select_parts = []
        group_parts = []
        order_parts = []
        if by_bucket:
            select_parts.append(
                "to_timestamp(floor(extract(epoch from created_at) / %(bw)s) * %(bw)s) "
                "AT TIME ZONE 'UTC' AS bucket"
            )
            group_parts.append("bucket")
            order_parts.append("bucket")
        else:
            select_parts.append("NULL::timestamptz AS bucket")
        if group_by is not None:
            select_parts.append(f"{group_by} AS grp")
            group_parts.append("grp")
            order_parts.append("grp")
        else:
            select_parts.append("NULL::text AS grp")
        select_parts.append(
            f"COALESCE(percentile_cont(%(p)s) WITHIN GROUP (ORDER BY {col}), 0) AS value"
        )

        sql_text = (
            f"SELECT {', '.join(select_parts)} FROM inference_logs "
            "WHERE created_at >= %(start)s AND created_at < %(end)s "
            f"AND status = 'ok' AND {col} IS NOT NULL"
        )
        params: dict[str, Any] = {
            "start": start,
            "end": end,
            "p": percentile,
            "bw": bucket_seconds,
        }
        sql_text, params = self._apply_dim_filters(sql_text, params, models, providers)
        if group_parts:
            sql_text += " GROUP BY " + ", ".join(group_parts)
        if order_parts:
            sql_text += " ORDER BY " + ", ".join(order_parts)

        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(cast(Any, sql_text), params)
            return [
                (row[0], row[1], float(row[2]) if row[2] is not None else 0.0)
                for row in cur.fetchall()
            ]

    @staticmethod
    def _check_percentile(p: float) -> None:
        if not 0.0 < p < 1.0:
            raise ValueError("percentile must be in (0, 1)")

    @classmethod
    def _check_column(cls, column: str) -> str:
        if column not in cls._PERCENTILE_COLUMNS:
            raise ValueError(f"column must be one of: {cls._PERCENTILE_COLUMNS}")
        return column

    @staticmethod
    def _apply_dim_filters(
        sql_text: str,
        params: dict[str, Any],
        models: tuple[str, ...],
        providers: tuple[str, ...],
    ) -> tuple[str, dict[str, Any]]:
        if models:
            sql_text += " AND model = ANY(%(models)s)"
            params["models"] = list(models)
        if providers:
            sql_text += " AND provider = ANY(%(providers)s)"
            params["providers"] = list(providers)
        return sql_text, params

    def get_metric_dimensions(self) -> tuple[list[str], list[str]]:
        with psycopg.connect(self.database_url) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT model FROM inference_logs WHERE model IS NOT NULL ORDER BY model"
            )
            models = [row[0] for row in cur.fetchall()]
            cur.execute(
                "SELECT DISTINCT provider FROM inference_logs "
                "WHERE provider IS NOT NULL ORDER BY provider"
            )
            providers = [row[0] for row in cur.fetchall()]
            return models, providers

    def get_logs(self, query: LogsQuery) -> list[InferenceEvent]:
        sql = """
            SELECT id, created_at, ts_start, ts_end, conversation_id, message_id,
                   model, provider, status, error_type, error_message,
                   provider_error_code, latency_ms, ttft_ms, prompt_tokens,
                   completion_tokens, total_tokens, cached_prompt_tokens,
                   reasoning_tokens, cost_usd, prompt_preview,
                   response_preview, raw_payload_uri, raw_payload_jsonb,
                   metadata_jsonb, sdk_version, schema_version
            FROM inference_logs
            WHERE created_at >= %(start)s AND created_at < %(end)s
        """
        params: dict[str, Any] = {"start": query.start, "end": query.end, "limit": query.limit}
        if query.model is not None:
            sql += " AND model = %(model)s"
            params["model"] = query.model
        if query.provider is not None:
            sql += " AND provider = %(provider)s"
            params["provider"] = query.provider
        if query.status is not None:
            sql += " AND status = %(status)s"
            params["status"] = query.status
        sql += " ORDER BY created_at DESC LIMIT %(limit)s"

        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return [self._row_to_event(row) for row in cur.fetchall()]

    def get_conversation_cost(self, conversation_id: str) -> ConversationCost:
        with psycopg.connect(self.database_url) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT
                  COUNT(*)::int AS calls,
                  COALESCE(SUM(prompt_tokens), 0)::bigint AS prompt_tokens,
                  COALESCE(SUM(completion_tokens), 0)::bigint AS completion_tokens,
                  COALESCE(SUM(cached_prompt_tokens), 0)::bigint AS cached_prompt_tokens,
                  COALESCE(SUM(reasoning_tokens), 0)::bigint AS reasoning_tokens,
                  COALESCE(SUM(cost_usd), 0)::double precision AS cost_usd
                FROM inference_logs
                WHERE conversation_id = %s
                """,
                (conversation_id,),
            )
            row = cur.fetchone() or {}
            return ConversationCost(
                conversation_id=conversation_id,
                calls=int(row.get("calls", 0)),
                prompt_tokens=int(row.get("prompt_tokens", 0)),
                completion_tokens=int(row.get("completion_tokens", 0)),
                cached_prompt_tokens=int(row.get("cached_prompt_tokens", 0)),
                reasoning_tokens=int(row.get("reasoning_tokens", 0)),
                cost_usd=float(row.get("cost_usd", 0.0)),
            )

    def _event_to_row(self, event: InferenceEvent) -> dict[str, Any]:
        created_at = event.created_at or datetime.now(UTC)
        return {
            "id": event.inference_id,
            "created_at": created_at,
            "ts_start": event.ts_start,
            "ts_end": event.ts_end,
            "conversation_id": event.conversation_id,
            "message_id": event.message_id,
            "model": event.model,
            "provider": event.provider,
            "status": event.status,
            "error_type": event.error.type if event.error else None,
            "error_message": event.error.message if event.error else None,
            "provider_error_code": event.error.provider_code if event.error else None,
            "latency_ms": event.latency_ms,
            "ttft_ms": event.ttft_ms,
            "prompt_tokens": event.usage.prompt_tokens,
            "completion_tokens": event.usage.completion_tokens,
            "total_tokens": event.usage.total_tokens,
            "cached_prompt_tokens": event.usage.cached_prompt_tokens,
            "reasoning_tokens": event.usage.reasoning_tokens,
            "cost_usd": event.cost_usd,
            "prompt_preview": event.prompt_preview,
            "response_preview": event.response_preview,
            "raw_payload_uri": event.raw_payload_uri,
            "raw_payload_jsonb": Jsonb(event.raw_payload_jsonb)
            if event.raw_payload_jsonb is not None
            else None,
            "metadata_jsonb": Jsonb(event.metadata),
            "sdk_version": event.sdk_version,
            "schema_version": event.schema_version,
        }

    def _tool_event_to_row(self, event: ToolInvocationEvent) -> dict[str, Any]:
        created_at = event.created_at or datetime.now(UTC)
        return {
            "id": event.tool_invocation_id,
            "created_at": created_at,
            "ts_start": event.ts_start,
            "ts_end": event.ts_end,
            "conversation_id": event.conversation_id,
            "inference_id": event.inference_id,
            "tool_name": event.tool_name,
            "status": event.status,
            "error_type": event.error.type if event.error else None,
            "error_message": event.error.message if event.error else None,
            "latency_ms": event.latency_ms,
            "arguments_preview": event.arguments_preview,
            "result_preview": event.result_preview,
            "metadata_jsonb": Jsonb(event.metadata),
            "sdk_version": event.sdk_version,
            "schema_version": event.schema_version,
        }

    def _row_to_event(self, row: dict[str, Any]) -> InferenceEvent:
        error = None
        if row["error_type"] is not None:
            error = ErrorInfo(
                type=row["error_type"],
                message=row["error_message"] or "",
                provider_code=row["provider_error_code"],
            )
        return InferenceEvent(
            schema_version=row["schema_version"],
            inference_id=str(row["id"]),
            conversation_id=str(row["conversation_id"]) if row["conversation_id"] else None,
            message_id=str(row["message_id"]) if row["message_id"] else None,
            model=row["model"],
            provider=row["provider"],
            status=row["status"],
            error=error,
            ts_start=row["ts_start"],
            ts_end=row["ts_end"],
            latency_ms=row["latency_ms"],
            ttft_ms=row["ttft_ms"],
            usage=Usage(
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["total_tokens"],
                cached_prompt_tokens=row["cached_prompt_tokens"],
                reasoning_tokens=row["reasoning_tokens"],
            ),
            cost_usd=row["cost_usd"],
            prompt_preview=row["prompt_preview"],
            response_preview=row["response_preview"],
            raw_payload_uri=row["raw_payload_uri"],
            raw_payload_jsonb=row["raw_payload_jsonb"],
            metadata=row["metadata_jsonb"],
            sdk_version=row["sdk_version"],
            created_at=row["created_at"],
        )
