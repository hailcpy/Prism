from __future__ import annotations

import bisect
import logging
import os
import pathlib
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from prism_infra.bus import Bus, RedisStreamsBus, StreamMessage
from prism_infra.events import event_from_wire
from prism_infra.models import InferenceEvent, MetricsRow
from prism_infra.storage import LogStore, PostgresLogStore

logging.basicConfig(
    level=os.getenv("PRISM_LOG_LEVEL", "INFO"), format="%(asctime)s %(name)s %(message)s"
)
log = logging.getLogger("metrics-roller")

STREAM = "inference.logged"
GROUP = "cg-roller"
CONSUMER = os.getenv("PRISM_CONSUMER_NAME", "metrics-roller-1")
WINDOW_SECONDS = 60
GRACE_SECONDS = int(os.getenv("PRISM_ROLLER_GRACE_SECONDS", "5"))
PENDING_IDLE_MS = int(os.getenv("PRISM_PENDING_IDLE_MS", "30000"))
HEARTBEAT_INTERVAL_S = 30.0
HEALTH_FILE = pathlib.Path("/tmp/prism-metrics-roller-healthy")


@dataclass
class _WindowBucket:
    minute_bucket: datetime
    model: str
    provider: str
    latencies: list[int] = field(default_factory=list)
    errors: int = 0
    prompt_tokens_sum: int = 0
    completion_tokens_sum: int = 0

    def add(self, event: InferenceEvent) -> None:
        bisect.insort(self.latencies, event.latency_ms)
        if event.status != "ok":
            self.errors += 1
        if event.usage.prompt_tokens:
            self.prompt_tokens_sum += event.usage.prompt_tokens
        if event.usage.completion_tokens:
            self.completion_tokens_sum += event.usage.completion_tokens

    def to_row(self) -> MetricsRow:
        return MetricsRow(
            minute_bucket=self.minute_bucket,
            model=self.model,
            provider=self.provider,
            count=len(self.latencies),
            error_count=self.errors,
            latency_p50_ms=_percentile(self.latencies, 0.50),
            latency_p95_ms=_percentile(self.latencies, 0.95),
            prompt_tokens_sum=self.prompt_tokens_sum,
            completion_tokens_sum=self.completion_tokens_sum,
        )


def _percentile(sorted_values: list[int], q: float) -> int:
    if not sorted_values:
        return 0
    # percentile_disc semantics: smallest value v such that cumulative dist >= q.
    n = len(sorted_values)
    idx = max(0, min(n - 1, int(-(-n * q // 1)) - 1))
    return sorted_values[idx]


def _floor_minute(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).replace(second=0, microsecond=0)


class WindowAggregator:
    """In-memory tumbling-window aggregator.

    Events are grouped into (minute_bucket, model, provider) buckets. A bucket
    is *closed* once wall-clock time passes `minute_bucket + 60s + grace`. On
    close, the aggregator hands back a MetricsRow plus the stream IDs that fed
    the bucket so the caller can XACK them.
    """

    def __init__(self, *, window_seconds: int = WINDOW_SECONDS, grace_seconds: int = GRACE_SECONDS):
        self._window = timedelta(seconds=window_seconds)
        self._grace = timedelta(seconds=grace_seconds)
        self._buckets: dict[tuple[datetime, str, str], _WindowBucket] = {}
        self._message_ids: dict[datetime, dict[str, None]] = {}

    def add(self, message_id: str, event: InferenceEvent) -> None:
        ts = event.created_at or event.ts_end
        bucket_key = _floor_minute(ts)
        message_ids = self._message_ids.setdefault(bucket_key, {})
        if message_id in message_ids:
            return
        message_ids[message_id] = None
        composite = (bucket_key, event.model, event.provider)
        bucket = self._buckets.get(composite)
        if bucket is None:
            bucket = _WindowBucket(
                minute_bucket=bucket_key, model=event.model, provider=event.provider
            )
            self._buckets[composite] = bucket
        bucket.add(event)

    def close_due(self, now: datetime) -> tuple[list[MetricsRow], list[str]]:
        cutoff = now - self._window - self._grace
        due_buckets = sorted(b for b in self._message_ids if b <= cutoff)
        if not due_buckets:
            return [], []
        rows: list[MetricsRow] = []
        ack_ids: list[str] = []
        for bucket_key in due_buckets:
            for composite in list(self._buckets):
                if composite[0] == bucket_key:
                    rows.append(self._buckets.pop(composite).to_row())
            ack_ids.extend(self._message_ids.pop(bucket_key).keys())
        return rows, ack_ids

    def flush_all(self) -> tuple[list[MetricsRow], list[str]]:
        rows = [bucket.to_row() for bucket in self._buckets.values()]
        ack_ids = [mid for ids in self._message_ids.values() for mid in ids]
        self._buckets.clear()
        self._message_ids.clear()
        return rows, ack_ids


def ingest_messages(messages: list[StreamMessage], aggregator: WindowAggregator) -> int:
    accepted = 0
    for message in messages:
        try:
            event = event_from_wire(message.event)
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("dropping invalid event id=%s err=%s", message.id, exc)
            continue
        aggregator.add(message.id, event)
        accepted += 1
    return accepted


def run(
    *,
    bus: Bus,
    store: LogStore,
    stream: str = STREAM,
    group: str = GROUP,
    consumer: str = CONSUMER,
    aggregator: WindowAggregator | None = None,
) -> None:
    HEALTH_FILE.touch()
    log.info("metrics-roller started")
    agg = aggregator or WindowAggregator()
    last_heartbeat = time.monotonic()

    while True:
        claimed = bus.claim_pending(stream, group, consumer, min_idle_ms=PENDING_IDLE_MS, count=500)
        if claimed:
            log.info("claimed pending messages count=%s", len(claimed))
            ingest_messages(claimed, agg)

        new_messages = bus.consume(stream, group, consumer, count=500, block_ms=1000)
        if new_messages:
            ingest_messages(new_messages, agg)

        rows, ack_ids = agg.close_due(datetime.now(UTC))
        if rows:
            store.upsert_metrics(rows)
            bus.ack(stream, group, ack_ids)
            log.info("closed buckets rows=%s acked=%s", len(rows), len(ack_ids))

        if time.monotonic() - last_heartbeat >= HEARTBEAT_INTERVAL_S:
            HEALTH_FILE.touch()
            log.info(
                "heartbeat open_buckets=%s pending_ids=%s",
                len(agg._buckets),
                sum(len(v) for v in agg._message_ids.values()),
            )
            last_heartbeat = time.monotonic()


def main() -> None:
    run(
        bus=RedisStreamsBus(os.environ["REDIS_URL"]),
        store=PostgresLogStore(os.environ["DATABASE_URL"]),
    )


if __name__ == "__main__":
    main()
