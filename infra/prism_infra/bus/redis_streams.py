from __future__ import annotations

import json
from typing import Any, cast

import redis
from redis.exceptions import ResponseError

from prism_infra.bus.base import StreamMessage

DEFAULT_STREAM_MAXLEN = 1_000_000


class RedisStreamsBus:
    def __init__(self, redis_url: str, *, maxlen: int = DEFAULT_STREAM_MAXLEN) -> None:
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        # Approximate trim caps memory growth if a worker is down for hours; ~ means
        # Redis trims in O(1) at radix-tree node boundaries (slightly over maxlen).
        self.maxlen = maxlen

    def publish(self, stream: str, event: dict[str, Any]) -> str:
        return str(
            self.client.xadd(
                stream,
                {"event": json.dumps(event, separators=(",", ":"))},
                maxlen=self.maxlen,
                approximate=True,
            )
        )

    def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        count: int = 100,
        block_ms: int = 5000,
    ) -> list[StreamMessage]:
        self._ensure_group(stream, group)
        response = cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]],
            self.client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=count,
                block=block_ms,
            ),
        )
        messages: list[StreamMessage] = []
        for _, entries in response:
            for message_id, fields in entries:
                messages.append(StreamMessage(id=message_id, event=json.loads(fields["event"])))
        return messages

    def claim_pending(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[StreamMessage]:
        self._ensure_group(stream, group)
        response = cast(
            Any,
            self.client.xautoclaim(
                name=stream,
                groupname=group,
                consumername=consumer,
                min_idle_time=min_idle_ms,
                start_id="0-0",
                count=count,
            ),
        )
        entries = cast(list[tuple[str, dict[str, str]]], response[1])
        return [
            StreamMessage(id=message_id, event=json.loads(fields["event"]))
            for message_id, fields in entries
        ]

    def ack(self, stream: str, group: str, message_ids: list[str]) -> None:
        if message_ids:
            self.client.xack(stream, group, *message_ids)

    def _ensure_group(self, stream: str, group: str) -> None:
        try:
            self.client.xgroup_create(stream, group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
